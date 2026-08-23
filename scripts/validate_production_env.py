#!/usr/bin/env python3
"""Fail-closed validation for Smart Stock production deployment settings."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


REQUIRED = (
    "DB_USERNAME",
    "DB_PASSWORD",
    "LLM_AUTH_MODE",
    "LLM_BOOTSTRAP_ADMIN_USERNAME",
    "LLM_BOOTSTRAP_ADMIN_PASSWORD",
    "PUBLIC_ORIGIN",
    "POSTGRES_IMAGE",
    "OLLAMA_IMAGE",
    "OLLAMA_MODEL",
)
COMMON_SECRETS = {
    "postgres",
    "password",
    "password123",
    "changeme",
    "change-me",
    "secret",
    "admin",
    "smartstock",
}
DIGEST_RE = re.compile(r"@sha256:([0-9a-fA-F]{64})$")
DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IDENTITY_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
TRUE_VALUES = {"1", "true", "yes", "on"}


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_no}: KEY=VALUE biçimi bekleniyor")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"{path}:{line_no}: boş değişken adı")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def merged_environment(env_file: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None:
        values.update(parse_env_file(env_file))
    values.update(os.environ)
    return values


def is_true(value: str | None) -> bool:
    return (value or "").strip().casefold() in TRUE_VALUES


def validate_immutable_image(name: str, value: str, errors: list[str]) -> None:
    match = DIGEST_RE.search(value)
    if match is None:
        errors.append(f"{name} immutable digest ile pinlenmeli: image@sha256:<64-hex>")
        return
    digest = match.group(1).casefold()
    if len(set(digest)) == 1:
        errors.append(f"{name} örnek/sentinel digest içeriyor; gerçek image digest girilmeli")


def validate_origin(value: str, errors: list[str]) -> None:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "https":
        errors.append("PUBLIC_ORIGIN production için https:// ile başlamalı")
    if not parsed.hostname:
        errors.append("PUBLIC_ORIGIN geçerli bir hostname içermeli")
    if parsed.username or parsed.password:
        errors.append("PUBLIC_ORIGIN credential içeremez")
    if parsed.query or parsed.fragment:
        errors.append("PUBLIC_ORIGIN query/fragment içeremez")
    if parsed.path not in {"", "/"}:
        errors.append("PUBLIC_ORIGIN yalnız origin olmalı; path içeremez")


def secret_is_placeholder(value: str) -> bool:
    lowered = value.casefold().strip()
    return lowered in COMMON_SECRETS or any(
        token in lowered
        for token in ("changeme", "replace-me", "replace_with", "example-password")
    )


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []

    for name in REQUIRED:
        if not values.get(name, "").strip():
            errors.append(f"{name} zorunlu")

    password = values.get("DB_PASSWORD", "")
    if password:
        if len(password) < 20:
            errors.append("DB_PASSWORD en az 20 karakter olmalı")
        if secret_is_placeholder(password):
            errors.append("DB_PASSWORD default/örnek bir değer olamaz")
        if values.get("DB_USERNAME", "").strip() and password == values.get("DB_USERNAME", "").strip():
            errors.append("DB_PASSWORD, DB_USERNAME ile aynı olamaz")

    auth_mode = values.get("LLM_AUTH_MODE", "").strip().casefold()
    if auth_mode and auth_mode != "local":
        errors.append("LLM_AUTH_MODE production için local olmalı")

    admin_username = values.get("LLM_BOOTSTRAP_ADMIN_USERNAME", "").strip().casefold()
    if admin_username and not IDENTITY_USERNAME_RE.fullmatch(admin_username):
        errors.append("LLM_BOOTSTRAP_ADMIN_USERNAME geçerli bir yerel kullanıcı adı olmalı")

    admin_password = values.get("LLM_BOOTSTRAP_ADMIN_PASSWORD", "")
    if admin_password:
        if len(admin_password) < 20:
            errors.append("LLM_BOOTSTRAP_ADMIN_PASSWORD en az 20 karakter olmalı")
        if secret_is_placeholder(admin_password):
            errors.append("LLM_BOOTSTRAP_ADMIN_PASSWORD default/örnek bir değer olamaz")
        if admin_username and admin_password.casefold() == admin_username:
            errors.append("LLM_BOOTSTRAP_ADMIN_PASSWORD kullanıcı adıyla aynı olamaz")
        if password and admin_password == password:
            errors.append("LLM_BOOTSTRAP_ADMIN_PASSWORD, DB_PASSWORD ile aynı olmamalı")

    origin = values.get("PUBLIC_ORIGIN", "").strip()
    if origin:
        validate_origin(origin, errors)

    for image_var in ("POSTGRES_IMAGE", "OLLAMA_IMAGE"):
        image = values.get(image_var, "").strip()
        if image:
            validate_immutable_image(image_var, image, errors)

    model = values.get("OLLAMA_MODEL", "").strip()
    if model and model.casefold() in {"latest", "qwen3:latest"}:
        errors.append("OLLAMA_MODEL açık bir model etiketi olmalı; latest kullanılamaz")

    db_name = values.get("DB_NAME", "smart_stock").strip()
    if not DB_NAME_RE.fullmatch(db_name):
        errors.append("DB_NAME yalnız harf, rakam ve alt çizgi içermeli ve harf/alt çizgi ile başlamalı")

    bind_address = values.get("WEB_BIND_ADDRESS", "127.0.0.1").strip()
    if bind_address in {"0.0.0.0", "::", "[::]"} and not is_true(values.get("ALLOW_PUBLIC_HTTP_BIND")):
        errors.append(
            "WEB_BIND_ADDRESS public interface'e açılıyor; bunu bilerek yapıyorsanız ALLOW_PUBLIC_HTTP_BIND=true ayarlayın"
        )

    try:
        web_port = int(values.get("WEB_PORT", "8080"))
        if not 1 <= web_port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("WEB_PORT 1-65535 arasında bir tam sayı olmalı")

    try:
        ttl = int(values.get("LLM_SESSION_TTL_SECONDS", "86400"))
        if not 300 <= ttl <= 2_592_000:
            raise ValueError
    except ValueError:
        errors.append("LLM_SESSION_TTL_SECONDS 300 ile 2592000 arasında olmalı")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Production env dosyası")
    args = parser.parse_args(argv)

    try:
        values = merged_environment(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] Production environment okunamadı: {exc}", file=sys.stderr)
        return 1

    errors = validate(values)
    if errors:
        print("Smart Stock production environment validation")
        for error in errors:
            print(f"  [FAIL] {error}")
        print(f"\n[FAIL] {len(errors)} production configuration error(s)")
        return 1

    print("Smart Stock production environment validation")
    print("  [OK] Required values are present")
    print("  [OK] Database credential policy passed")
    print("  [OK] Local identity/bootstrap policy passed")
    print("  [OK] Public origin is HTTPS-only")
    print("  [OK] External container images are digest-pinned")
    print("  [OK] HTTP bind/session settings passed")
    print("\n[PASS] Production environment is fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
