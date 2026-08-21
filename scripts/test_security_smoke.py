import unittest
from unittest.mock import patch

import security_smoke


TOKEN_A = "a" * 48
TOKEN_B = "b" * 48


class SecuritySmokeTests(unittest.TestCase):
    def test_issue_session_requires_valid_token(self):
        with patch.object(security_smoke, "request", return_value=(201, {"token": TOKEN_A})):
            self.assertEqual(TOKEN_A, security_smoke.issue_session("http://web/llm", 1.0))

        with patch.object(security_smoke, "request", return_value=(201, {"token": "short"})):
            with self.assertRaisesRegex(security_smoke.SecuritySmokeFailure, "Session issuance"):
                security_smoke.issue_session("http://web/llm", 1.0)

    def test_run_verifies_auth_isolation_and_read_only_proxy(self):
        tokens = iter([TOKEN_A, TOKEN_B])
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            headers = kwargs.get("headers") or {}
            if method == "GET" and url.endswith("/api/conversations") and not headers.get("Authorization"):
                return 401, {"detail": "auth required"}
            if method == "POST" and url.endswith("/api/session"):
                return 201, {"token": next(tokens)}
            if method == "POST" and url.endswith("/api/conversations"):
                self.assertEqual(f"Bearer {TOKEN_A}", headers.get("Authorization"))
                return 201, {"id": "conv-1"}
            if method == "GET" and url.endswith("/api/conversations/conv-1"):
                self.assertEqual(f"Bearer {TOKEN_B}", headers.get("Authorization"))
                return 404, {"detail": "not found"}
            if method == "DELETE" and url.endswith("/api/conversations/conv-1"):
                self.assertEqual(f"Bearer {TOKEN_A}", headers.get("Authorization"))
                return 204, None
            if method == "POST" and url.endswith("/stock/api/marketplace/orders"):
                return 405, "not allowed"
            raise AssertionError((method, url, kwargs))

        with patch.object(security_smoke, "request", side_effect=fake_request):
            security_smoke.run("http://web", timeout=1.0)

        spoof = calls[1]
        self.assertEqual("spoofed-owner", spoof[2]["headers"]["X-Client-Id"])

    def test_run_fails_if_stock_mutation_reaches_backend(self):
        tokens = iter([TOKEN_A, TOKEN_B])

        def fake_request(method, url, **kwargs):
            headers = kwargs.get("headers") or {}
            if method == "GET" and url.endswith("/api/conversations") and not headers.get("Authorization"):
                return 401, {}
            if method == "POST" and url.endswith("/api/session"):
                return 201, {"token": next(tokens)}
            if method == "POST" and url.endswith("/api/conversations"):
                return 201, {"id": "conv-1"}
            if method == "GET" and url.endswith("/api/conversations/conv-1"):
                return 404, {}
            if method == "DELETE":
                return 204, None
            if method == "POST" and url.endswith("/stock/api/marketplace/orders"):
                return 404, {"detail": "backend saw request"}
            raise AssertionError((method, url))

        with patch.object(security_smoke, "request", side_effect=fake_request):
            with self.assertRaisesRegex(security_smoke.SecuritySmokeFailure, "expected 405"):
                security_smoke.run("http://web", timeout=1.0)


if __name__ == "__main__":
    unittest.main()
