from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
RESULTS = ROOT / "evals" / "results" / "v4.0"
LINE_GATE = 95.0
BRANCH_GATE = 90.0
STABILITY_RUNS = 5
DIFF_CASES = 100_000
LATENCY_P95_MS = 50.0


def run(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=check,
    )


def git_clean() -> bool:
    return run("git", "status", "--porcelain").stdout.strip() == ""


def git_sha() -> str:
    return run("git", "rev-parse", "HEAD").stdout.strip()


def tracked_hash() -> str:
    files = run("git", "ls-files", "-z").stdout.split("\0")
    h = hashlib.sha256()
    for rel in sorted(x for x in files if x):
        data = (ROOT / rel).read_bytes()
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def provenance() -> dict:
    if not git_clean():
        raise RuntimeError("tracked working tree is dirty")
    return {
        "git_sha": git_sha(), "tracked_hash": tracked_hash(), "clean": True,
        "python": sys.version.split()[0], "platform": platform.platform(),
    }


def collect_test_count(*paths: str) -> int:
    out = run(sys.executable, "-m", "pytest", "--collect-only", "-q", *paths).stdout
    return sum(int(m.group(1)) for line in out.splitlines() if (m := re.search(r":\s*(\d+)\s*$", line)))


def coverage_stage() -> dict:
    count = collect_test_count()
    run(sys.executable, "-m", "coverage", "erase")
    run(sys.executable, "-m", "coverage", "run", "--branch", "-m", "pytest", "-q", capture=False)
    run(sys.executable, "-m", "coverage", "json", "-o", "coverage.json")
    data = json.loads((ROOT / "coverage.json").read_text())
    files = [v for k, v in data["files"].items() if k.startswith("src/")]
    stmts = sum(v["summary"]["num_statements"] for v in files)
    miss = sum(v["summary"]["missing_lines"] for v in files)
    branches = sum(v["summary"]["num_branches"] for v in files)
    miss_br = sum(v["summary"]["missing_branches"] for v in files)
    return {
        "tests_passed": count,
        "line_pct": 100.0 * (stmts - miss) / stmts,
        "branch_pct": 100.0 * (branches - miss_br) / branches,
        "statements": stmts, "branches": branches,
    }


def stability_once() -> dict:
    count = collect_test_count()
    p = run(sys.executable, "-m", "pytest", "-q", check=False, capture=False)
    return {"passed": p.returncode == 0, "returncode": p.returncode, "test_count": count}


def differential_stage() -> dict:
    sys.path.insert(0, str(SRC))
    from dataclasses import replace
    from agentcommit.domain.models import (
        AuthorizationMode, DelegationGrant, DelegationState, DomainSnapshot, ExecutionGrant,
        ExecutionRecord, ExecutionState, GrantState, MerchantQuote, MerchantReservation,
        PaymentProjection, PaymentState, ReservationState,
    )
    from agentcommit.domain.policy import evaluate_commit
    from agentcommit.domain.spec import spec_allows_commit

    now = 1_800_000_000_000
    base = DomainSnapshot(
        DelegationGrant("d", "b", "m", "monitor", 4_000_000, "INR", 1, now + 10_000),
        MerchantQuote("q", "m", "monitor", "sku", 3_899_000, "INR", 1, 1, 1),
        MerchantReservation("r", "q", "m", "monitor", "sku", 3_899_000, "INR", 1, 1, 1, now + 5_000),
        ExecutionGrant("g", "d", 1, "b", "r", "q", "m", "monitor", "sku", 3_899_000, "INR", 1, 1, 1),
        ExecutionRecord("e", "b"), PaymentProjection(), 0,
    )
    rng = random.Random(0xA64000)
    mismatches = allowed = 0
    for _ in range(DIFF_CASES):
        s = base
        choice = rng.randrange(16)
        if choice == 0: s = replace(s, delegation=replace(s.delegation, status=rng.choice(list(DelegationState))))
        elif choice == 1: s = replace(s, grant=replace(s.grant, status=rng.choice(list(GrantState))))
        elif choice == 2: s = replace(s, reservation=replace(s.reservation, status=rng.choice(list(ReservationState))))
        elif choice == 3: s = replace(s, execution=replace(s.execution, state=rng.choice(list(ExecutionState))))
        elif choice == 4:
            ps = rng.choice(list(PaymentState)); s = replace(s, payment=PaymentProjection(None if ps is PaymentState.UNKNOWN else "p", ps))
        elif choice == 5: s = replace(s, grant=replace(s.grant, expected_buyer_id=rng.choice(["b", "other"])))
        elif choice == 6: s = replace(s, grant=replace(s.grant, expected_delegation_version=rng.choice([1, 2])))
        elif choice == 7:
            gen = rng.choice([0, 1, 2]); s = replace(s, delegation=replace(s.delegation, plan_generation=gen), grant=replace(s.grant, expected_plan_generation=rng.choice([0, 1, 2])))
        elif choice == 8: s = replace(s, quote=replace(s.quote, sku=rng.choice(["sku", "other"])))
        elif choice == 9: s = replace(s, quote=replace(s.quote, amount_paise=rng.choice([3_899_000, 4_500_000])))
        elif choice == 10:
            if rng.choice([True, False]):
                s = replace(s, delegation=replace(s.delegation, mode=AuthorizationMode.EXACT, exact_sku="sku", exact_amount_paise=3_899_000, substitution_allowed=False))
        elif choice == 11: s = replace(s, grant=replace(s.grant, expected_reservation_revision=rng.choice([1, 2])))
        elif choice == 12: s = replace(s, commit_count=rng.choice([0, 1]))
        elif choice == 13: s = replace(s, delegation=replace(s.delegation, expires_at_ms=rng.choice([now, now + 10_000])))
        elif choice == 14: s = replace(s, reservation=replace(s.reservation, expires_at_ms=rng.choice([now, now + 5_000])))
        else: s = replace(s, grant=replace(s.grant, expected_plan_generation=rng.choice([0, 1])))
        prod = evaluate_commit(s, now_ms=now).allowed
        spec = spec_allows_commit(s, now_ms=now)
        allowed += int(prod); mismatches += int(prod != spec)
    return {"cases": DIFF_CASES, "mismatches": mismatches, "allowed_cases": allowed}


def race_stage() -> dict:
    paths = (
        "tests/test_process_races.py", "tests/test_v2_process_races.py", "tests/test_v2_hardening.py",
        "tests/test_v31_payments.py", "tests/test_v31_hardening.py", "tests/test_v31_cas_corruption.py",
        "tests/test_v4_persistence.py", "tests/test_v4_defensive.py", "tests/test_v4_process_races.py",
    )
    count = collect_test_count(*paths)
    p = run(sys.executable, "-m", "pytest", "-q", *paths, check=False, capture=False)
    return {"tests_passed": count if p.returncode == 0 else 0, "returncode": p.returncode, "test_count": count}


def performance_stage() -> dict:
    sys.path.insert(0, str(SRC))
    from agentcommit.ai.intent import ConstraintOp, HardConstraint, IntentSpec
    from agentcommit.domain.models import DelegationGrant
    from agentcommit.payments.models import RemoteOrder
    from agentcommit.payments.service import PaymentService
    from agentcommit.payments.store import PaymentStore
    from agentcommit.store.sqlite_store import MerchantStore

    class Gateway:
        def __init__(self): self.n = 0
        def create_order(self, *, amount_paise, currency, receipt):
            self.n += 1; return RemoteOrder(f"ord-{self.n}", receipt, amount_paise, currency, "created")
        def orders_by_receipt(self, *, receipt): return []
        def fetch_order(self, *, order_id): raise AssertionError
        def payments_for_order(self, *, order_id): return []

    now = 1_800_000_000_000
    commit_ms: list[float] = []; dispatch_ms: list[float] = []
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "bench.db"; store = MerchantStore(db); gw = Gateway(); svc = PaymentService(PaymentStore(db), gw)
        for i in range(60):
            m=f"m{i}"; sku=f"s{i}"; q=f"q{i}"; d=f"d{i}"; plan=f"p{i}"; g=f"g{i}"; e=f"e{i}"; r=f"r{i}"; iid=f"i{i}"
            store.add_product(merchant_id=m, sku=sku, category="monitor", currency="INR", price_paise=100, available_quantity=1)
            store.put_product_facts(merchant_id=m, sku=sku, attributes={"usb_c": True, "resolution": "4K"})
            store.create_delegation(DelegationGrant(d, f"b{i}", m, "monitor", 100, "INR", 1, now + 120_000))
            intent=IntentSpec(iid,f"b{i}","4K USB-C monitor",(HardConstraint("usb_c",ConstraintOp.EQ,True),HardConstraint("resolution",ConstraintOp.EQ,"4K")))
            store.create_intent(intent); store.attach_intent_to_delegation(delegation_id=d,intent_id=iid,max_replans=2,now_ms=now)
            store.create_quote(quote_id=q, merchant_id=m, sku=sku)
            store.activate_plan_from_quote(plan_id=plan,grant_id=g,execution_id=e,reservation_id=r,delegation_id=d,quote_id=q,now_ms=now,ttl_ms=60_000)
            t=time.perf_counter(); store.commit(request_id=f"req{i}", grant_id=g, now_ms=now+1); commit_ms.append((time.perf_counter()-t)*1000)
            t=time.perf_counter(); svc.dispatch_pending(now_ms=now+2, limit=1); dispatch_ms.append((time.perf_counter()-t)*1000)
    def p95(xs): return sorted(xs)[max(0, int(0.95*len(xs))-1)]
    return {
        "samples": len(commit_ms), "v4_commit_p50_ms": statistics.median(commit_ms), "v4_commit_p95_ms": p95(commit_ms),
        "dispatch_p50_ms": statistics.median(dispatch_ms), "dispatch_p95_ms": p95(dispatch_ms),
    }


def security_stage() -> dict:
    forbidden: list[str] = []; secrets: list[str] = []
    secret_re = re.compile(r"(?i)(key_secret|api[_-]?key|password|token)\s*=\s*['\"][^'\"]{8,}")
    for path in SRC.rglob("*.py"):
        text = path.read_text(); tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.id}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "system":
                forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}:os.system")
        if secret_re.search(text): secrets.append(str(path.relative_to(ROOT)))
    return {"forbidden_constructs": forbidden, "secret_findings": secrets}


def testmode_stage() -> dict:
    key_id = os.getenv("RAZORPAY_KEY_ID"); key_secret = os.getenv("RAZORPAY_KEY_SECRET"); wh = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not key_id or not key_secret or not wh:
        return {"status": "NOT_RUN", "reason": "Razorpay Test Mode credentials are not present"}
    if not key_id.startswith("rzp_test_"):
        return {"status": "REFUSED", "reason": "Non-Test-Mode Razorpay key refused"}
    # API smoke is intentionally separate from Standard Checkout/webhook certification.
    from agentcommit.payments.razorpay import HttpRazorpayGateway, deterministic_receipt
    gateway = HttpRazorpayGateway(key_id, key_secret)
    receipt = deterministic_receipt("v4-cert-" + git_sha()[:12])
    matches = gateway.orders_by_receipt(receipt=receipt)
    exact = [o for o in matches if o.amount_paise == 100 and o.currency == "INR" and o.receipt == receipt]
    order = exact[0] if exact else gateway.create_order(amount_paise=100, currency="INR", receipt=receipt)
    fetched = gateway.fetch_order(order_id=order.order_id)
    if fetched.order_id != order.order_id or fetched.amount_paise != 100 or fetched.currency != "INR" or fetched.receipt != receipt:
        return {"status": "FAIL", "reason": "Test Mode order fetch mismatch"}
    return {"status": "PASS", "order_id_sha256": hashlib.sha256(order.order_id.encode()).hexdigest(), "checkout_webhook": "MANUAL_REQUIRED"}


def stage_path(name: str) -> Path:
    return RESULTS / "stages" / f"{name}.json"


def write_stage(name: str, data: dict) -> None:
    payload = {"stage": name, "provenance": provenance(), "data": data}
    path = stage_path(name); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(json.dumps(payload, indent=2))


def load_stage(name: str) -> dict:
    p = stage_path(name)
    if not p.exists(): raise RuntimeError(f"missing certification stage: {name}")
    return json.loads(p.read_text())


def run_stage(name: str, run_id: int | None) -> int:
    provenance()
    if name == "coverage": data, label = coverage_stage(), "coverage"
    elif name == "stability":
        if run_id is None or not 1 <= run_id <= STABILITY_RUNS: raise RuntimeError("stability requires --run-id 1..5")
        data, label = stability_once(), f"stability-{run_id}"
    elif name == "differential": data, label = differential_stage(), "differential"
    elif name == "races": data, label = race_stage(), "races"
    elif name == "performance": data, label = performance_stage(), "performance"
    elif name == "security": data, label = security_stage(), "security"
    elif name == "testmode": data, label = testmode_stage(), "testmode"
    else: raise RuntimeError(f"unknown stage: {name}")
    write_stage(label, data); return 0


def aggregate() -> int:
    prov = provenance()
    names = ["coverage", *(f"stability-{i}" for i in range(1, 6)), "differential", "races", "performance", "security", "testmode"]
    stages = {n: load_stage(n) for n in names}
    for n, payload in stages.items():
        p = payload["provenance"]
        if p["git_sha"] != prov["git_sha"] or p["tracked_hash"] != prov["tracked_hash"] or not p["clean"]:
            raise RuntimeError(f"stale/mixed provenance in stage {n}")
    cov=stages["coverage"]["data"]; stability=[stages[f"stability-{i}"]["data"] for i in range(1,6)]
    diff=stages["differential"]["data"]; races=stages["races"]["data"]; perf=stages["performance"]["data"]; sec=stages["security"]["data"]; tm=stages["testmode"]["data"]
    offline = all((
        cov["tests_passed"] > 0, cov["line_pct"] >= LINE_GATE, cov["branch_pct"] >= BRANCH_GATE,
        all(x["passed"] for x in stability), diff["mismatches"] == 0, races["returncode"] == 0,
        perf["v4_commit_p95_ms"] < LATENCY_P95_MS, perf["dispatch_p95_ms"] < LATENCY_P95_MS,
        not sec["forbidden_constructs"], not sec["secret_findings"], git_clean(),
    ))
    metrics={
        "version":"v4.0", "provenance":prov, "coverage":cov,
        "stability":{"runs":5,"passed":sum(x["passed"] for x in stability),"flaky_runs":sum(not x["passed"] for x in stability),"test_counts":[x["test_count"] for x in stability]},
        "differential":diff,"races":races,"performance":perf,"security":sec,"testmode":tm,
        "v4_safety_promotion":offline,"full_v4_certified":False,
    }
    RESULTS.mkdir(parents=True,exist_ok=True); (RESULTS/"metrics.json").write_text(json.dumps(metrics,indent=2,sort_keys=True))
    report=["# AgentCommit V4.0 Intent-Safety Promotion","",f"- Git SHA: `{prov['git_sha']}`",f"- Tracked source hash: `{prov['tracked_hash']}`",f"- Tests: **{cov['tests_passed']}**",f"- Coverage: **{cov['line_pct']:.2f}% line / {cov['branch_pct']:.2f}% branch**",f"- Stability: **{sum(x['passed'] for x in stability)}/5**",f"- Differential: **{diff['cases']:,} cases / {diff['mismatches']} mismatches**",f"- Focused races/hardening: **{races['tests_passed']} tests**",f"- p95 V4 commit/dispatch: **{perf['v4_commit_p95_ms']:.2f} / {perf['dispatch_p95_ms']:.2f} ms**",f"- Security findings: **{len(sec['forbidden_constructs'])+len(sec['secret_findings'])}**",f"- Razorpay Test Mode: **{tm['status']}**","","## Decision","",f"**{'PROMOTE V4 SAFETY RC' if offline else 'DO NOT PROMOTE'}**","","Full V4 certification remains blocked until a real LLM adapter passes held-out AI evals and the real Razorpay Test Mode Checkout/webhook evidence is completed."]
    (RESULTS/"PROMOTION.md").write_text("\n".join(report)+"\n")
    print(json.dumps(metrics,indent=2)); return 0 if offline else 1


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(); p.add_argument("--stage",choices=["coverage","stability","differential","races","performance","security","testmode"]); p.add_argument("--run-id",type=int); p.add_argument("--aggregate",action="store_true"); a=p.parse_args(argv)
    if a.aggregate: return aggregate()
    if not a.stage: p.error("--stage or --aggregate required")
    return run_stage(a.stage,a.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
