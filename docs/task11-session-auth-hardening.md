# TASK 11: Session and authentication hardening

TASK 11 hardens the local identity boundary introduced in TASK 10. The goal is to reduce browser credential exposure and make cookie-authenticated production traffic resistant to cross-site request forgery and basic login abuse without changing the existing RBAC model.

## Goals

1. Remove the production/local identity bearer credential from JavaScript-accessible persistent storage.
2. Use server-issued `HttpOnly` cookies for local identity sessions.
3. Require an explicit CSRF proof for cookie-authenticated state-changing requests.
4. Preserve anonymous bearer-session compatibility only for development/test mode.
5. Add bounded login throttling with generic authentication failures and observable security events.
6. Preserve session revocation when users are disabled, passwords are reset, roles change, or users log out.
7. Keep the production stock gateway authenticated while ensuring browser credentials never reach `stock-service`.
8. Keep same-browser and cross-tab cache isolation without using the session credential as a browser synchronization primitive.

## Intended local-identity browser contract

- Login sets an opaque session cookie with `HttpOnly` and `SameSite=Strict`.
- The browser does not persist the opaque session credential in `localStorage` or `sessionStorage`.
- A separate non-secret CSRF value is available to same-origin JavaScript and must be echoed in a request header for protected mutation requests.
- Authenticated GET/HEAD requests rely on the session cookie and do not require CSRF proof.
- Logout revokes the server-side session and expires both browser cookies.
- Browser auth-change synchronization uses a non-secret epoch/event marker rather than a credential.

## CSRF boundary

For local identity mode, state-changing authenticated requests (`POST`, `PUT`, `PATCH`, `DELETE`) must pass CSRF validation unless they are intentionally public authentication bootstrap endpoints such as login.

The CSRF value is not an authentication credential. It exists only to prove that a same-origin script intentionally issued the mutation request. Missing or mismatched CSRF proof must return `403` before business logic executes.

## Login abuse boundary

Repeated failed authentication attempts must be throttled with bounded state and generic user-facing errors. Security logs may include coarse rejection/rate-limit events but must not contain passwords, bearer/session values, CSRF values, or raw request bodies.

The first implementation is process-local and intentionally does not claim distributed/global enforcement. Distributed rate-limit/session storage remains a later platform concern.

## Compatibility

Development `LLM_AUTH_MODE=anonymous` retains opaque bearer-session compatibility so existing local development and deterministic tests remain usable. Production continues to require `LLM_AUTH_MODE=local` and moves to the cookie/CSRF browser contract.

## Explicitly deferred

- MFA
- SSO/OIDC/SCIM
- password recovery/email verification
- organization/tenant federation
- distributed session or rate-limit storage
- external WAF policy
- TLS termination infrastructure
- formal external security audit

## Acceptance criteria

- No local-identity browser bearer token is stored in JavaScript-accessible persistent storage.
- Local login creates an `HttpOnly` session cookie.
- Missing/invalid CSRF proof blocks protected mutation requests.
- Authenticated read-only stock gateway still works.
- Browser credentials are not forwarded to `stock-service`.
- Logout revokes server access and clears browser cookies.
- Cross-tab logout clears protected cached UI state without sharing the credential.
- Repeated failed logins trigger throttling and later recover after the configured window.
- CI and production smoke cover cookie auth, CSRF rejection, login throttling, logout revocation, and gateway credential stripping.
