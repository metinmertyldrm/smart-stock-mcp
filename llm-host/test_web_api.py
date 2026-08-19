import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

if importlib.util.find_spec("fastapi") is None:
    raise unittest.SkipTest("FastAPI optional dependency is not installed")

from app import CachedProcurementPlan, ConversationState
from web_api import AgentApplication, ChatRequest, ConversationStore, conversation_title, now


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
