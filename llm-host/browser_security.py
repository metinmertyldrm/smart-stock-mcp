"""Browser-facing local-identity session policy.

This module contains cookie/CSRF helpers and a bounded process-local login
throttler. It deliberately stores no plaintext password, session credential,
CSRF value, username, or source address in limiter state.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import Request
from starlette.responses import Response


SESSION_COOKIE_NAME = os.getenv("LLM_SESSION_COOKIE_NAME", "smart_stock_session")
CSRF_COOKIE_NAME = os.getenv("LLM_CSRF_COOKIE_NAME", "smart_stock_csrf")
CSRF_HEADER_NAME = "X-CSRF-Token"
COOKIE_SAMESITE = "strict"


def _bool_or_auto(name: str, default: str = "auto") -> str:
    value = os.getenv(name, default).strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return "true"
    if value in {"0", "false", "no", "off"}:
        return "false"
    if value == "auto":
        return value
    raise RuntimeError(f"{name} must be true, false, or auto")


COOKIE_SECURE_MODE = _bool_or_auto("LLM_SESSION_COOKIE_SECURE")


def cookie_secure(request: Request) -> bool:
    if COOKIE_SECURE_MODE == "true":
        return True
    if COOKIE_SECURE_MODE == "false":
        return False
    return request.url.scheme.casefold() == "https"


def set_local_auth_cookies(
    response: Response,
    request: Request,
    *,
    session_token: str,
    csrf_token: str,
    max_age: int,
) -> None:
    secure = cookie_secure(request)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite=COOKIE_SAMESITE,
    )


def clear_local_auth_cookies(response: Response, request: Request) -> None:
    secure = cookie_secure(request)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=False,
        samesite=COOKIE_SAMESITE,
    )


def cookie_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def csrf_proof(request: Request) -> str | None:
    """Return a same-origin double-submit proof or ``None`` when it is invalid."""
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_value or not header_value:
        return None
    if not hmac.compare_digest(cookie_value, header_value):
        return None
    return header_value


@dataclass
class _LoginBucket:
    failures: list[float] = field(default_factory=list)
    blocked_until: float = 0.0


class LoginRateLimiter:
    """Bounded process-local fixed-window login throttling.

    Keys are SHA-256 digests of source + normalized username so raw source/user
    metadata is not retained. This is intentionally a single-process control;
    distributed enforcement remains deferred platform work.
    """

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 300,
        block_seconds: int = 300,
        max_keys: int = 5000,
    ):
        if max_failures < 1:
            raise ValueError("max_failures must be positive")
        if window_seconds < 1 or block_seconds < 1 or max_keys < 1:
            raise ValueError("rate-limit durations and max_keys must be positive")
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, _LoginBucket] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(source: str | None, username: str) -> str:
        raw = f"{source or 'unknown'}\n{username.strip().casefold()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _prune(self, bucket: _LoginBucket, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket.failures[:] = [stamp for stamp in bucket.failures if stamp > cutoff]
        if bucket.blocked_until <= now:
            bucket.blocked_until = 0.0

    def retry_after(self, source: str | None, username: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        key = self._key(source, username)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0
            self._prune(bucket, current)
            if not bucket.failures and bucket.blocked_until == 0:
                self._buckets.pop(key, None)
                return 0
            self._buckets.move_to_end(key)
            return max(0, math.ceil(bucket.blocked_until - current))

    def record_failure(self, source: str | None, username: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        key = self._key(source, username)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _LoginBucket()
                self._buckets[key] = bucket
            self._prune(bucket, current)
            bucket.failures.append(current)
            if len(bucket.failures) >= self.max_failures:
                bucket.blocked_until = max(bucket.blocked_until, current + self.block_seconds)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return max(0, math.ceil(bucket.blocked_until - current))

    def record_success(self, source: str | None, username: str) -> None:
        key = self._key(source, username)
        with self._lock:
            self._buckets.pop(key, None)


def configured_login_limiter() -> LoginRateLimiter:
    def integer(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = os.getenv(name)
        value = default if raw is None else int(raw)
        if not minimum <= value <= maximum:
            raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
        return value

    return LoginRateLimiter(
        max_failures=integer("LLM_LOGIN_MAX_FAILURES", 5, 1, 50),
        window_seconds=integer("LLM_LOGIN_WINDOW_SECONDS", 300, 1, 86400),
        block_seconds=integer("LLM_LOGIN_BLOCK_SECONDS", 300, 1, 86400),
        max_keys=integer("LLM_LOGIN_RATE_LIMIT_MAX_KEYS", 5000, 100, 100000),
    )
