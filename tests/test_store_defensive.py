import sqlite3, pytest
from agentcommit.domain.models import *
from agentcommit.store.sqlite_store import MerchantStore, Conflict, NotFound
from conftest import NOW, seed_path

@pytest.mark.parametrize('bad',[0,-1,True,1.2,2**63])
def test_invalid_now(store,bad):
    with pytest.raises(DomainError): store.cancel_reservation('x',now_ms=bad)

@pytest.mark.parametrize('bad',[-1,True,1.5,2**63])
def test_invalid_inventory(store,bad):
    with pytest.raises(DomainError): store.add_product(merchant_id='m',sku='s',category='c',currency='INR',price_paise=1,available_quantity=bad)

def test_product_missing(store):
    with pytest.raises(NotFound): store.product('m','s')

def test_change_price_validation_and_missing(store):
    with pytest.raises(DomainError): store.change_price(merchant_id='m',sku='s',new_price_paise=0)
    with pytest.raises(NotFound): store.change_price(merchant_id='m',sku='s',new_price_paise=1)

def test_change_price_revision_exhausted(store):
    store.add_product(merchant_id='m',sku='s',category='c',currency='INR',price_paise=1,available_quantity=1)
    c=store._connect(); c.execute('UPDATE products SET price_revision=?',(INT64_MAX,)); c.close()
    with pytest.raises(Conflict): store.change_price(merchant_id='m',sku='s',new_price_paise=2)

@pytest.mark.parametrize('bad',[0,-1,True,1.5,2**63])
def test_quote_bad_quantity(store,bad):
    with pytest.raises(DomainError): store.create_quote(quote_id='q',merchant_id='m',sku='s',quantity=bad)

def test_quote_missing_product(store):
    with pytest.raises(NotFound): store.create_quote(quote_id='q',merchant_id='m',sku='s')

@pytest.mark.parametrize('bad',[0,-1,True,1.5])
def test_bad_ttl(store,bad):
    with pytest.raises(DomainError): store.reserve(reservation_id='r',quote_id='q',now_ms=NOW,ttl_ms=bad)

def test_reserve_missing_quote(store):
    with pytest.raises(NotFound): store.reserve(reservation_id='r',quote_id='q',now_ms=NOW,ttl_ms=1)

def test_cancel_missing_reservation(store):
    with pytest.raises(NotFound): store.cancel_reservation('missing',now_ms=NOW)

def test_cancel_terminal_noop(store):
    x=seed_path(store,stock=1); store.cancel_reservation(x['reservation'],now_ms=NOW+1); store.cancel_reservation(x['reservation'],now_ms=NOW+2)

def test_reservation_revision_exhaustion(store):
    x=seed_path(store)
    c=store._connect(); c.execute('UPDATE reservations SET revision=? WHERE reservation_id=?',(INT64_MAX,x['reservation'])); c.close()
    with pytest.raises(Conflict): store.cancel_reservation(x['reservation'],now_ms=NOW+1)

def test_issue_missing_delegation_and_reservation(store):
    with pytest.raises(NotFound): store.issue_grant(grant_id='g',execution_id='e',delegation_id='d',reservation_id='r',now_ms=NOW)
    store.create_delegation(DelegationGrant('d','b','m','c',10,'INR',1,NOW+100))
    with pytest.raises(NotFound): store.issue_grant(grant_id='g',execution_id='e',delegation_id='d',reservation_id='r',now_ms=NOW)

def test_issue_expired_delegation(store):
    store.add_product(merchant_id='m',sku='s',category='c',currency='INR',price_paise=1,available_quantity=1)
    store.create_quote(quote_id='q',merchant_id='m',sku='s'); store.reserve(reservation_id='r',quote_id='q',now_ms=NOW,ttl_ms=100)
    store.create_delegation(DelegationGrant('d','b','m','c',10,'INR',1,NOW))
    with pytest.raises(Conflict): store.issue_grant(grant_id='g',execution_id='e',delegation_id='d',reservation_id='r',now_ms=NOW)

def test_issue_expired_reservation_releases(store):
    store.add_product(merchant_id='m',sku='s',category='c',currency='INR',price_paise=1,available_quantity=1)
    store.create_quote(quote_id='q',merchant_id='m',sku='s'); store.reserve(reservation_id='r',quote_id='q',now_ms=NOW,ttl_ms=1)
    store.create_delegation(DelegationGrant('d','b','m','c',10,'INR',1,NOW+100))
    with pytest.raises(Conflict): store.issue_grant(grant_id='g',execution_id='e',delegation_id='d',reservation_id='r',now_ms=NOW+1)
    assert store.scalar("SELECT available_quantity FROM products WHERE merchant_id='m' AND sku='s'")==1

def test_issue_scope_and_budget_mismatch(store):
    store.add_product(merchant_id='m',sku='s',category='c',currency='INR',price_paise=100,available_quantity=2)
    store.create_quote(quote_id='q',merchant_id='m',sku='s'); store.reserve(reservation_id='r',quote_id='q',now_ms=NOW,ttl_ms=100)
    store.create_delegation(DelegationGrant('d','b','m','other',100,'INR',1,NOW+100))
    with pytest.raises(Conflict): store.issue_grant(grant_id='g',execution_id='e',delegation_id='d',reservation_id='r',now_ms=NOW)
    store.create_delegation(DelegationGrant('d2','b','m','c',99,'INR',1,NOW+100))
    with pytest.raises(Conflict): store.issue_grant(grant_id='g2',execution_id='e2',delegation_id='d2',reservation_id='r',now_ms=NOW)

def test_revoke_missing_and_already_terminal(store):
    with pytest.raises(NotFound): store.revoke_delegation('missing',now_ms=NOW)
    x=seed_path(store); store.revoke_delegation(x['delegation'],now_ms=NOW+1); store.revoke_delegation(x['delegation'],now_ms=NOW+2)

def test_revoke_version_exhausted(store):
    x=seed_path(store); c=store._connect(); c.execute('UPDATE delegations SET version=? WHERE delegation_id=?',(INT64_MAX,x['delegation'])); c.close()
    with pytest.raises(Conflict): store.revoke_delegation(x['delegation'],now_ms=NOW+1)

def test_snapshot_missing_grant(store):
    with pytest.raises(NotFound): store.snapshot('missing')

def test_commit_missing_grant(store):
    with pytest.raises(NotFound): store.commit(request_id='r',grant_id='missing',now_ms=NOW)

def test_request_collision_existing(store):
    a=seed_path(store,suffix='a',sku='a',delegation_id='da'); b=seed_path(store,suffix='b',sku='b',delegation_id='db')
    store.commit(request_id='req',grant_id=a['grant'],now_ms=NOW+1)
    with pytest.raises(Conflict): store.commit(request_id='req',grant_id=b['grant'],now_ms=NOW+2)
