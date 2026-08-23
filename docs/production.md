# Production deployment

This document describes the hardened Smart Stock production deployment contract introduced in TASK 09 and extended with local identity/RBAC in TASK 10. It is intentionally separate from the default `docker-compose.yml`, which remains the local development/demo topology.

## Security boundary

Production is fail-closed around both deployment hardening and user identity:

- only the web gateway publishes a host port;
- PostgreSQL, stock-service, llm-host and Ollama remain on Docker-internal networks;
- `LLM_AUTH_MODE=local` is required;
- production stock reads require a valid bearer session at the nginx gateway;
- browser stock mutations remain blocked;
- AI write tools are additionally restricted by server-side roles and confirmation rules.

The included gateway is still bound to loopback by default. Put it behind a trusted HTTPS reverse proxy for internet-facing deployment. TASK 10 removes the need for a separate upstream login layer solely to provide Smart Stock user accounts, but it does not replace TLS, WAF/rate-limiting, MFA or enterprise SSO infrastructure.

## 1. Prepare production environment

Copy the template to a file that is never committed:

```bash
cp .env.production.example .env.production
```

Set the database credentials and canonical HTTPS origin. Also choose the first local administrator credentials:

```text
LLM_AUTH_MODE=local
LLM_BOOTSTRAP_ADMIN_USERNAME=<admin-username>
LLM_BOOTSTRAP_ADMIN_PASSWORD=<strong-random-password>
PUBLIC_ORIGIN=https://stock.example.com
```

Do not paste production passwords into issue trackers, chat logs or shell history shared with others. The bootstrap password is validated as a production secret and must not use template/default values.

The application gateway defaults to:

```text
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
```

This is deliberate. A trusted reverse proxy should terminate TLS and forward to this loopback listener. Binding plain HTTP to `0.0.0.0` requires explicit `ALLOW_PUBLIC_HTTP_BIND=true` and should not be used as a substitute for TLS.

The first administrator is created only if the identity database contains no users. Later changes to bootstrap environment variables do not replace existing accounts.

## 2. Pin external images by digest

Production rejects mutable PostgreSQL and Ollama image references. Resolve repository digests on the deployment host:

```bash
docker pull postgres:17
docker pull ollama/ollama:latest
docker image inspect postgres:17 --format '{{index .RepoDigests 0}}'
docker image inspect ollama/ollama:latest --format '{{index .RepoDigests 0}}'
```

Copy the returned `repository@sha256:...` values into `POSTGRES_IMAGE` and `OLLAMA_IMAGE`. The all-zero digests in `.env.production.example` are sentinels and intentionally fail validation.

Pinning makes upgrades explicit. Refresh a digest only as a reviewed deployment change.

## 3. Fail-closed validation

Before rendering or starting the stack:

```bash
python scripts/validate_production_env.py --env-file .env.production
```

The validator rejects, among other things:

- missing required values;
- weak/default database credentials;
- missing or non-local production auth mode;
- invalid bootstrap administrator username;
- weak/default/bootstrap administrator passwords;
- non-HTTPS public origins;
- mutable or placeholder external image references;
- unsafe database names;
- accidental public plain-HTTP bind;
- invalid port or session-TTL settings.

Then audit the rendered Compose security contract without starting containers:

```bash
python scripts/production_smoke.py --env-file .env.production --config-only
```

The audit verifies that only the gateway publishes a host port, app containers retain their hardening, telemetry uses production identity, and the LLM host receives the local-auth/identity configuration.

## 4. Persistent identity and conversations

Production keeps LLM-host persistent state under the named `llm-prod-data` volume, including:

- conversations;
- bearer-session hashes;
- local identities and password hashes.

The identity database is separate from the business PostgreSQL database but shares the protected LLM data volume. Do not delete that volume during routine restart or upgrade operations.

Passwords are stored only as salted `scrypt` hashes. Bearer tokens are stored only as SHA-256 digests.

## 5. Database migrations

The production Spring profile enables Flyway and uses versioned migrations from:

```text
stock-service/src/main/resources/db/migration
```

Hibernate runs with `ddl-auto=validate`. It validates the migrated schema but does not create or alter production tables. Development keeps its existing Hibernate-managed behavior and demo seed path.

A fresh production database is migrated automatically during stock-service startup. Demo/sample inventory is **not** inserted in production.

### Existing non-Flyway database

Do not point the production profile directly at an old development database with no Flyway history. `baseline-on-migrate=false` intentionally prevents silently adopting an untracked schema.

For an existing installation:

1. take and verify a backup;
2. rehearse migration on a disposable copy;
3. create a fresh Flyway-managed production database;
4. migrate required business data through a reviewed data-migration procedure;
5. run the production smoke suite before switching traffic.

Do not enable an ad-hoc Flyway baseline on the live database merely to make startup pass.

## 6. Start the stack

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Inspect health:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

Expected exposure:

- `web-ui`: one host mapping, loopback by default;
- `postgres`: no host mapping;
- `stock-service`: no host mapping;
- `llm-host`: no host mapping;
- `ollama`: no host mapping.

The production networks are segmented into data, gateway/application and model paths. Do not flatten them into one shared network.

Open the gateway in a browser. Production should show the Smart Stock login screen before application data is loaded. Log in with the bootstrap administrator on the first deployment, then create named users and assign the minimum role they require.

## 7. Runtime verification

Run the production smoke without LLM generation:

```bash
python scripts/production_smoke.py --env-file .env.production --web-url http://127.0.0.1:8080
```

Use the actual `WEB_PORT` if it differs from `8080`.

The runtime smoke verifies:

- production Compose hardening;
- gateway health and LLM readiness;
- production auth mode is `local`;
- anonymous session issuance is disabled;
- unauthenticated stock reads and metrics are rejected;
- bootstrap ADMIN login and `/api/auth/me` work;
- authenticated stock reads succeed through the gateway;
- ADMIN metrics work;
- stock mutation methods remain blocked;
- logout invalidates subsequent LLM and stock access with the old token.

The smoke does not ask Ollama to generate a response.

## 8. Role behavior

Production roles are:

- `VIEWER`: read stock/AI information and own conversations;
- `OPERATOR`: viewer access plus purchase-draft creation;
- `MANAGER`: operator access plus operational confirmation/write approval;
- `ADMIN`: manager access plus metrics and user management.

The browser UI displays the role and exposes the ADMIN user-management screen, but server-side middleware, plan preflight and MCP dispatch are authoritative. Hiding a button is not an authorization control.

The final active ADMIN cannot be disabled or demoted. Create another ADMIN before intentionally changing the last administrator account.

## 9. Production stock gateway

`/stock/` is read-only and authenticated:

1. nginx rejects methods other than `GET`/`HEAD`;
2. nginx performs an internal authorization subrequest to `llm-host /api/auth/me`;
3. invalid/missing sessions return `401`;
4. after authorization, nginx strips the browser `Authorization` header before forwarding to stock-service.

This keeps stock-service unaware of browser bearer credentials while ensuring direct calls to the public `/stock` path cannot bypass login.

## 10. TLS reverse proxy

The included Nginx container is the application gateway, not the internet-facing TLS terminator. Place a trusted reverse proxy/load balancer in front of `127.0.0.1:${WEB_PORT}` and configure:

- HTTPS-only public access;
- a valid certificate and renewal path;
- HTTP-to-HTTPS redirect at the outer proxy;
- request-size and timeout policies compatible with the application;
- rate limiting/WAF policy appropriate to the deployment;
- forwarding of original host/protocol information.

Do not publish stock-service, llm-host, PostgreSQL or Ollama just to make the outer proxy work. It only needs access to the web gateway.

## 11. Backup

Create an application-consistent PostgreSQL logical backup. The command runs through the internal container so the database does not need a host port:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > smart-stock-backup.dump
```

Also back up:

- deployment configuration through your secret-management process;
- the `llm-prod-data` volume when user identities/conversation continuity must survive disaster recovery.

Never put `.env.production`, identity data or backup files containing secrets in Git.

A backup is not valid until restore rehearsal succeeds on an isolated environment.

## 12. Restore rehearsal

Use an isolated database/stack, never the live production volume, for routine restore tests. After creating a fresh target database, restore the dump with `pg_restore`, restore the matching LLM data backup when identity continuity is required, then start the matching application release and run production smoke.

Conceptually:

```bash
cat backup.dump | docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists'
```

Use destructive `--clean` only on the explicitly selected restore target. Stop application writers during a real disaster-recovery restore.

## 13. Upgrade and rollback

Before an upgrade:

1. record the deployed Git tag/commit and image digests;
2. take verified PostgreSQL and required LLM-data backups;
3. review new Flyway migrations for backward compatibility;
4. deploy the new release;
5. run production smoke and inspect readiness/logs;
6. verify login and role-sensitive access.

Application rollback is straightforward only while database migrations remain backward compatible. Flyway migrations are forward-only by default. Do not manually delete Flyway history rows or reverse schema changes on a live database.

## 14. Stop without deleting data

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml down
```

Do **not** add `-v` during normal operations. Removing volumes deletes production PostgreSQL, Ollama, conversations, bearer-session state and local user identities.
