"""web_api.AgentApplication: onarım döngüsü, izin kapısı ve işlem izi."""
import asyncio
import json
import os
import tempfile
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

import web_api  # noqa: E402
from test_support import FakeMCPClient, ScriptedLLM  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def temp_store(test_case):
    """Geçici bir sohbet veritabanı açar ve test bitince kapatılmasını garantiler."""
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    store = web_api.ConversationStore(os.path.join(directory.name, "test.db"))
    test_case.addCleanup(store.close)   # LIFO: önce bağlantı kapanır, sonra klasör silinir
    return store


INVALID_PLAN = json.dumps({"type": "execution_plan", "goal": "PLAN", "steps": [
    {"id": "step_1", "tool": "list_low_stock"}]})

VALID_PLAN = json.dumps({"type": "execution_plan", "goal": "PLAN", "steps": [
    {"id": "step_1", "tool": "list_low_stock", "arguments": {}},
    {"id": "step_2", "tool": "create_procurement_plan",
     "arguments": {"items": [{"product_id": 1, "quantity": 8}], "objective": "CHEAPEST"}}]})

EMPTY_ARGS_PLAN = json.dumps({"type": "execution_plan", "goal": "PLAN", "steps": [
    {"tool": "create_procurement_plan"}]})

STOCK_TOOLS = {
    "list_low_stock": {"success": True, "products": [{"id": 1, "name": "iPhone"}]},
    "create_procurement_plan": lambda args: (
        {"success": True, "overall_total": 100.0, "items": []} if args.get("items")
        else {"success": False, "error": "No items provided."}),
}


class InvalidPlanRepairTest(unittest.TestCase):
    """Regresyon: doğrulama hatası eskiden 500 dönüyordu, onarıma girmiyordu."""

    def test_validation_failure_is_repaired(self):
        client = FakeMCPClient(STOCK_TOOLS)
        agent = web_api.AgentApplication(client, ScriptedLLM(INVALID_PLAN, VALID_PLAN), temp_store(self))

        response = run(agent.chat("c1", "kritik ürünler için plan hazırla"))

        self.assertEqual(client.called_tools, ["list_low_stock", "create_procurement_plan"])
        self.assertNotIn("tamamlanamadı", response["finalAnswer"])

    def test_unrepairable_plan_explains_and_asks(self):
        """Kullanıcının kararı: önce onar, tutmazsa sor."""
        agent = web_api.AgentApplication(
            FakeMCPClient(STOCK_TOOLS), ScriptedLLM(INVALID_PLAN, INVALID_PLAN), temp_store(self))

        response = run(agent.chat("c1", "kritik ürünler için plan hazırla"))

        self.assertEqual(response["plan"]["goal"], "CLARIFY")
        self.assertIn("belirgin", response["finalAnswer"])


class ExecutionRepairTest(unittest.TestCase):
    def test_failed_step_triggers_second_attempt(self):
        client = FakeMCPClient(STOCK_TOOLS)
        agent = web_api.AgentApplication(client, ScriptedLLM(EMPTY_ARGS_PLAN, VALID_PLAN), temp_store(self))

        response = run(agent.chat("c1", "kritik ürünler için plan hazırla"))

        self.assertEqual(client.called_tools[0], "create_procurement_plan")
        self.assertEqual(client.called_tools[-1], "create_procurement_plan")
        self.assertTrue(all(step["status"] == "success" for step in response["trace"]))


class TraceStatusTest(unittest.TestCase):
    """Regresyon: başarısız ve hiç çalışmamış adımlar 'Başarılı' görünüyordu."""

    def test_failed_step_is_reported_with_real_error(self):
        agent = web_api.AgentApplication(
            FakeMCPClient(STOCK_TOOLS), ScriptedLLM(EMPTY_ARGS_PLAN), temp_store(self))

        response = run(agent.chat("c1", "kritik ürünler için plan hazırla"))

        self.assertEqual(response["trace"][0]["status"], "failed")
        self.assertIn("No items provided", response["trace"][0]["resultSummary"])

    def test_steps_after_a_failure_are_marked_skipped(self):
        order_plan = json.dumps({"type": "execution_plan", "goal": "ORDER", "steps": [
            {"id": "step_1", "tool": "place_order",
             "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}},
            {"id": "step_2", "tool": "create_incoming_orders",
             "arguments": {"items": {"$from": "step_1", "$transform": "order_to_incoming_items"}}}]})
        client = FakeMCPClient({
            "place_order": {"success": False, "error": "Draft ID 12 not found."},
            "create_incoming_orders": {"success": True},
        })
        agent = web_api.AgentApplication(client, ScriptedLLM(order_plan), temp_store(self))
        state = web_api.ConversationState()
        state.pending_draft_id = 12
        agent.states["c1"] = state

        response = run(agent.chat("c1", "onaylıyorum"))

        self.assertEqual([step["status"] for step in response["trace"]], ["failed", "skipped"])
        self.assertIn("çalıştırılmadı", response["trace"][1]["resultSummary"])


class PendingDraftTest(unittest.TestCase):
    """Regresyon: create_purchase_draft sonucu 'id' döndürüyor ama web tarafı
    yalnızca 'draftId' arıyordu; taslak→onay→sipariş zinciri hiç kurulamıyordu."""

    DRAFT_PLAN = json.dumps({"type": "execution_plan", "goal": "DRAFT", "steps": [
        {"id": "step_1", "tool": "create_purchase_draft",
         "arguments": {"items": [{"offer_id": 1, "quantity": 5}]}}]})

    def test_draft_id_is_remembered_for_confirmation(self):
        client = FakeMCPClient({"create_purchase_draft": {
            "success": True, "id": 77, "totalCost": 1000.0, "status": "PENDING", "items": []}})
        agent = web_api.AgentApplication(client, ScriptedLLM(self.DRAFT_PLAN), temp_store(self))

        response = run(agent.chat("c1", "taslak sipariş oluştur"))

        self.assertEqual(response["pendingDraftId"], 77)

    def test_order_id_is_not_mistaken_for_a_draft_id(self):
        """place_order da 'id' döndürür; o sipariş kimliğidir, taslak değil."""
        plan = json.dumps({"type": "execution_plan", "goal": "ORDER", "steps": [
            {"id": "step_1", "tool": "place_order", "arguments": {"draft_id": 5}}]})
        client = FakeMCPClient({"place_order": {"success": True, "id": 999, "draftId": 5, "items": []}})
        agent = web_api.AgentApplication(client, ScriptedLLM(plan), temp_store(self))
        state = web_api.ConversationState()
        state.pending_draft_id = 5
        agent.states["c1"] = state

        response = run(agent.chat("c1", "onaylıyorum"))

        # Siparis verildi -> bekleyen taslak kalmamali, 999 asla taslak sanilmamali.
        self.assertIsNone(response["pendingDraftId"])


class OrderConfirmationTest(unittest.TestCase):
    """Zincir create_incoming_orders ile bittiği için iki şey bozulmuştu:
    cevap ham JSON basıyordu ve bekleyen taslak temizlenmiyordu."""

    ORDER_PLAN = json.dumps({"type": "execution_plan", "goal": "ORDER", "steps": [
        {"id": "step_1", "tool": "place_order",
         "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}},
        {"id": "step_2", "tool": "create_incoming_orders",
         "arguments": {"items": {"$from": "step_1", "$transform": "order_to_incoming_items"}}}]})

    def agent_with_state(self):
        client = FakeMCPClient({
            "place_order": {"success": True, "id": 1, "draftId": 5, "totalCost": 536700.0,
                            "status": "PENDING", "expectedDeliveryDate": "2026-08-23T10:39:13",
                            "items": [{"id": 1, "product": {"id": 1, "name": "iPhone 15 Pro 128GB"},
                                       "quantity": 8, "seller": {"name": "ElectroShop"},
                                       "price": 50500.0, "shippingFee": 0.0, "deliveryTimeDays": 3}]},
            "create_incoming_orders": {"success": True, "count": 2, "orders": [
                {"id": 2, "product": {"id": 1, "name": "iPhone 15 Pro 128GB"}, "quantity": 7},
                {"id": 5, "product": {"id": 1, "name": "iPhone 15 Pro 128GB"}, "quantity": 1}]},
        })
        agent = web_api.AgentApplication(client, ScriptedLLM(self.ORDER_PLAN), temp_store(self))
        state = web_api.ConversationState()
        state.pending_draft_id = 5
        agent.states["c1"] = state
        return agent

    def test_answer_is_a_summary_not_raw_json(self):
        response = run(self.agent_with_state().chat("c1", "onaylıyorum"))
        answer = response["finalAnswer"]

        self.assertIn("Sipariş oluşturuldu", answer)
        self.assertIn("#1", answer)
        self.assertIn("2026-08-23", answer)
        self.assertIn("iPhone 15 Pro 128GB: 8 adet", answer)   # 7 + 1 birleştirildi
        self.assertNotIn('"success"', answer)
        self.assertNotIn("stockQuantity", answer)

    def test_pending_draft_is_cleared_after_the_order(self):
        """Zincir place_order ile bitmediği için taslak temizlenmiyordu; onay
        düğmesi sipariş verildikten sonra da ekranda kalıyordu."""
        response = run(self.agent_with_state().chat("c1", "onaylıyorum"))

        self.assertIsNone(response["pendingDraftId"])


class PermissionTest(unittest.TestCase):
    def test_order_is_blocked_without_pending_draft(self):
        order_plan = json.dumps({"type": "execution_plan", "goal": "ORDER", "steps": [
            {"id": "step_1", "tool": "place_order",
             "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}}]})
        client = FakeMCPClient({"place_order": {"success": True}})
        agent = web_api.AgentApplication(client, ScriptedLLM(order_plan), temp_store(self))

        response = run(agent.chat("c1", "satın al"))

        self.assertEqual(client.called_tools, [])
        self.assertEqual(response["plan"]["goal"], "CLARIFY")

    def test_order_refusal_message_is_written_for_humans(self):
        """Doğrulama metni modele yazılmıştır; kullanıcıya ham hâliyle basılmamalı."""
        order_plan = json.dumps({"type": "execution_plan", "goal": "ORDER", "steps": [
            {"id": "step_1", "tool": "place_order",
             "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}}]})
        agent = web_api.AgentApplication(
            FakeMCPClient({"place_order": {"success": True}}), ScriptedLLM(order_plan), temp_store(self))

        response = run(agent.chat("c1", "satın alım yap"))
        answer = response["finalAnswer"]

        self.assertIn("taslak", answer.lower())
        for leaked in ("place_order", "calculate_replenishment", "create_purchase_draft", "goal DRAFT"):
            self.assertNotIn(leaked, answer)
        # Ham metin kaybolmasın; hata ayıklama için planda dursun.
        self.assertIn("place_order", response["plan"]["detail"])

    def test_write_tools_include_batch_incoming_orders(self):
        self.assertIn("create_incoming_orders", web_api.WRITE_TOOLS)

    def test_read_only_request_cannot_write(self):
        draft_plan = json.dumps({"type": "execution_plan", "goal": "DRAFT", "steps": [
            {"id": "step_1", "tool": "create_purchase_draft", "arguments": {"items": []}}]})
        client = FakeMCPClient({"create_purchase_draft": {"success": True}})
        agent = web_api.AgentApplication(client, ScriptedLLM(draft_plan), temp_store(self))

        with self.assertRaises(Exception):
            run(agent.chat("c1", "stok durumu nedir"))   # yazma niyeti yok -> PLAN seviyesi
        self.assertEqual(client.called_tools, [])


class HistoryBoundTest(unittest.TestCase):
    """Geçmiş prompta giriyor; num_ctx taşmasın diye sınırlı olmalı."""

    def test_history_is_capped_by_count_and_length(self):
        store = temp_store(self)
        store.create("owner", "başlık", "c1")
        for _ in range(30):
            store.add_message("c1", "assistant", "x" * 2000)

        history = store.history("c1")

        self.assertEqual(len(history), web_api.HISTORY_MESSAGES)
        self.assertEqual(len(history[0]["content"]), web_api.HISTORY_CHARS)


if __name__ == "__main__":
    unittest.main()
