import unittest

from validate_production_env import validate


POSTGRES_TEST_DIGEST = "0123456789abcdef" * 4
OLLAMA_TEST_DIGEST = "fedcba9876543210" * 4

GOOD_ENV = {
    "DB_USERNAME": "smartstock_app",
    "DB_PASSWORD": "a-very-long-random-production-secret-42",
    "DB_NAME": "smart_stock",
    "PUBLIC_ORIGIN": "https://stock.example.com",
    "POSTGRES_IMAGE": "postgres:17@sha256:" + POSTGRES_TEST_DIGEST,
    "OLLAMA_IMAGE": "ollama/ollama:0.11.4@sha256:" + OLLAMA_TEST_DIGEST,
    "OLLAMA_MODEL": "qwen3:8b",
    "WEB_BIND_ADDRESS": "127.0.0.1",
    "WEB_PORT": "8080",
    "LLM_SESSION_TTL_SECONDS": "86400",
}


class ProductionEnvValidationTest(unittest.TestCase):
    def test_good_environment_passes(self):
        self.assertEqual(validate(dict(GOOD_ENV)), [])

    def test_default_database_password_is_rejected(self):
        values = dict(GOOD_ENV)
        values["DB_PASSWORD"] = "postgres"
        errors = validate(values)
        self.assertTrue(any("DB_PASSWORD" in error for error in errors))

    def test_http_origin_is_rejected(self):
        values = dict(GOOD_ENV)
        values["PUBLIC_ORIGIN"] = "http://stock.example.com"
        errors = validate(values)
        self.assertTrue(any("https://" in error for error in errors))

    def test_mutable_image_reference_is_rejected(self):
        values = dict(GOOD_ENV)
        values["POSTGRES_IMAGE"] = "postgres:17"
        errors = validate(values)
        self.assertTrue(any("POSTGRES_IMAGE" in error and "digest" in error for error in errors))

    def test_example_digest_is_rejected(self):
        values = dict(GOOD_ENV)
        values["OLLAMA_IMAGE"] = "ollama/ollama:0.11.4@sha256:" + "0" * 64
        errors = validate(values)
        self.assertTrue(any("OLLAMA_IMAGE" in error and "sentinel" in error for error in errors))

    def test_public_http_bind_requires_explicit_opt_in(self):
        values = dict(GOOD_ENV)
        values["WEB_BIND_ADDRESS"] = "0.0.0.0"
        errors = validate(values)
        self.assertTrue(any("ALLOW_PUBLIC_HTTP_BIND" in error for error in errors))

        values["ALLOW_PUBLIC_HTTP_BIND"] = "true"
        self.assertEqual(validate(values), [])

    def test_session_ttl_is_bounded(self):
        values = dict(GOOD_ENV)
        values["LLM_SESSION_TTL_SECONDS"] = "999999999"
        errors = validate(values)
        self.assertTrue(any("LLM_SESSION_TTL_SECONDS" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
