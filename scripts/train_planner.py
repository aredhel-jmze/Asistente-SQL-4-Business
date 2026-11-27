#!/usr/bin/env python3
"""Deliverable 4: QLoRA fine-tuning of the Planificador (Qwen2.5-Coder-3B-Instruct).

The measured run used a Modal H100 in bf16 (scripts/modal_d4.py); on a Colab T4 (no bf16) it uses fp16.

Trains a LoRA adapter on data/finetune/train.jsonl, measures the loss on data/finetune/dev.jsonl
during training, and writes to --output-dir (default results/deliverable4/adapter):

  adapter_model.safetensors, adapter_config.json   the adapter (what is published on Hugging Face)
  training_log.json   configuration, hardware, versions, time and every logged loss
  loss.csv            step, train_loss, dev_loss (read by docs/deliverable4.tex with pgfplots)
  README.md           model card

Only the completion (the plan JSON) is learned: the prompt tokens are masked out of the loss. The
prompt is exactly the one the Planner reads at run time, so the adapter is used without changes.

    python scripts/train_planner.py                                  # about 1 h on a T4, less on an H100
    python scripts/train_planner.py --push-to-hub usuario/sql4business-planificador-qlora
    python scripts/train_planner.py --limit 16 --epochs 1            # smoke test
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.model import MODEL_ID  # noqa: E402

from sql4business.model import MODEL_REVISION as BASE_REVISION  # noqa: E402  (revision evaluated in D2-D4)
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def read_jsonl(path: Path, limit: int = 0) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def encode(tokenizer, example: dict, max_length: int) -> dict | None:
    """Chat-formatted prompt (masked) + completion and end-of-turn token (learned)."""

    prompt_text = tokenizer.apply_chat_template(
        [{"role": "user", "content": example["prompt"]}], tokenize=False, add_generation_prompt=True
    )
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    completion_ids = tokenizer(example["completion"] + "<|im_end|>\n", add_special_tokens=False)["input_ids"]
    ids = prompt_ids + completion_ids
    if len(ids) > max_length:
        return None
    return {"input_ids": ids, "labels": [-100] * len(prompt_ids) + completion_ids}


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--revision", default=BASE_REVISION)
    parser.add_argument("--train", default=ROOT / "data" / "finetune" / "train.jsonl", type=Path)
    parser.add_argument("--dev", default=ROOT / "data" / "finetune" / "dev.jsonl", type=Path)
    parser.add_argument("--output-dir", default=ROOT / "results" / "deliverable4" / "adapter", type=Path)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.05)
    parser.add_argument("--grad-accum", type=int, default=8, help="examples per optimizer step (batch size 1)")
    parser.add_argument("--warmup", type=float, default=0.05)
    parser.add_argument("--max-length", type=int, default=2560)
    parser.add_argument("--eval-every", type=int, default=25, help="optimizer steps between dev-loss measurements")
    parser.add_argument("--seed", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="examples per split, for a smoke test")
    parser.add_argument("--push-to-hub", default="", help="Hugging Face repo id, e.g. usuario/sql4business-planificador-qlora")
    parser.add_argument("--dtype", choices=("auto", "bf16", "fp16"), default="auto",
                        help="compute precision; auto = bf16 when the GPU supports it (H100), else fp16 (T4)")
    args = parser.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.dtype == "auto":
        args.dtype = "bf16" if torch.cuda.is_bf16_supported() else "fp16"
    dtype = torch.bfloat16 if args.dtype == "bf16" else torch.float16
    started = time.perf_counter()

    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        revision=args.revision,
        device_map={"": 0},
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            # bf16 on GPUs that have it (as in inference); the T4 has no native bf16 and trains in fp16.
            bnb_4bit_compute_dtype=dtype,
        ),
        torch_dtype=dtype,
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    lora = LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=args.dropout, bias="none",
                      target_modules=TARGET_MODULES, task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())

    splits = {}
    for name, path in (("train", args.train), ("dev", args.dev)):
        encoded = [encode(tokenizer, row, args.max_length) for row in read_jsonl(path, args.limit)]
        splits[name] = [row for row in encoded if row is not None]
        print(f"{name}: {len(splits[name])} examples ({sum(r is None for r in encoded)} longer than {args.max_length} tokens dropped)")
    train, dev = splits["train"], splits["dev"]

    steps_per_epoch = math.ceil(len(train) / args.grad_accum)
    total_steps = max(1, math.ceil(steps_per_epoch * args.epochs))
    warmup_steps = max(1, int(total_steps * args.warmup))
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lambda step: (step + 1) / warmup_steps if step < warmup_steps
        else 0.5 * (1 + math.cos(math.pi * (step - warmup_steps) / max(1, total_steps - warmup_steps))),
    )
    # Loss scaling is only needed in fp16; bf16 has the exponent range of fp32.
    scaler = torch.amp.GradScaler("cuda", enabled=args.dtype == "fp16")

    def loss_of(row: dict) -> torch.Tensor:
        input_ids = torch.tensor([row["input_ids"]], device=model.device)
        labels = torch.tensor([row["labels"]], device=model.device)
        with torch.autocast("cuda", dtype=dtype):
            return model(input_ids=input_ids, labels=labels).loss

    def dev_loss() -> float:
        model.eval()
        with torch.no_grad():
            value = sum(loss_of(row).item() for row in dev) / len(dev)
        model.train()
        return value

    log: list[dict] = [{"step": 0, "epoch": 0.0, "dev_loss": dev_loss()}]
    print(f"step 0 dev_loss={log[0]['dev_loss']:.4f}")
    model.train()
    step = 0
    order: list[int] = []
    window: list[float] = []
    while step < total_steps:
        if not order:
            order = list(range(len(train)))
            random.shuffle(order)
        batch = [train[order.pop()] for _ in range(min(args.grad_accum, len(order)))]
        losses = []
        for row in batch:
            loss = loss_of(row) / len(batch)
            scaler.scale(loss).backward()
            losses.append(loss.item() * len(batch))
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        scaler.step(optimizer)
        scaler.update()
        optimizer.zero_grad(set_to_none=True)
        scheduler.step()
        step += 1
        window.append(sum(losses) / len(losses))
        entry = {"step": step, "epoch": round(step / steps_per_epoch, 3), "train_loss": window[-1],
                 "lr": scheduler.get_last_lr()[0]}
        if step % args.eval_every == 0 or step == total_steps:
            entry["dev_loss"] = dev_loss()
        log.append(entry)
        if step % 5 == 0 or "dev_loss" in entry:
            extra = f" dev_loss={entry['dev_loss']:.4f}" if "dev_loss" in entry else ""
            print(f"step {step}/{total_steps} epoch {entry['epoch']:.2f} train_loss={sum(window[-5:]) / len(window[-5:]):.4f}{extra}", flush=True)

    seconds = time.perf_counter() - started
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)

    with (out / "loss.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["step", "epoch", "train_loss", "dev_loss"])
        for entry in log:
            writer.writerow([entry["step"], entry["epoch"], f"{entry['train_loss']:.5f}" if "train_loss" in entry else "nan",
                             f"{entry['dev_loss']:.5f}" if "dev_loss" in entry else "nan"])

    import peft
    import transformers
    import bitsandbytes

    config = {
        "base_model": args.model, "base_revision": args.revision, "method": "QLoRA (NF4 4-bit base, LoRA adapters)",
        "lora": {"r": args.rank, "alpha": args.alpha, "dropout": args.dropout, "target_modules": TARGET_MODULES},
        "optimizer": "AdamW", "lr": args.lr, "schedule": f"linear warmup {args.warmup:.0%} + cosine",
        "epochs": args.epochs, "batch_size": 1, "grad_accum": args.grad_accum, "optimizer_steps": total_steps,
        "max_length": args.max_length, "precision": f"{args.dtype} autocast, {args.dtype} 4-bit compute", "seed": args.seed,
        "train_examples": len(train), "dev_examples": len(dev),
        "trainable_params": trainable, "total_params": total,
        "loss": "cross-entropy on the plan tokens only (prompt masked)",
    }
    summary = {
        "config": config,
        "hardware": {"gpu": torch.cuda.get_device_name(0), "python": platform.python_version()},
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__,
                     "bitsandbytes": bitsandbytes.__version__},
        "commit": git_commit(),
        "seconds": round(seconds, 1),
        "final_train_loss": sum(window[-10:]) / len(window[-10:]),
        "first_dev_loss": log[0]["dev_loss"],
        "final_dev_loss": log[-1].get("dev_loss"),
        "log": log,
    }
    (out / "training_log.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "README.md").write_text(model_card(summary, args.push_to_hub), encoding="utf-8")
    print(f"saved adapter to {out} ({seconds / 60:.1f} min)")

    if args.push_to_hub:
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.push_to_hub, exist_ok=True, private=False)
        commit = api.upload_folder(repo_id=args.push_to_hub, folder_path=str(out),
                                   commit_message=f"Planificador QLoRA adapter (repo commit {summary['commit'][:7]})")
        published = {"repo_id": args.push_to_hub, "url": f"https://huggingface.co/{args.push_to_hub}",
                     "revision": getattr(commit, "oid", None) or str(commit)}
        (ROOT / "results" / "deliverable4" / "adapter_hf.json").write_text(json.dumps(published, indent=1), encoding="utf-8")
        print("published:", published)


def model_card(summary: dict, repo_id: str) -> str:
    c = summary["config"]
    return f"""---
base_model: {c['base_model']}
library_name: peft
license: apache-2.0
language: [es, en]
tags: [text-to-sql, qlora, lora, agents, sql4business]
---

# SQL4Business — Planificador (adaptador QLoRA)

Adaptador LoRA del agente **Planificador** del sistema de agentes SQL4Business (Generative Artificial
Intelligence 580694, Universidad de Concepción; Deliverable 4). Convierte una pregunta de negocio en un
plan JSON (pasos SQL + operación de composición) sobre una base SQLite sintética de ventas.

- Modelo base: `{c['base_model']}` (revisión `{c['base_revision'][:7]}`), cargado en NF4 de 4 bits.
- Datos: {c['train_examples']} ejemplos de entrenamiento y {c['dev_examples']} de validación, generados por
  `scripts/build_planner_data.py` del repositorio (planes construidos por código y verificados contra un
  valor de referencia; sin preguntas ni cálculos de los conjuntos de evaluación).
- Método: {c['method']}; r={c['lora']['r']}, alpha={c['lora']['alpha']}, dropout={c['lora']['dropout']},
  módulos {', '.join(c['lora']['target_modules'])}; AdamW lr={c['lr']}, {c['schedule']}; {c['epochs']} épocas,
  lote efectivo {c['grad_accum']}, {c['optimizer_steps']} pasos; precisión {c['precision']}.
- Hardware: {summary['hardware']['gpu']}; tiempo de entrenamiento {summary['seconds'] / 60:.1f} min.
- Pérdida de validación: {summary['first_dev_loss']:.4f} antes de entrenar, {summary['final_dev_loss']:.4f} al final.

Código, prompt exacto, datos y evaluación: https://github.com/aredhel-jmze/Asistente-SQL-4-Business

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM
base = AutoModelForCausalLM.from_pretrained("{c['base_model']}")
model = PeftModel.from_pretrained(base, "{repo_id or '<repo>'}")
```
"""


if __name__ == "__main__":
    main()
