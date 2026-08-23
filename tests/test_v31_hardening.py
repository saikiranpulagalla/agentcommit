from __future__ import annotations

import hashlib
import hmac
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from agentcommit.domain.models import DomainError, ExecutionState, INT64_MAX, PaymentState
from agentcommit.payments.models import InventoryHoldState, OrderIntentState, RemoteOrder
from agentcommit.payments.razorpay import (
    AmbiguousRemoteOutcome, DefiniteRemoteRejection, HttpRazorpayGateway, RemoteContractError,
    deterministic_local_order_id, deterministic_receipt,
)
from agentcommit.payments.service import PaymentService
from agentcommit.payments.store import PaymentConflict, PaymentStore
from agentcommit.store.sqlite_store import MerchantStore
from conftest import NOW, seed_path
from test_v31_payments import FakeGateway, committed, service_for, sign_webhook, webhook_body


def test_deterministic_external_identity_is_stable_and_bounded():
    a=deterministic_receipt("execution-123")
    assert a==deterministic_receipt("execution-123")
    assert a!=deterministic_receipt("execution-124")
    assert len(a)<=40
    assert deterministic_local_order_id("execution-123")==deterministic_local_order_id("execution-123")


def test_corrupted_dispatch_receipt_fails_before_remote_side_effect(store, db):
    x=committed(store,"badreceipt")
    ps=PaymentStore(db); con=ps._connect()
    try: con.execute("UPDATE payment_dispatch_outbox SET receipt='wrong' WHERE execution_id=?",(x["execution"],))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="receipt binding"):
        ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)


def test_nonpending_dispatch_missing_intent_fails_closed(store, db):
    x=committed(store,"brokenoutbox")
    ps=PaymentStore(db); con=ps._connect()
    try: con.execute("UPDATE payment_dispatch_outbox SET state='DISPATCHED' WHERE execution_id=?",(x["execution"],))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="missing its durable intent"):
        ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)


def test_counter_exhaustion_before_remote_claim_rolls_back_hold(store, db):
    x=committed(store,"maxcounter")
    ps=PaymentStore(db); intent=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    before=ps.hold(x["execution"])
    con=ps._connect()
    try: con.execute("UPDATE payment_order_intents SET version=? WHERE local_order_id=?",(INT64_MAX,intent.local_order_id))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="version exhausted"):
        ps.claim_remote_create(intent.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    after=ps.hold(x["execution"])
    assert after.hold_until_ms==before.hold_until_ms and after.version==before.version


def test_signed_webhook_rejects_boolean_money(store, db):
    x=committed(store,"boolmoney"); gw=FakeGateway(); svc=service_for(db,gw); svc.dispatch_pending(now_ms=NOW+2)
    intent=PaymentStore(db).intent_for_execution(x["execution"]); secret="s"
    body=json.dumps({"event":"payment.captured","payload":{"payment":{"entity":{"id":"p","order_id":intent.remote_order_id,"amount":True,"currency":"INR","status":"captured"}}}},separators=(",",":")).encode()
    with pytest.raises(PaymentConflict,match="malformed signed webhook"):
        PaymentStore(db).accept_webhook(event_id="e",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3)


def test_webhook_body_size_is_bounded(store, db):
    ps=PaymentStore(db); body=b"x"*1_048_577
    with pytest.raises(DomainError,match="1..1048576"):
        ps.accept_webhook(event_id="e",raw_body=body,signature="x",webhook_secret="s",now_ms=NOW)


def test_remote_binding_wrong_amount_fails_and_service_marks_unknown(store, db):
    x=committed(store,"wrongremote")
    class Wrong(FakeGateway):
        def create_order(self, *, amount_paise, currency, receipt):
            self.create_calls+=1
            return RemoteOrder("ord-wrong",receipt,amount_paise+1,currency,"created")
    gw=Wrong(); svc=service_for(db,gw)
    with pytest.raises(PaymentConflict,match="binding mismatch"):
        svc.dispatch_pending(now_ms=NOW+2)
    assert PaymentStore(db).intent_for_execution(x["execution"]).state is OrderIntentState.CREATE_UNKNOWN


def test_unique_remote_order_conflict_becomes_unknown_not_false_failure(store, db):
    a=committed(store,"ua"); b=committed(store,"ub")
    gw=FakeGateway(); svc=service_for(db,gw)
    svc.dispatch_pending(now_ms=NOW+2,limit=1)
    first=PaymentStore(db).intent_for_execution(a["execution"])
    # Force the second remote create to return the first remote ID.
    gw.forced_order_id=first.remote_order_id
    with pytest.raises(PaymentConflict,match="identity conflict"):
        svc.dispatch_pending(now_ms=NOW+3)
    second=PaymentStore(db).intent_for_execution(b["execution"])
    assert second.state is OrderIntentState.CREATE_UNKNOWN


def test_remote_contract_does_not_coerce_string_amount():
    with pytest.raises(RemoteContractError):
        HttpRazorpayGateway._order({"id":"o","receipt":"r","amount":"100","currency":"INR","status":"created"})


def test_http_4xx_is_definite_and_5xx_is_ambiguous():
    g=HttpRazorpayGateway("id","secret")
    def err(code):
        return urllib.error.HTTPError("https://api.razorpay.com/v1/orders",code,"x",{},io.BytesIO(b"{}"))
    with patch("urllib.request.urlopen",side_effect=err(400)):
        with pytest.raises(DefiniteRemoteRejection): g.create_order(amount_paise=100,currency="INR",receipt="r")
    with patch("urllib.request.urlopen",side_effect=err(500)):
        with pytest.raises(AmbiguousRemoteOutcome): g.create_order(amount_paise=100,currency="INR",receipt="r")


def test_http_network_error_is_ambiguous():
    g=HttpRazorpayGateway("id","secret")
    with patch("urllib.request.urlopen",side_effect=urllib.error.URLError("boom")):
        with pytest.raises(AmbiguousRemoteOutcome): g.create_order(amount_paise=100,currency="INR",receipt="r")


def test_http_malformed_json_is_contract_error():
    g=HttpRazorpayGateway("id","secret")
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return b"not-json"
    with patch("urllib.request.urlopen",return_value=Resp()):
        with pytest.raises(RemoteContractError): g.create_order(amount_paise=100,currency="INR",receipt="r")


def test_new_remote_order_must_be_created_status():
    g=HttpRazorpayGateway("id","secret")
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return b'{"id":"o","receipt":"r","amount":100,"currency":"INR","status":"attempted"}'
    with patch("urllib.request.urlopen",return_value=Resp()):
        with pytest.raises(RemoteContractError,match="not returned in created"):
            g.create_order(amount_paise=100,currency="INR",receipt="r")


def test_late_capture_after_terminal_release_can_only_move_to_compensation(store, db):
    x=committed(store,"terminalcap"); gw=FakeGateway(); svc=service_for(db,gw,ttl=50); svc.dispatch_pending(now_ms=NOW+2)
    intent=PaymentStore(db).intent_for_execution(x["execution"])
    svc.close_expired_checkout(local_order_id=intent.local_order_id,now_ms=NOW+52)
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?",(x["execution"],))==ExecutionState.RECONCILED_FAILED.value
    secret="s"; body=webhook_body("payment.captured",payment_id="p",order_id=intent.remote_order_id,status="captured")
    ps=PaymentStore(db); ps.accept_webhook(event_id="late",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+60)
    ps.process_webhook("late",now_ms=NOW+60)
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?",(x["execution"],))==ExecutionState.COMPENSATION_REQUIRED.value


def test_persisted_payment_binding_corruption_fails_on_next_event(store, db):
    x=committed(store,"corruptpay"); gw=FakeGateway(); svc=service_for(db,gw); svc.dispatch_pending(now_ms=NOW+2)
    intent=PaymentStore(db).intent_for_execution(x["execution"]); ps=PaymentStore(db); secret="s"
    body=webhook_body("payment.authorized",payment_id="p",order_id=intent.remote_order_id,status="authorized")
    ps.accept_webhook(event_id="a",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3); ps.process_webhook("a",now_ms=NOW+3)
    con=ps._connect()
    try: con.execute("UPDATE payment_attempts SET amount_paise=amount_paise+1 WHERE payment_id='p'")
    finally: con.close()
    body2=webhook_body("payment.failed",payment_id="p",order_id=intent.remote_order_id,status="failed")
    ps.accept_webhook(event_id="b",raw_body=body2,signature=sign_webhook(secret,body2),webhook_secret=secret,now_ms=NOW+4)
    with pytest.raises(PaymentConflict,match="binding changed"):
        ps.process_webhook("b",now_ms=NOW+4)
