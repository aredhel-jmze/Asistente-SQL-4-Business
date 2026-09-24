#!/usr/bin/env python3
"""Evaluate the structured solution on the unchanged 15-question set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.evaluation import answer_matches, gold_composed_answer, matches_gold_results  # noqa: E402
from sql4business.model import HuggingFaceGenerator  # noqa: E402
from sql4business.pipeline import BusinessAssistant  # noqa: E402
from sql4business.sql_tools import rows_to_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-Coder-3B-Instruct")
    parser.add_argument("--db", default=str(ROOT / "data" / "business.db"))
    parser.add_argument("--questions", default=str(ROOT / "data" / "questions.json"))
    parser.add_argument("--output", default=str(ROOT / "results" / "deliverable2_solution_results.json"))
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args()

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    assistant = BusinessAssistant(
        args.db,
        HuggingFaceGenerator(args.model),
        max_retries=args.max_retries,
    )
    records = []
    for question in questions:
        try:
            result = assistant.answer(question["question"])
            generated = [execution.rows for execution in result.executions]
            sql_correct = matches_gold_results(generated, question["gold_result"])
            answer_correct = answer_matches(
                result.composed.value,
                gold_composed_answer(question, args.db),
            )
            report_correct = result.report_fidelity
            records.append(
                {
                    "id": question["id"],
                    "type": question["type"],
                    "question": question["question"],
                    "plan": result.plan,
                    "executions": [
                        {"sql": item.sql, "rows": rows_to_json(item.rows), "error": item.error}
                        for item in result.executions
                    ],
                    "composed_answer": result.composed.value,
                    "report": result.report,
                    "sql_correct": sql_correct,
                    "answer_correct": answer_correct,
                    "report_correct": report_correct,
                    "correct": sql_correct and answer_correct and report_correct,
                    "attempts": result.attempts,
                    "errors": result.errors,
                }
            )
        except Exception as exc:
            records.append(
                {
                    "id": question["id"],
                    "type": question["type"],
                    "question": question["question"],
                    "correct": False,
                    "error": str(exc),
                }
            )

    output = Path(args.output)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(records)} solution records to {output}")
    for group in ("puntual", "combinada"):
        subset = [record for record in records if record["type"] == group]
        score = sum(record.get("correct", False) for record in subset) / len(subset)
        print(f"{group}: {score:.2%}")


if __name__ == "__main__":
    main()
