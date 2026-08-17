import importlib.util
import tempfile
import unittest
from unittest.mock import AsyncMock

if importlib.util.find_spec("fastapi") is None:
    raise unittest.SkipTest("FastAPI optional dependency is not installed")

from web_api import AgentApplication, ChatRequest, ConversationStore


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(suffix=".db")
        self.store = ConversationStore(self.database.name)

    def tearDown(self):
        self.store.close()
        self.database.close()

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

    def test_confirm_without_pending_draft(self):
        agent = AgentApplication(AsyncMock(), AsyncMock(), self.store)
        conversation = self.store.create("owner", "Sohbet")
        with self.assertRaises(Exception):
            import asyncio
            asyncio.run(agent.confirm(conversation["id"], "owner"))


if __name__ == "__main__":
    unittest.main()
