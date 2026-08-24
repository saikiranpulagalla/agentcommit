# V4.2 Live Model Evaluation

V4.2 connects the frozen V4.1 AI boundary to one real provider without changing the V4.1 held-out dataset.

Default provider/model: OpenAI Responses API / `gpt-5.6-luna` (override with `AGENTCOMMIT_OPENAI_MODEL`).

The evaluator requires `OPENAI_API_KEY`. If absent, it writes `NOT_RUN`; it never substitutes scripted outputs and never reports offline fixtures as real-model accuracy.

The evaluator refuses to run if `evals/v41/heldout_intents.jsonl` no longer has its frozen SHA-256:
`466c97b0c1eaf62e0ed95862f995224406397a0f703f9f01f9f361c1f8e00c64`.

Outputs record model name, dataset hash, intent metrics, adversarial planner metrics, and token usage. API keys are never written to the report.
