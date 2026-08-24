# Panel Q&A

Short answers first; expand only when the panel asks.

## “How is this different from Razorpay Agent Studio guardrails?”

Agent Studio publicly emphasizes merchant-defined permissions, verified first-party data and platform validation. AgentCommit focuses on a complementary race: **an action was authorized and valid when planned, but external state changed before execution**. The core primitive is execution-boundary, version-bound admission plus reconciliation—not a generic allow/deny policy layer.

## “How is this different from Vulcan?”

Vulcan improves payment intelligence such as payment reliability/risk/checkout decisions. AgentCommit is upstream: it decides whether an AI-originated commerce action is still authorized and semantically valid before it should reach the payment layer. It uses Razorpay primitives rather than rebuilding them.

## “Why does this need AI?”

Natural-language intent contains ambiguity, negation, soft preferences, substitution rules and semantic product requirements that a rule parser handles poorly. The AI interprets and ranks. The deterministic layer then independently validates hard structured constraints and authority. The AI provides usefulness; the commit layer provides safety.

## “Couldn't you just check everything once before checkout?”

That creates a TOCTOU window. State can change after the check while a worker waits. The grant must be bound to expected state and validated/claimed at the side-effect boundary.

## “Why not trust the webhook?”

Webhooks are asynchronous evidence and may be duplicated or arrive out of order. AgentCommit deduplicates them and merges state monotonically, then uses API reconciliation for critical current truth.

## “What if the Order POST times out?”

A timeout does not prove the remote side effect did not happen. AgentCommit persists `CREATING` before the network call, derives one stable receipt from the execution, moves to `CREATE_UNKNOWN` on ambiguity and reconciles by receipt. It does not blindly POST again.

## “What if two workers race?”

SQLite `BEGIN IMMEDIATE` plus conditional one-shot state transitions serialize the winning financial mutation in this prototype. Thread and independent-process race tests verify one winner for the same grant, last inventory unit and competing grants under one delegation.

## “What if your own service crashes after consuming authority?”

Authority consumption and a `PENDING` payment-dispatch outbox row are in the same transaction. After restart, a stateless worker resumes the same execution. We never reactivate already-spent authority.

## “What if payment says failed and later succeeds?”

Observed failure is not always terminal. New financial recovery stays frozen until reconciliation. If capture arrives later, captured truth wins. If inventory was already legitimately released, the system enters `COMPENSATION_REQUIRED` rather than pretending fulfilment succeeded.

## “How do you stop prompt injection?”

I do not claim the model itself becomes prompt-injection-proof. Catalog text is untrusted; the model may rank badly. But it cannot mint authority, and hard product requirements are rechecked against structured merchant facts. Prompt injection may hurt choice quality; it must not bypass financial/intent bounds.

## “Why SQLite? This isn't production scale.”

SQLite is intentional for the Buildathon prototype: it gives real transactional semantics and reproducible process-level races without spending the project on infrastructure. The invariants map cleanly to a production relational store. The project evaluates the control protocol, not database throughput.

## “Why not Kafka/microservices/blockchain?”

They do not solve the core invariant. A durable transactional outbox and explicit reconciliation are enough to demonstrate the hard correctness problem. More infrastructure would increase failure surface without adding Buildathon signal.

## “Exactly once?”

I do not claim magical distributed exactly-once. The design aims for durable intent, idempotent/one-shot business semantics where possible, duplicate-safe inbound events and reconciliation after ambiguous external outcomes.

## “What does the user approve?”

Current delegated mode binds the buyer authority to merchant/resource/amount/expiry/substitution scope and each execution grant to the current plan/resource state. A production Approval Mode would additionally persist a one-shot approval record bound to the exact execution scope/hash rather than treating a naked SHA digest as authority.

## “What if the AI keeps replanning forever?”

Replanning consumes a persisted bounded budget/generation. Concurrent attempts race for the same remaining budget and only one wins. Exhaustion falls back to clarification/human review rather than looping indefinitely.

## “What's your strongest piece of evidence?”

The strongest story is not a coverage number. It is that adversarial tests repeatedly changed the architecture: TOCTOU, payment uncertainty, one-shot delegation, crash dispatch, inventory leakage and stale captured-payment reconciliation were all bugs found and permanently regressed. The system is evidence-driven rather than designed only for the happy path.

## “What remains unfinished?”

Two external gates are deliberately separate in this bundled RC: real-model scoring on the frozen 60-case holdout and a real Razorpay Test Mode Standard Checkout + signed-webhook run. The repo labels both `NOT_RUN` rather than substituting fake evidence.
