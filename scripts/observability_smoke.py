"""Deterministic observability smoke checks for the secured Docker gateway.

The default checks do not invoke Ollama. Use ``--expect-chat`` after the normal
``smoke_stack.py --chat`` run to also require populated chat/tool aggregates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


class ObservabilitySmokeFailure(RuntimeError):
    pass


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any, dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw else None, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed, dict(exc.headers.items())
    except OSError as exc:
        raise ObservabilitySmokeFailure(f"{method} {url} -> {type(exc).__name__}: {exc}") from exc


def header(headers: dict[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if key.casefold() == target:
            return value
    return None


def require_request_id(headers: dict[str, str], label: str) -> str:
    request_id = header(headers, "X-Request-Id")
    if not isinstance(request_id, str) or len(request_id) < 32:
        raise ObservabilitySmokeFailure(f"{label} response is missing a server request ID")
    return request_id


def issue_session(llm_url: str, timeout: float) -> str:
    status, body, headers = request("POST", f"{llm_url}/api/session", timeout=timeout)
    require_request_id(headers, "Session")
    token = body.get("token") if isinstance(body, dict) else None
    if status != 201 or not isinstance(token, str) or len(token) < 32:
        raise ObservabilitySmokeFailure(f"Session issuance failed: HTTP {status}")
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def run(web_url: str, timeout: float = 10.0, expect_chat: bool = False) -> None:
    llm_url = f"{web_url.rstrip('/')}/llm"

    status, health, headers = request("GET", f"{llm_url}/api/health", timeout=timeout)
    require_request_id(headers, "Health")
    if status != 200 or not isinstance(health, dict) or health.get("status") != "ok":
        raise ObservabilitySmokeFailure(f"Liveness endpoint failed: HTTP {status}")
    print("  [OK] Liveness response carries X-Request-Id")

    status, ready, headers = request("GET", f"{llm_url}/api/ready", timeout=timeout)
    require_request_id(headers, "Readiness")
    mcp = (ready.get("checks") or {}).get("mcp") if isinstance(ready, dict) else None
    if status != 200 or ready.get("status") != "ready" or not isinstance(mcp, dict) or mcp.get("status") != "ok":
        raise ObservabilitySmokeFailure(f"Readiness endpoint is not ready: HTTP {status}, body={ready}")
    print(
        "  [OK] Readiness verifies conversation store + "
        f"{mcp.get('connectedServers')} MCP servers / {mcp.get('toolCount')} tools"
    )

    status, _, headers = request("GET", f"{llm_url}/api/metrics", timeout=timeout)
    require_request_id(headers, "Unauthenticated metrics")
    if status != 401:
        raise ObservabilitySmokeFailure(f"Unauthenticated metrics returned HTTP {status}, expected 401")
    print("  [OK] Metrics endpoint remains behind Bearer authentication")

    token = issue_session(llm_url, timeout)
    status, body, headers = request(
        "GET",
        f"{llm_url}/api/metrics",
        headers=auth(token),
        timeout=timeout,
    )
    require_request_id(headers, "Metrics")
    if status != 200 or not isinstance(body, dict):
        raise ObservabilitySmokeFailure(f"Authenticated metrics failed: HTTP {status}")

    http = body.get("http") or {}
    routes = http.get("routes") if isinstance(http, dict) else None
    if not isinstance(routes, list) or int(http.get("totalRequests", 0)) < 4:
        raise ObservabilitySmokeFailure("HTTP metrics did not aggregate the preceding gateway requests")
    observed_routes = {item.get("route") for item in routes if isinstance(item, dict)}
    if "/api/health" not in observed_routes or "/api/ready" not in observed_routes:
        raise ObservabilitySmokeFailure(f"Expected health/readiness routes in metrics, got {sorted(observed_routes)}")
    print(f"  [OK] HTTP metrics aggregate {http.get('totalRequests')} completed requests")

    if expect_chat:
        chat = body.get("chat") or {}
        tools = body.get("tools")
        if int(chat.get("total", 0)) < 1:
            raise ObservabilitySmokeFailure("Expected at least one recorded chat after --chat smoke")
        if not isinstance(tools, list) or not any(int(item.get("count", 0)) > 0 for item in tools if isinstance(item, dict)):
            raise ObservabilitySmokeFailure("Expected at least one aggregated tool execution after --chat smoke")
        print(
            "  [OK] Chat/tool aggregates populated: "
            f"{chat.get('total')} chats, {len(tools)} tool/status series"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Smart Stock observability boundaries")
    parser.add_argument("--web-url", default=os.getenv("SMOKE_WEB_URL", "http://localhost:5173"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--expect-chat", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    print("Smart Stock observability smoke verification")
    try:
        run(args.web_url, args.timeout, args.expect_chat)
    except ObservabilitySmokeFailure as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    print("\n[PASS] Smart Stock observability boundaries verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
