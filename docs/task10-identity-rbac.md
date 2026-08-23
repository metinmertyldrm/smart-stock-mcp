# TASK 10 — Identity and RBAC

TASK 10 replaces anonymous-only production access with stable local user identities and explicit operational roles while preserving the existing AI write-safety boundary.

Development/test topology may still run in `anonymous` mode for deterministic backwards compatibility. Production is fail-closed in `local` identity mode.

## Goals

1. Add stable user accounts with salted password hashing and no plaintext credential persistence.
2. Bind opaque bearer sessions to authenticated users so conversation ownership survives logout/login and token rotation.
3. Add role-based capabilities that are enforced by the host, never by the LLM prompt or browser UI alone.
4. Prevent lower-privilege users from escalating through crafted chat requests, confirmation requests, spoofed headers, hidden tools, direct MCP dispatch, metrics, or user-admin routes.
5. Require authentication for production stock reads as well as AI/conversation access.
6. Add browser login/logout/me flow, role visibility, and ADMIN user management.
7. Keep production configuration fail-closed and cover the boundaries with deterministic CI and smoke checks without LLM inference.

## Roles

| Role | Capabilities |
| --- | --- |
| `VIEWER` | Read stock/AI information and own conversations only |
| `OPERATOR` | `VIEWER` + create purchase drafts |
| `MANAGER` | `OPERATOR` + confirm operational writes such as placing orders or receiving stock |
| `ADMIN` | `MANAGER` + user administration and operational metrics |

The role is a server-owned property resolved from the authenticated session. External `X-Client-Id`, role, capability, or owner hints are never trusted.

## Identity and credential storage

Local users are stored in the LLM host identity SQLite database with stable UUID identifiers. Passwords use a per-user random salt and stdlib `scrypt`; plaintext passwords are never written to the database. Unknown-user authentication performs a dummy `scrypt` verification to reduce obvious username timing differences.

Bearer tokens remain opaque random credentials. Only their hashes are persisted. A local-mode session is linked to `user_id`, while the user's current role is loaded from the identity store on every protected request. Role changes therefore take effect without issuing a new token. Disabling a user revokes that user's active sessions.

The final active `ADMIN` cannot be demoted or disabled, preventing an administrator from accidentally locking the deployment out of user management.

## Authorization layers

Authorization is deliberately redundant:

1. **Session/identity middleware** resolves the bearer token to a stable user and current server-side role.
2. **Header sanitization** removes caller-supplied owner/role/capability headers and injects only server-owned identity metadata.
3. **Tool discovery filtering** hides write tools that the current role cannot use, reducing the chance that the LLM plans an impossible action.
4. **Whole-plan RBAC preflight** scans the complete execution plan before the first tool call. If a later write step exceeds the role, no earlier read step is executed either.
5. **MCP dispatch authorization** checks the role again immediately before every write-capable tool call.
6. **Confirmation authorization** separately requires the `confirm` capability on confirmation routes.
7. **Administrative authorization** restricts metrics and user management to `ADMIN`.

A model-generated plan therefore cannot grant itself a capability that the authenticated user does not have. The plan preflight result is also written into the audit payload as a bounded authorization marker when execution is blocked.

## Production gateway boundary

Production exposes only the web gateway. `/stock/` remains GET/HEAD-only and is now protected by nginx `auth_request` against the LLM host's `/api/auth/me` endpoint. The browser bearer token is used only for that authorization subrequest and is explicitly removed before the request is proxied to `stock-service`.

This gives the production gateway the following behavior:

- unauthenticated stock reads return `401`;
- authenticated users with a valid session can perform read-only stock requests;
- browser-facing stock mutation methods remain blocked with `405`;
- logout revokes both AI access and subsequent stock-gateway access with the same bearer token.

The production web image verifies that its nginx build contains the HTTP `auth_request` module. Static CI tests also pin the gateway configuration so accidental removal of this authorization boundary fails the build.

## Production bootstrap

Production requires:

- `LLM_AUTH_MODE=local`;
- a dedicated identity database path inside the persistent LLM data volume;
- `LLM_BOOTSTRAP_ADMIN_USERNAME`;
- `LLM_BOOTSTRAP_ADMIN_PASSWORD` satisfying the production credential policy.

The bootstrap administrator is created only when the identity database contains no users. Changing bootstrap environment values later does not overwrite existing accounts.

## Browser behavior

The web application reads `/api/auth/config` before rendering protected pages. In production local mode, the application shows the login screen until a valid user session exists. After login it displays the authenticated user's role. `ADMIN` users receive the user-management navigation and can create users, change roles, and enable or disable other accounts.

A `401` from either the LLM boundary or the authenticated production stock gateway clears the local bearer token and returns the UI to the login flow. UI role checks are presentation only; authorization remains server-side.

## Audit behavior

For authenticated chat requests, correlated telemetry includes only bounded actor metadata:

- stable user ID;
- username;
- role.

Passwords and bearer tokens are not copied into chat telemetry. RBAC-preflight blocks carry role, blocked step, and blocked tool information so the decision journal can distinguish authorization denial from ordinary tool failure.

## Acceptance criteria

- password plaintext never appears in the SQLite identity database;
- unknown and invalid passwords use the same generic authentication failure;
- disabled users cannot authenticate and active sessions are revoked;
- sessions resolve to stable user IDs while current roles remain server-controlled;
- two users cannot read each other's conversations;
- spoofed owner/role headers cannot change authorization;
- `VIEWER` cannot execute write tools;
- `OPERATOR` can create a draft but cannot confirm/place an order or receive stock;
- `MANAGER` can confirm permitted operational writes;
- only `ADMIN` can manage users or read metrics;
- the last active `ADMIN` cannot be demoted or disabled;
- a forbidden write plan is rejected atomically before any preceding read tool executes;
- MCP dispatch independently rejects a forbidden write even if a higher layer regresses;
- unauthenticated production stock reads return `401`;
- authenticated production stock reads remain read-only;
- logout revokes access to both LLM and production stock routes;
- production config requires local auth and strong bootstrap credentials;
- security/production contract tests pass without LLM inference;
- web UI requires login in production identity mode and supports logout.

## Deliberately deferred integrations

Enterprise SSO/OIDC, MFA, password recovery, email verification, SCIM, organization/tenant federation, distributed session storage, and browser `HttpOnly` cookie migration are not part of TASK 10. The server-owned role/capability boundary is designed so a future external identity provider can map trusted claims into the same authorization model.
