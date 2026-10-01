"""Shared evaluation: the same inputs and correctness criteria for every system.

Systems
  baseline  Deliverable 1 direct prompt with the plain schema (unchanged).
  grounded  Same direct prompt with the grounded schema (alternative strategy).
  solution  Structured assistant (plan, read-only SQL, composition, report).

Metrics
  sql       Deliverable 1 criterion: every gold result set is produced exactly
            by some executed statement.
  answer    The final answer is correct, whatever SQL produced it.
  report    (solution) the model-written report passes the fidelity check.
  e2e       (solution) answer and report are both correct.
"""

from __future__ import annotations

import json
import math
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .composition import ComposedAnswer, compose_results
from .model import BASELINE_MAX_NEW_TOKENS, TextGenerator, baseline_prompt
from .pipeline import AssistantFailure, BusinessAssistant
from .sql_tools import connect_read_only, execute_baseline, grounded_schema, rows_to_json


SYSTEMS = ("baseline", "grounded", "solution")
METRICS = {"baseline": ("sql", "answer"), "grounded": ("sql", "answer"), "solution": ("sql", "answer", "report", "e2e")}
DEFAULT_ANSWER_FIELDS = {
    "growth_pct": ["growth_pct"],
    "share_pct": ["share_pct"],
    "compare_equal": ["first", "second"],
    "filter_then_rank": ["winner"],
}


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 6)
    return value


def _row_set(rows: Iterable[Iterable[Any]]) -> frozenset:
    return frozenset(tuple(_normalize(value) for value in row) for row in rows)


def sql_correct(generated: list[list[Any]], gold: list[list[Any]]) -> bool:
    """Deliverable 1 criterion: each gold result set equals some executed result set."""

    produced = [_row_set(rows) for rows in generated]
    return all(_row_set(rows) in produced for rows in gold)


def expected_answer(question: dict[str, Any], db_path: str | Path) -> ComposedAnswer:
    conn = connect_read_only(db_path)
    try:
        return compose_results(question.get("combine", "direct"), question["gold_result"], conn=conn)
    finally:
        conn.close()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _label(row: Iterable[Any]) -> str | None:
    return next((value for value in row if isinstance(value, str)), None)


def _close(value: Any, target: float, percentage: bool) -> bool:
    if percentage:
        return abs(float(value) - target) <= 0.05
    return math.isclose(float(value), target, rel_tol=1e-6, abs_tol=1e-6)


def candidate_sets(value: Any) -> list[list[tuple[Any, ...]]]:
    """Express a composed answer as result sets, the form baseline answers have."""

    if isinstance(value, dict):
        if "winner" in value:
            return [[(value["winner"],)]]
        if "first" in value:
            return [[(value["first"],), (value["second"],)]]
        return [[tuple(v for v in value.values() if _is_number(v))]]
    if isinstance(value, list):
        return [[tuple(row) for row in value]]
    return [[(value,)]]


def answer_correct(result_sets: list[list[Any]], question: dict[str, Any], expected: ComposedAnswer) -> bool:
    """Whether the result sets contain the requested final answer (same rule for every system)."""

    sets = [[tuple(row) for row in rows] for rows in result_sets]
    operation = question.get("combine", "direct")
    gold = expected.value
    if operation == "direct":
        if isinstance(gold, list):
            wanted = sorted(str(_label(row) or row[0]) for row in gold)
            return any(len(rows) == len(gold) and sorted(str(_label(r) or r[0]) for r in rows) == wanted for rows in sets)
        if isinstance(gold, str):
            return any(len(rows) == 1 and gold in rows[0] for rows in sets)
        return any(len(rows) == 1 and any(_is_number(v) and _close(v, gold, False) for v in rows[0]) for rows in sets)
    fields = question.get("answer_fields") or DEFAULT_ANSWER_FIELDS[operation]
    # Final answers are short results (one row, or one row per compared period);
    # long listings do not count even if they happen to contain the value.
    short = [rows for rows in sets if 1 <= len(rows) <= 2]
    if operation in ("growth_pct", "share_pct"):
        numbers = [v for rows in short for row in rows for v in row if _is_number(v)]
        return all(any(_close(n, gold[f], f.endswith("_pct")) for n in numbers) for f in fields)
    if operation == "compare_equal":
        labels = {_label(row) for rows in short for row in rows} - {None}
        return labels == {gold["first"], gold["second"]}
    allowed = set(expected.details["filtered"])
    return any(rows and _label(rows[0]) == gold["winner"] and all(_label(r) in allowed for r in rows) for rows in sets)


def _direct_system(
    generator: TextGenerator, schema: str, db_path: Path, question: dict[str, Any], expected: ComposedAnswer
) -> dict[str, Any]:
    started = time.perf_counter()
    raw = generator.generate(baseline_prompt(schema, question["question"]), BASELINE_MAX_NEW_TOKENS)
    conn = connect_read_only(db_path)
    try:
        executions = execute_baseline(conn, raw)
    finally:
        conn.close()
    rows = [execution.rows for execution in executions]
    return {
        "model_output": raw,
        "executions": [{"sql": e.sql, "rows": rows_to_json(e.rows), "error": e.error} for e in executions],
        "sql": sql_correct(rows, question["gold_result"]),
        "answer": answer_correct(rows, question, expected),
        "seconds": round(time.perf_counter() - started, 2),
    }


def _solution_system(assistant: BusinessAssistant, question: dict[str, Any], expected: ComposedAnswer) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = assistant.answer(question["question"])
    except AssistantFailure as failure:
        return {
            "error": str(failure),
            "attempts": failure.attempts,
            "sql": False, "answer": False, "report": False, "e2e": False,
            "seconds": round(time.perf_counter() - started, 2),
        }
    answer = answer_correct(candidate_sets(result.composed.value), question, expected)
    return {
        **result.to_dict(),
        "sql": sql_correct([e.rows for e in result.executions], question["gold_result"]),
        "answer": answer,
        "report": result.fidelity.faithful,
        "e2e": answer and result.fidelity.faithful,
        "seconds": round(time.perf_counter() - started, 2),
    }


def evaluate(
    generator: TextGenerator,
    db_path: str | Path,
    question_sets: dict[str, list[dict[str, Any]]],
    baseline_schema: str,
    systems: Iterable[str] = SYSTEMS,
    max_retries: int = 2,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run every system on every question and return records plus a summary."""

    db_path = Path(db_path)
    systems = tuple(systems)
    assistant = BusinessAssistant(db_path, generator, max_retries=max_retries)
    schemas = {"baseline": baseline_schema, "grounded": grounded_schema(db_path)}
    records = []
    for set_name, questions in question_sets.items():
        for question in questions:
            expected = expected_answer(question, db_path)
            record = {
                "set": set_name,
                "id": question["id"],
                "source_id": question.get("source_id", question["id"]),
                "type": question["type"],
                "question": question["question"],
                "expected": expected.value,
            }
            for system in systems:
                if system == "solution":
                    record[system] = _solution_system(assistant, question, expected)
                else:
                    record[system] = _direct_system(generator, schemas[system], db_path, question, expected)
            records.append(record)
            flags = " ".join(
                f"{system}[" + ",".join(f"{m}={int(record[system][m])}" for m in METRICS[system]) + "]"
                for system in systems
            )
            progress(f"{set_name}/{question['id']}: {flags}")
    return {"summary": summarize(records, systems), "records": records}


def summarize(records: list[dict[str, Any]], systems: Iterable[str] = SYSTEMS) -> dict[str, Any]:
    """Counts [correct, total] per set, system, metric and question type."""

    summary: dict[str, Any] = {}
    for set_name in dict.fromkeys(record["set"] for record in records):
        subset = [record for record in records if record["set"] == set_name]
        summary[set_name] = {}
        for system in systems:
            summary[set_name][system] = {}
            for metric in METRICS[system]:
                counts = {}
                for group in ("puntual", "combinada", "global"):
                    rows = [r for r in subset if group == "global" or r["type"] == group]
                    counts[group] = [sum(bool(r[system][metric]) for r in rows), len(rows)]
                summary[set_name][system][metric] = counts
    return summary


def environment(generator: Any = None) -> dict[str, Any]:
    """Versions, hardware and code revision behind a run."""

    info: dict[str, Any] = {"python": platform.python_version()}
    for module in ("torch", "transformers", "accelerate", "bitsandbytes"):
        try:
            info[module] = __import__(module).__version__
        except ImportError:
            info[module] = None
    try:
        import torch

        info["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except ImportError:
        info["gpu"] = None
    try:
        info["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        info["commit"] = None
    model = getattr(generator, "model", None)
    info["model"] = getattr(generator, "model_id", None)
    info["model_revision"] = getattr(getattr(model, "config", None), "_commit_hash", None)
    return info


def latex_table(summary: dict[str, Any]) -> str:
    """Results table for docs/deliverable2.tex, generated from the results file."""

    def cell(counts: list[int]) -> str:
        return f"{counts[0]}/{counts[1]}"

    labels = {
        "baseline": "Prompt directo (baseline D1)",
        "grounded": "Prompt directo + esquema enriquecido",
        "solution": "Solución estructurada",
    }
    lines = [
        r"\begin{tabular}{@{}llccccc@{}}",
        r"\toprule",
        r"& & \multicolumn{3}{c}{\textbf{Set oficial (in-sample)}} & \multicolumn{2}{c}{\textbf{Paráfrasis (held-out)}} \\",
        r"\cmidrule(lr){3-5}\cmidrule(l){6-7}",
        r"\textbf{Sistema} & \textbf{Métrica} & Puntual & Combinada & Total & Puntual & Combinada \\",
        r"\midrule",
    ]
    for system in ("baseline", "grounded", "solution"):
        for index, metric in enumerate(METRICS[system]):
            official = summary["official"][system][metric]
            heldout = summary["heldout"][system][metric]
            name = labels[system] if index == 0 else ""
            lines.append(
                f"{name} & {metric} & {cell(official['puntual'])} & {cell(official['combinada'])} & "
                f"{cell(official['global'])} & {cell(heldout['puntual'])} & {cell(heldout['combinada'])} \\\\"
            )
        lines.append(r"\addlinespace" if system != "solution" else r"\bottomrule")
    lines.append(r"\end{tabular}")
    return "\n".join(lines) + "\n"


def load_questions(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
