# What Broke — and What Changed Because of It

The Buildathon application explicitly asks what broke and how the builder got out. These are the most important engineering failures discovered while developing AgentCommit.

## 1. TOCTOU: checked-now, executed-later

### Bad design

```text
check quote/product facts
        ↓
queue worker
        ↓
merchant state changes
        ↓
worker executes old plan
```

The check was correct when it ran. The action was incorrect when it executed.

### Fix

- bind the execution grant to the expected plan generation/revisions/hashes;
- validate at the side-effect boundary;
- claim authority transactionally/CAS-style;
- keep the exact failing interleaving as a regression test.

**Lesson:** a second early check does not solve TOCTOU. Critical state has to be bound and validated where the side effect is admitted.

## 2. Payment uncertainty accidentally allowed another financial path

An early kernel blocked another payment only after `CAPTURED`. That meant states such as `AUTHORIZED`, `UNCERTAIN`, or an observed `FAILED` could incorrectly allow a fresh financial action—even though the original attempt might still later capture.

### Fix

- distinguish observed failure from reconciled terminal failure;
- freeze new financial paths while payment outcome is nonterminal/uncertain;
- reconcile before recovery;
- make capture monotonic.

**Lesson:** `payment.failed` is evidence, not always terminal truth.

## 3. Buyer delegation could be amplified

A reusable-looking delegation such as “one monitor up to ₹40k” could issue multiple independently valid candidate grants, allowing concurrent workers to consume more authority than the user intended.

### Fix

The current prototype makes one delegation **one-shot**. Multiple planning candidates may exist, but exactly one financial path can consume the delegation; sibling grants/reservations are revoked/released atomically.

**Lesson:** authorization ceilings need resource-consumption semantics, not only per-request comparisons.

## 4. Negative/zero money passed a naive ceiling check

A policy that only tested:

```text
amount <= max_amount
```

accepts negative values.

### Fix

Strict domain construction validates positive bounded integer money/quantity/revisions before policy evaluation. Booleans are rejected as integers at authority boundaries.

**Lesson:** equality/bounds checks cannot replace domain validity.

## 5. Inventory could leak through losing plans

When one candidate plan won, sibling reservations could remain held, reducing available inventory even though their financial authority was dead.

### Fix

Winning commit, delegation consumption, sibling grant revocation, sibling reservation cancellation and inventory release happen in one transaction.

**Lesson:** authority cleanup and resource cleanup must share atomicity.

## 6. Crash after authority commit could strand execution

The V2 transaction could commit an execution as claimed and then the process could crash before V3 created its payment task.

### Fix

Write a `PENDING` payment-dispatch outbox row in the **same transaction** that consumes financial authority. Recovery continues the same execution; it never resurrects already-spent authority.

**Lesson:** DB durability of intent and external side effects are separate problems. Persist the work before leaving the transaction.

## 7. Remote order POST could succeed while the response was lost

Blind retry after timeout/5xx could create a second remote order.

### Fix

- stable execution-derived receipt;
- durable `CREATING` state before network;
- ambiguous outcome → `CREATE_UNKNOWN`;
- lookup/reconcile by receipt rather than retrying POST.

**Lesson:** “request failed” and “side effect did not happen” are not equivalent.

## 8. Stale API data could erase known captured payment truth

The reconciler originally initialized its capture view only from the latest API response. If a valid captured webhook had already been processed but a later API read was stale/empty, local order state could downgrade from paid while execution stayed succeeded.

### Fix

Previously known captured evidence participates in the merge. `CAPTURED` is monotonic and cannot be downgraded by weaker later observations.

**Lesson:** reconciliation must converge evidence, not replace stronger truth with the newest response.

## 9. The AI safety gap: financially valid but semantically wrong product

An AI can be manipulated by catalog text into ranking a product that violates a user's hard requirement while still staying under the financial budget.

### Fix

- freeze hard intent constraints;
- model ranks known SKUs only;
- commit reads authoritative structured merchant facts;
- deterministic verifier rechecks hard constraints;
- critical explicit money/quantity language receives an additional deterministic cross-check.

**Lesson:** financial integrity and reasoning integrity are different boundaries.

## 10. Certification itself had bugs

Early certification metrics included hard-coded coverage/count claims and a reference oracle that shared omissions with the production policy.

### Fix

- evidence generated from execution rather than constants;
- declarative safety spec independent from policy structure;
- exact source provenance/hash in staged certifications;
- adversarial tests intentionally attack the oracle/harness too.

**Lesson:** a safety system can only be as trustworthy as the evidence used to claim it is safe.
