from __future__ import annotations

import hashlib, hmac, json
from unittest.mock import patch
import pytest

from agentcommit.domain.models import DomainError, ExecutionState, INT64_MAX, PaymentState
from agentcommit.payments.models import (
    DispatchRecord, DispatchState, InventoryHold, InventoryHoldState, OrderIntentState,
    PaymentOrderIntent, RemoteOrder, RemotePayment,
)
from agentcommit.payments.razorpay import (
    HttpRazorpayGateway, RemoteContractError, verify_checkout_signature, verify_webhook_signature,
)
from agentcommit.payments.service import PaymentService
from agentcommit.payments.store import (
    PaymentConflict, PaymentNotFound, PaymentStore, merge_payment_state, remote_payment_state,
)
from conftest import NOW
from test_v31_payments import FakeGateway, committed, service_for, sign_webhook, webhook_body


@pytest.mark.parametrize("ctor,args", [
    (PaymentOrderIntent, ("lo","ex","bad receipt!",1,"INR",OrderIntentState.PREPARED,1,None)),
    (PaymentOrderIntent, ("lo","ex","r",1,"INR",OrderIntentState.CREATED,1,None)),
    (RemoteOrder, ("o","r",1,"INR","weird")),
    (RemotePayment, ("p","o",1,"INR","weird")),
    (DispatchRecord, ("e","r","PENDING",1,1)),
    (InventoryHold, ("e","r","m","s",1,"HELD",1,1)),
])
def test_payment_value_objects_reject_invalid_state_or_binding(ctor,args):
    with pytest.raises((DomainError, ValueError)):
        ctor(*args)


def test_payment_value_objects_accept_remote_id_only_when_state_allows():
    with pytest.raises(DomainError,match="requires remote_order_id"):
        PaymentOrderIntent("lo","e","r",1,"INR",OrderIntentState.PAID,1,None)
    with pytest.raises(DomainError,match="pre-bind"):
        PaymentOrderIntent("lo","e","r",1,"INR",OrderIntentState.PREPARED,1,"o")
    PaymentOrderIntent("lo","e","r",1,"INR",OrderIntentState.RECONCILED_FAILED,1,"o")


def test_merge_and_remote_state_reject_invalid_inputs():
    with pytest.raises(DomainError): merge_payment_state("x",PaymentState.CREATED)  # type: ignore[arg-type]
    with pytest.raises(DomainError): remote_payment_state("refunded")
    assert merge_payment_state(PaymentState.CREATED,PaymentState.OBSERVED_FAILED) is PaymentState.OBSERVED_FAILED
    assert merge_payment_state(PaymentState.CREATED,PaymentState.AUTHORIZED) is PaymentState.AUTHORIZED


def test_signature_helpers_fail_closed_on_bad_inputs():
    assert not verify_checkout_signature(key_secret="s",server_order_id="",payment_id="p",signature="x")
    assert not verify_webhook_signature(webhook_secret="s",raw_body=b"",signature="x")
    with pytest.raises(DomainError): verify_checkout_signature(key_secret="",server_order_id="o",payment_id="p",signature="x")
    with pytest.raises(DomainError): verify_webhook_signature(webhook_secret="\x01",raw_body=b"x",signature="x")


@pytest.mark.parametrize("kwargs", [
    {"key_id":"","key_secret":"s"}, {"key_id":"id","key_secret":""},
    {"key_id":"id","key_secret":"s","base_url":"http://bad"},
    {"key_id":"id","key_secret":"s","timeout_seconds":0},
])
def test_http_gateway_configuration_validation(kwargs):
    with pytest.raises(DomainError): HttpRazorpayGateway(**kwargs)


class Resp:
    def __init__(self,data:bytes): self.data=data
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def read(self): return self.data


def test_http_gateway_success_get_paths_and_contract_errors():
    g=HttpRazorpayGateway("id","secret")
    order=b'{"id":"o","receipt":"r","amount":100,"currency":"INR","status":"created"}'
    with patch("urllib.request.urlopen",return_value=Resp(order)):
        assert g.fetch_order(order_id="o").order_id=="o"
    payload=b'{"items":[{"id":"o","receipt":"r","amount":100,"currency":"INR","status":"created"}]}'
    with patch("urllib.request.urlopen",return_value=Resp(payload)):
        assert len(g.orders_by_receipt(receipt="r"))==1
    payments=b'{"items":[{"id":"p","order_id":"o","amount":100,"currency":"INR","status":"authorized"}]}'
    with patch("urllib.request.urlopen",return_value=Resp(payments)):
        assert g.payments_for_order(order_id="o")[0].payment_id=="p"
    with patch("urllib.request.urlopen",return_value=Resp(b'{"items":{}}')):
        with pytest.raises(RemoteContractError,match="orders.items"): g.orders_by_receipt(receipt="r")
    with patch("urllib.request.urlopen",return_value=Resp(b'{"items":{}}')):
        with pytest.raises(RemoteContractError,match="payments.items"): g.payments_for_order(order_id="o")
    with patch("urllib.request.urlopen",return_value=Resp(b'[]')):
        with pytest.raises(RemoteContractError,match="must be an object"): g.fetch_order(order_id="o")


def test_service_configuration_and_unbound_reconciliation(db,store):
    with pytest.raises(DomainError): PaymentService(PaymentStore(db),FakeGateway(),checkout_ttl_ms=0)
    x=committed(store,"unbound")
    ps=PaymentStore(db); intent=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    with pytest.raises(PaymentConflict,match="unbound"):
        PaymentService(ps,FakeGateway()).reconcile(local_order_id=intent.local_order_id,now_ms=NOW+3)


def test_store_getter_not_found_and_invalid_limit(db):
    from agentcommit.store.sqlite_store import MerchantStore
    MerchantStore(db)
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.intent("x")
    with pytest.raises(PaymentNotFound): ps.hold("x")
    with pytest.raises(PaymentNotFound): ps.dispatch("x")
    for bad in (0,1001,True,"1"):
        with pytest.raises(DomainError): ps.pending_dispatches(bad)  # type: ignore[arg-type]
    assert ps.intent_for_execution("x") is None


def test_prepare_missing_dispatch_receipt_and_execution_graph(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.prepare_from_dispatch("missing",now_ms=NOW)
    x=committed(store,"noreceipt")
    con=ps._connect()
    try: con.execute("DELETE FROM commit_receipts WHERE execution_id=?",(x["execution"],))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="no commit receipt"): ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)

    y=committed(store,"badexec")
    con=ps._connect()
    try: con.execute("UPDATE executions SET state=? WHERE execution_id=?",(ExecutionState.RECONCILING.value,y["execution"]))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="not claim-dispatchable"): ps.prepare_from_dispatch(y["execution"],now_ms=NOW+2)


def test_prepare_is_idempotent_when_intent_already_exists(store,db):
    x=committed(store,"idemprep"); ps=PaymentStore(db)
    a=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    b=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+3)
    assert a==b


def test_claim_remote_create_rejects_wrong_graph_states(store,db):
    x=committed(store,"claimbad"); ps=PaymentStore(db); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    con=ps._connect()
    try: con.execute("UPDATE payment_dispatch_outbox SET state=? WHERE execution_id=?",(DispatchState.CANCELLED.value,x["execution"]))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="not claimable"): ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+100)

    y=committed(store,"holdbad"); j=ps.prepare_from_dispatch(y["execution"],now_ms=NOW+2)
    con=ps._connect()
    try: con.execute("UPDATE inventory_holds SET state=? WHERE execution_id=?",(InventoryHoldState.RELEASED.value,y["execution"]))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="no longer held"): ps.claim_remote_create(j.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+100)


def _created(store,db,suffix):
    x=committed(store,suffix); gw=FakeGateway(); gw.forced_order_id=f"order-{suffix}"; svc=service_for(db,gw); svc.dispatch_pending(now_ms=NOW+2)
    return x,gw,svc,PaymentStore(db).intent_for_execution(x["execution"])


def test_bind_remote_order_idempotence_and_wrong_state(store,db):
    x,gw,svc,i=_created(store,db,"bindidem"); ps=PaymentStore(db)
    remote=gw.fetch_order(order_id=i.remote_order_id)
    assert ps.bind_remote_order(i.local_order_id,remote,now_ms=NOW+3).remote_order_id==i.remote_order_id
    con=ps._connect()
    try: con.execute("UPDATE payment_order_intents SET remote_order_id=NULL,state=? WHERE local_order_id=?",(OrderIntentState.PREPARED.value,i.local_order_id))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="cannot bind"): ps.bind_remote_order(i.local_order_id,remote,now_ms=NOW+4)


def test_mark_unknown_and_failed_idempotence_and_wrong_state(store,db):
    x=committed(store,"marku"); ps=PaymentStore(db); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2); ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+100)
    ps.mark_create_unknown(i.local_order_id,now_ms=NOW+4); ps.mark_create_unknown(i.local_order_id,now_ms=NOW+5)
    with pytest.raises(PaymentConflict,match="cannot mark create failed"): ps.mark_create_failed(i.local_order_id,now_ms=NOW+6)

    y=committed(store,"markf"); j=ps.prepare_from_dispatch(y["execution"],now_ms=NOW+2); ps.claim_remote_create(j.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+100)
    ps.mark_create_failed(j.local_order_id,now_ms=NOW+4); ps.mark_create_failed(j.local_order_id,now_ms=NOW+5)
    with pytest.raises(PaymentConflict,match="cannot mark create unknown"): ps.mark_create_unknown(j.local_order_id,now_ms=NOW+6)


def test_capture_when_hold_already_fulfilled_is_idempotent(store,db):
    x,gw,svc,i=_created(store,db,"fulfillidem"); ps=PaymentStore(db); sec="s"
    for eid in ("c1","c2"):
        body=webhook_body("payment.captured",payment_id="p",order_id=i.remote_order_id,status="captured")
        ps.accept_webhook(event_id=eid,raw_body=body,signature=sign_webhook(sec,body),webhook_secret=sec,now_ms=NOW+3)
        assert ps.process_webhook(eid,now_ms=NOW+3) is PaymentState.CAPTURED
    assert ps.hold(x["execution"]).state is InventoryHoldState.FULFILLED


def test_processed_webhook_returns_existing_state(store,db):
    x,gw,svc,i=_created(store,db,"processed"); ps=PaymentStore(db); sec="s"
    body=webhook_body("payment.authorized",payment_id="p",order_id=i.remote_order_id,status="authorized")
    ps.accept_webhook(event_id="e",raw_body=body,signature=sign_webhook(sec,body),webhook_secret=sec,now_ms=NOW+3)
    assert ps.process_webhook("e",now_ms=NOW+3) is PaymentState.AUTHORIZED
    assert ps.process_webhook("e",now_ms=NOW+4) is PaymentState.AUTHORIZED


def test_webhook_missing_unknown_order_and_binding_mismatch(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.process_webhook("missing",now_ms=NOW)
    x,gw,svc,i=_created(store,db,"unknownwh"); sec="s"
    body=webhook_body("payment.authorized",payment_id="p",order_id="order-unknown",status="authorized")
    ps.accept_webhook(event_id="u",raw_body=body,signature=sign_webhook(sec,body),webhook_secret=sec,now_ms=NOW+3)
    with pytest.raises(PaymentConflict,match="unknown order"): ps.process_webhook("u",now_ms=NOW+3)
    body2=webhook_body("payment.authorized",payment_id="p2",order_id=i.remote_order_id,status="authorized",amount=i.amount_paise+1)
    ps.accept_webhook(event_id="b",raw_body=body2,signature=sign_webhook(sec,body2),webhook_secret=sec,now_ms=NOW+4)
    with pytest.raises(PaymentConflict,match="binding mismatch"): ps.process_webhook("b",now_ms=NOW+4)


def test_checkout_unbound_and_payment_id_reuse(store,db):
    x=committed(store,"checkoutunbound"); ps=PaymentStore(db); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    with pytest.raises(PaymentConflict,match="no remote"): ps.confirm_checkout(local_order_id=i.local_order_id,payment_id="p",signature="x",key_secret="s",now_ms=NOW+3)
    con=ps._connect()
    try: con.execute("UPDATE payment_dispatch_outbox SET state=? WHERE execution_id=?",(DispatchState.CANCELLED.value,x["execution"]))
    finally: con.close()
    a,gw,svc,ia=_created(store,db,"reusea"); b,gw2,svc2,ib=_created(store,db,"reuseb")
    sec="s"
    sig=hmac.new(sec.encode(),f"{ia.remote_order_id}|samep".encode(),hashlib.sha256).hexdigest()
    ps.confirm_checkout(local_order_id=ia.local_order_id,payment_id="samep",signature=sig,key_secret=sec,now_ms=NOW+4)
    sig2=hmac.new(sec.encode(),f"{ib.remote_order_id}|samep".encode(),hashlib.sha256).hexdigest()
    with pytest.raises(PaymentConflict,match="payment id reused"): ps.confirm_checkout(local_order_id=ib.local_order_id,payment_id="samep",signature=sig2,key_secret=sec,now_ms=NOW+5)


def test_reconciliation_binding_and_existing_payment_branches(store,db):
    x,gw,svc,i=_created(store,db,"reconbranches"); ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound):
        ps.apply_reconciliation(local_order_id="missing",expected_intent_version=1,remote_order=RemoteOrder("o","r",1,"INR","created"),remote_payments=[],now_ms=NOW)
    current=ps.intent(i.local_order_id)
    wrong=RemoteOrder(i.remote_order_id,i.receipt,i.amount_paise+1,i.currency,"created")
    with pytest.raises(PaymentConflict,match="order reconciliation binding"):
        ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=current.version,remote_order=wrong,remote_payments=[],now_ms=NOW+3)
    badpay=RemotePayment("p",i.remote_order_id,i.amount_paise+1,i.currency,"authorized")
    with pytest.raises(PaymentConflict,match="payment reconciliation binding"):
        ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=current.version,remote_order=gw.fetch_order(order_id=i.remote_order_id),remote_payments=[badpay],now_ms=NOW+3)

    p=RemotePayment("p",i.remote_order_id,i.amount_paise,i.currency,"authorized")
    st=ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=current.version,remote_order=gw.fetch_order(order_id=i.remote_order_id),remote_payments=[p],now_ms=NOW+3)
    assert st is PaymentState.AUTHORIZED
    current=ps.intent(i.local_order_id)
    p2=RemotePayment("p",i.remote_order_id,i.amount_paise,i.currency,"failed")
    st=ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=current.version,remote_order=gw.fetch_order(order_id=i.remote_order_id),remote_payments=[p2],now_ms=NOW+4)
    assert st is PaymentState.UNCERTAIN


def test_checkout_close_all_fail_closed_branches(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.release_checkout_hold_after_reconcile(local_order_id="missing",expected_intent_version=1,now_ms=NOW)
    x,gw,svc,i=_created(store,db,"closeearly",); current=ps.intent(i.local_order_id)
    with pytest.raises(PaymentConflict,match="has not expired"):
        ps.release_checkout_hold_after_reconcile(local_order_id=i.local_order_id,expected_intent_version=current.version,now_ms=NOW+3)

    # Captured cannot be released.
    sec="s"; body=webhook_body("payment.captured",payment_id="p",order_id=i.remote_order_id,status="captured")
    ps.accept_webhook(event_id="c",raw_body=body,signature=sign_webhook(sec,body),webhook_secret=sec,now_ms=NOW+4); ps.process_webhook("c",now_ms=NOW+4)
    current=ps.intent(i.local_order_id)
    with pytest.raises(PaymentConflict,match="after capture"):
        ps.release_checkout_hold_after_reconcile(local_order_id=i.local_order_id,expected_intent_version=current.version,now_ms=NOW+999999)


def test_recovery_from_creating_marks_unknown_before_lookup(store,db):
    x=committed(store,"creatingrecover"); ps=PaymentStore(db); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2); ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    gw=FakeGateway(); gw.orders[i.receipt]=RemoteOrder("o",i.receipt,i.amount_paise,i.currency,"created")
    svc=PaymentService(ps,gw)
    assert svc.recover_unknown_orders(now_ms=NOW+4)==1
    assert ps.intent(i.local_order_id).state is OrderIntentState.CREATED


def test_close_expired_checkout_short_circuits_if_reconciliation_finds_capture(store,db):
    x,gw,svc,i=_created(store,db,"closecap")
    gw.add_payment(i.remote_order_id,"p","captured")
    assert svc.close_expired_checkout(local_order_id=i.local_order_id,now_ms=NOW+999999) is PaymentState.CAPTURED
    assert PaymentStore(db).hold(x["execution"]).state is InventoryHoldState.FULFILLED
