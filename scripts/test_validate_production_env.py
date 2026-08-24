import unittest

from validate_production_env import validate


POSTGRES_TEST_DIGEST = "0123456789abcdef" * 4
OLLAMA_TEST_DIGEST = "fedcba9876543210" * 4

GOOD_ENV = {
    "DB_USERNAME": "smartstock_app",
    "DB_PASSWORD": "a-very-long-random-production-secret-42",
    "DB_NAME": "smart_stock",
    "LLM_AUTH_MODE": "local",
    "LLM_BOOTSTRAP_ADMIN_USERNAME": "smartstock-admin",
    "LLM_BOOTSTRAP_ADMIN_PASSWORD": "another-very-long-admin-secret-84",
    "LLM_SESSION_COOKIE_SECURE": "auto",
    "LLM_LOGIN_MAX_FAILURES": "5",
    "LLM_LOGIN_WINDOW_SECONDS": "300",
    "LLM_LOGIN_BLOCK_SECONDS": "300",
    "LLM_LOGIN_RATE_LIMIT_MAX_KEYS": "5000",
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

    def test_production_requires_local_identity_mode(self):
        values = dict(GOOD_ENV)
        values["LLM_AUTH_MODE"] = "anonymous"
        errors = validate(values)
        self.assertTrue(any("LLM_AUTH_MODE" in error and "local" in error for error in errors))

    def test_bootstrap_admin_password_is_strong_and_separate(self):
        values = dict(GOOD_ENV)
        values["LLM_BOOTSTRAP_ADMIN_PASSWORD"] = "short"
        errors = validate(values)
        self.assertTrue(any("LLM_BOOTSTRAP_ADMIN_PASSWORD" in error for error in errors))

        values = dict(GOOD_ENV)
        values["LLM_BOOTSTRAP_ADMIN_PASSWORD"] = values["DB_PASSWORD"]
        errors = validate(values)
        self.assertTrue(any("DB_PASSWORD" in error and "BOOTSTRAP" in error for error in errors))

    def test_bootstrap_admin_username_must_match_local_identity_rules(self):
        values = dict(GOOD_ENV)
        values["LLM_BOOTSTRAP_ADMIN_USERNAME"] = "Bad User Name"
        errors = validate(values)
        self.assertTrue(any("LLM_BOOTSTRAP_ADMIN_USERNAME" in error for error in errors))

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

    def test_public_http_bind_requires_opt_in_and_secure_cookie(self):
        values = dict(GOOD_ENV)
        values["WEB_BIND_ADDRESS"] = "0.0.0.0"
        errors = validate(values)
        self.assertTrue(any("ALLOW_PUBLIC_HTTP_BIND" in error for error in errors))
        self.assertTrue(any("LLM_SESSION_COOKIE_SECURE=true" in error for error in errors))

        values["ALLOW_PUBLIC_HTTP_BIND"] = "true"
        errors = validate(values)
        self.assertTrue(any("LLM_SESSION_COOKIE_SECURE=true" in error for error in errors))

        values["LLM_SESSION_COOKIE_SECURE"] = "true"
        self.assertEqual(validate(values), [])

    def test_loopback_acceptance_may_use_auto_cookie_security(self):
        values = dict(GOOD_ENV)
        values["LLM_SESSION_COOKIE_SECURE"] = "auto"
        self.assertEqual(validate(values), [])

    def test_invalid_cookie_secure_mode_is_rejected(self):
        values = dict(GOOD_ENV)
        values["LLM_SESSION_COOKIE_SECURE"] = "sometimes"
        errors = validate(values)
        self.assertTrue(any("LLM_SESSION_COOKIE_SECURE" in error for error in errors))

    def test_session_ttl_is_bounded(self):
        values = dict(GOOD_ENV)
        values["LLM_SESSION_TTL_SECONDS"] = "999999999"
        errors = validate(values)
        self.assertTrue(any("LLM_SESSION_TTL_SECONDS" in error for error in errors))

    def test_login_throttle_settings_are_bounded(self):
        values = dict(GOOD_ENV)
        values["LLM_LOGIN_MAX_FAILURES"] = "0"
        values["LLM_LOGIN_WINDOW_SECONDS"] = "0"
        values["LLM_LOGIN_BLOCK_SECONDS"] = "999999"
        values["LLM_LOGIN_RATE_LIMIT_MAX_KEYS"] = "10"
        errors = validate(values)
        for name in (
            "LLM_LOGIN_MAX_FAILURES",
            "LLM_LOGIN_WINDOW_SECONDS",
            "LLM_LOGIN_BLOCK_SECONDS",
            "LLM_LOGIN_RATE_LIMIT_MAX_KEYS",
        ):
            self.assertTrue(any(name in error for error in errors), name)


if __name__ == "__main__":
    unittest.main()