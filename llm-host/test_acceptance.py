"""Kabul koşucusunun değerlendirme mantığı (gerçek yığın olmadan test edilir)."""
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

from acceptance_runner import (  # noqa: E402
    SCENARIOS, baseline_problems, collect_arguments, evaluate,
    required_preconditions, summarize)


def response(goal, steps, succeeded=True, answer="cevap"):
    """steps: (tool, status, arguments) üçlüleri."""
    return {
        "plan": {"goal": goal},
        "trace": [{"stepId": f"step_{i}", "tool": tool, "status": status, "arguments": args,
                   "resultSummary": "hata detayı" if status == "failed" else "ok"}
                  for i, (tool, status, args) in enumerate(steps, start=1)],
        "finalAnswer": answer,
        "succeeded": succeeded,
    }


class ArgumentCollectionTest(unittest.TestCase):
    def test_nested_filters_are_flattened(self):
        trace = [{"arguments": {"objective": "CHEAPEST", "filters": {"min_rating": 4.5}}}]
        self.assertEqual(collect_arguments(trace),
                         {"objective": "CHEAPEST", "min_rating": 4.5})

    def test_arguments_inside_lists_are_found(self):
        trace = [{"arguments": {"items": [{"product_id": 1, "quantity": 8}]}}]
        self.assertEqual(collect_arguments(trace)["product_id"], 1)


class EvaluateTest(unittest.TestCase):
    SCENARIO = {
        "id": "ornek",
        "expect": {
            "goals": ["PLAN"],
            "tools_required": ["create_procurement_plan"],
            "tools_forbidden": ["place_order"],
            "arguments_contain": {"objective": "CHEAPEST"},
        },
    }

    def test_matching_run_passes(self):
        result = evaluate(self.SCENARIO, response("PLAN", [
            ("calculate_replenishment", "success", {}),
            ("create_procurement_plan", "success", {"objective": "CHEAPEST"})]))

        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["signature"],
                         "PLAN: calculate_replenishment → create_procurement_plan")

    def test_failed_step_is_reported(self):
        result = evaluate(self.SCENARIO, response("PLAN", [
            ("create_procurement_plan", "failed", {"objective": "CHEAPEST"})]))

        self.assertFalse(result["ok"])
        self.assertTrue(any(problem.startswith("adım başarısız: create_procurement_plan")
                            for problem in result["problems"]), result["problems"])

    def test_wrong_goal_is_reported(self):
        result = evaluate(self.SCENARIO, response("DRAFT", [
            ("create_procurement_plan", "success", {"objective": "CHEAPEST"})]))

        self.assertFalse(result["ok"])
        self.assertTrue(any("goal" in problem for problem in result["problems"]))

    def test_forbidden_tool_is_reported(self):
        result = evaluate(self.SCENARIO, response("PLAN", [
            ("create_procurement_plan", "success", {"objective": "CHEAPEST"}),
            ("place_order", "success", {})]))

        self.assertFalse(result["ok"])
        self.assertIn("yasak tool çağrıldı: place_order", result["problems"])

    def test_missing_and_wrong_arguments_are_distinguished(self):
        eksik = evaluate(self.SCENARIO, response("PLAN", [
            ("create_procurement_plan", "success", {})]))
        yanlis = evaluate(self.SCENARIO, response("PLAN", [
            ("create_procurement_plan", "success", {"objective": "FASTEST"})]))

        self.assertIn("argüman verilmemiş: objective", eksik["problems"])
        self.assertIn("objective='FASTEST', beklenen 'CHEAPEST'", yanlis["problems"])

    def test_constraint_written_to_wrong_parameter_is_caught(self):
        """Bütçe kısıtının max_unit_price'a kaydırılması sessiz yanlış cevaptır."""
        scenario = {"id": "butce", "expect": {
            "arguments_contain": {"max_total_budget": 50000},
            "arguments_forbidden": {"max_unit_price": 50000}}}

        result = evaluate(scenario, response("PLAN", [
            ("create_procurement_plan", "success", {"filters": {"max_unit_price": 50000}})]))

        self.assertFalse(result["ok"])
        self.assertIn("argüman verilmemiş: max_total_budget", result["problems"])
        self.assertIn("kısıt yanlış parametreye yazılmış: max_unit_price=50000",
                      result["problems"])

    def test_skipped_steps_do_not_count_as_failure_by_themselves(self):
        """Atlanan adım tek başına hata değil; asıl hata 'failed' olanda raporlanır."""
        result = evaluate({"id": "x", "expect": {}}, response("ORDER", [
            ("place_order", "failed", {}),
            ("create_incoming_orders", "skipped", {})]))

        self.assertEqual([p for p in result["problems"] if "skipped" in p], [])
        self.assertTrue(any(problem.startswith("adım başarısız: place_order")
                            for problem in result["problems"]), result["problems"])


class OverallFailureTest(unittest.TestCase):
    """Regresyon: sıfır adımlı bir plan patladığında iz boş kalır ve
    yalnızca adım durumlarına bakan değerlendirme bunu 'başarılı' sayıyordu."""

    def test_stepless_failure_is_not_a_pass(self):
        result = evaluate({"id": "x", "expect": {"goals": ["REASON"], "tools_required": []}},
                          response("REASON", [], succeeded=False,
                                   answer="İşlem tamamlanamadı. Lütfen isteğinizi kontrol edin."))

        self.assertFalse(result["ok"])
        self.assertIn("istek başarısız tamamlandı", result["problems"])

    def test_stepless_success_still_passes(self):
        result = evaluate({"id": "x", "expect": {"goals": ["REASON"], "tools_required": []}},
                          response("REASON", [], succeeded=True))

        self.assertTrue(result["ok"], result["problems"])

    def test_failure_detail_is_carried_into_the_report(self):
        result = evaluate({"id": "x", "expect": {}}, response("PLAN", [
            ("create_procurement_plan", "failed", {})], succeeded=False))

        self.assertTrue(any("hata detayı" in problem for problem in result["problems"]))

    def test_missing_flag_falls_back_to_answer_text(self):
        payload = response("REASON", [], answer="İşlem tamamlanamadı.")
        del payload["succeeded"]

        result = evaluate({"id": "x", "expect": {}}, payload)

        self.assertFalse(result["ok"])


class SummaryTest(unittest.TestCase):
    def test_variance_counts_distinct_plans(self):
        runs = [
            {"ok": True, "signature": "PLAN: a → b", "problems": [], "duration": 10.0},
            {"ok": True, "signature": "PLAN: a → b", "problems": [], "duration": 12.0},
            {"ok": False, "signature": "PLAN: b", "problems": ["eksik tool: a"], "duration": 8.0},
        ]
        summary = summarize({"id": "x"}, runs)

        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["runs"], 3)
        self.assertEqual(summary["distinct_plans"], 2)
        self.assertEqual(summary["median_duration"], 10.0)
        self.assertEqual(summary["problems"], ["eksik tool: a"])


class ScenarioDefinitionTest(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [scenario["id"] for scenario in SCENARIOS]
        self.assertEqual(len(ids), len(set(ids)))

    WRITE_TOOLS = {"place_order", "create_purchase_draft", "receive_orders", "receive_order"}

    def test_write_scenarios_are_marked(self):
        """Veri değiştiren senaryolar varsayılan koşumda çalışmamalı."""
        for scenario in SCENARIOS:
            tools = scenario.get("expect", {}).get("tools_required", [])
            if self.WRITE_TOOLS & set(tools):
                self.assertTrue(scenario.get("writes"),
                                f"{scenario['id']} yazma senaryosu olarak işaretlenmemiş")

    # Taslak olusturmak onay ISTEMENIN kendisidir; siparisi kesinlestirmez ve
    # stogu degistirmez. Onay kapisi yalnizca bu iki isleme uygulanir.
    COMMIT_TOOLS = {"place_order", "receive_orders", "receive_order"}

    def test_commit_scenarios_include_a_confirmation_turn(self):
        """Onay kapısı: sipariş kesinleştiren veya stok artıran araç ilk turda çağrılamaz."""
        for scenario in SCENARIOS:
            tools = set(scenario.get("expect", {}).get("tools_required", []))
            if self.COMMIT_TOOLS & tools:
                self.assertGreaterEqual(
                    len(scenario["turns"]), 2,
                    f"{scenario['id']} tek turda yazma bekliyor; onay adımı yok")

    def test_receiving_never_shares_a_turn_with_the_listing(self):
        """Regresyon: senaryo bir turda list_incoming_orders + receive_order bekliyordu."""
        for scenario in SCENARIOS:
            tools = set(scenario.get("expect", {}).get("tools_required", []))
            self.assertFalse(
                {"receive_orders", "receive_order"} & tools and "list_incoming_orders" in tools,
                f"{scenario['id']} teslim almayı listeleme turuyla birleştiriyor")

    def test_every_scenario_has_turns_and_expectations(self):
        for scenario in SCENARIOS:
            self.assertTrue(scenario["turns"], scenario["id"])
            self.assertIn("expect", scenario)


class BaselineStateTest(unittest.TestCase):
    """Kirli veriyle koşmak kodla ilgisi olmayan başarısızlıklar üretir.

    20.08 koşumunda 9/27 çıktı; düşen senaryoların çoğu önceki manuel testlerin
    stoğu doldurmuş olmasındandı. Ölçüm, ölçtüğü şeyin geçerli olduğunu önce
    kendisi söylemeli.
    """

    PLAN = {"id": "p", "turns": ["x"],
            "expect": {"tools_required": ["create_procurement_plan"]}}
    RECEIVE = {"id": "r", "turns": ["x", "y"],
               "expect": {"tools_required": ["receive_orders"]}}
    LISTING = {"id": "l", "turns": ["x"],
               "expect": {"tools_required": ["list_incoming_orders"]}}
    NO_TOOLS = {"id": "n", "turns": ["x"], "expect": {"tools_required": []}}

    FULL = {"replenishment": 3, "low_stock": 2, "pending_order": 1, "receivable_order": 1}
    DRAINED = {"replenishment": 0, "low_stock": 0, "pending_order": 0, "receivable_order": 0}

    def test_healthy_baseline_has_no_problems(self):
        self.assertEqual(baseline_problems(self.FULL, SCENARIOS), [])

    def test_drained_stock_blocks_planning_scenarios(self):
        problems = baseline_problems(self.DRAINED, [self.PLAN])

        self.assertEqual(len(problems), 1)
        self.assertIn("replenishment", problems[0])

    def test_receive_scenario_needs_a_receivable_order(self):
        baseline = dict(self.FULL, receivable_order=0)

        problems = baseline_problems(baseline, [self.RECEIVE])

        self.assertEqual(len(problems), 1)
        self.assertIn("readyOnly", problems[0])

    def test_future_delivery_alone_is_not_enough(self):
        """Bekleyen sipariş var ama tarihi gelmemiş: teslim alma ölçülemez."""
        baseline = dict(self.FULL, pending_order=1, receivable_order=0)

        self.assertTrue(baseline_problems(baseline, [self.RECEIVE]))

    def test_listing_only_needs_a_pending_order_not_a_ready_one(self):
        baseline = dict(self.FULL, receivable_order=0)

        self.assertEqual(baseline_problems(baseline, [self.LISTING]), [])

    def test_unrelated_scenario_is_not_blocked(self):
        """--only ile tek senaryo koşarken alakasız ön koşul dayatılmamalı."""
        self.assertEqual(baseline_problems(self.DRAINED, [self.NO_TOOLS]), [])

    def test_receivable_requirement_subsumes_pending(self):
        """Aynı eksiği iki kez raporlamak kullanıcıyı yanıltır."""
        self.assertEqual(required_preconditions([self.RECEIVE]), {"receivable_order"})

    def test_every_scenario_precondition_has_a_message(self):
        from acceptance_runner import REQUIREMENT_MESSAGES
        for requirement in required_preconditions(SCENARIOS):
            self.assertIn(requirement, REQUIREMENT_MESSAGES)


if __name__ == "__main__":
    unittest.main()
