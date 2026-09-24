"""End-to-end structured assistant for Deliverable 2."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .composition import ComposedAnswer, compose_results
from .model import TextGenerator, parse_json_object, plan_prompt, report_prompt
from .sql_tools import (
    QueryExecution,
    SQLExecutionError,
    SQLValidationError,
    connect_read_only,
    execute_read_only,
    retrieve_relevant_schema,
    rows_to_json,
    normalize_text,
)


@dataclass
class AssistantResult:
    question: str
    plan: dict[str, Any]
    executions: list[QueryExecution]
    composed: ComposedAnswer
    report: str
    report_claims: list[str]
    report_fidelity: bool
    attempts: int
    schema_context: str
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["executions"] = [
            {
                "sql": execution.sql,
                "rows": rows_to_json(execution.rows),
                "error": execution.error,
            }
            for execution in self.executions
        ]
        return result


class BusinessAssistant:
    """Plan, execute, compose and report with a bounded retry budget."""

    def __init__(self, db_path: str | Path, generator: TextGenerator, max_retries: int = 2):
        self.db_path = Path(db_path)
        self.generator = generator
        self.max_retries = max(0, max_retries)

    def answer(self, question: str) -> AssistantResult:
        schema = retrieve_relevant_schema(self.db_path, question)
        expected_operation = infer_operation(question)
        errors: list[str] = []
        last_error: str | None = None
        last_plan: dict[str, Any] | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                raw_plan = self.generator.generate(
                    plan_prompt(schema, question, last_error, expected_operation)
                )
                parsed_plan = parse_json_object(raw_plan)
                last_plan = parsed_plan
                plan = self._validate_plan(parsed_plan, expected_operation)
                last_plan = plan
                executions, composed = self._execute_plan(plan)
                report, claims = self._generate_report(question, composed.value)
                fidelity = self._validate_report(report, composed.value)
                return AssistantResult(
                    question=question,
                    plan=plan,
                    executions=executions,
                    composed=composed,
                    report=report,
                    report_claims=claims,
                    report_fidelity=fidelity,
                    attempts=attempt,
                    schema_context=schema,
                    errors=errors,
                )
            except (ValueError, SQLValidationError, SQLExecutionError) as exc:
                plan_context = ""
                if last_plan is not None:
                    plan_context = f" Plan: {json.dumps(last_plan, ensure_ascii=False)}."
                last_error = f"{exc}.{plan_context}"
                errors.append(f"attempt_{attempt}: {last_error}")

        raise RuntimeError(
            f"The assistant failed after {self.max_retries + 1} attempts: {errors[-1]}"
        )

    @staticmethod
    def _validate_plan(
        plan: dict[str, Any], expected_operation: str | None = None
    ) -> dict[str, Any]:
        steps = plan.get("steps")
        composition = plan.get("composition")
        if not isinstance(steps, list) or not steps:
            raise ValueError("The plan must contain at least one step.")
        if not isinstance(composition, dict):
            raise ValueError("The plan must contain a composition object.")
        operation = composition.get("operation")
        allowed = {"direct", "growth_pct", "compare_equal", "share_pct", "filter_then_rank"}
        if operation not in allowed:
            raise ValueError(f"Unsupported composition operation: {operation}")
        if expected_operation and operation != expected_operation:
            raise ValueError(
                f"The question requires {expected_operation}, but the plan uses {operation}."
            )
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or not isinstance(step.get("sql"), str):
                raise ValueError(f"Step {index} must contain SQL text.")
        if operation == "direct" and len(steps) > 1:
            # Small models sometimes return a helper query followed by the
            # answer query for a punctual question. Keep the final executable
            # candidate and make
            # the normalization visible in the trace.
            plan = dict(plan)
            plan["steps"] = steps[-1:]
            warnings = list(plan.get("warnings", []))
            warnings.append("Multiple direct steps reduced to the final step.")
            plan["warnings"] = warnings
        if operation != "direct" and len(steps) != 2:
            raise ValueError(f"Operation {operation} requires exactly two steps.")
        return plan

    def _execute_plan(self, plan: dict[str, Any]) -> tuple[list[QueryExecution], ComposedAnswer]:
        conn = connect_read_only(self.db_path)
        executions: list[QueryExecution] = []
        try:
            for step in plan["steps"]:
                executions.append(execute_read_only(conn, step["sql"]))
            if any(
                value is None
                for execution in executions
                for row in execution.rows
                for value in row
            ):
                raise SQLExecutionError(
                    "A SQL step returned NULL. Check exact database labels, joins, "
                    "and date filters before retrying."
                )
            rows = [execution.rows for execution in executions]
            try:
                composed = compose_results(plan["composition"]["operation"], rows, conn=conn)
            except ValueError as exc:
                raise ValueError(f"{exc} Result sets: {rows!r}") from exc
            return executions, composed
        finally:
            conn.close()

    def _generate_report(self, question: str, answer: Any) -> tuple[str, list[str]]:
        raw = self.generator.generate(report_prompt(question, answer))
        try:
            payload = parse_json_object(raw)
            text = payload.get("answer")
            claims = payload.get("claims", [])
            if not isinstance(text, str) or not isinstance(claims, list):
                raise ValueError("The report JSON has an invalid shape.")
            return text.strip(), [str(claim) for claim in claims]
        except ValueError:
            # The verified answer remains available even if the reporter breaks its format.
            return (
                "Verified result: "
                + json.dumps(answer, ensure_ascii=False, sort_keys=True),
                [],
            )

    @staticmethod
    def _validate_report(report: str, answer: Any) -> bool:
        """Check answer-level values without requiring every intermediate value."""

        if not report.strip():
            return False
        required_values: list[Any] = []
        if isinstance(answer, dict) and "growth_pct" in answer:
            required_values.append(answer["growth_pct"])
        elif isinstance(answer, dict) and "share_pct" in answer:
            required_values.append(answer["share_pct"])
        elif isinstance(answer, dict) and "winner" in answer:
            required_values.append(answer["winner"])
        elif isinstance(answer, dict) and "first" in answer and "second" in answer:
            required_values.extend([answer["first"], answer["second"]])
        elif isinstance(answer, list) and answer and all(
            isinstance(row, (list, tuple)) for row in answer
        ):
            required_values.extend(row[0] for row in answer if row)
        else:
            required_values.extend(_flatten_values(answer))

        normalized_report = report.replace(",", "")
        for value in required_values:
            if value is None:
                return False
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                rounded = f"{value:.2f}".rstrip("0").rstrip(".")
                if rounded not in normalized_report and f"{value:g}" not in normalized_report:
                    return False
            elif normalize_text(str(value)) not in normalize_text(report):
                return False
        return True


def _flatten_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values: list[Any] = []
        for item in value.values():
            values.extend(_flatten_values(item))
        return values
    if isinstance(value, (list, tuple)):
        values: list[Any] = []
        for item in value:
            values.extend(_flatten_values(item))
        return values
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return [value]
    return []


def infer_operation(question: str) -> str:
    """Classify composition intent before the model writes SQL steps."""

    text = normalize_text(question)
    if "stock" in text and ("ingreso" in text or "income" in text or "revenue" in text):
        return "filter_then_rank"
    if "lider" in text or "cada trimestre" in text or "producto top" in text:
        return "compare_equal"
    if (
        "crecimiento porcentual" in text
        or "growth percentage" in text
        or "percentage growth" in text
        or "en que porcentaje" in text
    ):
        return "growth_pct"
    if "cuantas unidades totales" in text and "cuantas de esas" in text:
        return "share_pct"
    return "direct"


def save_trace(result: AssistantResult, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
