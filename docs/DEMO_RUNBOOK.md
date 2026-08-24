# 5-Minute Demo Runbook

This runbook is optimized for the Razorpay Buildathon's five-minute pitch format. Do not try to show every test or every version.

## Before recording

```bash
python -m pip install -e '.[demo]'
PYTHONPATH=src uvicorn agentcommit.demo.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and confirm the visible **OFFLINE DEMO — NOT REAL MONEY** badge.

Use the default buyer request:

> Buy me the cheapest 27-inch 4K USB-C monitor under ₹40,000. You can choose another model if the first becomes unavailable.

## 0:00–0:25 — Problem

Say:

> AI agents plan against a snapshot. Commerce changes underneath them: price, inventory, authority, even payment state. A purchase can be valid when the model decides and invalid when the side effect executes. AgentCommit is the state-aware commit boundary between those two moments.

Put the thesis on screen:

> **The plan may be stale. The commit must not be.**

## 0:25–0:55 — Architecture

Use the short explanation:

> The model can interpret, rank and replan. It owns no authority. Buyer delegation, merchant facts and Razorpay payment state remain separate sources of truth. Right before a financial side effect, AgentCommit checks the current versions and deterministic hard constraints.

Do not spend more than 30 seconds on the architecture.

## 0:55–2:05 — Main demo: Stale Product → Replan

Select **Stale Product → Replan**.

Narrate the timeline:

1. The request becomes a structured intent: 27-inch, 4K, USB-C, ≤₹40k; substitution is allowed.
2. The agent selects a valid product and authority is bound to its current product facts.
3. The failure lab changes authoritative merchant state after planning (`usb_c=false`).
4. The old worker reaches the commit boundary.
5. **DENY:** the plan no longer matches current structured truth.
6. The agent replans to the next valid product within the same allowed intent.
7. A fresh execution grant is created and the new path commits.

Say:

> This is the important point: we don't ask the LLM whether its old plan is still good. Current merchant facts decide.

## 2:05–2:50 — Payment ambiguity demo

Select **Crash / Unknown Order Recovery**.

Say:

> A financial POST can time out after the remote system processed it. Retrying blindly may create a second remote order. AgentCommit persists the intent before the network call and derives one stable receipt from the execution. On ambiguity, it searches/reconciles by that identity instead of issuing another POST.

Point out `remote_create_calls = 1` in the final state.

## 2:50–3:25 — Late financial truth

Select **Late Capture → Compensation**.

Say:

> Payment events can arrive late or out of order. If inventory was released after a reconciled failure and capture is later discovered, we don't silently re-consume stock or report success. We move to compensation-required because money moved but fulfilment authority is gone.

## 3:25–4:05 — What broke

Say:

> My first design checked state before queueing a worker. A concurrency test reproduced a TOCTOU bug: the state changed while the worker waited, and a stale action still executed. I moved validation to the exact execution boundary, bound the grant to expected state versions/hashes, and made the claim transactional. That failing interleaving is now a regression test.

Then add one sentence:

> Later adversarial testing also found payment uncertainty, authority-amplification, inventory-leak and stale-reconciliation bugs; the architecture changed when the tests disproved our assumptions.

## 4:05–4:35 — Evidence

Show only the strongest numbers:

- V5 demo: **40/40 repeated scenario runs, zero failures**.
- V4.2 AI/provider boundary: **396/396 tests, 5/5 stability, 97.54% line / 92.01% branch**.
- The frozen held-out intent dataset has **60 cases**.
- Real LLM and Razorpay Test Mode remain explicitly labeled `NOT_RUN` in the bundled RC—never substituted with fake evidence.

Do not read a wall of metrics.

## 4:35–5:00 — Why Razorpay / close

Say:

> Razorpay already has strong payment primitives, Agent Studio guardrails, and now Vulcan for payment intelligence. I am not rebuilding those. AgentCommit addresses the layer above them: whether an AI-originated commerce action is still authorized and valid at the moment it reaches the money-moving infrastructure.

Finish:

> **Agents may plan optimistically. Money commits only against current authority and current reality.**

## Recording rules

- Keep the browser zoom around 100–110% so the timeline is readable.
- Do not scroll through source code in the main five minutes.
- Do not show more than three failure scenarios in the video; Happy Path can be a quick intro or omitted.
- Never describe the fake gateway as Razorpay Test Mode.
- Never describe the reference compiler as real LLM accuracy.
- Keep `V42_LIVE_EVAL_STATUS.json` available if asked how evidence honesty is enforced.
