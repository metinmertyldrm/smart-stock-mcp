"""Plan doğrulama: şema kuralları ve mevcut duruma karşı kontroller."""
import json
import unittest
from types import SimpleNamespace

from test_support import install_optional_stubs

install_optional_stubs()

import app  # noqa: E402
import plan_validation  # noqa: E402


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
        app.validate_plan_against_state(self.PLAN, state)

    def test_guard_also_catches_place_order_in_non_order_goal(self):
        sneaky = {"goal": "DRAFT", "steps": [
            {"id": "step_1", "tool": "place_order", "arguments": {}}]}
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

    def test_draft_cannot_use_model_literal_offer_ids(self):
        with self.assertRaisesRegex(ValueError, "doğrudan yazılamaz"):
            app.parse_execution_plan(plan_json(goal="DRAFT", steps=[{
                "id": "step_1",
                "tool": "create_purchase_draft",
                "arguments": {
                    "items": [{"product_id": 418, "offer_id": 1, "quantity": 1}],
                },
            }]))

    def test_draft_accepts_offer_items_from_procurement_plan(self):
        parsed = app.parse_execution_plan(plan_json(goal="DRAFT", steps=[
            {
                "id": "step_1",
                "tool": "create_procurement_plan",
                "arguments": {
                    "items": [{"product_id": 418, "quantity": 1}],
                    "objective": "CHEAPEST",
                },
            },
            {
                "id": "step_2",
                "tool": "create_purchase_draft",
                "arguments": {
                    "items": {
                        "$from": "step_1",
                        "$transform": "plan_to_draft_items",
                    },
                },
            },
        ]))

        self.assertEqual(parsed["goal"], "DRAFT")

    def test_draft_accepts_selected_offer_from_comparison(self):
        parsed = app.parse_execution_plan(plan_json(goal="DRAFT", steps=[
            {
                "id": "step_1",
                "tool": "compare_offers",
                "arguments": {"product_id": 418, "quantity": 1},
            },
            {
                "id": "step_2",
                "tool": "create_purchase_draft",
                "arguments": {
                    "items": {
                        "$from": "step_1",
                        "$transform": "plan_to_draft_items",
                    },
                },
            },
        ]))

        self.assertEqual(parsed["steps"][0]["tool"], "compare_offers")

    def test_chat_goal_requires_answer_and_no_steps(self):
        parsed = app.parse_execution_plan(plan_json(goal="CHAT", answer="Merhaba"))
        self.assertEqual(parsed["answer"], "Merhaba")
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="CHAT"))

    def test_params_key_is_rejected_in_favour_of_arguments(self):
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="INFO", steps=[
                {"id": "step_1", "tool": "list_products", "params": {}}]))


class ReceiveGoalTest(unittest.TestCase):
    """Teslim alma hem okuma hem yazma içerir; INFO salt okunur olduğu için
    bu akışın kendi hedefi var."""

    STEPS = [
        {"id": "step_1", "tool": "list_incoming_orders", "arguments": {"pending_only": True}},
        {"id": "step_2", "tool": "receive_order",
         "arguments": {"order_id": {"$from": "step_1.orders.0.id"}}},
    ]

    def test_list_then_receive_is_accepted(self):
        parsed = app.parse_execution_plan(plan_json(goal="RECEIVE", steps=self.STEPS))
        self.assertEqual(len(parsed["steps"]), 2)

    def test_listing_only_is_the_proposal_phase(self):
        """İlk turda yalnızca listeleme yapılır; host onay ister, stok değişmez."""
        parsed = app.parse_execution_plan(plan_json(goal="RECEIVE", steps=self.STEPS[:1]))
        self.assertEqual([s["tool"] for s in parsed["steps"]], ["list_incoming_orders"])

    def test_receiving_requires_a_confirmed_pending_list(self):
        plan = {"goal": "RECEIVE", "steps": [
            {"id": "step_1", "tool": "receive_orders",
             "arguments": {"order_ids": {"$from_context": "pending_receive_ids"}}}]}

        with self.assertRaises(ValueError) as ctx:
            app.validate_plan_against_state(plan, app.ConversationState())
        self.assertIn("teslim alma", str(ctx.exception).lower())

        state = app.ConversationState()
        state.pending_receive_ids = [2, 3]
        app.validate_plan_against_state(plan, state)

    def test_receive_goal_cannot_smuggle_other_writes(self):
        steps = self.STEPS + [{"id": "step_3", "tool": "place_order", "arguments": {}}]
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="RECEIVE", steps=steps))

    def test_listing_alone_is_allowed_as_info(self):
        parsed = app.parse_execution_plan(plan_json(goal="INFO", steps=self.STEPS[:1]))
        self.assertEqual(parsed["steps"][0]["tool"], "list_incoming_orders")

    def test_info_still_rejects_receive_order(self):
        with self.assertRaises(ValueError):
            app.parse_execution_plan(plan_json(goal="INFO", steps=self.STEPS))


class PlanValidationExtractionTest(unittest.TestCase):
    def test_app_reexports_extracted_validation_api(self):
        self.assertIs(app.parse_execution_plan, plan_validation.parse_execution_plan)
        self.assertIs(app.validate_plan_against_state, plan_validation.validate_plan_against_state)
        self.assertIs(app.remove_json_comments, plan_validation.remove_json_comments)
        self.assertIs(app.ALLOWED_CONTEXT_SOURCES, plan_validation.ALLOWED_CONTEXT_SOURCES)

    def test_info_plan_rejects_write_tool(self):
        raw = (
            '{"type":"execution_plan","goal":"INFO","steps":['
            '{"id":"step_1","tool":"create_purchase_draft","arguments":{"items":[]}}]}'
        )
        with self.assertRaisesRegex(ValueError, "salt okunur"):
            plan_validation.parse_execution_plan(raw)

    def test_order_requires_pending_draft(self):
        plan = {
            "type": "execution_plan",
            "goal": "ORDER",
            "steps": [{"id": "step_1", "tool": "place_order", "arguments": {"draft_id": 7}}],
        }
        with self.assertRaisesRegex(ValueError, "Onay bekleyen bir taslak yok"):
            plan_validation.validate_plan_against_state(
                plan, SimpleNamespace(pending_draft_id=None))
        plan_validation.validate_plan_against_state(
            plan, SimpleNamespace(pending_draft_id=7))

    def test_receive_requires_pending_receive_ids(self):
        plan = {
            "type": "execution_plan",
            "goal": "RECEIVE",
            "steps": [{"id": "step_1", "tool": "receive_orders", "arguments": {"order_ids": [3]}}],
        }
        with self.assertRaisesRegex(ValueError, "Onay bekleyen teslim alma yok"):
            plan_validation.validate_plan_against_state(
                plan, SimpleNamespace(pending_receive_ids=[]))
        plan_validation.validate_plan_against_state(
            plan, SimpleNamespace(pending_receive_ids=[3]))

    def test_comment_tolerant_parser_behavior_is_preserved(self):
        raw = '''```json
        {
          // planner comment
          "type": "execution_plan",
          "goal": "INFO",
          "steps": [{"id":"step_1","tool":"list_out_of_stock","arguments":{}}]
        }
        ```'''
        parsed = plan_validation.parse_execution_plan(raw)
        self.assertEqual(parsed["goal"], "INFO")
        self.assertEqual(parsed["steps"][0]["tool"], "list_out_of_stock")


if __name__ == "__main__":
    unittest.main()
