"""Deterministic security smoke checks for the Docker web gateway.

No Ollama call is made. The checks focus on HTTP/session isolation and on the
read-only stock proxy that prevents browser-side mutation bypasses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


class SecuritySmokeFailure(RuntimeError):
    pass


def request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed
    except OSError as exc:
        raise SecuritySmokeFailure(f"{method} {url} -> {type(exc).__name__}: {exc}") from exc


def issue_session(llm_url: str, timeout: float) -> str:
    status, body = request("POST", f"{llm_url}/api/session", timeout=timeout)
    token = body.get("token") if isinstance(body, dict) else None
    if status != 201 or not isinstance(token, str) or len(token) < 32:
        raise SecuritySmokeFailure(f"Session issuance failed: HTTP {status}")
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def run(web_url: str, timeout: float = 10.0) -> None:
    llm_url = f"{web_url.rstrip('/')}/llm"
    stock_url = f"{web_url.rstrip('/')}/stock"

    status, _ = request("GET", f"{llm_url}/api/conversations", timeout=timeout)
    if status != 401:
        raise SecuritySmokeFailure(f"Unauthenticated LLM route returned HTTP {status}, expected 401")
    print("  [OK] Protected LLM route rejects missing bearer session")

    status, _ = request(
        "GET",
        f"{llm_url}/api/conversations",
        headers={"X-Client-Id": "spoofed-owner"},
        timeout=timeout,
    )
    if status != 401:
        raise SecuritySmokeFailure(f"Spoofed X-Client-Id returned HTTP {status}, expected 401")
    print("  [OK] Caller-supplied X-Client-Id cannot impersonate an owner")

    token_a = issue_session(llm_url, timeout)
    token_b = issue_session(llm_url, timeout)
    status, conversation = request(
        "POST",
        f"{llm_url}/api/conversations",
        payload={"title": "Security smoke"},
        headers=auth(token_a),
        timeout=timeout,
    )
    conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
    if status != 201 or not conversation_id:
        raise SecuritySmokeFailure(f"Authenticated conversation create failed: HTTP {status}")

    try:
        status, _ = request(
            "GET",
            f"{llm_url}/api/conversations/{conversation_id}",
            headers=auth(token_b),
            timeout=timeout,
        )
        if status != 404:
            raise SecuritySmokeFailure(
                f"Cross-session conversation access returned HTTP {status}, expected 404"
            )
        print("  [OK] Separate bearer sessions cannot read each other's conversations")
    finally:
        request(
            "DELETE",
            f"{llm_url}/api/conversations/{conversation_id}",
            headers=auth(token_a),
            timeout=timeout,
        )

    # Invalid draft id makes the request harmless even if a proxy regression
    # accidentally forwards it. The hardened gateway itself must reject it first.
    status, _ = request(
        "POST",
        f"{stock_url}/api/marketplace/orders",
        payload={"draftId": -1},
        timeout=timeout,
    )
    if status != 405:
        raise SecuritySmokeFailure(f"Browser stock mutation returned HTTP {status}, expected 405")
    print("  [OK] Browser-facing stock gateway blocks mutation methods")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Smart Stock security boundaries")
    parser.add_argument("--web-url", default=os.getenv("SMOKE_WEB_URL", "http://localhost:5173"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    print("Smart Stock security smoke verification")
    try:
        run(args.web_url, args.timeout)
    except SecuritySmokeFailure as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    print("\n[PASS] Smart Stock security boundaries verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
