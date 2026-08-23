"""Security and observability wrapper for the Smart Stock LLM HTTP API.

`web_api` keeps the orchestration implementation and its internal server-owned
identity contract. This deployable ASGI entry point supports two modes:

- ``anonymous`` for deterministic development/backward compatibility,
- ``local`` for TASK 10 stable user identity and role-based authorization.

In both modes caller-supplied owner/role headers are discarded before the
request reaches ``web_api``.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from identity import IdentityError, IdentityStore, ROLE_VIEWER
from observability import (
    correlate_response_payload,
    emit_event,
    metrics,
    normalize_route,
    persist_correlated_chat_response,
    readiness_snapshot,
    reset_request_id,
    set_request_id,
)
from security import DEFAULT_SESSION_DB, SessionError, SessionStore
from web_api import app


AUTH_MODE = os.getenv("LLM_AUTH_MODE", "anonymous").strip().casefold()
if AUTH_MODE not in {"anonymous", "local"}:
    raise RuntimeError("LLM_AUTH_MODE must be anonymous or local")

SESSION_PATH = os.getenv("LLM_SESSIONS_DB", DEFAULT_SESSION_DB)
IDENTITY_PATH = os.getenv("LLM_IDENTITY_DB", SESSION_PATH)
PUBLIC_PATHS = {"/api/health", "/api/ready", "/api/session", "/api/auth/login"}
CHAT_ROUTES = {"/api/chat", "/api/conversations/{conversation_id}/confirm"}
sessions = SessionStore(SESSION_PATH)
identities = IdentityStore(IDENTITY_PATH)
if AUTH_MODE == "local":
    identities.bootstrap_admin(
        os.getenv("LLM_BOOTSTRAP_ADMIN_USERNAME"),
        os.getenv("LLM_BOOTSTRAP_ADMIN_PASSWORD"),
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=512)
    displayName: str | None = Field(default=None, max_length=100)
    role: str = Field(default=ROLE_VIEWER, min_length=1, max_length=20)


class UpdateUserRequest(BaseModel):
    role: str | None = Field(default=None, min_length=1, max_length=20)
    enabled: bool | None = None


def authenticated_headers(
    raw_headers: list[tuple[bytes, bytes]], owner_id: str, role: str | None = None
) -> list[tuple[bytes, bytes]]:
    """Drop external credentials/identity hints and inject server-owned identity."""
    filtered = [
        (name, value)
        for name, value in raw_headers
        if name.lower()
        not in {b"authorization", b"x-client-id", b"x-client-role", b"x-client-capabilities"}
    ]
    filtered.append((b"x-client-id", owner_id.encode("utf-8")))
    if role:
        filtered.append((b"x-client-role", role.encode("utf-8")))
    return filtered


def current_identity(request: Request):
    identity = getattr(request.state, "identity", None)
    if AUTH_MODE != "local" or identity is None:
        raise HTTPException(401, "Kimliği doğrulanmış kullanıcı gerekli.")
    return identity


def require_capability(request: Request, capability: str):
    identity = current_identity(request)
    if not identity.has(capability):
        raise HTTPException(403, "Bu işlem için yetkiniz yok.")
    return identity


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
    if AUTH_MODE != "anonymous":
        raise HTTPException(404, "Anonim oturum bu ortamda kullanılamaz.")
    return JSONResponse(
        status_code=201,
        content=sessions.create(),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@app.post("/api/auth/login")
async def login(body: LoginRequest):
    if AUTH_MODE != "local":
        raise HTTPException(404, "Kullanıcı girişi bu ortamda etkin değil.")
    try:
        identity = identities.authenticate(body.username, body.password)
    except IdentityError:
        emit_event("security.login.rejected", level=logging.WARNING)
        raise HTTPException(401, "Kullanıcı adı veya parola hatalı.") from None
    session = sessions.create(user_id=identity.id)
    emit_event("security.login.succeeded", role=identity.role)
    return JSONResponse(
        content={**session, "user": identity.public_dict()},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@app.get("/api/auth/me")
async def me(request: Request):
    identity = current_identity(request)
    return {"user": identity.public_dict()}


@app.post("/api/auth/logout", status_code=204)
async def logout(request: Request):
    sessions.revoke(request.headers.get("authorization"))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/users")
async def list_users(request: Request):
    require_capability(request, "users")
    return {"items": [user.public_dict() for user in identities.list_users()]}


@app.post("/api/admin/users", status_code=201)
async def create_user(body: CreateUserRequest, request: Request):
    require_capability(request, "users")
    try:
        user = identities.create_user(
            body.username,
            body.password,
            display_name=body.displayName,
            role=body.role,
        )
    except IdentityError as exc:
        raise HTTPException(422, str(exc)) from exc
    return user.public_dict()


@app.post("/api/admin/users/{user_id}")
async def update_user(user_id: str, body: UpdateUserRequest, request: Request):
    actor = require_capability(request, "users")
    try:
        user = identities.get_user(user_id)
        if body.role is not None:
            user = identities.set_role(user_id, body.role)
        if body.enabled is not None:
            user = identities.set_enabled(user_id, body.enabled)
            if not body.enabled:
                sessions.revoke_user(user_id)
    except IdentityError as exc:
        raise HTTPException(422, str(exc)) from exc
    emit_event("security.user.updated", actorRole=actor.role, targetRole=user.role)
    return user.public_dict()


@app.get("/api/ready")
async def readiness():
    payload, status_code = readiness_snapshot(getattr(app.state, "agent", None))
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/metrics")
async def metric_snapshot(request: Request):
    """Return bounded process-local metrics; admin-only in local identity mode."""
    if AUTH_MODE == "local":
        require_capability(request, "metrics")
    return metrics.snapshot()


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    try:
        principal = sessions.principal_for_authorization(request.headers.get("authorization"))
        identity = None
        if AUTH_MODE == "local":
            if principal.user_id is None:
                raise SessionError("Anonymous session is not a user session")
            try:
                identity = identities.get_user(principal.user_id)
            except IdentityError as exc:
                raise SessionError("Unknown user") from exc
            if not identity.enabled:
                sessions.revoke_user(identity.id)
                raise SessionError("User disabled")
            owner_id = identity.id
            role = identity.role
            request.state.identity = identity
        else:
            owner_id = principal.owner_id
            role = None
            request.state.identity = None
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
    request.scope["headers"] = authenticated_headers(
        list(request.scope.get("headers", [])), owner_id, role
    )
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
