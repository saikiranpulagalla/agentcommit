# AgentCommit V4.1 — Structured AI Planner Boundary

V4.1 adds a provider-independent model boundary while preserving V4.0's deterministic commit authority.

## Core safety rule

> The model may interpret, rank and replan. It never certifies authority, money, merchant state, product truth or payment state.

### Intent compilation

`StructuredIntentCompiler` accepts only a strict JSON schema. Trusted caller code supplies `intent_id` and `buyer_id`; model output cannot inject either. Constraint fields and clarification concepts use separate whitelists so concepts such as `budget` may be asked about without becoming executable merchant-field predicates.

Explicit numeric money/quantity phrases are independently cross-checked by a deterministic critical extractor. If the model drops or changes an explicit budget/quantity, compilation fails or uses one bounded repair attempt.

### Planning

`CandidatePlanner` allows the model to rank only known SKU IDs. Catalog descriptions are explicitly labeled untrusted data. Every ranked candidate is re-evaluated against the frozen deterministic `IntentSpec` and current structured `ProductFacts`; a prompt-injected or hallucinated candidate cannot become executable merely because the model ranked it first.

Malformed rankings and invalid candidates may trigger bounded repair/replanning. The planner never loops unboundedly.

### Evaluation

`evals/v41/heldout_intents.jsonl` is a versioned 60-case held-out intent benchmark spanning critical money/quantity, hard structured requirements, ambiguity, substitution/soft preference, adversarial instruction-like text and compositional requests.

Offline reports distinguish:
- a deterministic reference-rule baseline (not an LLM);
- adversarial planner-boundary safety fixtures;
- real-model accuracy, which remains `null` until a real provider is connected.

## Non-goals

V4.1 offline RC does not claim production model quality and does not claim real Razorpay Test Mode coverage. It certifies the model boundary, deterministic enforcement, evaluation harness and inherited financial safety regressions.
