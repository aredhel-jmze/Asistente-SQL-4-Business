import json
import tempfile
import unittest
from pathlib import Path

from helpers import DB, HELDOUT, OFFICIAL, ROOT, ScriptedGenerator, plan
from oracle import OracleGenerator

from sql4business.agents import (
    first_dates,
    missing_labels,
    unused_steps,
    PROMPT_FILES,
    PROMPTS_DIR,
    TECHNIQUES,
    AgentFailure,
    AgentSystem,
    load_template,
    render_template,
    result_shapes,
    save_agent_trace,
    uncovered_periods,
)
from sql4business.composition import ComposedAnswer
from sql4business.evaluation_d3 import CONFIGS, latex_table, run_config
from sql4business.sql_tools import lint_sql

Q1 = "SELECT SUM(amount) FROM sales WHERE sale_date BETWEEN '2026-03-01' AND '2026-05-31'"
Q2 = "SELECT SUM(amount) FROM sales WHERE sale_date BETWEEN '2026-06-01' AND '2026-08-31'"
Q1_TRUNCATED = "SELECT SUM(amount) FROM sales WHERE strftime('%Y-%m', sale_date) BETWEEN '2026-03-01' AND '2026-05-31'"
GROWTH_QUESTION = "¿Cuánto crecieron las ventas de junio a agosto respecto de marzo a mayo?"
TOP_QUESTION = "¿Qué producto generó el mayor ingreso en todo el periodo?"
ALL_PRODUCTS = ("SELECT p.name, SUM(s.amount) FROM sales s JOIN products p ON s.product_id = p.product_id "
                "GROUP BY p.product_id")
TOP_PRODUCT = ALL_PRODUCTS + " ORDER BY SUM(s.amount) DESC LIMIT 1"


def checklist(asks_for, *periods):
    return json.dumps({"asks_for": asks_for, "periods": [{"from": a, "to": b} for a, b in periods]})


def fix(step, sql):
    return json.dumps({"action": "fix", "step": step, "sql": sql})


GROWTH_PERIODS = (("2026-03-01", "2026-05-31"), ("2026-06-01", "2026-08-31"))


class PromptFileTests(unittest.TestCase):
    def test_blocks_comments_and_placeholders(self):
        template = "%% comment\nA [[x]]X{{v}}[[/x]][[not_x]]notX[[/not_x]] [[y]]Y [[x]]inner[[/x]][[/y]]\n"
        cleaned = load_template_from_text(template)
        self.assertNotIn("comment", cleaned)
        self.assertEqual(render_template(cleaned, {"x", "y"}, v=1).strip(), "A X1 Y inner")
        self.assertEqual(render_template(cleaned, set(), v=1).strip(), "A notX")
        with self.assertRaises(KeyError):
            render_template(cleaned, {"x"})

    def test_every_prompt_renders_under_every_ablation(self):
        values = dict(schema="S", question="Q", feedback="F", previous_plan="P", operation="direct", steps="-",
                      failed="step_1", error="E", answer="1", evidence="-", previous_report="R",
                      date_from="2026-03-01", date_to="2026-08-31")
        for name, filename in PROMPT_FILES.items():
            template = load_template(PROMPTS_DIR / filename)
            self.assertNotIn("%%", template)
            for removed in [None, *TECHNIQUES]:
                enabled = set(TECHNIQUES) - {removed}
                for flags in (set(), {"if_feedback"}, {"if_growth"}, {"if_feedback", "if_growth"}):
                    text = render_template(template, enabled | flags, **values)
                    self.assertNotIn("[[", text, (name, removed))
                    self.assertNotIn("{{", text, (name, removed))

    def test_ablations_remove_their_technique_from_the_planner(self):
        template = load_template(PROMPTS_DIR / PROMPT_FILES["planner"])
        values = dict(schema="S", question="Q", feedback="", previous_plan="")
        full = render_template(template, set(TECHNIQUES), **values)
        no_examples = render_template(template, set(TECHNIQUES) - {"few_shot"}, **values)
        no_decomposition = render_template(template, set(TECHNIQUES) - {"decomposition"}, **values)
        self.assertIn("¿Qué categoría vendió más unidades en abril de 2026?", full)
        self.assertNotIn("¿Qué categoría vendió más unidades", no_examples)
        self.assertIn("answer_needs", full)
        self.assertNotIn("answer_needs", no_decomposition)
        self.assertIn("¿Qué categoría vendió más unidades", no_decomposition)

    def test_prompt_examples_are_not_evaluation_questions(self):
        texts = " ".join((PROMPTS_DIR / f).read_text(encoding="utf-8") for f in PROMPT_FILES.values())
        for question in OFFICIAL["questions"] + HELDOUT["questions"]:
            self.assertNotIn(question["question"], texts, question["id"])


def load_template_from_text(text):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
    return load_template(handle.name)


class ToolTests(unittest.TestCase):
    def test_lint_finds_the_deliverable_2_failure_case(self):
        self.assertTrue(lint_sql(Q1_TRUNCATED))
        self.assertFalse(lint_sql(Q1))
        self.assertFalse(lint_sql("SELECT SUM(amount) FROM sales WHERE strftime('%Y-%m', sale_date) = '2026-06'"))

    def test_result_shapes(self):
        self.assertEqual(result_shapes(ComposedAnswer("growth_pct", {})), {"percent_change"})
        self.assertIn("number", result_shapes(ComposedAnswer("direct", 488)))
        self.assertNotIn("share", result_shapes(ComposedAnswer("direct", 488)))
        self.assertIn("share", result_shapes(ComposedAnswer("direct", 29.49)))
        self.assertEqual(result_shapes(ComposedAnswer("direct", [["a", 1], ["b", 2], ["c", 3]])), {"list"})
        self.assertIn("name", result_shapes(ComposedAnswer("direct", [["Notebook 14 pulgadas", 91298340]])))
        self.assertIn("share", result_shapes(ComposedAnswer("direct", [[1655, 488]])))

    def test_uncovered_periods(self):
        data = ("2026-03-01", "2026-08-31")
        self.assertEqual(uncovered_periods([{"from": "2026-06-01", "to": "2026-06-30"}], [Q1], data),
                         [{"from": "2026-06-01", "to": "2026-06-30"}])
        self.assertEqual(uncovered_periods([{"from": "2026-03-01", "to": "2026-05-31"}], [Q1], data), [])
        self.assertEqual(uncovered_periods([{"from": "2026-01-01", "to": "2026-12-31"}], ["SELECT 1"], data), [])
        self.assertEqual(uncovered_periods([{"from": "2026-01-01", "to": "2026-06-30"}], ["SELECT 1"], data), [])
        self.assertEqual(uncovered_periods([{"from": "junio", "to": "?"}], [Q1], data), [])


class AgentSystemTests(unittest.TestCase):
    def test_analyst_fixes_the_step_flagged_by_the_linter(self):
        generator = ScriptedGenerator(
            plan(Q1_TRUNCATED, Q2, operation="growth_pct"),
            fix("step_1", Q1),
            "Las ventas aumentaron un 60.13%.",
            checklist("percent_change", *GROWTH_PERIODS),
        )
        result = AgentSystem(DB, generator).answer(GROWTH_QUESTION)
        self.assertAlmostEqual(result.composed.value["growth_pct"], 60.1257, places=3)
        self.assertTrue(result.fidelity.faithful, result.fidelity.issues)
        self.assertEqual(result.trace.model_calls, {"Planificador": 1, "Analista SQL": 1, "Reportero": 1, "Verificador": 1})
        kinds = [m.kind for m in result.trace.messages]
        self.assertIn("corrige el paso", kinds)
        self.assertEqual(kinds[-1], "respuesta final")
        self.assertIn("truncated date", generator.prompts[1])

    def test_without_self_correction_the_first_error_ends_the_attempt(self):
        generator = ScriptedGenerator(plan(Q1_TRUNCATED, Q2, operation="growth_pct"))
        with self.assertRaises(AgentFailure) as context:
            AgentSystem(DB, generator, disabled=["self_correction"]).answer(GROWTH_QUESTION)
        self.assertEqual(len(generator.prompts), 1)
        self.assertIn("truncated date", context.exception.reason)

    def test_analyst_can_send_the_plan_back_to_the_planner(self):
        generator = ScriptedGenerator(
            plan("SELECT SUM(quantity) FROM sales WHERE products.category = 'Alimentos'"),
            json.dumps({"action": "replan", "reason": "the plan needs a join"}),
            plan("SELECT SUM(s.quantity) FROM sales s JOIN products p ON s.product_id = p.product_id WHERE p.category = 'Alimentos'"),
            "Se vendieron 334 unidades de Alimentos.",
            checklist("number"),
        )
        result = AgentSystem(DB, generator).answer("¿Cuántas unidades de Alimentos se vendieron en total?")
        self.assertEqual(result.composed.value, 334)
        self.assertIn("the plan needs a join", generator.prompts[2])
        self.assertIn("no such column", generator.prompts[2])

    def test_verifier_sends_a_listing_back_when_one_name_is_asked(self):
        generator = ScriptedGenerator(
            plan(ALL_PRODUCTS),
            "Audifonos Inalambricos tuvo el mayor ingreso.",
            checklist("name"),
            plan(TOP_PRODUCT),
            "El producto con mayor ingreso fue Notebook 14 pulgadas, con 91298340.",
        )
        result = AgentSystem(DB, generator).answer(TOP_QUESTION)
        self.assertEqual(result.composed.value, [["Notebook 14 pulgadas", 91298340]])
        self.assertTrue(result.fidelity.faithful, result.fidelity.issues)
        self.assertIn("asks for one product or category name", generator.prompts[3])
        self.assertEqual(result.trace.model_calls["Verificador"], 1)  # The checklist is computed once.

    def test_without_the_critic_the_listing_is_accepted(self):
        generator = ScriptedGenerator(plan(ALL_PRODUCTS), "Resultado: Notebook 14 pulgadas con 91298340.")
        result = AgentSystem(DB, generator, disabled=["critic", "report_refine"]).answer(TOP_QUESTION)
        self.assertEqual(len(result.composed.value), 10)
        self.assertEqual(result.trace.model_calls["Verificador"], 0)

    def test_reporter_rewrites_an_unfaithful_report(self):
        ropa = plan("SELECT COUNT(*) FROM products WHERE category = 'Ropa'")
        generator = ScriptedGenerator(ropa, "La categoría Ropa tiene 3 productos.", checklist("number"),
                                      "La categoría Ropa tiene 2 productos.")
        result = AgentSystem(DB, generator).answer("¿Cuántos productos tiene Ropa?")
        self.assertTrue(result.fidelity.faithful)
        self.assertIn("does not state the verified answer", generator.prompts[3])
        generator = ScriptedGenerator(ropa, "La categoría Ropa tiene 3 productos.", checklist("number"))
        result = AgentSystem(DB, generator, disabled=["report_refine"]).answer("¿Cuántos productos tiene Ropa?")
        self.assertFalse(result.fidelity.faithful)

    def test_planner_revises_invalid_json_itself(self):
        generator = ScriptedGenerator("not json", plan("SELECT price FROM products WHERE name = 'Set de Ollas'"),
                                      "El Set de Ollas cuesta 59990.", checklist("number"))
        result = AgentSystem(DB, generator).answer("¿Cuánto cuesta el Set de Ollas?")
        self.assertEqual(result.composed.value, 59990)
        self.assertIn("not a valid plan", generator.prompts[1])

    def test_fallback_keeps_the_previous_answer_when_the_new_plan_fails(self):
        bad = plan("SELECT nothing FROM nowhere")
        generator = ScriptedGenerator(
            plan(ALL_PRODUCTS), "Resultado: Notebook 14 pulgadas.", checklist("name"),
            bad, json.dumps({"action": "replan", "reason": "x"}),
            bad, json.dumps({"action": "replan", "reason": "x"}),
        )
        result = AgentSystem(DB, generator).answer(TOP_QUESTION)
        self.assertEqual(result.status, "respaldo")
        self.assertEqual(len(result.composed.value), 10)

    def test_traces_are_saved_for_answers_and_failures(self):
        generator = ScriptedGenerator(plan(Q1_TRUNCATED, Q2, operation="growth_pct"))
        with tempfile.TemporaryDirectory() as folder:
            try:
                AgentSystem(DB, generator, disabled=["self_correction"]).answer(GROWTH_QUESTION)
            except AgentFailure as failure:
                save_agent_trace(failure, Path(folder) / "t.json")
            data = json.loads((Path(folder) / "t.json").read_text(encoding="utf-8"))
        self.assertEqual(data["trace"][0]["tipo"], "pregunta")
        self.assertIn("error", data)


class Version2Tests(unittest.TestCase):
    """Checks added after the first Colab run (results/deliverable3_v1/)."""

    def test_unused_steps_order_and_missing_labels(self):
        steps = [{"id": "step_1", "sql": "SELECT SUM(quantity) FROM sales"},
                 {"id": "step_2", "sql": "SELECT SUM(quantity) FROM sales WHERE product_id IN (SELECT product_id FROM products WHERE category = 'Electronica')"}]
        self.assertEqual(unused_steps(steps, "direct"), ["step_1"])
        self.assertEqual(unused_steps(steps, "share_pct"), [])
        chained = [steps[0], {"id": "step_2", "sql": "SELECT * FROM step_1"}]
        self.assertEqual(unused_steps(chained, "direct"), [])
        self.assertEqual(first_dates([Q2, Q1]), ["2026-06-01", "2026-03-01"])
        labels = {"Alimentos", "Electronica", "Notebook 14 pulgadas"}
        question = "¿Cuántas unidades se vendieron en la categoría Alimentos en agosto?"
        self.assertEqual(missing_labels(question, ["SELECT SUM(quantity) FROM sales"], labels), ["Alimentos"])
        self.assertEqual(missing_labels(question, ["SELECT 1 WHERE category = 'Alimentos'"], labels), [])

    def test_verifier_sends_back_a_plan_that_drops_the_category(self):
        question = "¿Cuántas unidades de la categoría Alimentos se vendieron en total?"
        generator = ScriptedGenerator(
            plan("SELECT SUM(quantity) FROM sales"), "Se vendieron 1655 unidades.", checklist("number"),
            plan("SELECT SUM(s.quantity) FROM sales s JOIN products p ON s.product_id = p.product_id WHERE p.category = 'Alimentos'"),
            "Se vendieron 334 unidades de Alimentos.",
        )
        result = AgentSystem(DB, generator).answer(question)
        self.assertEqual(result.composed.value, 334)
        self.assertIn("names 'Alimentos'", generator.prompts[3])

    def test_verifier_sends_back_a_growth_with_periods_in_reverse(self):
        generator = ScriptedGenerator(
            plan(Q2, Q1, operation="growth_pct"), "Las ventas disminuyeron un 37.55%.",
            checklist("percent_change", *GROWTH_PERIODS),
            plan(Q1, Q2, operation="growth_pct"), "Las ventas aumentaron un 60.13%.",
        )
        result = AgentSystem(DB, generator).answer(GROWTH_QUESTION)
        self.assertGreater(result.composed.value["growth_pct"], 0)
        self.assertIn("earlier period in the first step", generator.prompts[3])
        # The order problem is reported by revisar_periodos, so the trace shows which tool found it.
        outputs = [m.content for m in result.trace.messages
                   if m.sender == "herramienta:revisar_periodos"]
        self.assertEqual(outputs, [{"sin_cubrir": [], "orden_invertido": True}])

    def test_analyst_can_swap_steps_and_gives_up_on_an_unchanged_fix(self):
        total = "SELECT SUM(quantity) FROM sales"
        part = "SELECT SUM(s.quantity) FROM sales s JOIN products p ON s.product_id = p.product_id WHERE p.category = 'Electronica'"
        question = "¿Qué porcentaje de las unidades corresponde a Electronica?"
        generator = ScriptedGenerator(plan(part, total, operation="share_pct"), json.dumps({"action": "swap"}),
                                      "Electronica representa el 29.49% de las unidades.", checklist("share"))
        result = AgentSystem(DB, generator).answer(question)
        self.assertAlmostEqual(result.composed.value["share_pct"], 29.4864, places=3)
        self.assertIn("invierte el orden de los pasos", [m.kind for m in result.trace.messages])
        generator = ScriptedGenerator(plan(part, total, operation="share_pct"), fix("step_1", part),
                                      plan(total, part, operation="share_pct"),
                                      "Electronica representa el 29.49% de las unidades.", checklist("share"))
        result = AgentSystem(DB, generator).answer(question)
        self.assertIn("replanificar (corrección sin cambios)", [m.kind for m in result.trace.messages])

    def test_growth_instruction_only_for_growth_answers(self):
        ropa = plan("SELECT COUNT(*) FROM products WHERE category = 'Ropa'")
        generator = ScriptedGenerator(ropa, "La categoría Ropa tiene 2 productos.", checklist("number"))
        AgentSystem(DB, generator).answer("¿Cuántos productos tiene Ropa?")
        self.assertNotIn("one percentage change", generator.prompts[1])
        generator = ScriptedGenerator(plan(Q1, Q2, operation="growth_pct"), "Las ventas aumentaron un 60.13%.",
                                      checklist("percent_change", *GROWTH_PERIODS))
        AgentSystem(DB, generator).answer(GROWTH_QUESTION)
        self.assertIn("one percentage change", generator.prompts[1])


class Deliverable2FailureTests(unittest.TestCase):
    """The plans that failed in the Deliverable 2 run (results/deliverable2_v3.json) are caught."""

    ASKS_FOR = {"q03": "name", "q11": "share", "q09-p": "percent_change", "q14-p": "name"}

    class _StopAtHandoff(Exception):
        pass

    def _first_handoff(self, raw_plan, asks_for, question):
        test = self

        class Generator:
            planner_calls = 0

            def generate(self, prompt, max_new_tokens=None):
                if prompt.startswith("You are the Planner agent"):
                    self.planner_calls += 1
                    if self.planner_calls == 1:
                        return raw_plan
                    raise test._StopAtHandoff("Planificador: " + prompt[prompt.index("Reason:"):].splitlines()[0])
                if prompt.startswith("You are the SQL Analyst agent"):
                    raise test._StopAtHandoff("Analista SQL: " + prompt[prompt.index("Error observed:"):][:200])
                if prompt.startswith("You are the Verifier agent"):
                    return checklist(asks_for)
                return "Reporte."

        with self.assertRaises(self._StopAtHandoff) as context:
            AgentSystem(DB, Generator()).answer(question)
        return str(context.exception)

    def test_failed_plans_of_deliverable_2_are_sent_back(self):
        records = json.loads((ROOT / "results" / "deliverable2_v3.json").read_text(encoding="utf-8"))["records"]
        by_id = {(r["set"], r["id"]): r for r in records}
        cases = [("official", "q03"), ("official", "q11"), ("heldout", "q09-p"), ("heldout", "q14-p"), ("official", "q09")]
        for key in cases:
            record = by_id[key]
            raw_plan = record["solution"]["attempts"][-1]["raw_plan"]
            handoff = self._first_handoff(raw_plan, self.ASKS_FOR.get(key[1], "percent_change"), record["question"])
            expected = "Analista SQL: Error observed:" if key[1] == "q09" else "Planificador: Reason: The question asks for"
            self.assertTrue(handoff.startswith(expected), (key, handoff))


class EvaluationTests(unittest.TestCase):
    def test_every_configuration_runs_and_is_scored(self):
        sets = {"official": OFFICIAL["questions"][:3] + OFFICIAL["questions"][8:9], "heldout": HELDOUT["questions"][:2]}
        summaries = {}
        with tempfile.TemporaryDirectory() as folder:
            for config in CONFIGS:
                result = run_config(config, OracleGenerator(), DB, sets, OFFICIAL["schema"],
                                    trace_dir=folder if config == "agentes" else None, progress=lambda _: None)
                summaries[config] = result["summary"]
            self.assertEqual(len(list(Path(folder).glob("*.json"))), 6)
        self.assertEqual(summaries["agentes"]["official"]["e2e"]["global"], [4, 4])
        self.assertNotIn("e2e", summaries["baseline"]["official"])
        self.assertEqual(summaries["sin_critic"]["official"]["model_calls"], 2.0)
        table = latex_table(summaries)
        self.assertIn("Sistema de agentes v2 (D3) & 4/4 & 4/4 & 4/4", table)
        self.assertEqual(table.count(r"\\"), len(CONFIGS) + 2)


class RecordedTraceTests(unittest.TestCase):
    """Replays the v2 run from the model outputs saved in its traces (no GPU)."""

    # Tool outputs whose logging gained detail after v2 (pasos descartados, orden de los períodos).
    LOGGING_ONLY = {"herramienta:forma_resultado", "herramienta:revisar_periodos"}

    @staticmethod
    def _key(message):
        return (message["de"], message["para"], message["tipo"],
                json.dumps(message["contenido"], ensure_ascii=False, sort_keys=True, default=str))

    def test_current_code_reproduces_every_v2_decision(self):
        paths = sorted((ROOT / "results" / "deliverable3" / "trazas").glob("*.json"))
        self.assertEqual(len(paths), 27)
        for path in paths:
            with self.subTest(trace=path.name):
                recorded = json.loads(path.read_text(encoding="utf-8"))
                outputs = [m["salida_modelo"] for m in recorded["trace"] if "salida_modelo" in m]
                self.assertEqual(len(outputs), recorded["model_calls"])
                try:
                    messages = AgentSystem(DB, ScriptedGenerator(*outputs)).answer(recorded["question"]).trace.to_list()
                except AgentFailure as failure:
                    messages = failure.trace.to_list()
                expected = [self._key(m) for m in recorded["trace"] if m["de"] not in self.LOGGING_ONLY]
                replayed = [self._key(m) for m in messages if m["de"] not in self.LOGGING_ONLY]
                self.assertEqual(replayed, expected)


if __name__ == "__main__":
    unittest.main()
