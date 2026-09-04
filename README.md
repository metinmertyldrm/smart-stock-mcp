# Smart Stock & Procurement MCP

Smart Stock & Procurement MCP is an AI-assisted inventory and procurement system built around the Model Context Protocol (MCP). It tracks warehouse inventory, detects low-stock products, compares marketplace offers, creates procurement plans, and lets operators control sensitive actions through natural-language requests with explicit confirmation gates.

## Documentation

- `docs/tr/teknoloji-kararlari.md` — why each technology was chosen and how it is used (Turkish)
- `docs/tr/kurulum-rehberi.md` — first-time setup guide with troubleshooting (Turkish)
- `docs/tr/olcum-gunlugu.md` — acceptance measurement log and results (Turkish)
- `docs/security.md`, `docs/production.md`, `docs/release.md` — security boundaries, production deployment, release process

Latest acceptance measurement: **19/27 (70%)** on a clean acceptance database with
`qwen3:8b`. All confirmation-gate scenarios pass. See the measurement log for the
history and the classification of the remaining failures.

## Architecture

The repository contains five main components:

- `stock-service`: Java 21 / Spring Boot REST backend with PostgreSQL persistence and the core inventory, order and marketplace business rules.
- `stock-mcp`: Python MCP server exposing stock and incoming-order tools.
- `marketplace-mcp`: Python MCP server exposing seller, offer, draft, order and procurement-plan tools.
- `llm-host`: Python orchestrator using Qwen through Ollama, execution planning, MCP calls, permission/confirmation handling, conversation persistence, FastAPI endpoints and acceptance tooling.
- `web-ui`: React + TypeScript + Vite operations dashboard and AI operations center.

The LLM proposes execution plans, but business rules, user authorization and write-safety controls remain enforced outside the model. Purchase finalization and inventory receiving flows preserve explicit confirmation requirements.

## Key features

- Natural-language stock and procurement operations in Turkish or English.
- Low-stock and out-of-stock detection.
- Replenishment quantity calculation.
- Marketplace offer search and TOPSIS-based multi-criteria comparison.
- Cheapest, fastest, highest-rated and balanced procurement strategies.
- Purchase draft and marketplace order workflows.
- Incoming-order tracking and safe receive flow.
- Persistent AI conversations with owner isolation.
- Observable execution plan, MCP trace, telemetry and user-facing decision journal.
- Isolated acceptance database tooling for repeatable write scenarios.
- React/Vite operations dashboard.
- Anonymous opaque bearer-session compatibility in development; stable local identity with `HttpOnly` cookie sessions in production.
- CSRF protection for cookie-authenticated production mutations and bounded login throttling.
- Role-based authorization with `VIEWER`, `OPERATOR`, `MANAGER` and `ADMIN` capabilities.
- Whole-plan RBAC preflight plus independent MCP dispatch authorization.
- Production login/logout/me and ADMIN user management.
- Authenticated, read-only production stock gateway; browser session credentials are not forwarded to stock-service.
- Separate fail-closed production Compose topology with non-root/read-only application containers.
- Flyway-owned production schema migrations with Hibernate validation.
- Tag-gated release workflow that publishes versioned application images and immutable digests.

## Quick start with Docker Compose

Docker Compose is the recommended way to run the complete local development stack. It starts PostgreSQL, the Spring service, Ollama, the LLM host with both MCP subprocesses, and the web dashboard.

### Requirements

- Docker Engine / Docker Desktop with Compose v2
- Git

Clone the repository:

```bash
git clone https://github.com/metinmertyldrm/smart-stock-mcp.git
cd smart-stock-mcp
```

Optional: copy the environment template if you want to override local ports, the local database password, the Ollama model, or the anonymous development-session lifetime.

```bash
cp .env.example .env
```

Start everything:

```bash
docker compose up --build
```

On the first run, the `ollama-init` container downloads `qwen3:8b`, so startup can take several minutes depending on the network connection. The model and PostgreSQL data are kept in named Docker volumes and do not need to be downloaded/recreated on every restart.

Default host-facing endpoints:

| Surface | Address |
| --- | --- |
| Web dashboard / application gateway | `http://localhost:5173` |
| Read-only stock API through gateway | `http://localhost:5173/stock/api/...` |
| Secured LLM API through gateway | `http://localhost:5173/llm/api/...` |
| Ollama, loopback-only | `http://localhost:11434` |
| PostgreSQL, loopback-only | `localhost:5432` |

The normal Docker `stock-service` and `llm-host` containers do **not** publish host ports. Browser traffic enters through the web gateway. The default development topology keeps anonymous bearer-session compatibility for LLM conversation isolation. Production uses a separate, stricter local-identity cookie/CSRF contract described below.

In the anonymous development default the server does not assign a role, so there is
no login screen and no logout button; every action the backend allows is available.
To exercise named accounts, roles and the logout flow locally, set these in `.env`
before starting the stack:

```text
LLM_AUTH_MODE=local
LLM_BOOTSTRAP_ADMIN_USERNAME=<admin-username>
LLM_BOOTSTRAP_ADMIN_PASSWORD=<strong-random-password>
```

The first `ADMIN` is created only while the identity store is still empty. `.env` is
git-ignored; never commit these values.

Run in the background:

```bash
docker compose up --build -d
```

Inspect status and logs:

```bash
docker compose ps
docker compose logs -f llm-host
docker compose logs -f stock-service
```

Run deterministic security checks:

```bash
python scripts/security_smoke.py
```

Run observability checks:

```bash
python scripts/observability_smoke.py
```

Run the normal full-stack smoke, including one real read-only LLM + MCP turn:

```bash
python scripts/smoke_stack.py --chat
```

Stop the stack while keeping data:

```bash
docker compose down
```

Remove the development stack and its local Docker volumes only when you intentionally want to delete local database, conversation, session and Ollama model data:

```bash
docker compose down -v
```

The default `DB_PASSWORD=postgres` in Compose is strictly a local-development convenience. Override it in `.env` for any shared environment. This Compose stack is a development/demo topology, not the production deployment configuration.

See [`docs/security.md`](docs/security.md) for the trust boundaries, identity modes and deployment warnings.

## Production deployment

Production uses the separate `docker-compose.prod.yml` contract. Do not convert the development Compose file into a public deployment by simply changing its port bindings.

Copy `.env.production.example`, replace every weak/placeholder value, resolve real PostgreSQL/Ollama repository digests, and configure the first administrator:

```text
LLM_AUTH_MODE=local
LLM_BOOTSTRAP_ADMIN_USERNAME=<admin-username>
LLM_BOOTSTRAP_ADMIN_PASSWORD=<strong-random-password>
LLM_SESSION_COOKIE_SECURE=true
```

For a loopback-only local acceptance run over `http://127.0.0.1`, the template's `LLM_SESSION_COOKIE_SECURE=auto` mode is supported. Real HTTPS deployments should use `true`.

Then run the fail-closed checks before startup:

```bash
python scripts/validate_production_env.py --env-file .env.production
python scripts/production_smoke.py --env-file .env.production --config-only
```

The production topology keeps PostgreSQL, stock-service, llm-host and Ollama internal. Only the application gateway is host-facing and it binds to loopback by default so a trusted HTTPS reverse proxy can sit in front of it.

Production identity behavior:

- anonymous session issuance is disabled;
- the first local `ADMIN` is created only when the identity store is empty;
- local login creates an `HttpOnly`, `SameSite=Strict` session cookie and does not return the opaque session token in JSON;
- authenticated state-changing LLM requests require a matching CSRF cookie/header proof;
- repeated failed logins are protected by a bounded process-local throttle;
- current roles are re-read server-side on protected requests;
- unauthenticated `/stock` reads return `401`;
- nginx validates the session cookie through `/api/auth/me` and strips both `Cookie` and `Authorization` before forwarding to stock-service;
- metrics and user management are `ADMIN`-only;
- write-capable AI plans are limited by role at tool discovery, whole-plan preflight and MCP dispatch boundaries.

The production Spring profile uses Flyway versioned migrations and Hibernate `ddl-auto=validate`; development seed data is disabled. Hardened application containers run non-root with read-only root filesystems, dropped Linux capabilities and `no-new-privileges`.

Start the stack:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

After startup, run the no-inference runtime smoke using the configured web port:

```bash
python scripts/production_smoke.py --env-file .env.production --web-url http://127.0.0.1:8080
```

It verifies cookie identity mode, login without bearer exposure, `/me`, authenticated stock reads, ADMIN metrics, login throttling, CSRF rejection, mutation blocking and session revocation on logout without asking Ollama to generate a response.

See [`docs/production.md`](docs/production.md) for identity bootstrap, cookie/CSRF policy, TLS, migrations, backup/restore, upgrades and rollback. See [`docs/task10-identity-rbac.md`](docs/task10-identity-rbac.md) for the role/enforcement model, [`docs/task11-session-auth-hardening.md`](docs/task11-session-auth-hardening.md) for the browser-session hardening contract, and [`docs/release.md`](docs/release.md) for the guarded `vX.Y.Z` release flow and application image manifests.

## Docker acceptance profile

The normal Compose startup does **not** start destructive acceptance services. An isolated acceptance PostgreSQL instance and Spring service are available only through the `acceptance` profile:

```bash
docker compose --profile acceptance up -d postgres-acceptance stock-service-acceptance
```

Defaults:

- acceptance PostgreSQL: `localhost:5433`, database `smart_stock_acceptance`
- acceptance Spring service: `http://localhost:8082`

These acceptance ports are bound to loopback. This keeps the acceptance database separate from the normal `smart_stock` database. The existing acceptance reset script still refuses database names that do not end in `_acceptance`.

For state-changing acceptance runs, execute the runner from the host with the acceptance endpoints and the trusted reset command. The Compose profile supplies the isolated database/service topology; it does not weaken or bypass the runner's reset requirement.

## Manual development requirements

Docker is optional. To run every component directly on the host, install:

- Java 21
- Maven 3.9+
- Python 3.14+
- PostgreSQL 17
- Node.js 20+
- Git
- Ollama

The default LLM model is `qwen3:8b`.

## Manual dependency installation

### Python

```bash
pip install -r llm-host/requirements.txt -r stock-mcp/requirements.txt -r marketplace-mcp/requirements.txt
```

### Web UI

```bash
cd web-ui
npm ci
cd ..
```

## Manual PostgreSQL setup

Create the normal development database once:

```text
smart_stock
```

The Spring service reads its connection settings from environment variables. Example for PowerShell:

```powershell
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock"
$env:DB_USERNAME = "postgres"
$env:DB_PASSWORD = "your_password"
$env:SERVER_PORT = "8081"
```

The normal profile defaults to non-destructive schema updates. Do not use `DB_DDL_AUTO=create` for daily development unless you intentionally want a reset.

## Manual development stack

The normal host topology uses:

| Service | Default port |
| --- | ---: |
| PostgreSQL | 5432 |
| Spring Boot stock service | 8081 |
| Ollama | 11434 |
| LLM host HTTP API | 8000 |
| Vite web UI | 5173 |

Manual host mode is less isolated than the Docker gateway. Keep the Spring and LLM services bound to trusted interfaces and do not expose port 8081 to an untrusted network.

### 1. Spring Boot backend

```powershell
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock"
$env:DB_USERNAME = "postgres"
$env:DB_PASSWORD = "your_password"
$env:SERVER_PORT = "8081"
cd stock-service
mvn spring-boot:run
```

### 2. Ollama

```bash
ollama serve
ollama pull qwen3:8b
```

Optional overrides:

```powershell
$env:OLLAMA_URL = "http://localhost:11434/api/generate"
$env:OLLAMA_MODEL = "qwen3:8b"
```

### 3. LLM host HTTP API

Use the secured entry point for browser-facing/manual HTTP access:

```powershell
cd llm-host
$env:STOCK_SERVICE_URL = "http://localhost:8081"
uvicorn secure_api:app --host 127.0.0.1 --port 8000
```

Without `LLM_AUTH_MODE=local`, manual development uses anonymous session compatibility. To exercise local identity manually, set local auth mode plus bootstrap credentials and use a disposable identity/session database. Local identity uses browser cookies/CSRF rather than JavaScript-stored bearer credentials.

The internal `web_api:app` implementation remains available to trusted tests and development code, but it is not the authenticated public entry point.

The CLI entry point remains available:

```bash
python app.py
```

Both MCP service clients use `STOCK_SERVICE_URL` when it is set and continue to default to `http://localhost:8081` for direct local development.

### 4. Web UI

Copy `web-ui/.env.example` to `.env`, then start Vite:

```powershell
cd web-ui
$env:VITE_API_BASE_URL = "http://localhost:8081"
$env:VITE_LLM_HOST_URL = "http://localhost:8000"
npm run dev
```

Never put secrets in `VITE_*` variables because they are exposed to the browser bundle. The manual Vite topology talks directly to the local Spring read endpoints, so do not expose that Spring port to untrusted clients.

## Tests

The unit and integration suite needs neither Ollama nor a running stack. Fake MCP
modules are provided, so it also runs where the `mcp` package is not installed.

```bash
python -m unittest discover -s llm-host -p "test_*.py"   # 360 tests, ~9 s
python llm-host/golden_eval.py                            # plan contract, seconds
cd web-ui && npm test                                     # frontend unit tests
```

`golden_eval.py` checks the model-free part of the same behaviour contract — fast
path, plan parsing and state gates — so it can run on every change and in CI, while
the acceptance runner below needs a real stack and about an hour.

Two AST guards run inside the suite because their mistake was made twice:
`test_mcp_stdout.py` (MCP servers must not write to stdout, which is the JSON-RPC
channel) and `test_store_connections.py` (SQLite connections must be closed —
`with sqlite3.connect(...)` manages the transaction, not the connection).

## Isolated acceptance runner

Acceptance scenarios that change state must run against a dedicated database rather than the normal development database.

When running the acceptance database directly on the host, create once:

```text
smart_stock_acceptance
```

Start a second Spring process:

```powershell
$env:SPRING_PROFILES_ACTIVE = "acceptance"
$env:SERVER_PORT = "8082"
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock_acceptance"
$env:DB_USERNAME = "postgres"
$env:DB_PASSWORD = "your_password"
$env:PGPASSWORD = $env:DB_PASSWORD
cd stock-service
mvn spring-boot:run
```

When using the Docker acceptance profile, use port `5433` for the host-side reset connection instead:

```powershell
$env:STOCK_SERVICE_URL = "http://localhost:8082"
$env:DB_URL = "jdbc:postgresql://localhost:5433/smart_stock_acceptance"
$env:DB_USERNAME = "postgres"
$env:PGPASSWORD = "postgres"
```

The reset wrapper derives the target from the same `DB_URL` and refuses database names that do not end in `_acceptance`.

Example repeatable write scenario:

```powershell
cd llm-host
python acceptance_runner.py `
  --only pending_orders_receive `
  --runs 3 `
  --reset-command 'powershell -NoProfile -File "..\stock-service\scripts\reset-acceptance.ps1"'
```

Multiple read-only scenarios can be selected without a reset command:

```powershell
python acceptance_runner.py --only max_delivery_days --only pending_orders_listing_only --runs 1
```

## Main MCP tools

### Stock MCP

- `list_products`
- `search_products`
- `list_out_of_stock`
- `list_low_stock`
- `calculate_replenishment`
- incoming-order listing, creation and receive tools

### Marketplace MCP

- `list_sellers`
- `search_offers`
- `compare_offers`
- `create_procurement_plan`
- `create_purchase_draft`
- `place_order`

The authenticated role limits which write-capable tools are advertised to the model and which can actually be dispatched.

## Main REST surfaces

The Spring Boot service provides inventory, incoming-order and marketplace REST APIs under `/api`. In the Docker topology the browser sees them through the read-only `/stock` gateway; MCP processes reach the full service on the internal Docker network. Production additionally requires an authenticated local cookie session for `/stock` reads.

The secured LLM host provides endpoints including:

- `GET /api/health` (public liveness),
- `GET /api/ready` (public side-effect-free readiness),
- `GET /api/auth/config` (public identity/session-transport discovery),
- `POST /api/session` (anonymous development mode only),
- `POST /api/auth/login` (local identity mode; sets the cookie session),
- `GET /api/auth/me` and `POST /api/auth/logout` (authenticated local sessions; logout requires CSRF proof),
- `GET /api/metrics` (authenticated, `ADMIN`-only in local mode),
- `/api/admin/users...` (authenticated `ADMIN` user management; mutations require CSRF),
- `POST /api/chat` (authenticated session; local mutations require CSRF),
- conversation listing/detail/deletion (authenticated session; mutation methods require CSRF in local mode),
- conversation confirmation endpoints (authenticated session plus CSRF and `confirm` capability in local mode).

The dashboard consumes both services through the same-origin Nginx gateway.

## Web smoke checklist

### Development topology

After starting the default Compose stack:

1. Open `http://localhost:5173` and verify dashboard KPIs load.
2. Verify the inventory table and product filters.
3. Open marketplace, draft and incoming-order views.
4. Verify AI chat communicates through the `/llm` gateway and creates an anonymous development bearer session.
5. Ask: `Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle.`
6. Verify the first turn lists receivable orders without changing stock.
7. Verify the trace includes listing tools but no receive tool before approval.
8. Send `Onaylıyorum.` and verify only eligible delivered orders are received.
9. Verify the Drafts page does not offer a direct browser-side order mutation and routes finalization through AI Operations.
10. Exercise delivery, rating and budget constraints and inspect the observable trace arguments.

### Production topology

After production startup:

1. The browser should show a login screen before stock/application data loads.
2. Missing/invalid local cookie sessions must not read `/stock` data.
3. Log in as a named account and verify the current role is visible.
4. Verify the local browser does not store the opaque authentication credential in `localStorage` or `sessionStorage`.
5. Verify protected state-changing requests fail without valid CSRF proof.
6. `VIEWER` must remain read-only.
7. `OPERATOR` may create a purchase draft but cannot confirm/place an order.
8. `MANAGER`/`ADMIN` may perform confirmation flows permitted by the existing business state.
9. Only `ADMIN` should see/use user management and metrics.
10. Logout must return the browser to the login flow and invalidate the server-side cookie session.
