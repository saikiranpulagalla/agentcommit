# AgentCommit V4.0 — Intent Safety & Bounded Replanning

V4 adds AI-facing intent semantics without granting the model authority over financial truth.

## Core invariant

> The model may propose a product. Current structured merchant facts must independently satisfy the frozen hard constraints at commit time.

## Authority flow

1. `IntentSpec` freezes the buyer's hard constraints, soft preferences, substitution policy and clarification state.
2. `ProductFacts` contains immutable structured merchant facts. Commerce-reserved fields (`merchant_id`, `sku`, `category`, `currency`, `price_paise`, `quantity`) cannot be model-supplied attributes.
3. `delegation_intents` binds one intent version/hash to a delegation and stores the bounded replan budget.
4. Every execution grant with an attached intent records the expected intent version/hash, product-facts revision/hash and plan generation.
5. Commit reloads current persisted facts in the same SQLite writer transaction. Any drift, corruption, missing binding, clarification state or hard-constraint violation fails closed before authority is consumed.

## Hard-constraint DSL

Only a small non-executable whitelist is supported: `EQ`, `NEQ`, `LTE`, `GTE`, `IN`, `NOT_IN`.
There is no arbitrary `eval`, SQL, Python expression or model-authored executable policy.
Comparisons are type-strict, so `True` is not treated as integer `1`.

## Tamper/staleness detection

Both intent JSON and product-attribute JSON are canonicalized and SHA-256 hashed. Execution bindings carry both version/revision and hash. This detects stale state as well as accidental direct-row mutation without a counter bump.

## Replanning budget

The first plan does not consume a replan. Every later plan generation consumes exactly one persisted budget slot in the same transaction that supersedes the old plan and activates the new one. Budget CAS failure or any later fault rolls the entire replan back.

## V4.0 scope

V4.0 certifies the deterministic AI safety boundary and reference compiler. It does **not** claim that the reference compiler is the final LLM. A production model adapter may replace proposal generation later without changing the commit authority boundary.
