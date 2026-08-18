"""Plan doğrulama: şema kuralları ve mevcut duruma karşı kontroller."""
import json
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

import app  # noqa: E402


def plan_json(**payload):
    payload.setdefault("type", "execution_plan")
    return json.dumps(payload, ensure_ascii=False)


class OrderPlanShapeTest(unittest.TestCase):
    """ORDER zinciri artık create_incoming_orders ile bitebilir (taslak maddesi 8)."""

    STEPS = [
        {"id": "step_1", "tool": "place_order",
         "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}},
        {"id": "step_2", "tool": "create_incoming_orders",
         "arguments": {"items": {"$from": "step_1", "$transform": "order_to_incoming_items"}}},
    ]

    def test_two_step_order_chain_is_accepted(self):
        parsed = app.parse_execution_plan(plan_json(goal="ORDER", steps=self.STEPS))
        self.assertEqual(len(parsed["steps"]), 2)

    def test_order_without_place_order_is_rejected(self):
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="ORDER", steps=self.STEPS[1:]))

    def test_order_cannot_end_with_arbitrary_tool(self):
        steps = self.STEPS + [{"id": "step_3", "tool": "list_products"}]
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="ORDER", steps=steps))


class StateGuardTest(unittest.TestCase):
    """Para harcayan adımın tek koruması modelin kurala uyması olmamalı."""

    PLAN = {"goal": "ORDER", "steps": [
        {"id": "step_1", "tool": "place_order",
         "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}}]}

    def test_place_order_requires_pending_draft(self):
        with self.assertRaises(ValueError) as ctx:
            app.validate_plan_against_state(self.PLAN, app.ConversationState())
        self.assertIn("taslak", str(ctx.exception).lower())

    def test_place_order_allowed_once_draft_exists(self):
        state = app.ConversationState()
        state.pending_draft_id = 12
        app.validate_plan_against_state(self.PLAN, state)  # hata beklenmiyor

    def test_guard_also_catches_place_order_in_non_order_goal(self):
        sneaky = {"goal": "DRAFT", "steps": [{"id": "step_1", "tool": "place_order", "arguments": {}}]}
        with self.assertRaises(ValueError):
            app.validate_plan_against_state(sneaky, app.ConversationState())

    def test_plans_without_order_steps_pass(self):
        app.validate_plan_against_state(
            {"goal": "PLAN", "steps": [{"id": "step_1", "tool": "list_low_stock"}]},
            app.ConversationState())


class GoalRuleTest(unittest.TestCase):
    def test_plan_goal_must_end_with_procurement_plan(self):
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="PLAN", steps=[
                {"id": "step_1", "tool": "list_low_stock"}]))

    def test_draft_goal_cannot_contain_place_order(self):
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="DRAFT", steps=[
                {"id": "step_1", "tool": "place_order"},
                {"id": "step_2", "tool": "create_purchase_draft"}]))

    def test_chat_goal_requires_answer_and_no_steps(self):
        parsed = app.parse_execution_plan(plan_json(goal="CHAT", answer="Merhaba"))
        self.assertEqual(parsed["answer"], "Merhaba")
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="CHAT"))

    def test_params_key_is_rejected_in_favour_of_arguments(self):
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="INFO", steps=[
                {"id": "step_1", "tool": "list_products", "params": {}}]))


if __name__ == "__main__":
    unittest.main()
