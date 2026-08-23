from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from agentcommit.store.sqlite_store import MerchantStore
from conftest import NOW, seed_path

ROOT = Path(__file__).parents[1]
WORKER = ROOT / "tests" / "process_worker.py"


def _run_workers(arglists: list[list[str]], *, timeout_s: float = 20.0) -> list[dict]:
    """Run independent Python interpreters concurrently with deterministic cleanup."""
    procs: list[subprocess.Popen[str]] = []
    try:
        for args in arglists:
            procs.append(subprocess.Popen(
                [sys.executable, str(WORKER), *args],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ))
        deadline = time.monotonic() + timeout_s
        outputs: list[dict] = []
        for p in procs:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                stdout, stderr = p.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                p.kill()
                stdout, stderr = p.communicate(timeout=2)
                raise AssertionError(f"race worker timed out; stderr={stderr!r}")
            assert p.returncode == 0, stderr
            lines = [line for line in stdout.splitlines() if line.strip()]
            assert len(lines) == 1, f"worker must emit exactly one result, got {lines!r}"
            outputs.append(json.loads(lines[0]))
        return outputs
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                try:
                    p.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


def test_process_same_grant_one_receipt(db):
    s = MerchantStore(db)
    x = seed_path(s)
    results = _run_workers([
        ["commit", str(db), x["grant"], f"p-{i}", str(NOW + 1)] for i in range(8)
    ])
    assert sum(bool(r["ok"]) for r in results) == 8  # idempotent same-grant observation
    assert s.scalar("SELECT COUNT(*) FROM commit_receipts") == 1


def test_process_last_inventory_one_reservation(db):
    s = MerchantStore(db)
    s.add_product(
        merchant_id="pm", sku="ps", category="monitor", currency="INR",
        price_paise=100, available_quantity=1,
    )
    for i in range(8):
        s.create_quote(quote_id=f"pq-{i}", merchant_id="pm", sku="ps")
    results = _run_workers([
        ["reserve", str(db), f"pr-{i}", f"pq-{i}", str(NOW), "1000"] for i in range(8)
    ])
    assert sum(bool(r["ok"]) for r in results) == 1
    assert s.scalar("SELECT COUNT(*) FROM reservations") == 1
    assert s.scalar("SELECT available_quantity FROM products WHERE merchant_id='pm' AND sku='ps'") == 0
