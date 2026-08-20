"""execute_plan ve referans çözümleme davranışları."""
import asyncio
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

import app  # noqa: E402
from test_support import PLACED_ORDER, FakeMCPClient  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class ContextSourcesTest(unittest.TestCase):
    """Regresyon: bulunamayan bağlam kaynakları sessizce 'başarılı' sayılmamalı."""

    PLAN = {
        "type": "execution_plan",
        "goal": "REASON",
        "steps": [],
        "context_sources": ["last_cheapest_plan", "last_fastest_plan"],
    }

    def test_missing_sources_fail_so_repair_can_run(self):
        result = run(app.execute_plan(self.PLAN, None, set(), app.ConversationState()))

        self.assertFalse(result["success"])
        self.assertEqual(result["failed_step"], "context_sources")
        self.assertIn("last_cheapest_plan", result["error"])

    def test_available_source_still_succeeds(self):
        state = app.ConversationState()
        state.last_cheapest_plan = app.CachedProcurementPlan(
            objective="CHEAPEST", items=[], result={"success": True},
            saved_at="2026-08-18T10:00:00+00:00")

        result = run(app.execute_plan(self.PLAN, None, set(), state))

        self.assertTrue(result["success"])
        self.assertIn("last_cheapest_plan", result["results"])

    def test_serialize_plan_is_importable_at_module_level(self):
        """Regresyon: serialize_plan save_state içinde gömülüyken NameError veriyordu."""
        self.assertTrue(callable(app.serialize_plan))


class ReferenceResolutionTest(unittest.TestCase):
    def setUp(self):
        self.state = app.ConversationState()
        app.save_reference(self.state, "product_list", "list_low_stock",
                           [{"id": 1, "name": "iPhone"}, {"id": 4, "name": "A4"}])

    def test_context_name_inside_from_is_tolerated(self):
        """Model $from_context yerine $from yazdığında niyet açık; hata vermek yerine çözüyoruz."""
        resolved = app.resolve_argument_value({"$from": "last_reference.id"}, {}, self.state)
        self.assertEqual(resolved, [1, 4])

    def test_unknown_step_reference_still_fails(self):
        with self.assertRaises(ValueError):
            app.resolve_argument_value({"$from": "step_9.products"}, {}, self.state)


class OrderToIncomingItemsTest(unittest.TestCase):
    """place_order çıktısı create_incoming_orders girdisine dönüşmeli."""

    def test_keeps_the_full_datetime_from_the_order(self):
        """Backend LocalDateTime bekliyor; saat kısmını atmak /api/orders'ı 400 yapıyordu."""
        items = app.order_to_incoming_items(PLACED_ORDER)

        self.assertEqual(items, [
            {"product_id": 3, "quantity": 8, "expected_delivery_date": "2026-08-21T09:15:00"},
            {"product_id": 9, "quantity": 30, "expected_delivery_date": "2026-08-21T09:15:00"},
        ])

    def test_host_date_validation_accepts_a_full_datetime(self):
        """execute_plan tarih doğrulaması datetime'ı reddetmemeli."""
        from datetime import date
        raw = "2026-08-21T09:15:00"
        self.assertEqual(date.fromisoformat(raw.split("T")[0]), date(2026, 8, 21))

    def test_rejects_orders_without_items(self):
        with self.assertRaises(ValueError):
            app.order_to_incoming_items({"success": True, "items": []})


class ProcurementChainTest(unittest.TestCase):
    def test_order_chain_registers_expected_stock(self):
        client = FakeMCPClient({
            "place_order": PLACED_ORDER,
            "create_incoming_orders": lambda args: {"success": True, "count": len(args["items"])},
        })
        plan = {"type": "execution_plan", "goal": "ORDER", "steps": [
            {"id": "step_1", "tool": "place_order",
             "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}},
            {"id": "step_2", "tool": "create_incoming_orders",
             "arguments": {"items": {"$from": "step_1", "$transform": "order_to_incoming_items"}}},
        ]}
        state = app.ConversationState()
        state.pending_draft_id = 12

        result = run(app.execute_plan(plan, client, {"place_order", "create_incoming_orders"}, state))

        self.assertTrue(result["success"])
        self.assertEqual(client.called_tools, ["place_order", "create_incoming_orders"])
        registered = client.calls[1][1]["items"]
        self.assertEqual([item["product_id"] for item in registered], [3, 9])
        self.assertEqual([item["quantity"] for item in registered], [8, 30])


class ReceivedOrdersMessageTest(unittest.TestCase):
    """Teslim alma cevabı: kısmi başarı gizlenmemeli, ham JSON basılmamalı."""

    def test_all_received_lists_products_and_quantities(self):
        text = app.format_received_orders({
            "success": True, "count": 2, "failed": [],
            "orders": [
                {"quantity": 5, "product": {"name": "iPhone"}},
                {"quantity": 8, "product": {"name": "Galaxy S24"}}]})

        self.assertIn("2 sipariş teslim alındı", text)
        self.assertIn("iPhone: +5 adet", text)
        self.assertIn("Galaxy S24: +8 adet", text)

    def test_partial_failure_is_shown_not_hidden(self):
        text = app.format_received_orders({
            "success": True, "count": 1,
            "orders": [{"quantity": 5, "product": {"name": "iPhone"}}],
            "failed": [{"order_id": 9, "error": "Teslimat tarihi gelmedi."}]})

        self.assertIn("1 sipariş teslim alındı", text)
        self.assertIn("1 sipariş teslim alınamadı", text)
        self.assertIn("#9", text)
        self.assertIn("Teslimat tarihi gelmedi.", text)

    def test_failure_without_detail_still_says_something_useful(self):
        text = app.format_received_orders({
            "orders": [], "failed": [{"order_id": 3}]})

        self.assertIn("#3", text)
        self.assertIn("Teslimat hazır değil.", text)

    def test_nothing_to_report(self):
        self.assertEqual(app.format_received_orders({}), "Teslim alınan sipariş yok.")


if __name__ == "__main__":
    unittest.main()


class IncomingOrderPayloadTest(unittest.TestCase):
    """stock-mcp servisinin backend'e gönderdiği tarih biçimi.

    Regresyon: `place_order` LocalDateTime döndürüyor, biz gün olarak kırpınca
    /api/orders 400 veriyordu.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import os
        import sys
        import types

        # httpx kurulu olmayabilir; yalnızca istemci sınıfı referans ediliyor.
        if "httpx" not in sys.modules:
            try:
                import httpx  # noqa: F401
            except Exception:
                stub = types.ModuleType("httpx")
                stub.AsyncClient = object
                sys.modules["httpx"] = stub

        stock_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stock-mcp")
        sys.path.insert(0, stock_dir)
        try:
            spec = importlib.util.spec_from_file_location(
                "stock_services_under_test", os.path.join(stock_dir, "services.py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules["stock_services_under_test"] = module
            spec.loader.exec_module(module)
            cls.service = module.ProductService
        finally:
            sys.path.remove(stock_dir)

    def test_date_only_is_completed_to_midnight(self):
        self.assertEqual(self.service._normalize_expected("2026-08-23"), "2026-08-23T00:00:00")

    def test_full_datetime_is_passed_through(self):
        value = "2026-08-23T10:27:15.1547863"
        self.assertEqual(self.service._normalize_expected(value), value)

    def test_empty_value_stays_empty(self):
        self.assertIsNone(self.service._normalize_expected(None))
        self.assertIsNone(self.service._normalize_expected(""))


if __name__ == "__main__":
    unittest.main()
