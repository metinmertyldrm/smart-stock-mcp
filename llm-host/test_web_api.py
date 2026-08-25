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
                     TOOL_EXPLANATIONS, budget_replenishment_plan, conversation_title,
                     contextual_product_draft_plan, explicit_draft_quantity,
                     has_write_intent, now, offer_tradeoff_fallback,
                     prior_plan_draft_plan, product_replenishment_info_plan,
                     safe_value, structured_answer)


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

    def test_total_budget_shortage_plan_never_invents_product_id(self):
        plan = budget_replenishment_plan(
            "Toplam bütçe 50.000 TL'yi geçmeyecek şekilde eksik ürünleri tamamla."
        )

        self.assertEqual(plan["goal"], "PLAN")
        self.assertEqual(plan["steps"][0], {
            "id": "step_1",
            "tool": "calculate_replenishment",
            "arguments": {},
        })
        self.assertEqual(
            plan["steps"][1]["arguments"]["filters"]["max_total_budget"],
            50000.0,
        )
        self.assertNotIn("product_id", str(plan))

    def test_followup_draft_uses_complete_previous_plan(self):
        state = ConversationState()
        state.last_plan = {
            "success": True,
            "items": [
                {"product_id": 81, "allocations": [{"offer_id": 5, "quantity": 1}]},
                {"product_id": 82, "allocations": [{"offer_id": 12, "quantity": 30}]},
            ],
        }

        plan = prior_plan_draft_plan("Buna göre taslak sipariş oluştur.", state)

        self.assertEqual(plan["goal"], "DRAFT")
        self.assertEqual(plan["steps"], [{
            "id": "step_1",
            "tool": "create_purchase_draft",
            "arguments": {
                "items": {
                    "$from_context": "last_plan",
                    "$transform": "plan_to_draft_items",
                }
            },
        }])

    def test_followup_draft_accepts_natural_turkish_accusative(self):
        state = ConversationState()
        state.last_plan = {
            "success": True,
            "items": [{
                "product_id": 91,
                "allocations": [{"offer_id": 9, "quantity": 1}],
            }],
        }

        plan = prior_plan_draft_plan(
            "Bu planın tamamı için satın alma taslağı oluştur. "
            "Henüz siparişi onaylama.",
            state,
        )

        self.assertEqual(plan["goal"], "DRAFT")
        self.assertEqual(
            plan["steps"][0]["arguments"]["items"]["$from_context"],
            "last_plan",
        )

    def test_contextual_product_draft_uses_user_quantity_not_replenishment(self):
        state = ConversationState()
        state.last_product = {"id": 418, "name": "Veritabanından Gelen Ürün"}
        state.last_replenishment = {
            "product_id": 418,
            "replenishment_quantity_needed": 33,
        }
        state.last_reference_id = "ref_product"
        state.references["ref_product"] = {
            "type": "replenishment_list",
            "source_tool": "calculate_replenishment",
            "count": 1,
            "data": [{"productId": 418, "replenishmentQuantityNeeded": 33}],
        }

        plan = contextual_product_draft_plan("1 adeti için taslak oluştur", state)

        self.assertEqual(plan["goal"], "DRAFT")
        self.assertEqual(
            plan["steps"][0]["arguments"]["items"],
            [{"product_id": 418, "quantity": 1}],
        )
        self.assertEqual(
            plan["steps"][1]["arguments"]["items"],
            {"$from": "step_1", "$transform": "plan_to_draft_items"},
        )

    def test_contextual_product_draft_requires_one_authoritative_product(self):
        state = ConversationState()
        state.last_product = {"id": 418, "name": "İlk Ürün"}
        state.last_reference_id = "ref_many"
        state.references["ref_many"] = {
            "type": "product_list",
            "source_tool": "list_products",
            "count": 2,
            "data": [{"id": 418}, {"id": 419}],
        }

        self.assertIsNone(contextual_product_draft_plan("1 adet taslak oluştur", state))

    def test_explicit_draft_quantity_understands_common_turkish_forms(self):
        self.assertEqual(explicit_draft_quantity("1 adeti için taslak oluştur"), 1)
        self.assertEqual(explicit_draft_quantity("iki tanesini taslağa ekle"), 2)

    def test_offer_tradeoff_has_focused_safe_fallback(self):
        result = {
            "offers": [
                {
                    "id": 5,
                    "seller": {"name": "ElectroShop"},
                    "totalCost": 37350,
                    "deliveryTimeDays": 3,
                },
                {
                    "id": 6,
                    "seller": {"name": "FastDelivery"},
                    "totalCost": 39500,
                    "deliveryTimeDays": 1,
                },
            ],
            "hesaplanan_karsilastirma": {
                "cheapestOfferId": 5,
                "fastestOfferId": 6,
            },
        }

        answer = offer_tradeoff_fallback(
            "En ucuz ve en hızlı planı karşılaştır.",
            result,
        )

        self.assertIn("2.150,00 TL daha pahalı", answer)
        self.assertIn("2 gün daha erken", answer)
        self.assertIn("Taslak veya sipariş oluşturulmadı", answer)

    def test_explicit_no_order_phrase_is_read_only(self):
        message = (
            "Galaxy S24 256GB için mevcut marketplace tekliflerini karşılaştır. "
            "Henüz taslak veya sipariş oluşturma."
        )

        self.assertFalse(has_write_intent(message))
        self.assertEqual(conversation_title(message), "Tedarik Tekliflerini Karşılaştırma")

    def test_product_replenishment_info_uses_pending_aware_calculation(self):
        plan = product_replenishment_info_plan(
            "Dell Latitude 5440 için mevcut stok, bekleyen ikmal ve hedef stoğa "
            "ulaşmak için hâlâ sipariş edilmesi gereken miktarı göster. "
            "Henüz taslak veya sipariş oluşturma."
        )

        self.assertEqual(plan["goal"], "INFO")
        self.assertEqual(
            [step["tool"] for step in plan["steps"]],
            ["search_products", "calculate_replenishment"],
        )
        self.assertEqual(plan["steps"][0]["arguments"]["query"], "Dell Latitude 5440")
        self.assertEqual(
            plan["steps"][1]["arguments"]["product_ids"],
            {"$from": "step_1.products.id"},
        )

    def test_answer_schema_placeholder_uses_authoritative_fallback(self):
        fallback = "Dell Latitude 5440 için kalan ihtiyaç 3 adettir."

        answer = structured_answer('{"answer":"Kısa ve doğal Türkçe cevap"}', fallback)

        self.assertEqual(answer, fallback)

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

    def test_search_offers_runs_planner_and_llm_finalizer(self):
        conversation = self.store.create("owner", "Teklif Karşılaştırma")
        client = AsyncMock()
        client.list_tools.return_value = [SimpleNamespace(name="search_offers")]
        llm = Mock()
        llm.generate.side_effect = [
            (
                '{"type":"execution_plan","goal":"REASON","steps":['
                '{"id":"step_1","tool":"search_offers",'
                '"arguments":{"query":"Galaxy S24 256GB"}}]}'
            ),
            (
                '{"answer":"ElectroShop en ucuz seçenektir; FastDelivery ise '
                'en hızlı seçenektir. Henüz taslak veya sipariş oluşturulmadı."}'
            ),
        ]
        agent = AgentApplication(client, llm, self.store)
        execution = {
            "success": True,
            "results": {
                "step_1": {
                    "query": "Galaxy S24 256GB",
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

        self.assertEqual(llm.generate.call_count, 2)
        self.assertIn("ElectroShop en ucuz", response["finalAnswer"])
        self.assertIn("FastDelivery ise en hızlı", response["finalAnswer"])
        self.assertNotIn("Okay, let's", response["finalAnswer"])
        for call in llm.generate.call_args_list:
            self.assertTrue(call.kwargs["json_mode"])
            self.assertFalse(call.kwargs["allow_fast_route"])
        self.assertEqual(
            llm.generate.call_args_list[1].kwargs["num_predict"],
            512,
        )

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

    def test_informational_order_word_is_downgraded_to_read_only_permission(self):
        conversation = self.store.create("owner", "İkmal bilgisi")
        client = AsyncMock()
        client.list_tools.return_value = [
            SimpleNamespace(name="search_products"),
            SimpleNamespace(name="calculate_replenishment"),
        ]
        llm = Mock()
        llm.generate.side_effect = [
            '{"type":"execution_plan","goal":"INFO","steps":['
            '{"id":"step_1","tool":"list_products","arguments":{}}]}',
            '{"answer":"Kısa ve doğal Türkçe cevap"}',
        ]
        agent = AgentApplication(client, llm, self.store)
        execution = {
            "success": True,
            "results": {
                "step_1": {
                    "success": True,
                    "count": 1,
                    "products": [{"id": 2, "name": "Dell Latitude 5440"}],
                },
                "step_2": {
                    "success": True,
                    "count": 1,
                    "replenishments": [{
                        "productId": 2,
                        "productName": "Dell Latitude 5440",
                        "stockQuantity": 1,
                        "minimumStock": 3,
                        "targetStock": 5,
                        "pendingIncomingQuantity": 1,
                        "replenishmentQuantityNeeded": 3,
                    }],
                },
            },
        }
        execution["last_result"] = execution["results"]["step_2"]

        with patch("web_api.execute_plan", new=AsyncMock(return_value=execution)):
            import asyncio
            response = asyncio.run(agent.chat(
                conversation["id"],
                "Dell Latitude 5440 için mevcut stok, bekleyen ikmal ve hedef stoğa "
                "ulaşmak için hâlâ sipariş edilmesi gereken miktarı göster. "
                "Henüz taslak veya sipariş oluşturma.",
                "owner",
            ))

        self.assertEqual(response["permissionLevel"], "PLAN")
        self.assertEqual(
            [step["tool"] for step in response["plan"]["steps"]],
            ["search_products", "calculate_replenishment"],
        )
        self.assertIn("Dell Latitude 5440", response["finalAnswer"])
        self.assertIn("Bekleyen Sipariş: 1", response["finalAnswer"])
        self.assertIn("Alınması Gereken Miktar: 3", response["finalAnswer"])
        self.assertNotIn("Kısa ve doğal Türkçe cevap", response["finalAnswer"])

    def test_offer_tradeoff_followup_runs_ollama_mcp_and_finalizer(self):
        conversation = self.store.create("owner", "Teklif Karşılaştırma")
        client = AsyncMock()
        client.list_tools.return_value = [SimpleNamespace(name="search_offers")]
        client.call_tool.return_value = {
            "success": True,
            "query": "Galaxy S24 256GB",
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
        }
        llm = Mock()
        llm.generate.side_effect = [
            (
                '{"type":"execution_plan","goal":"CHAT","steps":[],'
                '"answer":"Önceki sonuçları karşılaştırabilirim."}'
            ),
            (
                '{"answer":"En hızlı seçenek 2.150,00 TL daha pahalı, '
                'ancak 2 gün daha erken teslim edilir."}'
            ),
        ]
        agent = AgentApplication(client, llm, self.store)
        state = agent.states.setdefault(conversation["id"], ConversationState())
        state.last_reference_id = "ref_offers"
        state.references["ref_offers"] = {
            "type": "comparison_response",
            "source_tool": "search_offers",
            "created_at": now(),
            "count": 1,
            "data": {
                "query": "Galaxy S24 256GB",
                "offers": [{"id": 5}, {"id": 6}],
            },
        }

        import asyncio
        response = asyncio.run(agent.chat(
            conversation["id"],
            "En ucuz ve en hızlı planı karşılaştır.",
            "owner",
        ))

        self.assertEqual(llm.generate.call_count, 2)
        client.call_tool.assert_awaited_once_with(
            "search_offers", {"query": "Galaxy S24 256GB"}
        )
        self.assertTrue(response["succeeded"])
        self.assertEqual(response["permissionLevel"], "PLAN")
        self.assertEqual(len(response["trace"]), 1)
        self.assertIn("2.150,00 TL daha pahalı", response["finalAnswer"])
        self.assertIn("2 gün daha erken", response["finalAnswer"])

    def test_cached_plan_comparison_with_no_steps_returns_reasoned_answer(self):
        conversation = self.store.create("owner", "Karşılaştırma")
        client = AsyncMock()
        client.list_tools.return_value = []
        llm = Mock()
        llm.generate.side_effect = [
            '{"type":"execution_plan","goal":"REASON","steps":[],"context_sources":["last_cheapest_plan","last_fastest_plan"]}',
            '{"answer":"En ucuz plan daha ekonomik, en hızlı plan ise daha erken teslim edilir."}',
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
