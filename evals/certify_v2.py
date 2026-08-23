from __future__ import annotations

import ast
from dataclasses import replace
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
RESULTS = ROOT / "evals" / "results" / "v2"
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
    for rel in sorted(f for f in files if f):
        data = (ROOT / rel).read_bytes()
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def collect_test_count(*paths: str) -> int:
    args = [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths]
    output = run(*args).stdout
    total = 0
    for line in output.splitlines():
        m = re.search(r":\s*(\d+)\s*$", line)
        if m:
            total += int(m.group(1))
    return total


def coverage_stage() -> dict:
    test_count = collect_test_count()
    run(sys.executable, "-m", "coverage", "erase")
    run(sys.executable, "-m", "coverage", "run", "--branch", "-m", "pytest", "-q", capture=False)
    run(sys.executable, "-m", "coverage", "json", "-o", "coverage.json")
    data = json.loads((ROOT / "coverage.json").read_text())
    files = [v for k, v in data["files"].items() if k.startswith("src/")]
    statements = sum(v["summary"]["num_statements"] for v in files)
    missing_lines = sum(v["summary"]["missing_lines"] for v in files)
    branches = sum(v["summary"]["num_branches"] for v in files)
    missing_branches = sum(v["summary"]["missing_branches"] for v in files)
    return {
        "tests_passed": test_count,
        "line_pct": 100.0 * (statements - missing_lines) / statements,
        "branch_pct": 100.0 * (branches - missing_branches) / branches,
        "statements": statements,
        "branches": branches,
    }


def stability_stage() -> dict:
    passed = 0
    test_count = collect_test_count()
    counts: list[int] = []
    for _ in range(STABILITY_RUNS):
        # Do not PIPE pytest output here: multiprocessing children can inherit the pipe and
        # keep communicate() blocked after the pytest parent exits.
        p = run(sys.executable, "-m", "pytest", "-q", check=False, capture=False)
        counts.append(test_count if p.returncode == 0 else 0)
        if p.returncode == 0:
            passed += 1
    return {"runs": STABILITY_RUNS, "passed": passed, "test_counts": counts, "flaky_runs": STABILITY_RUNS - passed}


def differential_stage() -> dict:
    sys.path.insert(0, str(SRC))
    from agentcommit.domain.models import (
        AuthorizationMode, DelegationGrant, DelegationState, DomainSnapshot, ExecutionGrant,
        ExecutionRecord, ExecutionState, GrantState, MerchantQuote, MerchantReservation,
        PaymentProjection, PaymentState, ReservationState,
    )
    from agentcommit.domain.policy import evaluate_commit
    from agentcommit.domain.spec import spec_allows_commit

    now = 1_800_000_000_000
    base = DomainSnapshot(
        DelegationGrant("d","b","m","monitor",4_000_000,"INR",1,now+10_000),
        MerchantQuote("q","m","monitor","sku",3_899_000,"INR",1,1,1),
        MerchantReservation("r","q","m","monitor","sku",3_899_000,"INR",1,1,1,now+5_000),
        ExecutionGrant("g","d",1,"b","r","q","m","monitor","sku",3_899_000,"INR",1,1,1),
        ExecutionRecord("e","b"), PaymentProjection(), 0,
    )
    rng = random.Random(0xA63E17)
    mismatches = 0
    allowed = 0
    for i in range(DIFF_CASES):
        s = base
        # Deterministic hostile perturbations with valid dataclass values. Multiple axes may change at once.
        choice = rng.randrange(16)
        if choice == 0:
            s = replace(s, delegation=replace(s.delegation, status=rng.choice(list(DelegationState))))
        elif choice == 1:
            s = replace(s, grant=replace(s.grant, status=rng.choice(list(GrantState))))
        elif choice == 2:
            s = replace(s, reservation=replace(s.reservation, status=rng.choice(list(ReservationState))))
        elif choice == 3:
            s = replace(s, execution=replace(s.execution, state=rng.choice(list(ExecutionState))))
        elif choice == 4:
            ps = rng.choice(list(PaymentState))
            s = replace(s, payment=PaymentProjection(None if ps is PaymentState.UNKNOWN else "p", ps))
        elif choice == 5:
            s = replace(s, grant=replace(s.grant, expected_buyer_id=rng.choice(["b","other"])))
        elif choice == 6:
            s = replace(s, grant=replace(s.grant, expected_delegation_version=rng.choice([1,2])))
        elif choice == 7:
            gen = rng.choice([0,1,2]); s = replace(s, delegation=replace(s.delegation, plan_generation=gen), grant=replace(s.grant, expected_plan_generation=rng.choice([0,1,2])))
        elif choice == 8:
            s = replace(s, quote=replace(s.quote, sku=rng.choice(["sku","other"])))
        elif choice == 9:
            s = replace(s, quote=replace(s.quote, amount_paise=rng.choice([3_899_000,4_500_000])))
        elif choice == 10:
            exact = rng.choice([True, False])
            if exact:
                s = replace(s, delegation=replace(s.delegation, mode=AuthorizationMode.EXACT, exact_sku="sku", exact_amount_paise=3_899_000, substitution_allowed=False))
        elif choice == 11:
            s = replace(s, grant=replace(s.grant, expected_reservation_revision=rng.choice([1,2])))
        elif choice == 12:
            s = replace(s, commit_count=rng.choice([0,1]))
        elif choice == 13:
            s = replace(s, delegation=replace(s.delegation, expires_at_ms=rng.choice([now,now+10_000])))
        elif choice == 14:
            s = replace(s, reservation=replace(s.reservation, expires_at_ms=rng.choice([now,now+5_000])))
        else:
            s = replace(s, delegation=replace(s.delegation, max_quantity=1), quote=replace(s.quote, quantity=1), reservation=replace(s.reservation, quantity=1), grant=replace(s.grant, expected_quantity=1))
        prod = evaluate_commit(s, now_ms=now).allowed
        spec = spec_allows_commit(s, now_ms=now)
        allowed += int(prod)
        mismatches += int(prod != spec)
    return {"cases": DIFF_CASES, "mismatches": mismatches, "allowed_cases": allowed}


def race_stage() -> dict:
    paths = ("tests/test_process_races.py", "tests/test_v2_process_races.py", "tests/test_v2_hardening.py")
    test_count = collect_test_count(*paths)
    p = run(sys.executable, "-m", "pytest", "-q", *paths, capture=False)
    return {"tests_passed": test_count, "returncode": p.returncode}


def performance_stage() -> dict:
    sys.path.insert(0, str(SRC))
    from agentcommit.domain.models import DelegationGrant
    from agentcommit.store.sqlite_store import MerchantStore

    now = 1_800_000_000_000
    activation_ms: list[float] = []
    replan_ms: list[float] = []
    commit_ms: list[float] = []
    with tempfile.TemporaryDirectory() as td:
        store = MerchantStore(Path(td) / "bench.db")
        for i in range(80):
            m=f"m{i}"; d=f"d{i}"; b=f"b{i}"; qa=f"qa{i}"; qb=f"qb{i}"
            store.add_product(merchant_id=m,sku="A",category="monitor",currency="INR",price_paise=100,available_quantity=1)
            store.add_product(merchant_id=m,sku="B",category="monitor",currency="INR",price_paise=100,available_quantity=1)
            store.create_delegation(DelegationGrant(d,b,m,"monitor",100,"INR",1,now+100_000))
            store.create_quote(quote_id=qa,merchant_id=m,sku="A")
            store.create_quote(quote_id=qb,merchant_id=m,sku="B")
            t=time.perf_counter(); store.activate_plan_from_quote(plan_id=f"pa{i}",grant_id=f"ga{i}",execution_id=f"ea{i}",reservation_id=f"ra{i}",delegation_id=d,quote_id=qa,now_ms=now,ttl_ms=10_000); activation_ms.append((time.perf_counter()-t)*1000)
            t=time.perf_counter(); store.activate_plan_from_quote(plan_id=f"pb{i}",grant_id=f"gb{i}",execution_id=f"eb{i}",reservation_id=f"rb{i}",delegation_id=d,quote_id=qb,now_ms=now+1,ttl_ms=10_000); replan_ms.append((time.perf_counter()-t)*1000)
            t=time.perf_counter(); store.commit(request_id=f"req{i}",grant_id=f"gb{i}",now_ms=now+2); commit_ms.append((time.perf_counter()-t)*1000)
    def p95(xs: list[float]) -> float:
        return sorted(xs)[max(0, int(0.95*len(xs))-1)]
    return {
        "samples": 80,
        "activation_p50_ms": statistics.median(activation_ms), "activation_p95_ms": p95(activation_ms),
        "replan_p50_ms": statistics.median(replan_ms), "replan_p95_ms": p95(replan_ms),
        "commit_p50_ms": statistics.median(commit_ms), "commit_p95_ms": p95(commit_ms),
    }


def security_stage() -> dict:
    forbidden: list[str] = []
    secret_hits: list[str] = []
    secret_re = re.compile(r"(?i)(key_secret|api[_-]?key|password|token)\s*=\s*['\"][^'\"]{8,}")
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in {"eval","exec","compile"}:
                    forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.id}")
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "system":
                    forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}:os.system")
        if secret_re.search(text):
            secret_hits.append(str(path.relative_to(ROOT)))
    return {"forbidden_constructs": forbidden, "secret_findings": secret_hits}


def write_report(metrics: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True))
    c=metrics["coverage"]; st=metrics["stability"]; d=metrics["differential"]; p=metrics["performance"]
    lines = [
        "# AgentCommit V2 Promotion\n",
        f"- Git SHA: `{metrics['provenance']['git_sha']}`",
        f"- Tracked source hash: `{metrics['provenance']['tracked_hash']}`",
        f"- Source tree clean: **{metrics['provenance']['clean']}**",
        f"- Tests: **{c['tests_passed']} passed**",
        f"- Source coverage: **{c['line_pct']:.2f}% line / {c['branch_pct']:.2f}% branch**",
        f"- Stability: **{st['passed']}/{st['runs']} clean runs**",
        f"- Differential oracle: **{d['cases']:,} cases / {d['mismatches']} mismatches**",
        f"- Focused inherited+V2 race/hardening tests: **{metrics['races']['tests_passed']} passed**",
        f"- V2 p95: activation **{p['activation_p95_ms']:.2f} ms**, replan **{p['replan_p95_ms']:.2f} ms**, commit **{p['commit_p95_ms']:.2f} ms**",
        f"- Security findings: **{len(metrics['security']['forbidden_constructs']) + len(metrics['security']['secret_findings'])}**",
        f"\n## Decision\n\n**{'PROMOTE' if metrics['promotion'] else 'DO NOT PROMOTE'}**\n",
    ]
    (RESULTS / "PROMOTION.md").write_text("\n".join(lines))


def current_provenance() -> dict:
    if not git_clean():
        raise RuntimeError("tracked working tree is dirty")
    return {
        "git_sha": git_sha(),
        "tracked_hash": tracked_hash(),
        "clean": True,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _stage_path(name: str) -> Path:
    return RESULTS / "stages" / f"{name}.json"


def write_stage(name: str, data: dict) -> dict:
    payload = {"stage": name, "provenance": current_provenance(), "data": data}
    path = _stage_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(json.dumps(payload, indent=2))
    return payload


def load_stage(name: str) -> dict:
    path = _stage_path(name)
    if not path.exists():
        raise RuntimeError(f"missing certification stage: {name}")
    return json.loads(path.read_text())


def stability_once() -> dict:
    test_count = collect_test_count()
    p = run(sys.executable, "-m", "pytest", "-q", check=False, capture=False)
    return {"passed": p.returncode == 0, "returncode": p.returncode, "test_count": test_count}


def run_named_stage(name: str, run_id: int | None = None) -> int:
    # Refuse to generate evidence from source drift. Result files are ignored by Git.
    current_provenance()
    if name == "coverage":
        data = coverage_stage(); stage_name = "coverage"
    elif name == "stability":
        if run_id is None or not 1 <= run_id <= STABILITY_RUNS:
            raise RuntimeError(f"stability requires --run-id 1..{STABILITY_RUNS}")
        data = stability_once(); stage_name = f"stability-{run_id}"
    elif name == "differential":
        data = differential_stage(); stage_name = "differential"
    elif name == "races":
        data = race_stage(); stage_name = "races"
    elif name == "performance":
        data = performance_stage(); stage_name = "performance"
    elif name == "security":
        data = security_stage(); stage_name = "security"
    else:
        raise RuntimeError(f"unknown stage: {name}")
    write_stage(stage_name, data)
    return 0


def aggregate() -> int:
    provenance = current_provenance()
    names = [
        "coverage",
        *(f"stability-{i}" for i in range(1, STABILITY_RUNS + 1)),
        "differential", "races", "performance", "security",
    ]
    stages = {name: load_stage(name) for name in names}
    for name, payload in stages.items():
        prov = payload.get("provenance", {})
        if prov.get("git_sha") != provenance["git_sha"] or prov.get("tracked_hash") != provenance["tracked_hash"]:
            raise RuntimeError(f"stale or mixed provenance in stage {name}")
        if not prov.get("clean"):
            raise RuntimeError(f"stage {name} was generated from a dirty tracked tree")

    cov = stages["coverage"]["data"]
    stability_runs = [stages[f"stability-{i}"]["data"] for i in range(1, STABILITY_RUNS + 1)]
    stable = {
        "runs": STABILITY_RUNS,
        "passed": sum(bool(x["passed"]) for x in stability_runs),
        "test_counts": [x["test_count"] if x["passed"] else 0 for x in stability_runs],
        "flaky_runs": sum(not bool(x["passed"]) for x in stability_runs),
    }
    diff = stages["differential"]["data"]
    races = stages["races"]["data"]
    perf = stages["performance"]["data"]
    security = stages["security"]["data"]
    still_clean = git_clean()
    promotion = all((
        cov["tests_passed"] > 0,
        cov["line_pct"] >= LINE_GATE,
        cov["branch_pct"] >= BRANCH_GATE,
        stable["passed"] == STABILITY_RUNS,
        diff["mismatches"] == 0,
        races["returncode"] == 0,
        perf["activation_p95_ms"] < LATENCY_P95_MS,
        perf["replan_p95_ms"] < LATENCY_P95_MS,
        perf["commit_p95_ms"] < LATENCY_P95_MS,
        not security["forbidden_constructs"],
        not security["secret_findings"],
        still_clean,
    ))
    metrics = {
        "version": "v2", "provenance": provenance, "coverage": cov, "stability": stable,
        "differential": diff, "races": races, "performance": perf, "security": security,
        "tree_clean_after": still_clean, "promotion": promotion,
    }
    write_report(metrics)
    print(json.dumps(metrics, indent=2))
    return 0 if promotion else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Provenance-safe staged AgentCommit V2 certification")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stage", choices=["coverage", "stability", "differential", "races", "performance", "security"])
    group.add_argument("--aggregate", action="store_true")
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args(argv)
    try:
        if args.aggregate:
            return aggregate()
        return run_named_stage(args.stage, args.run_id)
    except RuntimeError as exc:
        print(f"certification error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
