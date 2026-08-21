"""Lightweight observability primitives for the Smart Stock LLM host.

This module intentionally has no third-party dependency. It provides a stable
request-correlation and metrics contract that can later be exported to a
Prometheus/OpenTelemetry backend without coupling the application to one now.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


SERVICE_NAME = "smart-stock-llm-host"
_request_id: ContextVar[str | None] = ContextVar("smart_stock_request_id", default=None)
logger = logging.getLogger("smart_stock.observability")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_request_id(request_id: str):
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def normalize_route(path: str) -> str:
    """Bound metric cardinality for resource identifiers in HTTP paths."""
    if not path:
        return "/"
    parts = path.split("/")
    if len(parts) >= 4 and parts[1:3] == ["api", "conversations"]:
        parts[3] = "{conversation_id}"
    return "/".join(parts)


def emit_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured JSON event without request bodies or credentials."""
    payload = {
        "timestamp": now_iso(),
        "service": SERVICE_NAME,
        "event": event,
    }
    request_id = current_request_id()
    if request_id:
        payload["requestId"] = request_id
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    logger.log(level, json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


class MetricsRegistry:
    """Small process-local metrics registry with bounded route labels."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_clock = time.monotonic()
        self._http_total = 0
        self._http_active = 0
        self._http_errors = 0
        self._routes: dict[tuple[str, str, int], dict[str, float | int]] = defaultdict(
            lambda: {"count": 0, "durationMsTotal": 0.0, "durationMsMax": 0.0}
        )

    def reset(self) -> None:
        """Reset counters for deterministic tests; service uptime restarts too."""
        with self._lock:
            self._started_clock = time.monotonic()
            self._http_total = 0
            self._http_active = 0
            self._http_errors = 0
            self._routes.clear()

    def request_started(self) -> None:
        with self._lock:
            self._http_active += 1

    def request_finished(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        route = normalize_route(path)
        safe_duration = max(float(duration_ms), 0.0)
        key = (method.upper(), route, int(status_code))
        with self._lock:
            self._http_active = max(self._http_active - 1, 0)
            self._http_total += 1
            if status_code >= 500:
                self._http_errors += 1
            item = self._routes[key]
            item["count"] = int(item["count"]) + 1
            item["durationMsTotal"] = float(item["durationMsTotal"]) + safe_duration
            item["durationMsMax"] = max(float(item["durationMsMax"]), safe_duration)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = []
            for (method, route, status), values in sorted(self._routes.items()):
                count = int(values["count"])
                total = float(values["durationMsTotal"])
                routes.append(
                    {
                        "method": method,
                        "route": route,
                        "status": status,
                        "count": count,
                        "durationMsTotal": round(total, 3),
                        "durationMsAverage": round(total / count, 3) if count else 0.0,
                        "durationMsMax": round(float(values["durationMsMax"]), 3),
                    }
                )
            return {
                "service": SERVICE_NAME,
                "uptimeSeconds": round(max(time.monotonic() - self._started_clock, 0.0), 3),
                "http": {
                    "totalRequests": self._http_total,
                    "activeRequests": self._http_active,
                    "serverErrors": self._http_errors,
                    "routes": routes,
                },
            }


def readiness_snapshot(agent: Any) -> tuple[dict[str, Any], int]:
    """Return a side-effect-free readiness view of initialized local resources."""
    checks: dict[str, Any] = {}
    ready = True

    if agent is None:
        return {"status": "not_ready", "checks": {"agent": "missing"}}, 503

    checks["agent"] = "ok"

    try:
        agent.store.db.execute("SELECT 1").fetchone()
        checks["conversationStore"] = "ok"
    except Exception as exc:
        checks["conversationStore"] = {"status": "error", "type": type(exc).__name__}
        ready = False

    client = getattr(agent, "client", None)
    expected_servers = len(getattr(client, "servers", {}) or {}) if client is not None else 0
    connected_servers = len(getattr(client, "sessions", {}) or {}) if client is not None else 0
    tool_count = len(getattr(client, "tool_to_server", {}) or {}) if client is not None else 0
    mcp_ok = client is not None and expected_servers > 0 and connected_servers == expected_servers and tool_count > 0
    checks["mcp"] = {
        "status": "ok" if mcp_ok else "not_ready",
        "connectedServers": connected_servers,
        "expectedServers": expected_servers,
        "toolCount": tool_count,
    }
    ready = ready and mcp_ok

    return {"status": "ready" if ready else "not_ready", "checks": checks}, 200 if ready else 503


metrics = MetricsRegistry()
