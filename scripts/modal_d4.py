"""Deliverable 4 on Modal (modal.com): training, evaluation and the video demo on one H100.

The measured D4 run used this script. Every function clones the repository at a pushed ref (branch,
tag or commit), runs the same scripts as the Colab notebook, keeps a copy of its outputs in the
Volume ``sql4business-d4-results`` and returns them, so they land in this checkout at the same paths
(``results/deliverable4/``, ``docs/deliverable4_results.tex``). Adapter weights (*.safetensors) stay
in the Volume and on Hugging Face, not in git.

    modal run scripts/modal_d4.py::train --smoke                 # short training check, publishes nothing
    modal run scripts/modal_d4.py::check_hf                      # CPU, seconds: is the HF token able to write?
    modal run scripts/modal_d4.py::train                         # QLoRA (H100), saved in the Volume, then published (CPU)
    modal run scripts/modal_d4.py::publish --stamp <stamp>       # publish again from the Volume, no GPU
    modal run scripts/modal_d4.py::evaluate --limit 2 --configs agentes_ft   # smoke test (not saved here)
    modal run scripts/modal_d4.py::evaluate                      # four systems + control, 27 questions
    modal run scripts/modal_d4.py::demo --ids q12,q11-p          # base vs fine-tuned Planner, live messages

Needs ``modal setup`` and, for publishing, a Modal secret ``huggingface`` with HF_TOKEN (write access).
Costs GPU time: no container is kept warm, model weights are cached in the Volume ``sql4business-hf-cache``.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/VicenteSonez/Asistente-SQL-4-Business.git"
DEFAULT_REF = "deliverable4"
HF_REPO = "vicentesonez/sql4business-planificador-qlora"
GPU = "H100!"  # "!" stops Modal from upgrading to an H200: same hardware for training and evaluation
WORKDIR = "/root/repo"


def _pins():
    """requirements.txt (the pinned versions of the measured runs), read where the image is built."""

    if not modal.is_local():
        return []
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .pip_install(*_pins())
    .env({"HF_HOME": "/hf-cache", "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false"})
)
hf_cache = modal.Volume.from_name("sql4business-hf-cache", create_if_missing=True)
results_volume = modal.Volume.from_name("sql4business-d4-results", create_if_missing=True)
app = modal.App("sql4business-d4", image=image)

GPU_OPTIONS = dict(gpu=GPU, cpu=8.0, memory=32768, scaledown_window=2,
                   volumes={"/hf-cache": hf_cache, "/vol": results_volume})


# --------------------------------------------------------------------------- remote side


def _checkout(ref):
    subprocess.run(["git", "clone", "--quiet", REPO_URL, WORKDIR], check=True)
    subprocess.run(["git", "-C", WORKDIR, "checkout", "--quiet", ref], check=True)
    commit = subprocess.run(["git", "-C", WORKDIR, "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                         text=True).stdout.strip()
    print(f"ref {ref} -> commit {commit} | GPU {gpu}", flush=True)
    return commit


def _run(*args):
    print("$ python", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], cwd=WORKDIR, check=True)


def _collect(*relative, stamp=None):
    """Files under the given repo paths, without adapter weights; also copied to the results Volume."""

    import shutil

    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    files = {}
    for rel in relative:
        base = Path(WORKDIR) / rel
        paths = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file()) if base.exists() else []
        for path in paths:
            key = path.relative_to(WORKDIR).as_posix()
            backup = Path("/vol") / stamp / key
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            if not key.endswith(".safetensors"):
                files[key] = path.read_bytes()
    results_volume.commit()
    hf_cache.commit()
    print(f"saved a copy in the Volume sql4business-d4-results:/{stamp} ({len(files)} files returned)", flush=True)
    return files


@app.function(timeout=2 * 3600, **GPU_OPTIONS)
def train_remote(ref, smoke):
    """Trains and always keeps the adapter in the Volume (weights included) before anything else can fail.

    Publishing is a separate CPU step (publish_remote), so a Hugging Face error never loses GPU work.
    """

    _checkout(ref)
    folder = "results/deliverable4/" + ("prueba_adapter" if smoke else "adapter")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    args = ["scripts/train_planner.py", "--output-dir", folder]
    if smoke:
        args += ["--limit", "16", "--epochs", "1", "--eval-every", "1"]
    _run(*args)
    return stamp, _collect(folder, stamp=stamp)


@app.function(timeout=1800, cpu=2.0, memory=4096, scaledown_window=2, volumes={"/vol": results_volume},
              secrets=[modal.Secret.from_name("huggingface")])
def publish_remote(stamp):
    """CPU only: uploads the adapter saved by train_remote to Hugging Face and returns adapter_hf.json."""

    import json

    from huggingface_hub import HfApi

    folder = Path("/vol") / stamp / "results" / "deliverable4" / "adapter"
    card = folder / "README.md"
    card.write_text(card.read_text(encoding="utf-8").replace("<repo>", HF_REPO), encoding="utf-8")
    log = json.loads((folder / "training_log.json").read_text(encoding="utf-8"))
    api = HfApi()
    api.create_repo(HF_REPO, exist_ok=True, private=False)
    commit = api.upload_folder(repo_id=HF_REPO, folder_path=str(folder),
                               commit_message=f"Planificador QLoRA adapter (repo commit {log['commit'][:7]})")
    published = {"repo_id": HF_REPO, "url": f"https://huggingface.co/{HF_REPO}",
                 "revision": getattr(commit, "oid", None) or str(commit)}
    print("published:", published, flush=True)
    return {"results/deliverable4/adapter_hf.json": json.dumps(published, indent=1).encode("utf-8"),
            "results/deliverable4/adapter/README.md": card.read_bytes()}


@app.function(timeout=300, cpu=1.0, memory=1024, scaledown_window=2, secrets=[modal.Secret.from_name("huggingface")])
def check_hf_remote():
    """CPU only, a few seconds: which Hugging Face account the token belongs to and whether it can write."""

    from huggingface_hub import HfApi

    info = HfApi().whoami()
    auth = info.get("auth", {}).get("accessToken", {})
    print("user:", info.get("name"), "| token:", auth.get("displayName"), "| role:", auth.get("role"),
          "| fine-grained scopes:", auth.get("fineGrained", {}).get("scoped"), flush=True)
    print("target repo:", HF_REPO, flush=True)


@app.function(timeout=4 * 3600, **GPU_OPTIONS)
def evaluate_remote(ref, limit, configs):
    _checkout(ref)
    args = ["scripts/evaluate_d4.py"]
    if limit:
        args += ["--limit", str(limit)]
    if configs:
        args += ["--configs", configs]
    _run(*args)
    if limit:
        return _collect("results/deliverable4/prueba")
    return _collect("results/deliverable4", "docs/deliverable4_results.tex")


@app.function(timeout=3600, **GPU_OPTIONS)
def demo_remote(ref, ids):
    _checkout(ref)
    for question_id in ids:
        _run("scripts/run_agents.py", "--id", question_id, "--compare", "--no-baseline")
    return _collect("results/deliverable4/demo")


# --------------------------------------------------------------------------- local side


def _save(files, target):
    for key, data in files.items():
        path = Path(target) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"wrote {len(files)} files under {target}")


def _scratch(name):
    folder = Path(os.environ.get("TEMP", "/tmp")) / "sql4business-d4" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@app.local_entrypoint()
def train(ref: str = DEFAULT_REF, smoke: bool = False, publish: bool = True):
    stamp, files = train_remote.remote(ref, smoke)
    _save(files, _scratch("smoke-train") if smoke else ROOT)
    print(f"adapter kept in the Volume sql4business-d4-results:/{stamp}")
    if publish and not smoke:
        _save(publish_remote.remote(stamp), ROOT)


@app.local_entrypoint()
def publish(stamp: str):
    """Publishes an adapter already trained (Volume folder <stamp>), e.g. after a Hugging Face error."""

    _save(publish_remote.remote(stamp), ROOT)


@app.local_entrypoint()
def check_hf():
    check_hf_remote.remote()


@app.local_entrypoint()
def evaluate(ref: str = DEFAULT_REF, limit: int = 0, configs: str = ""):
    files = evaluate_remote.remote(ref, limit, configs)
    _save(files, _scratch("smoke-eval") if limit else ROOT)


@app.local_entrypoint()
def demo(ref: str = DEFAULT_REF, ids: str = "q12,q11-p"):
    files = demo_remote.remote(ref, [i.strip() for i in ids.split(",") if i.strip()])
    _save(files, ROOT)
