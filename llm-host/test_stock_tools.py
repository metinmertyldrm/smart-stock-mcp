"""Stock MCP tool'unun teslim alma sözleşmesi ve tool listesi bütünlüğü.

`stock-mcp/tools.py` doğrudan yüklenir; gerçek backend çağrısı yapılmaz.
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
STOCK = os.path.join(BASE_DIR, "stock-mcp")
MARKETPLACE = os.path.join(BASE_DIR, "marketplace-mcp")


class FakeOrder:
    """IncomingOrder'ın model_dump() sözleşmesini taklit eder."""

    def __init__(self, order_id, quantity=5, product_name="iPhone"):
        self.payload = {"id": order_id, "quantity": quantity,
                        "product": {"id": order_id, "name": product_name},
                        "status": "RECEIVED"}

    def model_dump(self):
        return dict(self.payload)


class FakeProductService:
    """receive_orders (teslim alınanlar, alınamayanlar) demeti döndürür."""

    received_ids = [1, 2]
    failed_entries = []

    def __init__(self, *args, **kwargs):
        pass

    async def receive_orders(self, order_ids):
        return ([FakeOrder(i) for i in self.received_ids], list(self.failed_entries))


def load_stock_tools():
    stub = types.ModuleType("services")
    stub.ProductService = FakeProductService
    previous = sys.modules.get("services")
    sys.modules["services"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "stock_tools_under_test", os.path.join(STOCK, "tools.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("services", None)
        else:
            sys.modules["services"] = previous


tools = load_stock_tools()


def call(name, arguments):
    result = asyncio.run(tools.handle_call_tool(name, arguments))
    return json.loads(result[0].text)


class ReceiveOrdersContractTest(unittest.TestCase):
    """Regresyon: servis demet döndürürken çağıran düz liste bekliyordu.

    Sonuç: `'list' object has no attribute 'model_dump'` — teslim alma aracı
    kabul koşumunda hiç çalışmadı.
    """

    def setUp(self):
        FakeProductService.received_ids = [1, 2]
        FakeProductService.failed_entries = []

    def test_tuple_result_is_unpacked(self):
        result = call("receive_orders", {"order_ids": [1, 2]})

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual([o["id"] for o in result["orders"]], [1, 2])
        self.assertEqual(result["failed"], [])

    def test_count_reports_orders_not_the_tuple_length(self):
        """Regresyon: len(demet) her zaman 2 idi; üç sipariş '2' görünüyordu."""
        FakeProductService.received_ids = [1, 2, 3]

        result = call("receive_orders", {"order_ids": [1, 2, 3]})

        self.assertEqual(result["count"], 3)

    def test_partial_failure_reports_both_sides(self):
        """Bir teslimatın reddedilmesi diğerlerini gizlememeli."""
        FakeProductService.received_ids = [1]
        FakeProductService.failed_entries = [
            {"order_id": 2, "status": 409, "error": "Teslimat tarihi gelmedi."}]

        result = call("receive_orders", {"order_ids": [1, 2]})

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["failed"][0]["order_id"], 2)

    def test_all_failed_is_not_reported_as_success(self):
        FakeProductService.received_ids = []
        FakeProductService.failed_entries = [
            {"order_id": 2, "error": "Teslimat tarihi gelmedi."}]

        result = call("receive_orders", {"order_ids": [2]})

        self.assertFalse(result["success"])
        self.assertIn("teslim alinamadi", result["error"].lower())

    def test_empty_ids_still_guides_the_model(self):
        result = call("receive_orders", {"order_ids": []})

        self.assertFalse(result["success"])
        self.assertIn("list_incoming_orders", result["error"])

    def test_non_list_ids_are_explained(self):
        result = call("receive_orders", {"order_ids": 5})

        self.assertFalse(result["success"])
        self.assertIn("must be a list", result["error"])
        self.assertNotIn("has no attribute", result["error"])


class ToolCatalogIntegrityTest(unittest.TestCase):
    """Aynı ad iki kez bildirilirse model çelişkili iki tarif görür.

    Regresyon: `receive_orders` iki kez bildirilmişti; prompt yer kaybediyor,
    hangi açıklamanın geçerli olduğu belirsizleşiyordu.
    """

    def tool_names(self, module):
        return [tool.name for tool in asyncio.run(module.list_tools())]

    def test_stock_server_declares_each_tool_once(self):
        names = self.tool_names(tools)
        duplicates = {n for n in names if names.count(n) > 1}
        self.assertEqual(duplicates, set(), f"tekrarlanan tool: {duplicates}")

    def test_marketplace_server_declares_each_tool_once(self):
        from test_marketplace_tools import tools as marketplace
        names = self.tool_names(marketplace)
        duplicates = {n for n in names if names.count(n) > 1}
        self.assertEqual(duplicates, set(), f"tekrarlanan tool: {duplicates}")

    def test_receive_orders_description_keeps_the_confirmation_hint(self):
        """Onay kapısı prompt'ta da anlatılmalı; host koruması tek başına yeterli değil."""
        declaration = next(t for t in asyncio.run(tools.list_tools())
                           if t.name == "receive_orders")

        self.assertIn("CONFIRMED", declaration.description)
        self.assertIn("list_incoming_orders", declaration.description)


if __name__ == "__main__":
    unittest.main()


class CategoryFilterTest(unittest.TestCase):
    """Regresyon: `list_low_stock(category=...)` şemaya takılıyordu.

    Taslak örnek komut 2 ("Elektronik kategorisindeki kritik ürünler") doğal
    olarak bu argümanı üretiyor. Modele bu aracı kullanmamayı öğretmek yerine
    aracın eksik olan yeteneğini tamamladık.

    İkinci kural: eşleşmeyen kategori sessizce boş liste döndürmemeli. Boş liste,
    o kategoride kritik ürün olmamasından ayırt edilemez.
    """

    class FakeCategory:
        def __init__(self, name):
            self.name = name

    class FakeSubcategory:
        def __init__(self, category):
            self.category = category

    class FakeProduct:
        def __init__(self, product_id, category_name):
            self.subcategory = CategoryFilterTest.FakeSubcategory(
                CategoryFilterTest.FakeCategory(category_name)) if category_name else None
            self.payload = {"id": product_id, "name": f"Urun {product_id}"}

        def model_dump(self):
            return dict(self.payload)

    def products(self):
        return [self.FakeProduct(1, "Elektronik"),
                self.FakeProduct(2, "Kırtasiye"),
                self.FakeProduct(3, "Elektronik")]

    def test_category_is_declared_on_both_listing_tools(self):
        catalog = {t.name: t for t in asyncio.run(tools.list_tools())}
        for name in ("list_low_stock", "list_out_of_stock"):
            with self.subTest(tool=name):
                self.assertIn("category", catalog[name].inputSchema["properties"])

    def test_filter_is_case_and_space_insensitive(self):
        filtered, error = tools.filter_by_category(
            self.products(), "  elektronik ", tools.product_category_name)

        self.assertIsNone(error)
        self.assertEqual([p.payload["id"] for p in filtered], [1, 3])

    def test_missing_category_returns_everything(self):
        filtered, error = tools.filter_by_category(
            self.products(), None, tools.product_category_name)

        self.assertIsNone(error)
        self.assertEqual(len(filtered), 3)

    def test_unknown_category_explains_instead_of_returning_empty(self):
        filtered, error = tools.filter_by_category(
            self.products(), "Mobilya", tools.product_category_name)

        self.assertEqual(filtered, [])
        self.assertIn("Mobilya", error)
        self.assertIn("Elektronik", error)
        self.assertIn("Kırtasiye", error)

    def test_empty_source_does_not_invent_a_category_error(self):
        """Hiç ürün yoksa sorun kategoride değil; boş liste doğru cevaptır."""
        filtered, error = tools.filter_by_category([], "Elektronik", tools.product_category_name)

        self.assertEqual(filtered, [])
        self.assertIsNone(error)

    def test_product_without_category_is_tolerated(self):
        items = self.products() + [self.FakeProduct(4, None)]

        filtered, error = tools.filter_by_category(
            items, "Elektronik", tools.product_category_name)

        self.assertIsNone(error)
        self.assertEqual([p.payload["id"] for p in filtered], [1, 3])
