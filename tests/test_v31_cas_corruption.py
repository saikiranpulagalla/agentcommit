from __future__ import annotations

import sqlite3
import pytest

from agentcommit.domain.models import ExecutionState, INT64_MAX, PaymentState
from agentcommit.payments.models import DispatchState, InventoryHoldState, OrderIntentState, RemoteOrder, RemotePayment
from agentcommit.payments.store import PaymentConflict, PaymentNotFound, PaymentStore
from conftest import NOW
from test_v31_payments import FakeGateway, committed, service_for, sign_webhook, webhook_body


def trigger_ignore(con, name, table, where):
    con.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} WHEN {where} BEGIN SELECT RAISE(IGNORE); END")


@pytest.mark.parametrize("table,msg", [
    ("inventory_holds","inventory hold CAS lost"),
    ("payment_order_intents","order intent CAS lost"),
    ("payment_dispatch_outbox","dispatch CAS lost"),
    ("executions","execution CAS lost"),
])
def test_claim_remote_create_cas_loss_rolls_back_all(store,db,table,msg):
    x=committed(store,f"cas-{table[:4]}"); ps=PaymentStore(db); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    before_h=ps.hold(x["execution"]); before_i=ps.intent(i.local_order_id); before_d=ps.dispatch(x["execution"])
    con=ps._connect()
    try:
        if table=="inventory_holds": where=f"OLD.execution_id='{x['execution']}'"
        elif table=="payment_order_intents": where=f"OLD.local_order_id='{i.local_order_id}'"
        elif table=="payment_dispatch_outbox": where=f"OLD.execution_id='{x['execution']}'"
        else: where=f"OLD.execution_id='{x['execution']}'"
        trigger_ignore(con,"block_update",table,where)
    finally: con.close()
    with pytest.raises(PaymentConflict,match=msg):
        ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    assert ps.hold(x["execution"])==before_h
    assert ps.intent(i.local_order_id)==before_i
    assert ps.dispatch(x["execution"])==before_d


@pytest.mark.parametrize("table,msg", [
    ("payment_order_intents","intent CAS lost"),
    ("payment_dispatch_outbox","dispatch CAS lost"),
])
def test_bind_remote_order_cas_loss_rolls_back(store,db,table,msg):
    x=committed(store,f"bind-{table[-4:]}"); ps=PaymentStore(db); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    creating=ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect()
    try:
        where=f"OLD.local_order_id='{i.local_order_id}'" if table=="payment_order_intents" else f"OLD.execution_id='{x['execution']}'"
        trigger_ignore(con,"block_bind",table,where)
    finally: con.close()
    remote=RemoteOrder("remote-bind",creating.receipt,creating.amount_paise,creating.currency,"created")
    with pytest.raises(PaymentConflict,match=msg): ps.bind_remote_order(i.local_order_id,remote,now_ms=NOW+4)
    assert ps.intent(i.local_order_id).remote_order_id is None
    assert ps.dispatch(x["execution"]).state is DispatchState.DISPATCHING


def test_claim_missing_intent_broken_graph_and_invalid_ttl(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.claim_remote_create("missing",now_ms=NOW,checkout_hold_until_ms=NOW+1)
    with pytest.raises(Exception,match="future"): ps.claim_remote_create("missing",now_ms=NOW,checkout_hold_until_ms=NOW)
    x=committed(store,"brokengraph"); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2)
    con=ps._connect()
    try: con.execute("DELETE FROM inventory_holds WHERE execution_id=?",(x["execution"],))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="broken dispatch graph"):
        ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)


def test_bind_missing_graph_counter_and_paid_remote(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.bind_remote_order("missing",RemoteOrder("o","r",1,"INR","created"),now_ms=NOW)

    x=committed(store,"bindbroken"); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2); c=ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect()
    try: con.execute("DELETE FROM inventory_holds WHERE execution_id=?",(x["execution"],))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="broken remote binding graph"):
        ps.bind_remote_order(i.local_order_id,RemoteOrder("o",c.receipt,c.amount_paise,c.currency,"created"),now_ms=NOW+4)

    y=committed(store,"bindmax"); j=ps.prepare_from_dispatch(y["execution"],now_ms=NOW+2); c2=ps.claim_remote_create(j.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect()
    try: con.execute("UPDATE payment_order_intents SET version=? WHERE local_order_id=?",(INT64_MAX,j.local_order_id))
    finally: con.close()
    with pytest.raises(PaymentConflict,match="counter exhausted"):
        ps.bind_remote_order(j.local_order_id,RemoteOrder("o2",c2.receipt,c2.amount_paise,c2.currency,"created"),now_ms=NOW+4)

    z=committed(store,"paidremote"); k=ps.prepare_from_dispatch(z["execution"],now_ms=NOW+2); c3=ps.claim_remote_create(k.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    bound=ps.bind_remote_order(k.local_order_id,RemoteOrder("o3",c3.receipt,c3.amount_paise,c3.currency,"paid"),now_ms=NOW+4)
    assert bound.state is OrderIntentState.PAID
    assert ps.hold(z["execution"]).state is InventoryHoldState.FULFILLED
    assert ps.scalar("SELECT state FROM executions WHERE execution_id=?",(z["execution"],))==ExecutionState.SUCCEEDED.value


def test_mark_unknown_missing_execution_counter_and_nontransition_state(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.mark_create_unknown("missing",now_ms=NOW)
    x=committed(store,"unkmissing"); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2); ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    raw=sqlite3.connect(db); raw.execute("PRAGMA foreign_keys=OFF"); raw.execute("DELETE FROM executions WHERE execution_id=?",(x["execution"],)); raw.commit(); raw.close()
    with pytest.raises(PaymentConflict,match="missing execution"): ps.mark_create_unknown(i.local_order_id,now_ms=NOW+4)

    y=committed(store,"unkmax"); j=ps.prepare_from_dispatch(y["execution"],now_ms=NOW+2); ps.claim_remote_create(j.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect(); con.execute("UPDATE payment_order_intents SET version=? WHERE local_order_id=?",(INT64_MAX,j.local_order_id)); con.close()
    with pytest.raises(PaymentConflict,match="counter exhausted"): ps.mark_create_unknown(j.local_order_id,now_ms=NOW+4)

    z=committed(store,"unkstate"); k=ps.prepare_from_dispatch(z["execution"],now_ms=NOW+2); ps.claim_remote_create(k.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect(); con.execute("UPDATE executions SET state=? WHERE execution_id=?",(ExecutionState.RECONCILING.value,z["execution"])); con.close()
    ps.mark_create_unknown(k.local_order_id,now_ms=NOW+4)
    assert ps.scalar("SELECT state FROM executions WHERE execution_id=?",(z["execution"],))==ExecutionState.RECONCILING.value


def test_mark_failed_missing_graph_and_counter(store,db):
    ps=PaymentStore(db)
    with pytest.raises(PaymentNotFound): ps.mark_create_failed("missing",now_ms=NOW)
    x=committed(store,"failbroken"); i=ps.prepare_from_dispatch(x["execution"],now_ms=NOW+2); ps.claim_remote_create(i.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect(); con.execute("DELETE FROM inventory_holds WHERE execution_id=?",(x["execution"],)); con.close()
    with pytest.raises(PaymentConflict,match="broken failure graph"): ps.mark_create_failed(i.local_order_id,now_ms=NOW+4)

    y=committed(store,"failmax"); j=ps.prepare_from_dispatch(y["execution"],now_ms=NOW+2); ps.claim_remote_create(j.local_order_id,now_ms=NOW+3,checkout_hold_until_ms=NOW+1000)
    con=ps._connect(); con.execute("UPDATE executions SET version=? WHERE execution_id=?",(INT64_MAX,y["execution"])); con.close()
    with pytest.raises(PaymentConflict,match="counter exhausted"): ps.mark_create_failed(j.local_order_id,now_ms=NOW+4)


def test_private_transition_fail_closed_cases(store,db):
    x=committed(store,"private"); ps=PaymentStore(db); con=ps._connect()
    try:
        ps._begin(con); row=con.execute("SELECT * FROM executions WHERE execution_id=?",(x["execution"],)).fetchone()
        ps._transition_execution_locked(con,row,ExecutionState.CLAIMED)  # same target is idempotent
        con.rollback()
    finally: con.close()

    con=ps._connect(); con.execute("UPDATE executions SET version=?,state=? WHERE execution_id=?",(INT64_MAX,ExecutionState.CLAIMED.value,x["execution"])); con.close()
    con=ps._connect()
    try:
        ps._begin(con); row=con.execute("SELECT * FROM executions WHERE execution_id=?",(x["execution"],)).fetchone()
        with pytest.raises(PaymentConflict,match="version exhausted"): ps._transition_execution_locked(con,row,ExecutionState.EXECUTING)
        con.rollback()
    finally: con.close()

    con=ps._connect(); con.execute("UPDATE executions SET version=1,state=? WHERE execution_id=?",(ExecutionState.SUCCEEDED.value,x["execution"])); con.close()
    con=ps._connect()
    try:
        ps._begin(con); row=con.execute("SELECT * FROM executions WHERE execution_id=?",(x["execution"],)).fetchone()
        with pytest.raises(PaymentConflict,match="terminal execution"): ps._transition_execution_locked(con,row,ExecutionState.RECONCILING)
        con.rollback()
    finally: con.close()


def test_release_hold_idempotent_and_fail_closed_variants(store,db):
    x=committed(store,"holdvariants"); ps=PaymentStore(db)
    con=ps._connect(); con.execute("UPDATE inventory_holds SET state=? WHERE execution_id=?",(InventoryHoldState.RELEASED.value,x["execution"])); con.close()
    con=ps._connect()
    try:
        ps._begin(con); row=con.execute("SELECT * FROM inventory_holds WHERE execution_id=?",(x["execution"],)).fetchone(); ps._release_hold_locked(con,row,now_ms=NOW); con.rollback()
    finally: con.close()

    for state,msg in ((InventoryHoldState.FULFILLED.value,"fulfilled"),("BOGUS","invalid hold state")):
        con=ps._connect(); con.execute("UPDATE inventory_holds SET state=?,version=1 WHERE execution_id=?",(state,x["execution"])); con.close()
        con=ps._connect()
        try:
            ps._begin(con); row=con.execute("SELECT * FROM inventory_holds WHERE execution_id=?",(x["execution"],)).fetchone()
            with pytest.raises((PaymentConflict,ValueError),match=msg if state!="BOGUS" else None): ps._release_hold_locked(con,row,now_ms=NOW)
            con.rollback()
        finally: con.close()

    con=ps._connect(); con.execute("UPDATE inventory_holds SET state=?,version=? WHERE execution_id=?",(InventoryHoldState.HELD.value,INT64_MAX,x["execution"])); con.close()
    con=ps._connect()
    try:
        ps._begin(con); row=con.execute("SELECT * FROM inventory_holds WHERE execution_id=?",(x["execution"],)).fetchone()
        with pytest.raises(PaymentConflict,match="version exhausted"): ps._release_hold_locked(con,row,now_ms=NOW)
        con.rollback()
    finally: con.close()


def test_invalid_event_id_and_unsupported_signed_event(store,db):
    ps=PaymentStore(db)
    for eid in ("","x"*257):
        with pytest.raises(Exception,match="event id"): ps.accept_webhook(event_id=eid,raw_body=b"x",signature="x",webhook_secret="s",now_ms=NOW)
    x=committed(store,"unsupported"); gw=FakeGateway(); svc=service_for(db,gw); svc.dispatch_pending(now_ms=NOW+2); i=ps.intent_for_execution(x["execution"]); secret="s"
    body=webhook_body("payment.refunded",payment_id="p",order_id=i.remote_order_id,status="failed")
    with pytest.raises(PaymentConflict,match="unsupported webhook"):
        ps.accept_webhook(event_id="e",raw_body=body,signature=sign_webhook(secret,body),webhook_secret=secret,now_ms=NOW+3)


def test_terminal_execution_ignores_weaker_payment_evidence(store,db):
    x=committed(store,"terminalweak"); gw=FakeGateway(); svc=service_for(db,gw); svc.dispatch_pending(now_ms=NOW+2); ps=PaymentStore(db); i=ps.intent_for_execution(x["execution"])
    con=ps._connect(); con.execute("UPDATE executions SET state=? WHERE execution_id=?",(ExecutionState.SUCCEEDED.value,x["execution"])); con.close()
    sec="s"; body=webhook_body("payment.authorized",payment_id="p",order_id=i.remote_order_id,status="authorized")
    ps.accept_webhook(event_id="e",raw_body=body,signature=sign_webhook(sec,body),webhook_secret=sec,now_ms=NOW+3); ps.process_webhook("e",now_ms=NOW+3)
    assert ps.scalar("SELECT state FROM executions WHERE execution_id=?",(x["execution"],))==ExecutionState.SUCCEEDED.value


def test_reconciliation_existing_payment_unchanged_and_version_exhaustion(store,db):
    x=committed(store,"reconexisting"); gw=FakeGateway(); svc=service_for(db,gw); svc.dispatch_pending(now_ms=NOW+2); ps=PaymentStore(db); i=ps.intent_for_execution(x["execution"])
    o=gw.fetch_order(order_id=i.remote_order_id); p=RemotePayment("p",i.remote_order_id,i.amount_paise,i.currency,"authorized")
    cur=ps.intent(i.local_order_id); ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=cur.version,remote_order=o,remote_payments=[p],now_ms=NOW+3)
    cur=ps.intent(i.local_order_id); assert ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=cur.version,remote_order=o,remote_payments=[p],now_ms=NOW+4) is PaymentState.AUTHORIZED
    con=ps._connect(); con.execute("UPDATE payment_attempts SET version=? WHERE payment_id='p'",(INT64_MAX,)); con.close()
    cur=ps.intent(i.local_order_id); failed=RemotePayment("p",i.remote_order_id,i.amount_paise,i.currency,"failed")
    with pytest.raises(PaymentConflict,match="payment version exhausted"):
        ps.apply_reconciliation(local_order_id=i.local_order_id,expected_intent_version=cur.version,remote_order=o,remote_payments=[failed],now_ms=NOW+5)


def test_checkout_close_version_hold_and_counter_failures(store,db):
    x=committed(store,"closefails"); gw=FakeGateway(); gw.forced_order_id="order-closefails"; svc=service_for(db,gw,ttl=50); svc.dispatch_pending(now_ms=NOW+2); ps=PaymentStore(db); i=ps.intent_for_execution(x["execution"])
    with pytest.raises(PaymentConflict,match="intent changed"): ps.release_checkout_hold_after_reconcile(local_order_id=i.local_order_id,expected_intent_version=999,now_ms=NOW+100)
    con=ps._connect(); con.execute("UPDATE inventory_holds SET state=? WHERE execution_id=?",(InventoryHoldState.RELEASED.value,x["execution"])); con.close(); i=ps.intent(i.local_order_id)
    with pytest.raises(PaymentConflict,match="not releasable"): ps.release_checkout_hold_after_reconcile(local_order_id=i.local_order_id,expected_intent_version=i.version,now_ms=NOW+100)

    y=committed(store,"closemax"); gw2=FakeGateway(); gw2.forced_order_id="order-closemax"; svc2=service_for(db,gw2,ttl=50); svc2.dispatch_pending(now_ms=NOW+2); j=ps.intent_for_execution(y["execution"])
    con=ps._connect(); con.execute("UPDATE payment_order_intents SET version=? WHERE local_order_id=?",(INT64_MAX,j.local_order_id)); con.close(); j=ps.intent(j.local_order_id)
    with pytest.raises(PaymentConflict,match="intent version exhausted"): ps.release_checkout_hold_after_reconcile(local_order_id=j.local_order_id,expected_intent_version=j.version,now_ms=NOW+100)
