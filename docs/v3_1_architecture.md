# AgentCommit V3.1 — Durable Payment Dispatch and Inventory Lifecycle

V3.1 closes the two liveness gaps that remain after V2 authority admission and V3 payment-state handling.

> The plan may be stale; the commit must not be. Once authority is consumed, recovery continues the same execution lineage rather than resurrecting authority.

## 1. Commit-to-payment durability

The V2 `commit()` transaction now atomically persists:

1. the one-shot delegation/grant/reservation/execution transition;
2. the commit receipt;
3. a deterministic `payment_dispatch_outbox` row; and
4. a physical `inventory_holds` row.

If any of those writes fails, SQLite rolls the entire commit back.

A crash after the DB commit but before the payment worker runs therefore leaves a durable `PENDING` dispatch. A stateless worker can recreate the payment-order intent for the **same execution**. Authority is never moved back to `ACTIVE`.

## 2. Stable external identity

One execution has one deterministic Razorpay receipt:

`receipt = "ac_" + SHA256(execution_id)[:32]`

The receipt is stable, ASCII, <= 40 characters, and never freshly generated per retry.

Remote order creation is durable-before-network:

`PREPARED -> CREATING -> {CREATED | CREATE_UNKNOWN | CREATE_FAILED}`

A network/5xx/post-write ambiguity becomes `CREATE_UNKNOWN`. The worker does **not** blindly POST again. Recovery queries Razorpay Orders by the deterministic receipt and binds the existing exact order when found.

## 3. Physical inventory lifecycle

V2 reservation consumption means the candidate path won authority; it does **not** imply successful payment/fulfilment.

V3.1 tracks a separate physical hold:

`HELD -> FULFILLED | RELEASED`

- Before remote order creation, the hold uses the reservation deadline.
- Immediately before the remote side effect, the hold is extended through the checkout window.
- Definite order-creation rejection releases inventory atomically and terminal-fails the execution.
- Checkout expiry requires remote reconciliation before inventory release.
- A capture after a released hold never re-consumes stock. It moves the execution to `COMPENSATION_REQUIRED`.

## 4. Payment truth is monotonic

Webhook events are authenticated evidence, not unquestioned current truth.

The merge operation over payment states is tested for idempotence, commutativity and associativity. `CAPTURED` dominates weaker observations. A later stale/empty API read cannot downgrade a prior captured payment.

Execution updates use the **merged** payment state, never just the latest raw webhook.

## 5. Boundary rules

- Checkout HMAC verification uses the server-stored Razorpay Order ID.
- Webhook HMAC verification uses the exact raw body.
- Duplicate webhook IDs with identical bytes are idempotent.
- Reusing an event ID with different signed bytes is rejected.
- Amount, currency, order ID and payment ID remain immutable bindings.
- More than one captured payment ID for one local order is a fail-closed conflict.

## 6. Certification boundary

`v3.1-offline-rc` proves deterministic/offline integration semantics, concurrency, corruption handling and performance.

Full `v3.1-certified` additionally requires real Razorpay Test Mode evidence:

- test Order creation/fetch;
- real Standard Checkout;
- server-side Checkout signature verification;
- real signed webhook delivery; and
- API reconciliation.

Offline fakes are never reported as live Razorpay evidence.
