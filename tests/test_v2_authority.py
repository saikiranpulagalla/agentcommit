from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import pytest
from agentcommit.domain.models import *
from agentcommit.store.sqlite_store import MerchantStore, Conflict
from conftest import NOW


def add_products(s: MerchantStore, stock=2):
    s.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=3_800_000,available_quantity=stock)
    s.add_product(merchant_id='m',sku='B',category='monitor',currency='INR',price_paise=3_900_000,available_quantity=stock)


def delegated(*, substitution=True, max_amount=4_000_000, expiry=NOW+100_000):
    return DelegationGrant('D','buyer','m','monitor',max_amount,'INR',1,expiry,
                           substitution_allowed=substitution)


def exact():
    return DelegationGrant('D','buyer','m','monitor',4_000_000,'INR',1,NOW+100_000,
                           mode=AuthorizationMode.EXACT,exact_sku='A',exact_amount_paise=3_800_000,
                           substitution_allowed=False)


def activate(s: MerchantStore, *, suffix, quote, delegation='D'):
    return s.activate_plan_from_quote(plan_id=f'P-{suffix}',grant_id=f'G-{suffix}',execution_id=f'E-{suffix}',
                                      reservation_id=f'R-{suffix}',delegation_id=delegation,quote_id=quote,
                                      now_ms=NOW,ttl_ms=50_000)


def test_exact_authority_accepts_only_exact_sku_and_amount(store):
    add_products(store); store.create_delegation(exact())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    g=activate(store,suffix='a',quote='QA'); assert g.expected_sku=='A'
    # New independent exact delegation cannot authorize B.
    store.create_delegation(replace(exact(),delegation_id='D2'))
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')
    with pytest.raises(Conflict,match='exact authority mismatch'):
        activate(store,suffix='b',quote='QB',delegation='D2')


def test_exact_authority_rejects_repriced_same_sku(store):
    add_products(store); store.create_delegation(exact())
    store.change_price(merchant_id='m',sku='A',new_price_paise=3_700_000)
    store.create_quote(quote_id='Q2',merchant_id='m',sku='A')
    with pytest.raises(Conflict,match='exact authority mismatch'):
        activate(store,suffix='x',quote='Q2')


def test_delegated_replan_supersedes_old_and_releases_old_inventory(store):
    add_products(store,stock=1); store.create_delegation(delegated(substitution=True))
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0
    store.create_quote(quote_id='QB',merchant_id='m',sku='B'); g2=activate(store,suffix='b',quote='QB')
    assert g2.expected_plan_generation==2
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.SUPERSEDED.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='G-a'")==GrantState.REVOKED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R-a'")==ReservationState.CANCELLED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==0
    with pytest.raises(Conflict): store.commit(request_id='old',grant_id='G-a',now_ms=NOW+1)
    store.commit(request_id='new',grant_id='G-b',now_ms=NOW+1)
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-b'")==PlanState.COMMITTED.value


def test_same_sku_replan_transfers_hold_without_second_unit(store):
    store.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.create_delegation(DelegationGrant('D','buyer','m','monitor',100,'INR',1,NOW+100_000,substitution_allowed=False))
    store.create_quote(quote_id='Q1',merchant_id='m',sku='A'); activate(store,suffix='1',quote='Q1')
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0
    store.create_quote(quote_id='Q2',merchant_id='m',sku='A'); activate(store,suffix='2',quote='Q2')
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R-1'")==ReservationState.CANCELLED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R-2'")==ReservationState.ACTIVE.value


def test_substitution_disallowed_rejects_other_sku_without_holding_it(store):
    add_products(store,stock=1); store.create_delegation(delegated(substitution=False))
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')
    with pytest.raises(Conflict,match='substitution not allowed'):
        activate(store,suffix='b',quote='QB')
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==1
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.ACTIVE.value

@pytest.mark.parametrize('stage',['v2_after_new_reservation','v2_after_old_superseded','v2_after_generation','v2_after_plan'])
def test_replan_fault_is_fully_atomic(store,stage):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')
    class Boom(Exception): pass
    def hook(s):
        if s==stage: raise Boom(stage)
    with pytest.raises(Boom):
        store.activate_plan_from_quote(plan_id='P-b',grant_id='G-b',execution_id='E-b',reservation_id='R-b',
                                       delegation_id='D',quote_id='QB',now_ms=NOW,ttl_ms=50_000,fault_hook=hook)
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.ACTIVE.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='G-a'")==GrantState.ACTIVE.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==1
    assert store.scalar("SELECT COUNT(*) FROM plans WHERE plan_id='P-b'")==0
    assert store.scalar("SELECT plan_generation FROM delegations WHERE delegation_id='D'")==1


def test_tightening_invalidates_old_plan_and_requires_new_generation(store):
    add_products(store,stock=1); store.create_delegation(delegated(max_amount=4_000_000))
    store.create_quote(quote_id='QB',merchant_id='m',sku='B'); activate(store,suffix='b',quote='QB')
    v=store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=3_850_000)
    assert v==2
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='G-b'")==GrantState.REVOKED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==1
    with pytest.raises(Conflict): store.commit(request_id='old',grant_id='G-b',now_ms=NOW+2)
    # B is now over budget, A is allowed.
    store.create_quote(quote_id='QB2',merchant_id='m',sku='B')
    with pytest.raises(Conflict,match='budget'):
        activate(store,suffix='b2',quote='QB2')
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); g=activate(store,suffix='a',quote='QA')
    assert g.expected_delegation_version==2 and g.expected_plan_generation==2

@pytest.mark.parametrize('kwargs',[
    {'max_amount_paise':4_000_001}, {'max_quantity':2}, {'expires_at_ms':NOW+100_001}
])
def test_authority_broadening_rejected_without_mutation(store,kwargs):
    add_products(store); store.create_delegation(delegated())
    before=(store.scalar("SELECT version FROM delegations WHERE delegation_id='D'"),store.scalar("SELECT max_amount_paise FROM delegations WHERE delegation_id='D'"))
    with pytest.raises(Conflict,match='broadening'):
        store.tighten_delegation('D',now_ms=NOW+1,**kwargs)
    after=(store.scalar("SELECT version FROM delegations WHERE delegation_id='D'"),store.scalar("SELECT max_amount_paise FROM delegations WHERE delegation_id='D'"))
    assert before==after

@pytest.mark.parametrize('stage',['v2_after_tighten_cleanup','v2_after_tighten_update'])
def test_tighten_fault_rolls_back_authority_and_plan(store,stage):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    class Boom(Exception): pass
    def hook(s):
        if s==stage: raise Boom(stage)
    with pytest.raises(Boom): store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=3_900_000,fault_hook=hook)
    assert store.scalar("SELECT version FROM delegations WHERE delegation_id='D'")==1
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.ACTIVE.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R-a'")==ReservationState.ACTIVE.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0


def test_expiry_exact_boundary_materializes_and_releases(store):
    add_products(store,stock=1); store.create_delegation(delegated(expiry=NOW+10))
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    assert store.expire_delegation('D',now_ms=NOW+9) is False
    assert store.expire_delegation('D',now_ms=NOW+10) is True
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.EXPIRED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1


def test_tighten_vs_commit_linearizes_safely(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    def commit():
        try: store.commit(request_id='race',grant_id='G-a',now_ms=NOW+1); return 'commit'
        except Conflict: return 'commit-lost'
    def tighten():
        try: store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=3_900_000); return 'tighten'
        except Conflict: return 'tighten-lost'
    with ThreadPoolExecutor(max_workers=2) as ex: out=list(ex.map(lambda f:f(),[commit,tighten]))
    assert ('commit' in out) ^ ('tighten' in out)
    assert store.scalar("SELECT COUNT(*) FROM commit_receipts") in (0,1)
    if store.scalar("SELECT COUNT(*) FROM commit_receipts")==1:
        assert store.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.CONSUMED.value
    else:
        assert store.scalar("SELECT version FROM delegations WHERE delegation_id='D'")==2


def test_plan_generation_rollback_attempt_fails_old_grant(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    store.create_quote(quote_id='QB',merchant_id='m',sku='B'); activate(store,suffix='b',quote='QB')
    # Even if persistence is maliciously rolled back, the old G-a is already REVOKED; policy remains fail closed.
    c=store._connect(); c.execute("UPDATE delegations SET plan_generation=1 WHERE delegation_id='D'"); c.close()
    with pytest.raises(Conflict): store.commit(request_id='old',grant_id='G-a',now_ms=NOW+1)


def test_multiple_active_plan_corruption_is_rejected(store):
    add_products(store,stock=2); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    # Forge a second ACTIVE plan row pointing at a separate V1-style grant.
    store.create_quote(quote_id='QB0',merchant_id='m',sku='B')
    store.reserve(reservation_id='RX',quote_id='QB0',now_ms=NOW,ttl_ms=1000)
    store.issue_grant(grant_id='GX',execution_id='EX',delegation_id='D',reservation_id='RX',now_ms=NOW)
    c=store._connect(); c.execute("INSERT INTO plans(plan_id,delegation_id,generation,quote_id,reservation_id,grant_id,sku,amount_paise,state,prior_plan_id,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ('PX','D',99,'QB0','RX','GX','B',3_900_000,PlanState.ACTIVE.value,None,NOW)); c.close()
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')
    with pytest.raises(Conflict,match='multiple active plans'):
        activate(store,suffix='b',quote='QB')
