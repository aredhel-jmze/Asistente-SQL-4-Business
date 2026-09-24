#!/usr/bin/env python3
"""Run direct baseline and structured solution on the same questions."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows Python does not expose resource.
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.evaluation import (  # noqa: E402
    answer_matches,
    execute_baseline_output,
    gold_composed_answer,
    matches_gold_results,
)
from sql4business.model import HuggingFaceGenerator, direct_prompt  # noqa: E402
from sql4business.pipeline import BusinessAssistant  # noqa: E402
from sql4business.sql_tools import connect_read_only  # noqa: E402


def _gpu_peak_memory_mb() -> float | None:
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    return torch.cuda.max_memory_allocated() / (1024 * 1024)


def _measure_call(function):
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass
    started = time.perf_counter()
    value = function()
    elapsed = time.perf_counter() - started
    process_memory_mb = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        if resource is not None
        else None
    )
    return value, elapsed, process_memory_mb, _gpu_peak_memory_mb()


def run_direct_baseline(generator, db_path: str, schema: str, question: dict):
    def run():
        raw = generator.generate(direct_prompt(schema, question["question"]))
        conn = connect_read_only(db_path)
        try:
            executions = execute_baseline_output(conn, raw)
        finally:
            conn.close()
        return raw, executions

    (raw, executions), elapsed, process_memory_mb, gpu_memory_mb = _measure_call(run)
    correct = matches_gold_results(
        [item.rows for item in executions], question["gold_result"]
    )
    return raw, executions, correct, elapsed, process_memory_mb, gpu_memory_mb


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-Coder-3B-Instruct")
    parser.add_argument("--db", default=str(ROOT / "data" / "business.db"))
    parser.add_argument("--questions", default=str(ROOT / "data" / "questions.json"))
    parser.add_argument("--output", default=str(ROOT / "results" / "deliverable2_comparison.json"))
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    data = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    questions = data["questions"][: args.limit or None]
    generator = HuggingFaceGenerator(args.model)
    assistant = BusinessAssistant(args.db, generator, max_retries=args.max_retries)
    records = []

    for index, question in enumerate(questions, start=1):
        print(f"[{index}/{len(questions)}] {question['id']}: baseline...", flush=True)
        (
            raw,
            baseline_executions,
            baseline_correct,
            baseline_elapsed,
            baseline_process_memory,
            baseline_gpu_memory,
        ) = run_direct_baseline(
            generator, args.db, data["schema"], question
        )
        print(f"[{index}/{len(questions)}] {question['id']}: structured solution...", flush=True)
        try:
            solution, solution_elapsed, solution_process_memory, solution_gpu_memory = _measure_call(
                lambda: assistant.answer(question["question"])
            )
            solution_sql_correct = matches_gold_results(
                [item.rows for item in solution.executions], question["gold_result"]
            )
            solution_answer_correct = answer_matches(
                solution.composed.value,
                gold_composed_answer(question, args.db),
            )
            records.append(
                {
                    "id": question["id"],
                    "type": question["type"],
                    "question": question["question"],
                    "baseline": {
                        "model_output": raw,
                        "executions": [
                            {"sql": item.sql, "rows": item.rows, "error": item.error}
                            for item in baseline_executions
                        ],
                        "execution_correct": baseline_correct,
                        "duration_seconds": baseline_elapsed,
                        "process_peak_memory_mb": baseline_process_memory,
                        "gpu_peak_memory_mb": baseline_gpu_memory,
                    },
                    "solution": {
                        "plan": solution.plan,
                        "executions": [
                            {"sql": item.sql, "rows": item.rows, "error": item.error}
                            for item in solution.executions
                        ],
                        "composed_answer": solution.composed.value,
                        "report": solution.report,
                        "execution_correct": solution_sql_correct,
                        "answer_correct": solution_answer_correct,
                        "report_fidelity": solution.report_fidelity,
                        "full_correct": (
                            solution_sql_correct
                            and solution_answer_correct
                            and solution.report_fidelity
                        ),
                        "duration_seconds": solution_elapsed,
                        "process_peak_memory_mb": solution_process_memory,
                        "gpu_peak_memory_mb": solution_gpu_memory,
                        "attempts": solution.attempts,
                        "errors": solution.errors,
                    },
                }
            )
            print(
                f"[{index}/{len(questions)}] {question['id']}: "
                f"done, full_correct={records[-1]['solution']['full_correct']}",
                flush=True,
            )
        except Exception as exc:
            records.append(
                {
                    "id": question["id"],
                    "type": question["type"],
                    "question": question["question"],
                    "baseline": {"execution_correct": baseline_correct},
                    "solution": {"full_correct": False, "error": str(exc)},
                }
            )
            print(f"[{index}/{len(questions)}] {question['id']}: failed: {exc}", flush=True)

    output = Path(args.output)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved comparison for {len(records)} questions to {output}")
    for group in ("puntual", "combinada"):
        subset = [record for record in records if record["type"] == group]
        if not subset:
            print(f"{group}: no questions in this run")
            continue
        baseline = sum(record["baseline"].get("execution_correct", False) for record in subset) / len(subset)
        solution = sum(record["solution"].get("execution_correct", False) for record in subset) / len(subset)
        full = sum(record["solution"].get("full_correct", False) for record in subset) / len(subset)
        print(f"{group}: baseline SQL={baseline:.2%}, solution SQL={solution:.2%}, solution full={full:.2%}")


if __name__ == "__main__":
    main()
