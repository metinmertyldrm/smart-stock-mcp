# Production deployment

This document describes the hardened Smart Stock production deployment contract introduced in TASK 09. It is intentionally separate from the default `docker-compose.yml`, which remains the local development/demo topology.

## Security boundary

TASK 09 hardens deployment, secrets, networking, database migration and container runtime behavior. It does **not** turn the current anonymous bearer-session mechanism into user accounts, SSO or RBAC.

Until a real identity/RBAC layer is added, do not expose Smart Stock as an unrestricted public multi-user service. Put the loopback-bound application gateway behind a trusted TLS reverse proxy and, where the audience is not already trusted, an upstream authentication/access-control layer.

The production Compose file publishes only the web gateway. PostgreSQL, stock-service, llm-host and Ollama remain on Docker-internal networks.

## 1. Prepare production environment

Copy the template to a file that is never committed:

```bash
cp .env.production.example .env.production
```

Generate a high-entropy database password and replace the placeholder value. Set the canonical HTTPS browser origin, for example:

```text
PUBLIC_ORIGIN=https://stock.example.com
```

The application gateway defaults to:

```text
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
```

This is deliberate. A trusted reverse proxy should terminate TLS and forward to this loopback listener. Binding plain HTTP to `0.0.0.0` requires the explicit `ALLOW_PUBLIC_HTTP_BIND=true` escape hatch and should not be used as a substitute for TLS.

## 2. Pin external images by digest

Production rejects mutable PostgreSQL and Ollama image references. Resolve the repository digest on the deployment host:

```bash
docker pull postgres:17
docker pull ollama/ollama:latest
docker image inspect postgres:17 --format '{{index .RepoDigests 0}}'
docker image inspect ollama/ollama:latest --format '{{index .RepoDigests 0}}'
```

Copy the returned `repository@sha256:...` values into `POSTGRES_IMAGE` and `OLLAMA_IMAGE`. The all-zero digests in `.env.production.example` are sentinels and intentionally fail validation.

Pinning means upgrades are explicit. Refresh a digest only as a reviewed deployment change.

## 3. Fail-closed validation

Before rendering or starting the stack:

```bash
python scripts/validate_production_env.py --env-file .env.production
```

The validator rejects, among other things:

- missing required values,
- weak/default database passwords,
- non-HTTPS public origins,
- mutable or placeholder external image references,
- unsafe database names,
- accidental public plain-HTTP bind,
- invalid port or session-TTL settings.

Then audit the rendered Compose security contract without starting containers:

```bash
python scripts/production_smoke.py --env-file .env.production --config-only
```

The audit verifies that only the gateway publishes a host port and that stock-service, llm-host and web-ui retain read-only root filesystems, dropped capabilities and `no-new-privileges`.

## 4. Database migrations

The production Spring profile enables Flyway and uses versioned migrations from:

```text
stock-service/src/main/resources/db/migration
```

Hibernate runs with `ddl-auto=validate`. It validates the migrated schema but does not create or alter production tables. Development keeps its existing Hibernate-managed behavior and demo `data.sql` seed path.

A fresh production database is migrated automatically during stock-service startup. Demo/sample inventory is **not** inserted in production.

### Existing non-Flyway database

Do not point the production profile directly at an old development database that has no Flyway history. The production profile intentionally has `baseline-on-migrate=false` so an untracked schema cannot be silently adopted.

For an existing installation:

1. take and verify a backup,
2. rehearse the migration on a disposable copy,
3. create a fresh Flyway-managed production database,
4. migrate required business data through a reviewed data-migration procedure,
5. run the production smoke suite before switching traffic.

Do not enable an ad-hoc Flyway baseline on the live database merely to make startup pass.

## 5. Start the stack

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Inspect health:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

Expected exposure:

- `web-ui`: one host mapping, loopback by default,
- `postgres`: no host mapping,
- `stock-service`: no host mapping,
- `llm-host`: no host mapping,
- `ollama`: no host mapping.

The production networks are segmented into data, gateway/application and model paths. They are not intended to be flattened into one shared network.

## 6. Runtime verification

Run the production smoke without an LLM generation:

```bash
python scripts/production_smoke.py --env-file .env.production
```

It verifies:

- production Compose hardening,
- gateway health,
- read-only stock access,
- LLM readiness,
- bearer protection on metrics,
- browser mutation blocking.

For a release candidate, also run the existing security and observability checks against the production gateway URL where appropriate.

## 7. TLS reverse proxy

The included Nginx container is the application gateway, not the internet-facing TLS terminator. Place a trusted reverse proxy/load balancer in front of `127.0.0.1:${WEB_PORT}` and configure:

- HTTPS-only public access,
- a valid certificate and renewal path,
- HTTP to HTTPS redirect at the outer proxy,
- request-size and timeout policies compatible with the application,
- upstream authentication/access control until TASK 10 identity/RBAC exists,
- forwarding of the original host/protocol information.

Do not publish stock-service, llm-host, PostgreSQL or Ollama just to make the outer proxy work. It only needs access to the web gateway.

## 8. Backup

Create an application-consistent PostgreSQL logical backup. The command runs through the internal container so the database does not need a host port:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U "$DB_USERNAME" -d "$DB_NAME" -Fc > smart-stock-$(date +%Y%m%d-%H%M%S).dump
```

Also back up the persistent deployment configuration and, if conversation continuity is required, the `llm-prod-data` volume through your infrastructure's volume-backup mechanism. Never place `.env.production` or backup files containing secrets in Git.

A backup is not considered valid until a restore rehearsal succeeds on a disposable environment.

## 9. Restore rehearsal

Use an isolated database/stack, never the live production volume, for routine restore tests. After creating a fresh target database, restore the dump with `pg_restore`, then start the matching application release and run the production smoke.

Conceptually:

```bash
cat backup.dump | docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U "$DB_USERNAME" -d "$DB_NAME" --clean --if-exists
```

Use this destructive `--clean` form only on the explicitly selected restore target. Stop application writers during a real disaster-recovery restore.

## 10. Upgrade and rollback

Before an upgrade:

1. record the deployed Git tag/commit and image digests,
2. take a verified database backup,
3. review new Flyway migrations for backward compatibility,
4. deploy the new release,
5. run production smoke and inspect readiness/logs.

Application rollback is straightforward only while database migrations remain backward compatible. Flyway migrations are forward-only by default. Do not manually delete Flyway history rows or reverse schema changes on a live database. If a migration is not backward compatible, restore the verified pre-upgrade database backup together with the previous application release.

## 11. Stop without deleting data

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml down
```

Do **not** add `-v` during normal operations. Removing volumes deletes production PostgreSQL, Ollama and LLM-host persistent data.
