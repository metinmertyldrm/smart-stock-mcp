"""Cross-platform smoke verifier for the local Smart Stock Docker stack.

The default checks are intentionally deterministic and do not ask the LLM to
plan anything. Use --chat to add one read-only end-to-end LLM + MCP turn.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class SmokeConfig:
    stock_url: str
    llm_url: str
    web_url: str
    ollama_url: str
    model: str
    timeout: float
    chat_timeout: float
    retries: int
    retry_delay: float
    chat: bool


class SmokeFailure(RuntimeError):
    pass


def request_json(
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
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return response.status, None
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SmokeFailure(f"{method} {url} returned invalid JSON: {raw[:300]}") from exc
    except SmokeFailure:
        raise
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"{method} {url} -> HTTP {exc.code}: {raw[:300]}") from exc
    except OSError as exc:
        raise SmokeFailure(f"{method} {url} -> {type(exc).__name__}: {exc}") from exc


def request_text(url: str, *, timeout: float = 10.0) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "text/plain"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"GET {url} -> HTTP {exc.code}: {raw[:300]}") from exc
    except OSError as exc:
        raise SmokeFailure(f"GET {url} -> {type(exc).__name__}: {exc}") from exc


def with_retries(label: str, fn: Callable[[], Any], retries: int, delay: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except SmokeFailure as exc:
            last_error = exc
            if attempt < retries:
                print(f"  [WAIT] {label} ({attempt}/{retries}): {exc}")
                time.sleep(delay)
    raise SmokeFailure(f"{label} failed after {retries} attempts: {last_error}")


def check_stock(config: SmokeConfig) -> None:
    status, products = request_json("GET", f"{config.stock_url}/api/products", timeout=config.timeout)
    if status != 200 or not isinstance(products, list):
        raise SmokeFailure("Stock service /api/products did not return a JSON list")
    print(f"  [OK] Stock service (read-only gateway): {len(products)} products visible")


def check_ollama(config: SmokeConfig) -> None:
    status, result = request_json("GET", f"{config.ollama_url}/api/tags", timeout=config.timeout)
    if status != 200 or not isinstance(result, dict):
        raise SmokeFailure("Ollama /api/tags returned an unexpected response")
    models = [entry.get("name", "") for entry in result.get("models", []) if isinstance(entry, dict)]
    expected = config.model.casefold()
    if not any(name.casefold() == expected for name in models):
        raise SmokeFailure(f"Ollama is healthy but model {config.model!r} is not installed; models={models}")
    print(f"  [OK] Ollama: model {config.model} installed")


def check_llm_health(config: SmokeConfig) -> None:
    status, result = request_json("GET", f"{config.llm_url}/api/health", timeout=config.timeout)
    if status != 200 or not isinstance(result, dict) or result.get("status") != "ok":
        raise SmokeFailure("LLM host health endpoint did not report status=ok")
    print("  [OK] LLM host health through web gateway")


def check_web(config: SmokeConfig) -> None:
    status, body = request_text(f"{config.web_url}/healthz", timeout=config.timeout)
    if status != 200:
        raise SmokeFailure(f"Web UI health endpoint returned HTTP {status}")
    print(f"  [OK] Web UI health: {body.strip() or 'HTTP 200'}")


def session_headers(config: SmokeConfig) -> dict[str, str]:
    status, session = request_json("POST", f"{config.llm_url}/api/session", timeout=config.timeout)
    token = session.get("token") if isinstance(session, dict) else None
    if status != 201 or not isinstance(token, str) or len(token) < 32:
        raise SmokeFailure("LLM host did not issue a valid session token")
    return {"Authorization": f"Bearer {token}"}


def delete_conversation(config: SmokeConfig, conversation_id: str, headers: dict[str, str]) -> None:
    status, _ = request_json(
        "DELETE",
        f"{config.llm_url}/api/conversations/{conversation_id}",
        headers=headers,
        timeout=config.timeout,
    )
    if status != 204:
        raise SmokeFailure(f"Conversation cleanup returned HTTP {status}, expected 204")


def check_conversation_crud(config: SmokeConfig) -> None:
    headers = session_headers(config)
    status, created = request_json(
        "POST",
        f"{config.llm_url}/api/conversations",
        payload={"title": "Smoke test"},
        headers=headers,
        timeout=config.timeout,
    )
    if status != 201 or not isinstance(created, dict) or not created.get("id"):
        raise SmokeFailure("Conversation create did not return a conversation id")
    conversation_id = str(created["id"])

    try:
        status, fetched = request_json(
            "GET",
            f"{config.llm_url}/api/conversations/{conversation_id}",
            headers=headers,
            timeout=config.timeout,
        )
        if status != 200 or not isinstance(fetched, dict) or fetched.get("id") != conversation_id:
            raise SmokeFailure("Conversation fetch did not return the created conversation")

        status, listing = request_json(
            "GET",
            f"{config.llm_url}/api/conversations?limit=10&offset=0",
            headers=headers,
            timeout=config.timeout,
        )
        ids = [item.get("id") for item in listing.get("items", []) if isinstance(item, dict)] if isinstance(listing, dict) else []
        if status != 200 or conversation_id not in ids:
            raise SmokeFailure("Created conversation was not visible in owner-scoped listing")
    except Exception:
        try:
            delete_conversation(config, conversation_id, headers)
        except SmokeFailure:
            pass
        raise

    delete_conversation(config, conversation_id, headers)
    print("  [OK] Conversation persistence CRUD + authenticated owner scope")


def check_read_only_chat(config: SmokeConfig) -> None:
    headers = session_headers(config)
    status, created = request_json(
        "POST",
        f"{config.llm_url}/api/conversations",
        payload={"title": "Smoke chat"},
        headers=headers,
        timeout=config.timeout,
    )
    if status != 201 or not isinstance(created, dict) or not created.get("id"):
        raise SmokeFailure("Smoke chat conversation could not be created")
    conversation_id = str(created["id"])
    try:
        status, result = request_json(
            "POST",
            f"{config.llm_url}/api/chat",
            payload={"conversationId": conversation_id, "message": "Stokta olmayan ürünleri listele."},
            headers=headers,
            timeout=config.chat_timeout,
        )
        if status != 200 or not isinstance(result, dict):
            raise SmokeFailure("Read-only chat returned an unexpected response")
        if result.get("succeeded") is not True:
            raise SmokeFailure(f"Read-only chat did not succeed: {result.get('finalAnswer')}")
        if result.get("permissionLevel") != "PLAN":
            raise SmokeFailure(f"Read-only chat permission was {result.get('permissionLevel')!r}, expected 'PLAN'")
        forbidden = {"create_purchase_draft", "place_order", "create_incoming_order", "create_incoming_orders", "receive_order", "receive_orders"}
        tools = {step.get("tool") for step in result.get("trace", []) if isinstance(step, dict)}
        used_forbidden = sorted(tool for tool in tools if tool in forbidden)
        if used_forbidden:
            raise SmokeFailure(f"Read-only smoke chat used write tools: {used_forbidden}")
        print(f"  [OK] Read-only LLM + MCP chat ({len(tools)} tool types)")
    finally:
        try:
            delete_conversation(config, conversation_id, headers)
        except SmokeFailure as exc:
            print(f"  [WARN] Smoke chat cleanup: {exc}")


def parse_args(argv: list[str] | None = None) -> SmokeConfig:
    parser = argparse.ArgumentParser(description="Verify the running Smart Stock Docker stack")
    parser.add_argument("--stock-url", default=os.getenv("SMOKE_STOCK_URL", "http://localhost:5173/stock"))
    parser.add_argument("--llm-url", default=os.getenv("SMOKE_LLM_URL", "http://localhost:5173/llm"))
    parser.add_argument("--web-url", default=os.getenv("SMOKE_WEB_URL", "http://localhost:5173"))
    parser.add_argument("--ollama-url", default=os.getenv("SMOKE_OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen3:8b"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--chat-timeout", type=float, default=float(os.getenv("SMOKE_CHAT_TIMEOUT", "330")))
    parser.add_argument("--retries", type=int, default=12)
    parser.add_argument("--retry-delay", type=float, default=5.0)
    parser.add_argument("--chat", action="store_true", help="also run one real read-only LLM + MCP turn")
    args = parser.parse_args(argv)
    if args.retries < 1:
        parser.error("--retries must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.chat_timeout <= 0:
        parser.error("--chat-timeout must be greater than zero")
    if args.retry_delay < 0:
        parser.error("--retry-delay cannot be negative")
    return SmokeConfig(
        stock_url=args.stock_url.rstrip("/"),
        llm_url=args.llm_url.rstrip("/"),
        web_url=args.web_url.rstrip("/"),
        ollama_url=args.ollama_url.rstrip("/"),
        model=args.model,
        timeout=args.timeout,
        chat_timeout=args.chat_timeout,
        retries=args.retries,
        retry_delay=args.retry_delay,
        chat=args.chat,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    print("Smart Stock full-stack smoke verification")
    checks = [
        ("Stock service", lambda: check_stock(config)),
        ("Ollama", lambda: check_ollama(config)),
        ("LLM host", lambda: check_llm_health(config)),
        ("Web UI", lambda: check_web(config)),
        ("Conversation CRUD", lambda: check_conversation_crud(config)),
    ]
    try:
        for label, check in checks:
            with_retries(label, check, config.retries, config.retry_delay)
        if config.chat:
            # Chat is intentionally attempted once so a client timeout cannot overlap
            # a still-running server-side generation on a slow local machine.
            with_retries("Read-only LLM chat", lambda: check_read_only_chat(config), 1, config.retry_delay)
    except SmokeFailure as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    print("\n[PASS] Smart Stock stack smoke verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
