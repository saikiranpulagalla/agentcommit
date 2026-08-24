from __future__ import annotations

import json
from pathlib import Path
import pytest

from agentcommit.ai import (
    CandidatePlanner, CatalogCandidate, ConstraintOp, HardConstraint, IntentSpec, IntentStatus,
    PlanningGoldCase, ProductFacts, ScriptedJsonModel, evaluate_candidate_planner,
    load_intent_gold_jsonl,
)
from agentcommit.domain.models import DomainError


def pf(sku: str, usb_c: bool) -> ProductFacts:
    return ProductFacts("m", sku, "monitor", "INR", 3_000_000, 1, 1, {"usb_c": usb_c})


def test_gold_jsonl_loader_strict_and_round_trippable(tmp_path: Path):
    row = {
        "case_id":"c1", "raw_request":"USB-C under 40k", "expected_status":"READY",
        "hard_constraints":[
            {"field":"usb_c","op":"EQ","value":True},
            {"field":"price_paise","op":"LTE","value":4_000_000},
        ],
        "soft_preferences":[], "substitution_allowed":False,
        "unresolved_fields":[], "category":"critical",
    }
    p=tmp_path/"gold.jsonl"
    p.write_text(json.dumps(row)+"\n", encoding="utf-8")
    cases=load_intent_gold_jsonl(p)
    assert len(cases)==1 and cases[0].expected_hard_constraints[1].value==4_000_000


def test_gold_loader_rejects_duplicate_ids(tmp_path: Path):
    row = {
        "case_id":"dup", "raw_request":"x", "expected_status":"READY",
        "hard_constraints":[], "soft_preferences":[], "substitution_allowed":False,
        "unresolved_fields":[], "category":"x",
    }
    p=tmp_path/"gold.jsonl"
    p.write_text(json.dumps(row)+"\n"+json.dumps(row)+"\n", encoding="utf-8")
    with pytest.raises(DomainError, match="duplicate"):
        load_intent_gold_jsonl(p)


def test_planning_eval_tracks_unsafe_selection_separately():
    intent=IntentSpec("i","b","USB-C",(HardConstraint("usb_c",ConstraintOp.EQ,True),))
    cases=(PlanningGoldCase(
        "c", intent, (CatalogCandidate(pf("bad",False)),CatalogCandidate(pf("good",True))),
        frozenset({"good"}),
    ),)
    model=ScriptedJsonModel([{"ranked_skus":["bad","good"],"reason":"x"}])
    metrics=evaluate_candidate_planner(planner=CandidatePlanner(model), cases=cases)
    assert metrics.outcome_accuracy==1.0
    assert metrics.expected_selection_accuracy==1.0
    assert metrics.unsafe_selection_rate==0.0
