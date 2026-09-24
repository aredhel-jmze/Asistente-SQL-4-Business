"""Shared correctness contract for baseline and structured solution."""

from __future__ import annotations

import math
from typing import Any

from .composition import compose_results
from .sql_tools import (
    QueryExecution,
    connect_read_only,
    execute_read_only,
    extract_sql_statements,
)


def _equal_value(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def _normalize_row(row: Any) -> tuple[Any, ...]:
    return tuple(row)


def _same_result_set(left: list[Any], right: list[Any]) -> bool:
    if len(left) != len(right):
        return False
    unmatched = list(right)
    for candidate in left:
        match_index = next(
            (
                index
                for index, expected in enumerate(unmatched)
                if _row_contains(candidate, expected)
            ),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return not unmatched


def _row_contains(candidate: Any, expected: Any) -> bool:
    """Allow useful extra columns while preserving the expected values/order."""

    if len(candidate) < len(expected):
        return False
    expected_index = 0
    for value in candidate:
        if expected_index < len(expected) and _equal_value(value, expected[expected_index]):
            expected_index += 1
    return expected_index == len(expected)


def matches_gold_results(
    generated_results: list[list[Any]], gold_results: list[list[Any]]
) -> bool:
    """Preserve the Deliverable 1 criterion: every gold set must be produced."""

    return all(
        any(_same_result_set(generated, gold) for generated in generated_results)
        for gold in gold_results
    )


def execute_baseline_output(conn, raw_output: str) -> list[QueryExecution]:
    executions: list[QueryExecution] = []
    for statement in extract_sql_statements(raw_output):
        try:
            executions.append(execute_read_only(conn, statement))
        except Exception as exc:  # Baseline failures are evidence, not crashes.
            executions.append(QueryExecution(statement, [], str(exc)))
    return executions


def gold_composed_answer(question: dict[str, Any], db_path: str | None = None) -> Any:
    gold = [
        [tuple(row) for row in result_set]
        for result_set in question["gold_result"]
    ]
    operation = question.get("combine", "direct")
    if operation != "filter_then_rank" or db_path is None:
        return compose_results(operation, gold).value
    conn = connect_read_only(db_path)
    try:
        return compose_results(operation, gold, conn=conn).value
    finally:
        conn.close()


def answer_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and answer_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if (
            isinstance(actual, list)
            and actual
            and expected
            and all(isinstance(row, (list, tuple)) for row in actual)
            and all(isinstance(row, (list, tuple)) for row in expected)
        ):
            return _same_result_set(actual, expected)
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            answer_matches(left, right) for left, right in zip(actual, expected)
        )
    if isinstance(actual, list) and actual and all(
        isinstance(row, (list, tuple)) for row in actual
    ):
        return any(_row_contains(row, [expected]) for row in actual)
    return _equal_value(actual, expected)
