import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.composition import compose_results
from sql4business.evaluation import gold_composed_answer, matches_gold_results
from sql4business.pipeline import BusinessAssistant
from sql4business.sql_tools import SQLExecutionError, SQLValidationError, execute_read_only


class SQL4BusinessTests(unittest.TestCase):
    def test_only_read_only_sql_is_allowed(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE values_table (value INTEGER)")
        conn.execute("INSERT INTO values_table VALUES (1)")
        self.assertEqual(execute_read_only(conn, "SELECT value FROM values_table").rows, [(1,)])
        with self.assertRaises(SQLValidationError):
            execute_read_only(conn, "DELETE FROM values_table")

    def test_growth_is_calculated_deterministically(self):
        answer = compose_results("growth_pct", [[(100,)], [(125,)]])
        self.assertAlmostEqual(answer.value["growth_pct"], 25.0)

    def test_gold_comparison_accepts_equivalent_rows(self):
        self.assertTrue(matches_gold_results([[(1,), (2,)]], [[[1], [2]]]))

    def test_filter_then_rank_gold_answer_uses_database_ids(self):
        question = {
            "combine": "filter_then_rank",
            "gold_result": [
                [[6], [7], [8], [9]],
                [["Notebook 14 pulgadas"], ["Set de Ollas"], ["Zapatillas Urbanas"]],
            ],
        }
        answer = gold_composed_answer(question, ROOT / "data" / "business.db")
        self.assertEqual(answer["winner"], "Zapatillas Urbanas")

    def test_date_outside_database_range_is_rejected(self):
        import sqlite3

        conn = sqlite3.connect(ROOT / "data" / "business.db")
        with self.assertRaisesRegex(SQLExecutionError, "outside the available sales range"):
            execute_read_only(
                conn,
                "SELECT SUM(amount) FROM sales WHERE sale_date BETWEEN '2023-03-01' AND '2023-05-31'",
            )

    def test_direct_plan_with_duplicate_steps_is_reduced(self):
        plan = BusinessAssistant._validate_plan(
            {
                "steps": [
                    {"sql": "SELECT 1"},
                    {"sql": "SELECT 2"},
                ],
                "composition": {"operation": "direct"},
            }
        )
        self.assertEqual(len(plan["steps"]), 1)
        self.assertIn("warnings", plan)
        self.assertEqual(plan["steps"][0]["sql"], "SELECT 2")

    def test_operation_intent_is_classified(self):
        from sql4business.pipeline import infer_operation

        self.assertEqual(infer_operation("growth percentage between two quarters"), "growth_pct")
        self.assertEqual(infer_operation("stock below reorder point and highest income"), "filter_then_rank")

    def test_filter_then_rank_accepts_names_and_unsorted_revenue_rows(self):
        conn = sqlite3.connect(ROOT / "data" / "business.db")
        answer = compose_results(
            "filter_then_rank",
            [
                [("Lampara de Escritorio",), ("Zapatillas Urbanas",)],
                [("Lampara de Escritorio", 100), ("Zapatillas Urbanas", 200)],
            ],
            conn=conn,
        )
        self.assertEqual(answer.value["winner"], "Zapatillas Urbanas")


if __name__ == "__main__":
    unittest.main()
