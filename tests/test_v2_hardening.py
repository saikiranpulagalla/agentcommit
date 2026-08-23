from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from agentcommit.domain.models import *
from agentcommit.domain.policy import evaluate_commit
from agentcommit.store.sqlite_store import MerchantStore, Conflict, DomainError
from conftest import NOW, seed_path
from test_v2_authority import add_products, delegated, activate


def test_cancel_reservation_cancels_plan_row(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    store.cancel_reservation('R-a',now_ms=NOW+1)
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.CANCELLED.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='G-a'")==GrantState.REVOKED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1


def test_revoke_delegation_cancels_plan_row(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    store.revoke_delegation('D',now_ms=NOW+1)
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P-a'")==PlanState.CANCELLED.value


def test_replan_materializes_expired_old_hold_before_new_plan(store):
    store.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.create_delegation(DelegationGrant('D','buyer','m','monitor',100,'INR',1,NOW+100_000,substitution_allowed=False))
    store.create_quote(quote_id='Q1',merchant_id='m',sku='A')
    store.activate_plan_from_quote(plan_id='P1',grant_id='G1',execution_id='E1',reservation_id='R1',delegation_id='D',quote_id='Q1',now_ms=NOW,ttl_ms=5)
    store.create_quote(quote_id='Q2',merchant_id='m',sku='A')
    g2=store.activate_plan_from_quote(plan_id='P2',grant_id='G2',execution_id='E2',reservation_id='R2',delegation_id='D',quote_id='Q2',now_ms=NOW+5,ttl_ms=100)
    assert g2.expected_plan_generation==2
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R1'")==ReservationState.EXPIRED.value
    assert store.scalar("SELECT state FROM plans WHERE plan_id='P1'")==PlanState.CANCELLED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R2'")==ReservationState.ACTIVE.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0


def test_activate_v2_invalidates_legacy_orphan_path_and_releases_hold(store):
    # Create V1-style path under same delegation first.
    store.add_product(merchant_id='m',sku='A',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    store.add_product(merchant_id='m',sku='B',category='monitor',currency='INR',price_paise=100,available_quantity=1)
    d=DelegationGrant('D','buyer','m','monitor',100,'INR',1,NOW+100_000)
    store.create_delegation(d)
    store.create_quote(quote_id='Qold',merchant_id='m',sku='A'); store.reserve(reservation_id='Rold',quote_id='Qold',now_ms=NOW,ttl_ms=1000)
    store.issue_grant(grant_id='Gold',execution_id='Eold',delegation_id='D',reservation_id='Rold',now_ms=NOW)
    store.create_quote(quote_id='Qnew',merchant_id='m',sku='B'); activate(store,suffix='new',quote='Qnew')
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='Gold'")==GrantState.REVOKED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='Rold'")==ReservationState.CANCELLED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1


def test_tighten_cleans_legacy_v1_path(store):
    x=seed_path(store,stock=1,delegation_id='D')
    store.tighten_delegation('D',now_ms=NOW+1,max_amount_paise=3_900_000)
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(x['grant'],))==GrantState.REVOKED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id=?",(x['reservation'],))==ReservationState.CANCELLED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x['merchant'],x['sku']))==1


def test_expire_cleans_legacy_v1_path(store):
    # manual V1-style delegation with short expiry
    store.add_product(merchant_id='merchant-1',sku='MON-A',category='monitor',currency='INR',price_paise=3_899_000,available_quantity=1)
    store.create_quote(quote_id='q',merchant_id='merchant-1',sku='MON-A'); store.reserve(reservation_id='r',quote_id='q',now_ms=NOW,ttl_ms=1000)
    store.create_delegation(DelegationGrant('D','buyer-1','merchant-1','monitor',4_000_000,'INR',1,NOW+5))
    store.issue_grant(grant_id='g',execution_id='e',delegation_id='D',reservation_id='r',now_ms=NOW)
    assert store.expire_delegation('D',now_ms=NOW+5)
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id='g'")==GrantState.EXPIRED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='r'")==ReservationState.EXPIRED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='merchant-1' AND sku='MON-A'")==1


def test_expire_counter_exhaustion_rolls_back(store):
    add_products(store,stock=1); store.create_delegation(delegated(expiry=NOW+5))
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    c=store._connect(); c.execute("UPDATE execution_grants SET version=? WHERE grant_id='G-a'",(INT64_MAX,)); c.close()
    with pytest.raises(Conflict,match='counter exhausted'):
        store.expire_delegation('D',now_ms=NOW+5)
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.ACTIVE.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id='R-a'")==ReservationState.ACTIVE.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==0


def test_expire_delegation_version_exhaustion_rolls_back(store):
    add_products(store,stock=1); store.create_delegation(delegated(expiry=NOW+5))
    c=store._connect(); c.execute("UPDATE delegations SET version=? WHERE delegation_id='D'",(INT64_MAX,)); c.close()
    with pytest.raises(Conflict,match='version exhausted'): store.expire_delegation('D',now_ms=NOW+5)
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.ACTIVE.value


@pytest.mark.parametrize('bad',['', 'bad space', 'é', 'x\n'])
def test_activate_rejects_bad_plan_identifiers_before_state_change(store,bad):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    with pytest.raises(DomainError):
        store.activate_plan_from_quote(plan_id=bad,grant_id='G',execution_id='E',reservation_id='R',delegation_id='D',quote_id='QA',now_ms=NOW,ttl_ms=10)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1
    assert store.scalar("SELECT plan_generation FROM delegations WHERE delegation_id='D'")==0


def test_plan_generation_exhaustion_is_fail_closed(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    c=store._connect(); c.execute("UPDATE delegations SET plan_generation=? WHERE delegation_id='D'",(INT64_MAX,)); c.close()
    store.create_quote(quote_id='QA',merchant_id='m',sku='A')
    with pytest.raises(Conflict,match='generation exhausted'): activate(store,suffix='a',quote='QA')
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='A'")==1


def test_model_rejects_invalid_authorization_mode_shapes():
    with pytest.raises(DomainError):
        DelegationGrant('d','b','m','c',10,'INR',1,NOW+1,mode='EXACT')
    with pytest.raises(DomainError):
        DelegationGrant('d','b','m','c',10,'INR',1,NOW+1,mode=AuthorizationMode.EXACT,substitution_allowed=False)
    with pytest.raises(DomainError):
        DelegationGrant('d','b','m','c',10,'INR',1,NOW+1,mode=AuthorizationMode.EXACT,exact_sku='s',exact_amount_paise=1,substitution_allowed=True)
    with pytest.raises(DomainError):
        DelegationGrant('d','b','m','c',10,'INR',1,NOW+1,exact_sku='s')
    with pytest.raises(DomainError):
        replace(delegated(),substitution_allowed=1)


def test_policy_denies_plan_generation_mismatch():
    from test_policy import valid_snapshot
    s=valid_snapshot()
    s=replace(s,delegation=replace(s.delegation,plan_generation=2),grant=replace(s.grant,expected_plan_generation=1))
    assert evaluate_commit(s,now_ms=NOW).code is DecisionCode.DELEGATION_VERSION_MISMATCH


def test_policy_denies_exact_binding_mismatch():
    from test_policy import valid_snapshot
    s=valid_snapshot()
    d=replace(s.delegation,mode=AuthorizationMode.EXACT,exact_sku='other',exact_amount_paise=s.quote.amount_paise,substitution_allowed=False)
    s=replace(s,delegation=d)
    assert evaluate_commit(s,now_ms=NOW).code is DecisionCode.RESOURCE_MISMATCH


def _setup_race(store):
    add_products(store,stock=1); store.create_delegation(delegated())
    store.create_quote(quote_id='QA',merchant_id='m',sku='A'); activate(store,suffix='a',quote='QA')
    store.create_quote(quote_id='QB',merchant_id='m',sku='B')

@pytest.mark.parametrize('rounds',[12])
def test_replan_vs_old_commit_repeated_linearization(tmp_path,rounds):
    for i in range(rounds):
        s=MerchantStore(tmp_path/f'r{i}.db'); _setup_race(s)
        def old_commit():
            try: s.commit(request_id='req-old',grant_id='G-a',now_ms=NOW+1); return 'old-commit'
            except Exception: return 'old-denied'
        def replan():
            try: activate(s,suffix='b',quote='QB'); return 'replan'
            except Exception: return 'replan-denied'
        with ThreadPoolExecutor(max_workers=2) as ex: out=list(ex.map(lambda f:f(),[old_commit,replan]))
        # Exactly one operation owns the linearization point. If old commit wins, replan must lose;
        # if replan wins, old grant must be stale/revoked and produce no receipt.
        assert ('old-commit' in out) ^ ('replan' in out)
        assert s.scalar("SELECT COUNT(*) FROM commit_receipts") in (0,1)
        if s.scalar("SELECT COUNT(*) FROM commit_receipts")==1:
            assert s.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.CONSUMED.value
        else:
            assert s.scalar("SELECT plan_generation FROM delegations WHERE delegation_id='D'")==2

@pytest.mark.parametrize('rounds',[12])
def test_replan_vs_revoke_repeated_linearization(tmp_path,rounds):
    for i in range(rounds):
        s=MerchantStore(tmp_path/f'v{i}.db'); _setup_race(s)
        def revoke():
            try: s.revoke_delegation('D',now_ms=NOW+1); return 'revoke'
            except Exception: return 'revoke-denied'
        def replan():
            try: activate(s,suffix='b',quote='QB'); return 'replan'
            except Exception: return 'replan-denied'
        with ThreadPoolExecutor(max_workers=2) as ex: out=list(ex.map(lambda f:f(),[revoke,replan]))
        # Replan may linearize first, but revoke then still validly revokes it; final authority must be revoked.
        assert 'revoke' in out
        assert s.scalar("SELECT state FROM delegations WHERE delegation_id='D'")==DelegationState.REVOKED.value
        assert s.scalar("SELECT COUNT(*) FROM commit_receipts")==0
        assert s.scalar("SELECT COUNT(*) FROM plans WHERE delegation_id='D' AND state='ACTIVE'")==0
