# Security model

Smart Stock uses several independent safety layers. They solve different problems and should not be treated as interchangeable.

## 1. AI write-safety boundary

The LLM proposes execution plans, but it is not the authority for write operations. Host-side validation still enforces the existing workflow:

- read-only / planning requests cannot execute write tools,
- a purchase draft must exist before an order can be confirmed,
- order confirmation is separate from draft creation,
- incoming orders are listed before receiving,
- receiving inventory requires a later explicit confirmation.

TASK 07 does not move these decisions into the model. It adds HTTP and deployment boundaries around them.

## 2. Anonymous bearer sessions

The deployable LLM API entry point is `secure_api:app`.

`POST /api/session` creates an anonymous session with:

- a cryptographically random opaque bearer token,
- a separate random server-side owner identifier,
- a bounded expiration time.

Only the SHA-256 digest of the bearer token is stored in the session SQLite database. The plaintext bearer credential is returned once to the client and is not persisted by the server.

All LLM API routes except `/api/health` and `/api/session` require:

```text
Authorization: Bearer <opaque-token>
```

A caller-supplied `X-Client-Id` is not trusted at the public boundary. `secure_api` strips it and injects the owner identifier resolved from the bearer session before handing the request to the internal `web_api` implementation.

The default session lifetime is 30 days and can be overridden with `LLM_SESSION_TTL_SECONDS`. Values are bounded between 5 minutes and 365 days.

### Important limitation

These sessions provide anonymous client isolation. They are **not** user accounts, organization membership, roles, MFA, or RBAC.

The current web client stores the opaque bearer token in browser `localStorage`. That is suitable for this local/demo topology, but a successful same-origin XSS vulnerability could read the token. A production internet-facing deployment should use a hardened identity/session design appropriate to its threat model, usually behind TLS and with stronger browser credential handling.

## 3. Docker web gateway

The default Docker topology has one browser-facing application entry point:

```text
http://localhost:5173
```

The published web port is bound to `127.0.0.1` by default.

Nginx exposes two application proxy paths:

- `/stock/...` proxies to the stock service but accepts only `GET` and `HEAD`,
- `/llm/...` proxies to the secured LLM host and preserves bearer authentication.

The normal `postgres`, `stock-service` and `llm-host` containers do not publish host ports. They remain reachable to one another over the Docker-internal network. Keeping the normal PostgreSQL service internal also avoids conflicts with an independently installed host PostgreSQL instance and removes an unnecessary host-facing database socket.

Ollama remains published for local development because the host-side smoke verifier checks the configured model directly; its port is bound to `127.0.0.1` rather than all interfaces.

Acceptance PostgreSQL and the acceptance Spring service remain host-accessible because the acceptance runner/reset tooling is host-side. Those ports are also loopback-only and operate on the isolated acceptance database.

## 4. Browser mutation policy

The web dashboard's stock data path is intentionally read-only. The previous direct browser `POST /api/marketplace/orders` action from the drafts screen has been removed.

A draft may be inspected in the Drafts page, but purchase finalization must go through the AI Operations confirmation flow so the same host-side state and confirmation rules are authoritative for every order.

Do not re-introduce browser-side stock/order mutations through the `/stock` proxy. If a new mutation is needed, route it through a server-side authorization/confirmation boundary first.

## 5. Security headers

The default Nginx gateway sets conservative browser headers including:

- `X-Content-Type-Options: nosniff`,
- `X-Frame-Options: DENY`,
- `Referrer-Policy: no-referrer`,
- a restrictive camera/microphone/geolocation `Permissions-Policy`.

Request bodies are limited to 1 MiB at the gateway. Session-creation and authentication-error responses use `Cache-Control: no-store`.

## 6. Verification

After rebuilding the secured stack, run the deterministic security smoke:

```bash
python scripts/security_smoke.py
```

It verifies that:

- protected LLM routes reject missing bearer credentials,
- a spoofed `X-Client-Id` cannot impersonate another owner,
- two independent bearer sessions cannot read each other's conversations,
- browser-facing stock mutation requests are rejected by the gateway before reaching the backend.

Then run the normal full-stack smoke, optionally including the real read-only LLM + MCP path:

```bash
python scripts/smoke_stack.py --chat
```

The normal smoke now reaches stock and LLM APIs through the same `/stock` and `/llm` gateway paths used by the Docker web application.

## 7. Manual development warning

Starting `stock-service` directly on host port 8081 is a development topology and does not receive the Docker gateway's method restriction. Do not bind that service to an untrusted interface or expose it to the internet.

Likewise, run the browser-facing LLM API with:

```bash
uvicorn secure_api:app --host 127.0.0.1 --port 8000
```

`web_api:app` remains available as the internal orchestration implementation for tests and trusted development code. It should not be treated as the public authenticated entry point.

## Non-goals for TASK 07

TASK 07 does not attempt to provide a complete production identity platform, TLS termination, per-user roles, enterprise SSO, secret management infrastructure, WAF/rate-limiting infrastructure, or a formal security audit. Those require deployment-specific decisions beyond this repository's local/demo topology.
