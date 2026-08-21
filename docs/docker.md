# Docker Compose workflow

The Docker stack preserves the existing architecture. The Stock MCP and Marketplace MCP servers still use MCP stdio and are launched as child Python processes by `llm-host`; Docker does not convert them into separate HTTP services.

## Services

| Compose service | Purpose | Default host port |
| --- | --- | ---: |
| `postgres` | PostgreSQL 17 development database | 5432 |
| `stock-service` | Spring Boot inventory/procurement API | 8081 |
| `ollama` | Local LLM runtime | 11434 |
| `ollama-model` | One-shot model pull/verification job | none |
| `llm-host` | FastAPI orchestrator + stdio MCP processes | 8000 |
| `web-ui` | Production Vite build served by nginx | 5173 |

An optional `acceptance` profile adds `acceptance-db-init` and `stock-service-acceptance` on port 8082, backed by the separate `smart_stock_acceptance` database.

## First start

Copy the template if you want to override defaults:

```bash
cp .env.example .env
```

Then build and start the stack:

```bash
docker compose up --build
```

The first startup can take several minutes because the Spring image downloads Maven dependencies and `ollama-model` pulls `qwen3:8b` into the persistent Ollama volume.

When all health checks are green:

- Web UI: `http://localhost:5173`
- Stock API: `http://localhost:8081`
- LLM host health: `http://localhost:8000/api/health`
- Ollama: `http://localhost:11434`

Run in the background with:

```bash
docker compose up --build -d
```

## Logs and status

```bash
docker compose ps
docker compose logs -f stock-service
docker compose logs -f llm-host
docker compose logs -f ollama
```

## Stop and restart

Stop containers while preserving data:

```bash
docker compose down
```

Restart later:

```bash
docker compose up -d
```

The named volumes preserve PostgreSQL data, downloaded Ollama models and LLM conversation history.

To intentionally delete all Docker-managed project data:

```bash
docker compose down -v
```

`down -v` is destructive. It removes the normal development database, Ollama model volume and persisted conversation database.

## Configuration

The root `.env.example` documents supported Compose variables. Important values include:

- `DB_USERNAME` / `DB_PASSWORD`
- `POSTGRES_PORT`
- `STOCK_SERVICE_PORT`
- `LLM_HOST_PORT`
- `WEB_UI_PORT`
- `OLLAMA_PORT`
- `OLLAMA_MODEL`
- `OLLAMA_NUM_CTX`
- `OLLAMA_NUM_PREDICT`
- `CORS_ALLOWED_ORIGINS`
- `LLM_CORS_ALLOWED_ORIGINS`
- `VITE_API_BASE_URL`
- `VITE_LLM_HOST_URL`

`VITE_*` values are public browser configuration, not secrets. They are compiled into the frontend bundle while the `web-ui` image is built. If you change the public hostname or mapped backend ports, update the `VITE_*` values and rebuild the frontend image:

```bash
docker compose build web-ui
docker compose up -d web-ui
```

Never put passwords, tokens or API keys in `VITE_*` variables.

## Service-to-service addressing

Inside the Compose network, containers use Compose DNS names rather than host-loopback addresses:

- Spring -> PostgreSQL: `postgres:5432`
- LLM host / MCP subprocesses -> Spring: `stock-service:8081`
- LLM host -> Ollama: `ollama:11434`

The MCP subprocesses inherit `STOCK_SERVICE_URL` from the `llm-host` container. Local non-Docker execution still defaults to `http://localhost:8081`.

## Acceptance profile

Start the normal stack plus the isolated acceptance backend:

```bash
docker compose --profile acceptance up --build -d
```

The one-shot `acceptance-db-init` service creates `smart_stock_acceptance` only if it does not already exist. `stock-service-acceptance` then starts with the Spring `acceptance` profile on host port 8082.

For read-only acceptance scenarios, run the existing runner on the host:

```powershell
cd llm-host
$env:STOCK_SERVICE_URL = "http://localhost:8082"
python acceptance_runner.py --only max_delivery_days --only pending_orders_listing_only --runs 1
```

For write scenarios, keep using the repository's guarded reset command. It refuses targets whose database name does not end in `_acceptance`:

```powershell
cd llm-host
$env:STOCK_SERVICE_URL = "http://localhost:8082"
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock_acceptance"
$env:DB_USERNAME = "postgres"
$env:PGPASSWORD = "postgres"
python acceptance_runner.py `
  --only pending_orders_receive `
  --runs 3 `
  --reset-command 'powershell -NoProfile -File "..\stock-service\scripts\reset-acceptance.ps1"'
```

If you changed `POSTGRES_PORT` or database credentials in `.env`, use the same values in the acceptance runner environment.

## Rebuilding one component

Examples:

```bash
docker compose build stock-service
docker compose up -d stock-service
```

```bash
docker compose build llm-host
docker compose up -d llm-host
```

```bash
docker compose build web-ui
docker compose up -d web-ui
```

## Ollama model changes

Set another model in `.env`:

```dotenv
OLLAMA_MODEL=qwen3:8b
```

Then run the model job and recreate the LLM host:

```bash
docker compose run --rm ollama-model
docker compose up -d --force-recreate llm-host
```

The model data remains in the `ollama_data` named volume.
