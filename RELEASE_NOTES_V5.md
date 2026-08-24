# AgentCommit V5 Demo RC

V5 does **not** change the certified financial safety kernel. It adds a thin FastAPI Buildathon demonstration layer over the V4.2 provider-ready RC.

## Demo scenarios

1. Happy Path — valid plan → state-aware commit → captured payment → fulfilled inventory hold.
2. Stale Product → Replan — authoritative product facts change after planning; stale commit is denied and a fresh substitute plan is bound.
3. Crash / Unknown Order Recovery — durable dispatch outbox + deterministic receipt recover an ambiguous remote Order write without a second POST.
4. Late Capture → Compensation — inventory is released only after reconciliation; a later capture becomes `COMPENSATION_REQUIRED` rather than corrupting inventory state.

## Evidence honesty

The page is visibly labelled **OFFLINE DEMO — NOT REAL MONEY**. It uses the deterministic reference compiler and a fake Razorpay-shaped gateway. Real-model accuracy and real Razorpay Test Mode remain separate gates inherited from V4.2.

## Run

```bash
python -m pip install -e '.[demo]'
PYTHONPATH=src uvicorn agentcommit.demo.app:app --host 127.0.0.1 --port 8000
```
