# Full-stack smoke verification

Use this check after `docker compose up --build` has finished starting the normal development stack.

The verifier is cross-platform and uses only the Python standard library:

```bash
python scripts/smoke_stack.py
```

The deterministic default run checks:

1. Spring `GET /api/products` returns a JSON product list.
2. Ollama responds and the configured `OLLAMA_MODEL` (default `qwen3:8b`) is installed.
3. LLM Host `GET /api/health` reports `status=ok`.
4. Nginx/Web UI `GET /healthz` responds successfully.
5. LLM conversation persistence can create, fetch, list and delete an owner-scoped smoke conversation.

These checks verify the running topology without asking the model to generate a plan, so they are suitable for a quick repeatable infrastructure smoke test.

## Optional end-to-end LLM + MCP turn

After the deterministic checks pass, add `--chat` to execute one real read-only natural-language request through Ollama, the LLM Host and MCP servers:

```bash
python scripts/smoke_stack.py --chat
```

The chat smoke test asserts that:

- the request completes successfully;
- the host assigns `PLAN` permission;
- no write-capable purchasing or stock-receive tool is executed.

The smoke conversation is deleted after the test.

The LLM Host request timeout is configurable through `OLLAMA_CONNECT_TIMEOUT` and `OLLAMA_READ_TIMEOUT`; the Docker defaults remain 20 seconds / 300 seconds. The optional smoke chat timeout is independently configurable through `SMOKE_CHAT_TIMEOUT` / `--chat-timeout` and defaults to 330 seconds.

The optional chat is attempted only once. If the client times out, immediately retrying can overlap with a still-running server-side generation and produce misleading failures.

A real Windows/Docker Desktop CPU-only run with `qwen3:8b` showed that the current execution-planning prompt can exceed both 300 seconds and an experimental 600-second window. That is treated as a model/prompt performance finding rather than a reason to keep increasing default timeouts. Prompt size, local model sizing and the synchronous Ollama call are follow-up work for the LLM-host refactor/resilience tasks.

For diagnostics only, the windows can still be overridden explicitly:

```bash
OLLAMA_READ_TIMEOUT=600 docker compose up -d --build llm-host
python scripts/smoke_stack.py --chat --chat-timeout 630
```

On PowerShell, set the values in `.env` or use the CLI `--chat-timeout` flag for the smoke client.

## Custom ports / URLs

The defaults match `docker-compose.yml`:

```text
Stock service  http://localhost:8081
LLM Host       http://localhost:8000
Web UI         http://localhost:5173
Ollama         http://localhost:11434
```

Override them with CLI flags:

```bash
python scripts/smoke_stack.py \
  --stock-url http://localhost:18081 \
  --llm-url http://localhost:18000 \
  --web-url http://localhost:15173 \
  --ollama-url http://localhost:11434
```

or environment variables:

```text
SMOKE_STOCK_URL
SMOKE_LLM_URL
SMOKE_WEB_URL
SMOKE_OLLAMA_URL
SMOKE_CHAT_TIMEOUT
OLLAMA_MODEL
OLLAMA_CONNECT_TIMEOUT
OLLAMA_READ_TIMEOUT
```

## Startup retries

By default each deterministic service check is retried 12 times with a 5 second delay. This lets the verifier be started shortly after Compose while services are still becoming healthy.

Tune this if needed:

```bash
python scripts/smoke_stack.py --retries 20 --retry-delay 3 --timeout 15
```

A failed check exits with status code `1`; a complete pass exits with `0`.

## Relationship to acceptance tests

This script is not a replacement for `llm-host/acceptance_runner.py`.

- `scripts/smoke_stack.py` verifies service connectivity, basic persistence and an optional safe end-to-end read-only turn.
- `llm-host/acceptance_runner.py` measures richer LLM planning behavior, tool selection, repeated-run success and write scenarios against the isolated acceptance database.

Run smoke verification first. Run the acceptance suite when validating agent behavior or release readiness.
