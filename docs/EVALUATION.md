# Evaluation and Evidence

AgentCommit keeps three evidence classes separate.

## 1. Deterministic system safety

This covers authority, state transitions, concurrency, persistence, payment reconciliation, hard-constraint enforcement and failure-lab behavior.

Representative evidence in the bundled release:

### V5 demo RC

- 413 tests collected.
- Four inherited+demo shards: all PASS.
- 17/17 demo-focused tests.
- 5/5 demo stability.
- 99% demo-layer source coverage.
- 40/40 repeated scenario executions, zero scenario failures.
- Local Uvicorn smoke: PASS.
- Dangerous runtime calls: 0.
- Secret findings: 0.

See `V5_DEMO_CERTIFICATION.json`.

### V4.2 provider-ready AI boundary

- 396/396 tests.
- 5/5 stability.
- 97.54% source line coverage.
- 92.01% source branch coverage.
- OpenAI provider adapter coverage: 98%.
- Frozen held-out dataset: 60 intent cases.
- Dataset SHA-256: `466c97b0c1eaf62e0ed95862f995224406397a0f703f9f01f9f361c1f8e00c64`.

See `V42_CERTIFICATION.json`.

## 2. Offline AI harness / baseline

The deterministic reference compiler and scripted/adversarial model fixtures validate the **evaluation harness and safety boundary**. They are not claimed as real-model accuracy.

The reference compiler intentionally performs poorly on the full held-out natural-language task. That is useful because it shows why a semantic model is needed rather than hiding behind keyword rules.

The important safety result is separate: even intentionally bad/malicious candidate rankings cannot bypass deterministic product/intent verification.

## 3. Real external-system evidence

These are explicit gates and are not replaced by simulation:

### Real LLM held-out accuracy

Status in the bundled release: **NOT_RUN**.

The provider-ready runner is `evals/run_v42_live.py`. It refuses to modify the frozen dataset and records provider/model/token/evaluation metadata without persisting the API credential.

Metrics intended for the live run:

- hard-constraint exact match;
- hard-constraint precision/recall;
- critical money/quantity accuracy;
- clarification accuracy;
- valid-plan rate;
- replanning success;
- compile/provider failure rate;
- unsafe selection rate (must remain 0 after deterministic verification).

### Razorpay Test Mode

Status in the bundled release: **NOT_RUN**.

A real certification should show:

1. server-created Test Mode Order;
2. actual Standard Checkout completion;
3. server-side Checkout signature verification;
4. signed raw-body webhook receipt;
5. webhook deduplication;
6. API reconciliation;
7. final local payment/inventory state convergence.

The offline failure lab intentionally uses a fake Razorpay-shaped gateway and labels itself visibly.

## What numbers belong in the 5-minute pitch?

Use only a few:

- **40/40** demo failure-lab runs.
- **0** demo scenario failures.
- **396/396** V4.2 provider-boundary regression tests.
- **5/5** V4.2 stability.
- **97.54% / 92.01%** V4.2 source line/branch coverage.
- **60** frozen held-out intent cases.

Then say clearly:

> Real LLM accuracy and Razorpay Test Mode are separate gates and are not represented by offline fixtures.

This honesty is stronger than presenting simulated metrics as external evidence.
