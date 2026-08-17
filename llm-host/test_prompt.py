import unittest
from types import SimpleNamespace

from prompt import get_execution_plan_prompt


class ExecutionPlanPromptTest(unittest.TestCase):
    def test_draft_request_keeps_draft_goal_after_procurement_step(self):
        tool = SimpleNamespace(
            name="create_purchase_draft",
            description="Create a purchase draft",
            inputSchema={"type": "object", "properties": {}},
        )

        prompt = get_execution_plan_prompt([tool])

        self.assertIn(
            '"Stokta azalan ürünleri bul ve en ucuz tekliften taslak sipariş oluştur" '
            '-> goal: DRAFT',
            prompt,
        )
        self.assertIn(
            'A preceding create_procurement_plan step does not make the goal PLAN '
            'when draft creation is requested.',
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
