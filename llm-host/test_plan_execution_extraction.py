"""Regression coverage for the extracted plan-execution module."""
import asyncio
import unittest

from test_support import FakeMCPClient, install_optional_stubs

install_optional_stubs()

import agent_runtime  # noqa: E402
import app  # noqa: E402
import plan_execution  # noqa: E402


class PlanExecutionExtractionTest(unittest.TestCase):
    def test_app_reexports_execution_api(self):
        self.assertIs(app.execute_plan, plan_execution.execute_plan)
        self.assertIs(app.resolve_step_arguments, plan_execution.resolve_step_arguments)
        self.assertIs(app.resolve_argument_value, plan_execution.resolve_argument_value)
        self.assertIs(app.normalize_tool_result, plan_execution.normalize_tool_result)
        self.assertIs(app.save_reference, plan_execution.save_reference)
        self.assertIs(app.order_to_incoming_items, plan_execution.order_to_incoming_items)

    def test_legacy_runtime_globals_are_rebound(self):
        self.assertIs(agent_runtime.execute_plan, plan_execution.execute_plan)
        self.assertIs(agent_runtime.resolve_step_arguments, plan_execution.resolve_step_arguments)
        self.assertIs(agent_runtime.resolve_argument_value, plan_execution.resolve_argument_value)
        self.assertIs(agent_runtime.normalize_tool_result, plan_execution.normalize_tool_result)

    def test_read_only_tool_executes_through_extracted_executor(self):
        client = FakeMCPClient({
            "list_out_of_stock": {
                "success": True,
                "count": 1,
                "products": [{"id": 2, "name": "Galaxy S24", "stockQuantity": 0}],
            }
        })
        state = app.ConversationState()
        plan = {
            "type": "execution_plan",
            "goal": "INFO",
            "steps": [{"id": "step_1", "tool": "list_out_of_stock", "arguments": {}}],
        }

        result = asyncio.run(
            plan_execution.execute_plan(plan, client, {"list_out_of_stock"}, state)
        )

        self.assertTrue(result["success"])
        self.assertEqual(client.called_tools, ["list_out_of_stock"])
        self.assertEqual(result["last_result"]["count"], 1)
        self.assertIsNotNone(state.last_reference_id)

    def test_empty_collection_is_business_failure_without_tool_retry(self):
        client = FakeMCPClient({
            "list_low_stock": {"success": True, "products": []},
            "create_procurement_plan": {"success": True, "items": []},
        })
        plan = {
            "type": "execution_plan",
            "goal": "PLAN",
            "steps": [
                {"id": "step_1", "tool": "list_low_stock", "arguments": {}},
                {
                    "id": "step_2",
                    "tool": "create_procurement_plan",
                    "arguments": {
                        "items": {
                            "$from": "step_1.products",
                            "$transform": "low_stock_products_to_items",
                        },
                        "objective": "CHEAPEST",
                    },
                },
            ],
        }

        result = asyncio.run(
            plan_execution.execute_plan(
                plan,
                client,
                {"list_low_stock", "create_procurement_plan"},
                app.ConversationState(),
            )
        )

        self.assertFalse(result["success"])
        self.assertFalse(result["retryable"])
        self.assertEqual(result["failed_tool"], "create_procurement_plan")
        self.assertEqual(client.called_tools, ["list_low_stock"])


if __name__ == "__main__":
    unittest.main()
