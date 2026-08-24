import json
import unittest
from unittest.mock import patch

from llm import LLMService, SAFE_DRAFT_ROUTE, prepare_inference_messages


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

    def test_zero_stock_quantity_phrase_with_no_order_guard_uses_fast_route(self):
        prepared, tool = prepare_inference_messages([
            {"role": "system", "content": FULL_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Stok miktarı sıfır olan ürünleri listele. Her ürün için ürün adı, "
                    "SKU, minimum stok ve hedef stok bilgisini göster. Henüz sipariş oluşturma."
                ),
            },
        ])

        self.assertEqual("list_out_of_stock", tool)
        self.assertIn('"tool":"list_out_of_stock"', prepared[0]["content"])

    def test_read_only_offer_comparison_skips_planner_llm(self):
        service = LLMService()
        messages = [
            {"role": "system", "content": FULL_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Galaxy S24 256GB için mevcut marketplace tekliflerini karşılaştır. "
                    "Her teklif için fiyat ve teslimatı göster. "
                    "Henüz taslak veya sipariş oluşturma."
                ),
            },
        ]

        with patch("llm.requests.post") as post:
            raw = service.generate(messages)

        post.assert_not_called()
        plan = json.loads(raw)
        self.assertEqual("REASON", plan["goal"])
        self.assertEqual(
            [{
                "id": "step_1",
                "tool": "search_offers",
                "arguments": {"query": "Galaxy S24 256GB"},
            }],
            plan["steps"],
        )

    def test_low_stock_lookup_uses_low_stock_tool(self):
        prepared, tool = prepare_inference_messages([
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Kritik stoktaki ürünleri göster."},
        ])

        self.assertEqual("list_low_stock", tool)
        self.assertIn('"tool":"list_low_stock"', prepared[0]["content"])

    def test_deterministic_fast_route_skips_ollama(self):
        service = LLMService()
        messages = [
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Stokta olmayan ürünleri listele."},
        ]

        with patch("llm.requests.post") as post:
            raw = service.generate(messages)

        post.assert_not_called()
        plan = json.loads(raw)
        self.assertEqual("execution_plan", plan["type"])
        self.assertEqual("INFO", plan["goal"])
        self.assertEqual(
            [{"id": "step_1", "tool": "list_out_of_stock", "arguments": {}}],
            plan["steps"],
        )

    def test_simple_replenishment_order_request_routes_to_safe_draft(self):
        service = LLMService()
        messages = [
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Eksik stoklar için satın alma siparişi oluştur."},
        ]

        prepared, route = prepare_inference_messages(messages)
        self.assertEqual(SAFE_DRAFT_ROUTE, route)
        self.assertEqual(2, len(prepared))

        with patch("llm.requests.post") as post:
            raw = service.generate(messages)

        post.assert_not_called()
        plan = json.loads(raw)
        self.assertEqual("DRAFT", plan["goal"])
        tools = [step["tool"] for step in plan["steps"]]
        self.assertEqual(
            ["calculate_replenishment", "create_procurement_plan", "create_purchase_draft"],
            tools,
        )
        self.assertNotIn("place_order", tools)
        self.assertEqual(
            {"$from": "step_1.replenishments", "$transform": "replenishments_to_items"},
            plan["steps"][1]["arguments"]["items"],
        )

    def test_advanced_replenishment_request_keeps_full_planner(self):
        original = [
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "Eksik stoklar için 50.000 TL bütçeyle sipariş oluştur."},
        ]

        prepared, route = prepare_inference_messages(original)

        self.assertIsNone(route)
        self.assertIs(prepared, original)

    def test_confirmation_never_uses_safe_draft_route(self):
        original = [
            {"role": "system", "content": FULL_SYSTEM},
            {"role": "user", "content": "42 numaralı taslağı onayla ve siparişi oluştur."},
        ]

        prepared, route = prepare_inference_messages(original)

        self.assertIsNone(route)
        self.assertIs(prepared, original)

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
