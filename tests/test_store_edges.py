from __future__ import annotations
import sqlite3
import pytest
from agentcommit.domain.models import *
from agentcommit.store.sqlite_store import MerchantStore, Conflict, DomainError
from conftest import NOW, seed_path


def test_quote_amount_overflow(store):
    store.add_product(merchant_id="m",sku="s",category="c",currency="INR",price_paise=2**62,available_quantity=2)
    with pytest.raises(DomainError): store.create_quote(quote_id="q",merchant_id="m",sku="s",quantity=2)


def test_reservation_expiry_overflow(store):
    store.add_product(merchant_id="m",sku="s",category="c",currency="INR",price_paise=1,available_quantity=1)
    store.create_quote(quote_id="q",merchant_id="m",sku="s")
    with pytest.raises(DomainError): store.reserve(reservation_id="r",quote_id="q",now_ms=2**63-2,ttl_ms=5)


def test_direct_storage_amount_drift_fails_closed(store):
    x=seed_path(store)
    con=store._connect(); con.execute("PRAGMA ignore_check_constraints=ON"); con.execute("UPDATE reservations SET amount_paise=1 WHERE reservation_id=?",(x["reservation"],)); con.close()
    with pytest.raises(Conflict): store.commit(request_id="drift",grant_id=x["grant"],now_ms=NOW+1)


def test_direct_buyer_drift_fails_closed(store):
    x=seed_path(store)
    con=store._connect(); con.execute("UPDATE executions SET buyer_id='buyer-evil' WHERE execution_id=?",(x["execution"],)); con.close()
    with pytest.raises(Conflict): store.commit(request_id="drift",grant_id=x["grant"],now_ms=NOW+1)


def test_cancel_is_idempotent_and_never_double_releases(store):
    x=seed_path(store,stock=1)
    store.cancel_reservation(x["reservation"],now_ms=NOW+1)
    store.cancel_reservation(x["reservation"],now_ms=NOW+2)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x["merchant"],x["sku"]))==1


def test_revocation_idempotent(store):
    x=seed_path(store,stock=1)
    store.revoke_delegation(x["delegation"],now_ms=NOW+1)
    store.revoke_delegation(x["delegation"],now_ms=NOW+2)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x["merchant"],x["sku"]))==1


def test_commit_then_revoke_does_not_release_consumed_inventory(store):
    x=seed_path(store,stock=1)
    store.commit(request_id="req",grant_id=x["grant"],now_ms=NOW+1)
    store.revoke_delegation(x["delegation"],now_ms=NOW+2)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id=? AND sku=?",(x["merchant"],x["sku"]))==0


def test_same_delegation_two_grants_race_only_one_financial_winner(store):
    a=seed_path(store,suffix="aa",sku="AA",stock=1,delegation_id="D")
    b=seed_path(store,suffix="bb",sku="BB",stock=1,delegation_id="D")
    import concurrent.futures
    def f(pair):
        req,g=pair
        try: return store.commit(request_id=req,grant_id=g,now_ms=NOW+1).grant_id
        except Conflict: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        out=list(ex.map(f,[("ra",a["grant"]),("rb",b["grant"])]))
    assert sum(v is not None for v in out)==1
    # Correct semantic evidence is one receipt/delegation consumption despite client-level outcomes.
    assert store.scalar("SELECT COUNT(*) FROM commit_receipts")==1
