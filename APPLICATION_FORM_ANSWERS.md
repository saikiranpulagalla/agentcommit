# Razorpay AI Buildathon — Application Answers

> Finalize only the bracketed personal/logistics fields before submission. Technical claims below are restricted to evidence already present in this repository.

## Candidate details

- **Full name:** Pulagalla Sai Kiran
- **College:** Keshav Memorial Institute of Technology (KMIT), Hyderabad
- **Graduation year:** 2027
- **Track:** AI Growth & Agentic Commerce
- **In-person Bangalore from September:** [YES/NO — confirm before submit]
- **Internship duration:** [6 months / 12 months — choose one]
- **Public GitHub:** [PASTE PUBLIC REPOSITORY URL]
- **5-minute pitch video:** [PASTE VIDEO URL]
- **Resume:** [UPLOAD CURRENT RESUME]

---

## Project name

**AgentCommit — State-aware commit control for AI-initiated commerce**

### Short title alternative

**AgentCommit: The plan may be stale. The commit must not be.**

---

## What problem does it solve?

### Recommended form answer

AI commerce agents reason against snapshots. A purchase can be valid when an agent plans it, but invalid seconds later because price, inventory, buyer authorization, product facts, or payment state changed before money actually moves.

AgentCommit adds a state-aware commit boundary between an AI buyer and the financial side effect. The AI can interpret intent, rank products and replan, but it does not own buyer authority, merchant truth or payment truth. At execution time, AgentCommit revalidates current authorization, plan generation, structured merchant facts, hard user constraints, reservation state and payment state before admitting a financial action. If the plan is stale it is denied and, when allowed, replanned against current state.

The core principle is: **the plan may be stale; the commit must not be.**

### 1-sentence version

AgentCommit prevents an AI-generated purchase that was valid when planned from moving money after authorization, merchant state or payment truth has changed.

---

## How does AI meaningfully contribute?

The AI layer interprets natural-language buyer intent, separates hard constraints from soft preferences, ranks catalog candidates, explains choices and replans when a candidate becomes invalid. It is intentionally not authoritative: critical money/quantity language gets a deterministic cross-check, catalog descriptions are untrusted data, and every selected SKU is revalidated against structured merchant facts before commit.

This separation lets model quality affect usefulness without letting model mistakes redefine financial safety.

---

## Architecture summary

```text
Buyer request
    ↓
AI intent compiler / planner
    ↓
Frozen IntentSpec (hard + soft constraints)
    ↓
Buyer delegation
    ↓
Versioned product facts + merchant reservation
    ↓
ExecutionGrant bound to versions/hashes
    ↓
AGENTCOMMIT
  - delegation still valid?
  - plan generation current?
  - product facts current?
  - hard constraints still satisfied?
  - reservation current?
  - payment state permits a new financial side effect?
    ↓
Durable payment dispatch outbox
    ↓
Razorpay Order / Checkout boundary
    ↓
Webhook + API reconciliation
    ↓
Fulfil / replan / compensate
```

The LLM owns no authoritative state. Buyer authority, merchant state and payment state remain separate sources of truth.

---

## What broke, and how did you get out?

### Recommended answer

My first version validated merchant state before queueing the payment worker. A concurrency test reproduced a classic TOCTOU bug: the product/quote changed while the worker waited, but the old plan still executed because it had already passed an earlier check.

I fixed it by binding each ExecutionGrant to the expected plan generation, authorization version and merchant-state revisions/hashes, then moving validation and claiming to the exact side-effect boundary. If any bound state changes, the stale worker is denied and must replan. I kept the original failing interleaving as a permanent regression test.

That adversarial process exposed deeper issues too: uncertain payments could accidentally allow another financial path, a one-shot buyer delegation could be amplified by sibling grants, losing plans could leak inventory, a crash after authority commit could strand payment work, and stale API data could downgrade a known captured payment. Each failure changed the architecture: reconciliation before recovery, one-shot authority, atomic sibling cleanup, durable outbox dispatch, deterministic external receipt identity and monotonic captured-payment truth.

### Short version

The first implementation checked merchant state before queueing a worker, creating a TOCTOU race: state changed while the worker waited and the stale purchase could still execute. I reproduced it with a concurrency test, moved state-bound validation/claiming to the actual commit boundary, and bound the grant to expected authorization/plan/product revisions and hashes. The stale interleaving is now a permanent regression. The same red-team process later found and fixed payment-uncertainty, delegation-amplification, inventory-leak and crash-recovery bugs.

---

## Evidence / metrics

Use these exactly; do not inflate them:

- **V5 demo scenarios:** 40/40 repeated offline failure-lab executions passed.
- **V4.2 provider-ready boundary:** 396 tests across 5 clean stability runs.
- **V4.2 source coverage:** 97.54% line / 92.01% branch.
- **OpenAI provider adapter:** 98% covered in the V4.2 certification.
- **Frozen AI evaluation set:** 60 held-out intent cases; dataset hash is recorded in the repo.
- **Adversarial planner fixture:** 0 unsafe selections in the scripted/offline safety evaluation.
- **Real-model held-out accuracy:** NOT RUN in this bundled release because provider credentials were unavailable.
- **Real Razorpay Test Mode Checkout/webhook:** NOT RUN in this bundled release; the offline demo is clearly labeled NOT REAL MONEY.

### Why these numbers matter

The test count is not the pitch. The important evidence is that stale state, concurrency, payment uncertainty and model mistakes were used to change the design, and the four demo scenarios show those failures visibly end-to-end.

---

## Why Razorpay / track fit?

Razorpay's AI Growth & Agentic Commerce track asks builders to make merchants transactable by AI buyers, with every money action explainable, bounded and gated, an audit trail, and failure handling. AgentCommit is built around exactly that execution boundary.

It does not try to replace Razorpay's payment rails, Agent Studio or payment intelligence. It focuses on the layer immediately before those rails: whether an AI-originated commerce action is still authorized and valid when it reaches money-moving infrastructure.

---

## Demo scenario to emphasize

Buyer: **“Buy me the cheapest 27-inch 4K USB-C monitor under ₹40,000. You can choose another model if the first becomes unavailable.”**

1. AI selects Monitor A.
2. Execution authority is bound to current structured facts.
3. Inject a merchant-state change so Monitor A no longer satisfies USB-C.
4. Old worker reaches AgentCommit → **DENIED: stale/current facts violate hard intent**.
5. Bounded replan chooses Monitor B.
6. Fresh execution authority is created.
7. Payment flow proceeds.
8. Timeline shows exactly why the stale action was denied and why the replacement was allowed.

Then briefly show one remote/payment failure:

- ambiguous Order creation → recover existing Order by deterministic receipt, **no blind duplicate POST**, or
- late capture after inventory release → **COMPENSATION_REQUIRED**, not fake fulfilment.

---

## Known limitations — say this if asked

- The bundled demo is offline and uses a deterministic/reference model path plus a Razorpay-shaped fake gateway.
- The real-model adapter and frozen held-out evaluator exist, but real-model accuracy is not reported without provider credentials.
- Real Razorpay Standard Checkout/Test Mode evidence is a separate gate and is not replaced with fake numbers.
- This is a prototype for execution-control semantics, not a production PCI/payment platform or a replacement for Razorpay's existing risk/payment systems.

---

## Final one-line close

**Agents may plan optimistically. Money commits only against current authority and current reality.**
