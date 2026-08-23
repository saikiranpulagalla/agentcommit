from __future__ import annotations
import pytest
from agentcommit.domain.models import DelegationGrant
from agentcommit.store.sqlite_store import MerchantStore

NOW = 1_800_000_000_000

@pytest.fixture
def db(tmp_path):
    return tmp_path / "agentcommit.db"

@pytest.fixture
def store(db):
    return MerchantStore(db)


def seed_path(store: MerchantStore, *, suffix="1", sku="MON-A", price=3899000, stock=10, delegation_id=None, buyer="buyer-1", expires_at_ms=None):
    merchant = "merchant-1"
    category = "monitor"
    if store.scalar("SELECT COUNT(*) FROM products WHERE merchant_id=? AND sku=?", (merchant, sku)) == 0:
        store.add_product(merchant_id=merchant, sku=sku, category=category, currency="INR", price_paise=price, available_quantity=stock)
    qid = f"q-{suffix}"
    rid = f"r-{suffix}"
    did = delegation_id or f"d-{suffix}"
    gid = f"g-{suffix}"
    eid = f"e-{suffix}"
    store.create_quote(quote_id=qid, merchant_id=merchant, sku=sku)
    store.reserve(reservation_id=rid, quote_id=qid, now_ms=NOW, ttl_ms=60_000)
    if store.scalar("SELECT COUNT(*) FROM delegations WHERE delegation_id=?", (did,)) == 0:
        store.create_delegation(DelegationGrant(did, buyer, merchant, category, 4_000_000, "INR", 1, NOW + 120_000 if expires_at_ms is None else expires_at_ms))
    store.issue_grant(grant_id=gid, execution_id=eid, delegation_id=did, reservation_id=rid, now_ms=NOW)
    return {"merchant":merchant,"sku":sku,"quote":qid,"reservation":rid,"delegation":did,"grant":gid,"execution":eid}
