from __future__ import annotations

from pathlib import Path
from agentcommit.ai import load_intent_gold_jsonl
from agentcommit.ai.critical import extract_critical_expectation
from agentcommit.ai.intent import ConstraintOp

DATA = Path(__file__).parents[1] / "evals" / "v41" / "heldout_intents.jsonl"


def test_heldout_dataset_loads_and_has_expected_breadth():
    cases=load_intent_gold_jsonl(DATA)
    assert len(cases) >= 50
    assert len({c.case_id for c in cases}) == len(cases)
    assert any(c.expected_status.value == "NEEDS_CLARIFICATION" for c in cases)
    assert any(c.expected_substitution_allowed for c in cases)


def test_gold_critical_labels_agree_with_deterministic_crosscheck():
    for case in load_intent_gold_jsonl(DATA):
        expected=extract_critical_expectation(case.raw_request)
        if expected.max_price_paise is not None:
            matches=[c for c in case.expected_hard_constraints if c.field=="price_paise" and c.op is ConstraintOp.LTE]
            assert len(matches)==1 and matches[0].value==expected.max_price_paise, case.case_id
        if expected.quantity is not None:
            matches=[c for c in case.expected_hard_constraints if c.field=="quantity" and c.op is ConstraintOp.EQ]
            assert len(matches)==1 and matches[0].value==expected.quantity, case.case_id
