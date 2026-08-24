# AgentCommit — 5-Minute Pitch Script

## 0:00–0:25

AI agents increasingly make purchase decisions, but they reason against a snapshot. Price, inventory, buyer authority and payment state can change while an agent is still executing. A plan can be valid when the model decides and invalid when money is about to move.

**AgentCommit is a state-aware commit boundary for AI commerce. The plan may be stale; the commit must not be.**

## 0:25–0:55

The model is useful but never authoritative. It interprets the buyer's request, ranks products and replans. Buyer delegation owns permission. The merchant owns product, price and inventory truth. Razorpay owns payment truth. Right before a financial side effect, AgentCommit revalidates those worlds together.

## 0:55–2:05 — Demo

The buyer asks for a 27-inch 4K USB-C monitor under ₹40,000 and allows substitution.

The agent selects Monitor A and the execution grant is bound to the current intent, product facts, reservation and authority.

Now I change the merchant's structured state after planning: Monitor A no longer satisfies USB-C.

The old worker reaches the commit boundary and is denied. The LLM doesn't get to argue with the gate—the current merchant facts no longer satisfy the frozen hard intent.

Because substitution is allowed and replan budget remains, the agent selects Monitor B, receives fresh state-bound authority, and the new transaction commits.

## 2:05–2:50 — Remote ambiguity

Next, the payment worker crashes around remote Order creation. A timeout does not tell us whether the side effect happened, so blindly retrying can duplicate external work.

AgentCommit persisted the payment intent before the network call and uses one deterministic receipt for this logical execution. The outcome becomes unknown, then the reconciler finds the already-created Order by receipt. Notice the remote create count stays one.

## 2:50–3:20 — Late capture

Payment state can also arrive late or out of order. If inventory was safely released after a reconciled failure and a late capture is later discovered, AgentCommit does not silently re-consume stock or claim fulfilment. It moves to compensation-required because money moved after fulfilment authority was gone.

## 3:20–4:00 — What broke

My first implementation validated merchant state before queueing the payment worker. A concurrency test reproduced a classic time-of-check/time-of-use bug: merchant state changed while the worker waited, and the stale plan still executed.

I fixed it by binding the execution grant to the expected state and moving validation/claiming to the exact side-effect boundary. That failing interleaving became a permanent regression test.

The same adversarial process later found payment-uncertainty, delegated-authority amplification, inventory-leak and stale-reconciliation bugs. The tests changed the architecture rather than just measuring it.

## 4:00–4:35 — Evidence

The V5 failure lab has run 40 out of 40 repeated scenarios without a failure. The V4.2 AI/provider boundary passed 396 tests across five clean stability runs, with roughly 97.5 percent line and 92 percent branch coverage. The natural-language evaluator uses a frozen 60-case held-out set.

This bundled demo is intentionally offline. Real-model accuracy and real Razorpay Test Mode are separate gates and are not replaced with scripted numbers.

## 4:35–5:00 — Why Razorpay

Razorpay already has the payment primitives, Agent Studio guardrails and Vulcan payment intelligence. I am not trying to rebuild those systems. AgentCommit focuses on the layer above them: whether an AI-originated commerce action is still authorized and valid when it reaches the money-moving infrastructure.

**Agents may plan optimistically. Money commits only against current authority and current reality.**
