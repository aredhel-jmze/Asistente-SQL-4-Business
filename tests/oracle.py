"""A rule-based stand-in for the model, used to test the evaluation plumbing without a GPU.

It answers every prompt type of the repository (D1 baseline, D2 planner/reporter, D3 agents) with
the reference SQL of the question found in the prompt. It says nothing about how well Qwen does;
it only proves that every configuration runs end to end and is scored and saved correctly.
"""

import json
import re

from helpers import QUESTIONS

ASKS_FOR = {"growth_pct": "percent_change", "share_pct": "share", "compare_equal": "same_or_changed", "filter_then_rank": "name"}


def _question_in(prompt):
    found = [q for q in QUESTIONS.values() if q["question"] in prompt]
    if not found:
        raise AssertionError("The oracle found no evaluation question in the prompt.")
    return max(found, key=lambda q: len(q["question"]))


def _plan(question):
    steps = [{"id": f"step_{i}", "purpose": f"reference step {i}", "sql": sql} for i, sql in enumerate(question["gold_sql"], 1)]
    return json.dumps({"answer_needs": "reference", "periods": [], "steps": steps,
                       "composition": {"operation": question.get("combine", "direct")}}, ensure_ascii=False)


def _report(prompt):
    answer = json.loads(re.search(r"Verified answer computed by code:\n(.*)\n", prompt).group(1))
    if isinstance(answer, dict) and "growth_pct" in answer:
        word = "aumentó" if answer["growth_pct"] >= 0 else "disminuyó"
        return f"El valor {word} un {abs(answer['growth_pct']):.2f}%."
    if isinstance(answer, dict) and "share_pct" in answer:
        return f"Se vendieron {answer['total']:.0f} unidades y {answer['subset']:.0f} corresponden al grupo ({answer['share_pct']:.2f}%)."
    if isinstance(answer, dict) and "winner" in answer:
        return f"El producto es {answer['winner']}."
    if isinstance(answer, dict) and "first" in answer:
        same = "no cambió" if not answer["changed"] else "cambió"
        return f"El líder fue {answer['first']} y luego {answer['second']}; el líder {same}."
    if isinstance(answer, list):
        return "Resultado: " + ", ".join(str(row[0]) for row in answer) + "."
    return f"El resultado es {answer}."


class OracleGenerator:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, max_new_tokens=None):
        self.prompts.append(prompt)
        if prompt.startswith("Eres un asistente que traduce"):
            return ";\n".join(_question_in(prompt)["gold_sql"])
        if prompt.startswith(("You are the planning component", "You are the Planner agent")):
            return _plan(_question_in(prompt))
        if prompt.startswith(("You are the reporting component", "You are the Reporter agent")):
            return _report(prompt)
        if prompt.startswith("You are the Verifier agent"):
            question = _question_in(prompt)
            asks = ASKS_FOR.get(question.get("combine"))
            if asks is None:
                gold = question["gold_result"][0]
                asks = "list" if len(gold) > 1 else ("name" if isinstance(gold[0][0], str) else "number")
            return json.dumps({"asks_for": asks, "periods": []})
        if prompt.startswith("You are the SQL Analyst agent"):
            return json.dumps({"action": "replan", "reason": "the oracle never fixes SQL"})
        raise AssertionError("Unknown prompt type: " + prompt[:80])
