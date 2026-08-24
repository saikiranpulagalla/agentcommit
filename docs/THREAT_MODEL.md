# Threat Model

## Assets to protect

- buyer financial authority;
- merchant inventory/resources;
- exact product/amount/currency the buyer authorized;
- payment/order identity;
- captured-payment truth;
- audit/evaluation integrity;
- API/webhook secrets.

## Trust boundaries

### Untrusted / probabilistic

- natural-language model output;
- merchant/catalog descriptive text;
- model explanations/reasoning;
- client/browser state;
- webhook delivery order/timing;
- network timeout outcome.

### Authoritative only within scope

- buyer delegation store → buyer authority;
- merchant structured facts/reservation → commerce truth;
- Razorpay API/payment entities → remote financial truth;
- server-side signatures/secrets → authenticity checks.

## Threats and mitigations

| Threat | Example | Mitigation |
|---|---|---|
| Stale plan / TOCTOU | price/facts change while worker waits | version/hash binding + execution-boundary admission |
| Confused deputy | Merchant A authority reused at B | explicit principal/merchant/resource binding |
| Authority amplification | two candidate grants spend one delegation | one-shot delegation + atomic sibling cleanup |
| Prompt injection | catalog says “ignore budget” | untrusted descriptions + deterministic structured-fact verification |
| Product substitution | approval for A reused on B | plan/generation/SKU/reservation/amount binding |
| Duplicate side effect | two workers commit same grant | transactional/CAS one-shot consumption |
| Ambiguous remote write | POST times out after success | durable intent + stable receipt + reconcile, no blind retry |
| Duplicate webhook | same event delivered repeatedly | unique event ID + payload-hash collision/tamper detection |
| Out-of-order payment events | failed before captured / captured first | monotonic merge + API reconciliation |
| Stale API response | empty/created after known capture | captured truth cannot downgrade |
| Inventory/payment divergence | stock released then late capture | compensation-required instead of silent fulfilment |
| Crash after DB commit | authority consumed, worker dies | durable payment-dispatch outbox in same transaction |
| Malformed money/types | `-1`, `0`, `True` | strict positive bounded integer domain validation |
| Persistence corruption | JSON changes without version bump | canonical hashes + fail-closed DTO/invariant validation |
| Secret leakage | key printed in eval artifact | keys stay in environment/header only; secret scans |

## Security non-claims

AgentCommit does **not** claim:

- to make LLM reasoning prompt-injection-proof;
- universal distributed exactly-once semantics;
- cryptographic proof of transaction correctness;
- that webhooks are always current truth;
- that offline fake-gateway tests are real Razorpay evidence.

The security objective is narrower and testable:

> Even if reasoning is stale, wrong or partially compromised, it must not be able to exceed current explicit authority or bypass current structured financial/commerce state.
