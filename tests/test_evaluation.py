import json
import unittest

from helpers import DB, QUESTIONS, ROOT, ScriptedGenerator, plan, report

from sql4business.evaluation import (
    answer_correct,
    candidate_sets,
    evaluate,
    expected_answer,
    latex_table,
    sql_correct,
)
from sql4business.sql_tools import connect_read_only, execute_baseline


def answer_ok(question_id, result_sets):
    question = QUESTIONS[question_id]
    return answer_correct(result_sets, question, expected_answer(question, DB))


def run(sql):
    conn = connect_read_only(DB)
    try:
        return [e.rows for e in execute_baseline(conn, sql)]
    finally:
        conn.close()


class CriterionTests(unittest.TestCase):
    def test_sql_criterion_is_deliverable_1_exact_row_sets(self):
        gold = [[[34]]]
        self.assertTrue(sql_correct([[(34,)]], gold))
        self.assertTrue(sql_correct([[(99,)], [(34.0,)]], gold))
        self.assertFalse(sql_correct([[(34, "Notebook")]], gold))
        self.assertTrue(sql_correct([[("b",), ("a",)]], [[["a"], ["b"]]]))

    def test_single_query_answer_to_a_combined_question_counts(self):
        single = run(
            "SELECT p.name, SUM(s.amount) FROM products p JOIN inventory i ON p.product_id = i.product_id "
            "JOIN sales s ON p.product_id = s.product_id WHERE i.stock < i.reorder_point "
            "GROUP BY p.product_id ORDER BY 2 DESC LIMIT 1"
        )
        self.assertTrue(answer_ok("q14", single))
        self.assertFalse(sql_correct(single, QUESTIONS["q14"]["gold_result"]))
        full_ranking = run("SELECT p.name FROM sales s JOIN products p USING(product_id) GROUP BY p.product_id ORDER BY SUM(s.amount) DESC")
        self.assertFalse(answer_ok("q14", full_ranking))

    def test_answer_rules_per_operation(self):
        self.assertTrue(answer_ok("q12", [[(59.08,)]]))
        self.assertFalse(answer_ok("q12", [[(-59.0803,)]]))
        self.assertTrue(answer_ok("q11", [[(1655, 488)]]))
        self.assertFalse(answer_ok("q11", [[(29.486,)]]))
        self.assertTrue(answer_ok("q11-p", [[(29.486,)]]))
        self.assertTrue(answer_ok("q10", [[("Notebook 14 pulgadas",)], [("Notebook 14 pulgadas",)]]))
        self.assertFalse(answer_ok("q10", [[("Notebook 14 pulgadas",), ("Set de Ollas",)]]))
        self.assertTrue(answer_ok("q06", run("SELECT p.name, i.stock FROM inventory i JOIN products p USING(product_id) WHERE i.stock < i.reorder_point")))
        self.assertTrue(answer_ok("q01", [[(34, "Notebook 14 pulgadas")]]))
        self.assertTrue(answer_ok("q09", candidate_sets({"previous": 1.0, "current": 2.0, "growth_pct": 60.12569, "direction": "increase"})))


class EvaluateTests(unittest.TestCase):
    def test_every_system_is_scored_with_the_same_criteria(self):
        question = QUESTIONS["q13"]
        direct_sql = "SELECT COUNT(*) FROM products WHERE category = 'Ropa'"
        generator = ScriptedGenerator(direct_sql, direct_sql, plan(direct_sql), report("Ropa tiene 2 productos."))
        results = evaluate(generator, DB, {"official": [question]}, baseline_schema="schema", progress=lambda _: None)
        record = results["records"][0]
        for system in ("baseline", "grounded", "solution"):
            self.assertTrue(record[system]["sql"] and record[system]["answer"], system)
        self.assertTrue(record["solution"]["e2e"])
        self.assertEqual(results["summary"]["official"]["solution"]["e2e"]["global"], [1, 1])

    def test_v1_results_rescored(self):
        """The committed v1 run under the shared answer metric (audit finding A2)."""

        records = json.loads((ROOT / "results" / "deliverable2_v1.json").read_text(encoding="utf-8"))
        baseline = sum(answer_ok(r["id"], run(r["baseline"]["model_output"])) for r in records)
        self.assertEqual(baseline, 7)  # 6 punctual + q14 answered in one query

    def test_latex_table_renders_both_sets(self):
        counts = {"puntual": [1, 2], "combinada": [0, 1], "global": [1, 3]}
        systems = {"baseline": {"sql": counts, "answer": counts}, "grounded": {"sql": counts, "answer": counts},
                   "solution": {m: counts for m in ("sql", "answer", "report", "e2e")}}
        table = latex_table({"official": systems, "heldout": systems})
        self.assertEqual(table.count(r"\\"), 10)  # 2 header rows + 8 metric rows
        self.assertIn("Solución estructurada & sql & 1/2 & 0/1 & 1/3 & 1/2 & 0/1", table)


if __name__ == "__main__":
    unittest.main()
