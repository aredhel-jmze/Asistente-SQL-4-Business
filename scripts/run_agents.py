#!/usr/bin/env python3
"""Answer one question with the agent system, printing every message between agents live.

This is what the Deliverable 3 video shows. The baseline answer on the same input is printed
first, and the full trace is saved as JSON. Needs a GPU for the model.

    python scripts/run_agents.py --id q10-p
    python scripts/run_agents.py "¿Cuál fue el ingreso total de julio de 2026?"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.agents import TECHNIQUES, AgentFailure, AgentSystem, format_message, save_agent_trace  # noqa: E402
from sql4business.model import BASELINE_MAX_NEW_TOKENS, MODEL_ID, HuggingFaceGenerator, baseline_prompt  # noqa: E402
from sql4business.sql_tools import connect_read_only, execute_baseline  # noqa: E402


def load(name: str) -> dict:
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", help="question text")
    parser.add_argument("--id", help="id of a question in data/questions.json or data/paraphrases.json")
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--db", default=ROOT / "data" / "business.db", type=Path)
    parser.add_argument("--without", default="", help="comma-separated techniques to remove: " + ",".join(TECHNIQUES))
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--trace", type=Path, help="trace file (default results/deliverable3/demo/<id>.json)")
    args = parser.parse_args()

    official = load("questions.json")
    if args.id:
        dataset = official["questions"] + load("paraphrases.json")["questions"]
        question = next(q["question"] for q in dataset if q["id"] == args.id)
    elif args.question:
        question = args.question
    else:
        parser.error("give a question or --id")
    trace_path = args.trace or ROOT / "results" / "deliverable3" / "demo" / f"{args.id or 'pregunta'}.json"
    print("PREGUNTA:", question)

    generator = HuggingFaceGenerator(args.model)
    if not args.no_baseline:
        print("\n=== Baseline (prompt directo del D1) ===")
        raw = generator.generate(baseline_prompt(official["schema"], question), BASELINE_MAX_NEW_TOKENS)
        conn = connect_read_only(args.db)
        try:
            executions = execute_baseline(conn, raw)
        finally:
            conn.close()
        for execution in executions or [None]:
            if execution is None:
                print("sin SELECT en la salida:", raw)
                continue
            print("SQL:  ", execution.sql)
            print("filas:", execution.error or execution.rows[:10])

    disabled = [name.strip() for name in args.without.split(",") if name.strip()]
    print("\n=== Sistema de agentes ===" + (f" (sin {', '.join(disabled)})" if disabled else ""))
    system = AgentSystem(args.db, generator, disabled=disabled)
    try:
        result = system.answer(question, listener=lambda message: print(format_message(message, 400), flush=True))
    except AgentFailure as failure:
        print("\nSIN RESPUESTA:", failure.reason)
        save_agent_trace(failure, trace_path)
        print("traza:", trace_path)
        return
    print("\nRESPUESTA FINAL:", result.report)
    print("resultado verificado:", result.composed.operation, "->", result.composed.value)
    print("reporte fiel:", result.fidelity.faithful, result.fidelity.issues or "")
    print("llamadas al modelo:", result.trace.model_calls)
    save_agent_trace(result, trace_path)
    print("traza:", trace_path)


if __name__ == "__main__":
    main()
