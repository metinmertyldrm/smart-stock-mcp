import json
import logging
import sqlite3
import unittest
from types import SimpleNamespace

from observability import (
    MetricsRegistry,
    correlate_response_payload,
    current_request_id,
    emit_event,
    logger,
    normalize_route,
    readiness_snapshot,
    reset_request_id,
    set_request_id,
)


class RequestCorrelationTest(unittest.TestCase):
    def test_structured_logger_has_dedicated_info_stream(self):
        self.assertFalse(logger.propagate)
        self.assertLessEqual(logger.getEffectiveLevel(), logging.INFO)
        self.assertTrue(logger.handlers)
        self.assertTrue(any(handler.level <= logging.INFO for handler in logger.handlers))

    def test_request_context_is_scoped_and_structured_log_includes_id(self):
        token = set_request_id("req-123")
        try:
            self.assertEqual(current_request_id(), "req-123")
            with self.assertLogs("smart_stock.observability", level=logging.INFO) as captured:
                emit_event("test.event", route="/api/health", status=200)
            payload = json.loads(captured.output[-1].split(":", 2)[-1])
            self.assertEqual(payload["event"], "test.event")
            self.assertEqual(payload["requestId"], "req-123")
            self.assertEqual(payload["route"], "/api/health")
        finally:
            reset_request_id(token)
        self.assertIsNone(current_request_id())

    def test_conversation_ids_do_not_create_metric_cardinality(self):
        self.assertEqual(
            normalize_route("/api/conversations/abc-123"),
            "/api/conversations/{conversation_id}",
        )
        self.assertEqual(
            normalize_route("/api/conversations/abc-123/confirm"),
            "/api/conversations/{conversation_id}/confirm",
        )
        self.assertEqual(normalize_route("/api/health"), "/api/health")

    def test_chat_telemetry_receives_http_request_id(self):
        payload = {
            "telemetry": {
                "executionId": "exec-1",
                "missingFields": ["HTTP request ID", "tool sürümü"],
            }
        }
        changed = correlate_response_payload(payload, "req-456")
        self.assertTrue(changed)
        self.assertEqual(payload["telemetry"]["requestId"], "req-456")
        self.assertEqual(payload["telemetry"]["missingFields"], ["tool sürümü"])

    def test_payload_without_telemetry_is_left_alone(self):
        payload = {"status": "ok"}
        self.assertFalse(correlate_response_payload(payload, "req-456"))
        self.assertEqual(payload, {"status": "ok"})


class MetricsRegistryTest(unittest.TestCase):
    def test_http_counts_latency_and_server_errors_are_aggregated(self):
        registry = MetricsRegistry()
        registry.request_started()
        registry.request_finished("GET", "/api/conversations/a", 200, 10.0)
        registry.request_started()
        registry.request_finished("GET", "/api/conversations/b", 200, 30.0)
        registry.request_started()
        registry.request_finished("POST", "/api/chat", 500, 8.0)

        snapshot = registry.snapshot()
        self.assertEqual(snapshot["http"]["totalRequests"], 3)
        self.assertEqual(snapshot["http"]["activeRequests"], 0)
        self.assertEqual(snapshot["http"]["serverErrors"], 1)

        conversation = next(
            item
            for item in snapshot["http"]["routes"]
            if item["route"] == "/api/conversations/{conversation_id}"
        )
        self.assertEqual(conversation["count"], 2)
        self.assertEqual(conversation["durationMsAverage"], 20.0)
        self.assertEqual(conversation["durationMsMax"], 30.0)

    def test_chat_and_tool_metrics_use_existing_response_trace(self):
        registry = MetricsRegistry()
        registry.record_chat_response(
            {
                "conversationId": "conv-1",
                "succeeded": True,
                "plan": {"goal": "INFO"},
                "explanation": {"repaired": True},
                "telemetry": {"durationMs": 120.0},
                "trace": [
                    {"tool": "list_low_stock", "status": "success", "durationMs": 25.0},
                    {"tool": "create_procurement_plan", "status": "success", "durationMs": 40.0},
                ],
            }
        )
        registry.record_chat_response(
            {
                "conversationId": "conv-2",
                "succeeded": False,
                "plan": {"goal": "PLAN"},
                "explanation": {"repaired": False},
                "telemetry": {"durationMs": 80.0},
                "trace": [
                    {"tool": "list_low_stock", "status": "failed", "durationMs": 10.0},
                ],
            }
        )

        snapshot = registry.snapshot()
        self.assertEqual(snapshot["chat"]["total"], 2)
        self.assertEqual(snapshot["chat"]["succeeded"], 1)
        self.assertEqual(snapshot["chat"]["failed"], 1)
        self.assertEqual(snapshot["chat"]["repaired"], 1)
        self.assertEqual(snapshot["chat"]["durationMsAverage"], 100.0)
        self.assertEqual(snapshot["chat"]["goals"], {"INFO": 1, "PLAN": 1})

        low_stock = [item for item in snapshot["tools"] if item["tool"] == "list_low_stock"]
        self.assertEqual(len(low_stock), 2)
        successful = next(item for item in low_stock if item["status"] == "success")
        failed = next(item for item in low_stock if item["status"] == "failed")
        self.assertEqual(successful["durationMsAverage"], 25.0)
        self.assertEqual(failed["durationMsAverage"], 10.0)

    def test_active_request_counter_never_goes_negative(self):
        registry = MetricsRegistry()
        registry.request_finished("GET", "/api/health", 200, 1.0)
        self.assertEqual(registry.snapshot()["http"]["activeRequests"], 0)


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_ready_requires_store_and_all_mcp_servers(self):
        agent = SimpleNamespace(
            store=SimpleNamespace(db=self.db),
            client=SimpleNamespace(
                servers={"stock-server": "stock.py", "marketplace-server": "market.py"},
                sessions={"stock-server": object(), "marketplace-server": object()},
                tool_to_server={"list_products": "stock-server", "search_offers": "marketplace-server"},
            ),
        )
        payload, status = readiness_snapshot(agent)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["checks"]["mcp"]["connectedServers"], 2)
        self.assertEqual(payload["checks"]["mcp"]["toolCount"], 2)

    def test_missing_agent_is_not_ready(self):
        payload, status = readiness_snapshot(None)
        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "not_ready")

    def test_partial_mcp_connection_is_not_ready(self):
        agent = SimpleNamespace(
            store=SimpleNamespace(db=self.db),
            client=SimpleNamespace(
                servers={"stock-server": "stock.py", "marketplace-server": "market.py"},
                sessions={"stock-server": object()},
                tool_to_server={"list_products": "stock-server"},
            ),
        )
        payload, status = readiness_snapshot(agent)
        self.assertEqual(status, 503)
        self.assertEqual(payload["checks"]["mcp"]["status"], "not_ready")


if __name__ == "__main__":
    unittest.main()
