# AgentCommit V3.1

State-aware execution control for AI-initiated commerce.

> The plan may be stale; the commit must not be.

This repository was reconstructed from the preserved **V2 certified release artifact** (original V2 metadata: tag `v2-certified`, SHA `8ecedbb6c59335480e77fa011d7a415551fbb3d0`) because the original Git working tree did not persist across the execution environment. The reconstruction baseline is recorded as `v2-certified-reconstructed`; V3/V3.1 changes are forward-only deltas.

## Current scope

- V0/V1/V2 deterministic authority, reservation, CAS and replan invariants.
- Durable V3.1 commit-to-payment outbox in the **same SQLite transaction** as authority consumption.
- Deterministic Razorpay receipt identity derived from execution identity.
- Durable-before-network Order creation with `CREATE_UNKNOWN` rather than blind retry.
- Receipt-based unknown-order recovery.
- Standard Checkout server-order HMAC verification.
- Raw-body webhook verification, duplicate/out-of-order tolerance and monotonic capture truth.
- Separate physical inventory hold lifecycle (`HELD -> FULFILLED | RELEASED`).
- Late capture after inventory release becomes `COMPENSATION_REQUIRED` without re-consuming stock.

## Certification

Run stages independently or use:

```bash
make certify-v31
```

The offline release gate requires >=95% source line coverage, >=90% source branch coverage, 5/5 complete stability runs, 100k independent policy/spec cases with zero mismatches, focused concurrency/hardening tests, p95 local commit/dispatch latency <50 ms, zero source security findings, and exact Git SHA/source-hash provenance.

Full Razorpay certification remains separate and requires real **Test Mode Standard Checkout + signed webhook + API reconciliation** evidence. Offline fakes are not represented as live Razorpay evidence.

See `docs/v3_1_architecture.md` and `docs/v3_1_test_mode_checklist.md`.

## V4.0 intent-safety slice

V4 adds a typed `IntentSpec`, immutable structured `ProductFacts`, deterministic hard-constraint enforcement at the exact commit boundary, tamper/staleness hashes, and an atomic bounded replan budget. The reference compiler is a deterministic fixture only; it is not presented as the final LLM.

## V4.1 structured AI boundary

V4.1 adds a provider-agnostic structured model interface, separate constraint/clarification vocabularies, deterministic critical money/quantity cross-checks, bounded intent repair, bounded candidate replanning, prompt-injection-resistant catalog handling, and a 60-case held-out evaluation harness.

`evals/run_v41_offline.py` produces an **offline harness/reference-baseline report only**. It does not claim real-LLM accuracy; real provider metrics must record model/provider/version/date and the frozen dataset SHA-256.

## V4.2 provider-ready RC

V4.2 adds an OpenAI Responses API adapter using strict Structured Outputs and a live held-out evaluator over the frozen V4.1 dataset. This release is **provider-ready/offline certified only** when no `OPENAI_API_KEY` is available; real-model accuracy remains `NOT_RUN` until credentials are supplied and the frozen evaluator is executed unchanged.

Run live evaluation:

```bash
PYTHONPATH=src python evals/run_v42_live.py
```

Optional model override:

```bash
AGENTCOMMIT_OPENAI_MODEL=gpt-5.6-terra PYTHONPATH=src python evals/run_v42_live.py
```

## V5 Buildathon demo layer

The V5 demo is a thin FastAPI UI over the certified AgentCommit kernel. It never reimplements authority, commit, payment, or reconciliation decisions in the web layer.

Run locally:

```bash
PYTHONPATH=src uvicorn agentcommit.demo.app:app --reload --port 8000
```

Then open `http://127.0.0.1:8000`.

The page is intentionally labelled **OFFLINE DEMO — NOT REAL MONEY** whenever it uses the deterministic reference compiler and fake Razorpay-shaped gateway. Real LLM and Razorpay Test Mode results remain separate evidence gates.
