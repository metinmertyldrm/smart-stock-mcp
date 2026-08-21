# Contributing

Thanks for contributing to Smart Stock & Procurement MCP.

## Project components

- `stock-service`: Java 21 / Spring Boot backend and PostgreSQL business logic.
- `stock-mcp`: stock and inventory MCP tools.
- `marketplace-mcp`: marketplace, offer comparison, procurement and order MCP tools.
- `llm-host`: Ollama/Qwen orchestrator, MCP client, permissions, confirmation flow, conversation persistence, FastAPI API and acceptance tooling.
- `web-ui`: React + TypeScript + Vite operations dashboard.

## Branch and pull request workflow

Create a focused feature branch from the current `main` branch. Keep unrelated changes out of the same pull request and include a short summary plus exact verification commands and results.

Never commit passwords, API keys, tokens, local `.env` files, runtime databases, generated reports, dependency directories such as `node_modules`, or build output.

## Local quality checks

### Python / LLM host

```bash
python -m unittest discover -s llm-host -p 'test_*.py'
python -m py_compile llm-host/*.py
```

### Frontend

```bash
cd web-ui
npm ci
npm run lint
npm run test
npm run build
```

### Spring Boot backend

```bash
cd stock-service
mvn test
```

### Repository hygiene

```bash
git diff --check
git status
```

## Safety-sensitive behavior

Changes that can place marketplace orders or receive inventory must preserve the application's explicit confirmation model. An LLM-generated plan must never bypass backend permission or confirmation controls.

Pull requests that change behavior should include regression coverage where practical. If a verification step cannot run because of an environment or network restriction, state that explicitly instead of reporting it as passed.
