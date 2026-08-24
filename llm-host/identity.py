"""Local identity and role primitives for Smart Stock.

TASK 10 introduces stable user identities without coupling the core application to
an external IdP. Passwords are never stored directly; each credential uses
stdlib scrypt with a per-user random salt. Session issuance remains a separate
concern in ``security.py`` so identity storage and bearer-token handling can be
tested independently.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_IDENTITY_DB = os.getenv(
    "LLM_IDENTITY_DB",
    os.getenv("LLM_SESSIONS_DB", os.path.join(os.path.dirname(__file__), "sessions.db")),
)

ROLE_VIEWER = "VIEWER"
ROLE_OPERATOR = "OPERATOR"
ROLE_MANAGER = "MANAGER"
ROLE_ADMIN = "ADMIN"
ROLES = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_MANAGER, ROLE_ADMIN)

ROLE_CAPABILITIES = {
    ROLE_VIEWER: frozenset({"read"}),
    ROLE_OPERATOR: frozenset({"read", "draft"}),
    ROLE_MANAGER: frozenset({"read", "draft", "confirm"}),
    ROLE_ADMIN: frozenset({"read", "draft", "confirm", "metrics", "users"}),
}

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
PASSWORD_MIN_LENGTH = 12
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
_DUMMY_SALT = b"smart-stock-dummy-salt"
_DUMMY_DIGEST = hashlib.scrypt(
    b"not-a-real-user-password",
    salt=_DUMMY_SALT,
    n=SCRYPT_N,
    r=SCRYPT_R,
    p=SCRYPT_P,
    dklen=SCRYPT_DKLEN,
)
_DUMMY_SALT_B64 = base64.urlsafe_b64encode(_DUMMY_SALT).decode("ascii")
_DUMMY_DIGEST_B64 = base64.urlsafe_b64encode(_DUMMY_DIGEST).decode("ascii")


class IdentityError(ValueError):
    """Raised for invalid credentials, users, roles, or identity state."""


@dataclass(frozen=True)
class UserIdentity:
    id: str
    username: str
    display_name: str
    role: str
    enabled: bool
    created_at: str

    @property
    def capabilities(self) -> frozenset[str]:
        return ROLE_CAPABILITIES[self.role]

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "displayName": self.display_name,
            "role": self.role,
            "enabled": self.enabled,
            "capabilities": sorted(self.capabilities),
        }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> str:
    if not isinstance(username, str):
        raise IdentityError("Kullanıcı adı geçersiz.")
    normalized = username.strip().casefold()
    if not _USERNAME_RE.fullmatch(normalized):
        raise IdentityError("Kullanıcı adı 3-64 karakter olmalı ve yalnızca harf, rakam, nokta, alt çizgi veya tire içermeli.")
    return normalized


def normalize_role(role: str) -> str:
    normalized = str(role or "").strip().upper()
    if normalized not in ROLES:
        raise IdentityError(f"Bilinmeyen rol: {role}")
    return normalized


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise IdentityError(f"Parola en az {PASSWORD_MIN_LENGTH} karakter olmalı.")
    if len(password) > 512:
        raise IdentityError("Parola çok uzun.")


def hash_password(password: str, *, salt: bytes | None = None) -> tuple[str, str]:
    validate_password(password)
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return (
        base64.urlsafe_b64encode(actual_salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, salt_b64: str, digest_b64: str) -> bool:
    try:
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=len(expected),
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(candidate, expected)


class IdentityStore:
    """SQLite-backed local user store with stable user IDs and explicit roles."""

    def __init__(self, path: str = DEFAULT_IDENTITY_DB):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY,
                  username TEXT NOT NULL UNIQUE,
                  display_name TEXT NOT NULL,
                  role TEXT NOT NULL,
                  password_salt TEXT NOT NULL,
                  password_hash TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS users_role ON users(role);
                """
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _identity(row: sqlite3.Row) -> UserIdentity:
        return UserIdentity(
            id=str(row["id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            role=normalize_role(row["role"]),
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"]),
        )

    def count_users(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def count_enabled_admins(self) -> int:
        with self._connect() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM users WHERE role=? AND enabled=1",
                    (ROLE_ADMIN,),
                ).fetchone()[0]
            )

    def create_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str | None = None,
        role: str = ROLE_VIEWER,
        enabled: bool = True,
    ) -> UserIdentity:
        normalized_username = normalize_username(username)
        normalized_role = normalize_role(role)
        validate_password(password)
        display = (display_name or normalized_username).strip()
        if not display or len(display) > 100:
            raise IdentityError("Görünen ad 1-100 karakter olmalı.")
        salt, digest = hash_password(password)
        timestamp = now()
        user_id = str(uuid.uuid4())
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO users(id,username,display_name,role,password_salt,password_hash,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        user_id,
                        normalized_username,
                        display,
                        normalized_role,
                        salt,
                        digest,
                        1 if enabled else 0,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise IdentityError("Bu kullanıcı adı zaten kullanılıyor.") from exc
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> UserIdentity:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise IdentityError("Kullanıcı bulunamadı.")
        return self._identity(row)

    def get_by_username(self, username: str) -> UserIdentity:
        normalized = normalize_username(username)
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
        if row is None:
            raise IdentityError("Kullanıcı bulunamadı.")
        return self._identity(row)

    def authenticate(self, username: str, password: str) -> UserIdentity:
        try:
            normalized = normalize_username(username)
        except IdentityError:
            raise IdentityError("Kullanıcı adı veya parola hatalı.") from None
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
        if row is None:
            verify_password(password, _DUMMY_SALT_B64, _DUMMY_DIGEST_B64)
            raise IdentityError("Kullanıcı adı veya parola hatalı.")
        if not verify_password(password, row["password_salt"], row["password_hash"]):
            raise IdentityError("Kullanıcı adı veya parola hatalı.")
        identity = self._identity(row)
        if not identity.enabled:
            raise IdentityError("Kullanıcı devre dışı.")
        return identity

    def list_users(self) -> list[UserIdentity]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self._identity(row) for row in rows]

    def set_role(self, user_id: str, role: str) -> UserIdentity:
        normalized_role = normalize_role(role)
        with self._connect() as db:
            row = db.execute("SELECT role,enabled FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise IdentityError("Kullanıcı bulunamadı.")
            if (
                row["role"] == ROLE_ADMIN
                and bool(row["enabled"])
                and normalized_role != ROLE_ADMIN
                and db.execute(
                    "SELECT COUNT(*) FROM users WHERE role=? AND enabled=1",
                    (ROLE_ADMIN,),
                ).fetchone()[0]
                <= 1
            ):
                raise IdentityError("Son aktif yönetici rolü düşürülemez.")
            db.execute(
                "UPDATE users SET role=?,updated_at=? WHERE id=?",
                (normalized_role, now(), user_id),
            )
        return self.get_user(user_id)

    def set_enabled(self, user_id: str, enabled: bool) -> UserIdentity:
        with self._connect() as db:
            row = db.execute("SELECT role,enabled FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise IdentityError("Kullanıcı bulunamadı.")
            if (
                row["role"] == ROLE_ADMIN
                and bool(row["enabled"])
                and not enabled
                and db.execute(
                    "SELECT COUNT(*) FROM users WHERE role=? AND enabled=1",
                    (ROLE_ADMIN,),
                ).fetchone()[0]
                <= 1
            ):
                raise IdentityError("Son aktif yönetici devre dışı bırakılamaz.")
            db.execute(
                "UPDATE users SET enabled=?,updated_at=? WHERE id=?",
                (1 if enabled else 0, now(), user_id),
            )
        return self.get_user(user_id)

    def set_password(self, user_id: str, password: str) -> UserIdentity:
        validate_password(password)
        salt, digest = hash_password(password)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE users SET password_salt=?,password_hash=?,updated_at=? WHERE id=?",
                (salt, digest, now(), user_id),
            )
            if cursor.rowcount != 1:
                raise IdentityError("Kullanıcı bulunamadı.")
        return self.get_user(user_id)

    def bootstrap_admin(self, username: str | None, password: str | None) -> UserIdentity | None:
        """Create the first ADMIN exactly once when the identity DB is empty."""
        if self.count_users() != 0:
            return None
        if not username or not password:
            raise IdentityError("İlk yönetici için kullanıcı adı ve parola gerekli.")
        return self.create_user(username, password, role=ROLE_ADMIN, display_name="Smart Stock Admin")
