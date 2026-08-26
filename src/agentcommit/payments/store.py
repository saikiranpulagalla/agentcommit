from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from agentcommit.domain.models import DomainError, ExecutionState, INT64_MAX, PaymentState
from .models import (
    DispatchRecord, DispatchState, InventoryHold, InventoryHoldState,
    OrderIntentState, PaymentOrderIntent, RemoteOrder, RemotePayment,
)
from .razorpay import deterministic_local_order_id, deterministic_receipt, verify_webhook_signature


class PaymentStoreError(RuntimeError):
    pass


class PaymentConflict(PaymentStoreError):
    pass


class PaymentNotFound(PaymentStoreError):
    pass


def _valid_now(now_ms: int) -> None:
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or not (1 <= now_ms <= INT64_MAX):
        raise DomainError("invalid now_ms")


def merge_payment_state(a: PaymentState, b: PaymentState) -> PaymentState:
    if not isinstance(a, PaymentState) or not isinstance(b, PaymentState):
        raise DomainError("invalid payment state")
    if a is b:
        return a
    if PaymentState.CAPTURED in (a, b):
        return PaymentState.CAPTURED
    if PaymentState.RECONCILED_FAILED in (a, b):
        return PaymentState.RECONCILED_FAILED
    if a is PaymentState.UNKNOWN:
        return b
    if b is PaymentState.UNKNOWN:
        return a
    if PaymentState.UNCERTAIN in (a, b):
        return PaymentState.UNCERTAIN
    pair = {a, b}
    if pair == {PaymentState.AUTHORIZED, PaymentState.OBSERVED_FAILED}:
        return PaymentState.UNCERTAIN
    if PaymentState.AUTHORIZED in pair:
        return PaymentState.AUTHORIZED
    if PaymentState.OBSERVED_FAILED in pair:
        return PaymentState.OBSERVED_FAILED
    return PaymentState.CREATED


def remote_payment_state(status: str) -> PaymentState:
    mapping = {
        "created": PaymentState.CREATED,
        "authorized": PaymentState.AUTHORIZED,
        "failed": PaymentState.OBSERVED_FAILED,
        "captured": PaymentState.CAPTURED,
    }
    try:
        return mapping[status]
    except KeyError as exc:
        raise DomainError("unsupported payment status") from exc


@dataclass(frozen=True, slots=True)
class WebhookAcceptResult:
    inserted: bool
    duplicate: bool


class PaymentStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    @staticmethod
    def _begin(con: sqlite3.Connection) -> None:
        con.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _intent(row: sqlite3.Row) -> PaymentOrderIntent:
        return PaymentOrderIntent(
            local_order_id=row["local_order_id"], execution_id=row["execution_id"], receipt=row["receipt"],
            amount_paise=row["amount_paise"], currency=row["currency"], state=OrderIntentState(row["state"]),
            version=row["version"], remote_order_id=row["remote_order_id"],
        )

    @staticmethod
    def _dispatch(row: sqlite3.Row) -> DispatchRecord:
        return DispatchRecord(row["execution_id"], row["receipt"], DispatchState(row["state"]), row["version"], row["created_at_ms"])

    @staticmethod
    def _hold(row: sqlite3.Row) -> InventoryHold:
        return InventoryHold(
            row["execution_id"], row["reservation_id"], row["merchant_id"], row["sku"], row["quantity"],
            InventoryHoldState(row["state"]), row["hold_until_ms"], row["version"],
        )

    def intent_for_execution(self, execution_id: str) -> PaymentOrderIntent | None:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM payment_order_intents WHERE execution_id=?", (execution_id,)).fetchone()
            return None if row is None else self._intent(row)
        finally:
            con.close()

    def intent(self, local_order_id: str) -> PaymentOrderIntent:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?", (local_order_id,)).fetchone()
            if row is None:
                raise PaymentNotFound("order intent")
            return self._intent(row)
        finally:
            con.close()

    def hold(self, execution_id: str) -> InventoryHold:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM inventory_holds WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                raise PaymentNotFound("inventory hold")
            return self._hold(row)
        finally:
            con.close()

    def dispatch(self, execution_id: str) -> DispatchRecord:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM payment_dispatch_outbox WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                raise PaymentNotFound("dispatch")
            return self._dispatch(row)
        finally:
            con.close()

    def pending_dispatches(self, limit: int = 100) -> list[DispatchRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise DomainError("invalid limit")
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM payment_dispatch_outbox WHERE state=? ORDER BY created_at_ms,execution_id LIMIT ?",
                (DispatchState.PENDING.value, limit),
            ).fetchall()
            return [self._dispatch(r) for r in rows]
        finally:
            con.close()

    def unresolved_dispatches(self, limit: int = 100) -> list[PaymentOrderIntent]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT i.* FROM payment_order_intents i JOIN payment_dispatch_outbox d ON d.execution_id=i.execution_id "
                "WHERE (d.state=? AND i.state IN (?,?)) OR i.state=? ORDER BY i.created_at_ms LIMIT ?",
                (DispatchState.DISPATCHING.value, OrderIntentState.CREATING.value, OrderIntentState.CREATE_UNKNOWN.value,
                 OrderIntentState.CREATE_REQUIRES_MANUAL_REVIEW.value, limit),
            ).fetchall()
            return [self._intent(r) for r in rows]
        finally:
            con.close()

    def prepare_from_dispatch(self, execution_id: str, *, now_ms: int) -> PaymentOrderIntent:
        _valid_now(now_ms)
        con = self._connect()
        try:
            self._begin(con)
            d = con.execute("SELECT * FROM payment_dispatch_outbox WHERE execution_id=?", (execution_id,)).fetchone()
            if d is None:
                raise PaymentNotFound("dispatch")
            expected_receipt = deterministic_receipt(execution_id)
            if d["receipt"] != expected_receipt:
                raise PaymentConflict("dispatch receipt binding mismatch")
            existing = con.execute("SELECT * FROM payment_order_intents WHERE execution_id=?", (execution_id,)).fetchone()
            if existing is not None:
                con.commit()
                return self._intent(existing)
            if d["state"] != DispatchState.PENDING.value:
                raise PaymentConflict("non-pending dispatch is missing its durable intent")
            receipt = con.execute("SELECT * FROM commit_receipts WHERE execution_id=?", (execution_id,)).fetchone()
            if receipt is None:
                raise PaymentConflict("committed execution has no commit receipt")
            execution = con.execute("SELECT * FROM executions WHERE execution_id=?", (execution_id,)).fetchone()
            if execution is None or execution["state"] != ExecutionState.CLAIMED.value:
                raise PaymentConflict("execution is not claim-dispatchable")
            local_order_id = deterministic_local_order_id(execution_id)
            con.execute(
                "INSERT INTO payment_order_intents(local_order_id,execution_id,receipt,amount_paise,currency,state,remote_order_id,version,created_at_ms,updated_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (local_order_id, execution_id, expected_receipt, receipt["amount_paise"], receipt["currency"],
                 OrderIntentState.PREPARED.value, None, 1, now_ms, now_ms),
            )
            row = con.execute("SELECT * FROM payment_order_intents WHERE execution_id=?", (execution_id,)).fetchone()
            con.commit()
            return self._intent(row)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def claim_remote_create(self, local_order_id: str, *, now_ms: int, checkout_hold_until_ms: int) -> PaymentOrderIntent:
        _valid_now(now_ms); _valid_now(checkout_hold_until_ms)
        if checkout_hold_until_ms <= now_ms:
            raise DomainError("checkout hold must extend into the future")
        con = self._connect()
        expired = False
        try:
            self._begin(con)
            i = con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?", (local_order_id,)).fetchone()
            if i is None:
                raise PaymentNotFound("order intent")
            if i["state"] != OrderIntentState.PREPARED.value:
                raise PaymentConflict("order intent is not prepared")
            d = con.execute("SELECT * FROM payment_dispatch_outbox WHERE execution_id=?", (i["execution_id"],)).fetchone()
            h = con.execute("SELECT * FROM inventory_holds WHERE execution_id=?", (i["execution_id"],)).fetchone()
            e = con.execute("SELECT * FROM executions WHERE execution_id=?", (i["execution_id"],)).fetchone()
            if None in (d, h, e):
                raise PaymentConflict("broken dispatch graph")
            if d["state"] != DispatchState.PENDING.value or e["state"] != ExecutionState.CLAIMED.value:
                raise PaymentConflict("dispatch/execution not claimable")
            if h["state"] != InventoryHoldState.HELD.value:
                raise PaymentConflict("inventory is no longer held")
            if now_ms >= int(h["hold_until_ms"]):
                self._release_hold_locked(con, h, now_ms=now_ms)
                self._transition_execution_locked(con, e, ExecutionState.RECONCILED_FAILED)
                con.execute("UPDATE payment_dispatch_outbox SET state=?,version=version+1 WHERE execution_id=?",
                            (DispatchState.CANCELLED.value, i["execution_id"]))
                con.execute("UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? WHERE local_order_id=?",
                            (OrderIntentState.RECONCILED_FAILED.value, now_ms, local_order_id))
                con.commit(); expired = True
            else:
                for row, name in ((i, "intent"), (d, "dispatch"), (h, "hold"), (e, "execution")):
                    if int(row["version"]) >= INT64_MAX:
                        raise PaymentConflict(f"{name} version exhausted")
                if con.execute("UPDATE inventory_holds SET hold_until_ms=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                               (checkout_hold_until_ms, i["execution_id"], InventoryHoldState.HELD.value, h["version"])).rowcount != 1:
                    raise PaymentConflict("inventory hold CAS lost")
                if con.execute("UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? WHERE local_order_id=? AND state=? AND version=?",
                               (OrderIntentState.CREATING.value, now_ms, local_order_id, OrderIntentState.PREPARED.value, i["version"])).rowcount != 1:
                    raise PaymentConflict("order intent CAS lost")
                if con.execute("UPDATE payment_dispatch_outbox SET state=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                               (DispatchState.DISPATCHING.value, i["execution_id"], DispatchState.PENDING.value, d["version"])).rowcount != 1:
                    raise PaymentConflict("dispatch CAS lost")
                if con.execute("UPDATE executions SET state=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                               (ExecutionState.EXECUTING.value, i["execution_id"], ExecutionState.CLAIMED.value, e["version"])).rowcount != 1:
                    raise PaymentConflict("execution CAS lost")
                row = con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?", (local_order_id,)).fetchone()
                con.commit()
                return self._intent(row)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()
        if expired:
            raise PaymentConflict("pre-payment inventory hold expired")
        raise AssertionError("unreachable")

    def bind_remote_order(self, local_order_id: str, remote: RemoteOrder, *, now_ms: int) -> PaymentOrderIntent:
        _valid_now(now_ms); remote.__post_init__()
        con = self._connect()
        try:
            self._begin(con)
            i = con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?", (local_order_id,)).fetchone()
            if i is None:
                raise PaymentNotFound("order intent")
            if i["state"] not in (OrderIntentState.CREATING.value, OrderIntentState.CREATE_UNKNOWN.value, OrderIntentState.CREATE_REQUIRES_MANUAL_REVIEW.value):
                if i["remote_order_id"] == remote.order_id and i["state"] in (OrderIntentState.CREATED.value, OrderIntentState.PAID.value):
                    con.commit(); return self._intent(i)
                raise PaymentConflict("order intent cannot bind remote order")
            if remote.receipt != i["receipt"] or remote.amount_paise != i["amount_paise"] or remote.currency != i["currency"]:
                raise PaymentConflict("remote order binding mismatch")
            d = con.execute("SELECT * FROM payment_dispatch_outbox WHERE execution_id=?", (i["execution_id"],)).fetchone()
            h = con.execute("SELECT * FROM inventory_holds WHERE execution_id=?", (i["execution_id"],)).fetchone()
            e = con.execute("SELECT * FROM executions WHERE execution_id=?", (i["execution_id"],)).fetchone()
            if None in (d, h, e):
                raise PaymentConflict("broken remote binding graph")
            if max(int(i["version"]), int(d["version"]), int(e["version"]), int(h["version"])) >= INT64_MAX:
                raise PaymentConflict("counter exhausted")
            state = OrderIntentState.PAID if remote.status == "paid" else OrderIntentState.CREATED
            try:
                if con.execute("UPDATE payment_order_intents SET remote_order_id=?,state=?,version=version+1,updated_at_ms=? WHERE local_order_id=? AND version=?",
                               (remote.order_id, state.value, now_ms, local_order_id, i["version"])).rowcount != 1:
                    raise PaymentConflict("intent CAS lost")
            except sqlite3.IntegrityError as exc:
                raise PaymentConflict("remote order identity conflict") from exc
            if con.execute("UPDATE payment_dispatch_outbox SET state=?,version=version+1 WHERE execution_id=? AND version=?",
                           (DispatchState.DISPATCHED.value, i["execution_id"], d["version"])).rowcount != 1:
                raise PaymentConflict("dispatch CAS lost")
            if remote.status == "paid":
                self._apply_captured_locked(con, execution=e, hold=h, now_ms=now_ms)
            con.commit()
            row = self.intent(local_order_id)
            return row
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def mark_create_unknown(self, local_order_id: str, *, now_ms: int) -> None:
        _valid_now(now_ms)
        con=self._connect()
        try:
            self._begin(con)
            i=con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?",(local_order_id,)).fetchone()
            if i is None: raise PaymentNotFound("order intent")
            if i["state"] == OrderIntentState.CREATE_UNKNOWN.value:
                con.commit(); return
            if i["state"] != OrderIntentState.CREATING.value: raise PaymentConflict("cannot mark create unknown")
            e=con.execute("SELECT * FROM executions WHERE execution_id=?",(i["execution_id"],)).fetchone()
            if e is None: raise PaymentConflict("missing execution")
            if int(i["version"])>=INT64_MAX or int(e["version"])>=INT64_MAX: raise PaymentConflict("counter exhausted")
            con.execute("UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? WHERE local_order_id=?",
                        (OrderIntentState.CREATE_UNKNOWN.value,now_ms,local_order_id))
            if e["state"] in (ExecutionState.EXECUTING.value, ExecutionState.CLAIMED.value):
                self._transition_execution_locked(con,e,ExecutionState.EXECUTION_UNKNOWN)
            con.commit()
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def mark_create_requires_manual_review(self, local_order_id: str, *, now_ms: int) -> None:
        """Stop automatic retries after an expired unknown remote create.

        This deliberately does not release inventory or restore authority: an empty
        lookup is not proof that a money-moving remote side effect never happened.
        """
        _valid_now(now_ms)
        con = self._connect()
        try:
            self._begin(con)
            i = con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?", (local_order_id,)).fetchone()
            if i is None:
                raise PaymentNotFound("order intent")
            if i["state"] == OrderIntentState.CREATE_REQUIRES_MANUAL_REVIEW.value:
                con.commit()
                return
            if i["state"] != OrderIntentState.CREATE_UNKNOWN.value or i["remote_order_id"] is not None:
                raise PaymentConflict("create is not an unbound unknown")
            d = con.execute("SELECT * FROM payment_dispatch_outbox WHERE execution_id=?", (i["execution_id"],)).fetchone()
            h = con.execute("SELECT * FROM inventory_holds WHERE execution_id=?", (i["execution_id"],)).fetchone()
            e = con.execute("SELECT * FROM executions WHERE execution_id=?", (i["execution_id"],)).fetchone()
            if None in (d, h, e):
                raise PaymentConflict("broken unknown-create graph")
            if now_ms < int(h["hold_until_ms"]):
                raise PaymentConflict("unknown create hold has not expired")
            if h["state"] != InventoryHoldState.HELD.value:
                raise PaymentConflict("unknown create inventory hold is not retained")
            if max(int(i["version"]), int(d["version"]), int(e["version"])) >= INT64_MAX:
                raise PaymentConflict("counter exhausted")
            if con.execute(
                "UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? "
                "WHERE local_order_id=? AND state=? AND version=?",
                (OrderIntentState.CREATE_REQUIRES_MANUAL_REVIEW.value, now_ms, local_order_id,
                 OrderIntentState.CREATE_UNKNOWN.value, i["version"]),
            ).rowcount != 1:
                raise PaymentConflict("unknown create intent CAS lost")
            if con.execute(
                "UPDATE payment_dispatch_outbox SET state=?,version=version+1 WHERE execution_id=? AND version=?",
                (DispatchState.CANCELLED.value, i["execution_id"], d["version"]),
            ).rowcount != 1:
                raise PaymentConflict("unknown create dispatch CAS lost")
            if ExecutionState(e["state"]) is not ExecutionState.EXECUTION_UNKNOWN:
                self._transition_execution_locked(con, e, ExecutionState.EXECUTION_UNKNOWN)
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def mark_create_failed(self, local_order_id: str, *, now_ms: int) -> None:
        _valid_now(now_ms)
        con=self._connect()
        try:
            self._begin(con)
            i=con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?",(local_order_id,)).fetchone()
            if i is None: raise PaymentNotFound("order intent")
            if i["state"] == OrderIntentState.CREATE_FAILED.value:
                con.commit(); return
            if i["state"] != OrderIntentState.CREATING.value: raise PaymentConflict("cannot mark create failed")
            d=con.execute("SELECT * FROM payment_dispatch_outbox WHERE execution_id=?",(i["execution_id"],)).fetchone()
            h=con.execute("SELECT * FROM inventory_holds WHERE execution_id=?",(i["execution_id"],)).fetchone()
            e=con.execute("SELECT * FROM executions WHERE execution_id=?",(i["execution_id"],)).fetchone()
            if None in (d,h,e): raise PaymentConflict("broken failure graph")
            if max(int(i["version"]),int(d["version"]),int(h["version"]),int(e["version"]))>=INT64_MAX: raise PaymentConflict("counter exhausted")
            self._release_hold_locked(con,h,now_ms=now_ms)
            self._transition_execution_locked(con,e,ExecutionState.RECONCILED_FAILED)
            con.execute("UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? WHERE local_order_id=?",
                        (OrderIntentState.CREATE_FAILED.value,now_ms,local_order_id))
            con.execute("UPDATE payment_dispatch_outbox SET state=?,version=version+1 WHERE execution_id=?",
                        (DispatchState.CANCELLED.value,i["execution_id"]))
            con.commit()
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def _transition_execution_locked(self, con: sqlite3.Connection, row: sqlite3.Row, target: ExecutionState) -> None:
        current=ExecutionState(row["state"])
        if current is target: return
        if int(row["version"])>=INT64_MAX: raise PaymentConflict("execution version exhausted")
        terminal={ExecutionState.SUCCEEDED,ExecutionState.RECONCILED_FAILED,ExecutionState.COMPENSATION_REQUIRED}
        if current in terminal and not (current is ExecutionState.RECONCILED_FAILED and target is ExecutionState.COMPENSATION_REQUIRED):
            raise PaymentConflict("terminal execution cannot be rewritten")
        if con.execute("UPDATE executions SET state=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                       (target.value,row["execution_id"],row["state"],row["version"])).rowcount!=1:
            raise PaymentConflict("execution CAS lost")
        row2=con.execute("SELECT * FROM executions WHERE execution_id=?",(row["execution_id"],)).fetchone()
        # mutate only local reference through return is inconvenient; callers re-fetch when needed.

    def _release_hold_locked(self, con: sqlite3.Connection, hold: sqlite3.Row, *, now_ms: int) -> None:
        if hold["state"] == InventoryHoldState.RELEASED.value: return
        if hold["state"] == InventoryHoldState.FULFILLED.value: raise PaymentConflict("fulfilled inventory cannot be released")
        if hold["state"] != InventoryHoldState.HELD.value: raise PaymentConflict("invalid hold state")
        if int(hold["version"])>=INT64_MAX: raise PaymentConflict("hold version exhausted")
        if con.execute("UPDATE inventory_holds SET state=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                       (InventoryHoldState.RELEASED.value,hold["execution_id"],InventoryHoldState.HELD.value,hold["version"])).rowcount!=1:
            raise PaymentConflict("hold CAS lost")
        con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
                    (hold["quantity"],hold["merchant_id"],hold["sku"]))

    def _apply_captured_locked(self, con: sqlite3.Connection, *, execution: sqlite3.Row, hold: sqlite3.Row, now_ms: int) -> None:
        hs=InventoryHoldState(hold["state"])
        if hs is InventoryHoldState.HELD:
            if int(hold["version"])>=INT64_MAX: raise PaymentConflict("hold version exhausted")
            if con.execute("UPDATE inventory_holds SET state=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                           (InventoryHoldState.FULFILLED.value,hold["execution_id"],InventoryHoldState.HELD.value,hold["version"])).rowcount!=1:
                raise PaymentConflict("hold CAS lost")
            if ExecutionState(execution["state"]) is not ExecutionState.SUCCEEDED:
                self._transition_execution_locked(con,execution,ExecutionState.SUCCEEDED)
        elif hs is InventoryHoldState.RELEASED:
            if ExecutionState(execution["state"]) is not ExecutionState.COMPENSATION_REQUIRED:
                self._transition_execution_locked(con,execution,ExecutionState.COMPENSATION_REQUIRED)
        elif hs is InventoryHoldState.FULFILLED:
            if ExecutionState(execution["state"]) not in {ExecutionState.SUCCEEDED,ExecutionState.COMPENSATION_REQUIRED}:
                self._transition_execution_locked(con,execution,ExecutionState.SUCCEEDED)
        else:
            raise PaymentConflict("invalid hold state")

    def accept_webhook(self, *, event_id: str, raw_body: bytes, signature: str, webhook_secret: str, now_ms: int) -> WebhookAcceptResult:
        _valid_now(now_ms)
        if not isinstance(event_id,str) or not event_id or len(event_id)>256:
            raise DomainError("invalid webhook event id")
        if not isinstance(raw_body, (bytes, bytearray)) or not raw_body or len(raw_body) > 1_048_576:
            raise DomainError("webhook body must be 1..1048576 bytes")
        if not verify_webhook_signature(webhook_secret=webhook_secret, raw_body=raw_body, signature=signature):
            raise PaymentConflict("invalid webhook signature")
        try:
            obj=json.loads(bytes(raw_body).decode("utf-8"))
            event_type=obj["event"]
            entity=obj["payload"]["payment"]["entity"]
            payment_id=entity["id"]; order_id=entity["order_id"]; amount=entity["amount"]; currency=entity["currency"]; status=entity["status"]
            remote=RemotePayment(payment_id,order_id,amount,currency,status)
        except Exception as exc:
            raise PaymentConflict("malformed signed webhook") from exc
        expected_status={"payment.authorized":"authorized","payment.failed":"failed","payment.captured":"captured"}.get(event_type)
        if expected_status is None:
            raise PaymentConflict("unsupported webhook event")
        if remote.status != expected_status:
            raise PaymentConflict("webhook event/status mismatch")
        body_hash=hashlib.sha256(bytes(raw_body)).hexdigest()
        con=self._connect()
        try:
            self._begin(con)
            old=con.execute("SELECT * FROM webhook_inbox WHERE event_id=?",(event_id,)).fetchone()
            if old is not None:
                if old["body_hash"]!=body_hash: raise PaymentConflict("webhook event id reused with different body")
                con.commit(); return WebhookAcceptResult(False,True)
            con.execute("INSERT INTO webhook_inbox(event_id,body_hash,event_type,payment_id,remote_order_id,amount_paise,currency,payment_status,received_at_ms,processed_at_ms) VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                        (event_id,body_hash,event_type,remote.payment_id,remote.order_id,remote.amount_paise,remote.currency,remote.status,now_ms))
            con.commit(); return WebhookAcceptResult(True,False)
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def process_webhook(self, event_id: str, *, now_ms: int) -> PaymentState:
        _valid_now(now_ms)
        con=self._connect()
        try:
            self._begin(con)
            ev=con.execute("SELECT * FROM webhook_inbox WHERE event_id=?",(event_id,)).fetchone()
            if ev is None: raise PaymentNotFound("webhook")
            if ev["processed_at_ms"] is not None:
                p=con.execute("SELECT state FROM payment_attempts WHERE payment_id=?",(ev["payment_id"],)).fetchone()
                con.commit(); return PaymentState(p["state"]) if p else PaymentState.UNKNOWN
            i=con.execute("SELECT * FROM payment_order_intents WHERE remote_order_id=?",(ev["remote_order_id"],)).fetchone()
            if i is None: raise PaymentConflict("webhook references unknown order")
            if ev["amount_paise"]!=i["amount_paise"] or ev["currency"]!=i["currency"]:
                raise PaymentConflict("webhook payment binding mismatch")
            existing=con.execute("SELECT * FROM payment_attempts WHERE payment_id=?",(ev["payment_id"],)).fetchone()
            observed=remote_payment_state(ev["payment_status"])
            if existing is not None:
                if existing["local_order_id"]!=i["local_order_id"] or existing["remote_order_id"]!=ev["remote_order_id"] or existing["amount_paise"]!=ev["amount_paise"] or existing["currency"]!=ev["currency"]:
                    raise PaymentConflict("payment identity binding changed")
                current=PaymentState(existing["state"])
                merged=merge_payment_state(current,observed)
                if merged is not current:
                    if int(existing["version"])>=INT64_MAX: raise PaymentConflict("payment version exhausted")
                    con.execute("UPDATE payment_attempts SET state=?,version=version+1 WHERE payment_id=?",(merged.value,ev["payment_id"]))
            else:
                merged=observed
                con.execute("INSERT INTO payment_attempts(payment_id,local_order_id,remote_order_id,amount_paise,currency,state,version) VALUES(?,?,?,?,?,?,1)",
                            (ev["payment_id"],i["local_order_id"],ev["remote_order_id"],ev["amount_paise"],ev["currency"],merged.value))
            aggregate=self._aggregate_payment_state_locked(con,i["local_order_id"])
            self._apply_aggregate_locked(con,i,aggregate,now_ms=now_ms)
            con.execute("UPDATE webhook_inbox SET processed_at_ms=? WHERE event_id=?",(now_ms,event_id))
            con.commit(); return aggregate
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def _aggregate_payment_state_locked(self, con: sqlite3.Connection, local_order_id: str) -> PaymentState:
        rows=con.execute("SELECT state,payment_id FROM payment_attempts WHERE local_order_id=?",(local_order_id,)).fetchall()
        captured={r["payment_id"] for r in rows if r["state"]==PaymentState.CAPTURED.value}
        if len(captured)>1: raise PaymentConflict("multiple captured payment ids for one order")
        state=PaymentState.UNKNOWN
        for r in rows:
            state=merge_payment_state(state,PaymentState(r["state"]))
        return state

    def _apply_aggregate_locked(self, con: sqlite3.Connection, intent: sqlite3.Row, aggregate: PaymentState, *, now_ms: int) -> None:
        e=con.execute("SELECT * FROM executions WHERE execution_id=?",(intent["execution_id"],)).fetchone()
        h=con.execute("SELECT * FROM inventory_holds WHERE execution_id=?",(intent["execution_id"],)).fetchone()
        if e is None or h is None: raise PaymentConflict("broken payment projection graph")
        estate=ExecutionState(e["state"])
        if aggregate is PaymentState.CAPTURED:
            if int(intent["version"])>=INT64_MAX: raise PaymentConflict("intent version exhausted")
            if intent["state"] != OrderIntentState.PAID.value:
                con.execute("UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? WHERE local_order_id=?",
                            (OrderIntentState.PAID.value,now_ms,intent["local_order_id"]))
            self._apply_captured_locked(con,execution=e,hold=h,now_ms=now_ms)
            return
        if estate in {ExecutionState.SUCCEEDED,ExecutionState.COMPENSATION_REQUIRED,ExecutionState.RECONCILED_FAILED}:
            return
        target=ExecutionState.RECONCILING if aggregate in {PaymentState.AUTHORIZED,PaymentState.UNCERTAIN,PaymentState.OBSERVED_FAILED,PaymentState.CREATED} else ExecutionState.EXECUTING
        if estate is not target:
            self._transition_execution_locked(con,e,target)

    def confirm_checkout(self, *, local_order_id: str, payment_id: str, signature: str, key_secret: str, now_ms: int) -> bool:
        from .razorpay import verify_checkout_signature
        _valid_now(now_ms)
        i=self.intent(local_order_id)
        if i.remote_order_id is None: raise PaymentConflict("order has no remote binding")
        RemotePayment(payment_id, i.remote_order_id, i.amount_paise, i.currency, "authorized")
        if not verify_checkout_signature(key_secret=key_secret,server_order_id=i.remote_order_id,payment_id=payment_id,signature=signature):
            raise PaymentConflict("invalid checkout signature")
        con=self._connect()
        try:
            self._begin(con)
            fresh=con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?",(local_order_id,)).fetchone()
            if fresh is None or fresh["remote_order_id"]!=i.remote_order_id: raise PaymentConflict("checkout order binding changed")
            old=con.execute("SELECT * FROM payment_attempts WHERE payment_id=?",(payment_id,)).fetchone()
            if old is None:
                con.execute("INSERT INTO payment_attempts(payment_id,local_order_id,remote_order_id,amount_paise,currency,state,version) VALUES(?,?,?,?,?,?,1)",
                            (payment_id,local_order_id,i.remote_order_id,i.amount_paise,i.currency,PaymentState.AUTHORIZED.value))
            elif old["local_order_id"]!=local_order_id or old["remote_order_id"]!=i.remote_order_id:
                raise PaymentConflict("checkout payment id reused")
            aggregate=self._aggregate_payment_state_locked(con,local_order_id)
            self._apply_aggregate_locked(con,fresh,aggregate,now_ms=now_ms)
            con.commit(); return True
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def apply_reconciliation(self, *, local_order_id: str, expected_intent_version: int, remote_order: RemoteOrder,
                             remote_payments: Iterable[RemotePayment], now_ms: int) -> PaymentState:
        _valid_now(now_ms); remote_order.__post_init__()
        payments=list(remote_payments)
        for p in payments: p.__post_init__()
        con=self._connect()
        try:
            self._begin(con)
            i=con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?",(local_order_id,)).fetchone()
            if i is None: raise PaymentNotFound("order intent")
            if i["version"]!=expected_intent_version: raise PaymentConflict("order intent changed during reconciliation")
            if i["remote_order_id"]!=remote_order.order_id or i["receipt"]!=remote_order.receipt or i["amount_paise"]!=remote_order.amount_paise or i["currency"]!=remote_order.currency:
                raise PaymentConflict("remote order reconciliation binding mismatch")
            for p in payments:
                if p.order_id!=remote_order.order_id or p.amount_paise!=i["amount_paise"] or p.currency!=i["currency"]:
                    raise PaymentConflict("remote payment reconciliation binding mismatch")
                old=con.execute("SELECT * FROM payment_attempts WHERE payment_id=?",(p.payment_id,)).fetchone()
                obs=remote_payment_state(p.status)
                if old is None:
                    con.execute("INSERT INTO payment_attempts(payment_id,local_order_id,remote_order_id,amount_paise,currency,state,version) VALUES(?,?,?,?,?,?,1)",
                                (p.payment_id,local_order_id,remote_order.order_id,p.amount_paise,p.currency,obs.value))
                else:
                    if old["local_order_id"]!=local_order_id or old["remote_order_id"]!=remote_order.order_id or old["amount_paise"]!=p.amount_paise or old["currency"]!=p.currency:
                        raise PaymentConflict("persisted payment binding mismatch")
                    merged=merge_payment_state(PaymentState(old["state"]),obs)
                    if merged.value!=old["state"]:
                        if int(old["version"])>=INT64_MAX: raise PaymentConflict("payment version exhausted")
                        con.execute("UPDATE payment_attempts SET state=?,version=version+1 WHERE payment_id=?",(merged.value,p.payment_id))
            # A bound remote Order marked paid is high-strength captured evidence.  The
            # payment-list read may be temporarily incomplete, so never release stock
            # merely because that secondary read is empty.
            aggregate=self._aggregate_payment_state_locked(con,local_order_id)
            if remote_order.status == "paid":
                aggregate=merge_payment_state(aggregate,PaymentState.CAPTURED)
            self._apply_aggregate_locked(con,i,aggregate,now_ms=now_ms)
            con.commit(); return aggregate
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def release_checkout_hold_after_reconcile(self, *, local_order_id: str, expected_intent_version: int, now_ms: int) -> None:
        _valid_now(now_ms)
        con=self._connect()
        try:
            self._begin(con)
            i=con.execute("SELECT * FROM payment_order_intents WHERE local_order_id=?",(local_order_id,)).fetchone()
            if i is None: raise PaymentNotFound("order intent")
            if i["version"]!=expected_intent_version: raise PaymentConflict("intent changed before checkout close")
            aggregate=self._aggregate_payment_state_locked(con,local_order_id)
            if aggregate is PaymentState.CAPTURED: raise PaymentConflict("cannot release inventory after capture")
            h=con.execute("SELECT * FROM inventory_holds WHERE execution_id=?",(i["execution_id"],)).fetchone()
            e=con.execute("SELECT * FROM executions WHERE execution_id=?",(i["execution_id"],)).fetchone()
            if h is None or e is None: raise PaymentConflict("broken checkout close graph")
            if h["state"] != InventoryHoldState.HELD.value: raise PaymentConflict("inventory hold is not releasable")
            if now_ms < int(h["hold_until_ms"]): raise PaymentConflict("checkout window has not expired")
            if int(i["version"])>=INT64_MAX: raise PaymentConflict("intent version exhausted")
            self._release_hold_locked(con,h,now_ms=now_ms)
            self._transition_execution_locked(con,e,ExecutionState.RECONCILED_FAILED)
            con.execute("UPDATE payment_order_intents SET state=?,version=version+1,updated_at_ms=? WHERE local_order_id=?",
                        (OrderIntentState.RECONCILED_FAILED.value,now_ms,local_order_id))
            con.commit()
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def scalar(self, sql: str, params: tuple=()):
        con=self._connect()
        try:
            row=con.execute(sql,params).fetchone(); return None if row is None else row[0]
        finally: con.close()
