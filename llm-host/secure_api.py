"""Security and observability wrapper for the Smart Stock LLM HTTP API.

`web_api` keeps the orchestration implementation and its internal X-Client-Id
owner contract. This module is the deployable ASGI entry point: it issues opaque
sessions, authenticates bearer tokens, injects server-owned identity, and adds
request correlation plus process-local operational metrics.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from observability import (
    correlate_response_payload,
    emit_event,
    metrics,
    normalize_route,
    readiness_snapshot,
    reset_request_id,
    set_request_id,
)
from security import SessionError, SessionStore
from web_api import app


PUBLIC_PATHS = {"/api/health", "/api/ready", "/api/session"}
CHAT_ROUTES = {"/api/chat", "/api/conversations/{conversation_id}/confirm"}
sessions = SessionStore()


def authenticated_headers(raw_headers: list[tuple[bytes, bytes]], owner_id: str) -> list[tuple[bytes, bytes]]:
    """Drop external credentials/owner hints and inject the authenticated owner."""
    filtered = [
        (name, value)
        for name, value in raw_headers
        if name.lower() not in {b"authorization", b"x-client-id"}
    ]
    filtered.append((b"x-client-id", owner_id.encode("utf-8")))
    return filtered


def persist_correlated_chat_response(payload, store) -> bool:
    """Persist middleware-added correlation into the matching assistant audit record.

    `web_api` writes the response before this outer middleware can attach the HTTP
    request ID. Matching by conversation + execution ID avoids overwriting a
    different concurrent chat response.
    """
    if not isinstance(payload, dict):
        return False
    conversation_id = payload.get("conversationId")
    telemetry = payload.get("telemetry")
    execution_id = telemetry.get("executionId") if isinstance(telemetry, dict) else None
    db = getattr(store, "db", None)
    if not conversation_id or not execution_id or db is None:
        return False

    rows = db.execute(
        "SELECT id,response_json FROM messages "
        "WHERE conversation_id=? AND role='assistant' AND response_json IS NOT NULL "
        "ORDER BY created_at DESC,id DESC LIMIT 10",
        (conversation_id,),
    ).fetchall()
    for row in rows:
        try:
            stored = json.loads(row["response_json"])
        except (TypeError, ValueError, KeyError):
            continue
        stored_telemetry = stored.get("telemetry") if isinstance(stored, dict) else None
        if not isinstance(stored_telemetry, dict) or stored_telemetry.get("executionId") != execution_id:
            continue
        db.execute(
            "UPDATE messages SET response_json=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
        db.commit()
        return True
    return False


async def correlate_chat_response(response, request_id: str):
    """Attach HTTP correlation to JSON chat telemetry and aggregate its trace."""
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type.casefold() or not hasattr(response, "body_iterator"):
        return response

    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk))
    raw_body = b"".join(chunks)

    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers.pop("content-type", None)
    background = getattr(response, "background", None)

    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return Response(
            content=raw_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
            background=background,
        )

    correlated = correlate_response_payload(payload, request_id)
    if correlated:
        try:
            persist_correlated_chat_response(payload, getattr(app.state.agent, "store", None))
        except Exception as exc:
            # Correlation persistence is observability-only; never fail a valid chat.
            emit_event(
                "chat.correlation.persistence_failed",
                level=logging.WARNING,
                errorType=type(exc).__name__,
            )
    metrics.record_chat_response(payload)
    return JSONResponse(
        status_code=response.status_code,
        content=payload,
        headers=headers,
        background=background,
    )


@app.post("/api/session", status_code=201)
async def create_session():
    return JSONResponse(
        status_code=201,
        content=sessions.create(),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@app.get("/api/ready")
async def readiness():
    payload, status_code = readiness_snapshot(getattr(app.state, "agent", None))
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/metrics")
async def metric_snapshot():
    """Return bounded process-local metrics; protected by the Bearer boundary."""
    return metrics.snapshot()


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    try:
        owner_id = sessions.owner_for_authorization(request.headers.get("authorization"))
    except SessionError:
        emit_event(
            "security.session.rejected",
            level=logging.WARNING,
            method=request.method,
            route=normalize_route(request.url.path),
        )
        return JSONResponse(
            status_code=401,
            content={"detail": "Geçerli bir oturum gerekli."},
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
    request.scope["headers"] = authenticated_headers(list(request.scope.get("headers", [])), owner_id)
    return await call_next(request)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    """Correlate and measure every request without logging bodies or credentials."""
    request_id = str(uuid.uuid4())
    route = normalize_route(request.url.path)
    token = set_request_id(request_id)
    metrics.request_started()
    started = time.perf_counter()
    status_code = 500
    emit_event("http.request.started", method=request.method, route=route)
    try:
        response = await call_next(request)
        status_code = response.status_code
        if route in CHAT_ROUTES and status_code < 500:
            response = await correlate_chat_response(response, request_id)
        response.headers["X-Request-Id"] = request_id
        return response
    except Exception as exc:
        emit_event(
            "http.request.failed",
            level=logging.ERROR,
            method=request.method,
            route=route,
            errorType=type(exc).__name__,
        )
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        metrics.request_finished(request.method, route, status_code, duration_ms)
        emit_event(
            "http.request.completed",
            method=request.method,
            route=route,
            status=status_code,
            durationMs=duration_ms,
        )
        reset_request_id(token)


# `web_api` already has a restrictive CORS layer for direct/internal tests. The
# deployable wrapper adds the Authorization header to browser preflight policy.
origins = [
    value.strip()
    for value in os.getenv("LLM_CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Request-Id"],
)
