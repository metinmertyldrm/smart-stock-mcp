# TASK 09.1 — Post-production audit polish

This follow-up captures issues found during the real Windows + Docker Desktop production UI walkthrough after TASK 09.

## Scope

- Persist the HTTP request correlation ID into stored chat audit records, not only the immediate HTTP response.
- Report the production runtime identity (`APP_ENV`, application version and active LLM model) in audit telemetry.
- Enforce the production telemetry identity in the production Compose smoke contract.
- Make read-only INFO intent labels describe observation rather than imply a stock-changing action.
- Do not claim a nonexistent next plan step when the final tool call has completed the plan.
- Keep the concept glossary and debug payload secondary to the decision summary.
- Keep decision-journal section numbering contiguous when no warnings section is present.
- Use the `AI İşlem Merkezi` product name consistently in the drafts page.
- Display legacy `Eksik Stokları Tamamlama` conversation titles using the neutral `Stokta Olmayan Ürünler` label without rewriting stored historical records.

## Acceptance

CI must pass before local verification. Then rebuild only the production LLM host and web UI and verify a fresh chat shows:

- an HTTP request ID after the conversation reloads;
- `production` as the environment;
- the configured Ollama model;
- a neutral INFO intent for `Stokta olmayan ürünleri listele.`;
- no fabricated next-step wording for a one-step plan.

Finally, run a write-intent request against the empty production database and confirm it cannot create an order without a valid procurement/draft state.
