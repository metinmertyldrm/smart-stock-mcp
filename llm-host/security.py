"""Security primitives for anonymous LLM-host sessions.

The browser receives an opaque random bearer token. Only its SHA-256 digest is
stored server-side, so a leaked SQLite file does not immediately reveal active
session credentials.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone


DEFAULT_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
MIN_SESSION_TTL_SECONDS = 5 * 60
MAX_SESSION_TTL_SECONDS = 365 * 24 * 60 * 60


def session_token_hash(token: str) -> str:
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Session token is missing or too short")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_credentials(ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS) -> tuple[str, str, str, str]:
    """Return ``token, owner_id, created_at, expires_at`` for a new session."""
    if not MIN_SESSION_TTL_SECONDS <= ttl_seconds <= MAX_SESSION_TTL_SECONDS:
        raise ValueError(
            f"Session TTL must be between {MIN_SESSION_TTL_SECONDS} and {MAX_SESSION_TTL_SECONDS} seconds"
        )
    token = secrets.token_urlsafe(32)
    owner_id = secrets.token_urlsafe(24)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(seconds=ttl_seconds)
    return token, owner_id, created.isoformat(), expires.isoformat()


def parse_bearer_token(authorization: str | None) -> str:
    """Extract an opaque bearer token without accepting alternate auth schemes."""
    if not authorization:
        raise ValueError("Authorization header is required")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise ValueError("Authorization must use Bearer scheme")
    token = token.strip()
    session_token_hash(token)  # validate minimum entropy-bearing length
    return token


def is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    expiry = datetime.fromisoformat(expires_at)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return expiry <= current
