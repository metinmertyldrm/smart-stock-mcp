import unittest

from llm import prepare_inference_messages


FULL_SYSTEM = "Smart Stock & Procurement execution planner.\n" + ("x" * 12000)


class FastReadOnlyPlannerRoutingTest(unittest.TestCase):
    def test_out_of_stock_lookup_uses_compact_single_tool_prompt(self):
        prepared, tool = prepare_inference_messages([
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Stokta olmayan ürünleri listele."},
        ])

        self.assertEqual("list_out_of_stock", tool)
        self.assertEqual(2, len(prepared))
        self.assertLess(len(prepared[0]["content"]), 1200)
        self.assertIn('"goal":"INFO"', prepared[0]["content"])
        self.assertIn('"tool":"list_out_of_stock"', prepared[0]["content"])
        self.assertNotIn("create_purchase_draft", prepared[0]["content"])

    def test_low_stock_lookup_uses_low_stock_tool(self):
        prepared, tool = prepare_inference_messages([
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Kritik stoktaki ürünleri göster."},
        ])

        self.assertEqual("list_low_stock", tool)
        self.assertIn('"tool":"list_low_stock"', prepared[0]["content"])

    def test_procurement_request_keeps_full_planner(self):
        original = [
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Stokta olmayan ürünler için satın alma planı hazırla."},
        ]

        prepared, tool = prepare_inference_messages(original)

        self.assertIsNone(tool)
        self.assertIs(prepared, original)

    def test_quantity_reasoning_keeps_full_planner(self):
        original = [
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Kritik ürünlerden kaç tane sipariş etmeliyim?"},
        ]

        prepared, tool = prepare_inference_messages(original)

        self.assertIsNone(tool)
        self.assertIs(prepared, original)

    def test_non_planner_prompt_is_never_rewritten(self):
        original = [
            {"role": "system", "content": "Smart Stock karar açıklama katmanısın."},
            {"role": "user", "content": "Stokta olmayan ürünleri listele."},
        ]

        prepared, tool = prepare_inference_messages(original)

        self.assertIsNone(tool)
        self.assertIs(prepared, original)


if __name__ == "__main__":
    unittest.main()
