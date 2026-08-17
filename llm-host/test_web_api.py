import importlib.util
import unittest
from unittest.mock import AsyncMock

if importlib.util.find_spec("fastapi") is None:
    raise unittest.SkipTest("FastAPI optional dependency is not installed")

from web_api import AgentApplication, ChatRequest

class WebApiTest(unittest.TestCase):
    def test_chat_request_requires_conversation(self):
        with self.assertRaises(Exception): ChatRequest(conversationId='',message='test')
    def test_confirm_without_pending_draft(self):
        agent=AgentApplication(AsyncMock(),AsyncMock())
        with self.assertRaises(Exception):
            import asyncio; asyncio.run(agent.confirm('missing'))
if __name__=='__main__': unittest.main()
