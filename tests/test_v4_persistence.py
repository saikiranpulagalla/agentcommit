from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import pytest

from agentcommit.ai.intent import ConstraintOp, HardConstraint, IntentSpec, IntentStatus
from agentcommit.domain.models import DelegationGrant, GrantState, PlanState, ReservationState
from agentcommit.store.sqlite_store import Conflict, MerchantStore
from conftest import NOW


def add_monitor(store: MerchantStore, sku: str, *, price: int, stock: int=2, usb_c: bool=True, resolution: str="4K"):
    store.add_product(merchant_id="m",sku=sku,category="monitor",currency="INR",price_paise=price,available_quantity=stock)
    store.put_product_facts(merchant_id="m",sku=sku,attributes={"screen_size_inches":27,"resolution":resolution,"usb_c":usb_c,"gaming":False})


def intent(*, substitution=True, status=IntentStatus.READY):
    unresolved=() if status is IntentStatus.READY else ("budget",)
    return IntentSpec(
        "I","buyer","27-inch 4K USB-C monitor under 40k",
        (
            HardConstraint("screen_size_inches",ConstraintOp.EQ,27),
            HardConstraint("resolution",ConstraintOp.EQ,"4K"),
            HardConstraint("usb_c",ConstraintOp.EQ,True),
            HardConstraint("price_paise",ConstraintOp.LTE,4_000_000),
        ),
        substitution_allowed=substitution,status=status,unresolved_fields=unresolved,
    )


def delegation():
    return DelegationGrant("D","buyer","m","monitor",4_000_000,"INR",1,NOW+120_000,substitution_allowed=True)


def setup_v4(store: MerchantStore, *, max_replans=2, substitution=True):
    add_monitor(store,"A",price=3_800_000)
    add_monitor(store,"B",price=3_900_000)
    store.create_delegation(delegation())
    i=intent(substitution=substitution)
    store.create_intent(i)
    store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=max_replans,now_ms=NOW)


def activate(store: MerchantStore, suffix: str, sku: str):
    q=f"Q-{suffix}"; store.create_quote(quote_id=q,merchant_id="m",sku=sku)
    return store.activate_plan_from_quote(
        plan_id=f"P-{suffix}",grant_id=f"G-{suffix}",execution_id=f"E-{suffix}",reservation_id=f"R-{suffix}",
        delegation_id="D",quote_id=q,now_ms=NOW,ttl_ms=50_000,
    )


def test_attach_requires_ready_intent_and_same_buyer(store):
    add_monitor(store,"A",price=3_800_000); store.create_delegation(delegation())
    bad=intent(status=IntentStatus.NEEDS_CLARIFICATION)
    store.create_intent(bad)
    with pytest.raises(Conflict,match="clarification"):
        store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)


def test_v4_initial_plan_and_commit_success(store):
    setup_v4(store)
    g=activate(store,"a","A")
    assert store.scalar("SELECT expected_product_facts_revision FROM grant_intent_bindings WHERE grant_id=?",(g.grant_id,))==1
    r=store.commit(request_id="req",grant_id=g.grant_id,now_ms=NOW+1)
    assert r.grant_id==g.grant_id


def test_missing_product_facts_rejects_before_inventory_hold(store):
    store.add_product(merchant_id="m",sku="A",category="monitor",currency="INR",price_paise=3_800_000,available_quantity=1)
    store.create_delegation(delegation()); store.create_intent(intent())
    store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)
    store.create_quote(quote_id="Q",merchant_id="m",sku="A")
    with pytest.raises(Conflict,match="PRODUCT_FACTS_MISSING"):
        store.activate_plan_from_quote(plan_id="P",grant_id="G",execution_id="E",reservation_id="R",delegation_id="D",quote_id="Q",now_ms=NOW,ttl_ms=1000)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1
    assert store.scalar("SELECT plan_generation FROM delegations WHERE delegation_id='D'")==0


def test_hard_constraint_violation_rejects_before_reservation(store):
    add_monitor(store,"A",price=3_800_000,usb_c=False,stock=1)
    store.create_delegation(delegation()); store.create_intent(intent())
    store.attach_intent_to_delegation(delegation_id="D",intent_id="I",max_replans=1,now_ms=NOW)
    store.create_quote(quote_id="Q",merchant_id="m",sku="A")
    with pytest.raises(Conflict,match="HARD_CONSTRAINT_VIOLATION"):
        store.activate_plan_from_quote(plan_id="P",grant_id="G",execution_id="E",reservation_id="R",delegation_id="D",quote_id="Q",now_ms=NOW,ttl_ms=1000)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1


def test_product_facts_change_after_plan_blocks_stale_commit(store):
    setup_v4(store); g=activate(store,"a","A")
    store.put_product_facts(merchant_id="m",sku="A",attributes={"screen_size_inches":27,"resolution":"4K","usb_c":False,"gaming":False})
    with pytest.raises(Conflict,match="HARD_CONSTRAINT_VIOLATION|STALE_PRODUCT_FACTS"):
        store.commit(request_id="req",grant_id=g.grant_id,now_ms=NOW+1)
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(g.grant_id,))==GrantState.ACTIVE.value


def test_product_facts_revision_change_even_if_semantically_same_requires_replan(store):
    setup_v4(store); g=activate(store,"a","A")
    store.put_product_facts(merchant_id="m",sku="A",attributes={"screen_size_inches":27,"resolution":"4K","usb_c":True,"gaming":False})
    with pytest.raises(Conflict,match="STALE_PRODUCT_FACTS"):
        store.commit(request_id="req",grant_id=g.grant_id,now_ms=NOW+1)


def test_direct_product_facts_json_corruption_is_detected(store):
    setup_v4(store); g=activate(store,"a","A")
    c=store._connect()
    try: c.execute("UPDATE product_facts SET attributes_json='{}' WHERE merchant_id='m' AND sku='A'")
    finally: c.close()
    with pytest.raises(Conflict,match="corrupt product facts hash"):
        store.commit(request_id="req",grant_id=g.grant_id,now_ms=NOW+1)


def test_direct_intent_json_corruption_is_detected(store):
    setup_v4(store); g=activate(store,"a","A")
    c=store._connect()
    try: c.execute("UPDATE intent_specs SET body_json='{}' WHERE intent_id='I'")
    finally: c.close()
    with pytest.raises(Conflict,match="corrupt persisted intent hash"):
        store.commit(request_id="req",grant_id=g.grant_id,now_ms=NOW+1)


def test_intent_disallows_substitution_even_when_delegation_allows(store):
    setup_v4(store,substitution=False)
    activate(store,"a","A")
    store.create_quote(quote_id="Q-b",merchant_id="m",sku="B")
    with pytest.raises(Conflict,match="intent substitution not allowed"):
        store.activate_plan_from_quote(plan_id="P-b",grant_id="G-b",execution_id="E-b",reservation_id="R-b",delegation_id="D",quote_id="Q-b",now_ms=NOW,ttl_ms=50_000)


def test_replan_budget_is_consumed_atomically_and_blocks_next_replan(store):
    setup_v4(store,max_replans=1)
    activate(store,"a","A")
    activate(store,"b","B")
    assert store.scalar("SELECT replans_used FROM delegation_intents WHERE delegation_id='D'")==1
    # Same SKU would otherwise be a valid third generation.
    store.create_quote(quote_id="Q-c",merchant_id="m",sku="B")
    with pytest.raises(Conflict,match="REPLAN_BUDGET_EXHAUSTED"):
        store.activate_plan_from_quote(plan_id="P-c",grant_id="G-c",execution_id="E-c",reservation_id="R-c",delegation_id="D",quote_id="Q-c",now_ms=NOW,ttl_ms=50_000)
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-b'")==PlanState.ACTIVE.value
    assert store.scalar("SELECT replans_used FROM delegation_intents WHERE delegation_id='D'")==1


def test_replan_budget_fault_rolls_back_budget_and_plan(store):
    setup_v4(store,max_replans=1); activate(store,"a","A")
    store.create_quote(quote_id="Q-b",merchant_id="m",sku="B")
    class Boom(Exception): pass
    def hook(stage):
        if stage=="v4_after_replan_budget": raise Boom()
    with pytest.raises(Boom):
        store.activate_plan_from_quote(plan_id="P-b",grant_id="G-b",execution_id="E-b",reservation_id="R-b",delegation_id="D",quote_id="Q-b",now_ms=NOW,ttl_ms=50_000,fault_hook=hook)
    assert store.scalar("SELECT replans_used FROM delegation_intents WHERE delegation_id='D'")==0
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.ACTIVE.value
    assert store.scalar("SELECT COUNT(*) FROM plans WHERE plan_id='P-b'")==0


def test_two_concurrent_replans_with_one_budget_slot_have_one_winner(store):
    setup_v4(store,max_replans=1); activate(store,"a","A")
    # give B enough stock for both race candidates; budget, not inventory, must choose the winner
    store.change_price(merchant_id="m",sku="B",new_price_paise=3_850_000)
    store.create_quote(quote_id="Q-b1",merchant_id="m",sku="B")
    store.create_quote(quote_id="Q-b2",merchant_id="m",sku="B")
    def work(suffix,q):
        try:
            store.activate_plan_from_quote(plan_id=f"P-{suffix}",grant_id=f"G-{suffix}",execution_id=f"E-{suffix}",reservation_id=f"R-{suffix}",delegation_id="D",quote_id=q,now_ms=NOW,ttl_ms=50_000)
            return "ok"
        except Conflict as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as ex:
        results=list(ex.map(lambda x: work(*x),[("b1","Q-b1"),("b2","Q-b2")]))
    assert results.count("ok")==1
    assert sum("REPLAN_BUDGET_EXHAUSTED" in x for x in results)==1
    assert store.scalar("SELECT replans_used FROM delegation_intents WHERE delegation_id='D'")==1
    assert store.scalar("SELECT COUNT(*) FROM plans WHERE delegation_id='D' AND state='ACTIVE'")==1


def test_plan_binding_tracks_intent_facts_and_generation(store):
    setup_v4(store); g=activate(store,"a","A")
    row=store._connect().execute("SELECT * FROM grant_intent_bindings WHERE grant_id=?",(g.grant_id,)).fetchone()
    assert row["intent_id"]=="I" and row["expected_intent_version"]==1
    assert row["expected_product_facts_revision"]==1
    assert row["expected_plan_generation"]==1


def test_binding_hash_detects_direct_facts_change_even_without_revision_bump(store):
    setup_v4(store); g=activate(store,"a","A")
    body=json.dumps({"screen_size_inches":27,"resolution":"4K","usb_c":False,"gaming":False},sort_keys=True,separators=(",",":"))
    digest=hashlib.sha256(body.encode()).hexdigest()
    c=store._connect()
    try: c.execute("UPDATE product_facts SET attributes_json=?,facts_hash=? WHERE merchant_id='m' AND sku='A'",(body,digest))
    finally: c.close()
    with pytest.raises(Conflict,match="HARD_CONSTRAINT_VIOLATION|STALE_PRODUCT_FACTS"):
        store.commit(request_id="req",grant_id=g.grant_id,now_ms=NOW+1)
