import unittest
from unittest.mock import patch

import observability_smoke


TOKEN = "a" * 48
REQUEST_ID = "r" * 36
HEADERS = {"X-Request-Id": REQUEST_ID}


class ObservabilitySmokeTests(unittest.TestCase):
    def test_request_id_is_required(self):
        with self.assertRaisesRegex(observability_smoke.ObservabilitySmokeFailure, "request ID"):
            observability_smoke.require_request_id({}, "Health")
        self.assertEqual(REQUEST_ID, observability_smoke.require_request_id(HEADERS, "Health"))

    def test_run_verifies_readiness_metrics_and_auth(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            headers = kwargs.get("headers") or {}
            if method == "GET" and url.endswith("/api/health"):
                return 200, {"status": "ok"}, HEADERS
            if method == "GET" and url.endswith("/api/ready"):
                return 200, {
                    "status": "ready",
                    "checks": {"mcp": {"status": "ok", "connectedServers": 2, "toolCount": 20}},
                }, HEADERS
            if method == "GET" and url.endswith("/api/metrics") and not headers.get("Authorization"):
                return 401, {"detail": "auth required"}, HEADERS
            if method == "POST" and url.endswith("/api/session"):
                return 201, {"token": TOKEN}, HEADERS
            if method == "GET" and url.endswith("/api/metrics"):
                self.assertEqual(f"Bearer {TOKEN}", headers.get("Authorization"))
                return 200, {
                    "http": {
                        "totalRequests": 4,
                        "routes": [
                            {"route": "/api/health", "count": 1},
                            {"route": "/api/ready", "count": 1},
                            {"route": "/api/metrics", "count": 1},
                        ],
                    },
                    "chat": {"total": 0},
                    "tools": [],
                }, HEADERS
            raise AssertionError((method, url, kwargs))

        with patch.object(observability_smoke, "request", side_effect=fake_request):
            observability_smoke.run("http://web", timeout=1.0)

        self.assertEqual(5, len(calls))

    def test_expect_chat_requires_chat_and_tool_aggregates(self):
        def fake_request(method, url, **kwargs):
            headers = kwargs.get("headers") or {}
            if url.endswith("/api/health"):
                return 200, {"status": "ok"}, HEADERS
            if url.endswith("/api/ready"):
                return 200, {
                    "status": "ready",
                    "checks": {"mcp": {"status": "ok", "connectedServers": 2, "toolCount": 20}},
                }, HEADERS
            if url.endswith("/api/metrics") and not headers.get("Authorization"):
                return 401, {}, HEADERS
            if url.endswith("/api/session"):
                return 201, {"token": TOKEN}, HEADERS
            if url.endswith("/api/metrics"):
                return 200, {
                    "http": {
                        "totalRequests": 8,
                        "routes": [{"route": "/api/health"}, {"route": "/api/ready"}],
                    },
                    "chat": {"total": 1},
                    "tools": [{"tool": "list_out_of_stock", "status": "success", "count": 1}],
                }, HEADERS
            raise AssertionError((method, url))

        with patch.object(observability_smoke, "request", side_effect=fake_request):
            observability_smoke.run("http://web", timeout=1.0, expect_chat=True)

    def test_expect_chat_fails_when_metrics_are_empty(self):
        def fake_request(method, url, **kwargs):
            headers = kwargs.get("headers") or {}
            if url.endswith("/api/health"):
                return 200, {"status": "ok"}, HEADERS
            if url.endswith("/api/ready"):
                return 200, {
                    "status": "ready",
                    "checks": {"mcp": {"status": "ok", "connectedServers": 2, "toolCount": 20}},
                }, HEADERS
            if url.endswith("/api/metrics") and not headers.get("Authorization"):
                return 401, {}, HEADERS
            if url.endswith("/api/session"):
                return 201, {"token": TOKEN}, HEADERS
            if url.endswith("/api/metrics"):
                return 200, {
                    "http": {
                        "totalRequests": 4,
                        "routes": [{"route": "/api/health"}, {"route": "/api/ready"}],
                    },
                    "chat": {"total": 0},
                    "tools": [],
                }, HEADERS
            raise AssertionError((method, url))

        with patch.object(observability_smoke, "request", side_effect=fake_request):
            with self.assertRaisesRegex(observability_smoke.ObservabilitySmokeFailure, "recorded chat"):
                observability_smoke.run("http://web", timeout=1.0, expect_chat=True)


if __name__ == "__main__":
    unittest.main()
