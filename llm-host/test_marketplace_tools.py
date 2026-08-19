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
