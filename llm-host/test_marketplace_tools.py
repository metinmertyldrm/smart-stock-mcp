"""Marketplace MCP tool'unun girdi doğrulaması ve tahsis gerekçeleri.

Bu testler `marketplace-mcp/tools.py` dosyasını doğrudan yükler; MCP SDK ve
`services` modülü kurulu olmasa da çalışır.
"""
import asyncio
import importlib.util
import json
import os
import sys
import types
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKETPLACE = os.path.join(BASE_DIR, "marketplace-mcp")


def load_marketplace_tools():
    """tools.py'yi izole biçimde yükler (gerçek servis çağrısı yapmadan)."""
    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        async def get_offers_by_product_id(self, product_id):
            return []

    stub = types.ModuleType("services")
    stub.MarketplaceService = FakeService
    previous = sys.modules.get("services")
    sys.modules["services"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "marketplace_tools_under_test", os.path.join(MARKETPLACE, "tools.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("services", None)
        else:
            sys.modules["services"] = previous


tools = load_marketplace_tools()


def call(name, arguments):
    result = asyncio.run(tools.handle_call_tool(name, arguments))
    return json.loads(result[0].text)


OFFER = {"offer_id": 1, "available_stock": 10, "unit_price": 100.0, "seller_name": "Satıcı",
         "rating": 4.2, "delivery_days": 7, "shipping_cost": 0.0, "topsis_score": 0.0}


class ItemValidationTest(unittest.TestCase):
    """Regresyon: bozuk items ham 'str object has no attribute get' hatası veriyordu."""

    def test_string_instead_of_list_is_explained(self):
        result = call("create_procurement_plan", {"items": "iPhone"})

        self.assertFalse(result["success"])
        self.assertIn("must be a list of objects", result["error"])
        self.assertIn("received str", result["error"])

    def test_list_of_strings_is_explained(self):
        result = call("create_procurement_plan", {"items": ["1", "2"]})

        self.assertFalse(result["success"])
        self.assertIn("must be an object", result["error"])
        self.assertNotIn("has no attribute", result["error"])

    def test_one_bad_entry_among_good_ones_is_caught(self):
        result = call("create_procurement_plan",
                      {"items": [{"product_id": 1, "quantity": 5}, "A4"]})

        self.assertFalse(result["success"])
        self.assertIn("A4", result["error"])

    def test_empty_items_still_guides_the_model(self):
        result = call("create_procurement_plan", {"items": []})

        self.assertFalse(result["success"])
        self.assertIn("No items provided", result["error"])


class FilterValidationTest(unittest.TestCase):
    """Regresyon: 'filters' string gelince ham 'str object has no attribute get' patlıyordu.

    Kabul koşumunda tam olarak filtre gerektiren iki senaryo bu hatayı verdi
    (min_rating ve max_delivery_days komutları).
    """

    def test_string_instead_of_object_is_explained(self):
        result = call("create_procurement_plan", {
            "items": [{"product_id": 1, "quantity": 5}],
            "filters": "min_rating=4.5"})

        self.assertFalse(result["success"])
        self.assertIn("'filters' must be an object", result["error"])
        self.assertIn("received str", result["error"])
        self.assertNotIn("has no attribute", result["error"])

    def test_numeric_value_written_as_text_is_accepted(self):
        """Model sayıyı tırnak içinde yazdığında isteği reddetmek gereksiz katılık."""
        normalized, error = tools.normalize_filters({"min_rating": "4.5"})

        self.assertIsNone(error)
        self.assertEqual(normalized["min_rating"], 4.5)

    def test_comma_decimal_is_accepted(self):
        normalized, error = tools.normalize_filters({"max_total_budget": "50000,50"})

        self.assertIsNone(error)
        self.assertEqual(normalized["max_total_budget"], 50000.5)

    def test_unparseable_number_names_the_field(self):
        normalized, error = tools.normalize_filters({"max_delivery_days": "üç gün"})

        self.assertIsNone(normalized)
        self.assertIn("filters.max_delivery_days", error)
        self.assertIn("must be a number", error)

    def test_missing_filters_is_not_an_error(self):
        self.assertEqual(tools.normalize_filters(None), ({}, None))

    def test_unknown_keys_are_left_alone(self):
        """Bilinmeyen anahtarı reddetmek modeli gereksiz onarım turuna sokar."""
        normalized, error = tools.normalize_filters({"category": "Elektronik"})

        self.assertIsNone(error)
        self.assertEqual(normalized["category"], "Elektronik")

    def test_boolean_is_not_silently_read_as_a_number(self):
        """True/1 karışması sessiz yanlış filtre üretir."""
        normalized, error = tools.normalize_filters({"min_rating": True})

        self.assertIsNone(normalized)
        self.assertIn("filters.min_rating", error)


class AllocationReasonTest(unittest.TestCase):
    """Boş tahsis 'uygun teklif yok' deyip susuyordu; artık nedenini söylüyor."""

    def allocate(self, offers, filters=None):
        return tools.allocate_across_offers(offers, 5, "CHEAPEST", filters or {})

    def test_no_offers_at_all(self):
        result = self.allocate([])
        self.assertFalse(result["success"])
        self.assertIn("hic teklif yok", result["reason"])

    def test_offers_exist_but_out_of_stock(self):
        result = self.allocate([dict(OFFER, available_stock=0)])
        self.assertFalse(result["success"])
        self.assertIn("stok kalmamis", result["reason"])

    def test_filters_excluded_everything_names_the_filter(self):
        result = self.allocate([OFFER], {"max_delivery_days": 3})
        self.assertFalse(result["success"])
        self.assertIn("filtrelere takildi", result["reason"])
        self.assertIn("max_delivery_days=3", result["reason"])

    def test_successful_allocation_has_no_reason(self):
        result = self.allocate([OFFER])
        self.assertTrue(result["success"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["fulfilled_quantity"], 5)


if __name__ == "__main__":
    unittest.main()


class TotalBudgetTest(unittest.TestCase):
    """Taslak komut 3: toplam bütçe tavanı. Bütçe hiçbir koşulda aşılmamalı."""

    CHEAP = dict(OFFER, offer_id=9, unit_price=100.0, available_stock=100)
    PRICEY = dict(OFFER, offer_id=8, unit_price=50000.0, available_stock=10)

    def test_allocation_never_exceeds_budget(self):
        result = tools.allocate_across_offers([self.PRICEY], 5, "CHEAPEST", {}, budget=120000.0)

        self.assertLessEqual(result["overall_total"], 120000.0)
        self.assertEqual(result["fulfilled_quantity"], 2)
        self.assertIn("Butce siniri", result["reason"])

    def test_shipping_is_paid_from_the_budget(self):
        offer = dict(self.CHEAP, shipping_cost=50.0)

        result = tools.allocate_across_offers([offer], 10, "CHEAPEST", {}, budget=250.0)

        self.assertLessEqual(result["overall_total"], 250.0)
        self.assertEqual(result["fulfilled_quantity"], 2)   # 50 kargo + 2x100

    def test_no_budget_means_no_limit(self):
        result = tools.allocate_across_offers([self.PRICEY], 5, "CHEAPEST", {})

        self.assertEqual(result["fulfilled_quantity"], 5)
        self.assertIsNone(result["reason"])

    def test_budget_is_shared_across_the_whole_plan(self):
        """Bütçe plan seviyesinde; ilk kalem tüketirse sonrakiler alamaz."""
        result = call("create_procurement_plan", {
            "items": [{"product_id": 1, "quantity": 1}, {"product_id": 2, "quantity": 1}],
            "filters": {"max_total_budget": 1000},
        })

        self.assertTrue(result["success"])
        self.assertEqual(result["budget_limit"], 1000)
        self.assertLessEqual(result["budget_used"], 1000)

    def test_budget_fields_absent_when_not_requested(self):
        result = call("create_procurement_plan", {"items": [{"product_id": 1, "quantity": 1}]})

        self.assertNotIn("budget_limit", result)

    def test_budget_is_not_reported_as_an_offer_filter(self):
        """max_total_budget bir teklif filtresi değil; 'filtrelere takıldı' metninde geçmemeli."""
        result = tools.allocate_across_offers(
            [dict(OFFER, delivery_days=9)], 5, "CHEAPEST",
            {"max_delivery_days": 3, "max_total_budget": 99999})

        self.assertIn("max_delivery_days=3", result["reason"])
        self.assertNotIn("max_total_budget", result["reason"])
