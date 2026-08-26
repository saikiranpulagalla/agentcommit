# Post-publication hardening candidate

## Status and evidence boundary

This document describes a **successor candidate** to the published V5 Razorpay Buildathon submission. It is not a replacement for, or an edit to, the historical V5 certification artifacts.

The public V5 baseline is commit `bb83e503c019303a4139fc6e1e3e0f31d3ee7cce` (`Publish AgentCommit Razorpay Buildathon submission`). Its release JSON and certification JSON files remain unchanged so that their claims retain their original scope.

In particular, this candidate does **not** claim that real-provider LLM evaluation, a complete Razorpay Standard Checkout flow, signed webhook delivery, or end-to-end Test Mode reconciliation has passed. Those historical integration claims remain `NOT_RUN` unless separately evidenced.

## Why this candidate exists

An adversarial local audit identified boundary cases worth hardening before any future release:

- common explicit price caps could be omitted by the deterministic reference compiler;
- an ambiguous remote Order creation that produced no discoverable Order could retain consumed authority and inventory forever;
- reconciliation could treat a remotely observed paid Order plus a temporarily empty payment-list read as a terminal failure and release inventory;
- the live-evaluation runner could describe a completed run as accurate even when every intent compilation failed.

These are changes to the executable candidate, not retroactive claims about the V5 RC.

## Candidate controls

- The deterministic critical-value parser recognizes common rupee-cap wording, applies the strictest of multiple stated caps, and avoids interpreting non-monetary phrases such as "at most 2 days" as price authority.
- An expired, unresolved Order create transitions to `CREATE_REQUIRES_MANUAL_REVIEW`. It keeps inventory and consumed authority intact; a late matching remote Order can still bind safely. The system does not infer that an empty lookup proves a remote side effect did not happen.
- Reconciliation treats a remotely observed `paid` Order as strong captured evidence. Missing or weaker payment-detail reads cannot manufacture a failure that releases the hold.
- The live evaluator now separates execution completion from quality promotion and fails its promotion gate when compilation failures or constraint-accuracy thresholds are not met.

## Candidate revalidation

Run from the repository root with dependencies already installed:

```powershell
$env:PYTHONPATH = "src"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python scripts/submission_check.py
python -m pytest -q
python -m compileall -q src evals
git diff --check
```

For the offline UI smoke, launch the FastAPI demo and verify the **Stale Product -> Replan** scenario still ends in `CAPTURED` / `SUCCEEDED` / `FULFILLED`. Also enter a request with a lower explicit cap than the demonstration product: it must not capture above that cap.

## Deliberately unresolved work

- Manual review is intentional for an ambiguous no-create result without a matching Order; absence cannot safely be inferred from a transient empty lookup.
- A complete Razorpay Test Mode Checkout, signature, raw-body webhook, duplicate-event, ordering, and reconciliation demonstration still requires separate credentials and real external evidence.
- Real-provider LLM evaluation remains separate from the offline deterministic demo and needs its own measured evidence.
- Historical archive/provenance references from the reconstructed release remain historical; this document does not imply that absent source archives are newly verifiable.