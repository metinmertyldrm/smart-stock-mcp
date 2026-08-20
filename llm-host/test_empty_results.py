"""Bos kaynak sonucu: teknik hata degil, is durumu.

Regresyon: 9 siparis stoga alindiktan sonra kritik urun kalmayinca zincir
list_low_stock -> [] -> create_procurement_plan(items=[]) -> "No items provided."
seklinde patliyor, kullaniciya "Islem tamamlanamadi" gosteriliyor ve bosuna
ikinci bir LLM cagrisi (onarim) yapiliyordu.
"""
import asyncio
import json
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

import app  # noqa: E402
import web_api  # noqa: E402
from test_support import FakeMCPClient, ScriptedLLM  # noqa: E402
from test_agent_flow import temp_store  # noqa: E402


def run(coro):
    return asyncio.run(coro)


EMPTY_LOW_STOCK_PLAN = {
    "type": "execution_plan",
    "goal": "PLAN",
    "steps": [
        {"id": "step_1", "tool": "list_low_stock", "arguments": {}},
        {"id": "step_2", "tool": "create_procurement_plan", "arguments": {
            "items": {"$from": "step_1.products", "$transform": "low_stock_products_to_items"},
            "objective": "CHEAPEST"}},
    ],
}

TOOLS_EMPTY = {
    "list_low_stock": {"success": True, "products": []},
    "create_procurement_plan": lambda args: (
        {"success": True, "overall_total": 100.0, "items": []} if args.get("items")
        else {"success": False, "error": "No items provided."}),
}

TOOLS_FULL = {
    "list_low_stock": {"success": True, "products": [
        {"id": 1, "name": "iPhone", "stockQuantity": 1, "minimumStock": 5, "targetStock": 9}]},
    "create_procurement_plan": lambda args: (
        {"success": True, "overall_total": 100.0, "items": []} if args.get("items")
        else {"success": False, "error": "No items provided."}),
}


class DetectEmptyInputTest(unittest.TestCase):
    """Saf fonksiyon: hangi bos girdi hangi is gerekcesine cevriliyor."""

    def test_empty_collection_from_low_stock_names_the_source(self):
        step = EMPTY_LOW_STOCK_PLAN["steps"][1]
        reason = app.detect_empty_input(
            "create_procurement_plan", {"items": [], "objective": "CHEAPEST"},
            step, EMPTY_LOW_STOCK_PLAN)

        self.assertIsNotNone(reason)
        self.assertIn("Kritik seviyede ürün bulunmuyor", reason)
        self.assertIn("satın alma planı oluşturulamadı", reason)

    def test_non_empty_collection_is_not_flagged(self):
        step = EMPTY_LOW_STOCK_PLAN["steps"][1]
        self.assertIsNone(app.detect_empty_input(
            "create_procurement_plan", {"items": [{"product_id": 1, "quantity": 8}]},
            step, EMPTY_LOW_STOCK_PLAN))

    def test_tool_without_collection_argument_is_ignored(self):
        """Koruma yalnizca koleksiyon bekleyen araclara uygulanir."""
        self.assertIsNone(app.detect_empty_input(
            "list_low_stock", {}, {"id": "step_1", "tool": "list_low_stock"},
            EMPTY_LOW_STOCK_PLAN))

    def test_unknown_source_falls_back_to_generic_business_reason(self):
        plan = {"steps": [{"id": "step_1", "tool": "receive_orders", "arguments": {
            "order_ids": {"$from": "step_0.orders"}}}]}
        reason = app.detect_empty_input(
            "receive_orders", {"order_ids": []}, plan["steps"][0], plan)

        self.assertIn("uygun kalem bulunamadı", reason)
        self.assertIn("teslim alma işlemi yapılamadı", reason)

    def test_empty_pending_order_list_blocks_receive(self):
        plan = {"steps": [
            {"id": "step_1", "tool": "list_incoming_orders", "arguments": {}},
            {"id": "step_2", "tool": "receive_orders", "arguments": {
                "order_ids": {"$from": "step_1.orders"}}}]}
        reason = app.detect_empty_input(
            "receive_orders", {"order_ids": []}, plan["steps"][1], plan)

        self.assertIn("Teslim alınmayı bekleyen sipariş bulunmuyor", reason)


class ExecutePlanEmptyInputTest(unittest.TestCase):
    def test_downstream_tool_is_never_called_with_an_empty_collection(self):
        client = FakeMCPClient(TOOLS_EMPTY)

        result = run(app.execute_plan(
            EMPTY_LOW_STOCK_PLAN, client, set(TOOLS_EMPTY), app.ConversationState()))

        self.assertEqual(client.called_tools, ["list_low_stock"])
        self.assertFalse(result["success"])
        self.assertIs(result["retryable"], False)
        self.assertIn("Kritik seviyede ürün bulunmuyor", result["business_reason"])
        self.assertEqual(result["failed_step"], "step_2")

    def test_non_empty_source_still_executes_the_whole_chain(self):
        client = FakeMCPClient(TOOLS_FULL)

        result = run(app.execute_plan(
            EMPTY_LOW_STOCK_PLAN, client, set(TOOLS_FULL), app.ConversationState()))

        self.assertEqual(client.called_tools, ["list_low_stock", "create_procurement_plan"])
        self.assertTrue(result["success"])
        self.assertNotIn("business_reason", result)


class EmptyResultChatTest(unittest.TestCase):
    """Uctan uca: kullanici ne goruyor, kac LLM cagrisi yapiliyor."""

    def setUp(self):
        self.client = FakeMCPClient(TOOLS_EMPTY)
        self.llm = ScriptedLLM(json.dumps(EMPTY_LOW_STOCK_PLAN))
        self.agent = web_api.AgentApplication(self.client, self.llm, temp_store(self))
        self.response = run(self.agent.chat(
            "c1", "Stokta azalan ürünler için en ucuz tekliflerden taslak sipariş oluştur."))

    def test_answer_explains_the_business_situation(self):
        self.assertIn("Kritik seviyede ürün bulunmuyor", self.response["finalAnswer"])
        self.assertNotIn("İşlem tamamlanamadı", self.response["finalAnswer"])
        self.assertNotIn("No items provided", self.response["finalAnswer"])

    def test_no_repair_attempt_is_made(self):
        """Onarim olmayan veriyi var edemez; ikinci cagri kullaniciyi bosuna bekletir."""
        self.assertEqual(self.llm.call_count, 1)
        self.assertEqual(self.client.called_tools, ["list_low_stock"])

    def test_run_is_not_reported_as_successful(self):
        self.assertFalse(self.response["succeeded"])

    def test_trace_marks_the_blocked_step_and_keeps_the_raw_detail(self):
        statuses = [step["status"] for step in self.response["trace"]]
        self.assertEqual(statuses, ["success", "failed"])


class RealFailureStillRepairsTest(unittest.TestCase):
    """Koruma, gercek plan hatalarinin onarimini kapatmamali."""

    BROKEN = json.dumps({"type": "execution_plan", "goal": "PLAN", "steps": [
        {"tool": "create_procurement_plan"}]})
    FIXED = json.dumps({"type": "execution_plan", "goal": "PLAN", "steps": [
        {"id": "step_1", "tool": "list_low_stock", "arguments": {}},
        {"id": "step_2", "tool": "create_procurement_plan", "arguments": {
            "items": [{"product_id": 1, "quantity": 8}], "objective": "CHEAPEST"}}]})

    def test_missing_arguments_still_go_through_the_repair_loop(self):
        client = FakeMCPClient(TOOLS_FULL)
        llm = ScriptedLLM(self.BROKEN, self.FIXED)
        agent = web_api.AgentApplication(client, llm, temp_store(self))

        response = run(agent.chat("c1", "kritik ürünler için plan hazırla"))

        self.assertGreater(llm.call_count, 1)
        self.assertTrue(response["succeeded"])


if __name__ == "__main__":
    unittest.main()
