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

    def test_prompt_budget_stays_bounded_and_safety_rules_remain(self):
        tools = [
            SimpleNamespace(
                name=f"tool_{index}",
                description="x" * 1000,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                },
            )
            for index in range(20)
        ]

        prompt = get_execution_plan_prompt(tools)

        self.assertLess(len(prompt), 16000)
        self.assertNotIn("x" * 200, prompt)
        self.assertIn("INFO and REASON are read-only", prompt)
        self.assertIn("PENDING_DRAFT_ID", prompt)
        self.assertIn("PENDING_RECEIVE_IDS", prompt)
        self.assertIn("create_incoming_orders", prompt)
        self.assertIn("Never emit `final_response`", prompt)


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


class OfferComparisonRuleTest(unittest.TestCase):
    """27.08 ölçümü: plan karşılaştırma zincirine gereksiz compare_offers ekleniyordu.

    Model iki create_procurement_plan (CHEAPEST + FASTEST) ürettikten sonra
    dördüncü adım olarak `compare_offers(product_id=[2,5], quantity=[], ...)`
    çağırdı; araç tek ürün için tasarlandığından şema reddetti
    ("[] is not of type 'integer'") ve senaryo düştü. Host tarafında
    düzeltilemez: doğru davranış zinciri dördüncü adıma hiç götürmemek.
    """

    TOOL = SimpleNamespace(
        name="compare_offers",
        description="Compare marketplace offers for exactly one product.",
        inputSchema={"type": "object", "properties": {}},
    )

    def prompt(self):
        return get_execution_plan_prompt([self.TOOL])

    def test_comparison_chain_stops_after_the_two_plans(self):
        prompt = self.prompt()

        self.assertIn("Never append compare_offers to this chain", prompt)
        self.assertIn("the host computes the cost/delivery", prompt)

    def test_compare_offers_is_documented_as_single_product(self):
        prompt = self.prompt()

        self.assertIn("EXACTLY ONE product", prompt)
        self.assertIn("Never pass arrays", prompt)

    def test_empty_filter_arrays_are_forbidden(self):
        """Model kullanmadığı filtreleri `[]` ile dolduruyordu."""
        self.assertIn("never pass an empty array for a filter you do not need",
                      self.prompt())


if __name__ == "__main__":
    unittest.main()
