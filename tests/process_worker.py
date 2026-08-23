"""Small import-safe subprocess worker for real OS-process race tests.

No multiprocessing primitives are used.  Each invocation performs exactly one
store operation, emits exactly one JSON result to stdout, then exits.
"""
from __future__ import annotations

import json
import sys

from agentcommit.store.sqlite_store import MerchantStore


def emit(ok: bool, outcome: str) -> int:
    print(json.dumps({"ok": ok, "outcome": outcome}), flush=True)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit("usage: process_worker.py MODE DB [ARGS...]")
    mode, db, *args = argv[1:]
    store = MerchantStore(db)
    try:
        if mode == "commit":
            grant_id, request_id, now_ms = args
            receipt = store.commit(request_id=request_id, grant_id=grant_id, now_ms=int(now_ms))
            return emit(True, receipt.grant_id)
        if mode == "reserve":
            reservation_id, quote_id, now_ms, ttl_ms = args
            store.reserve(
                reservation_id=reservation_id,
                quote_id=quote_id,
                now_ms=int(now_ms),
                ttl_ms=int(ttl_ms),
            )
            return emit(True, "reserved")
        if mode == "replan":
            now_ms, = args
            store.activate_plan_from_quote(
                plan_id="P-b",
                grant_id="G-b",
                execution_id="E-b",
                reservation_id="R-b",
                delegation_id="D",
                quote_id="QB",
                now_ms=int(now_ms),
                ttl_ms=1000,
            )
            return emit(True, "replan")
        if mode == "tighten":
            now_ms, = args
            store.tighten_delegation("D", now_ms=int(now_ms), max_amount_paise=3_850_000)
            return emit(True, "tighten")
        raise ValueError(f"unknown worker mode: {mode}")
    except Exception as exc:  # expected race losers are data, not process failures
        return emit(False, type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
