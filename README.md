# Smart Stock & Procurement MCP

Smart Stock & Procurement MCP is an AI-assisted inventory and procurement system built around the Model Context Protocol (MCP). It tracks warehouse inventory, detects low-stock products, compares marketplace offers, creates procurement plans, and lets operators control sensitive actions through natural-language requests with explicit confirmation gates.

## Architecture

The repository contains five main components:

- `stock-service`: Java 21 / Spring Boot REST backend with PostgreSQL persistence and the core inventory, order and marketplace business rules.
- `stock-mcp`: Python MCP server exposing stock and incoming-order tools.
- `marketplace-mcp`: Python MCP server exposing seller, offer, draft, order and procurement-plan tools.
- `llm-host`: Python orchestrator using Qwen through Ollama, execution planning, MCP calls, permission/confirmation handling, conversation persistence, FastAPI endpoints and acceptance tooling.
- `web-ui`: React + TypeScript + Vite operations dashboard and AI operations center.

The LLM proposes execution plans, but business rules and write-safety controls remain enforced outside the model. Purchase finalization and inventory receiving flows preserve explicit confirmation requirements.

## Key features

- Natural-language stock and procurement operations in Turkish or English.
- Low-stock and out-of-stock detection.
- Replenishment quantity calculation.
- Marketplace offer search and TOPSIS-based multi-criteria comparison.
- Cheapest, fastest, highest-rated and balanced procurement strategies.
- Purchase draft and marketplace order workflows.
- Incoming-order tracking and safe receive flow.
- Persistent AI conversations.
- Observable execution plan, MCP trace, telemetry and user-facing decision journal.
- Isolated acceptance database tooling for repeatable write scenarios.
- React/Vite operations dashboard.

## Quick start with Docker Compose

Docker Compose is the recommended way to run the complete local stack. It starts PostgreSQL, the Spring service, Ollama, the LLM host with both MCP subprocesses, and the production-built web dashboard.

### Requirements

- Docker Engine / Docker Desktop with Compose v2
- Git

Clone the repository:

```bash
git clone https://github.com/metinmertyldrm/smart-stock-mcp.git
cd smart-stock-mcp
```

Optional: copy the environment template if you want to override ports, the local database password, or the Ollama model.

```bash
cp .env.example .env
```

Start everything:

```bash
docker compose up --build
```

On the first run, the `ollama-init` container downloads `qwen3:8b`, so startup can take several minutes depending on the network connection. The model and PostgreSQL data are kept in named Docker volumes and do not need to be downloaded/recreated on every restart.

Default endpoints:

| Service | Address |
| --- | --- |
| Web dashboard | `http://localhost:5173` |
| Spring stock service | `http://localhost:8081` |
| LLM host API | `http://localhost:8000` |
| Ollama | `http://localhost:11434` |
| PostgreSQL | `localhost:5432` |

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

Stop the stack while keeping data:

```bash
docker compose down
```

Remove the stack and its local Docker volumes only when you intentionally want to delete local database, conversation, and Ollama model data:

```bash
docker compose down -v
```

The default `DB_PASSWORD=postgres` in Compose is strictly a local-development convenience. Override it in `.env` for any shared environment. This Compose stack is a development/demo topology, not a production deployment configuration.

## Docker acceptance profile

The normal Compose startup does **not** start destructive acceptance services. An isolated acceptance PostgreSQL instance and Spring service are available only through the `acceptance` profile:

```bash
docker compose --profile acceptance up -d postgres-acceptance stock-service-acceptance
```

Defaults:

- acceptance PostgreSQL: `localhost:5433`, database `smart_stock_acceptance`
- acceptance Spring service: `http://localhost:8082`

This keeps the acceptance database separate from the normal `smart_stock` database. The existing acceptance reset script still refuses database names that do not end in `_acceptance`.

For state-changing acceptance runs, execute the runner from the host with the acceptance endpoints and the trusted reset command documented below. The Compose profile only supplies the isolated database/service topology; it does not weaken or bypass the runner's reset requirement.

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

```powershell
cd llm-host
$env:STOCK_SERVICE_URL = "http://localhost:8081"
uvicorn web_api:app --host 0.0.0.0 --port 8000
```

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

Never put secrets in `VITE_*` variables because they are exposed to the browser bundle.

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

## Main REST surfaces

The Spring Boot service provides inventory, incoming-order and marketplace REST APIs under `/api`.

The LLM host provides endpoints including:

- `GET /api/health`
- `POST /api/chat`
- conversation listing/detail/deletion
- conversation confirmation endpoints

The dashboard consumes both services.

## Web smoke checklist

After starting the complete stack:

1. Open `http://localhost:5173` and verify dashboard KPIs load.
2. Verify the inventory table and product filters.
3. Open marketplace, draft and incoming-order views.
4. Verify AI chat communicates with `http://localhost:8000`.
5. Ask: `Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle.`
6. Verify the first turn lists receivable orders without changing stock.
7. Verify the trace includes listing tools but no receive tool before approval.
8. Send `Onaylıyorum.` and verify only eligible delivered orders are received.
9. Exercise delivery, rating and budget constraints and inspect the observable trace arguments.

## Quality checks

### Python

```bash
python -m unittest discover -s llm-host -p 'test_*.py'
python -m py_compile llm-host/*.py stock-mcp/*.py marketplace-mcp/*.py
```

### Frontend

```bash
cd web-ui
npm ci
npm run lint
npm run test
npm run build
```

### Java

```bash
cd stock-service
mvn test
```

### Docker

```bash
docker compose config
docker compose build
```

### Repository hygiene

```bash
git diff --check
git status
```

## Environment and secrets

- Local `.env` files are ignored.
- Runtime SQLite conversation databases are ignored.
- Generated acceptance reports are ignored.
- `node_modules`, Vite output, coverage and common Python caches are ignored.
- Server-side secrets must never be exposed through `VITE_*` variables.
- The Docker Compose defaults are intended for local development only.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution and verification expectations.

## Planned improvements

Potential future work includes real marketplace provider integrations, demand forecasting, notifications, CI/CD, stronger deployment automation and additional production hardening.
