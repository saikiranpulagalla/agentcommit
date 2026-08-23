from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from agentcommit.domain.models import DelegationGrant, DelegationState, GrantState, ReservationState
from agentcommit.store.sqlite_store import MerchantStore, Conflict
from conftest import NOW, seed_path


def test_quote_stales_after_price_change(store):
    store.add_product(merchant_id="m1",sku="s1",category="monitor",currency="INR",price_paise=100,available_quantity=1)
    store.create_quote(quote_id="q1",merchant_id="m1",sku="s1")
    store.change_price(merchant_id="m1",sku="s1",new_price_paise=101)
    with pytest.raises(Conflict,match="stale quote"):
        store.reserve(reservation_id="r1",quote_id="q1",now_ms=NOW,ttl_ms=1000)


def test_inventory_only_change_does_not_stale_quote_but_reservation_checks_stock(store):
    store.add_product(merchant_id="m1",sku="s1",category="monitor",currency="INR",price_paise=100,available_quantity=1)
    store.create_quote(quote_id="q1",merchant_id="m1",sku="s1")
    store.reserve(reservation_id="r0",quote_id="q1",now_ms=NOW,ttl_ms=1000)
    with pytest.raises(Conflict,match="insufficient inventory"):
        store.reserve(reservation_id="r1",quote_id="q1",now_ms=NOW,ttl_ms=1000)


def test_commit_idempotent_same_request_and_grant(store):
    x=seed_path(store)
    a=store.commit(request_id="req-1",grant_id=x["grant"],now_ms=NOW+1)
    b=store.commit(request_id="req-1",grant_id=x["grant"],now_ms=NOW+2)
    c=store.commit(request_id="req-2",grant_id=x["grant"],now_ms=NOW+3)
    assert a==b==c
    assert store.scalar("SELECT COUNT(*) FROM commit_receipts")==1


def test_request_id_collision_different_grant(store):
    a=seed_path(store,suffix="a",sku="A",delegation_id="da")
    b=seed_path(store,suffix="b",sku="B",delegation_id="db")
    store.commit(request_id="same",grant_id=a["grant"],now_ms=NOW+1)
    with pytest.raises(Conflict,match="different grant"):
        store.commit(request_id="same",grant_id=b["grant"],now_ms=NOW+2)


def test_reservation_expiry_releases_inventory_and_revokes_grant(store):
    x=seed_path(store)
    with pytest.raises(Conflict,match="reservation expired"):
        store.commit(request_id="req",grant_id=x["grant"],now_ms=NOW+60_000)
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id=?",(x["reservation"],))==ReservationState.EXPIRED.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(x["grant"],))==GrantState.REVOKED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x["merchant"],x["sku"]))==10


def test_revoke_releases_linked_reservation_and_grant(store):
    x=seed_path(store)
    store.revoke_delegation(x["delegation"],now_ms=NOW+1)
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id=?",(x["delegation"],))==DelegationState.REVOKED.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(x["grant"],))==GrantState.REVOKED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id=?",(x["reservation"],))==ReservationState.CANCELLED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x["merchant"],x["sku"]))==10


def test_two_candidate_grants_same_delegation_one_commit_and_sibling_cleanup(store):
    # Two products, one delegated purchase authority.
    a=seed_path(store,suffix="a",sku="A",delegation_id="dshared",stock=1)
    b=seed_path(store,suffix="b",sku="B",delegation_id="dshared",stock=1)
    store.commit(request_id="winner",grant_id=a["grant"],now_ms=NOW+1)
    assert store.scalar("SELECT state FROM delegations WHERE delegation_id='dshared'")==DelegationState.CONSUMED.value
    assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(b["grant"],))==GrantState.REVOKED.value
    assert store.scalar("SELECT state FROM reservations WHERE reservation_id=?",(b["reservation"],))==ReservationState.CANCELLED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='merchant-1' AND sku='B'")==1


def test_fault_injection_rolls_back_every_stage(store):
    stages=["after_admission","after_delegation","after_grant","after_reservation","after_execution","after_sibling_cleanup","after_receipt","after_audit"]
    for i,stage in enumerate(stages):
        # independent store/db per stage via separate names in same DB
        x=seed_path(store,suffix=f"f{i}",sku=f"S{i}",delegation_id=f"D{i}",stock=1)
        class Boom(RuntimeError): pass
        def hook(s):
            if s==stage: raise Boom(stage)
        with pytest.raises(Boom): store.commit(request_id=f"req-f{i}",grant_id=x["grant"],now_ms=NOW+1,fault_hook=hook)
        assert store.scalar("SELECT state FROM delegations WHERE delegation_id=?",(x["delegation"],))==DelegationState.ACTIVE.value
        assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(x["grant"],))==GrantState.ACTIVE.value
        assert store.scalar("SELECT state FROM reservations WHERE reservation_id=?",(x["reservation"],))==ReservationState.ACTIVE.value
        assert store.scalar("SELECT COUNT(*) FROM commit_receipts WHERE grant_id=?",(x["grant"],))==0


def test_thread_race_same_grant_exactly_one_receipt(db):
    s=MerchantStore(db); x=seed_path(s)
    def run(i):
        try: return s.commit(request_id=f"req-{i}",grant_id=x["grant"],now_ms=NOW+1)
        except Conflict: return None
    with ThreadPoolExecutor(max_workers=20) as ex: results=list(ex.map(run,range(20)))
    assert sum(r is not None for r in results)==20  # idempotent grant retries return the one durable result
    assert s.scalar("SELECT COUNT(*) FROM commit_receipts")==1


def test_thread_race_last_inventory_exactly_one_reservation(db):
    s=MerchantStore(db); s.add_product(merchant_id="m",sku="s",category="monitor",currency="INR",price_paise=100,available_quantity=1)
    for i in range(20): s.create_quote(quote_id=f"q{i}",merchant_id="m",sku="s")
    def run(i):
        try: s.reserve(reservation_id=f"r{i}",quote_id=f"q{i}",now_ms=NOW,ttl_ms=1000); return True
        except Conflict: return False
    with ThreadPoolExecutor(max_workers=20) as ex: results=list(ex.map(run,range(20)))
    assert sum(results)==1
    assert s.scalar("SELECT COUNT(*) FROM reservations")==1
    assert s.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='s'")==0
