"""Regression coverage for blocking local LLM inference in the async API path."""
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest

from test_support import FakeMCPClient, install_optional_stubs

install_optional_stubs()

import web_api  # noqa: E402


INFO_PLAN = json.dumps({
    "type": "execution_plan",
    "goal": "INFO",
    "steps": [
        {"id": "step_1", "tool": "list_out_of_stock", "arguments": {}},
    ],
})


class BlockingLLM:
    """Simulate a slow synchronous Ollama HTTP call."""

    def __init__(self):
        self.release = threading.Event()

    def generate(self, messages):
        self.release.wait(timeout=1.0)
        return INFO_PLAN


class AsyncLLMGenerationTest(unittest.TestCase):
    def test_slow_generation_does_not_block_the_event_loop(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                store = web_api.ConversationStore(os.path.join(directory, "test.db"))
                llm = BlockingLLM()
                agent = web_api.AgentApplication(
                    FakeMCPClient({
                        "list_out_of_stock": {
                            "success": True,
                            "products": [],
                        },
                    }),
                    llm,
                    store,
                )

                # Release the fake synchronous model from a real OS thread. If
                # generate() runs on the event loop, the 50 ms tick below cannot
                # resume until this 250 ms timer fires.
                timer = threading.Timer(0.25, llm.release.set)
                timer.start()
                try:
                    started = time.perf_counter()
                    chat_task = asyncio.create_task(
                        agent.chat("event-loop-test", "Stokta olmayan ürünleri listele.")
                    )
                    await asyncio.sleep(0.05)
                    elapsed = time.perf_counter() - started

                    self.assertLess(
                        elapsed,
                        0.15,
                        "Synchronous LLM generation blocked the FastAPI event loop.",
                    )
                    response = await chat_task
                    self.assertTrue(response["succeeded"])
                    self.assertEqual(response["permissionLevel"], "PLAN")
                finally:
                    llm.release.set()
                    timer.cancel()
                    store.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
