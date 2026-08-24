"""Security primitives for Smart Stock bearer sessions.

Bearer credentials remain opaque random tokens and only their SHA-256 digests
are persisted. Sessions may be anonymous (development compatibility) or bound
to a stable authenticated ``user_id`` for TASK 10 identity mode.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


DEFAULT_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
MIN_SESSION_TTL_SECONDS = 5 * 60
MAX_SESSION_TTL_SECONDS = 365 * 24 * 60 * 60
DEFAULT_SESSION_DB = os.getenv(
    "LLM_SESSIONS_DB", os.path.join(os.path.dirname(__file__), "sessions.db")
)


class SessionError(ValueError):
    """Raised when a bearer session is missing, malformed, unknown, or expired."""


@dataclass(frozen=True)
class SessionPrincipal:
    """Server-side identity resolved from one opaque bearer credential."""

    owner_id: str
    user_id: str | None = None

    @property
    def conversation_owner_id(self) -> str:
        return self.user_id or self.owner_id


def session_token_hash(token: str) -> str:
    if not isinstance(token, str) or len(token) < 32:
        raise SessionError("Session token is missing or too short")
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
        raise SessionError("Authorization header is required")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise SessionError("Authorization must use Bearer scheme")
    token = token.strip()
    session_token_hash(token)  # validate minimum entropy-bearing length
    return token


def is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    expiry = datetime.fromisoformat(expires_at)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return expiry <= current


def _configured_ttl() -> int:
    raw = os.getenv("LLM_SESSION_TTL_SECONDS")
    if raw is None:
        return DEFAULT_SESSION_TTL_SECONDS
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise ValueError("LLM_SESSION_TTL_SECONDS must be an integer") from exc
    if not MIN_SESSION_TTL_SECONDS <= ttl <= MAX_SESSION_TTL_SECONDS:
        raise ValueError(
            f"LLM_SESSION_TTL_SECONDS must be between {MIN_SESSION_TTL_SECONDS} and {MAX_SESSION_TTL_SECONDS}"
        )
    return ttl


class SessionStore:
    """SQLite-backed opaque-token store.

    Existing anonymous session databases are migrated in place by adding the
    nullable ``user_id`` column. A fresh connection is used per operation so the
    store remains safe across the ASGI event loop and worker threads.
    """

    def __init__(self, path: str = DEFAULT_SESSION_DB, ttl_seconds: int | None = None):
        self.path = path
        self.ttl_seconds = _configured_ttl() if ttl_seconds is None else ttl_seconds
        if not MIN_SESSION_TTL_SECONDS <= self.ttl_seconds <= MAX_SESSION_TTL_SECONDS:
            raise ValueError("Invalid session TTL")
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL UNIQUE,
                  user_id TEXT,
                  created_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions(expires_at);
                """
            )
            columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
            if "user_id" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id)")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, *, user_id: str | None = None) -> dict[str, str]:
        token, owner_id, created_at, expires_at = new_session_credentials(self.ttl_seconds)
        digest = session_token_hash(token)
        normalized_user_id = str(user_id).strip() if user_id is not None else None
        if normalized_user_id == "":
            raise ValueError("user_id cannot be empty")
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now(timezone.utc).isoformat(),))
            db.execute(
                "INSERT INTO sessions(token_hash,owner_id,user_id,created_at,expires_at,last_seen_at) VALUES(?,?,?,?,?,?)",
                (digest, owner_id, normalized_user_id, created_at, expires_at, created_at),
            )
        return {"token": token, "expiresAt": expires_at}

    def principal_for_authorization(self, authorization: str | None) -> SessionPrincipal:
        token = parse_bearer_token(authorization)
        digest = session_token_hash(token)
        current = datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute(
                "SELECT owner_id,user_id,expires_at FROM sessions WHERE token_hash=?", (digest,)
            ).fetchone()
            if row is None:
                raise SessionError("Unknown session")
            if is_expired(row["expires_at"], now=current):
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
                # Raising inside sqlite's context manager would roll this delete
                # back, so persist cleanup before returning the auth failure.
                db.commit()
                raise SessionError("Session expired")
            db.execute(
                "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
                (current.isoformat(), digest),
            )
            return SessionPrincipal(
                owner_id=str(row["owner_id"]),
                user_id=str(row["user_id"]) if row["user_id"] is not None else None,
            )

    def owner_for_authorization(self, authorization: str | None) -> str:
        """Compatibility helper returning the stable conversation owner."""
        return self.principal_for_authorization(authorization).conversation_owner_id

    def revoke(self, authorization: str | None) -> None:
        token = parse_bearer_token(authorization)
        digest = session_token_hash(token)
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))

    def revoke_user(self, user_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return int(cursor.rowcount)
