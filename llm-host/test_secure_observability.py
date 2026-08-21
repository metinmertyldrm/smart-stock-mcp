import asyncio
import json
import os
import tempfile
import unittest

from starlette.responses import StreamingResponse

# secure_api creates its SQLite session store at import time. Keep this focused
# test isolated from the repository working tree.
_SESSION_DB = os.path.join(tempfile.gettempdir(), f"smart-stock-observability-{os.getpid()}.db")
os.environ["LLM_SESSIONS_DB"] = _SESSION_DB

from observability import metrics  # noqa: E402
from secure_api import correlate_chat_response  # noqa: E402


class SecuredResponseObservabilityTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(_SESSION_DB)
        except FileNotFoundError:
            pass

    def setUp(self):
        metrics.reset()

    def test_streamed_chat_response_is_correlated_and_aggregated(self):
        payload = {
            "conversationId": "conv-1",
            "succeeded": True,
            "plan": {"goal": "INFO"},
            "explanation": {"repaired": False},
            "trace": [
                {"tool": "list_out_of_stock", "status": "success", "durationMs": 12.5}
            ],
            "telemetry": {
                "executionId": "exec-1",
                "durationMs": 50,
                "missingFields": ["HTTP request ID", "tool sürümü"],
            },
        }
        raw = json.dumps(payload).encode("utf-8")
        response = StreamingResponse(iter([raw]), status_code=200, media_type="application/json")

        correlated = asyncio.run(correlate_chat_response(response, "request-12345678901234567890123456789012"))
        restored = json.loads(correlated.body)

        self.assertEqual(
            restored["telemetry"]["requestId"],
            "request-12345678901234567890123456789012",
        )
        self.assertNotIn("HTTP request ID", restored["telemetry"]["missingFields"])
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["chat"]["total"], 1)
        self.assertEqual(snapshot["chat"]["succeeded"], 1)
        self.assertEqual(snapshot["tools"][0]["tool"], "list_out_of_stock")
        self.assertEqual(snapshot["tools"][0]["durationMsAverage"], 12.5)


if __name__ == "__main__":
    unittest.main()
