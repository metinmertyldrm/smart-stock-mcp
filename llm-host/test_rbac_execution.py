import unittest

from app import execute_plan
from identity import ROLE_MANAGER, ROLE_OPERATOR, ROLE_VIEWER
from rbac import reset_current_role, set_current_role


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments or {}))
        if tool_name == "list_out_of_stock":
            return '{"products": []}'
        if tool_name == "create_purchase_draft":
            return '{"id": 42, "items": []}'
        if tool_name == "place_order":
            return '{"id": 99, "items": []}'
        return '{}'


class PlanRbacPreflightTest(unittest.IsolatedAsyncioTestCase):
    async def test_viewer_write_plan_is_blocked_before_any_read_step_runs(self):
        client = FakeClient()
        plan = {
            "type": "execution_plan",
            "goal": "DRAFT",
            "steps": [
                {"id": "read-stock", "tool": "list_out_of_stock", "arguments": {}},
                {
                    "id": "create-draft",
                    "tool": "create_purchase_draft",
                    "arguments": {"items": [{"product_id": 10, "offer_id": 1, "quantity": 1}]},
                },
            ],
        }
        token = set_current_role(ROLE_VIEWER)
        try:
            result = await execute_plan(
                plan,
                client,
                {"list_out_of_stock", "create_purchase_draft"},
            )
        finally:
            reset_current_role(token)

        self.assertFalse(result["success"])
        self.assertTrue(result["authorization_denied"])
        self.assertTrue(result["preflight"])
        self.assertFalse(result["retryable"])
        self.assertEqual(result["failed_step"], "create-draft")
        self.assertEqual(result["failed_tool"], "create_purchase_draft")
        self.assertIn("VIEWER", result["business_reason"])
        self.assertEqual(client.calls, [])

    async def test_operator_can_execute_draft_plan(self):
        client = FakeClient()
        plan = {
            "type": "execution_plan",
            "goal": "DRAFT",
            "steps": [
                {
                    "id": "create-draft",
                    "tool": "create_purchase_draft",
                    "arguments": {"items": [{"product_id": 10, "offer_id": 1, "quantity": 1}]},
                }
            ],
        }
        token = set_current_role(ROLE_OPERATOR)
        try:
            result = await execute_plan(plan, client, {"create_purchase_draft"})
        finally:
            reset_current_role(token)

        self.assertTrue(result["success"])
        self.assertEqual([name for name, _ in client.calls], ["create_purchase_draft"])

    async def test_operator_order_plan_is_blocked_before_dispatch(self):
        client = FakeClient()
        plan = {
            "type": "execution_plan",
            "goal": "ORDER",
            "steps": [
                {"id": "place-order", "tool": "place_order", "arguments": {"draft_id": 42}}
            ],
        }
        token = set_current_role(ROLE_OPERATOR)
        try:
            result = await execute_plan(plan, client, {"place_order"})
        finally:
            reset_current_role(token)

        self.assertFalse(result["success"])
        self.assertEqual(result["failed_tool"], "place_order")
        self.assertIn("MANAGER", result["business_reason"])
        self.assertEqual(client.calls, [])

    async def test_manager_plan_reaches_dispatch_boundary(self):
        client = FakeClient()
        plan = {
            "type": "execution_plan",
            "goal": "ORDER",
            "steps": [
                {"id": "place-order", "tool": "place_order", "arguments": {"draft_id": 42}}
            ],
        }
        token = set_current_role(ROLE_MANAGER)
        try:
            result = await execute_plan(plan, client, {"place_order"})
        finally:
            reset_current_role(token)

        self.assertTrue(result["success"])
        self.assertEqual([name for name, _ in client.calls], ["place_order"])


if __name__ == "__main__":
    unittest.main()
