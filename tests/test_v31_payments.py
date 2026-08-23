from __future__ import annotations

import hashlib
import hmac
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentcommit.domain.models import ExecutionState, PaymentState, INT64_MAX
from agentcommit.payments.models import (
    DispatchState, InventoryHoldState, OrderIntentState, RemoteOrder, RemotePayment,
)
from agentcommit.payments.razorpay import (
    AmbiguousRemoteOutcome, DefiniteRemoteRejection, deterministic_receipt,
)
from agentcommit.payments.service import PaymentService
from agentcommit.payments.store import PaymentConflict, PaymentStore, merge_payment_state
from agentcommit.store.sqlite_store import MerchantStore
from conftest import NOW, seed_path


class FakeGateway:
    def __init__(self):
        self.orders: dict[str, RemoteOrder] = {}
        self.payments: dict[str, list[RemotePayment]] = {}
        self.create_calls = 0
        self.mode = "success"
        self.lock = threading.Lock()
        self.forced_order_id: str | None = None

    def create_order(self, *, amount_paise: int, currency: str, receipt: str) -> RemoteOrder:
        with self.lock:
            self.create_calls += 1
            order = RemoteOrder(self.forced_order_id or f"order-{self.create_calls}", receipt, amount_paise, currency, "created")
            if self.mode == "definite":
                raise DefiniteRemoteRejection("4xx")
            if self.mode == "ambiguous_no_create":
                raise AmbiguousRemoteOutcome("timeout")
            self.orders[receipt] = order
            self.payments.setdefault(order.order_id, [])
            if self.mode == "ambiguous_after_create":
                raise AmbiguousRemoteOutcome("response lost")
            return order

    def orders_by_receipt(self, *, receipt: str) -> list[RemoteOrder]:
        order = self.orders.get(receipt)
        return [] if order is None else [order]

    def fetch_order(self, *, order_id: str) -> RemoteOrder:
        for o in self.orders.values():
            if o.order_id == order_id:
                ps = self.payments.get(order_id, [])
                if any(p.status == "captured" for p in ps):
                    return RemoteOrder(o.order_id, o.receipt, o.amount_paise, o.currency, "paid")
                if ps:
                    return RemoteOrder(o.order_id, o.receipt, o.amount_paise, o.currency, "attempted")
                return o
        raise KeyError(order_id)

    def payments_for_order(self, *, order_id: str) -> list[RemotePayment]:
        return list(self.payments.get(order_id, []))

    def add_payment(self, order_id: str, payment_id: str, status: str, amount: int = 3_899_000, currency: str = "INR"):
        self.payments.setdefault(order_id, []).append(RemotePayment(payment_id, order_id, amount, currency, status))


def committed(store: MerchantStore, suffix="pay"):
    x = seed_path(store, suffix=suffix, sku=f"SKU-{suffix}", delegation_id=f"D-{suffix}", stock=1)
    store.commit(request_id=f"REQ-{suffix}", grant_id=x["grant"], now_ms=NOW + 1)
    return x


def service_for(db, gateway=None, ttl=600_000):
    return PaymentService(PaymentStore(db), gateway or FakeGateway(), checkout_ttl_ms=ttl)


def sign_webhook(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook_body(event: str, *, payment_id: str, order_id: str, status: str, amount=3_899_000):
    return json.dumps({
        "event": event,
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "amount": amount, "currency": "INR", "status": status,
        }}},
    }, separators=(",", ":")).encode()


def checkout_sig(secret: str, order_id: str, payment_id: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def test_commit_atomically_creates_dispatch_and_inventory_hold(store, db):
    x = committed(store, "atomic")
    ps = PaymentStore(db)
    d = ps.dispatch(x["execution"])
    h = ps.hold(x["execution"])
    assert d.state is DispatchState.PENDING
    assert d.receipt == deterministic_receipt(x["execution"])
    assert h.state is InventoryHoldState.HELD
    assert h.reservation_id == x["reservation"]
    assert store.scalar("SELECT available_quantity FROM products WHERE sku=?", (x["sku"],)) == 0


@pytest.mark.parametrize("stage", ["after_payment_outbox", "after_inventory_hold"])
def test_commit_fault_after_new_v31_stages_rolls_back_everything(store, stage):
    x = seed_path(store, suffix=stage[-4:], sku=f"S-{stage[-4:]}", delegation_id=f"D-{stage[-4:]}", stock=1)
    class Boom(RuntimeError): pass
    def hook(s):
        if s == stage: raise Boom(stage)
    with pytest.raises(Boom):
        store.commit(request_id=f"r-{stage[-4:]}", grant_id=x["grant"], now_ms=NOW + 1, fault_hook=hook)
    assert store.scalar("SELECT COUNT(*) FROM commit_receipts WHERE grant_id=?", (x["grant"],)) == 0
    assert store.scalar("SELECT COUNT(*) FROM payment_dispatch_outbox WHERE execution_id=?", (x["execution"],)) == 0
    assert store.scalar("SELECT COUNT(*) FROM inventory_holds WHERE execution_id=?", (x["execution"],)) == 0
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?", (x["execution"],)) == ExecutionState.PLANNED.value


def test_crash_after_commit_is_resumed_from_durable_outbox(store, db):
    x = committed(store, "resume")
    gw = FakeGateway(); svc = service_for(db, gw)
    assert PaymentStore(db).intent_for_execution(x["execution"]) is None
    assert svc.dispatch_pending(now_ms=NOW + 2) == ["CREATED"]
    intent = PaymentStore(db).intent_for_execution(x["execution"])
    assert intent.state is OrderIntentState.CREATED
    assert gw.create_calls == 1


def test_dispatch_success_extends_hold_before_remote_path(store, db):
    x = committed(store, "extend")
    gw = FakeGateway(); svc = service_for(db, gw, ttl=123_456)
    svc.dispatch_pending(now_ms=NOW + 2)
    h = PaymentStore(db).hold(x["execution"])
    assert h.hold_until_ms == NOW + 2 + 123_456
    assert h.state is InventoryHoldState.HELD
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?", (x["execution"],)) == ExecutionState.EXECUTING.value


def test_concurrent_dispatchers_issue_one_remote_create(store, db):
    committed(store, "race")
    gw = FakeGateway(); svc = service_for(db, gw)
    def run(_):
        try: return svc.dispatch_pending(now_ms=NOW + 2)
        except PaymentConflict: return []
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(run, range(12)))
    assert gw.create_calls == 1
    assert PaymentStore(db).scalar("SELECT COUNT(*) FROM payment_order_intents") == 1


def test_definite_remote_rejection_releases_inventory(store, db):
    x = committed(store, "reject")
    gw = FakeGateway(); gw.mode = "definite"; svc = service_for(db, gw)
    assert svc.dispatch_pending(now_ms=NOW + 2) == ["CREATE_FAILED"]
    ps = PaymentStore(db)
    assert ps.hold(x["execution"]).state is InventoryHoldState.RELEASED
    assert store.scalar("SELECT available_quantity FROM products WHERE sku=?", (x["sku"],)) == 1
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?", (x["execution"],)) == ExecutionState.RECONCILED_FAILED.value


def test_ambiguous_create_after_remote_success_recovers_by_receipt_without_second_post(store, db):
    x = committed(store, "amb")
    gw = FakeGateway(); gw.mode = "ambiguous_after_create"; svc = service_for(db, gw)
    assert svc.dispatch_pending(now_ms=NOW + 2) == ["CREATE_UNKNOWN"]
    intent = PaymentStore(db).intent_for_execution(x["execution"])
    assert intent.state is OrderIntentState.CREATE_UNKNOWN
    assert gw.create_calls == 1
    assert svc.recover_unknown_orders(now_ms=NOW + 3) == 1
    assert PaymentStore(db).intent(intent.local_order_id).state is OrderIntentState.CREATED
    assert gw.create_calls == 1


def test_ambiguous_create_without_remote_match_remains_unknown(store, db):
    x = committed(store, "ambnone")
    gw = FakeGateway(); gw.mode = "ambiguous_no_create"; svc = service_for(db, gw)
    svc.dispatch_pending(now_ms=NOW + 2)
    assert svc.recover_unknown_orders(now_ms=NOW + 3) == 0
    assert PaymentStore(db).intent_for_execution(x["execution"]).state is OrderIntentState.CREATE_UNKNOWN
    assert gw.create_calls == 1


def test_recovery_rejects_multiple_orders_for_same_receipt(store, db):
    x = committed(store, "multi")
    gw = FakeGateway(); gw.mode = "ambiguous_no_create"; svc = service_for(db, gw)
    svc.dispatch_pending(now_ms=NOW + 2)
    intent = PaymentStore(db).intent_for_execution(x["execution"])
    class Multi(FakeGateway):
        def orders_by_receipt(self, *, receipt):
            return [RemoteOrder("o1", receipt, intent.amount_paise, intent.currency, "created"), RemoteOrder("o2", receipt, intent.amount_paise, intent.currency, "created")]
    svc.gateway = Multi()
    with pytest.raises(PaymentConflict, match="multiple remote orders"):
        svc.recover_unknown_orders(now_ms=NOW + 3)


def test_pre_payment_hold_expiry_cancels_without_remote_call(store, db):
    x = committed(store, "preexp")
    ps = PaymentStore(db)
    # reservation/hold expires at NOW+60_000
    ps.prepare_from_dispatch(x["execution"], now_ms=NOW + 2)
    with pytest.raises(PaymentConflict, match="hold expired"):
        ps.claim_remote_create(ps.intent_for_execution(x["execution"]).local_order_id, now_ms=NOW + 60_000, checkout_hold_until_ms=NOW + 70_000)
    assert ps.hold(x["execution"]).state is InventoryHoldState.RELEASED
    assert ps.dispatch(x["execution"]).state is DispatchState.CANCELLED
    assert store.scalar("SELECT available_quantity FROM products WHERE sku=?", (x["sku"],)) == 1


def _create_remote(store, db, suffix="web", ttl=10_000):
    x = committed(store, suffix)
    gw = FakeGateway(); svc = service_for(db, gw, ttl=ttl)
    svc.dispatch_pending(now_ms=NOW + 2)
    intent = PaymentStore(db).intent_for_execution(x["execution"])
    return x, gw, svc, intent


def test_checkout_signature_uses_server_stored_order_id(store, db):
    x, gw, svc, intent = _create_remote(store, db, "checkout")
    secret = "checkout-secret"
    payment = "pay-1"
    good = checkout_sig(secret, intent.remote_order_id, payment)
    assert PaymentStore(db).confirm_checkout(local_order_id=intent.local_order_id,payment_id=payment,signature=good,key_secret=secret,now_ms=NOW+3)
    bad = checkout_sig(secret, "attacker-order", "pay-2")
    with pytest.raises(PaymentConflict, match="invalid checkout signature"):
        PaymentStore(db).confirm_checkout(local_order_id=intent.local_order_id,payment_id="pay-2",signature=bad,key_secret=secret,now_ms=NOW+4)


def test_raw_webhook_signature_and_duplicate_handling(store, db):
    x, gw, svc, intent = _create_remote(store, db, "wh")
    secret="wh-secret"; ps=PaymentStore(db)
    body=webhook_body("payment.authorized",payment_id="pay-a",order_id=intent.remote_order_id,status="authorized")
    sig=sign_webhook(secret,body)
    r=ps.accept_webhook(event_id="evt-1",raw_body=body,signature=sig,webhook_secret=secret,now_ms=NOW+3)
    assert r.inserted and not r.duplicate
    r2=ps.accept_webhook(event_id="evt-1",raw_body=body,signature=sig,webhook_secret=secret,now_ms=NOW+4)
    assert r2.duplicate
    altered=body+b" "
    with pytest.raises(PaymentConflict,match="invalid webhook signature"):
        ps.accept_webhook(event_id="evt-2",raw_body=altered,signature=sig,webhook_secret=secret,now_ms=NOW+4)


def test_same_event_id_with_different_signed_body_is_rejected(store, db):
    x, gw, svc, intent = _create_remote(store, db, "evt")
    secret="secret"; ps=PaymentStore(db)
    a=webhook_body("payment.authorized",payment_id="p1",order_id=intent.remote_order_id,status="authorized")
    b=webhook_body("payment.authorized",payment_id="p2",order_id=intent.remote_order_id,status="authorized")
    ps.accept_webhook(event_id="same",raw_body=a,signature=sign_webhook(secret,a),webhook_secret=secret,now_ms=NOW+3)
    with pytest.raises(PaymentConflict,match="event id reused"):
        ps.accept_webhook(event_id="same",raw_body=b,signature=sign_webhook(secret,b),webhook_secret=secret,now_ms=NOW+4)


def test_webhook_event_status_mismatch_fails_closed(store, db):
    x, gw, svc, intent = _create_remote(store, db, "mismatch")
    secret="secret"; ps=PaymentStore(db)
    body=webhook_body("payment.captured",payment_id="p1",order_id=intent.remote_order_id,status="failed")
    with pytest.raises(PaymentConflict,match="event/status mismatch"):
        ps.accept_webhook(event_id="e",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3)


def test_authorized_then_failed_merges_to_uncertain_and_execution_reconciling(store, db):
    x, gw, svc, intent = _create_remote(store, db, "uncertain")
    ps=PaymentStore(db); secret="secret"
    for n,(event,status) in enumerate((("payment.authorized","authorized"),("payment.failed","failed"))):
        body=webhook_body(event,payment_id="p1",order_id=intent.remote_order_id,status=status)
        ps.accept_webhook(event_id=f"e{n}",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3+n)
        state=ps.process_webhook(f"e{n}",now_ms=NOW+3+n)
    assert state is PaymentState.UNCERTAIN
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?",(x["execution"],))==ExecutionState.RECONCILING.value


@pytest.mark.parametrize("events", [
    [("payment.failed","failed"),("payment.captured","captured")],
    [("payment.captured","captured"),("payment.failed","failed")],
])
def test_captured_is_monotonic_across_late_or_reordered_events(store, db, events):
    x, gw, svc, intent = _create_remote(store, db, f"cap{len(events)}{events[0][1][0]}")
    ps=PaymentStore(db); secret="secret"
    state=None
    for n,(event,status) in enumerate(events):
        body=webhook_body(event,payment_id="p1",order_id=intent.remote_order_id,status=status)
        ps.accept_webhook(event_id=f"e{n}",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3+n)
        state=ps.process_webhook(f"e{n}",now_ms=NOW+3+n)
    assert state is PaymentState.CAPTURED
    assert ps.hold(x["execution"]).state is InventoryHoldState.FULFILLED
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?",(x["execution"],))==ExecutionState.SUCCEEDED.value


def test_stale_empty_api_read_cannot_downgrade_prior_captured_webhook(store, db):
    x, gw, svc, intent = _create_remote(store, db, "staleapi")
    ps=PaymentStore(db); secret="secret"
    body=webhook_body("payment.captured",payment_id="p1",order_id=intent.remote_order_id,status="captured")
    ps.accept_webhook(event_id="cap",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3)
    ps.process_webhook("cap",now_ms=NOW+3)
    before=ps.intent(intent.local_order_id)
    stale=RemoteOrder(intent.remote_order_id,intent.receipt,intent.amount_paise,intent.currency,"created")
    state=ps.apply_reconciliation(local_order_id=intent.local_order_id,expected_intent_version=before.version,remote_order=stale,remote_payments=[],now_ms=NOW+4)
    assert state is PaymentState.CAPTURED
    assert ps.intent(intent.local_order_id).state is OrderIntentState.PAID


def test_checkout_expiry_releases_then_late_capture_requires_compensation(store, db):
    x, gw, svc, intent = _create_remote(store, db, "late", ttl=100)
    assert svc.close_expired_checkout(local_order_id=intent.local_order_id,now_ms=NOW+2+100) is PaymentState.RECONCILED_FAILED
    ps=PaymentStore(db)
    assert ps.hold(x["execution"]).state is InventoryHoldState.RELEASED
    assert store.scalar("SELECT available_quantity FROM products WHERE sku=?",(x["sku"],))==1
    secret="secret"
    body=webhook_body("payment.captured",payment_id="late-pay",order_id=intent.remote_order_id,status="captured")
    ps.accept_webhook(event_id="late-cap",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+200)
    assert ps.process_webhook("late-cap",now_ms=NOW+200) is PaymentState.CAPTURED
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?",(x["execution"],))==ExecutionState.COMPENSATION_REQUIRED.value
    assert store.scalar("SELECT available_quantity FROM products WHERE sku=?",(x["sku"],))==1


def test_multiple_captured_payment_ids_for_one_order_fail_closed(store, db):
    x, gw, svc, intent = _create_remote(store, db, "doublecap")
    ps=PaymentStore(db); secret="secret"
    for n,pid in enumerate(("p1","p2")):
        body=webhook_body("payment.captured",payment_id=pid,order_id=intent.remote_order_id,status="captured")
        ps.accept_webhook(event_id=f"c{n}",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3+n)
        if n==0: ps.process_webhook(f"c{n}",now_ms=NOW+3+n)
        else:
            with pytest.raises(PaymentConflict,match="multiple captured"):
                ps.process_webhook(f"c{n}",now_ms=NOW+3+n)


def test_payment_state_merge_is_idempotent_commutative_associative():
    states=list(PaymentState)
    for a in states:
        assert merge_payment_state(a,a) is a
        for b in states:
            assert merge_payment_state(a,b) is merge_payment_state(b,a)
            for c in states:
                assert merge_payment_state(merge_payment_state(a,b),c) is merge_payment_state(a,merge_payment_state(b,c))


def test_reconciliation_rejects_local_intent_version_change(store, db):
    x, gw, svc, intent = _create_remote(store, db, "reconcas")
    ps=PaymentStore(db)
    before=ps.intent(intent.local_order_id)
    con=ps._connect()
    try:
        con.execute("UPDATE payment_order_intents SET version=version+1 WHERE local_order_id=?",(intent.local_order_id,))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="changed during reconciliation"):
        ps.apply_reconciliation(local_order_id=intent.local_order_id,expected_intent_version=before.version,
                                remote_order=gw.fetch_order(order_id=intent.remote_order_id),remote_payments=[],now_ms=NOW+3)
