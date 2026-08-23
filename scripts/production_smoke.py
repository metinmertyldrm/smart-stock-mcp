#!/usr/bin/env python3
"""Production deployment contract + gateway smoke for Smart Stock.

No LLM inference is performed. The script checks the rendered production Compose
security contract, local identity boundary, and browser-facing HTTP boundary.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from validate_production_env import merged_environment


class ProductionSmokeFailure(RuntimeError):
    pass


def request(
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
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed
    except OSError as exc:
        raise ProductionSmokeFailure(f"{method} {url} -> {type(exc).__name__}: {exc}") from exc


def render_compose(repo_root: Path, env_file: Path | None) -> dict[str, Any]:
    cmd = ["docker", "compose"]
    if env_file is not None:
        cmd.extend(["--env-file", str(env_file)])
    cmd.extend(["-f", str(repo_root / "docker-compose.prod.yml"), "config", "--format", "json"])
    try:
        completed = subprocess.run(
            cmd,
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise ProductionSmokeFailure(f"Production Compose render failed: {stderr.strip() or exc}") from exc
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProductionSmokeFailure("Production Compose JSON output could not be parsed") from exc


def audit_compose_config(config: dict[str, Any], *, allow_public_bind: bool = False) -> None:
    services = config.get("services")
    if not isinstance(services, dict):
        raise ProductionSmokeFailure("Compose config has no services map")

    required = {"postgres", "stock-service", "ollama", "ollama-init", "llm-host", "web-ui"}
    missing = sorted(required - set(services))
    if missing:
        raise ProductionSmokeFailure(f"Production Compose missing services: {', '.join(missing)}")

    for name in ("postgres", "stock-service", "ollama", "ollama-init", "llm-host"):
        ports = services[name].get("ports") or []
        if ports:
            raise ProductionSmokeFailure(f"{name} unexpectedly publishes host ports")

    web_ports = services["web-ui"].get("ports") or []
    if len(web_ports) != 1:
        raise ProductionSmokeFailure("web-ui must publish exactly one gateway port")
    published = web_ports[0]
    host_ip = str(published.get("host_ip") or "") if isinstance(published, dict) else ""
    if host_ip in {"0.0.0.0", "::", "[::]", ""} and not allow_public_bind:
        raise ProductionSmokeFailure(
            f"web-ui gateway is not loopback-bound ({host_ip or 'unspecified'}); use --allow-public-bind only behind a trusted TLS boundary"
        )

    for name in ("stock-service", "llm-host", "web-ui"):
        service = services[name]
        if service.get("read_only") is not True:
            raise ProductionSmokeFailure(f"{name} root filesystem is not read-only")
        cap_drop = {str(value).upper() for value in (service.get("cap_drop") or [])}
        if "ALL" not in cap_drop:
            raise ProductionSmokeFailure(f"{name} does not drop all Linux capabilities")
        options = {str(value).casefold() for value in (service.get("security_opt") or [])}
        if "no-new-privileges:true" not in options:
            raise ProductionSmokeFailure(f"{name} is missing no-new-privileges")

    web_healthcheck = services["web-ui"].get("healthcheck") or {}
    health_test = web_healthcheck.get("test") or []
    health_command = " ".join(str(value) for value in health_test)
    if "http://127.0.0.1:8080/healthz" not in health_command:
        raise ProductionSmokeFailure(
            "web-ui healthcheck must probe the explicit IPv4 loopback endpoint http://127.0.0.1:8080/healthz"
        )

    stock_env = services["stock-service"].get("environment") or {}
    if stock_env.get("SPRING_PROFILES_ACTIVE") != "production":
        raise ProductionSmokeFailure("stock-service is not using the production Spring profile")

    llm_env = services["llm-host"].get("environment") or {}
    if llm_env.get("APP_ENV") != "production":
        raise ProductionSmokeFailure("llm-host telemetry environment must be production")
    if not str(llm_env.get("APP_VERSION") or "").strip():
        raise ProductionSmokeFailure("llm-host telemetry application version must be set")
    if llm_env.get("LLM_MODEL") != llm_env.get("OLLAMA_MODEL"):
        raise ProductionSmokeFailure("llm-host telemetry model must match OLLAMA_MODEL")
    if str(llm_env.get("LLM_AUTH_MODE") or "").casefold() != "local":
        raise ProductionSmokeFailure("llm-host production identity mode must be local")
    if not str(llm_env.get("LLM_IDENTITY_DB") or "").strip():
        raise ProductionSmokeFailure("llm-host identity database path must be set")
    if not str(llm_env.get("LLM_BOOTSTRAP_ADMIN_USERNAME") or "").strip():
        raise ProductionSmokeFailure("llm-host bootstrap admin username must be set")
    if not str(llm_env.get("LLM_BOOTSTRAP_ADMIN_PASSWORD") or "").strip():
        raise ProductionSmokeFailure("llm-host bootstrap admin password must be set")


def login(llm_url: str, username: str, password: str, timeout: float) -> str:
    status, body = request(
        "POST",
        f"{llm_url}/api/auth/login",
        payload={"username": username, "password": password},
        timeout=timeout,
    )
    token = body.get("token") if isinstance(body, dict) else None
    user = body.get("user") if isinstance(body, dict) else None
    if status != 200 or not isinstance(token, str) or len(token) < 32:
        raise ProductionSmokeFailure(f"Admin login failed: HTTP {status}")
    if not isinstance(user, dict) or user.get("role") != "ADMIN":
        raise ProductionSmokeFailure("Bootstrap login did not resolve to ADMIN")
    return token


def run_http_checks(web_url: str, timeout: float, *, username: str, password: str) -> None:
    base = web_url.rstrip("/")
    stock_url = f"{base}/stock"
    llm_url = f"{base}/llm"

    status, body = request("GET", f"{base}/healthz", timeout=timeout)
    if status != 200 or str(body).strip() != "ok":
        raise ProductionSmokeFailure(f"Gateway health returned HTTP {status}: {body!r}")
    print("  [OK] Production gateway health")

    status, products = request("GET", f"{stock_url}/api/products", timeout=timeout)
    if status != 200 or not isinstance(products, list):
        raise ProductionSmokeFailure(f"Read-only stock gateway returned HTTP {status}")
    print(f"  [OK] Read-only stock gateway: {len(products)} products visible")

    status, readiness = request("GET", f"{llm_url}/api/ready", timeout=timeout)
    if status != 200 or not isinstance(readiness, dict) or readiness.get("status") != "ready":
        raise ProductionSmokeFailure(f"LLM readiness returned HTTP {status}: {readiness!r}")
    print("  [OK] LLM readiness through production gateway")

    status, auth_config = request("GET", f"{llm_url}/api/auth/config", timeout=timeout)
    if status != 200 or not isinstance(auth_config, dict) or auth_config.get("mode") != "local":
        raise ProductionSmokeFailure(f"Production auth config returned HTTP {status}: {auth_config!r}")
    print("  [OK] Production local identity mode")

    status, _ = request("POST", f"{llm_url}/api/session", timeout=timeout)
    if status != 404:
        raise ProductionSmokeFailure(f"Anonymous production session returned HTTP {status}, expected 404")

    status, _ = request("GET", f"{llm_url}/api/metrics", timeout=timeout)
    if status != 401:
        raise ProductionSmokeFailure(f"Unauthenticated metrics returned HTTP {status}, expected 401")

    token = login(llm_url, username, password, timeout)
    auth_header = {"Authorization": f"Bearer {token}"}
    status, me = request("GET", f"{llm_url}/api/auth/me", headers=auth_header, timeout=timeout)
    if status != 200 or not isinstance(me, dict) or (me.get("user") or {}).get("role") != "ADMIN":
        raise ProductionSmokeFailure(f"Authenticated identity returned HTTP {status}")

    status, metrics = request("GET", f"{llm_url}/api/metrics", headers=auth_header, timeout=timeout)
    if status != 200 or not isinstance(metrics, dict) or "http" not in metrics:
        raise ProductionSmokeFailure(f"Admin metrics returned HTTP {status}")
    print("  [OK] Metrics require authenticated ADMIN role")

    status, _ = request("POST", f"{llm_url}/api/auth/logout", headers=auth_header, timeout=timeout)
    if status != 204:
        raise ProductionSmokeFailure(f"Logout returned HTTP {status}")
    status, _ = request("GET", f"{llm_url}/api/metrics", headers=auth_header, timeout=timeout)
    if status != 401:
        raise ProductionSmokeFailure(f"Revoked session returned HTTP {status}, expected 401")
    print("  [OK] Logout revokes the bearer session")

    status, _ = request(
        "POST",
        f"{stock_url}/api/marketplace/orders",
        payload={"draftId": -1},
        timeout=timeout,
    )
    if status != 405:
        raise ProductionSmokeFailure(f"Browser stock mutation returned HTTP {status}, expected 405")
    print("  [OK] Browser-facing stock mutations remain blocked")


def run(
    web_url: str,
    *,
    timeout: float,
    repo_root: Path,
    env_file: Path | None,
    allow_public_bind: bool,
    skip_compose_audit: bool,
    config_only: bool,
) -> None:
    if config_only and skip_compose_audit:
        raise ProductionSmokeFailure("--config-only cannot be combined with --skip-compose-audit")
    if not skip_compose_audit:
        config = render_compose(repo_root, env_file)
        audit_compose_config(config, allow_public_bind=allow_public_bind)
        print("  [OK] Production Compose exposes only the hardened gateway")
    if config_only:
        return
    values = merged_environment(env_file)
    username = values.get("LLM_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = values.get("LLM_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        raise ProductionSmokeFailure("Production identity credentials are required for HTTP smoke")
    run_http_checks(web_url, timeout, username=username, password=password)


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Verify Smart Stock production deployment boundaries")
    parser.add_argument("--web-url", default=os.getenv("PRODUCTION_WEB_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--repo-root", type=Path, default=default_root)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--allow-public-bind", action="store_true")
    parser.add_argument("--skip-compose-audit", action="store_true")
    parser.add_argument("--config-only", action="store_true", help="Audit rendered Compose and skip HTTP checks")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    print("Smart Stock production smoke verification")
    try:
        run(
            args.web_url,
            timeout=args.timeout,
            repo_root=args.repo_root.resolve(),
            env_file=args.env_file.resolve() if args.env_file else None,
            allow_public_bind=args.allow_public_bind,
            skip_compose_audit=args.skip_compose_audit,
            config_only=args.config_only,
        )
    except (ProductionSmokeFailure, OSError, ValueError) as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    print("\n[PASS] Smart Stock production deployment boundaries verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
