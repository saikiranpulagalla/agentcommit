from __future__ import annotations

import hashlib
import json
import sqlite3
import pytest

from agentcommit.ai.intent import (
    ConstraintOp, HardConstraint, IntentSpec, IntentStatus, PreferenceDirection,
    ProductFacts, SoftPreference,
)
from agentcommit.domain.models import DelegationGrant, DomainError, INT64_MAX
from agentcommit.store.sqlite_store import Conflict, MerchantStore, NotFound
from conftest import NOW
from test_v4_persistence import add_monitor, delegation, intent, setup_v4, activate


@pytest.mark.parametrize("field", ["Bad", "a-b", "", 1])
def test_invalid_constraint_field_rejected(field):
    with pytest.raises(DomainError): HardConstraint(field,ConstraintOp.EQ,1)

@pytest.mark.parametrize("value", [INT64_MAX+1, -(INT64_MAX+1), 1.5, None, "\n", "x"*129])
def test_invalid_constraint_scalar_rejected(value):
    with pytest.raises(DomainError): HardConstraint("x",ConstraintOp.EQ,value)


def test_invalid_constraint_and_preference_enum_rejected():
    with pytest.raises(DomainError): HardConstraint("x","EQ",1)  # type: ignore[arg-type]
    with pytest.raises(DomainError): SoftPreference("x","MINIMIZE")  # type: ignore[arg-type]

@pytest.mark.parametrize("value", [(), [], None])
def test_set_constraint_requires_nonempty_tuple(value):
    with pytest.raises(DomainError): HardConstraint("x",ConstraintOp.IN,value)


def test_intent_constructor_type_and_status_guards():
    c=HardConstraint("x",ConstraintOp.EQ,1)
    with pytest.raises(DomainError): IntentSpec("i","b","",(c,))
    with pytest.raises(DomainError): IntentSpec("i","b","x",[c])  # type: ignore[arg-type]
    with pytest.raises(DomainError): IntentSpec("i","b","x",(c,),[])  # type: ignore[arg-type]
    with pytest.raises(DomainError): IntentSpec("i","b","x",(c,),substitution_allowed=1)  # type: ignore[arg-type]
    with pytest.raises(DomainError): IntentSpec("i","b","x",(c,),status="READY")  # type: ignore[arg-type]
    with pytest.raises(DomainError): IntentSpec("i","b","x",(c,),unresolved_fields=["x"])  # type: ignore[arg-type]
    with pytest.raises(DomainError): IntentSpec("i","b","x",(c,),unresolved_fields=("x",))
    with pytest.raises(DomainError): IntentSpec("i","b","x",(c,),status=IntentStatus.NEEDS_CLARIFICATION)

@pytest.mark.parametrize("text", [None, "x"*16001, "not-json", "[]"])
def test_intent_json_outer_guards(text):
    with pytest.raises(DomainError): IntentSpec.from_canonical_json(text)  # type: ignore[arg-type]


def test_intent_json_inner_schema_guards():
    base=json.loads(IntentSpec("i","b","x",(HardConstraint("x",ConstraintOp.EQ,1),)).canonical_json())
    bad=dict(base); bad["extra"]=1
    with pytest.raises(DomainError): IntentSpec.from_canonical_json(json.dumps(bad))
    bad=dict(base); bad["hard_constraints"]=[{"field":"x","op":"EQ"}]
    with pytest.raises(DomainError): IntentSpec.from_canonical_json(json.dumps(bad))
    bad=dict(base); bad["soft_preferences"]=[{"field":"x"}]
    with pytest.raises(DomainError): IntentSpec.from_canonical_json(json.dumps(bad))


def test_product_facts_outer_guards():
    with pytest.raises(DomainError): ProductFacts("m","s","c","INR",1,1,1,[])  # type: ignore[arg-type]
    with pytest.raises(DomainError): ProductFacts("m","s","c","INR",1,1,1,{"Bad":1})


def test_put_product_facts_missing_and_revision_exhaustion(store):
    with pytest.raises(NotFound): store.put_product_facts(merchant_id="m",sku="A",attributes={})
    add_monitor(store,"A",price=100)
    c=store._connect(); c.execute("UPDATE product_facts SET revision=? WHERE merchant_id='m' AND sku='A'",(INT64_MAX,)); c.close()
    with pytest.raises(Conflict,match="revision exhausted"):
        store.put_product_facts(merchant_id="m",sku="A",attributes={"usb_c":True})


def test_attach_missing_expired_buyer_and_after_plan_guards(store):
    add_monitor(store,"A",price=100)
    i=intent(); store.create_intent(i)
    with pytest.raises(NotFound): store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)
    store.create_delegation(DelegationGrant("D","other","m","monitor",4_000_000,"INR",1,NOW+1000))
    with pytest.raises(Conflict,match="buyer mismatch"):
        store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)
    # separate expired delegation
    store.create_delegation(DelegationGrant("D2","buyer","m","monitor",4_000_000,"INR",1,NOW))
    with pytest.raises(Conflict,match="inactive/expired"):
        store.attach_intent_to_delegation(delegation_id="D2",intent_id="I",max_replans=1,now_ms=NOW)


def test_attach_after_plan_or_grant_is_rejected(store):
    add_monitor(store,"A",price=100); store.create_delegation(DelegationGrant("D","buyer","m","monitor",100,"INR",1,NOW+10000))
    store.create_quote(quote_id="Q",merchant_id="m",sku="A")
    store.activate_plan_from_quote(plan_id="P",grant_id="G",execution_id="E",reservation_id="R",delegation_id="D",quote_id="Q",now_ms=NOW,ttl_ms=1000)
    store.create_intent(IntentSpec("I","buyer","x",()))
    with pytest.raises(Conflict,match="before planning"):
        store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)


def test_negative_and_overflow_replan_budget_rejected(store):
    add_monitor(store,"A",price=100); store.create_delegation(DelegationGrant("D","buyer","m","monitor",100,"INR",1,NOW+1000)); store.create_intent(IntentSpec("I","buyer","x",()))
    for v in (-1, INT64_MAX+1, True):
        with pytest.raises(DomainError): store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=v,now_ms=NOW)  # type: ignore[arg-type]


def test_missing_or_crossed_v4_binding_fails_closed(store):
    setup_v4(store); g=activate(store,"a","A")
    c=store._connect(); c.execute("DELETE FROM grant_intent_bindings WHERE grant_id=?",(g.grant_id,)); c.close()
    with pytest.raises(Conflict,match="INTENT_BINDING_MISSING"):
        store.commit(request_id="r",grant_id=g.grant_id,now_ms=NOW+1)


def test_stale_plan_generation_binding_fails_closed(store):
    setup_v4(store); g=activate(store,"a","A")
    c=store._connect(); c.execute("UPDATE grant_intent_bindings SET expected_plan_generation=99 WHERE grant_id=?",(g.grant_id,)); c.close()
    with pytest.raises(Conflict,match="STALE_PLAN_INTENT_BINDING"):
        store.commit(request_id="r",grant_id=g.grant_id,now_ms=NOW+1)


def test_stale_intent_binding_fails_closed_even_with_valid_intent_row(store):
    setup_v4(store); g=activate(store,"a","A")
    c=store._connect(); c.execute("UPDATE grant_intent_bindings SET expected_intent_version=2 WHERE grant_id=?",(g.grant_id,)); c.close()
    with pytest.raises(Conflict,match="STALE_INTENT"):
        store.commit(request_id="r",grant_id=g.grant_id,now_ms=NOW+1)


def test_corrupt_intent_binding_hash_fails_during_plan(store):
    add_monitor(store,"A",price=3_800_000); store.create_delegation(delegation()); store.create_intent(intent())
    store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)
    c=store._connect(); c.execute("UPDATE delegation_intents SET expected_intent_hash='bad' WHERE delegation_id='D'"); c.close()
    store.create_quote(quote_id="Q",merchant_id="m",sku="A")
    with pytest.raises(Conflict,match="stale/corrupt intent binding"):
        store.activate_plan_from_quote(plan_id="P",grant_id="G",execution_id="E",reservation_id="R",delegation_id="D",quote_id="Q",now_ms=NOW,ttl_ms=1000)


def test_corrupt_product_facts_json_and_hash_shape_fail_closed(store):
    setup_v4(store)
    c=store._connect(); c.execute("UPDATE product_facts SET attributes_json='[]',facts_hash=? WHERE merchant_id='m' AND sku='A'",(hashlib.sha256(b'[]').hexdigest(),)); c.close()
    store.create_quote(quote_id="Q",merchant_id="m",sku="A")
    with pytest.raises(Conflict,match="must be object"):
        store.activate_plan_from_quote(plan_id="P",grant_id="G",execution_id="E",reservation_id="R",delegation_id="D",quote_id="Q",now_ms=NOW,ttl_ms=1000)


def test_replan_budget_counter_exhaustion_fails_before_mutation(store):
    setup_v4(store,max_replans=2); activate(store,"a","A")
    c=store._connect(); c.execute("UPDATE delegation_intents SET version=? WHERE delegation_id='D'",(INT64_MAX,)); c.close()
    store.create_quote(quote_id="Q-b",merchant_id="m",sku="B")
    before=store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")
    with pytest.raises(Conflict,match="counter exhausted"):
        store.activate_plan_from_quote(plan_id="P-b",grant_id="G-b",execution_id="E-b",reservation_id="R-b",delegation_id="D",quote_id="Q-b",now_ms=NOW,ttl_ms=1000)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==before
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")=="ACTIVE"


def test_replan_budget_cas_loss_rolls_back_everything(store):
    setup_v4(store,max_replans=1); activate(store,"a","A")
    store.create_quote(quote_id="Q-b",merchant_id="m",sku="B")
    c=store._connect()
    c.execute("CREATE TEMP TRIGGER suppress_v4_budget BEFORE UPDATE ON delegation_intents BEGIN SELECT RAISE(IGNORE); END;")
    # trigger is connection-local, activate opens another connection, so use persistent trigger instead
    c.execute("DROP TRIGGER suppress_v4_budget")
    c.execute("CREATE TRIGGER suppress_v4_budget BEFORE UPDATE ON delegation_intents BEGIN SELECT RAISE(IGNORE); END;")
    c.close()
    with pytest.raises(Conflict,match="replan budget CAS lost"):
        store.activate_plan_from_quote(plan_id="P-b",grant_id="G-b",execution_id="E-b",reservation_id="R-b",delegation_id="D",quote_id="Q-b",now_ms=NOW,ttl_ms=1000)
    c=store._connect(); c.execute("DROP TRIGGER suppress_v4_budget"); c.close()
    assert store.scalar("SELECT replans_used FROM delegation_intents WHERE delegation_id='D'")==0
    assert store.scalar("SELECT COUNT(*) FROM plans WHERE plan_id='P-b'")==0
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")=="ACTIVE"
