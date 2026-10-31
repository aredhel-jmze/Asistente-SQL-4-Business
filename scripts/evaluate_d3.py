#!/usr/bin/env python3
"""Deliverable 3 evaluation: baseline, D2 solution, agent system and one ablation per technique.

Runs every configuration on the 15 official questions and the 12 held-out paraphrases with the
same criteria, writes one results file per configuration (so an interrupted Colab run can resume
with --resume), one trace file per question for the full agent system, and the LaTeX table used
by docs/deliverable3.tex. Needs a GPU for the model.

    python scripts/evaluate_d3.py                     # everything (about 2 hours on a T4)
    python scripts/evaluate_d3.py --limit 2           # smoke test, 2 questions per set
    python scripts/evaluate_d3.py --configs agentes   # only some configurations
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.evaluation import environment, load_questions  # noqa: E402
from sql4business.evaluation_d3 import CONFIGS, latex_table, load_results, run_config  # noqa: E402
from sql4business.model import MODEL_ID, GenerationSettings, HuggingFaceGenerator  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--db", default=ROOT / "data" / "business.db", type=Path)
    parser.add_argument("--questions", default=ROOT / "data" / "questions.json", type=Path)
    parser.add_argument("--heldout", default=ROOT / "data" / "paraphrases.json", type=Path)
    parser.add_argument("--configs", default=",".join(CONFIGS), help="comma-separated subset of " + ",".join(CONFIGS))
    parser.add_argument("--limit", type=int, default=0, help="questions per set, for a smoke test")
    parser.add_argument("--output-dir", default=ROOT / "results" / "deliverable3", type=Path)
    parser.add_argument("--resume", action="store_true", help="skip configurations that already have a results file")
    parser.add_argument("--tex", default=ROOT / "docs" / "deliverable3_results.tex", type=Path)
    args = parser.parse_args()

    configs = [name.strip() for name in args.configs.split(",") if name.strip()]
    unknown = [name for name in configs if name not in CONFIGS]
    if unknown:
        parser.error(f"unknown configurations: {', '.join(unknown)}")
    official = load_questions(args.questions)
    question_sets = {"official": official["questions"], "heldout": load_questions(args.heldout)["questions"]}
    if args.limit:
        question_sets = {name: questions[: args.limit] for name, questions in question_sets.items()}
        args.output_dir = args.output_dir / "prueba"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    generator = HuggingFaceGenerator(args.model, GenerationSettings())
    meta = {**environment(generator), "settings": vars(generator.settings), "limit": args.limit}
    for config in configs:
        path = args.output_dir / f"{config}.json"
        if args.resume and path.exists():
            print(f"skip {config}: {path} exists")
            continue
        started = time.perf_counter()
        trace_dir = args.output_dir / "trazas" if config == "agentes" else None
        result = run_config(config, generator, args.db, question_sets, official["schema"], trace_dir=trace_dir)
        result["meta"] = {**meta, "seconds": round(time.perf_counter() - started, 1)}
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"saved {path}")

    first_run = ROOT / "results" / "deliverable3_v1"
    summaries = load_results(args.output_dir, first_run=None if args.limit else first_run)
    print()
    for config, summary in summaries.items():
        cells = []
        for set_name, data in summary.items():
            metrics = " ".join(f"{m}={v['global'][0]}/{v['global'][1]}" for m, v in data.items() if isinstance(v, dict))
            cells.append(f"{set_name}: {metrics} calls={data['model_calls']}")
        print(f"{config:20} " + " | ".join(cells))
    tex = args.tex if not args.limit else args.output_dir / "deliverable3_results.tex"
    tex.write_text(latex_table(summaries), encoding="utf-8")
    print(f"\nwrote {tex}")


if __name__ == "__main__":
    main()
