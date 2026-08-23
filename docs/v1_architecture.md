# V1 — Persistent merchant commit kernel

- `BEGIN IMMEDIATE` serializes local writers; conditional updates remain CAS guards.
- Buyer delegation is one-shot and consumed atomically with the winning grant/reservation/execution.
- Multiple candidate grants may exist before commit; winning commit revokes/cancels siblings and releases held inventory in the same transaction.
- Quotes bind price revision. Reservations hold quantity and amount until expiry.
- Commit receipts provide local idempotency. No distributed exactly-once claim is made.
- V1 does not integrate Razorpay or an LLM yet.
