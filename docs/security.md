# Security model

Smart Stock uses several independent safety layers. They solve different problems and must not be treated as interchangeable.

## 1. AI write-safety boundary

The LLM proposes execution plans, but it is not the authority for write operations. Host-side validation enforces the business workflow:

- read-only requests cannot execute write tools;
- a purchase draft must exist before an order can be confirmed;
- order confirmation is separate from draft creation;
- incoming orders are listed before receiving;
- receiving inventory requires a later explicit confirmation.

TASK 10 added stable user identity and RBAC around this existing boundary. TASK 11 hardens the browser session transport without moving authorization into the model.

## 2. Identity modes and session transport

The deployable LLM API entry point is `secure_api:app`.

Two identity modes exist:

- `anonymous`: development/test compatibility. `POST /api/session` creates an opaque bearer session and the development browser may keep that opaque token locally.
- `local`: production user identity. `POST /api/auth/login` authenticates a stable user account and creates a server-side session represented in the browser by an `HttpOnly`, `SameSite=Strict` cookie.

Production requires `LLM_AUTH_MODE=local`; anonymous session issuance returns `404` there.

For both modes only the SHA-256 digest of the opaque session token is persisted. In local identity mode the plaintext session token is never returned in the login JSON and is not available to browser JavaScript. The user's role is not trusted from a token, cookie or browser field; the current role is loaded from the identity database on protected requests.

The intentionally public LLM endpoints are limited to coarse liveness/readiness and identity bootstrap entry points such as:

- `GET /api/health`;
- `GET /api/ready`;
- `GET /api/auth/config`;
- `POST /api/auth/login` in local mode;
- `POST /api/session` only in anonymous mode.

Protected local-identity routes use the session cookie automatically. Anonymous development routes continue to use:

```text
Authorization: Bearer <opaque-token>
```

`GET /api/auth/config` reports the active session transport and CSRF header so the browser does not have to guess deployment policy.

Readiness exposes only coarse local component state and MCP server/tool counts. It does not expose credentials, conversation content, request bodies, or tool results.

The session lifetime remains bounded and configurable with `LLM_SESSION_TTL_SECONDS`.

## 3. Local user credentials and login abuse protection

Local identities use stable UUIDs. Passwords are never stored directly. Each password is processed with stdlib `scrypt` and a per-user random salt.

Authentication failures do not distinguish unknown usernames from incorrect passwords. Unknown-user attempts perform a dummy `scrypt` calculation to reduce obvious timing differences.

Repeated failed local logins are protected by a bounded process-local throttler. The limiter stores hashed source/username keys rather than raw usernames, source addresses, passwords, session values or request bodies. Configuration is bounded through `LLM_LOGIN_MAX_FAILURES`, `LLM_LOGIN_WINDOW_SECONDS`, `LLM_LOGIN_BLOCK_SECONDS` and `LLM_LOGIN_RATE_LIMIT_MAX_KEYS`.

The limiter is intentionally process-local. It is a local deployment protection, not a claim of globally distributed abuse prevention. Multi-instance deployments still require a shared rate-limit/WAF layer.

A successful login resets the matching failure bucket and rotates any existing local browser session when one is present.

Disabling a user invalidates active sessions. Password-reset flows can revoke that user's existing sessions. The final enabled `ADMIN` cannot be disabled or demoted, preventing accidental administrative lockout.

## 4. Local browser CSRF boundary

TASK 11 uses a double-submit style CSRF boundary for local identity mode:

- the opaque authentication session is stored in an `HttpOnly`, `SameSite=Strict` cookie;
- a separate non-secret CSRF value is stored in a JavaScript-readable `SameSite=Strict` cookie;
- only the SHA-256 digest of the CSRF value is stored server-side with the session;
- authenticated `POST`, `PUT`, `PATCH` and `DELETE` requests must echo that value in the configured `X-CSRF-Token` header;
- the server checks that the cookie value, header value and server-side digest agree before business logic executes;
- missing or mismatched proof returns `403`.

The CSRF value is not an authentication credential. Possessing it without the `HttpOnly` session cookie does not create an authenticated session.

Legacy TASK 10 user sessions that predate CSRF state are not accepted as local cookie sessions. After the TASK 11 deployment users sign in again and receive a cookie/CSRF-bound session.

`LLM_SESSION_COOKIE_SECURE` controls the Secure flag. `auto` is intended only for loopback acceptance where the browser directly reaches `http://127.0.0.1`; real HTTPS deployment should use `true`. The fail-closed production validator requires secure cookies if the HTTP gateway is deliberately bound to a public interface.

## 5. Roles and capabilities

The local role model is:

| Role | Server capabilities |
| --- | --- |
| `VIEWER` | read |
| `OPERATOR` | read + purchase draft creation |
| `MANAGER` | operator capabilities + operational confirmation |
| `ADMIN` | manager capabilities + metrics + user management |

Role information shown by the UI is informational. The server is authoritative.

Caller-supplied `X-Client-Id`, role, capability, cookie, authorization and CSRF headers are removed before requests enter the internal orchestration layer. The server injects only the identity metadata derived from the authenticated session.

## 6. Layered RBAC enforcement

TASK 10 deliberately checks write authorization more than once:

1. request middleware resolves the authenticated user and role;
2. MCP tool discovery hides write tools the role cannot use;
3. the whole execution plan is checked before the first tool runs;
4. MCP dispatch checks authorization again immediately before each write-capable tool call;
5. confirmation routes independently require the `confirm` capability;
6. metrics and user-management routes require `ADMIN` capabilities.

Whole-plan preflight is atomic. If a plan contains an allowed read step followed by a forbidden write, the read step is not executed either. This prevents partial execution of a plan that can never be authorized.

A blocked plan records bounded audit metadata such as role, blocked step and blocked tool. Passwords, session tokens, CSRF values and browser cookies are not copied into chat telemetry.

## 7. Conversation and browser cache isolation

Conversation ownership uses the stable authenticated user ID in local mode. Fetch, list and delete operations filter by that owner. A different user receives the same not-found behavior rather than another user's conversation.

Legacy anonymous development sessions keep their random owner IDs and remain isolated from one another.

The local browser no longer stores the authentication credential in `localStorage`. React Query user-scoped cache is cleared on login, logout and session loss. Cross-tab synchronization uses a non-secret auth epoch/event marker; receiving that marker causes each tab to re-check `/api/auth/me` rather than copying credentials between tabs.

## 8. Docker and production gateway

The default development topology uses the browser-facing web gateway, typically on loopback. Production publishes only the hardened web gateway; PostgreSQL, stock-service, llm-host and Ollama stay on Docker-internal networks.

Production `/stock/...` behavior is stricter than development:

- only `GET` and `HEAD` are accepted;
- nginx performs an internal `auth_request` against `llm-host /api/auth/me` using the browser session cookie;
- unauthenticated requests return `401`;
- after successful authorization both `Cookie` and `Authorization` are explicitly removed before proxying to `stock-service`;
- browser stock mutations remain blocked with `405`.

`/llm/...` proxies to the secured LLM host and preserves the browser cookie needed by the public authentication boundary. The internal orchestration layer does not receive the raw cookie.

The normal PostgreSQL, stock-service, LLM host and Ollama production services do not publish host ports.

## 9. Browser mutation policy

The dashboard's stock data path is intentionally read-only. Purchase finalization must go through the AI operation/confirmation flow so host-side state, business validation, CSRF and RBAC remain authoritative.

Do not re-introduce browser-side stock/order mutation endpoints through `/stock`. New mutations must pass a server-side session, CSRF, authorization and confirmation boundary as appropriate.

## 10. Security headers

The production Nginx gateway sets conservative browser headers including:

- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy: no-referrer`;
- a restrictive camera/microphone/geolocation `Permissions-Policy`;
- a same-origin-oriented Content Security Policy.

Request bodies are bounded at the gateway. Authentication/session responses use no-store caching where appropriate.

## 11. Verification

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

The production smoke performs no LLM inference. It verifies local cookie identity mode, login without bearer exposure, authenticated `/me`, ADMIN metrics, authenticated stock reads, login throttling, CSRF rejection, browser mutation blocking and session revocation on logout.

CI additionally starts the production nginx image with a real local-identity LLM host and a mock stock backend. The mock backend deliberately fails if it receives either an `Authorization` or `Cookie` header, proving that the gateway authenticates the browser without leaking browser credentials to `stock-service`. The gateway integration test also proves CSRF-less logout is rejected and CSRF-protected logout revokes the cookie session.

For the detailed TASK 11 contract, see [`task11-session-auth-hardening.md`](task11-session-auth-hardening.md). For observability-specific checks, see [`observability.md`](observability.md).

## 12. Manual development warning

Starting stock-service directly on a host port bypasses the production gateway's authentication and method restrictions. Do not bind it to an untrusted interface.

Likewise, use the secured entry point for browser-facing LLM traffic:

```bash
uvicorn secure_api:app --host 127.0.0.1 --port 8000
```

`web_api:app` remains an internal orchestration implementation for tests and trusted code. It is not the public authentication boundary.

## 13. Deferred security work

TASK 11 improves the local identity browser/session boundary, but it is not a complete enterprise identity platform. Deferred work includes:

- TLS termination infrastructure;
- MFA;
- SSO/OIDC and SCIM;
- password recovery/email verification;
- centralized/distributed session storage;
- distributed rate limiting and external WAF policy;
- organization/tenant federation;
- formal external security audit.
