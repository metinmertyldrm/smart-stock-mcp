"""Security wrapper for the Smart Stock LLM HTTP API.

`web_api` keeps the orchestration implementation and its internal X-Client-Id
owner contract. This module is the deployable ASGI entry point: it issues opaque
sessions, authenticates bearer tokens, and replaces any caller-supplied owner
header with the server-side session owner before the request reaches web_api.
"""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from security import SessionError, SessionStore
from web_api import app


PUBLIC_PATHS = {"/api/health", "/api/session"}
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


@app.post("/api/session", status_code=201)
async def create_session():
    return sessions.create()


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    try:
        owner_id = sessions.owner_for_authorization(request.headers.get("authorization"))
    except SessionError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Geçerli bir oturum gerekli."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.scope["headers"] = authenticated_headers(list(request.scope.get("headers", [])), owner_id)
    return await call_next(request)


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
)
