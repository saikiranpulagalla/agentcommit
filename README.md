# AgentCommit

**State-aware commit control for AI-initiated commerce.**

> **The plan may be stale. The commit must not be.**

AgentCommit sits between an AI buyer's plan and the financial side effect. The AI may interpret intent, search, rank, explain, and replan. It does **not** own buyer authority, merchant truth, payment truth, or permission to move money.

This repository is prepared for the **Razorpay AI Buildathon — AI Growth & Agentic Commerce** track. The current demo is deliberately labeled **OFFLINE DEMO — NOT REAL MONEY** whenever it uses the deterministic reference compiler and fake Razorpay-shaped gateway.

**Release note:** this V5 Submission RC changes reviewer documentation/workflow only; the executable safety/payment/demo kernel is inherited unchanged from the preserved V5 Demo RC. `SUBMISSION_RELEASE.json` records the imported artifact hash and current evidence.
> **Evidence scope:** the V5 certification and release JSON files are historical evidence for the original V5 RC. This successor hardening candidate is documented separately in [Hardening Candidate](docs/HARDENING_CANDIDATE.md); it does not relabel historical evidence or turn any `NOT_RUN` integration claim into a PASS claim.

## 30-second version

A normal AI buyer can make a valid decision at 12:00:00 and execute an invalid one at 12:00:05 because price, inventory, authorization, product facts, or payment state changed in between.

AgentCommit adds an execution boundary:

```text
Human intent
   ↓
AI proposes a plan
   ↓
Buyer delegation + merchant reservation
   ↓
Execution grant bound to current versions/hashes
   ↓
AGENTCOMMIT
  - authority still valid?
  - plan generation current?
  - structured product facts current?
  - hard intent constraints still satisfied?
  - reservation current?
  - payment state permits a new side effect?
   ↓
Razorpay Order / Checkout boundary
   ↓
Webhook + API reconciliation
   ↓
Fulfil, replan, or compensate
```

The model can be wrong. The commit gate must still be correct.

## What is actually novel here?

AgentCommit is **not** another fraud model, payment router, generic AI guardrail layer, or replacement payment system.

It focuses on one failure class:

> **stale-plan execution** — an AI-generated action was valid when planned but is no longer valid when the financial side effect is about to occur.

The contribution is applying established systems techniques—version binding, one-shot authority, CAS/transactional admission, durable outbox, reconciliation, monotonic payment truth, and deterministic hard-constraint verification—to **probabilistic agent plans operating over changing commerce/payment state**.

## Demo in 60 seconds

Install and run:

```bash
python -m pip install -e '.[demo]'
PYTHONPATH=src uvicorn agentcommit.demo.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

Use the default buyer request:

> Buy me the cheapest 27-inch 4K USB-C monitor under ₹40,000. You can choose another model if the first becomes unavailable.

Then run these four scenarios:

1. **Happy Path** — valid plan → state-aware commit → captured payment → fulfilled inventory.
2. **Stale Product → Replan** — merchant facts change after planning → old commit denied → substitute gets a fresh plan/grant → success.
3. **Crash / Unknown Order Recovery** — durable outbox survives the crash → stable receipt finds the existing remote Order → no duplicate POST.
4. **Late Capture → Compensation** — inventory is released only after reconciliation; if money is later known captured, execution becomes `COMPENSATION_REQUIRED` instead of pretending fulfilment succeeded.

See [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) for the rehearsed 5-minute flow.

## Core invariants

AgentCommit is designed around these non-negotiable properties:

- A revoked/expired/consumed buyer authority cannot commit.
- One one-shot delegation can produce at most one winning financial path.
- Merchant/resource/amount/currency/product bindings cannot be substituted silently.
- A stale plan generation, quote/reservation revision, intent version, or product-facts revision/hash cannot commit.
- The model cannot attest that a product satisfies a hard constraint; current structured merchant facts are checked deterministically at commit.
- `CAPTURED` payment truth is monotonic: later weaker events/API observations cannot erase known money movement.
- Ambiguous remote order creation is **reconciled**, not blindly retried.
- A committed execution cannot be stranded between authority consumption and payment dispatch: a durable outbox is written transactionally.
- Physical inventory hold state is separate from payment state; a late capture after release leads to compensation rather than silent inventory corruption.
- Terminal execution contexts are never resurrected. Recovery continues the same lineage or creates a new explicitly linked path.

See [`docs/ARCHITECTURE_OVERVIEW.md`](docs/ARCHITECTURE_OVERVIEW.md) and [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## AI boundary

The AI layer is intentionally narrow:

```text
Model may:
  interpret natural language
  produce structured constraints/preferences
  rank known SKU IDs
  replan within bounded authority
  explain its reasoning

Model may NOT:
  choose buyer identity
  mint authority
  change budget
  fabricate merchant facts
  certify hard constraints
  create an ExecutionGrant
  decide payment truth
  bypass reconciliation
  issue arbitrary code/SQL/tool actions
```

Hard structured constraints are rechecked against authoritative product facts inside the same commit transaction. Critical budget/quantity language also has a deterministic cross-check so model omission cannot silently remove an explicit monetary bound.

A frozen 60-case held-out intent dataset exists at `evals/v41/heldout_intents.jsonl`. The real-model evaluator is provider-ready, but **real LLM accuracy is currently `NOT_RUN` in the included evidence because provider credentials were not available when the RC was built.** No scripted score is substituted.

## Razorpay boundary

The payment layer is built around current Razorpay-style integration semantics:

- durable local order intent before the remote POST;
- deterministic, execution-derived receipt identity;
- ambiguous create outcome → `CREATE_UNKNOWN`, then receipt lookup/reconciliation;
- Standard Checkout signature verification bound to the server-stored order identity;
- raw-body webhook signature verification;
- webhook event-ID deduplication and tampered-duplicate detection;
- duplicate/out-of-order event tolerance;
- API reconciliation for critical payment truth;
- late `failed → captured` convergence without starting a stale second financial path.

The included V5 demo uses a fake Razorpay-shaped gateway and is visibly labeled as such. **Real Razorpay Test Mode Checkout/webhook certification is separate and currently `NOT_RUN` in the bundled evidence.**

## Evidence snapshot

### V5 demo RC

| Check | Result |
|---|---:|
| Tests collected | **413** |
| Inherited + demo test shards | **4/4 PASS** |
| Demo-focused tests | **17/17 PASS** |
| Demo stability | **5/5 PASS** |
| Demo-layer source coverage | **99%** |
| Repeated demo scenarios | **40/40 PASS** |
| Scenario failures | **0** |
| Local Uvicorn smoke | **PASS** |
| Security headers | **PASS** |
| Dangerous runtime calls | **0** |
| Secret findings | **0** |
| Real LLM evaluation | **NOT_RUN** |
| Razorpay Test Mode | **NOT_RUN** |

### V4.2 provider-ready AI boundary

| Check | Result |
|---|---:|
| Tests | **396/396 PASS** |
| Stability | **5/5 PASS** |
| Source line coverage | **97.54%** |
| Source branch coverage | **92.01%** |
| OpenAI provider adapter coverage | **98%** |
| Frozen held-out cases | **60** |
| Security/secret findings | **0** |
| Real-model accuracy | **NOT_RUN** |

Evidence files are included in the release root: `V5_DEMO_CERTIFICATION.json`, `V42_CERTIFICATION.json`, and `V42_LIVE_EVAL_STATUS.json`.

## What broke during development?

The strongest failure story is a real TOCTOU defect found during the build:

```text
v1 design:
validate quote/product state
      ↓
queue work
      ↓
merchant state changes
      ↓
old worker executes anyway   ← bug
```

The fix was not “check one more time earlier.” The execution authority became bound to the expected state and was admitted at the side-effect boundary using transactional/CAS semantics. The stale interleaving is now a permanent regression test.

Other bugs found by adversarial review included payment uncertainty permitting a second path, negative/zero money acceptance, reusable delegation authority amplification, inventory leakage from losing plans, stale API observations downgrading captured payment truth, and crash gaps between commit and payment dispatch. See [`docs/WHAT_BROKE.md`](docs/WHAT_BROKE.md).

## Why this fits Razorpay now

Razorpay's public Agent Studio principles emphasize merchant-defined boundaries, verified merchant data, platform validation before execution, and explicit control for irreversible actions. AgentCommit is complementary: it asks whether an already-authorized AI-generated transaction is **still admissible when current state is re-read at execution time**.

Razorpay also launched Vulcan as a payments foundation model focused on payment reliability, fraud/risk and checkout intelligence. AgentCommit does not compete with that layer; it governs whether an AI-originated commerce action should be allowed to reach the payment layer at all.

Official context:

- Razorpay AI Buildathon: https://razorpay.com/buildathon/
- Agent Studio guardrails: https://razorpay.com/blog/?p=26508
- Razorpay Vulcan: https://razorpay.com/blog/?p=27542

## Repository map

```text
src/agentcommit/domain/       deterministic authority + state model
src/agentcommit/store/        SQLite transaction/CAS + merchant/intent persistence
src/agentcommit/payments/     order intent, signatures, webhooks, reconciliation
src/agentcommit/ai/           intent compiler, structured model boundary, planner/evals
src/agentcommit/demo/         thin FastAPI Buildathon UI only

tests/                        unit, adversarial, process-race and regression suites
evals/                        frozen held-out data + certification/evaluation harnesses
docs/                         architecture, threat model, demo/panel runbooks
```

## Reviewer path — 2 minutes

If you are reviewing this repo quickly:

1. Read this README through **Core invariants**.
2. Open [`docs/ARCHITECTURE_OVERVIEW.md`](docs/ARCHITECTURE_OVERVIEW.md).
3. Run the **Stale Product → Replan** demo.
4. Read [`docs/WHAT_BROKE.md`](docs/WHAT_BROKE.md).
5. Inspect `src/agentcommit/store/sqlite_store.py` for transactional admission and `src/agentcommit/payments/service.py` for ambiguity/reconciliation.
6. Inspect `src/agentcommit/ai/structured.py` + `src/agentcommit/ai/planner.py` for the model boundary.
7. Check `V5_DEMO_CERTIFICATION.json` for demo evidence and `V42_LIVE_EVAL_STATUS.json` for evidence that is intentionally **not** claimed.

## Evidence honesty / current limitations

This repository intentionally distinguishes:

**Proven in the included offline RC:** deterministic authority/commit semantics, persistent concurrency behavior, state reconciliation logic, provider boundary validation, offline AI harness, and the four demo failure scenarios.

**Not proven by the included release:** real-model held-out accuracy and a complete real Razorpay Test Mode Standard Checkout + signed webhook flow. Both are explicit separate gates and remain labeled `NOT_RUN` rather than being simulated and reported as live evidence.

That distinction is part of the project, not a disclaimer added at the end: **AgentCommit's thesis is that financial systems should know what they know, know what they do not know, and fail safely in between.**

## Submission pack

- [`APPLICATION_FORM_ANSWERS.md`](APPLICATION_FORM_ANSWERS.md) — form-ready project answers and evidence wording.
- [`VIDEO_REHEARSAL.md`](VIDEO_REHEARSAL.md) — timed camera/demo rehearsal.
- [`FINAL_SUBMISSION_CHECKLIST.md`](FINAL_SUBMISSION_CHECKLIST.md) — final public-repo/video/application checks.
