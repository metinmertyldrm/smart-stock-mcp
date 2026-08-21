# Golden AI evaluations

Smart Stock has two complementary AI evaluation layers. They answer different
questions and should not be treated as substitutes for each other.

## 1. Offline golden contract evaluation

Run from the repository root:

```bash
python llm-host/golden_eval.py
```

The golden evaluator requires no Ollama, Spring Boot service, MCP subprocess, or
database. It loads `llm-host/golden_cases.json` and checks each curated user
intent against production contract code:

- fast read-only route classification,
- execution-plan parsing and goal rules,
- ORDER / RECEIVE conversation-state safety guards,
- required and forbidden tools,
- critical argument mappings such as total budget, delivery limit, seller rating,
  and procurement objective.

A successful golden run means the deterministic AI orchestration contract still
accepts the intended plans and rejects the explicitly unsafe ones. It does **not**
mean the live language model will always produce those plans.

Useful commands:

```bash
# One case
python llm-host/golden_eval.py --only budget_maps_to_total_budget

# Machine-readable report
python llm-host/golden_eval.py --json golden-eval.json
```

The CI Python job runs the evaluator explicitly in addition to normal unit tests.
Any golden regression therefore fails the repository quality gate.

## 2. Live acceptance evaluation

`llm-host/acceptance_runner.py` exercises the actual LLM and MCP/business stack.
It measures repeated end-to-end success and plan variance, so it is the layer for
questions such as:

- Does the configured Qwen model actually produce a valid plan for this wording?
- How often does the same request choose a different plan shape?
- Does a complete real-stack scenario execute successfully?

Example read-only run:

```bash
cd llm-host
python acceptance_runner.py --runs 3
```

Write scenarios require the isolated acceptance database and a reset command.
The runner intentionally refuses to execute write scenarios without that reset
boundary.

## Adding a golden case

Prefer behavioral contracts over exact prose snapshots. A case should specify:

1. a stable user intent,
2. whether it may use the deterministic fast route or must stay on the full planner,
3. one canonical valid execution plan,
4. existing acceptance expectations when an equivalent live scenario exists,
5. explicit state and `validation_error_contains` when the correct result is host-side rejection.

New cases should target meaningful failure modes. Examples include an unsafe tool
appearing before confirmation, a budget being mapped to unit price instead of
total budget, or a procurement request accidentally entering the read-only fast
path. Avoid adding multiple cases that differ only cosmetically.

## Interpretation

The layers form a small evaluation pyramid:

- **Unit tests** verify individual implementation details.
- **Golden evals** lock cross-module AI orchestration contracts deterministically.
- **Live acceptance** measures real model + MCP + service behavior and variance.

A release-quality change should keep the first two layers green in CI and use live
acceptance when the prompt, model, routing behavior, or write-flow semantics are
materially changed.
