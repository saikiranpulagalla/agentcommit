from __future__ import annotations

from agentcommit.ai import (
    ConstraintOp, HardConstraint, IntentGoldCase, IntentStatus, PreferenceDirection,
    ScriptedJsonModel, SoftPreference, StructuredIntentCompiler, evaluate_intent_compiler,
)


def response(case: IntentGoldCase):
    return {
        "status": case.expected_status.value,
        "hard_constraints": [
            {"field": c.field, "op": c.op.value, "value": list(c.value) if isinstance(c.value, tuple) else c.value}
            for c in case.expected_hard_constraints
        ],
        "soft_preferences": [
            {"field": p.field, "direction": p.direction.value} for p in case.expected_soft_preferences
        ],
        "substitution_allowed": case.expected_substitution_allowed,
        "unresolved_fields": list(case.expected_unresolved_fields),
    }


def test_eval_harness_perfect_scripted_model_scores_one():
    cases = [
        IntentGoldCase(
            "c1", "27 inch 4K USB-C monitor under 40k",
            IntentStatus.READY,
            (
                HardConstraint("screen_size_inches", ConstraintOp.EQ, 27),
                HardConstraint("resolution", ConstraintOp.EQ, "4K"),
                HardConstraint("usb_c", ConstraintOp.EQ, True),
                HardConstraint("price_paise", ConstraintOp.LTE, 4_000_000),
            ),
        ),
        IntentGoldCase(
            "c2", "buy me a monitor", IntentStatus.NEEDS_CLARIFICATION, (),
            expected_unresolved_fields=("budget", "screen_size"),
        ),
        IntentGoldCase(
            "c3", "cheapest USB-C monitor, another model is fine", IntentStatus.READY,
            (HardConstraint("usb_c", ConstraintOp.EQ, True),),
            (SoftPreference("price_paise", PreferenceDirection.MINIMIZE),), True,
        ),
    ]
    model = ScriptedJsonModel([response(c) for c in cases])
    metrics = evaluate_intent_compiler(compiler=StructuredIntentCompiler(model), cases=cases)
    assert metrics.cases == 3 and metrics.compile_failures == 0
    assert metrics.hard_constraint_exact_match == 1.0
    assert metrics.critical_constraint_exact_match == 1.0
    assert metrics.clarification_exact_match == 1.0
    assert metrics.substitution_accuracy == 1.0


def test_eval_harness_counts_compile_failure_and_false_positive_constraint():
    cases = [
        IntentGoldCase("c1", "USB-C", IntentStatus.READY, (HardConstraint("usb_c", ConstraintOp.EQ, True),)),
        IntentGoldCase("c2", "monitor", IntentStatus.NEEDS_CLARIFICATION, (), expected_unresolved_fields=("budget",)),
    ]
    model = ScriptedJsonModel([
        {
            "status":"READY",
            "hard_constraints":[
                {"field":"usb_c","op":"EQ","value":True},
                {"field":"price_paise","op":"LTE","value":4_000_000},
            ],
            "soft_preferences":[],"substitution_allowed":False,"unresolved_fields":[],
        },
        {"bad":"schema"},
        {"bad":"schema again"},
    ])
    metrics = evaluate_intent_compiler(compiler=StructuredIntentCompiler(model), cases=cases)
    assert metrics.compile_failures == 1
    assert metrics.hard_constraint_precision < 1.0
    assert metrics.hard_constraint_recall == 1.0
    assert metrics.hard_constraint_exact_match == 0.0
