"""Security primitives for Smart Stock sessions.

Session credentials remain opaque random tokens and only their SHA-256 digests
are persisted. Anonymous development sessions continue to support bearer
transport, while local identity mode resolves a CSRF-enabled opaque credential
from an HttpOnly cookie. Legacy bearer sessions are not accepted as local cookie
sessions after migration.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import contextmanager
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
    """Raised when a session credential is missing, malformed, unknown, or expired."""


class CsrfError(ValueError):
    """Raised when a cookie-authenticated mutation has no valid CSRF proof."""


@dataclass(frozen=True)
class SessionPrincipal:
    """Server-side identity resolved from one opaque session credential."""

    owner_id: str
    user_id: str | None = None

    @property
    def conversation_owner_id(self) -> str:
        return self.user_id or self.owner_id


def session_token_hash(token: str) -> str:
    if not isinstance(token, str) or len(token) < 32:
        raise SessionError("Session token is missing or too short")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token_hash(token: str) -> str:
    if not isinstance(token, str) or len(token) < 24:
        raise CsrfError("CSRF token is missing or too short")
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


def parse_session_token(token: str | None) -> str:
    """Validate one opaque session credential independent of its transport."""
    if token is None:
        raise SessionError("Session token is required")
    normalized = token.strip()
    session_token_hash(normalized)
    return normalized


def parse_bearer_token(authorization: str | None) -> str:
    """Extract an opaque bearer token without accepting alternate auth schemes."""
    if not authorization:
        raise SessionError("Authorization header is required")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise SessionError("Authorization must use Bearer scheme")
    return parse_session_token(token)


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
    """SQLite-backed opaque-token store with in-place schema migration."""

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
                  csrf_hash TEXT,
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
            if "csrf_hash" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN csrf_hash TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id)")

    @contextmanager
    def _connect(self):
        """Her islem icin baglanti acar ve KAPATIR.

        `with sqlite3.connect(...) as db` yalnizca islemi (commit/rollback)
        yonetir, baglantiyi kapatmaz. Kapanmayan baglanti dosya kilidini
        tutuyor; Windows'ta veritabani dosyasi silinemiyor (WinError 32) ve
        es zamanli istekte "database is locked" riski doguyor.
        """
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:          # islem siniri: commit ya da rollback
                yield connection
        finally:
            connection.close()        # dosya kilidi birakilir

    def create(self, *, user_id: str | None = None, with_csrf: bool = False) -> dict[str, str]:
        token, owner_id, created_at, expires_at = new_session_credentials(self.ttl_seconds)
        digest = session_token_hash(token)
        normalized_user_id = str(user_id).strip() if user_id is not None else None
        if normalized_user_id == "":
            raise ValueError("user_id cannot be empty")
        csrf_token = secrets.token_urlsafe(24) if with_csrf else None
        csrf_digest = csrf_token_hash(csrf_token) if csrf_token is not None else None
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now(timezone.utc).isoformat(),))
            db.execute(
                "INSERT INTO sessions(token_hash,owner_id,user_id,csrf_hash,created_at,expires_at,last_seen_at) VALUES(?,?,?,?,?,?,?)",
                (digest, owner_id, normalized_user_id, csrf_digest, created_at, expires_at, created_at),
            )
        payload = {"token": token, "expiresAt": expires_at}
        if csrf_token is not None:
            payload["csrfToken"] = csrf_token
        return payload

    def _row_for_token(self, token: str) -> sqlite3.Row:
        normalized = parse_session_token(token)
        digest = session_token_hash(normalized)
        current = datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute(
                "SELECT owner_id,user_id,csrf_hash,expires_at FROM sessions WHERE token_hash=?", (digest,)
            ).fetchone()
            if row is None:
                raise SessionError("Unknown session")
            if is_expired(row["expires_at"], now=current):
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
                db.commit()
                raise SessionError("Session expired")
            db.execute(
                "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
                (current.isoformat(), digest),
            )
            return row

    @staticmethod
    def _principal_from_row(row: sqlite3.Row) -> SessionPrincipal:
        return SessionPrincipal(
            owner_id=str(row["owner_id"]),
            user_id=str(row["user_id"]) if row["user_id"] is not None else None,
        )

    def principal_for_token(self, token: str | None) -> SessionPrincipal:
        normalized = parse_session_token(token)
        return self._principal_from_row(self._row_for_token(normalized))

    def principal_for_cookie(self, token: str | None) -> SessionPrincipal:
        """Resolve only sessions minted for the cookie/CSRF transport."""
        normalized = parse_session_token(token)
        row = self._row_for_token(normalized)
        if not row["csrf_hash"]:
            raise SessionError("Legacy session is not cookie-enabled")
        return self._principal_from_row(row)

    def principal_for_authorization(self, authorization: str | None) -> SessionPrincipal:
        return self.principal_for_token(parse_bearer_token(authorization))

    def owner_for_authorization(self, authorization: str | None) -> str:
        """Compatibility helper returning the stable conversation owner."""
        return self.principal_for_authorization(authorization).conversation_owner_id

    def validate_csrf(self, token: str | None, csrf_token: str | None) -> None:
        normalized = parse_session_token(token)
        provided_hash = csrf_token_hash(csrf_token or "")
        row = self._row_for_token(normalized)
        expected_hash = row["csrf_hash"]
        if not expected_hash or not hmac.compare_digest(str(expected_hash), provided_hash):
            raise CsrfError("Invalid CSRF token")

    def revoke_token(self, token: str | None) -> None:
        normalized = parse_session_token(token)
        digest = session_token_hash(normalized)
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))

    def revoke(self, authorization: str | None) -> None:
        self.revoke_token(parse_bearer_token(authorization))

    def revoke_user(self, user_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return int(cursor.rowcount)
