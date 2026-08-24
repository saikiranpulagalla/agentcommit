# AgentCommit V4.2 — Real Provider Boundary

V4.2 connects the V4.1 provider-neutral `JsonModel` boundary to the OpenAI Responses API using strict Structured Outputs.

## Non-authority rule

The provider adapter has no financial or authorization authority. It can only return decoded JSON. `StructuredIntentCompiler`, `CandidatePlanner`, deterministic hard-constraint evaluation, V4 commit-time intent/facts binding, and the V0–V3.1 payment safety kernel remain authoritative.

## API contract

- Endpoint: `POST /v1/responses` over HTTPS.
- `store=false`.
- Structured output configured with `text.format.type=json_schema`, `strict=true`.
- Separate schemas are used for intent extraction and candidate ranking.
- Provider schemas intentionally constrain structure only. Semantic bounds/identity/known-SKU checks are enforced by deterministic AgentCommit code.
- Refusal, incomplete response, malformed envelope/output, transport failure, or invalid JSON becomes `ModelFailure` and never an authorization decision.

## Held-out integrity

V4.2 reuses the exact frozen V4.1 60-case dataset. Live evaluation refuses to run if its SHA-256 differs from:

`466c97b0c1eaf62e0ed95862f995224406397a0f703f9f01f9f361c1f8e00c64`

## Live certification boundary

`evals/run_v42_live.py` defaults to `gpt-5.6-luna` and requires `OPENAI_API_KEY`. When credentials are absent it writes `NOT_RUN`; scripted fixtures are never substituted for a real-model score.

A live report records provider/model, dataset hash, intent metrics, adversarial planning metrics, and token usage. It never writes the API key.
