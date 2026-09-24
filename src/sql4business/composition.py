"""Deterministic composition of SQL results for combined questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_OPERATIONS = {
    "direct",
    "growth_pct",
    "compare_equal",
    "share_pct",
    "filter_then_rank",
}


@dataclass
class ComposedAnswer:
    operation: str
    value: Any
    details: dict[str, Any]


def _scalar(rows: list[tuple[Any, ...]]) -> Any:
    if not rows or not rows[0]:
        return None
    return rows[0][0]


def _number(value: Any) -> float:
    if value is None:
        raise ValueError("A numeric SQL result was required; SQLite returned NULL.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"A numeric SQL result was required; received {value!r}."
        ) from exc


def compose_results(
    operation: str,
    result_sets: list[list[tuple[Any, ...]]],
    conn=None,
) -> ComposedAnswer:
    """Apply a named operation without asking the language model to calculate."""

    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"Unsupported composition operation: {operation}")
    if not result_sets:
        raise ValueError("At least one SQL result is required.")

    if operation == "direct":
        value = result_sets[0][0][0] if len(result_sets[0]) == 1 and len(result_sets[0][0]) == 1 else result_sets[0]
        return ComposedAnswer(operation, value, {"result": result_sets[0]})

    if len(result_sets) < 2:
        raise ValueError(f"Operation {operation} requires at least two results.")

    first = _scalar(result_sets[0])
    second = _scalar(result_sets[1])

    if operation == "growth_pct":
        previous = _number(first)
        current = _number(second)
        if previous == 0:
            raise ValueError("Cannot calculate growth from a zero denominator.")
        growth = (current - previous) / previous * 100
        return ComposedAnswer(
            operation,
            {"previous": previous, "current": current, "growth_pct": growth, "direction": "increase" if growth >= 0 else "decrease"},
            {"previous": previous, "current": current},
        )

    if operation == "compare_equal":
        changed = first != second
        return ComposedAnswer(
            operation,
            {"first": first, "second": second, "changed": changed},
            {},
        )

    if operation == "share_pct":
        total = _number(first)
        subset = _number(second)
        if total == 0:
            raise ValueError("Cannot calculate share from a zero denominator.")
        share = subset / total * 100
        return ComposedAnswer(
            operation,
            {"total": total, "subset": subset, "share_pct": share},
            {},
        )

    ranking_rows = list(result_sets[1])
    if ranking_rows and len(ranking_rows[0]) > 1 and all(
        isinstance(row[-1], (int, float)) for row in ranking_rows
    ):
        ranking_rows.sort(key=lambda row: row[-1], reverse=True)
    ranked_names = [
        row[0] if isinstance(row[0], str) else row[1]
        for row in ranking_rows
        if row
    ]
    winner = None
    if conn is not None:
        name_to_id = {
            row[1]: row[0]
            for row in conn.execute("SELECT product_id, name FROM products").fetchall()
        }
        filtered_ids = {
            value if isinstance(value, (int, float)) else name_to_id.get(value)
            for row in result_sets[0]
            if row
            for value in [row[0]]
        }
        winner = next(
            (name for name in ranked_names if name_to_id.get(name) in filtered_ids),
            None,
        )
    else:
        filtered_ids = {row[0] for row in result_sets[0] if row}
    return ComposedAnswer(
        operation,
        {"winner": winner},
        {"filtered_ids": sorted(filtered_ids), "ranked_names": ranked_names},
    )


def answer_claim_values(answer: Any) -> list[Any]:
    """Flatten values that a faithful report should preserve."""

    values: list[Any] = []
    if isinstance(answer, dict):
        for key, value in answer.items():
            if key != "changed" and key != "direction":
                values.extend(answer_claim_values(value))
    elif isinstance(answer, list):
        for value in answer:
            values.extend(answer_claim_values(value))
    elif isinstance(answer, (int, float, str)) and not isinstance(answer, bool):
        values.append(answer)
    return values
