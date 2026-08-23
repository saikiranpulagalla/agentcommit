# AgentCommit V2 — Versioned Delegated Authority and Atomic Replanning

V2 extends the certified V1 persistent commit kernel without changing its central rule:

> The plan may be stale; the commit must not be.

## Scope

V2 adds versioned buyer authority and plan lineage. It does **not** execute a real payment and does not integrate Razorpay yet.
One `DomainSnapshot`/execution grant still represents exactly one financial path.

## Authority modes

### EXACT
The buyer authorizes one exact SKU at one exact amount. Substitution is forbidden.
A price/SKU change requires fresh buyer authority.

### DELEGATED
The buyer authorizes a bounded scope: merchant, category, currency, maximum amount,
maximum quantity and expiry. `substitution_allowed` controls whether replanning may move to another SKU.

Authority broadening is never an in-place amendment. A larger budget, larger quantity, later expiry,
or broader resource scope requires a brand-new delegation ID.

## Two independent monotonic clocks

- `delegation.version` changes when authority itself is narrowed/revoked/expired/consumed.
- `delegation.plan_generation` changes when the agent activates a new candidate plan under unchanged authority.

Every `ExecutionGrant` binds both values. A worker from an older generation cannot commit after a replan.

## Atomic replan boundary

`activate_plan_from_quote()` performs one SQLite `BEGIN IMMEDIATE` transaction:

1. validate the persisted delegation and quote as domain objects;
2. validate current merchant price/inventory and buyer authority;
3. acquire/transfer the new inventory hold;
4. supersede the previous active plan and revoke/cancel its execution path;
5. increment `plan_generation` by CAS;
6. create execution record + execution grant;
7. create the new ACTIVE plan lineage row;
8. audit and commit.

Any exception rolls the entire operation back.

### Cross-SKU replan
The new hold is acquired **before** the old hold is released. This prevents losing the old valid candidate
when the replacement is unavailable.

### Same-SKU/same-quantity replan
The existing physical inventory hold is transferred atomically to the new reservation. The system does not
require a second unit merely to refresh/reprice/replan the same product.

An expired reservation is never renewed. Expiry is materialized first, inventory is released, and replanning
starts from current state.

## Invalidation semantics

- tightening authority invalidates all child execution paths and active reservations in the same transaction;
- revocation does the same;
- expiry does the same at the exact `now >= expires_at` boundary;
- cleanup is based on both grant and reservation state, so a drifted terminal grant cannot leak an ACTIVE inventory hold;
- terminal/counter-exhausted child state causes fail-closed rollback before resource accounting changes.

## One reservation → one execution grant

The storage schema has a unique index on `execution_grants(reservation_id)` and issuance also performs an
explicit conflict precheck. This prevents ambiguous ownership of a merchant hold.

## Replan/commit linearization

Both operations use `BEGIN IMMEDIATE`. Therefore either:

- the old commit linearizes first, consumes the one-shot delegation, and the replan is denied; or
- the replan linearizes first, advances the plan generation/revokes old authority, and the stale commit is denied.

There is no state in which both financial paths win.

## V2 non-goals

- Razorpay Orders/Checkout/webhooks;
- payment reconciliation;
- an LLM planner;
- recovery/alternate payment paths.

Those enter later versions only after this authority/replanning layer remains certified.
