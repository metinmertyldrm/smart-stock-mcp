"""Regression tests for the offline golden AI evaluator."""
import unittest
from copy import deepcopy

from test_support import install_optional_stubs

install_optional_stubs()

from golden_eval import evaluate_case, load_cases, run_cases  # noqa: E402


class GoldenCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases()

    def test_all_golden_cases_pass(self):
        results = run_cases(self.cases)
        failures = {
            result["id"]: result["problems"]
            for result in results
            if not result["ok"]
        }
        self.assertEqual({}, failures)

    def test_ids_are_unique(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_corpus_covers_fast_and_full_routes(self):
        modes = {case.get("route", {}).get("mode") for case in self.cases}
        self.assertEqual({"fast", "full"}, modes)

    def test_corpus_covers_critical_safety_boundaries(self):
        tags = {tag for case in self.cases for tag in case.get("tags", [])}
        self.assertTrue({"draft", "order", "receive", "write-boundary"} <= tags)

        rejection_tags = {
            tag
            for case in self.cases
            if case.get("validation_error_contains")
            for tag in case.get("tags", [])
        }
        self.assertIn("order", rejection_tags)
        self.assertIn("receive", rejection_tags)

    def test_every_case_has_user_route_and_plan(self):
        for case in self.cases:
            with self.subTest(case=case.get("id")):
                self.assertTrue(case.get("user"))
                self.assertIn(case.get("route", {}).get("mode"), {"fast", "full"})
                self.assertEqual("execution_plan", (case.get("plan") or {}).get("type"))


class GoldenEvaluatorDetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in load_cases()}

    def test_wrong_objective_is_detected(self):
        case = deepcopy(self.cases["balanced_objective_is_explicit"])
        case["plan"]["steps"][-1]["arguments"]["objective"] = "CHEAPEST"

        result = evaluate_case(case)

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("objective='CHEAPEST'" in problem for problem in result["problems"]),
            result["problems"],
        )

    def test_wrong_fast_route_expectation_is_detected(self):
        case = deepcopy(self.cases["fast_out_of_stock_listing"])
        case["route"] = {"mode": "full"}

        result = evaluate_case(case)

        self.assertFalse(result["ok"])
        self.assertTrue(any("route=fast" in problem for problem in result["problems"]), result["problems"])

    def test_missing_confirmation_rejection_is_enforced(self):
        case = deepcopy(self.cases["order_requires_pending_draft"])
        case["state"]["pending_draft_id"] = 99

        result = evaluate_case(case)

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("beklenen hata" in problem for problem in result["problems"]),
            result["problems"],
        )

    def test_unknown_case_selection_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "Bilinmeyen golden case"):
            run_cases(list(self.cases.values()), {"does-not-exist"})


if __name__ == "__main__":
    unittest.main()
