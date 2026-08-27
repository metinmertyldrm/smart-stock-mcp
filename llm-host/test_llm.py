import os
import unittest
from unittest.mock import patch

from llm import LLMService


class LLMServiceTest(unittest.TestCase):
    @patch("llm.requests.post")
    def test_generate_uses_non_streaming_ollama_request(self, post):
        response = post.return_value
        response.json.return_value = {"response": "done"}

        service = LLMService()
        result = service.generate(
            [{"role": "user", "content": "hello"}],
            json_mode=True,
            allow_fast_route=False,
        )

        self.assertEqual(result, "done")
        response.raise_for_status.assert_called_once_with()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], service.model)
        self.assertEqual(payload["prompt"], "/no_think\nuser: hello\nassistant:")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["format"], "json")
        # num_predict/num_ctx artik env ile ayarlanabilir; varsayilanlari dogrula.
        self.assertEqual(payload["options"]["num_predict"], 1024)
        self.assertEqual(payload["options"]["num_ctx"], 8192)

    @patch("llm.requests.post")
    def test_context_window_is_configurable(self, post):
        post.return_value.json.return_value = {"response": "done"}

        with patch.dict(os.environ, {"OLLAMA_NUM_CTX": "16384", "OLLAMA_NUM_PREDICT": "256"}):
            service = LLMService()
        service.generate([{"role": "user", "content": "hello"}])

        options = post.call_args.kwargs["json"]["options"]
        self.assertEqual(options["num_ctx"], 16384)
        self.assertEqual(options["num_predict"], 256)

    @patch("llm.requests.post")
    def test_warns_when_prompt_fills_context_window(self, post):
        """Ollama promptu bastan keserse once AVAILABLE TOOLS kaybolur; gorunur olmali."""
        post.return_value.json.return_value = {
            "response": "done",
            "prompt_eval_count": 9000,
            "eval_count": 10,
        }

        service = LLMService()
        with patch("builtins.print") as printed:
            service.generate([{"role": "user", "content": "hello"}])

        logged = " ".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("9000", logged)
        self.assertIn("UYARI", logged)


if __name__ == "__main__":
    unittest.main()
