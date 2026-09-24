#!/usr/bin/env python3
"""Insert measured comparison values and a real failure into deliverable2.tex."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def percentage(records: list[dict], system: str, field: str, group: str) -> str:
    subset = [record for record in records if record["type"] == group]
    values = [record[system].get(field, False) for record in subset]
    return f"{sum(values) / len(values) * 100:.1f}\\%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results/deliverable2_comparison.json")
    parser.add_argument("--tex", default="docs/deliverable2.tex")
    args = parser.parse_args()

    results_path = Path(args.results)
    tex_path = Path(args.tex)
    records = json.loads(results_path.read_text(encoding="utf-8"))
    punctual = [record for record in records if record["type"] == "puntual"]
    combined = [record for record in records if record["type"] == "combinada"]
    failure = next(
        (record for record in records if not record.get("solution", {}).get("full_correct", False)),
        None,
    )
    if failure is None:
        failure_text = "no failure was observed in the measured set"
    else:
        solution = failure.get("solution", {})
        raw_error = solution.get("error") or "; ".join(solution.get("errors", []))
        if "no such column" in raw_error:
            reason = "the generated SQL referenced a column unavailable in the selected table"
        elif "no such table" in raw_error:
            reason = "the generated SQL referenced a result that does not persist between steps"
        elif "requires exactly two steps" in raw_error:
            reason = "the planner returned an invalid number of steps for a combined operation"
        else:
            reason = "the solution failed its correctness checks"
        failure_text = f"{failure['id']}: {reason}"

    text = tex_path.read_text(encoding="utf-8")
    row = (
        "Structured solution (SQL) & "
        f"{percentage(records, 'solution', 'execution_correct', 'puntual')} & "
        f"{percentage(records, 'solution', 'execution_correct', 'combinada')} & "
        f"{percentage(records, 'solution', 'execution_correct', 'puntual')}"
    )
    global_sql = sum(record.get("solution", {}).get("execution_correct", False) for record in records) / len(records)
    row = row.rsplit("&", 1)[0] + f"& {global_sql * 100:.1f}\\%" + r" \\"
    text = re.sub(r"(?m)^Structured solution(?: \(SQL\))? &.*$", lambda _: row, text)
    full_punctual = percentage(records, "solution", "full_correct", "puntual")
    full_combined = percentage(records, "solution", "full_correct", "combinada")
    full_global = f"{sum(record.get('solution', {}).get('full_correct', False) for record in records) / len(records) * 100:.1f}\\%"
    full_summary = (
        r"\textbf{End-to-end correctness.} Including SQL execution, composed answer, "
        f"and report fidelity, the structured solution achieves {full_punctual}, "
        f"{full_combined}, and {full_global} globally."
    )
    text = re.sub(r"(?m)^\\textbf\{End-to-end correctness\.\}.*$", lambda _: full_summary, text)
    text = text.replace("FAILURE_CASE", failure_text)
    failure_sentence = (
        r"\textbf{Failure case:} \texttt{" + failure_text + r"}. "
        r"It is reported from a real trace without replacing its output with gold data."
    )
    text = re.sub(
        r"\\textbf\{Failure case:\}.*?It is reported from a real trace without "
        r"replacing its output with gold data\.",
        lambda _: failure_sentence,
        text,
    )
    text = text.replace(
        "The Measured results from the committed Colab execution.",
        "Measured results from the committed Colab execution.",
    )
    tex_path.write_text(text, encoding="utf-8")
    print(f"Updated {tex_path} from {results_path}.")


if __name__ == "__main__":
    main()
