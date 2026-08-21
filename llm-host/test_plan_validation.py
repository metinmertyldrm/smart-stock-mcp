import unittest
from types import SimpleNamespace

import app
import plan_validation


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
            "steps": [
                {"id": "step_1", "tool": "place_order", "arguments": {"draft_id": 7}}
            ],
        }

        with self.assertRaisesRegex(ValueError, "Onay bekleyen bir taslak yok"):
            plan_validation.validate_plan_against_state(plan, SimpleNamespace(pending_draft_id=None))

        plan_validation.validate_plan_against_state(plan, SimpleNamespace(pending_draft_id=7))

    def test_receive_requires_pending_receive_ids(self):
        plan = {
            "type": "execution_plan",
            "goal": "RECEIVE",
            "steps": [
                {"id": "step_1", "tool": "receive_orders", "arguments": {"order_ids": [3]}}
            ],
        }

        with self.assertRaisesRegex(ValueError, "Onay bekleyen teslim alma yok"):
            plan_validation.validate_plan_against_state(plan, SimpleNamespace(pending_receive_ids=[]))

        plan_validation.validate_plan_against_state(plan, SimpleNamespace(pending_receive_ids=[3]))

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
