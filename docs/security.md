# Security model

Smart Stock uses several independent safety layers. They solve different problems and must not be treated as interchangeable.

## 1. AI write-safety boundary

The LLM proposes execution plans, but it is not the authority for write operations. Host-side validation enforces the business workflow:

- read-only requests cannot execute write tools;
- a purchase draft must exist before an order can be confirmed;
- order confirmation is separate from draft creation;
- incoming orders are listed before receiving;
- receiving inventory requires a later explicit confirmation.

TASK 10 adds user identity and RBAC around this existing boundary rather than moving authorization into the model.

## 2. Identity modes and bearer sessions

The deployable LLM API entry point is `secure_api:app`.

Two identity modes exist:

- `anonymous`: development/test compatibility. `POST /api/session` creates an opaque anonymous bearer session.
- `local`: production user identity. `POST /api/auth/login` authenticates a stable user account and creates an opaque bearer session linked to that user ID.

Production requires `LLM_AUTH_MODE=local`; anonymous session issuance returns `404` there.

Only the SHA-256 digest of a bearer token is stored server-side. The plaintext token is returned to the browser and is never persisted by the session store. The user's role is not trusted from the token or browser. In local mode the current role is loaded from the identity database on each protected request.

The intentionally public LLM endpoints are limited to coarse liveness/readiness and identity bootstrap entry points such as:

- `GET /api/health`;
- `GET /api/ready`;
- `GET /api/auth/config`;
- `POST /api/auth/login` in local mode;
- `POST /api/session` only in anonymous mode.

Protected routes require:

```text
Authorization: Bearer <opaque-token>
```

Readiness exposes only coarse local component state and MCP server/tool counts. It does not expose credentials, conversation content, request bodies, or tool results.

The default session lifetime is bounded and configurable with `LLM_SESSION_TTL_SECONDS`.

## 3. Local user credentials

Local identities use stable UUIDs. Passwords are never stored directly. Each password is processed with stdlib `scrypt` and a per-user random salt.

Authentication failures do not distinguish unknown usernames from incorrect passwords. Unknown-user attempts perform a dummy `scrypt` calculation to reduce obvious timing differences.

Disabling a user invalidates active sessions. The final enabled `ADMIN` cannot be disabled or demoted, preventing accidental administrative lockout.

The browser currently stores the opaque bearer token in `localStorage`. A same-origin XSS vulnerability could therefore read it. The production CSP reduces the attack surface, but `HttpOnly` cookie migration, MFA, enterprise SSO/OIDC and distributed session storage remain future hardening work.

## 4. Roles and capabilities

The local role model is:

| Role | Server capabilities |
| --- | --- |
| `VIEWER` | read |
| `OPERATOR` | read + purchase draft creation |
| `MANAGER` | operator capabilities + operational confirmation |
| `ADMIN` | manager capabilities + metrics + user management |

Role information shown by the UI is informational. The server is authoritative.

A caller-supplied `X-Client-Id`, role, or capability header is removed at the public boundary. The server injects identity metadata derived from the authenticated session.

## 5. Layered RBAC enforcement

TASK 10 deliberately checks write authorization more than once:

1. request middleware resolves the authenticated user and role;
2. MCP tool discovery hides write tools the role cannot use;
3. the whole execution plan is checked before the first tool runs;
4. MCP dispatch checks authorization again immediately before each write-capable tool call;
5. confirmation routes independently require the `confirm` capability;
6. metrics and user-management routes require `ADMIN` capabilities.

Whole-plan preflight is atomic. If a plan contains an allowed read step followed by a forbidden write, the read step is not executed either. This prevents partial execution of a plan that can never be authorized.

A blocked plan records bounded audit metadata such as role, blocked step and blocked tool. Passwords and bearer tokens are not copied into chat telemetry.

## 6. Conversation isolation

Conversation ownership uses the stable authenticated user ID in local mode. Fetch, list and delete operations filter by that owner. A different user receives the same not-found behavior rather than another user's conversation.

Legacy anonymous development sessions keep their random owner IDs and remain isolated from one another.

## 7. Docker and production gateway

The default development topology uses the browser-facing web gateway, typically on loopback. Production publishes only the hardened web gateway; PostgreSQL, stock-service, llm-host and Ollama stay on Docker-internal networks.

Production `/stock/...` behavior is stricter than development:

- only `GET` and `HEAD` are accepted;
- nginx performs an internal `auth_request` against `llm-host /api/auth/me`;
- unauthenticated requests return `401`;
- after successful authorization, the bearer token is explicitly removed before proxying to stock-service;
- browser stock mutations remain blocked with `405`.

`/llm/...` proxies to the secured LLM host and preserves bearer authentication.

The normal PostgreSQL, stock-service, LLM host and Ollama production services do not publish host ports.

## 8. Browser mutation policy

The dashboard's stock data path is intentionally read-only. Purchase finalization must go through the AI operation/confirmation flow so host-side state, business validation and RBAC remain authoritative.

Do not re-introduce browser-side stock/order mutation endpoints through `/stock`. New mutations must pass a server-side authorization and confirmation boundary.

## 9. Security headers

The production Nginx gateway sets conservative browser headers including:

- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy: no-referrer`;
- a restrictive camera/microphone/geolocation `Permissions-Policy`;
- a same-origin-oriented Content Security Policy.

Request bodies are bounded at the gateway. Authentication/session responses use no-store caching where appropriate.

## 10. Verification

Development security smoke remains available:

```bash
python scripts/security_smoke.py
```

The production contract and runtime boundary are verified with:

```bash
python scripts/validate_production_env.py --env-file .env.production
python scripts/production_smoke.py --env-file .env.production --config-only
python scripts/production_smoke.py --env-file .env.production --web-url http://127.0.0.1:8080
```

The production smoke performs no LLM inference. It verifies local identity mode, admin login, authenticated `/me`, ADMIN metrics, authenticated stock reads, browser mutation blocking and session revocation on logout.

CI additionally starts the production nginx image with a real local-identity LLM host and a mock stock backend. The mock backend deliberately fails if it receives the browser Authorization header, proving that the gateway validates the bearer credential without leaking it to stock-service.

For observability-specific checks, see [`observability.md`](observability.md).

## 11. Manual development warning

Starting stock-service directly on a host port bypasses the production gateway's authentication and method restrictions. Do not bind it to an untrusted interface.

Likewise, use the secured entry point for browser-facing LLM traffic:

```bash
uvicorn secure_api:app --host 127.0.0.1 --port 8000
```

`web_api:app` remains an internal orchestration implementation for tests and trusted code. It is not the public authentication boundary.

## 12. Deferred security work

TASK 10 is a local identity/RBAC implementation, not a complete enterprise identity platform. Deferred work includes:

- TLS termination infrastructure;
- MFA;
- SSO/OIDC and SCIM;
- password recovery/email verification;
- centralized/distributed session storage;
- rate limiting / WAF policy;
- `HttpOnly` cookie migration;
- organization/tenant federation;
- formal external security audit.
