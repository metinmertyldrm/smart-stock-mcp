# TASK 10 — Identity and RBAC

TASK 10 replaces anonymous-only client isolation with stable local user identities and explicit operational roles while preserving the existing AI write-safety boundary.

## Goals

1. Add stable user accounts with salted password hashing and no plaintext credential persistence.
2. Bind bearer sessions to authenticated users so conversation ownership survives logout/login and token rotation.
3. Add role-based capabilities that are enforced by the host, never by the LLM prompt alone.
4. Prevent lower-privilege users from escalating through a crafted chat request, confirmation request, spoofed header, or direct metrics/user-admin request.
5. Add a browser login/logout/me flow and display the authenticated role.
6. Make production identity fail closed and add deterministic security/production smoke coverage.

## Roles

| Role | Capabilities |
| --- | --- |
| `VIEWER` | Read stock/AI information and own conversations only |
| `OPERATOR` | `VIEWER` + create purchase drafts |
| `MANAGER` | `OPERATOR` + confirm operational writes such as placing orders or receiving stock |
| `ADMIN` | `MANAGER` + user administration and operational metrics |

The role is a server-owned property resolved from the authenticated session. External `X-Client-Id`, role, or capability headers are never trusted.

## Enforcement model

The authorization decision is layered:

- identity/session middleware resolves `user_id` + role;
- `web_api` receives only server-injected identity metadata;
- the agent computes the requested AI permission (`PLAN` / write intent) as before;
- host RBAC clamps the plan/tool set to the user's maximum capability;
- confirmation endpoints separately require the `confirm` capability;
- `/api/metrics` and user-management endpoints require `ADMIN` capabilities.

This means an LLM-generated plan cannot grant itself a capability that the authenticated user does not have.

## Compatibility and rollout

Development/test topology may retain anonymous mode temporarily for existing deterministic tests. Production will use explicit local identity mode and bootstrap exactly one first administrator when the identity database is empty. Existing anonymous bearer sessions are not treated as authenticated user accounts.

The local identity provider is intentionally small and self-contained. Enterprise SSO/OIDC, MFA, password recovery, email verification, SCIM, and organization/tenant federation remain future integrations; the RBAC boundary added here is designed so an external IdP can later map claims into the same server-owned roles.

## Acceptance criteria

- password plaintext never appears in the SQLite identity database;
- disabled users cannot authenticate;
- sessions resolve to stable user IDs and current server-side roles;
- two users cannot read each other's conversations;
- `VIEWER` cannot execute write tools;
- `OPERATOR` can create a draft but cannot confirm/place an order;
- `MANAGER` can confirm permitted operational writes;
- only `ADMIN` can manage users or read metrics;
- spoofed owner/role headers do not change authorization;
- security smoke and production contract tests pass without LLM inference;
- web UI requires login in production identity mode and supports logout.
