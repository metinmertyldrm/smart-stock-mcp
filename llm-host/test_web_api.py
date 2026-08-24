import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

if importlib.util.find_spec("fastapi") is None:
    raise unittest.SkipTest("FastAPI optional dependency is not installed")

from app import CachedProcurementPlan, ConversationState, execute_plan
from web_api import (AgentApplication, ChatRequest, ConversationStore, FALLBACK_PURPOSE,
                     TOOL_EXPLANATIONS, conversation_title, has_write_intent, now, safe_value)


class WebApiTest(unittest.TestCase):
    def setUp(self):
        # Windows'ta NamedTemporaryFile dosyayi ozel erisimle acik tutar ve
        # sqlite3 ayni dosyayi adiyla acamaz ("unable to open database file").
        self.directory = tempfile.TemporaryDirectory()
        self.store = ConversationStore(os.path.join(self.directory.name, "test.db"))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_chat_request_requires_conversation(self):
        with self.assertRaises(Exception):
            ChatRequest(conversationId="", message="test")

    def test_conversations_are_persistent_and_owner_isolated(self):
        conversation = self.store.create("owner-a", "İlk sohbet")
        self.store.add_message(conversation["id"], "user", "Merhaba")
        self.assertEqual(self.store.get(conversation["id"], "owner-a")["messages"][0]["content"], "Merhaba")
        with self.assertRaises(Exception):
            self.store.get(conversation["id"], "owner-b")

    def test_delete_removes_owned_conversation(self):
        conversation = self.store.create("owner", "Silinecek")
        self.store.delete(conversation["id"], "owner")
        self.assertEqual(self.store.list("owner"), [])

    def test_first_message_replaces_and_persists_default_title(self):
        conversation = self.store.create("owner")
        self.store.ensure(conversation["id"], "owner", "50.000 TL bütçeyle eksik ürünleri satın al")
        self.assertEqual(self.store.get(conversation["id"], "owner")["title"],
                         "50.000 TL Bütçeli Satın Alma")

    def test_title_falls_back_to_a_short_version_of_message(self):
        title = conversation_title("Lütfen Galaxy S24 Ultra için ayrıntılı bir çalışma yapabilir misin?")
        self.assertLessEqual(len(title.split()), 6)
        self.assertIn("Galaxy", title)

    def test_explicit_no_order_phrase_is_read_only(self):
        message = (
            "Galaxy S24 256GB için mevcut marketplace tekliflerini karşılaştır. "
            "Henüz taslak veya sipariş oluşturma."
        )

        self.assertFalse(has_write_intent(message))
        self.assertEqual(conversation_title(message), "Tedarik Tekliflerini Karşılaştırma")

    def test_confirm_without_pending_draft(self):
        agent = AgentApplication(AsyncMock(), AsyncMock(), self.store)
        conversation = self.store.create("owner", "Sohbet")
        with self.assertRaises(Exception):
            import asyncio
            asyncio.run(agent.confirm(conversation["id"], "owner"))

    def test_pending_draft_is_restored_from_persisted_conversation(self):
        conversation = self.store.create("owner", "Sohbet")
        self.store.add_message(
            conversation["id"], "assistant", "Taslak hazır.", response={"pendingDraftId": 42}
        )
        agent = AgentApplication(AsyncMock(), AsyncMock(), self.store)

        with patch.object(agent, "chat", new=AsyncMock(return_value={"ok": True})) as chat:
            import asyncio
            result = asyncio.run(agent.confirm(conversation["id"], "owner"))

        self.assertEqual(result, {"ok": True})
        chat.assert_awaited_once_with(
            conversation["id"], "42 numaralı taslağı onayla ve siparişi oluştur", "owner"
        )

    def test_tool_catalog_and_safe_fallback_are_deterministic(self):
        self.assertEqual(TOOL_EXPLANATIONS["list_low_stock"][0], "Kritik stokları kontrol et")
        self.assertIn("gerekli veriyi", FALLBACK_PURPOSE)
        self.assertEqual(safe_value({"token": "secret", "items": [1, 2]}),
                         {"token": "[gizlendi]", "items": [1, 2]})

    def test_explanation_is_persisted_with_response_json(self):
        conversation = self.store.create("owner", "Sohbet")
        explanation = {"requestSummary": "stokları göster", "safetyChecks": []}
        self.store.add_message(conversation["id"], "assistant", "Tamam",
                               response={"finalAnswer": "Tamam", "explanation": explanation})
        restored = self.store.get(conversation["id"], "owner")["messages"][0]["response"]
        self.assertEqual(restored["explanation"], explanation)

    def test_natural_language_confirmation_allows_pending_write(self):
        conversation = self.store.create("owner", "Sohbet")
        self.store.add_message(
            conversation["id"], "assistant", "Onaylıyor musunuz?", response={"pendingDraftId": 42}
        )
        client = AsyncMock()
        client.list_tools.return_value = [SimpleNamespace(name="place_order")]
        llm = Mock()
        llm.generate.return_value = '{"type":"execution_plan","goal":"ORDER","steps":[{"id":"step_1","tool":"place_order","arguments":{"draftId":42}}]}'
        agent = AgentApplication(client, llm, self.store)
        execution = {"success": True, "results": {"step_1": {"orderId": 7}}, "last_result": {"orderId": 7}}

        with patch("web_api.execute_plan", new=AsyncMock(return_value=execution)):
            import asyncio
            response = asyncio.run(agent.chat(conversation["id"], "onaylıyorum", "owner"))

        self.assertEqual(response["permissionLevel"], "FULL")
        self.assertIsNone(response["pendingDraftId"])

    def test_search_offers_uses_deterministic_turkish_answer(self):
        conversation = self.store.create("owner", "Teklif Karşılaştırma")
        client = AsyncMock()
        client.list_tools.return_value = [SimpleNamespace(name="search_offers")]
        llm = Mock()
        llm.generate.return_value = (
            '{"type":"execution_plan","goal":"REASON","steps":['
            '{"id":"step_1","tool":"search_offers",'
            '"arguments":{"query":"Galaxy S24 256GB"}}]}'
        )
        agent = AgentApplication(client, llm, self.store)
        execution = {
            "success": True,
            "results": {
                "step_1": {
                    "offers": [
                        {
                            "id": 4,
                            "product": {"name": "Galaxy S24 256GB", "sku": "SAM-GS24"},
                            "seller": {"name": "TechStore", "rating": 4.8},
                            "price": 38000,
                            "shippingFee": 100,
                            "totalCost": 38100,
                            "deliveryTimeDays": 2,
                        },
                        {
                            "id": 5,
                            "product": {"name": "Galaxy S24 256GB", "sku": "SAM-GS24"},
                            "seller": {"name": "ElectroShop", "rating": 4.2},
                            "price": 37200,
                            "shippingFee": 150,
                            "totalCost": 37350,
                            "deliveryTimeDays": 3,
                        },
                        {
                            "id": 6,
                            "product": {"name": "Galaxy S24 256GB", "sku": "SAM-GS24"},
                            "seller": {"name": "FastDelivery", "rating": 3.9},
                            "price": 39500,
                            "shippingFee": 0,
                            "totalCost": 39500,
                            "deliveryTimeDays": 1,
                        },
                    ],
                    "hesaplanan_karsilastirma": {
                        "quantity": 1,
                        "cheapestOfferId": 5,
                        "fastestOfferId": 6,
                    },
                }
            },
            "last_result": {},
        }
        execution["last_result"] = execution["results"]["step_1"]

        with patch("web_api.execute_plan", new=AsyncMock(return_value=execution)):
            import asyncio
            response = asyncio.run(agent.chat(
                conversation["id"],
                "Galaxy S24 256GB tekliflerini karşılaştır. Henüz sipariş oluşturma.",
                "owner",
            ))

        self.assertEqual(llm.generate.call_count, 1)
        self.assertIn("Marketplace Teklifleri", response["finalAnswer"])
        self.assertIn("Toplam Maliyet: 37.350,00 TL", response["finalAnswer"])
        self.assertIn("En ucuz seçenek: ElectroShop — 37.350,00 TL", response["finalAnswer"])
        self.assertIn("En hızlı seçenek: FastDelivery — 1 gün", response["finalAnswer"])
        self.assertNotIn("Okay, let's", response["finalAnswer"])

    def test_search_offers_result_is_saved_as_last_reference(self):
        state = ConversationState()
        client = AsyncMock()
        client.call_tool.return_value = {
            "success": True,
            "offers": [{"id": 5}, {"id": 6}],
            "hesaplanan_karsilastirma": {
                "cheapestOfferId": 5,
                "fastestOfferId": 6,
            },
        }
        plan = {
            "type": "execution_plan",
            "goal": "REASON",
            "steps": [{
                "id": "step_1",
                "tool": "search_offers",
                "arguments": {"query": "Galaxy S24 256GB"},
            }],
        }

        import asyncio
        result = asyncio.run(execute_plan(plan, client, {"search_offers"}, state))

        self.assertTrue(result["success"])
        reference = state.references[state.last_reference_id]
        self.assertEqual(reference["source_tool"], "search_offers")
        self.assertEqual(reference["data"]["hesaplanan_karsilastirma"]["cheapestOfferId"], 5)

    def test_offer_tradeoff_followup_uses_last_reference_without_llm_or_tool(self):
        conversation = self.store.create("owner", "Teklif Karşılaştırma")
        client = AsyncMock()
        client.list_tools.return_value = []
        llm = Mock()
        agent = AgentApplication(client, llm, self.store)
        state = agent.states.setdefault(conversation["id"], ConversationState())
        state.last_reference_id = "ref_offers"
        state.references["ref_offers"] = {
            "type": "comparison_response",
            "source_tool": "search_offers",
            "created_at": now(),
            "count": 1,
            "data": {
                "offers": [
                    {
                        "id": 5,
                        "seller": {"name": "ElectroShop", "rating": 4.2},
                        "totalCost": 37350,
                        "deliveryTimeDays": 3,
                    },
                    {
                        "id": 6,
                        "seller": {"name": "FastDelivery", "rating": 3.9},
                        "totalCost": 39500,
                        "deliveryTimeDays": 1,
                    },
                ],
                "hesaplanan_karsilastirma": {
                    "cheapestOfferId": 5,
                    "fastestOfferId": 6,
                },
            },
        }

        import asyncio
        response = asyncio.run(agent.chat(
            conversation["id"],
            "En ucuz ve en hızlı planı karşılaştır.",
            "owner",
        ))

        llm.generate.assert_not_called()
        client.call_tool.assert_not_called()
        self.assertTrue(response["succeeded"])
        self.assertEqual(response["permissionLevel"], "PLAN")
        self.assertEqual(response["trace"], [])
        self.assertIn("En ucuz — ElectroShop", response["finalAnswer"])
        self.assertIn("En hızlı — FastDelivery", response["finalAnswer"])
        self.assertIn("2.150,00 TL daha pahalı", response["finalAnswer"])
        self.assertIn("2 gün daha erken", response["finalAnswer"])

    def test_cached_plan_comparison_with_no_steps_returns_reasoned_answer(self):
        conversation = self.store.create("owner", "Karşılaştırma")
        client = AsyncMock()
        client.list_tools.return_value = []
        llm = Mock()
        llm.generate.side_effect = [
            '{"type":"execution_plan","goal":"REASON","steps":[],"context_sources":["last_cheapest_plan","last_fastest_plan"]}',
            "En ucuz plan daha ekonomik, en hızlı plan ise daha erken teslim edilir.",
        ]
        agent = AgentApplication(client, llm, self.store)
        state = agent.states.setdefault(conversation["id"], ConversationState())
        state.last_cheapest_plan = CachedProcurementPlan("CHEAPEST", [], {"success": True, "total": 100}, now())
        state.last_fastest_plan = CachedProcurementPlan("FASTEST", [], {"success": True, "total": 120}, now())

        import asyncio
        response = asyncio.run(agent.chat(conversation["id"], "En ucuz ve en hızlı planı karşılaştır.", "owner"))

        self.assertEqual(response["finalAnswer"], "En ucuz plan daha ekonomik, en hızlı plan ise daha erken teslim edilir.")
        self.assertEqual(response["trace"], [])
        reasoning_prompt = llm.generate.call_args_list[1].args[0][0]["content"]
        self.assertIn("last_cheapest_plan", reasoning_prompt)
        self.assertIn("last_fastest_plan", reasoning_prompt)


if __name__ == "__main__":
    unittest.main()
