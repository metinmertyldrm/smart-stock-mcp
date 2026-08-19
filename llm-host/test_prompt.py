import unittest
from types import SimpleNamespace

from prompt import get_decision_journal_prompt, get_execution_plan_prompt


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


class DecisionJournalPromptTest(unittest.TestCase):
    def test_prompt_targets_non_technical_readers_and_forbids_raw_json(self):
        prompt = get_decision_journal_prompt(
            "Siparişi onaylıyorum",
            {"goal": "ORDER", "steps": [{"id": "1", "tool": "place_order"}]},
            [{"stepId": "1", "result": {"id": 4, "totalCost": 305930.0}}],
            "Sipariş oluşturuldu.",
            "FULL",
        )

        self.assertIn("LLM, MCP, API, JSON", prompt)
        self.assertIn('"whatItDoes"', prompt)
        self.assertIn('"whyUsed"', prompt)
        self.assertIn("Ham JSON", prompt)
        self.assertIn("neden bu", prompt)
        self.assertIn("305.930,00 TL", prompt)
        self.assertIn("Siparişi onaylıyorum", prompt)


if __name__ == "__main__":
    unittest.main()
