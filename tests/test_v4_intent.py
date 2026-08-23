from __future__ import annotations

from dataclasses import replace
import pytest

from agentcommit.ai.intent import (
    ConstraintOp, HardConstraint, IntentSpec, IntentStatus, PreferenceDirection,
    ProductFacts, ReferenceIntentCompiler, SoftPreference, evaluate_hard_constraints,
)
from agentcommit.domain.models import DomainError


def facts(**attrs):
    return ProductFacts(
        merchant_id="m", sku="A", category="monitor", currency="INR",
        price_paise=3_899_000, quantity=1, revision=1, attributes=attrs,
    )


def test_reference_compiler_demo_request():
    c=ReferenceIntentCompiler().compile(
        intent_id="i1", buyer_id="buyer",
        raw_request="Buy me the cheapest 27-inch 4K USB-C monitor under 40k. Another model is fine.",
    )
    assert c.status is IntentStatus.READY
    assert c.substitution_allowed is True
    assert SoftPreference("price_paise",PreferenceDirection.MINIMIZE) in c.soft_preferences
    assert evaluate_hard_constraints(c,facts(screen_size_inches=27,resolution="4K",usb_c=True)).satisfied


def test_reference_compiler_refuses_to_invent_for_vague_request():
    c=ReferenceIntentCompiler().compile(intent_id="i1",buyer_id="buyer",raw_request="Buy me a good monitor")
    assert c.status is IntentStatus.NEEDS_CLARIFICATION
    assert not evaluate_hard_constraints(c,facts(screen_size_inches=27,resolution="4K",usb_c=True)).satisfied


def test_strict_bool_vs_int_semantics():
    i=IntentSpec("i","b","USB-C required",(HardConstraint("usb_c",ConstraintOp.EQ,True),))
    assert evaluate_hard_constraints(i,facts(usb_c=True)).satisfied
    assert not evaluate_hard_constraints(i,facts(usb_c=1)).satisfied


def test_numeric_range_allows_multiple_constraints_same_field():
    i=IntentSpec("i","b","midrange",(
        HardConstraint("price_paise",ConstraintOp.GTE,3_000_000),
        HardConstraint("price_paise",ConstraintOp.LTE,4_000_000),
    ))
    assert evaluate_hard_constraints(i,facts()).satisfied
    assert not evaluate_hard_constraints(i,replace(facts(),price_paise=4_500_000)).satisfied


def test_exact_duplicate_constraint_rejected():
    c=HardConstraint("usb_c",ConstraintOp.EQ,True)
    with pytest.raises(DomainError,match="duplicate"):
        IntentSpec("i","b","x",(c,c))


def test_missing_structured_fact_fails_closed():
    i=IntentSpec("i","b","needs hdr",(HardConstraint("hdr",ConstraintOp.EQ,True),))
    r=evaluate_hard_constraints(i,facts(usb_c=True))
    assert not r.satisfied and r.violations==("MISSING:hdr",)


def test_membership_is_type_strict():
    i=IntentSpec("i","b","brand",(HardConstraint("tier",ConstraintOp.IN,(1,2)),))
    assert evaluate_hard_constraints(i,facts(tier=1)).satisfied
    assert not evaluate_hard_constraints(i,facts(tier=True)).satisfied


def test_product_attributes_are_read_only():
    f=facts(usb_c=True)
    with pytest.raises(TypeError):
        f.attributes["usb_c"]=False


def test_reserved_commerce_keys_cannot_be_spoofed_as_attributes():
    with pytest.raises(DomainError,match="reserved"):
        facts(price_paise=1)


def test_canonical_intent_round_trip_and_schema_strictness():
    i=IntentSpec("i","b","x",(
        HardConstraint("brand",ConstraintOp.NOT_IN,("bad","worse")),
    ),(SoftPreference("price_paise",PreferenceDirection.MINIMIZE),),False)
    j=IntentSpec.from_canonical_json(i.canonical_json())
    assert j==i
    with pytest.raises(DomainError):
        IntentSpec.from_canonical_json('{"intent_id":"i"}')


def test_ordered_constraint_requires_strict_int_types():
    i=IntentSpec("i","b","x",(HardConstraint("usb_c",ConstraintOp.LTE,1),))
    assert not evaluate_hard_constraints(i,facts(usb_c=True)).satisfied


def test_set_constraint_rejects_duplicate_type_value_pairs():
    with pytest.raises(DomainError,match="duplicate"):
        HardConstraint("brand",ConstraintOp.IN,("A","A"))
