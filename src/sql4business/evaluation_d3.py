"""Deliverable 3 evaluation: baseline, Deliverable 2 solution, agent system and ablations.

Every configuration answers the same 27 questions (15 official, 12 held-out paraphrases) and is
scored with the same criteria as Deliverable 2 (see evaluation.py):

  sql     Deliverable 1 criterion: each gold result set is produced exactly by some step.
  answer  the final answer is correct, whatever SQL produced it.
  report  the model-written report passes the fidelity check (D2 solution and agents).
  e2e     answer and report correct at the same time.

Configurations
  baseline             Deliverable 1 direct prompt (unchanged).
  d2                   Deliverable 2 structured solution (unchanged code).
  agentes              Deliverable 3 agent system with every technique.
  sin_<technique>      agent system with that technique removed (one at a time).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .agents import TECHNIQUES, AgentFailure, AgentSystem, save_agent_trace
from .evaluation import _direct_system, _solution_system, answer_correct, candidate_sets, expected_answer, sql_correct
from .model import TextGenerator
from .pipeline import BusinessAssistant


ABLATIONS = tuple(f"sin_{name}" for name in TECHNIQUES)
CONFIGS = ("baseline", "d2", "agentes") + ABLATIONS
METRICS = {"baseline": ("sql", "answer")}
DEFAULT_METRICS = ("sql", "answer", "report", "e2e")
LABELS = {
    "baseline": "Prompt directo (baseline D1)",
    "d2": "Solución D2 (pipeline)",
    "agentes_v1": "Agentes v1 (primera corrida)",
    "agentes": "Sistema de agentes v2 (D3)",
    "sin_few_shot": "\\quad sin ejemplos (few-shot)",
    "sin_decomposition": "\\quad sin descomposición explícita",
    "sin_self_correction": "\\quad sin autocorrección del Analista",
    "sin_critic": "\\quad sin verificación crítica",
    "sin_report_refine": "\\quad sin refinamiento del reporte",
}


def metrics_for(config: str) -> tuple[str, ...]:
    return METRICS.get(config, DEFAULT_METRICS)


class CountingGenerator:
    """Wraps the model to count calls per question (cost of each configuration)."""

    def __init__(self, generator: TextGenerator):
        self.generator = generator
        self.calls = 0

    def generate(self, prompt: str, max_new_tokens: int | None = None) -> str:
        self.calls += 1
        return self.generator.generate(prompt, max_new_tokens)


def _agent_system(system: AgentSystem, question: dict[str, Any], expected) -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    try:
        result = system.answer(question["question"])
    except AgentFailure as failure:
        record = {
            "error": failure.reason,
            "trace": failure.trace.to_list(),
            "model_calls_by_agent": dict(failure.trace.model_calls),
            "sql": False, "answer": False, "report": False, "e2e": False,
            "seconds": round(time.perf_counter() - started, 2),
        }
        return record, failure
    answer = answer_correct(candidate_sets(result.composed.value), question, expected)
    record = {
        **result.to_dict(),
        "sql": sql_correct([e.rows for e in result.executions], question["gold_result"]),
        "answer": answer,
        "report": result.fidelity.faithful,
        "e2e": answer and result.fidelity.faithful,
        "seconds": round(time.perf_counter() - started, 2),
    }
    return record, result


def run_config(
    config: str,
    generator: TextGenerator,
    db_path: str | Path,
    question_sets: dict[str, list[dict[str, Any]]],
    baseline_schema: str,
    trace_dir: str | Path | None = None,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run one configuration on every question; optionally save one trace file per question."""

    if config not in CONFIGS:
        raise ValueError(f"Unknown configuration {config!r}; choose from {', '.join(CONFIGS)}")
    db_path = Path(db_path)
    counter = CountingGenerator(generator)
    assistant = BusinessAssistant(db_path, counter) if config == "d2" else None
    disabled = [config[4:]] if config.startswith("sin_") else []
    system = AgentSystem(db_path, counter, disabled=disabled) if config == "agentes" or disabled else None
    records = []
    for set_name, questions in question_sets.items():
        for question in questions:
            expected = expected_answer(question, db_path)
            counter.calls = 0
            if config == "baseline":
                result = _direct_system(counter, baseline_schema, db_path, question, expected)
            elif config == "d2":
                result = _solution_system(assistant, question, expected)
            else:
                result, outcome = _agent_system(system, question, expected)
                if trace_dir is not None:
                    save_agent_trace(outcome, Path(trace_dir) / f"{set_name}_{question['id']}.json")
            result["model_calls"] = counter.calls
            records.append({
                "set": set_name, "id": question["id"], "type": question["type"],
                "question": question["question"], "expected": expected.value, "result": result,
            })
            flags = ",".join(f"{metric}={int(bool(result[metric]))}" for metric in metrics_for(config))
            progress(f"{config:20} {set_name}/{question['id']}: {flags} calls={counter.calls}")
    return {"config": config, "summary": summarize(records, config), "records": records}


def summarize(records: list[dict[str, Any]], config: str) -> dict[str, Any]:
    """[correct, total] per set, metric and question type, plus mean model calls and seconds."""

    summary: dict[str, Any] = {}
    for set_name in dict.fromkeys(record["set"] for record in records):
        subset = [record for record in records if record["set"] == set_name]
        summary[set_name] = {}
        for metric in metrics_for(config):
            summary[set_name][metric] = {
                group: [
                    sum(bool(r["result"][metric]) for r in subset if group == "global" or r["type"] == group),
                    sum(1 for r in subset if group == "global" or r["type"] == group),
                ]
                for group in ("puntual", "combinada", "global")
            }
        summary[set_name]["model_calls"] = round(sum(r["result"]["model_calls"] for r in subset) / len(subset), 2)
        summary[set_name]["seconds"] = round(sum(r["result"]["seconds"] for r in subset) / len(subset), 2)
    return summary


def latex_table(summaries: dict[str, dict[str, Any]]) -> str:
    """Results table for docs/deliverable3.tex, generated from the results files."""

    def cell(config: str, set_name: str, metric: str) -> str:
        data = summaries[config].get(set_name, {})
        if metric not in data:
            return "--"
        correct, total = data[metric]["global"]
        return f"{correct}/{total}"

    def calls(config: str) -> str:
        values = [summaries[config][s]["model_calls"] for s in ("official", "heldout") if s in summaries[config]]
        return f"{sum(values) / len(values):.1f}" if values else "--"

    lines = [
        r"\begin{tabular}{@{}lccccccc@{}}",
        r"\toprule",
        r"& \multicolumn{3}{c}{\textbf{Set oficial (15)}} & \multicolumn{3}{c}{\textbf{Paráfrasis (12)}} & \\",
        r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}",
        r"\textbf{Configuración} & sql & answer & e2e & sql & answer & e2e & \textbf{Llamadas} \\",
        r"\midrule",
    ]
    for config in ("baseline", "d2", "agentes_v1", "agentes") + ABLATIONS:
        if config not in summaries:
            continue
        row = [LABELS[config]]
        row += [cell(config, s, m) for s in ("official", "heldout") for m in ("sql", "answer", "e2e")]
        row.append(calls(config))
        lines.append(" & ".join(row) + r" \\")
        if config in ("d2", "agentes") or (config == "agentes_v1" and "agentes" not in summaries):
            lines.append(r"\addlinespace")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines) + "\n"


def load_results(
    directory: str | Path, configs: Iterable[str] = CONFIGS, first_run: str | Path | None = None
) -> dict[str, dict[str, Any]]:
    """Summaries of the configurations already evaluated in ``directory``.

    ``first_run`` is the folder of the first agent run (results/deliverable3_v1); its full agent
    system is added as the row ``agentes_v1`` so the table shows both versions.
    """

    summaries = {}
    for config in configs:
        path = Path(directory) / f"{config}.json"
        if path.exists():
            summaries[config] = json.loads(path.read_text(encoding="utf-8"))["summary"]
    if first_run is not None and (Path(first_run) / "agentes.json").exists():
        summaries["agentes_v1"] = json.loads((Path(first_run) / "agentes.json").read_text(encoding="utf-8"))["summary"]
    return summaries


__all__ = ["CONFIGS", "ABLATIONS", "run_config", "summarize", "latex_table", "load_results"]
