from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from agentcommit.store.sqlite_store import MerchantStore
from agentcommit.domain.models import DelegationGrant
from conftest import NOW

ROOT = Path(__file__).parents[1]
WORKER = ROOT / "tests" / "process_worker.py"


def _run_pair(left: list[str], right: list[str], *, timeout_s: float = 20.0) -> list[dict]:
    procs = [
        subprocess.Popen([sys.executable, str(WORKER), *args], cwd=ROOT, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for args in (left, right)
    ]
    try:
        deadline = time.monotonic() + timeout_s
        out = []
        for p in procs:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                stdout, stderr = p.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                p.kill(); stdout, stderr = p.communicate(timeout=2)
                raise AssertionError(f"race worker timed out; stderr={stderr!r}")
            assert p.returncode == 0, stderr
            lines = [line for line in stdout.splitlines() if line.strip()]
            assert len(lines) == 1, lines
            out.append(json.loads(lines[0]))
        return out
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                try: p.communicate(timeout=2)
                except subprocess.TimeoutExpired: pass


def _setup(db):
    s = MerchantStore(db)
    s.add_product(merchant_id="m", sku="A", category="monitor", currency="INR", price_paise=3_800_000, available_quantity=1)
    s.add_product(merchant_id="m", sku="B", category="monitor", currency="INR", price_paise=3_900_000, available_quantity=1)
    s.create_delegation(DelegationGrant("D", "buyer", "m", "monitor", 4_000_000, "INR", 1, NOW + 100_000))
    s.create_quote(quote_id="QA", merchant_id="m", sku="A")
    s.activate_plan_from_quote(plan_id="P-a", grant_id="G-a", execution_id="E-a", reservation_id="R-a", delegation_id="D", quote_id="QA", now_ms=NOW, ttl_ms=1000)
    s.create_quote(quote_id="QB", merchant_id="m", sku="B")
    return s


def test_process_replan_vs_commit_has_single_authoritative_outcome(db):
    s = _setup(db)
    results = _run_pair(
        ["commit", str(db), "G-a", "old-process", str(NOW + 1)],
        ["replan", str(db), str(NOW + 1)],
    )
    outcomes = {(r["ok"], r["outcome"]) for r in results}
    assert sum(bool(r["ok"]) for r in results) == 1
    assert any(r["ok"] and r["outcome"] in {"G-a", "replan"} for r in results)
    assert s.scalar("SELECT COUNT(*) FROM commit_receipts") in (0, 1)
    assert s.scalar("SELECT COUNT(*) FROM plans WHERE state='ACTIVE'") in (0, 1)


def test_process_tighten_vs_commit_has_single_authoritative_outcome(db):
    s = _setup(db)
    results = _run_pair(
        ["commit", str(db), "G-a", "old-process", str(NOW + 1)],
        ["tighten", str(db), str(NOW + 1)],
    )
    assert sum(bool(r["ok"]) for r in results) == 1
    assert s.scalar("SELECT COUNT(*) FROM commit_receipts") in (0, 1)
