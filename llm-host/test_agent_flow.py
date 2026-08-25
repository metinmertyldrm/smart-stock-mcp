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


class PersistedPlanFollowupTest(unittest.TestCase):
    """A plan-to-draft follow-up must survive loss of process-local state."""

    def test_followup_draft_restores_the_complete_plan_after_host_restart(self):
        procurement = {
            "success": True,
            "complete": False,
            "items": [{
                "product_id": 2,
                "product_name": "Dell Latitude 5440",
                "required_quantity": 4,
                "fulfilled_quantity": 1,
                "missing_quantity": 3,
                "complete": False,
                "allocations": [{"offer_id": 9, "quantity": 1}],
                "total_cost": 28500.0,
            }],
            "overall_total": 28500.0,
        }
        client = FakeMCPClient({
            "list_low_stock": {
                "success": True,
                "products": [{"id": 2, "name": "Dell Latitude 5440"}],
            },
            "create_procurement_plan": procurement,
            "create_purchase_draft": {
                "success": True,
                "id": 77,
                "totalCost": 28500.0,
                "status": "PENDING",
                "items": [],
            },
        })

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "restart.db")
            first_store = web_api.ConversationStore(path)
            first_agent = web_api.AgentApplication(client, ScriptedLLM(VALID_PLAN), first_store)
            first_response = run(first_agent.chat("c1", "kritik ürünler için plan hazırla"))
            self.assertTrue(first_response["succeeded"])
            first_store.close()

            restarted_store = web_api.ConversationStore(path)
            try:
                restarted_agent = web_api.AgentApplication(
                    client,
                    ScriptedLLM(INVALID_PLAN),
                    restarted_store,
                )
                response = run(restarted_agent.chat(
                    "c1",
                    "Bu planın tamamı için satın alma taslağı oluştur. "
                    "Henüz siparişi onaylama.",
                ))
            finally:
                restarted_store.close()

        self.assertTrue(response["succeeded"])
        self.assertEqual(response["pendingDraftId"], 77)
        self.assertEqual(client.calls[-1], (
            "create_purchase_draft",
            {"items": [{"product_id": 2, "offer_id": 9, "quantity": 1}]},
        ))


class ContextualProductQuantityDraftTest(unittest.TestCase):
    """Regression for a follow-up that previously drafted another product."""

    BAD_LITERAL_PLAN = json.dumps({
        "type": "execution_plan",
        "goal": "DRAFT",
        "steps": [
            {"id": "step_1", "tool": "search_products",
             "arguments": {"query": "Database Product"}},
            {"id": "step_2", "tool": "calculate_replenishment",
             "arguments": {"product_id": 742}},
            {"id": "step_3", "tool": "create_purchase_draft",
             "arguments": {"items": [{"offer_id": 1, "quantity": 33}]}},
        ],
    })

    def test_explicit_quantity_and_context_product_replace_bad_model_ids(self):
        client = FakeMCPClient({
            "create_procurement_plan": {
                "success": True,
                "complete": True,
                "items": [{
                    "product_id": 742,
                    "product_name": "Database Product",
                    "required_quantity": 1,
                    "fulfilled_quantity": 1,
                    "allocations": [{"offer_id": 9007, "quantity": 1}],
                }],
                "overall_total": 28000.0,
            },
            "create_purchase_draft": {
                "success": True,
                "id": 88,
                "totalCost": 28000.0,
                "status": "PENDING",
                "items": [{
                    "product": {"id": 742, "name": "Database Product"},
                    "quantity": 1,
                    "seller": {"name": "Current Seller"},
                    "price": 28000.0,
                    "shippingFee": 0.0,
                    "deliveryTimeDays": 2,
                }],
            },
        })
        agent = web_api.AgentApplication(
            client,
            ScriptedLLM(self.BAD_LITERAL_PLAN),
            temp_store(self),
        )
        state = web_api.ConversationState()
        state.last_product = {"id": 742, "name": "Database Product"}
        state.last_replenishment = {
            "product_id": 742,
            "replenishment_quantity_needed": 33,
        }
        state.last_reference_id = "ref_one"
        state.references["ref_one"] = {
            "type": "replenishment_list",
            "source_tool": "calculate_replenishment",
            "count": 1,
            "data": [{"productId": 742, "replenishmentQuantityNeeded": 33}],
        }
        agent.states["c1"] = state

        response = run(agent.chat("c1", "1 adeti için taslak oluştur"))

        self.assertTrue(response["succeeded"])
        self.assertEqual(client.called_tools, [
            "create_procurement_plan",
            "create_purchase_draft",
        ])
        self.assertEqual(client.calls[0][1]["items"], [
            {"product_id": 742, "quantity": 1},
        ])
        self.assertEqual(client.calls[1][1]["items"], [
            {"product_id": 742, "offer_id": 9007, "quantity": 1},
        ])
        self.assertIn("Database Product", response["finalAnswer"])
        self.assertNotIn("33 adet", response["finalAnswer"])


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
        {"id": "step_1", "tool": "create_procurement_plan",
         "arguments": {"items": [{"product_id": 18, "quantity": 5}],
                       "objective": "CHEAPEST"}},
        {"id": "step_2", "tool": "create_purchase_draft",
         "arguments": {"items": {"$from": "step_1",
                                  "$transform": "plan_to_draft_items"}}}]})

    def test_draft_id_is_remembered_for_confirmation(self):
        client = FakeMCPClient({
            "create_procurement_plan": {
                "success": True,
                "items": [{
                    "product_id": 18,
                    "allocations": [{"offer_id": 1, "quantity": 5}],
                }],
            },
            "create_purchase_draft": {
                "success": True, "id": 77, "totalCost": 1000.0,
                "status": "PENDING", "items": [],
            },
        })
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


class ReceiveConfirmationTest(unittest.TestCase):
    """Taslak: stok değiştirme işlemlerinden önce kullanıcı onayı alınmalı.
    Teslim alma da satın alma gibi iki aşamalı."""

    LISTING = json.dumps({"type": "execution_plan", "goal": "RECEIVE", "steps": [
        {"id": "step_1", "tool": "list_incoming_orders", "arguments": {"pending_only": True, "ready_only": True}}]})
    RECEIVING = json.dumps({"type": "execution_plan", "goal": "RECEIVE", "steps": [
        {"id": "step_1", "tool": "receive_orders",
         "arguments": {"order_ids": {"$from_context": "pending_receive_ids"}}}]})

    PENDING = {"success": True, "count": 2, "orders": [
        {"id": 2, "product": {"id": 1, "name": "iPhone 15 Pro 128GB"}, "quantity": 8,
         "status": "PENDING", "expectedDeliveryDate": "2026-08-23T10:39:13"},
        {"id": 3, "product": {"id": 6, "name": "Kablosuz Klavye"}, "quantity": 6,
         "status": "PENDING", "expectedDeliveryDate": "2026-08-23T10:39:13"}]}

    def client(self):
        return FakeMCPClient({
            "list_incoming_orders": self.PENDING,
            "receive_orders": lambda args: {"success": True, "count": len(args["order_ids"]),
                "orders": [{"id": i, "product": {"name": "iPhone 15 Pro 128GB"}, "quantity": 8,
                            "status": "RECEIVED"} for i in args["order_ids"]]},
        })

    def test_first_request_only_lists_and_asks(self):
        client = self.client()
        agent = web_api.AgentApplication(client, ScriptedLLM(self.LISTING), temp_store(self))

        response = run(agent.chat("c1", "teslim edilen ürünleri stoğa ekle"))

        self.assertEqual(client.called_tools, ["list_incoming_orders"])
        self.assertEqual(client.calls[0][1], {"pending_only": True, "ready_only": True})
        self.assertNotIn("receive_orders", client.called_tools)
        self.assertIn("onaylıyor musunuz", response["finalAnswer"])
        self.assertEqual(response["pendingReceiveIds"], [2, 3])

    def test_receive_listing_forces_ready_filter_even_if_model_omits_it(self):
        unsafe_plan = json.dumps({"type": "execution_plan", "goal": "RECEIVE", "steps": [
            {"id": "step_1", "tool": "list_incoming_orders", "arguments": {}}]})
        client = self.client()
        agent = web_api.AgentApplication(client, ScriptedLLM(unsafe_plan), temp_store(self))

        run(agent.chat("c1", "teslim edilenleri stoğa ekle"))

        self.assertEqual(client.calls[0][1], {"pending_only": True, "ready_only": True})

    def test_empty_receivable_list_does_not_offer_confirmation(self):
        client = FakeMCPClient({"list_incoming_orders": {"success": True, "count": 0, "orders": []}})
        agent = web_api.AgentApplication(client, ScriptedLLM(self.LISTING), temp_store(self))

        response = run(agent.chat("c1", "teslim edilenleri stoğa ekle"))

        self.assertEqual(response["pendingReceiveIds"], [])
        self.assertEqual(response["finalAnswer"], "Şu anda stoğa alınabilecek teslimat yok.")

    def test_partial_batch_failure_is_explained(self):
        client = FakeMCPClient({"receive_orders": {
            "success": True, "count": 1,
            "orders": [{"id": 2, "product": {"name": "Telefon"}, "quantity": 3}],
            "failed": [{"order_id": 3, "error": "Beklenen teslim tarihi: 2026-08-23"}],
        }})
        agent = web_api.AgentApplication(client, ScriptedLLM(self.RECEIVING), temp_store(self))
        state = web_api.ConversationState()
        state.pending_receive_ids = [2, 3]
        agent.states["c1"] = state

        response = run(agent.chat("c1", "onaylıyorum"))

        self.assertIn("1 sipariş teslim alındı", response["finalAnswer"])
        self.assertIn("#3", response["finalAnswer"])
        self.assertIn("2026-08-23", response["finalAnswer"])

    def test_receiving_without_confirmation_is_blocked(self):
        client = self.client()
        agent = web_api.AgentApplication(client, ScriptedLLM(self.RECEIVING), temp_store(self))

        response = run(agent.chat("c1", "stoğa al"))

        self.assertEqual(client.called_tools, [])
        self.assertEqual(response["plan"]["goal"], "CLARIFY")

    def test_confirm_endpoint_handles_receiving_too(self):
        """Onay düğmesi hem taslak hem teslim alma için çalışmalı."""
        client = self.client()
        agent = web_api.AgentApplication(client, ScriptedLLM(self.RECEIVING), temp_store(self))
        agent.store.create("owner", "Sohbet", "c1")
        state = web_api.ConversationState()
        state.pending_receive_ids = [2, 3]
        agent.states["c1"] = state

        response = run(agent.confirm("c1", "owner"))

        self.assertEqual(client.called_tools, ["receive_orders"])
        self.assertEqual(response["pendingReceiveIds"], [])

    def test_confirm_endpoint_reports_when_nothing_is_pending(self):
        agent = web_api.AgentApplication(self.client(), ScriptedLLM(self.RECEIVING), temp_store(self))
        agent.store.create("owner", "Sohbet", "c1")

        with self.assertRaises(Exception):
            run(agent.confirm("c1", "owner"))

    def test_confirmation_applies_and_clears_the_pending_list(self):
        client = self.client()
        agent = web_api.AgentApplication(client, ScriptedLLM(self.RECEIVING), temp_store(self))
        state = web_api.ConversationState()
        state.pending_receive_ids = [2, 3]
        agent.states["c1"] = state

        response = run(agent.chat("c1", "evet, stoğa al"))

        self.assertEqual(client.called_tools, ["receive_orders"])
        self.assertEqual(client.calls[0][1]["order_ids"], [2, 3])
        self.assertIn("stoğa eklendi", response["finalAnswer"])
        self.assertEqual(response["pendingReceiveIds"], [])


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
            {"id": "step_1", "tool": "create_procurement_plan",
             "arguments": {"items": [{"product_id": 1, "quantity": 1}]}},
            {"id": "step_2", "tool": "create_purchase_draft",
             "arguments": {"items": {"$from": "step_1",
                                      "$transform": "plan_to_draft_items"}}}]})
        client = FakeMCPClient({
            "create_procurement_plan": {"success": True, "items": []},
            "create_purchase_draft": {"success": True},
        })
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


class EmptyResultWordingTest(unittest.TestCase):
    """Boş liste her zaman kötü haber değil; kritik ürün kalmaması iyi haberdir."""

    LOW_STOCK_PLAN = json.dumps({"type": "execution_plan", "goal": "INFO", "steps": [
        {"id": "step_1", "tool": "list_low_stock", "arguments": {}}]})

    def test_no_critical_products_reads_as_good_news(self):
        client = FakeMCPClient({"list_low_stock": {"success": True, "count": 0, "products": []}})
        agent = web_api.AgentApplication(client, ScriptedLLM(self.LOW_STOCK_PLAN), temp_store(self))

        response = run(agent.chat("c1", "stokta azalan ürünleri göster"))

        self.assertIn("Kritik seviyede ürün yok", response["finalAnswer"])
        self.assertNotIn("bulunamadı", response["finalAnswer"])

    def test_search_with_no_match_still_says_not_found(self):
        plan = json.dumps({"type": "execution_plan", "goal": "INFO", "steps": [
            {"id": "step_1", "tool": "search_products", "arguments": {"query": "xyz"}}]})
        client = FakeMCPClient({"search_products": {"success": True, "products": []}})
        agent = web_api.AgentApplication(client, ScriptedLLM(plan), temp_store(self))

        response = run(agent.chat("c1", "xyz ara"))

        self.assertIn("eşleşen ürün bulunamadı", response["finalAnswer"])
