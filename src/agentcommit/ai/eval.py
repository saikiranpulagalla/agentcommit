from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from agentcommit.ai.intent import HardConstraint, IntentSpec, IntentStatus, SoftPreference
from agentcommit.ai.structured import CRITICAL_FIELDS
from agentcommit.ai.intent import IntentCompiler


def _constraint_key(c: HardConstraint) -> tuple:
    value = tuple(c.value) if isinstance(c.value, tuple) else c.value
    return (c.field, c.op.value, type(value).__name__, value)


def _preference_key(p: SoftPreference) -> tuple:
    return (p.field, p.direction.value)


@dataclass(frozen=True, slots=True)
class IntentGoldCase:
    case_id: str
    raw_request: str
    expected_status: IntentStatus
    expected_hard_constraints: tuple[HardConstraint, ...]
    expected_soft_preferences: tuple[SoftPreference, ...] = ()
    expected_substitution_allowed: bool = False
    expected_unresolved_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntentEvalMetrics:
    cases: int
    compiled_cases: int
    compile_failures: int
    exact_status_accuracy: float
    hard_constraint_exact_match: float
    hard_constraint_precision: float
    hard_constraint_recall: float
    critical_constraint_exact_match: float
    soft_preference_exact_match: float
    substitution_accuracy: float
    clarification_exact_match: float


def evaluate_intent_compiler(*, compiler: IntentCompiler, cases: Sequence[IntentGoldCase], buyer_id: str = "eval_buyer") -> IntentEvalMetrics:
    if not cases:
        raise ValueError("cases cannot be empty")
    compiled = 0
    failures = 0
    status_ok = hard_exact = critical_exact = soft_exact = subst_ok = clarify_exact = 0
    tp = fp = fn = 0
    for idx, case in enumerate(cases):
        try:
            got = compiler.compile(intent_id=f"eval_{idx}", buyer_id=buyer_id, raw_request=case.raw_request)
        except Exception:
            failures += 1
            continue
        compiled += 1
        if got.status is case.expected_status:
            status_ok += 1
        gold_h = {_constraint_key(x) for x in case.expected_hard_constraints}
        got_h = {_constraint_key(x) for x in got.hard_constraints}
        if got_h == gold_h:
            hard_exact += 1
        tp += len(got_h & gold_h)
        fp += len(got_h - gold_h)
        fn += len(gold_h - got_h)
        gold_critical = {x for x in gold_h if x[0] in CRITICAL_FIELDS}
        got_critical = {x for x in got_h if x[0] in CRITICAL_FIELDS}
        if got_critical == gold_critical:
            critical_exact += 1
        if {_preference_key(x) for x in got.soft_preferences} == {_preference_key(x) for x in case.expected_soft_preferences}:
            soft_exact += 1
        if got.substitution_allowed is case.expected_substitution_allowed:
            subst_ok += 1
        if set(got.unresolved_fields) == set(case.expected_unresolved_fields):
            clarify_exact += 1
    n = len(cases)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return IntentEvalMetrics(
        cases=n,
        compiled_cases=compiled,
        compile_failures=failures,
        exact_status_accuracy=status_ok / n,
        hard_constraint_exact_match=hard_exact / n,
        hard_constraint_precision=precision,
        hard_constraint_recall=recall,
        critical_constraint_exact_match=critical_exact / n,
        soft_preference_exact_match=soft_exact / n,
        substitution_accuracy=subst_ok / n,
        clarification_exact_match=clarify_exact / n,
    )

from pathlib import Path
import json
from agentcommit.ai.planner import CandidatePlanner, CatalogCandidate
from agentcommit.ai.intent import ProductFacts, ConstraintOp, PreferenceDirection, evaluate_hard_constraints
from agentcommit.domain.models import DomainError


def load_intent_gold_jsonl(path: str | Path) -> tuple[IntentGoldCase, ...]:
    rows: list[IntentGoldCase] = []
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DomainError(f"invalid gold JSON at line {lineno}") from exc
            allowed = {
                "case_id", "raw_request", "expected_status", "hard_constraints",
                "soft_preferences", "substitution_allowed", "unresolved_fields", "category",
            }
            if not isinstance(raw, dict) or set(raw) != allowed:
                raise DomainError(f"gold schema mismatch at line {lineno}")
            hard: list[HardConstraint] = []
            for item in raw["hard_constraints"]:
                if not isinstance(item, dict) or set(item) != {"field", "op", "value"}:
                    raise DomainError(f"invalid hard constraint at line {lineno}")
                op = ConstraintOp(item["op"])
                value = tuple(item["value"]) if op in {ConstraintOp.IN, ConstraintOp.NOT_IN} else item["value"]
                hard.append(HardConstraint(item["field"], op, value))
            soft = tuple(
                SoftPreference(item["field"], PreferenceDirection(item["direction"]))
                for item in raw["soft_preferences"]
            )
            rows.append(IntentGoldCase(
                case_id=raw["case_id"], raw_request=raw["raw_request"],
                expected_status=IntentStatus(raw["expected_status"]),
                expected_hard_constraints=tuple(hard), expected_soft_preferences=soft,
                expected_substitution_allowed=raw["substitution_allowed"],
                expected_unresolved_fields=tuple(raw["unresolved_fields"]),
            ))
    if not rows:
        raise DomainError("gold dataset is empty")
    ids = [x.case_id for x in rows]
    if len(ids) != len(set(ids)):
        raise DomainError("duplicate gold case_id")
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class PlanningGoldCase:
    case_id: str
    intent: IntentSpec
    catalog: tuple[CatalogCandidate, ...]
    allowed_selected_skus: frozenset[str]
    expected_outcome: str = "SELECTED"


@dataclass(frozen=True, slots=True)
class PlanningEvalMetrics:
    cases: int
    outcome_accuracy: float
    expected_selection_accuracy: float
    unsafe_selection_rate: float
    average_model_calls: float


def evaluate_candidate_planner(*, planner: CandidatePlanner, cases: Sequence[PlanningGoldCase]) -> PlanningEvalMetrics:
    if not cases:
        raise ValueError("cases cannot be empty")
    outcome_ok = selection_ok = unsafe = total_calls = 0
    for case in cases:
        result = planner.plan(intent=case.intent, catalog=case.catalog)
        total_calls += result.model_calls
        if result.outcome == case.expected_outcome:
            outcome_ok += 1
        if result.selected is not None:
            if result.selected.sku in case.allowed_selected_skus:
                selection_ok += 1
            if not evaluate_hard_constraints(case.intent, result.selected).satisfied:
                unsafe += 1
        elif not case.allowed_selected_skus:
            selection_ok += 1
    n = len(cases)
    return PlanningEvalMetrics(
        cases=n,
        outcome_accuracy=outcome_ok / n,
        expected_selection_accuracy=selection_ok / n,
        unsafe_selection_rate=unsafe / n,
        average_model_calls=total_calls / n,
    )
