"""Deliverable 3: the Deliverable 2 solution rebuilt as a system of four agents.

Every agent is a call to the same model (Qwen2.5-Coder-3B-Instruct) with its own role, its own
prompt (a versioned file in ``prompts/``), a defined input and output, and a decision about what
happens next:

  Planificador  question -> JSON plan. Revises its own output when the JSON is invalid.
  Analista SQL  plan -> verified result. Runs the tools; on an error it asks the model whether to
                fix the failing step itself or to send the plan back to the Planificador.
  Reportero     verified result -> sentences for the manager. Rewrites them with the
                Verificador's feedback.
  Verificador   question + result + report -> accept, send the report back to the Reportero, or
                send the case back to the Planificador with a concrete reason.

Every message between agents and every tool call is logged in a ``Trace``. The prompting
techniques of each agent can be switched off one at a time (``disabled``) for the ablation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from .composition import ComposedAnswer, compose_results
from .fidelity import FidelityCheck, check_report
from .model import TextGenerator, parse_json_object
from .pipeline import expand_step_references, validate_plan
from .sql_tools import (
    QueryExecution,
    SQLExecutionError,
    connect_read_only,
    execute_read_only,
    grounded_schema,
    lint_sql,
    rows_to_json,
    normalize_text,
    sales_date_range,
    strip_fences,
    strip_literals,
)


PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_FILES = {
    "planner": "planificador.md",
    "analyst": "analista_sql.md",
    "reporter": "reportero.md",
    "verifier": "verificador.md",
}

USER, PLANNER, ANALYST, REPORTER, VERIFIER = "Usuario", "Planificador", "Analista SQL", "Reportero", "Verificador"

# Prompting techniques that the ablation removes one at a time (key -> agent, description).
TECHNIQUES = {
    "few_shot": (PLANNER, "ejemplos resueltos (few-shot)"),
    "decomposition": (PLANNER, "descomposición explícita: lo que pide la respuesta y los períodos antes del SQL"),
    "self_correction": (ANALYST, "autocorrección guiada por el error de las herramientas (self-debugging)"),
    "critic": (VERIFIER, "verificación con lista de chequeo contra el resultado (self-verification)"),
    "report_refine": (REPORTER, "refinamiento del reporte con la retroalimentación del Verificador (self-refine)"),
}

MAX_NEW_TOKENS = {"planner": 512, "analyst": 320, "reporter": 160, "verifier": 160}
ASKS_FOR = ("number", "name", "list", "percent_change", "share", "same_or_changed")
DESCRIPTIONS = {
    "number": "one number",
    "name": "one product or category name",
    "list": "a list of items",
    "percent_change": "a percentage change between two values",
    "share": "a part compared with its total",
    "same_or_changed": "the top item of two periods and whether it is the same",
}
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_IN_SQL = re.compile(r"\b(\d{4}-\d{2})(?:-\d{2})?\b")
BLOCK = re.compile(r"\[\[(?P<name>[a-z_]+)\]\]\n?(?P<body>.*?)\[\[/(?P=name)\]\]\n?", re.DOTALL)
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
BLOCK_TAG = re.compile(r"\[\[/?[a-z_]+\]\]")
# Errors that come from the model's work and can be repaired or replanned.
WORK_ERRORS = (ValueError, TypeError, KeyError, IndexError, SQLExecutionError)


# --------------------------------------------------------------------------- prompts


def load_template(path: str | Path) -> str:
    """Prompt file without its %% comment lines (documentation for humans only)."""

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.startswith("%%")).strip() + "\n"


def render_template(template: str, enabled: Iterable[str], **values: Any) -> str:
    """Keep the [[name]] blocks that are enabled ([[not_name]] when name is not), fill {{values}}."""

    enabled = set(enabled)

    def keep(name: str) -> bool:
        return name[4:] not in enabled if name.startswith("not_") else name in enabled

    text, previous = template, None
    while text != previous:  # Nested blocks are resolved from the outside in.
        previous = text
        text = BLOCK.sub(lambda match: match["body"] if keep(match["name"]) else "", text)

    def fill(match: re.Match) -> str:
        if match[1] not in values:
            raise KeyError(f"The prompt needs a value for {{{{{match[1]}}}}}.")
        return str(values[match[1]])

    if BLOCK_TAG.search(text):
        raise ValueError("Unbalanced technique block in a prompt file.")
    text = PLACEHOLDER.sub(fill, text)
    return re.sub(r"\n{3,}", "\n\n", text)


# --------------------------------------------------------------------------- traces


@dataclass
class Message:
    index: int
    sender: str
    receiver: str
    kind: str
    content: Any
    raw: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"n": self.index, "de": self.sender, "para": self.receiver, "tipo": self.kind, "contenido": self.content}
        if self.raw is not None:
            data["salida_modelo"] = self.raw
        return data


class Trace:
    """Messages exchanged between agents and tools for one question, in order."""

    def __init__(self, listener: Callable[[Message], None] | None = None):
        self.messages: list[Message] = []
        self.model_calls = {PLANNER: 0, ANALYST: 0, REPORTER: 0, VERIFIER: 0}
        self.listener = listener

    def add(self, sender: str, receiver: str, kind: str, content: Any, raw: str | None = None) -> None:
        message = Message(len(self.messages) + 1, sender, receiver, kind, content, raw)
        self.messages.append(message)
        if self.listener:
            self.listener(message)

    def to_list(self) -> list[dict[str, Any]]:
        return [message.to_dict() for message in self.messages]


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def format_message(message: Message | dict[str, Any], limit: int = 600) -> str:
    data = message.to_dict() if isinstance(message, Message) else message
    return f"[{data['n']:02d}] {data['de']} -> {data['para']} · {data['tipo']}\n     {_short(data['contenido'], limit)}"


def format_trace(messages: Iterable[Message | dict[str, Any]], limit: int = 600) -> str:
    return "\n".join(format_message(message, limit) for message in messages)


# --------------------------------------------------------------------------- results


@dataclass
class AnalystOutcome:
    status: str  # "ok", "replan" or "failed"
    steps: list[dict[str, Any]]
    executions: list[QueryExecution] = field(default_factory=list)
    composed: ComposedAnswer | None = None
    reason: str = ""


@dataclass
class Verdict:
    action: str  # "accept", "revise_report" or "replan"
    feedback: str
    fidelity: FidelityCheck
    checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    question: str
    plan: dict[str, Any]
    executions: list[QueryExecution]
    composed: ComposedAnswer
    report: str
    report_source: str
    fidelity: FidelityCheck
    status: str
    trace: Trace

    @property
    def model_calls(self) -> int:
        return sum(self.trace.model_calls.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "plan": self.plan,
            "executions": [{"sql": e.sql, "rows": rows_to_json(e.rows)} for e in self.executions],
            "composed": {"operation": self.composed.operation, "value": self.composed.value, "details": self.composed.details},
            "report_text": self.report,
            "report_source": self.report_source,
            "report_faithful": self.fidelity.faithful,
            "report_issues": self.fidelity.issues,
            "status": self.status,
            "model_calls": self.model_calls,
            "model_calls_by_agent": dict(self.trace.model_calls),
            "trace": self.trace.to_list(),
        }


class AgentFailure(RuntimeError):
    """No agent produced an executable result within the budgets."""

    def __init__(self, question: str, reason: str, trace: Trace):
        self.question = question
        self.reason = reason
        self.trace = trace
        super().__init__(reason)


# --------------------------------------------------------------------------- tools used by the Verificador


def result_shapes(composed: ComposedAnswer) -> set[str]:
    """Kinds of answer (see ASKS_FOR) that a composed result can satisfy."""

    fixed = {"growth_pct": "percent_change", "share_pct": "share", "compare_equal": "same_or_changed", "filter_then_rank": "name"}
    if composed.operation in fixed:
        return {fixed[composed.operation]}
    value = composed.value
    if isinstance(value, str):
        return {"name"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        shapes = {"number", "percent_change"}
        return shapes | ({"share"} if abs(value) <= 100 else set())
    rows = value if isinstance(value, list) else []
    if len(rows) == 1:
        row = rows[0]
        numbers = [v for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
        shapes = {"list"}
        if any(isinstance(v, str) for v in row):
            shapes.add("name")
        if numbers:
            shapes |= {"number", "percent_change"}
        if len(numbers) >= 2 or (len(numbers) == 1 and abs(numbers[0]) <= 100):
            shapes.add("share")
        return shapes
    if len(rows) == 2 and all(any(isinstance(v, str) for v in row) for row in rows):
        return {"list", "same_or_changed"}
    return {"list"} if rows else set()


def describe_result(composed: ComposedAnswer) -> str:
    value = composed.value
    if composed.operation != "direct":
        return f"the operation {composed.operation} with {_short(value, 160)}"
    if isinstance(value, list):
        return f"{len(value)} rows ({_short(value, 160)})"
    return f"the single value {_short(value, 80)}"


def uncovered_periods(
    periods: list[dict[str, Any]], sqls: list[str], data_range: tuple[str, str] | None
) -> list[dict[str, str]]:
    """Question periods whose first or last month no executed step filters."""

    months_in_sql = {match for sql in sqls for match in DATE_IN_SQL.findall(sql)}
    missing = []
    for period in periods:
        start, end = str(period.get("from", "")), str(period.get("to", ""))
        if not (ISO_DAY.match(start) and ISO_DAY.match(end)):
            continue
        try:
            date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            continue
        if start > end:
            start, end = end, start
        if data_range:
            start, end = max(start, data_range[0]), min(end, data_range[1])
            if start > end or (start <= data_range[0] and end >= data_range[1]):
                continue  # Outside the data, or the whole data range: nothing to filter.
        if not months_in_sql and (date.fromisoformat(end) - date.fromisoformat(start)).days >= 90:
            continue  # A long period and no date filter: read as "the whole period", not as a miss.
        if not {start[:7], end[:7]} <= months_in_sql:
            missing.append({"from": start, "to": end})
    return missing


def _same_sql(left: str, right: str) -> bool:
    return " ".join(strip_fences(left).rstrip(";").split()).lower() == " ".join(strip_fences(right).rstrip(";").split()).lower()


def _references(steps: list[dict[str, Any]]) -> bool:
    """Whether a later step reads an earlier one (FROM step_1)."""

    for index, step in enumerate(steps):
        tokens = {t.lower() for t in re.findall(r"[A-Za-z_]\w*", strip_literals(step["sql"]))}
        if any(earlier["id"].lower() in tokens or f"step_{n + 1}" in tokens for n, earlier in enumerate(steps[:index])):
            return True
    return False


def unused_steps(steps: list[dict[str, Any]], operation: str) -> list[str]:
    """Steps of a direct plan whose result no later step reads: the direct operation drops them."""

    if operation != "direct" or len(steps) < 2:
        return []
    unused = []
    for index, step in enumerate(steps[:-1]):
        names = {step["id"].lower(), f"step_{index + 1}"}
        later = " ".join(strip_literals(s["sql"]).lower() for s in steps[index + 1:])
        if not any(re.search(rf"\b{re.escape(name)}\b", later) for name in names):
            unused.append(step["id"])
    return unused


def first_dates(sqls: list[str]) -> list[str | None]:
    found = []
    for sql in sqls:
        dates = re.findall(r"'(\d{4}-\d{2}-\d{2})'", sql)
        found.append(min(dates) if dates else None)
    return found


def missing_labels(question: str, sqls: list[str], labels: Iterable[str]) -> list[str]:
    """Exact database labels named in the question that no executed step uses."""

    text = f" {normalize_text(question)} "
    used = f" {normalize_text(' '.join(sqls))} "
    missing = []
    for label in sorted(labels, key=len, reverse=True):
        key = f" {normalize_text(label)} "
        if key.strip() and key in text and key not in used and not any(key in f" {normalize_text(m)} " for m in missing):
            missing.append(label)
    return missing


# --------------------------------------------------------------------------- agents


class _Agent:
    key = ""
    name = ""

    def __init__(self, system: "AgentSystem"):
        self.system = system
        self.template = load_template(system.prompts_dir / PROMPT_FILES[self.key])

    def prompt(self, *flags: str, **values: Any) -> str:
        return render_template(self.template, self.system.enabled | set(flags), **values)

    def call_model(self, prompt: str, trace: Trace) -> str:
        trace.model_calls[self.name] += 1
        return self.system.generator.generate(prompt, MAX_NEW_TOKENS[self.key])


class PlannerAgent(_Agent):
    key, name = "planner", PLANNER

    def run(self, question: str, trace: Trace, feedback: str | None = None, previous_plan: Any = None) -> dict[str, Any]:
        reason, previous = feedback, previous_plan
        for _ in range(self.system.max_format_revisions + 1):
            flags = ("if_feedback",) if reason else ()
            prompt = self.prompt(
                *flags,
                schema=self.system.schema,
                question=question,
                feedback=reason or "",
                previous_plan=_short(previous, 1500) if previous is not None else "",
            )
            raw = self.call_model(prompt, trace)
            try:
                parsed = parse_json_object(raw)
                plan = validate_plan(parsed)
            except WORK_ERRORS as exc:
                trace.add(PLANNER, PLANNER, "autorrevisión (JSON inválido)", str(exc), raw)
                reason, previous = f"Your output was not a valid plan: {exc}", raw
                continue
            for extra in ("answer_needs", "periods"):
                if extra in parsed and "decomposition" in self.system.enabled:
                    plan[extra] = parsed[extra]
            trace.add(PLANNER, ANALYST, "plan", plan, raw)
            return plan
        raise AgentFailure(question, "The Planner could not produce a valid plan.", trace)


class AnalystAgent(_Agent):
    key, name = "analyst", ANALYST

    def run(self, question: str, plan: dict[str, Any], trace: Trace) -> AnalystOutcome:
        steps = [dict(step) for step in plan["steps"]]
        operation = plan["operation"]
        fixes = 0
        while True:
            failed, error, executions, composed = self._execute(steps, operation, trace)
            if error is None:
                trace.add(
                    ANALYST, REPORTER, "resultado verificado",
                    {"operation": operation, "value": composed.value,
                     "steps": [{"id": s["id"], "rows": rows_to_json(e.rows[:10])} for s, e in zip(steps, executions)]},
                )
                return AnalystOutcome("ok", steps, executions, composed)
            if "self_correction" not in self.system.enabled:
                trace.add(ANALYST, USER, "error sin corrección (técnica desactivada)", {"failed": failed, "error": error})
                return AnalystOutcome("failed", steps, reason=error)
            if fixes >= self.system.max_sql_fixes:
                reason = f"The SQL Analyst could not fix the plan after {fixes} corrections. Last error in {failed}: {error}"
                trace.add(ANALYST, PLANNER, "replanificar (presupuesto de correcciones agotado)", reason)
                return AnalystOutcome("replan", steps, reason=reason)
            listing = "\n".join(f"- {s['id']}: {s['purpose']}\n  SQL: {s['sql']}" for s in steps)
            raw = self.call_model(
                self.prompt(schema=self.system.schema, question=question, operation=operation,
                            steps=listing, failed=failed, error=error),
                trace,
            )
            try:
                decision = parse_json_object(raw)
            except ValueError:
                decision = {"action": "replan", "reason": "The analyst decision was not valid JSON."}
            action = decision.get("action")
            target = str(decision.get("step") or (failed if failed != "composition" else steps[-1]["id"]))
            index = next((i for i, step in enumerate(steps) if step["id"] == target), None)
            sql = decision.get("sql")
            if action == "swap" and len(steps) == 2 and not _references(steps):
                fixes += 1
                trace.add(ANALYST, ANALYST, "invierte el orden de los pasos", {"error": error}, raw)
                steps = [{**steps[1], "id": steps[0]["id"]}, {**steps[0], "id": steps[1]["id"]}]
                continue
            if action == "fix" and index is not None and isinstance(sql, str) and sql.strip():
                if _same_sql(sql, steps[index]["sql"]):
                    reason = f"The SQL Analyst found no fix for {target} (error in {failed}: {error})"
                    trace.add(ANALYST, PLANNER, "replanificar (corrección sin cambios)", reason, raw)
                    return AnalystOutcome("replan", steps, reason=reason)
                fixes += 1
                trace.add(ANALYST, ANALYST, "corrige el paso", {"step": target, "sql": sql, "error": error}, raw)
                steps[index] = {**steps[index], "sql": sql}
                continue
            reason = str(decision.get("reason") or "The plan cannot answer the question.")
            reason = f"{reason} (error in {failed}: {error})"
            trace.add(ANALYST, PLANNER, "replanificar", reason, raw)
            return AnalystOutcome("replan", steps, reason=reason)

    def _execute(
        self, steps: list[dict[str, Any]], operation: str, trace: Trace
    ) -> tuple[str, str | None, list[QueryExecution], ComposedAnswer | None]:
        conn = connect_read_only(self.system.db_path)
        executions: list[QueryExecution] = []
        try:
            try:
                expanded = expand_step_references(steps)
            except WORK_ERRORS as exc:
                return steps[0]["id"], str(exc), executions, None
            for step, sql in zip(steps, expanded):
                problems = lint_sql(step["sql"])
                trace.add(ANALYST, "herramienta:revisar_sql", "llamada", {"step": step["id"], "sql": step["sql"]})
                trace.add("herramienta:revisar_sql", ANALYST, "resultado", problems or "sin problemas")
                if problems:
                    return step["id"], " ".join(problems), executions, None
                trace.add(ANALYST, "herramienta:ejecutar_sql", "llamada", {"step": step["id"], "sql": sql})
                try:
                    execution = execute_read_only(conn, sql)
                except WORK_ERRORS as exc:
                    trace.add("herramienta:ejecutar_sql", ANALYST, "error", str(exc))
                    return step["id"], str(exc), executions, None
                trace.add("herramienta:ejecutar_sql", ANALYST, "resultado",
                          {"rows": rows_to_json(execution.rows[:10]), "row_count": len(execution.rows)})
                if not execution.rows:
                    return step["id"], f"{step['id']} returned no rows; check labels, joins and dates.", executions, None
                if any(value is None for row in execution.rows for value in row):
                    return step["id"], f"{step['id']} returned NULL; check labels, joins and dates.", executions, None
                executions.append(execution)
            trace.add(ANALYST, "herramienta:componer", "llamada", {"operation": operation})
            try:
                composed = compose_results(operation, [e.rows for e in executions], conn=conn)
            except WORK_ERRORS as exc:
                trace.add("herramienta:componer", ANALYST, "error", str(exc))
                return "composition", f"Composition {operation} failed: {exc}", executions, None
            trace.add("herramienta:componer", ANALYST, "resultado", composed.value)
            return "", None, executions, composed
        finally:
            conn.close()


class ReporterAgent(_Agent):
    key, name = "reporter", REPORTER

    def run(
        self, question: str, steps: list[dict[str, Any]], outcome: AnalystOutcome, trace: Trace,
        feedback: str | None = None, previous: str | None = None,
    ) -> tuple[str, str, str]:
        evidence = "\n".join(
            f"- {step['id']}: {step['purpose']} -> {json.dumps(rows_to_json(e.rows[:5]), ensure_ascii=False)}"
            for step, e in zip(steps, outcome.executions)
        )
        flags = (("if_feedback",) if feedback else ()) + (("if_growth",) if outcome.composed.operation == "growth_pct" else ())
        raw = self.call_model(
            self.prompt(*flags, question=question, answer=json.dumps(outcome.composed.value, ensure_ascii=False),
                        evidence=evidence, feedback=feedback or "", previous_report=previous or ""),
            trace,
        )
        text = strip_fences(raw).strip().strip('"').strip()
        if text.startswith("{"):
            try:
                text = str(parse_json_object(text).get("answer", "")).strip()
            except ValueError:
                pass
        source = "model" if text else "fallback"
        if not text:
            text = "Verified result: " + json.dumps(outcome.composed.value, ensure_ascii=False, sort_keys=True)
        trace.add(REPORTER, VERIFIER, "reporte" if not feedback else "reporte corregido", text, raw)
        return text, source, raw


class VerifierAgent(_Agent):
    key, name = "verifier", VERIFIER

    def __init__(self, system: "AgentSystem"):
        super().__init__(system)
        self._analysis: dict[str, dict[str, Any]] = {}

    def analyse_question(self, question: str, trace: Trace) -> dict[str, Any]:
        """Checklist filled by the model from the question alone (one call per question)."""

        if question in self._analysis:
            return self._analysis[question]
        low, high = self.system.date_range or ("", "")
        raw = self.call_model(self.prompt(question=question, date_from=low, date_to=high), trace)
        try:
            parsed = parse_json_object(raw)
        except ValueError:
            parsed = {}
        asks_for = parsed.get("asks_for") if parsed.get("asks_for") in ASKS_FOR else None
        periods = parsed.get("periods") if isinstance(parsed.get("periods"), list) else []
        analysis = {"asks_for": asks_for, "periods": [p for p in periods if isinstance(p, dict)]}
        trace.add(VERIFIER, VERIFIER, "lista de chequeo de la pregunta", analysis, raw)
        self._analysis[question] = analysis
        return analysis

    def run(
        self, question: str, outcome: AnalystOutcome, report: str, source: str, trace: Trace,
        can_replan: bool, can_revise: bool,
    ) -> Verdict:
        checks: dict[str, Any] = {}
        if "critic" in self.system.enabled and can_replan:
            analysis = self.analyse_question(question, trace)
            shapes = result_shapes(outcome.composed)
            # Every check the Verifier relies on is logged as the output of its tool, so the trace shows
            # where each problem comes from (forma_resultado: shapes and dropped steps; revisar_periodos:
            # uncovered months and the order of the two periods of a percentage change).
            dropped = unused_steps(outcome.steps, outcome.composed.operation)
            trace.add(VERIFIER, "herramienta:forma_resultado", "llamada", {"operation": outcome.composed.operation})
            trace.add("herramienta:forma_resultado", VERIFIER, "resultado",
                      {"formas": sorted(shapes), "pasos_descartados": dropped})
            sqls = [execution.sql for execution in outcome.executions]
            missing = uncovered_periods(analysis["periods"], sqls, self.system.date_range)
            reversed_order = False
            if outcome.composed.operation == "growth_pct":
                starts = first_dates(sqls)
                reversed_order = None not in starts and starts[0] > starts[1]
            trace.add(VERIFIER, "herramienta:revisar_periodos", "llamada", {"periods": analysis["periods"]})
            trace.add("herramienta:revisar_periodos", VERIFIER, "resultado",
                      {"sin_cubrir": missing, "orden_invertido": reversed_order}
                      if missing or reversed_order else "todos cubiertos, en orden")
            problems = []
            if analysis["asks_for"] and analysis["asks_for"] not in shapes:
                problems.append(
                    f"The question asks for {DESCRIPTIONS[analysis['asks_for']]}, but the plan returns "
                    f"{describe_result(outcome.composed)}."
                )
            for period in missing:
                problems.append(f"The question names the period {period['from']} to {period['to']}, but no step filters those dates.")
            if dropped:
                problems.append(
                    f"The direct operation only returns the last step, so the result of {', '.join(dropped)} is lost; "
                    "if the answer needs those values, use a two-step operation."
                )
            if reversed_order:
                problems.append("growth_pct needs the earlier period in the first step and the later one in the second.")
            labels = missing_labels(question, sqls, self.system.labels)
            trace.add(VERIFIER, "herramienta:revisar_filtros", "llamada", {"question": question})
            trace.add("herramienta:revisar_filtros", VERIFIER, "resultado", labels or "sin filtros faltantes")
            for label in labels:
                problems.append(f"The question names '{label}', but no step filters by it.")
            checks = {"asks_for": analysis["asks_for"], "result_shapes": sorted(shapes), "uncovered_periods": missing,
                      "unused_steps": dropped, "missing_labels": labels}
            if problems:
                feedback = " ".join(problems)
                fidelity = FidelityCheck(False, ["Not checked: the plan was sent back to the Planner."])
                trace.add(VERIFIER, PLANNER, "replanificar", feedback)
                return Verdict("replan", feedback, fidelity, checks)

        if source == "model":
            evidence = question + "\n" + json.dumps(
                [{"sql": e.sql, "rows": rows_to_json(e.rows)} for e in outcome.executions], ensure_ascii=False
            ) + "\n" + json.dumps([{"purpose": s.get("purpose", "")} for s in outcome.steps], ensure_ascii=False)
            fidelity = check_report(report, outcome.composed.value, evidence)
        else:
            fidelity = FidelityCheck(False, ["The Reporter returned no text; the deterministic summary is shown."])
        trace.add(VERIFIER, "herramienta:verificar_fidelidad", "llamada", {"report": report})
        trace.add("herramienta:verificar_fidelidad", VERIFIER, "resultado", fidelity.issues or "fiel")
        checks["report_issues"] = fidelity.issues
        if not fidelity.faithful and can_revise:
            feedback = " ".join(fidelity.issues)
            trace.add(VERIFIER, REPORTER, "revisar reporte", feedback)
            return Verdict("revise_report", feedback, fidelity, checks)
        trace.add(VERIFIER, USER, "respuesta final", {"report": report, "faithful": fidelity.faithful})
        return Verdict("accept", "", fidelity, checks)


def text_labels(conn) -> set[str]:
    """Exact text values stored in the database (product names, categories), dates excluded."""

    labels: set[str] = set()
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        for column in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
            if (column[2] or "").upper() != "TEXT":
                continue
            for (value,) in conn.execute(f'SELECT DISTINCT "{column[1]}" FROM "{table}" WHERE "{column[1]}" IS NOT NULL LIMIT 200'):
                if isinstance(value, str) and not ISO_DAY.match(value):
                    labels.add(value)
    return labels


# --------------------------------------------------------------------------- system


class AgentSystem:
    """Four agents, one model, bounded loops. ``disabled`` removes techniques for the ablation."""

    def __init__(
        self,
        db_path: str | Path,
        generator: TextGenerator,
        disabled: Iterable[str] = (),
        prompts_dir: str | Path = PROMPTS_DIR,
        max_replans: int = 2,
        max_critic_replans: int = 1,
        max_sql_fixes: int = 2,
        max_report_revisions: int = 2,
        max_format_revisions: int = 2,
    ):
        disabled = set(disabled)
        unknown = disabled - set(TECHNIQUES)
        if unknown:
            raise ValueError(f"Unknown techniques: {sorted(unknown)}")
        self.db_path = Path(db_path)
        self.generator = generator
        self.enabled = set(TECHNIQUES) - disabled
        self.prompts_dir = Path(prompts_dir)
        self.max_replans = max_replans
        self.max_critic_replans = max_critic_replans
        self.max_sql_fixes = max_sql_fixes
        self.max_report_revisions = max_report_revisions if "report_refine" in self.enabled else 0
        self.max_format_revisions = max_format_revisions
        self.schema = grounded_schema(self.db_path)
        conn = connect_read_only(self.db_path)
        try:
            self.date_range = sales_date_range(conn)
            self.labels = text_labels(conn)
        finally:
            conn.close()
        self.planner = PlannerAgent(self)
        self.analyst = AnalystAgent(self)
        self.reporter = ReporterAgent(self)
        self.verifier = VerifierAgent(self)

    def answer(self, question: str, listener: Callable[[Message], None] | None = None) -> AgentResult:
        trace = Trace(listener)
        trace.add(USER, PLANNER, "pregunta", question)
        trace.add(PLANNER, "herramienta:leer_esquema", "llamada", {})
        trace.add("herramienta:leer_esquema", PLANNER, "resultado", f"{len(self.schema.splitlines())} líneas de esquema enriquecido")
        feedback: str | None = None
        previous_plan: Any = None
        replans = critic_replans = 0
        best: AgentResult | None = None

        while True:
            try:
                plan = self.planner.run(question, trace, feedback, previous_plan)
            except AgentFailure:
                if best:
                    return self._fallback(best, trace, "the new plan was not valid")
                raise
            outcome = self.analyst.run(question, plan, trace)
            if outcome.status != "ok":
                if outcome.status == "replan" and replans < self.max_replans:
                    replans += 1
                    feedback, previous_plan = outcome.reason, {"steps": outcome.steps, "operation": plan["operation"]}
                    continue
                if best:
                    return self._fallback(best, trace, outcome.reason)
                trace.add(ANALYST, USER, "sin respuesta", outcome.reason)
                raise AgentFailure(question, outcome.reason, trace)

            report, source, _ = self.reporter.run(question, outcome.steps, outcome, trace)
            revisions = 0
            can_replan = critic_replans < self.max_critic_replans and replans < self.max_replans
            while True:
                verdict = self.verifier.run(
                    question, outcome, report, source, trace,
                    can_replan=can_replan, can_revise=revisions < self.max_report_revisions,
                )
                if verdict.action != "revise_report":
                    break
                revisions += 1
                report, source, _ = self.reporter.run(
                    question, outcome.steps, outcome, trace, feedback=verdict.feedback, previous=report
                )
                can_replan = False  # The plan was already accepted; only the report is being revised.

            plan = {**plan, "steps": outcome.steps}
            candidate = AgentResult(question, plan, outcome.executions, outcome.composed, report, source,
                                    verdict.fidelity, "aceptado", trace)
            if verdict.action == "replan":
                if best is not None and best.composed.value == candidate.composed.value:
                    return candidate  # The new plan reached the same result: nothing left to fix.
                best = candidate
                critic_replans += 1
                replans += 1
                feedback, previous_plan = verdict.feedback, {"steps": outcome.steps, "operation": plan["operation"]}
                continue
            return candidate

    @staticmethod
    def _fallback(best: AgentResult, trace: Trace, reason: str) -> AgentResult:
        trace.add(VERIFIER, USER, "respuesta de respaldo", {"reason": reason, "report": best.report})
        best.status = "respaldo"
        return best


def save_agent_trace(result: AgentResult | AgentFailure, path: str | Path) -> None:
    if isinstance(result, AgentFailure):
        data = {"question": result.question, "error": result.reason, "trace": result.trace.to_list(),
                "model_calls_by_agent": dict(result.trace.model_calls)}
    else:
        data = result.to_dict()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
