import unittest

from helpers import DB, QUESTIONS

from sql4business.evaluation import expected_answer
from sql4business.fidelity import check_report


def gold(question_id):
    return expected_answer(QUESTIONS[question_id], DB).value


class FidelityTests(unittest.TestCase):
    """Cases from the audit (artifacts/auditoria-deliverable-2.md, finding A4)."""

    def assertFaithful(self, report, answer, evidence=""):
        check = check_report(report, answer, evidence)
        self.assertTrue(check.faithful, (report, check.issues))

    def assertUnfaithful(self, report, answer, evidence=""):
        self.assertFalse(check_report(report, answer, evidence).faithful, report)

    def test_faithful_reports_pass(self):
        self.assertFaithful("La categoría Ropa tiene dos productos distintos.", 2)
        self.assertFaithful("Sales grew 60.1% from the first to the second quarter.", gold("q09"))
        self.assertFaithful("1655 units were sold, 488 of them Electronica.", gold("q11"))
        self.assertFaithful("Electronica represents 29.49% of the 1,655 units sold.", gold("q11"))
        self.assertFaithful(
            "El ingreso de agosto de 2026 fue mayor que el de marzo de 2026: aumentó un 59.08%, "
            "de 18,5 millones a 29,4 millones.",
            gold("q12"),
            QUESTIONS["q12"]["question"],
        )
        self.assertFaithful("El líder no cambió: Notebook 14 pulgadas encabezó ambos trimestres.", gold("q10"))
        self.assertFaithful("Sales fell 12.5% between March and August 2026.", {"previous": 8.0, "current": 7.0, "growth_pct": -12.5, "direction": "decrease"}, "2026-03-01 2026-08-31")

    def test_unfaithful_reports_fail(self):
        self.assertUnfaithful("In 2026 the Ropa category had 3 distinct products.", 2)
        self.assertUnfaithful("Sales decreased by 60.13% between the two quarters.", gold("q09"))
        self.assertUnfaithful("Sales grew 60.13%, driven by a 250% jump in Electronica.", gold("q09"))
        self.assertUnfaithful("The leader changed: Notebook 14 pulgadas led Q1 and Notebook 14 pulgadas led Q2.", gold("q10"))
        self.assertUnfaithful("August 2026 revenue was higher than March 2026 revenue by 34.7%.", gold("q12"))
        self.assertUnfaithful('Verified result: "Notebook 14 pulgadas"', 34)
        # Deliverable 3 demo (q09-p): one change reported as if each period had grown by it.
        self.assertUnfaithful(
            "Las ventas aumentaron un 60.13% entre marzo y mayo y un 60.13% entre junio y agosto.", gold("q09")
        )

    def test_figures_from_the_question_or_the_sql_are_allowed(self):
        evidence = "¿Cuántas unidades se vendieron en junio de 2026? SELECT ... BETWEEN '2026-06-01' AND '2026-06-30'"
        self.assertFaithful("Se vendieron 34 unidades entre el 1 y el 30 de junio de 2026.", 34, evidence)
        self.assertUnfaithful("Se vendieron 34 unidades, 12 más que en mayo.", 34, evidence)


if __name__ == "__main__":
    unittest.main()
