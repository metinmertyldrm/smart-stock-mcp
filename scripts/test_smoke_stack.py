import unittest
from unittest.mock import patch

import smoke_stack


class SmokeStackTests(unittest.TestCase):
    def config(self, **overrides):
        values = dict(
            stock_url="http://stock",
            llm_url="http://llm",
            web_url="http://web",
            ollama_url="http://ollama",
            model="qwen3:8b",
            timeout=1.0,
            retries=2,
            retry_delay=0.0,
            chat=False,
        )
        values.update(overrides)
        return smoke_stack.SmokeConfig(**values)

    def test_parse_args_strips_trailing_slashes(self):
        config = smoke_stack.parse_args([
            "--stock-url", "http://localhost:8081/",
            "--llm-url", "http://localhost:8000/",
            "--web-url", "http://localhost:5173/",
            "--ollama-url", "http://localhost:11434/",
            "--retries", "3",
            "--retry-delay", "0",
        ])
        self.assertEqual("http://localhost:8081", config.stock_url)
        self.assertEqual("http://localhost:8000", config.llm_url)
        self.assertEqual("http://localhost:5173", config.web_url)
        self.assertEqual("http://localhost:11434", config.ollama_url)
        self.assertEqual(3, config.retries)

    def test_with_retries_retries_smoke_failures(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise smoke_stack.SmokeFailure("not ready")
            return "ok"

        with patch.object(smoke_stack.time, "sleep") as sleep:
            result = smoke_stack.with_retries("service", flaky, retries=2, delay=0.1)
        self.assertEqual("ok", result)
        self.assertEqual(2, len(calls))
        sleep.assert_called_once_with(0.1)

    def test_ollama_check_requires_configured_model(self):
        with patch.object(smoke_stack, "request_json", return_value=(200, {"models": [{"name": "other:latest"}]})):
            with self.assertRaisesRegex(smoke_stack.SmokeFailure, "not installed"):
                smoke_stack.check_ollama(self.config())

    def test_conversation_crud_creates_reads_lists_and_deletes(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "POST":
                return 201, {"id": "conv-1"}
            if method == "GET" and url.endswith("/conv-1"):
                return 200, {"id": "conv-1"}
            if method == "GET":
                return 200, {"items": [{"id": "conv-1"}]}
            if method == "DELETE":
                return 204, None
            raise AssertionError((method, url))

        with patch.object(smoke_stack, "request_json", side_effect=fake_request):
            smoke_stack.check_conversation_crud(self.config())

        methods = [method for method, _, _ in calls]
        self.assertEqual(["POST", "GET", "GET", "DELETE"], methods)
        for _, _, kwargs in calls:
            self.assertTrue(kwargs["headers"]["X-Client-Id"].startswith("smoke-"))

    def test_conversation_crud_fails_when_delete_fails(self):
        def fake_request(method, url, **kwargs):
            if method == "POST":
                return 201, {"id": "conv-1"}
            if method == "GET" and url.endswith("/conv-1"):
                return 200, {"id": "conv-1"}
            if method == "GET":
                return 200, {"items": [{"id": "conv-1"}]}
            if method == "DELETE":
                return 500, {"detail": "cleanup failed"}
            raise AssertionError((method, url))

        with patch.object(smoke_stack, "request_json", side_effect=fake_request):
            with self.assertRaisesRegex(smoke_stack.SmokeFailure, "expected 204"):
                smoke_stack.check_conversation_crud(self.config())

    def test_read_only_chat_rejects_write_tools(self):
        def fake_request(method, url, **kwargs):
            if method == "POST" and url.endswith("/api/conversations"):
                return 201, {"id": "conv-2"}
            if method == "POST" and url.endswith("/api/chat"):
                return 200, {
                    "succeeded": True,
                    "permissionLevel": "PLAN",
                    "trace": [{"tool": "place_order"}],
                }
            if method == "DELETE":
                return 204, None
            raise AssertionError((method, url))

        with patch.object(smoke_stack, "request_json", side_effect=fake_request):
            with self.assertRaisesRegex(smoke_stack.SmokeFailure, "write tools"):
                smoke_stack.check_read_only_chat(self.config())

    def test_main_runs_optional_chat_once(self):
        config = self.config(chat=True)
        with (
            patch.object(smoke_stack, "parse_args", return_value=config),
            patch.object(smoke_stack, "check_stock"),
            patch.object(smoke_stack, "check_ollama"),
            patch.object(smoke_stack, "check_llm_health"),
            patch.object(smoke_stack, "check_web"),
            patch.object(smoke_stack, "check_conversation_crud"),
            patch.object(smoke_stack, "check_read_only_chat"),
            patch.object(smoke_stack, "with_retries", wraps=smoke_stack.with_retries) as retries,
        ):
            result = smoke_stack.main([])

        self.assertEqual(0, result)
        chat_calls = [call for call in retries.call_args_list if call.args[0] == "Read-only LLM chat"]
        self.assertEqual(1, len(chat_calls))
        self.assertEqual(1, chat_calls[0].args[2])


if __name__ == "__main__":
    unittest.main()
