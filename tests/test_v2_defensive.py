from __future__ import annotations
import pytest
from agentcommit.domain.models import *
from agentcommit.store.sqlite_store import MerchantStore, Conflict, NotFound
from conftest import NOW, seed_path
from test_v2_authority import add_products, delegated, activate

@pytest.mark.parametrize('bad',[0,-1,True,1.2])
def test_activate_bad_ttl(store,bad):
    with pytest.raises(DomainError):
        store.activate_plan_from_quote(plan_id='P',grant_id='G',execution_id='E',reservation_id='R',delegation_id='D',quote_id='Q',now_ms=NOW,ttl_ms=bad)

def test_activate_expiry_overflow(store):
    with pytest.raises(DomainError):
        store.activate_plan_from_quote(plan_id='P',grant_id='G',execution_id='E',reservation_id='R',delegation_id='D',quote_id='Q',now_ms=INT64_MAX-1,ttl_ms=2)

def test_activate_missing_delegation_and_quote(store):
    with pytest.raises(NotFound,match='delegation'):
        store.activate_plan_from_quote(plan_id='P',grant_id='G',execution_id='E',reservation_id='R',delegation_id='D',quote_id='Q',now_ms=NOW,ttl_ms=1)
    store.create_delegation(delegated())
    with pytest.raises(NotFound,match='quote'):
        store.activate_plan_from_quote(plan_id='P',grant_id='G',execution_id='E',reservation_id='R',delegation_id='D',quote_id='Q',now_ms=NOW,ttl_ms=1)

def test_activate_inactive_delegation(store):
    add_products(store); d=delegated(); store.create_delegation(d); store.revoke_delegation('D',now_ms=NOW)
    store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    with pytest.raises(Conflict,match='inactive'):
        activate(store,suffix='a',quote='QA')

def test_activate_stale_quote(store):
    add_products(store); store.create_delegation(delegated()); store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    store.change_price(merchant_id='m',sku='A',new_price_paise=3_700_000)
    with pytest.raises(Conflict,match='stale quote'): activate(store,suffix='a',quote='QA')

def test_activate_scope_mismatch(store):
    add_products(store); store.create_delegation(DelegationGrant('D','buyer','other','monitor',4_000_000,'INR',1,NOW+1000))
    store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    with pytest.raises(Conflict,match='scope'): activate(store,suffix='a',quote='QA')

def test_activate_insufficient_inventory(store):
    add_products(store,stock=0); store.create_delegation(delegated()); store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    with pytest.raises(Conflict,match='insufficient'): activate(store,suffix='a',quote='QA')

def test_cancel_fails_atomically_if_linked_grant_counter_exhausted(store):
    x=seed_path(store,stock=1)
    c=store._connect(); c.execute('UPDATE execution_grants SET version=? WHERE grant_id=?',(INT64_MAX,x['grant'])); c.close()
    with pytest.raises(Conflict,match='grant version exhausted'):
        store.cancel_reservation(x['reservation'],now_ms=NOW+1)
    assert store.scalar('SELECT state FROM reservations WHERE reservation_id=?',(x['reservation'],))==ReservationState.ACTIVE.value
    assert store.scalar('SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?',(x['merchant'],x['sku']))==0

def test_replan_fails_atomically_if_superseded_counter_exhausted(store):
    add_products(store,stock=1); store.create_delegation(delegated()); store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    c=store._connect(); c.execute("UPDATE execution_grants SET version=? WHERE grant_id='G-a'",(INT64_MAX,)); c.close()
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')
    with pytest.raises(Conflict,match='superseded path counter exhausted'): activate(store,suffix='b',quote='QB')
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.ACTIVE.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==1

def test_replan_fails_atomically_if_orphan_counter_exhausted(store):
    store.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.add_product(merchant_id='m',sku='B',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.create_delegation(DelegationGrant('D','buyer','m','monitor',100,'INR',1,NOW+1000))
    store.create_quote(quote_id='Qold',merchant_id='m',sku='A'); store.reserve(reservation_id='Rold',quote_id='Qold',now_ms=NOW,ttl_ms=100); store.issue_grant(grant_id='Gold',execution_id='Eold',delegation_id='D',reservation_id='Rold',now_ms=NOW)
    c=store._connect(); c.execute("UPDATE execution_grants SET version=? WHERE grant_id='Gold'",(INT64_MAX,)); c.close()
    store.create_quote(quote_id='Qnew',merchant_id='m',sku='B')
    with pytest.raises(Conflict,match='orphan path counter exhausted'):
        store.activate_plan_from_quote(plan_id='Pnew',grant_id='Gnew',execution_id='Enew',reservation_id='Rnew',delegation_id='D',quote_id='Qnew',now_ms=NOW,ttl_ms=100)
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='Gold'")==GrantState.ACTIVE.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='B'")==1

def test_commit_fails_atomically_if_sibling_counter_exhausted(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    # create two V1 candidate grants so commit must cleanup sibling
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); store.reserve(reservation_id='RA',quote_id='QA',now_ms=NOW,ttl_ms=100); store.issue_grant(grant_id='GA',execution_id='EA',delegation_id='D',reservation_id='RA',now_ms=NOW)
    store.create_quote(quote_id='QB',merchant_id='m',sku='B'); store.reserve(reservation_id='RB',quote_id='QB',now_ms=NOW,ttl_ms=100); store.issue_grant(grant_id='GB',execution_id='EB',delegation_id='D',reservation_id='RB',now_ms=NOW)
    c=store._connect(); c.execute("UPDATE execution_grants SET version=? WHERE grant_id='GB'",(INT64_MAX,)); c.close()
    with pytest.raises(Conflict,match='sibling path counter exhausted'):
        store.commit(request_id='req',grant_id='GA',now_ms=NOW+1)
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.ACTIVE.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='GA'")==GrantState.ACTIVE.value
    assert store.scalar("SELECT COUNT(*) FROM commit_receipts")==0


def test_tighten_missing_inactive_noop_and_invalid(store):
    with pytest.raises(NotFound): store.tighten_delegation('D',now_ms=NOW,max_amount_paise=1)
    add_products(store); store.create_delegation(delegated())
    assert store.tighten_delegation('D',now_ms=NOW+1)==1
    with pytest.raises(DomainError): store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=0)
    with pytest.raises(Conflict,match='future-valid'): store.tighten_delegation('D',now_ms=NOW+1,expires_at_ms=NOW+1)
    store.revoke_delegation('D',now_ms=NOW+2)
    with pytest.raises(Conflict,match='inactive'): store.tighten_delegation('D',now_ms=NOW+3,max_amount_paise=3_000_000)

def test_tighten_version_exhaustion(store):
    add_products(store); store.create_delegation(delegated())
    c=store._connect(); c.execute("UPDATE delegations SET version=? WHERE delegation_id='D'",(INT64_MAX,)); c.close()
    with pytest.raises(Conflict,match='version exhausted'): store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=3_900_000)

def test_expire_missing_inactive_and_future(store):
    with pytest.raises(NotFound): store.expire_delegation('D',now_ms=NOW)
    add_products(store); store.create_delegation(delegated(expiry=NOW+100))
    assert store.expire_delegation('D',now_ms=NOW+99) is False
    store.revoke_delegation('D',now_ms=NOW)
    assert store.expire_delegation('D',now_ms=NOW+100) is False

def test_broken_persistence_graph_fails_closed(store):
    x=seed_path(store)
    c=store._connect(); c.execute('PRAGMA foreign_keys=OFF'); c.execute("UPDATE execution_grants SET execution_id='missing' WHERE grant_id=?",(x['grant'],)); c.close()
    with pytest.raises(Conflict,match='broken persistence graph'):
        store.commit(request_id='req',grant_id=x['grant'],now_ms=NOW+1)

def test_activate_fails_closed_on_corrupt_persisted_exact_authority(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    c=store._connect()
    c.execute("UPDATE delegations SET mode='EXACT', exact_sku='A', exact_amount_paise=3899000, substitution_allowed=1 WHERE delegation_id='D'")
    c.close()
    with pytest.raises(DomainError):
        activate(store,suffix='a',quote='QA')
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1
    assert store.scalar("SELECT plan_generation FROM delegations WHERE delegation_id='D'")==0
    assert store.scalar("SELECT COUNT(*) FROM plans")==0


def test_one_execution_grant_per_reservation(store):
    x=seed_path(store,stock=1,delegation_id='D')
    with pytest.raises(Conflict,match='already has execution grant'):
        store.issue_grant(grant_id='g-second',execution_id='e-second',delegation_id='D',reservation_id=x['reservation'],now_ms=NOW+1)
    assert store.scalar("SELECT COUNT(*) FROM execution_grants WHERE reservation_id=?",(x['reservation'],))==1
    assert store.scalar("SELECT COUNT(*) FROM executions WHERE execution_id='e-second'")==0


@pytest.mark.parametrize('action',['tighten','expire','revoke'])
def test_authority_invalidation_releases_active_reservation_even_if_grant_terminal(store,action):
    expiry=NOW+5 if action=='expire' else NOW+1000
    x=seed_path(store,stock=1,delegation_id='D',expires_at_ms=expiry)
    # Simulate persistence drift: grant is terminal but its reservation is still ACTIVE/holding stock.
    c=store._connect(); c.execute("UPDATE execution_grants SET state=? WHERE grant_id=?",(GrantState.REVOKED.value,x['grant'])); c.close()
    if action=='tighten':
        store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=3_900_000)
        expected=ReservationState.CANCELLED.value
    elif action=='expire':
        assert store.expire_delegation('D',now_ms=NOW+5)
        expected=ReservationState.EXPIRED.value
    else:
        store.revoke_delegation('D',now_ms=NOW+1)
        expected=ReservationState.CANCELLED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id=?",(x['reservation'],))==expected
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x['merchant'],x['sku']))==1

def test_legacy_issue_grant_enforces_exact_authority_before_creating_execution(store):
    store.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.add_product(merchant_id='m',sku='B',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    d=DelegationGrant('D','buyer','m','monitor',100,'INR',1,NOW+1000,mode=AuthorizationMode.EXACT,exact_sku='A',exact_amount_paise=100,substitution_allowed=False)
    store.create_delegation(d)
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')
    store.reserve(reservation_id='RB',quote_id='QB',now_ms=NOW,ttl_ms=100)
    with pytest.raises(Conflict,match='exact authority mismatch'):
        store.issue_grant(grant_id='GB',execution_id='EB',delegation_id='D',reservation_id='RB',now_ms=NOW)
    assert store.scalar("SELECT COUNT(*) FROM executions WHERE execution_id='EB'")==0
    assert store.scalar("SELECT COUNT(*) FROM execution_grants WHERE grant_id='GB'")==0


@pytest.mark.parametrize('action',['revoke','expire'])
def test_authority_invalidation_fails_closed_on_corrupt_persisted_authority(store,action):
    expiry=NOW+5 if action=='expire' else NOW+1000
    store.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.create_delegation(DelegationGrant('D','buyer','m','monitor',100,'INR',1,expiry))
    c=store._connect(); c.execute("UPDATE delegations SET mode='EXACT',substitution_allowed=1,exact_sku='A',exact_amount_paise=100 WHERE delegation_id='D'"); c.close()
    with pytest.raises(DomainError):
        (store.expire_delegation('D',now_ms=NOW+5) if action=='expire' else store.revoke_delegation('D',now_ms=NOW+1))
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.ACTIVE.value
