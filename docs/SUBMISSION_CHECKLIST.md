# Buildathon Submission Checklist

## Repo

- [ ] Public GitHub repository opens directly to the current AgentCommit README.
- [ ] No private API keys, `.env`, DB files, caches or local paths committed.
- [ ] `python -m pip install -e '.[demo]'` succeeds in a fresh environment.
- [ ] `PYTHONPATH=src python -m pytest -q` passes.
- [ ] Demo starts with the documented Uvicorn command.
- [ ] README evidence matches the committed JSON evidence files.
- [ ] Offline/live distinctions remain visible.

## Demo

- [ ] Browser badge says **OFFLINE DEMO — NOT REAL MONEY** for scripted/fake mode.
- [ ] Stale Product → Replan works from a fresh start.
- [ ] Crash / Unknown Order Recovery shows one remote create call.
- [ ] Late Capture → Compensation ends in `COMPENSATION_REQUIRED`.
- [ ] Timeline text fits on screen at recording zoom.
- [ ] No terminal/debug window containing secrets appears in the recording.

## Real external gates (when available)

- [ ] Real LLM runner executed against the unchanged 60-case dataset.
- [ ] Dataset hash is still `466c97b0c1eaf62e0ed95862f995224406397a0f703f9f01f9f361c1f8e00c64`.
- [ ] Provider/model/date/tokens recorded.
- [ ] Real Razorpay **Test Mode** Order created.
- [ ] Standard Checkout completed.
- [ ] Checkout signature verified server-side.
- [ ] Signed raw webhook received and verified.
- [ ] API reconciliation confirms final state.
- [ ] No Live Mode keys/payments used.

## 5-minute pitch

- [ ] Total video is ≤5:00.
- [ ] First 25 seconds state the stale-plan problem.
- [ ] Architecture explanation ≤30 seconds.
- [ ] Main demo is Stale Product → Replan.
- [ ] Show one payment ambiguity scenario.
- [ ] Explain the original TOCTOU bug and fix.
- [ ] Show only 4–6 useful metrics.
- [ ] Explicitly distinguish offline vs real external evidence.
- [ ] End with: **Agents may plan optimistically. Money commits only against current authority and current reality.**

## Application text

- [ ] Public GitHub URL correct.
- [ ] 5-minute video URL publicly accessible.
- [ ] Problem statement matches README language.
- [ ] “What broke” uses the real TOCTOU story, not a fabricated failure.
- [ ] Architecture answer mentions separate buyer/merchant/payment/AI authority.
- [ ] Do not claim real LLM/Test Mode metrics unless they have actually run.

## Final day

- [ ] Submit by **September 4** if possible; keep September 5 as emergency buffer.
- [ ] Re-open every public link in an incognito window.
- [ ] Verify GitHub default branch is the intended submission commit.
- [ ] Verify the video has audio and readable screen text.
- [ ] Save the final submission response/confirmation.
