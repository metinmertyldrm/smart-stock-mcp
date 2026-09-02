"""Model zaman asimi: beklenmeyen hata degil, isletim durumu.

Regresyon: 02.09.2026'da taslak zincirinde Ollama 300 saniyeyi asti.
llm.py zaman asimini anlamli bir hataya cevirdigi halde sohbet katmani onu
genel "except Exception" yoluna dusuruyor, kullaniciya takip koduyla
"beklenmeyen bir hata" gosteriliyordu. Kullanici hatayi kendi isteginde
degil sistemde ariyor; oysa yapmasi gereken komutu kisaltmak ya da siniri
yukseltmek.
"""
import unittest

import requests

from test_support import install_optional_stubs

install_optional_stubs()

from llm import LLMService, LlmTimeoutError  # noqa: E402


class LlmTimeoutErrorTest(unittest.TestCase):
    def test_timeout_error_is_a_runtime_error(self):
        """Eski yakalama noktalari bozulmasin diye RuntimeError alt sinifi."""
        error = LlmTimeoutError(20, 300)
        self.assertIsInstance(error, RuntimeError)

    def test_timeout_error_carries_the_limits(self):
        """Kullaniciya gosterilecek ileti siniri sayiyla soyleyebilmeli."""
        error = LlmTimeoutError(20, 300)
        self.assertEqual(error.read_timeout, 300)
        self.assertEqual(error.connect_timeout, 20)
        self.assertIn("300", str(error))

    def test_request_timeout_is_converted(self):
        """requests zaman asimi ham hata olarak yukari sizmamali."""
        service = LLMService()

        def fake_post(*args, **kwargs):
            raise requests.exceptions.ReadTimeout("timed out")

        original = requests.post
        requests.post = fake_post
        try:
            with self.assertRaises(LlmTimeoutError) as caught:
                service.generate([{"role": "user", "content": "x"}], allow_fast_route=False)
        finally:
            requests.post = original
        self.assertEqual(caught.exception.read_timeout, service.read_timeout)


class ChatTimeoutResponseTest(unittest.TestCase):
    def test_chat_reports_timeout_as_gateway_timeout(self):
        """Sohbet ucu 500 + takip kodu degil, 504 + acik aciklama dondurmeli."""
        import web_api

        with open(web_api.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("except LlmTimeoutError as exc:", source)
        self.assertIn("raise HTTPException(504, message) from exc", source)
        # Zaman asimi genel yola dusmemeli: ozel dal genel daldan once gelmeli.
        self.assertLess(
            source.index("except LlmTimeoutError as exc:"),
            source.index("İşlem beklenmeyen bir hatayla tamamlanamadı"),
        )


if __name__ == "__main__":
    unittest.main()
