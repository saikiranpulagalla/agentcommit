# AgentCommit — Final 5-Minute Video Rehearsal

## Before recording

- Browser zoom 100–110%; no tiny terminal text.
- Close all unrelated tabs/windows and notifications.
- Start the app before recording.
- Confirm the page visibly says **OFFLINE DEMO — NOT REAL MONEY**.
- Keep README architecture diagram open in a second tab.
- Keep a terminal ready only for one short test/evidence shot.
- Do not scroll through hundreds of tests.

## 0:00–0:20 — Hook

**Say:**

> An AI buyer can make a correct decision and still execute the wrong transaction seconds later. Price, inventory, authorization or payment state can change while the agent is working. AgentCommit is the boundary that asks one question immediately before money moves: is this plan still authorized and still true?

**Show:** Project title + tagline.

## 0:20–0:45 — Architecture

**Say:**

> The model interprets intent, ranks products and replans. It owns no financial truth. Buyer delegation owns permission, the merchant owns product and inventory truth, and Razorpay owns payment truth. The execution grant binds those states together and is revalidated at commit.

**Show:** architecture diagram. Point to AI → AgentCommit → Razorpay.

## 0:45–2:10 — Main demo: stale product → replan

**Show buyer prompt:**

> Buy me the cheapest 27-inch 4K USB-C monitor under ₹40,000. You can choose another model if the first becomes unavailable.

**Narrate:**

1. The agent selects Monitor A.
2. Current product facts and authority are bound to the execution.
3. Trigger the stale-product failure.
4. Show the deny event.

**Say when denied:**

> The important part is that the LLM does not get to argue with this decision. Current structured merchant facts no longer satisfy the frozen hard intent, so this execution has lost authority.

5. Show bounded replan to Monitor B.
6. Show success state.

## 2:10–3:00 — Remote ambiguity

Run **Crash / Unknown Order Recovery**.

**Say:**

> A timeout after a POST does not prove the remote side effect did not happen. Blind retry can duplicate external work. AgentCommit persists payment intent before the network call and derives one stable receipt from the logical execution. An ambiguous outcome becomes unknown; recovery looks up the existing Order by receipt rather than blindly POSTing again.

**Point out:** create count remains one.

## 3:00–3:35 — Late capture

Run **Late Capture → Compensation**.

**Say:**

> Payment events are evidence, not always current truth. If inventory was legitimately released after reconciliation and a later capture is discovered, AgentCommit does not re-consume inventory or claim fulfilment. It moves to compensation-required because money moved after fulfilment authority was gone.

## 3:35–4:15 — What broke

**Say:**

> The first implementation checked merchant state before queueing the worker. My concurrency test reproduced a TOCTOU race: state changed while the worker waited, but the stale plan still executed. I moved version-bound validation and claiming to the exact side-effect boundary. That failing interleaving stayed as a regression test. The same process later found payment-uncertainty, delegated-authority, inventory-leak and stale-reconciliation bugs, and each one changed the architecture.

## 4:15–4:40 — Evidence

**Show:** compact eval table only.

**Say:**

> The offline failure lab completed 40 out of 40 repeated scenario runs. The provider-ready AI boundary passed 396 tests across five clean stability runs with about 97.5% line and 92% branch coverage. The natural-language evaluator is frozen at 60 held-out cases. I keep real-model accuracy and real Razorpay Test Mode as separate not-run gates rather than replacing them with scripted numbers.

## 4:40–5:00 — Razorpay fit / close

**Say:**

> Razorpay already has payment rails, Agent Studio and payment intelligence. I am not rebuilding those. AgentCommit focuses on the missing execution question above them: when an AI-originated action reaches money-moving infrastructure, is the authority and the world it was based on still current? The plan may be stale. The commit must not be.

## If a demo action is slow

Do **not** fill silence by rambling about implementation.

Say:

> While that runs, the invariant is that no worker can bypass the current-state check just because an earlier plan was valid.

Then continue when the event appears.

## If the UI fails during recording

Keep one pre-opened terminal fallback:

```bash
make submission-check
```

Then show the architecture/timeline screenshots and continue the pitch. Re-record afterward if possible; the fallback is for rehearsal robustness, not the preferred submission.
