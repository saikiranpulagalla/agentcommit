from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path
from typing import Callable

from agentcommit.domain.models import (
    INT64_MAX, AuthorizationMode, PlanState,
    DelegationGrant, DelegationState, DomainError, DomainSnapshot,
    ExecutionGrant, ExecutionRecord, ExecutionState,
    GrantState, MerchantQuote, MerchantReservation,
    PaymentProjection, PaymentState, ReservationState,
)
from agentcommit.domain.policy import evaluate_commit
from agentcommit.payments.razorpay import deterministic_receipt
from agentcommit.payments.models import DispatchState, InventoryHoldState


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    pass


class NotFound(StoreError):
    pass


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    request_id: str
    grant_id: str
    execution_id: str
    reservation_id: str
    delegation_id: str
    amount_paise: int
    currency: str


FaultHook = Callable[[str], None]

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS products(
  merchant_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  category TEXT NOT NULL,
  currency TEXT NOT NULL,
  price_paise INTEGER NOT NULL CHECK(price_paise > 0),
  price_revision INTEGER NOT NULL CHECK(price_revision > 0),
  available_quantity INTEGER NOT NULL CHECK(available_quantity >= 0),
  PRIMARY KEY(merchant_id, sku)
);
CREATE TABLE IF NOT EXISTS quotes(
  quote_id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  category TEXT NOT NULL,
  currency TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  price_revision INTEGER NOT NULL CHECK(price_revision > 0),
  quote_revision INTEGER NOT NULL CHECK(quote_revision > 0),
  FOREIGN KEY(merchant_id, sku) REFERENCES products(merchant_id, sku)
);
CREATE TABLE IF NOT EXISTS delegations(
  delegation_id TEXT PRIMARY KEY,
  buyer_id TEXT NOT NULL,
  merchant_id TEXT NOT NULL,
  category TEXT NOT NULL,
  max_amount_paise INTEGER NOT NULL CHECK(max_amount_paise > 0),
  currency TEXT NOT NULL,
  max_quantity INTEGER NOT NULL CHECK(max_quantity > 0),
  expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > 0),
  state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  plan_generation INTEGER NOT NULL DEFAULT 0 CHECK(plan_generation >= 0),
  mode TEXT NOT NULL DEFAULT 'DELEGATED',
  exact_sku TEXT NULL,
  exact_amount_paise INTEGER NULL CHECK(exact_amount_paise IS NULL OR exact_amount_paise > 0),
  substitution_allowed INTEGER NOT NULL DEFAULT 1 CHECK(substitution_allowed IN (0,1))
);
CREATE TABLE IF NOT EXISTS reservations(
  reservation_id TEXT PRIMARY KEY,
  quote_id TEXT NOT NULL,
  merchant_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  category TEXT NOT NULL,
  currency TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  quote_revision INTEGER NOT NULL CHECK(quote_revision > 0),
  revision INTEGER NOT NULL CHECK(revision > 0),
  expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > 0),
  state TEXT NOT NULL,
  FOREIGN KEY(quote_id) REFERENCES quotes(quote_id),
  FOREIGN KEY(merchant_id, sku) REFERENCES products(merchant_id, sku)
);
CREATE TABLE IF NOT EXISTS executions(
  execution_id TEXT PRIMARY KEY,
  buyer_id TEXT NOT NULL,
  state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0)
);
CREATE TABLE IF NOT EXISTS execution_grants(
  grant_id TEXT PRIMARY KEY,
  delegation_id TEXT NOT NULL,
  expected_delegation_version INTEGER NOT NULL CHECK(expected_delegation_version > 0),
  expected_buyer_id TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  expected_quote_id TEXT NOT NULL,
  expected_merchant_id TEXT NOT NULL,
  expected_category TEXT NOT NULL,
  expected_sku TEXT NOT NULL,
  expected_amount_paise INTEGER NOT NULL CHECK(expected_amount_paise > 0),
  expected_currency TEXT NOT NULL,
  expected_quantity INTEGER NOT NULL CHECK(expected_quantity > 0),
  expected_quote_revision INTEGER NOT NULL CHECK(expected_quote_revision > 0),
  expected_reservation_revision INTEGER NOT NULL CHECK(expected_reservation_revision > 0),
  state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  expected_plan_generation INTEGER NOT NULL DEFAULT 0 CHECK(expected_plan_generation >= 0),
  execution_id TEXT NOT NULL UNIQUE,
  FOREIGN KEY(delegation_id) REFERENCES delegations(delegation_id),
  FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id),
  FOREIGN KEY(expected_quote_id) REFERENCES quotes(quote_id),
  FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
);

CREATE TABLE IF NOT EXISTS plans(
  plan_id TEXT PRIMARY KEY,
  delegation_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK(generation > 0),
  quote_id TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  grant_id TEXT NOT NULL UNIQUE,
  sku TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
  state TEXT NOT NULL,
  prior_plan_id TEXT NULL,
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms > 0),
  UNIQUE(delegation_id, generation),
  FOREIGN KEY(delegation_id) REFERENCES delegations(delegation_id),
  FOREIGN KEY(quote_id) REFERENCES quotes(quote_id),
  FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id),
  FOREIGN KEY(grant_id) REFERENCES execution_grants(grant_id),
  FOREIGN KEY(prior_plan_id) REFERENCES plans(plan_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_grant_reservation ON execution_grants(reservation_id);

CREATE TABLE IF NOT EXISTS commit_receipts(
  request_id TEXT PRIMARY KEY,
  grant_id TEXT NOT NULL UNIQUE,
  execution_id TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  delegation_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
  currency TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms > 0),
  FOREIGN KEY(grant_id) REFERENCES execution_grants(grant_id)
);
CREATE TABLE IF NOT EXISTS payment_dispatch_outbox(
  execution_id TEXT PRIMARY KEY,
  receipt TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms > 0),
  FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
);
CREATE TABLE IF NOT EXISTS inventory_holds(
  execution_id TEXT PRIMARY KEY,
  reservation_id TEXT NOT NULL UNIQUE,
  merchant_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  state TEXT NOT NULL,
  hold_until_ms INTEGER NOT NULL CHECK(hold_until_ms > 0),
  version INTEGER NOT NULL CHECK(version > 0),
  FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
  FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id),
  FOREIGN KEY(merchant_id, sku) REFERENCES products(merchant_id, sku)
);
CREATE TABLE IF NOT EXISTS payment_order_intents(
  local_order_id TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL UNIQUE,
  receipt TEXT NOT NULL UNIQUE,
  amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
  currency TEXT NOT NULL,
  state TEXT NOT NULL,
  remote_order_id TEXT NULL UNIQUE,
  version INTEGER NOT NULL CHECK(version > 0),
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms > 0),
  updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms > 0),
  FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
);
CREATE TABLE IF NOT EXISTS payment_attempts(
  payment_id TEXT PRIMARY KEY,
  local_order_id TEXT NOT NULL,
  remote_order_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
  currency TEXT NOT NULL,
  state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  FOREIGN KEY(local_order_id) REFERENCES payment_order_intents(local_order_id)
);
CREATE TABLE IF NOT EXISTS webhook_inbox(
  event_id TEXT PRIMARY KEY,
  body_hash TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payment_id TEXT NULL,
  remote_order_id TEXT NULL,
  amount_paise INTEGER NULL,
  currency TEXT NULL,
  payment_status TEXT NULL,
  received_at_ms INTEGER NOT NULL CHECK(received_at_ms > 0),
  processed_at_ms INTEGER NULL CHECK(processed_at_ms IS NULL OR processed_at_ms > 0)
);
CREATE TABLE IF NOT EXISTS audit_events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms > 0)
);
"""


class MerchantStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        con = self._connect()
        try:
            con.executescript(SCHEMA)
            con.commit()
        finally:
            con.close()

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
    def _fault(hook: FaultHook | None, stage: str) -> None:
        if hook is not None:
            hook(stage)

    @staticmethod
    def _valid_now(now_ms: int) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or not (1 <= now_ms <= INT64_MAX):
            raise DomainError("invalid now_ms")

    def add_product(self, *, merchant_id: str, sku: str, category: str, currency: str,
                    price_paise: int, available_quantity: int) -> None:
        MerchantQuote("validate", merchant_id, category, sku, price_paise, currency, 1, 1, 1)
        if isinstance(available_quantity, bool) or not isinstance(available_quantity, int) or not (0 <= available_quantity <= INT64_MAX):
            raise DomainError("available_quantity must be a nonnegative int64")
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO products(merchant_id,sku,category,currency,price_paise,price_revision,available_quantity) VALUES(?,?,?,?,?,?,?)",
                (merchant_id, sku, category, currency, price_paise, 1, available_quantity),
            )
        finally:
            con.close()

    def product(self, merchant_id: str, sku: str) -> sqlite3.Row:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM products WHERE merchant_id=? AND sku=?", (merchant_id, sku)).fetchone()
            if row is None:
                raise NotFound("product")
            return row
        finally:
            con.close()

    def change_price(self, *, merchant_id: str, sku: str, new_price_paise: int) -> int:
        # Validate token/currency shape through current row and positive money explicitly.
        if isinstance(new_price_paise, bool) or not isinstance(new_price_paise, int) or not (1 <= new_price_paise <= INT64_MAX):
            raise DomainError("invalid price")
        con = self._connect()
        try:
            self._begin(con)
            row = con.execute("SELECT * FROM products WHERE merchant_id=? AND sku=?", (merchant_id, sku)).fetchone()
            if row is None:
                raise NotFound("product")
            cur = int(row["price_revision"])
            if cur >= INT64_MAX:
                raise Conflict("price revision exhausted")
            con.execute(
                "UPDATE products SET price_paise=?, price_revision=? WHERE merchant_id=? AND sku=? AND price_revision=?",
                (new_price_paise, cur + 1, merchant_id, sku, cur),
            )
            con.commit()
            return cur + 1
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def create_quote(self, *, quote_id: str, merchant_id: str, sku: str, quantity: int = 1) -> MerchantQuote:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not (1 <= quantity <= INT64_MAX):
            raise DomainError("invalid quantity")
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM products WHERE merchant_id=? AND sku=?", (merchant_id, sku)).fetchone()
            if row is None:
                raise NotFound("product")
            total = int(row["price_paise"]) * quantity
            if total > INT64_MAX:
                raise DomainError("quote amount overflow")
            q = MerchantQuote(
                quote_id=quote_id, merchant_id=merchant_id, category=row["category"], sku=sku,
                amount_paise=total, currency=row["currency"], quantity=quantity,
                price_revision=row["price_revision"], quote_revision=1,
            )
            con.execute(
                "INSERT INTO quotes(quote_id,merchant_id,sku,category,currency,amount_paise,quantity,price_revision,quote_revision) VALUES(?,?,?,?,?,?,?,?,?)",
                (q.quote_id, q.merchant_id, q.sku, q.category, q.currency, q.amount_paise,
                 q.quantity, q.price_revision, q.quote_revision),
            )
            return q
        finally:
            con.close()

    @staticmethod
    def _delegation_from_row(d: sqlite3.Row) -> DelegationGrant:
        return DelegationGrant(
            d["delegation_id"], d["buyer_id"], d["merchant_id"], d["category"], d["max_amount_paise"],
            d["currency"], d["max_quantity"], d["expires_at_ms"], DelegationState(d["state"]), d["version"],
            d["plan_generation"], AuthorizationMode(d["mode"]), d["exact_sku"], d["exact_amount_paise"], bool(d["substitution_allowed"]),
        )

    @staticmethod
    def _quote_from_row(q: sqlite3.Row) -> MerchantQuote:
        return MerchantQuote(q["quote_id"], q["merchant_id"], q["category"], q["sku"], q["amount_paise"],
                             q["currency"], q["quantity"], q["price_revision"], q["quote_revision"])

    def create_delegation(self, d: DelegationGrant) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO delegations(delegation_id,buyer_id,merchant_id,category,max_amount_paise,currency,max_quantity,expires_at_ms,state,version,plan_generation,mode,exact_sku,exact_amount_paise,substitution_allowed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.delegation_id, d.buyer_id, d.merchant_id, d.category, d.max_amount_paise,
                 d.currency, d.max_quantity, d.expires_at_ms, d.status.value, d.version, d.plan_generation, d.mode.value, d.exact_sku, d.exact_amount_paise, int(d.substitution_allowed)),
            )
        finally:
            con.close()

    def reserve(self, *, reservation_id: str, quote_id: str, now_ms: int, ttl_ms: int) -> MerchantReservation:
        self._valid_now(now_ms)
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms <= 0:
            raise DomainError("invalid ttl_ms")
        expires = now_ms + ttl_ms
        if expires > INT64_MAX:
            raise DomainError("reservation expiry overflow")
        con = self._connect()
        try:
            self._begin(con)
            q = con.execute("SELECT * FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
            if q is None:
                raise NotFound("quote")
            p = con.execute("SELECT * FROM products WHERE merchant_id=? AND sku=?", (q["merchant_id"], q["sku"])).fetchone()
            if p is None:
                raise NotFound("product")
            if int(p["price_revision"]) != int(q["price_revision"]) or int(p["price_paise"]) * int(q["quantity"]) != int(q["amount_paise"]):
                raise Conflict("stale quote")
            rc = con.execute(
                "UPDATE products SET available_quantity=available_quantity-? WHERE merchant_id=? AND sku=? AND available_quantity>=?",
                (q["quantity"], q["merchant_id"], q["sku"], q["quantity"]),
            ).rowcount
            if rc != 1:
                raise Conflict("insufficient inventory")
            r = MerchantReservation(
                reservation_id=reservation_id, quote_id=q["quote_id"], merchant_id=q["merchant_id"],
                category=q["category"], sku=q["sku"], amount_paise=q["amount_paise"], currency=q["currency"],
                quantity=q["quantity"], quote_revision=q["quote_revision"], revision=1,
                expires_at_ms=expires, status=ReservationState.ACTIVE,
            )
            con.execute(
                "INSERT INTO reservations(reservation_id,quote_id,merchant_id,sku,category,currency,amount_paise,quantity,quote_revision,revision,expires_at_ms,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.reservation_id, r.quote_id, r.merchant_id, r.sku, r.category, r.currency,
                 r.amount_paise, r.quantity, r.quote_revision, r.revision, r.expires_at_ms, r.status.value),
            )
            con.commit()
            return r
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def _release_reservation_locked(self, con: sqlite3.Connection, reservation_id: str, *, terminal: ReservationState) -> None:
        row = con.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            raise NotFound("reservation")
        if row["state"] != ReservationState.ACTIVE.value:
            return
        if int(row["revision"]) >= INT64_MAX:
            raise Conflict("reservation revision exhausted")
        max_grant = con.execute(
            "SELECT 1 FROM execution_grants WHERE reservation_id=? AND state=? AND version>=? LIMIT 1",
            (reservation_id, GrantState.ACTIVE.value, INT64_MAX),
        ).fetchone()
        if max_grant is not None:
            raise Conflict("linked grant version exhausted")
        con.execute(
            "UPDATE reservations SET state=?, revision=revision+1 WHERE reservation_id=? AND state=?",
            (terminal.value, reservation_id, ReservationState.ACTIVE.value),
        )
        con.execute(
            "UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
            (row["quantity"], row["merchant_id"], row["sku"]),
        )
        con.execute(
            "UPDATE execution_grants SET state=?, version=version+1 WHERE reservation_id=? AND state=? AND version<?",
            (GrantState.REVOKED.value, reservation_id, GrantState.ACTIVE.value, INT64_MAX),
        )

    def cancel_reservation(self, reservation_id: str, *, now_ms: int) -> None:
        self._valid_now(now_ms)
        con = self._connect()
        try:
            self._begin(con)
            self._release_reservation_locked(con, reservation_id, terminal=ReservationState.CANCELLED)
            con.execute("UPDATE plans SET state=? WHERE reservation_id=? AND state=?",
                        (PlanState.CANCELLED.value, reservation_id, PlanState.ACTIVE.value))
            con.execute("INSERT INTO audit_events(event_type,object_id,created_at_ms) VALUES(?,?,?)", ("RESERVATION_CANCELLED", reservation_id, now_ms))
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def issue_grant(self, *, grant_id: str, execution_id: str, delegation_id: str, reservation_id: str, now_ms: int) -> ExecutionGrant:
        self._valid_now(now_ms)
        con = self._connect()
        try:
            self._begin(con)
            d = con.execute("SELECT * FROM delegations WHERE delegation_id=?", (delegation_id,)).fetchone()
            r = con.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if d is None:
                raise NotFound("delegation")
            if r is None:
                raise NotFound("reservation")
            dg = self._delegation_from_row(d)
            if d["state"] != DelegationState.ACTIVE.value or now_ms >= int(d["expires_at_ms"]):
                raise Conflict("delegation inactive/expired")
            if r["state"] != ReservationState.ACTIVE.value or now_ms >= int(r["expires_at_ms"]):
                if r["state"] == ReservationState.ACTIVE.value and now_ms >= int(r["expires_at_ms"]):
                    self._release_reservation_locked(con, reservation_id, terminal=ReservationState.EXPIRED)
                    con.commit()
                raise Conflict("reservation inactive/expired")
            q = con.execute("SELECT * FROM quotes WHERE quote_id=?", (r["quote_id"],)).fetchone()
            if q is None:
                raise NotFound("quote")
            self._quote_from_row(q)
            MerchantReservation(
                r["reservation_id"], r["quote_id"], r["merchant_id"], r["category"], r["sku"],
                r["amount_paise"], r["currency"], r["quantity"], r["quote_revision"], r["revision"],
                r["expires_at_ms"], ReservationState(r["state"]),
            )
            # Authority/resource checks happen at grant issuance and again at commit.
            if r["merchant_id"] != d["merchant_id"] or r["category"] != d["category"] or r["currency"] != d["currency"]:
                raise Conflict("delegation scope mismatch")
            if int(r["amount_paise"]) > int(d["max_amount_paise"]) or int(r["quantity"]) > int(d["max_quantity"]):
                raise Conflict("delegation budget/quantity exceeded")
            if dg.mode is AuthorizationMode.EXACT and (r["sku"] != dg.exact_sku or int(r["amount_paise"]) != dg.exact_amount_paise):
                raise Conflict("exact authority mismatch")
            if con.execute("SELECT 1 FROM execution_grants WHERE reservation_id=? LIMIT 1", (reservation_id,)).fetchone() is not None:
                raise Conflict("reservation already has execution grant")
            con.execute("INSERT INTO executions(execution_id,buyer_id,state,version) VALUES(?,?,?,?)",
                        (execution_id, d["buyer_id"], ExecutionState.PLANNED.value, 1))
            g = ExecutionGrant(
                grant_id=grant_id,
                delegation_id=delegation_id,
                expected_delegation_version=d["version"],
                expected_buyer_id=d["buyer_id"],
                reservation_id=reservation_id,
                expected_quote_id=q["quote_id"],
                expected_merchant_id=r["merchant_id"],
                expected_category=r["category"],
                expected_sku=r["sku"],
                expected_amount_paise=r["amount_paise"],
                expected_currency=r["currency"],
                expected_quantity=r["quantity"],
                expected_quote_revision=r["quote_revision"],
                expected_reservation_revision=r["revision"],
                status=GrantState.ACTIVE,
                version=1,
                expected_plan_generation=d["plan_generation"],
            )
            con.execute(
                "INSERT INTO execution_grants(grant_id,delegation_id,expected_delegation_version,expected_buyer_id,reservation_id,expected_quote_id,expected_merchant_id,expected_category,expected_sku,expected_amount_paise,expected_currency,expected_quantity,expected_quote_revision,expected_reservation_revision,state,version,expected_plan_generation,execution_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (g.grant_id, g.delegation_id, g.expected_delegation_version, g.expected_buyer_id,
                 g.reservation_id, g.expected_quote_id, g.expected_merchant_id, g.expected_category,
                 g.expected_sku, g.expected_amount_paise, g.expected_currency, g.expected_quantity,
                 g.expected_quote_revision, g.expected_reservation_revision, g.status.value, g.version, g.expected_plan_generation, execution_id),
            )
            con.commit()
            return g
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def revoke_delegation(self, delegation_id: str, *, now_ms: int) -> None:
        self._valid_now(now_ms)
        con = self._connect()
        try:
            self._begin(con)
            d = con.execute("SELECT * FROM delegations WHERE delegation_id=?", (delegation_id,)).fetchone()
            if d is None:
                raise NotFound("delegation")
            self._delegation_from_row(d)
            if d["state"] != DelegationState.ACTIVE.value:
                con.commit()
                return
            if int(d["version"]) >= INT64_MAX:
                raise Conflict("delegation version exhausted")
            rows = con.execute(
                "SELECT g.reservation_id,g.state AS gstate,g.version AS gversion,r.state AS rstate,r.revision AS rrevision "
                "FROM execution_grants g JOIN reservations r ON r.reservation_id=g.reservation_id "
                "WHERE g.delegation_id=?",
                (delegation_id,),
            ).fetchall()
            for row in rows:
                if row["gstate"] == GrantState.ACTIVE.value and int(row["gversion"]) >= INT64_MAX:
                    raise Conflict("child grant version exhausted")
                if row["rstate"] == ReservationState.ACTIVE.value and int(row["rrevision"]) >= INT64_MAX:
                    raise Conflict("child reservation revision exhausted")
            if con.execute(
                "UPDATE delegations SET state=?, version=version+1 WHERE delegation_id=? AND state=? AND version=?",
                (DelegationState.REVOKED.value, delegation_id, DelegationState.ACTIVE.value, d["version"]),
            ).rowcount != 1:
                raise Conflict("delegation CAS lost")
            for reservation_id in dict.fromkeys(row["reservation_id"] for row in rows):
                self._release_reservation_locked(con, reservation_id, terminal=ReservationState.CANCELLED)
            con.execute("UPDATE plans SET state=? WHERE delegation_id=? AND state=?",
                        (PlanState.CANCELLED.value, delegation_id, PlanState.ACTIVE.value))
            con.execute("INSERT INTO audit_events(event_type,object_id,created_at_ms) VALUES(?,?,?)", ("DELEGATION_REVOKED", delegation_id, now_ms))
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()


    def activate_plan_from_quote(
        self, *, plan_id: str, grant_id: str, execution_id: str, reservation_id: str,
        delegation_id: str, quote_id: str, now_ms: int, ttl_ms: int,
        fault_hook: FaultHook | None = None,
    ) -> ExecutionGrant:
        """Atomically activate a new plan generation and supersede the previous candidate.

        This is the V2 replan boundary: current authority + current quote + inventory hold +
        plan generation + execution grant move together or not at all.
        """
        self._valid_now(now_ms)
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms <= 0:
            raise DomainError("invalid ttl_ms")
        expires = now_ms + ttl_ms
        if expires > INT64_MAX:
            raise DomainError("reservation expiry overflow")
        con = self._connect()
        try:
            self._begin(con)
            d = con.execute("SELECT * FROM delegations WHERE delegation_id=?", (delegation_id,)).fetchone()
            q = con.execute("SELECT * FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
            if d is None:
                raise NotFound("delegation")
            if q is None:
                raise NotFound("quote")
            dg = self._delegation_from_row(d)
            self._quote_from_row(q)
            if d["state"] != DelegationState.ACTIVE.value or now_ms >= int(d["expires_at_ms"]):
                raise Conflict("delegation inactive/expired")
            if int(d["plan_generation"]) >= INT64_MAX:
                raise Conflict("plan generation exhausted")
            p = con.execute("SELECT * FROM products WHERE merchant_id=? AND sku=?", (q["merchant_id"], q["sku"])).fetchone()
            if p is None:
                raise NotFound("product")
            if int(p["price_revision"]) != int(q["price_revision"]) or int(p["price_paise"]) * int(q["quantity"]) != int(q["amount_paise"]):
                raise Conflict("stale quote")
            if q["merchant_id"] != d["merchant_id"] or q["category"] != d["category"] or q["currency"] != d["currency"]:
                raise Conflict("delegation scope mismatch")
            if int(q["amount_paise"]) > int(d["max_amount_paise"]) or int(q["quantity"]) > int(d["max_quantity"]):
                raise Conflict("delegation budget/quantity exceeded")
            mode = AuthorizationMode(d["mode"])
            if mode is AuthorizationMode.EXACT:
                if q["sku"] != d["exact_sku"] or int(q["amount_paise"]) != int(d["exact_amount_paise"]):
                    raise Conflict("exact authority mismatch")
            elif not bool(d["substitution_allowed"]):
                first = con.execute(
                    "SELECT sku FROM plans WHERE delegation_id=? ORDER BY generation ASC LIMIT 1", (delegation_id,)
                ).fetchone()
                if first is not None and first["sku"] != q["sku"]:
                    raise Conflict("substitution not allowed")

            new_generation = int(d["plan_generation"]) + 1
            active = con.execute(
                "SELECT p.*,g.state AS gstate,g.version AS gversion,r.state AS rstate,r.revision AS rrevision,r.quantity,r.merchant_id,r.sku AS rsku,r.expires_at_ms AS rexpires "
                "FROM plans p JOIN execution_grants g ON g.grant_id=p.grant_id "
                "JOIN reservations r ON r.reservation_id=p.reservation_id "
                "WHERE p.delegation_id=? AND p.state=? ORDER BY p.generation DESC",
                (delegation_id, PlanState.ACTIVE.value),
            ).fetchall()
            if len(active) > 1:
                raise Conflict("multiple active plans violate V2 lineage invariant")
            # Canonical identifiers are validated before any new state is written.
            for token in (plan_id, grant_id, execution_id, reservation_id):
                ExecutionRecord(token, d["buyer_id"])
            if active and active[0]["rstate"] == ReservationState.ACTIVE.value and now_ms >= int(active[0]["rexpires"]):
                old = active[0]
                if int(old["gversion"]) >= INT64_MAX or int(old["rrevision"]) >= INT64_MAX:
                    raise Conflict("expired path counter exhausted")
                con.execute("UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=? AND state=? AND version<?",
                            (GrantState.EXPIRED.value, old["grant_id"], GrantState.ACTIVE.value, INT64_MAX))
                con.execute("UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=? AND state=? AND revision<?",
                            (ReservationState.EXPIRED.value, old["reservation_id"], ReservationState.ACTIVE.value, INT64_MAX))
                con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
                            (old["quantity"], old["merchant_id"], old["rsku"]))
                con.execute("UPDATE plans SET state=? WHERE plan_id=?", (PlanState.CANCELLED.value, old["plan_id"]))
                con.commit()
                # Expiry is independent truth; retry the replan against the cleaned current state.
                return self.activate_plan_from_quote(
                    plan_id=plan_id, grant_id=grant_id, execution_id=execution_id, reservation_id=reservation_id,
                    delegation_id=delegation_id, quote_id=quote_id, now_ms=now_ms, ttl_ms=ttl_ms, fault_hook=fault_hook,
                )
            prior_plan_id = active[0]["plan_id"] if active else None
            transfer_hold = bool(
                active
                and active[0]["rstate"] == ReservationState.ACTIVE.value
                and active[0]["rsku"] == q["sku"]
                and int(active[0]["quantity"]) == int(q["quantity"])
            )

            # Cross-SKU replans acquire new inventory before old inventory is released.
            # Same-SKU/same-quantity replans atomically transfer the existing hold instead of demanding a second unit.
            if not transfer_hold and con.execute(
                "UPDATE products SET available_quantity=available_quantity-? WHERE merchant_id=? AND sku=? AND available_quantity>=?",
                (q["quantity"], q["merchant_id"], q["sku"], q["quantity"]),
            ).rowcount != 1:
                raise Conflict("insufficient inventory")
            reservation = MerchantReservation(
                reservation_id, q["quote_id"], q["merchant_id"], q["category"], q["sku"], q["amount_paise"],
                q["currency"], q["quantity"], q["quote_revision"], 1, expires, ReservationState.ACTIVE,
            )
            con.execute(
                "INSERT INTO reservations(reservation_id,quote_id,merchant_id,sku,category,currency,amount_paise,quantity,quote_revision,revision,expires_at_ms,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (reservation.reservation_id,reservation.quote_id,reservation.merchant_id,reservation.sku,reservation.category,
                 reservation.currency,reservation.amount_paise,reservation.quantity,reservation.quote_revision,
                 reservation.revision,reservation.expires_at_ms,reservation.status.value),
            )
            self._fault(fault_hook, "v2_after_new_reservation")

            # Supersede old candidates only after the new candidate is known valid/reserved.
            for old in active:
                if (old["gstate"] == GrantState.ACTIVE.value and int(old["gversion"]) >= INT64_MAX) or (old["rstate"] == ReservationState.ACTIVE.value and int(old["rrevision"]) >= INT64_MAX):
                    raise Conflict("superseded path counter exhausted")
                if old["gstate"] == GrantState.ACTIVE.value:
                    con.execute("UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=? AND state=? AND version<?",
                                (GrantState.REVOKED.value, old["grant_id"], GrantState.ACTIVE.value, INT64_MAX))
                if old["rstate"] == ReservationState.ACTIVE.value:
                    con.execute("UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=? AND state=? AND revision<?",
                                (ReservationState.CANCELLED.value, old["reservation_id"], ReservationState.ACTIVE.value, INT64_MAX))
                    if not transfer_hold:
                        con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
                                    (old["quantity"], old["merchant_id"], old["rsku"]))
                con.execute("UPDATE plans SET state=? WHERE plan_id=? AND state=?",
                            (PlanState.SUPERSEDED.value, old["plan_id"], PlanState.ACTIVE.value))
            # Also invalidate legacy/non-plan candidate grants under the same delegation so plan-generation
            # advancement cannot leak inventory through an orphaned V1 path.
            orphans = con.execute(
                "SELECT g.grant_id,g.reservation_id,g.state AS gstate,g.version AS gversion,r.state AS rstate,r.revision AS rrevision,r.quantity,r.merchant_id,r.sku "
                "FROM execution_grants g JOIN reservations r ON r.reservation_id=g.reservation_id "
                "WHERE g.delegation_id=? AND (g.state=? OR r.state=?) AND NOT EXISTS(SELECT 1 FROM plans p WHERE p.grant_id=g.grant_id)",
                (delegation_id, GrantState.ACTIVE.value, ReservationState.ACTIVE.value),
            ).fetchall()
            for old in orphans:
                if (old["gstate"]==GrantState.ACTIVE.value and int(old["gversion"]) >= INT64_MAX) or (old["rstate"]==ReservationState.ACTIVE.value and int(old["rrevision"])>=INT64_MAX):
                    raise Conflict("orphan path counter exhausted")
                if old["gstate"]==GrantState.ACTIVE.value:
                    con.execute("UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=? AND state=? AND version<?",
                                (GrantState.REVOKED.value,old["grant_id"],GrantState.ACTIVE.value,INT64_MAX))
                if old["rstate"]==ReservationState.ACTIVE.value:
                    con.execute("UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=? AND state=? AND revision<?",
                                (ReservationState.CANCELLED.value,old["reservation_id"],ReservationState.ACTIVE.value,INT64_MAX))
                    con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
                                (old["quantity"],old["merchant_id"],old["sku"]))
            self._fault(fault_hook, "v2_after_old_superseded")

            if con.execute(
                "UPDATE delegations SET plan_generation=? WHERE delegation_id=? AND state=? AND version=? AND plan_generation=?",
                (new_generation, delegation_id, DelegationState.ACTIVE.value, d["version"], d["plan_generation"]),
            ).rowcount != 1:
                raise Conflict("plan generation CAS lost")
            self._fault(fault_hook, "v2_after_generation")

            con.execute("INSERT INTO executions(execution_id,buyer_id,state,version) VALUES(?,?,?,1)",
                        (execution_id, d["buyer_id"], ExecutionState.PLANNED.value))
            grant = ExecutionGrant(
                grant_id=grant_id, delegation_id=delegation_id, expected_delegation_version=d["version"],
                expected_buyer_id=d["buyer_id"], reservation_id=reservation_id, expected_quote_id=q["quote_id"],
                expected_merchant_id=q["merchant_id"], expected_category=q["category"], expected_sku=q["sku"],
                expected_amount_paise=q["amount_paise"], expected_currency=q["currency"], expected_quantity=q["quantity"],
                expected_quote_revision=q["quote_revision"], expected_reservation_revision=1,
                status=GrantState.ACTIVE, version=1, expected_plan_generation=new_generation,
            )
            con.execute(
                "INSERT INTO execution_grants(grant_id,delegation_id,expected_delegation_version,expected_buyer_id,reservation_id,expected_quote_id,expected_merchant_id,expected_category,expected_sku,expected_amount_paise,expected_currency,expected_quantity,expected_quote_revision,expected_reservation_revision,state,version,expected_plan_generation,execution_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (grant.grant_id,grant.delegation_id,grant.expected_delegation_version,grant.expected_buyer_id,
                 grant.reservation_id,grant.expected_quote_id,grant.expected_merchant_id,grant.expected_category,
                 grant.expected_sku,grant.expected_amount_paise,grant.expected_currency,grant.expected_quantity,
                 grant.expected_quote_revision,grant.expected_reservation_revision,grant.status.value,grant.version,
                 grant.expected_plan_generation,execution_id),
            )
            con.execute(
                "INSERT INTO plans(plan_id,delegation_id,generation,quote_id,reservation_id,grant_id,sku,amount_paise,state,prior_plan_id,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (plan_id,delegation_id,new_generation,quote_id,reservation_id,grant_id,q["sku"],q["amount_paise"],
                 PlanState.ACTIVE.value,prior_plan_id,now_ms),
            )
            self._fault(fault_hook, "v2_after_plan")
            con.execute("INSERT INTO audit_events(event_type,object_id,created_at_ms) VALUES(?,?,?)",
                        ("PLAN_ACTIVATED", plan_id, now_ms))
            con.commit()
            return grant
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def tighten_delegation(
        self, delegation_id: str, *, now_ms: int,
        max_amount_paise: int | None = None, max_quantity: int | None = None,
        expires_at_ms: int | None = None, fault_hook: FaultHook | None = None,
    ) -> int:
        """Narrow authority only. Any broadening requires a fresh delegation ID."""
        self._valid_now(now_ms)
        con = self._connect()
        try:
            self._begin(con)
            d = con.execute("SELECT * FROM delegations WHERE delegation_id=?", (delegation_id,)).fetchone()
            if d is None:
                raise NotFound("delegation")
            self._delegation_from_row(d)
            if d["state"] != DelegationState.ACTIVE.value or now_ms >= int(d["expires_at_ms"]):
                raise Conflict("delegation inactive/expired")
            old_amount, old_qty, old_exp = int(d["max_amount_paise"]), int(d["max_quantity"]), int(d["expires_at_ms"])
            new_amount = old_amount if max_amount_paise is None else max_amount_paise
            new_qty = old_qty if max_quantity is None else max_quantity
            new_exp = old_exp if expires_at_ms is None else expires_at_ms
            # Strict integer validation and monotonic narrowing.
            for name, val in (("max_amount_paise",new_amount),("max_quantity",new_qty),("expires_at_ms",new_exp)):
                if isinstance(val,bool) or not isinstance(val,int) or not (1 <= val <= INT64_MAX):
                    raise DomainError(f"invalid {name}")
            if new_amount > old_amount or new_qty > old_qty or new_exp > old_exp:
                raise Conflict("authority broadening requires new delegation")
            if new_exp <= now_ms:
                raise Conflict("tightened authority must remain future-valid")
            if (new_amount,new_qty,new_exp)==(old_amount,old_qty,old_exp):
                con.commit(); return int(d["version"])
            if int(d["version"]) >= INT64_MAX:
                raise Conflict("delegation version exhausted")

            active = con.execute(
                "SELECT g.grant_id,g.reservation_id,g.state AS gstate,g.version AS gversion,r.state AS rstate,r.revision AS rrevision,r.quantity,r.merchant_id,r.sku "
                "FROM execution_grants g JOIN reservations r ON r.reservation_id=g.reservation_id "
                "WHERE g.delegation_id=?",
                (delegation_id,),
            ).fetchall()
            for old in active:
                if (old["gstate"]==GrantState.ACTIVE.value and int(old["gversion"]) >= INT64_MAX) or (old["rstate"]==ReservationState.ACTIVE.value and int(old["rrevision"])>=INT64_MAX):
                    raise Conflict("child counter exhausted")
                if old["gstate"]==GrantState.ACTIVE.value:
                    con.execute("UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=? AND state=?",
                                (GrantState.REVOKED.value,old["grant_id"],GrantState.ACTIVE.value))
                if old["rstate"]==ReservationState.ACTIVE.value:
                    con.execute("UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=? AND state=?",
                                (ReservationState.CANCELLED.value,old["reservation_id"],ReservationState.ACTIVE.value))
                    con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
                                (old["quantity"],old["merchant_id"],old["sku"]))
            con.execute("UPDATE plans SET state=? WHERE delegation_id=? AND state=?",
                        (PlanState.CANCELLED.value,delegation_id,PlanState.ACTIVE.value))
            self._fault(fault_hook,"v2_after_tighten_cleanup")
            if con.execute(
                "UPDATE delegations SET max_amount_paise=?,max_quantity=?,expires_at_ms=?,version=version+1 "
                "WHERE delegation_id=? AND state=? AND version=?",
                (new_amount,new_qty,new_exp,delegation_id,DelegationState.ACTIVE.value,d["version"]),
            ).rowcount != 1:
                raise Conflict("delegation CAS lost")
            self._fault(fault_hook,"v2_after_tighten_update")
            con.execute("INSERT INTO audit_events(event_type,object_id,created_at_ms) VALUES(?,?,?)",
                        ("DELEGATION_TIGHTENED",delegation_id,now_ms))
            con.commit()
            return int(d["version"])+1
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def expire_delegation(self, delegation_id: str, *, now_ms: int) -> bool:
        """Materialize expiry and release active candidate resources. Exact boundary: now >= expiry."""
        self._valid_now(now_ms)
        con=self._connect()
        try:
            self._begin(con)
            d=con.execute("SELECT * FROM delegations WHERE delegation_id=?",(delegation_id,)).fetchone()
            if d is None: raise NotFound("delegation")
            self._delegation_from_row(d)
            if d["state"] != DelegationState.ACTIVE.value:
                con.commit(); return False
            if now_ms < int(d["expires_at_ms"]):
                con.commit(); return False
            if int(d["version"]) >= INT64_MAX:
                raise Conflict("delegation version exhausted")
            rows=con.execute(
                "SELECT g.grant_id,g.reservation_id,g.state AS gstate,g.version AS gversion,r.state AS rstate,r.revision AS rrevision,r.quantity,r.merchant_id,r.sku "
                "FROM execution_grants g JOIN reservations r ON r.reservation_id=g.reservation_id "
                "WHERE g.delegation_id=?",(delegation_id,)).fetchall()
            for old in rows:
                if (old["gstate"]==GrantState.ACTIVE.value and int(old["gversion"]) >= INT64_MAX) or (old["rstate"]==ReservationState.ACTIVE.value and int(old["rrevision"])>=INT64_MAX):
                    raise Conflict("child counter exhausted")
                if old["gstate"]==GrantState.ACTIVE.value:
                    con.execute("UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=?",(GrantState.EXPIRED.value,old["grant_id"]))
                if old["rstate"]==ReservationState.ACTIVE.value:
                    con.execute("UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=?",(ReservationState.EXPIRED.value,old["reservation_id"]))
                    con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",(old["quantity"],old["merchant_id"],old["sku"]))
            con.execute("UPDATE plans SET state=? WHERE delegation_id=? AND state=?",
                        (PlanState.CANCELLED.value,delegation_id,PlanState.ACTIVE.value))
            if con.execute(
                "UPDATE delegations SET state=?,version=version+1 WHERE delegation_id=? AND state=? AND version=?",
                (DelegationState.EXPIRED.value,delegation_id,DelegationState.ACTIVE.value,d["version"]),
            ).rowcount != 1:
                raise Conflict("delegation CAS lost")
            con.commit(); return True
        except Exception:
            if con.in_transaction: con.rollback()
            raise
        finally: con.close()

    def _load_snapshot_locked(self, con: sqlite3.Connection, grant_id: str) -> DomainSnapshot:
        g = con.execute("SELECT * FROM execution_grants WHERE grant_id=?", (grant_id,)).fetchone()
        if g is None:
            raise NotFound("grant")
        d = con.execute("SELECT * FROM delegations WHERE delegation_id=?", (g["delegation_id"],)).fetchone()
        r = con.execute("SELECT * FROM reservations WHERE reservation_id=?", (g["reservation_id"],)).fetchone()
        q = con.execute("SELECT * FROM quotes WHERE quote_id=?", (g["expected_quote_id"],)).fetchone()
        e = con.execute("SELECT * FROM executions WHERE execution_id=?", (g["execution_id"],)).fetchone()
        if None in (d, r, q, e):
            raise Conflict("broken persistence graph")
        receipt_count = con.execute("SELECT COUNT(*) FROM commit_receipts WHERE grant_id=?", (grant_id,)).fetchone()[0]
        return DomainSnapshot(
            delegation=self._delegation_from_row(d),
            quote=self._quote_from_row(q),
            reservation=MerchantReservation(
                r["reservation_id"], r["quote_id"], r["merchant_id"], r["category"], r["sku"],
                r["amount_paise"], r["currency"], r["quantity"], r["quote_revision"], r["revision"],
                r["expires_at_ms"], ReservationState(r["state"]),
            ),
            grant=ExecutionGrant(
                g["grant_id"], g["delegation_id"], g["expected_delegation_version"], g["expected_buyer_id"],
                g["reservation_id"], g["expected_quote_id"], g["expected_merchant_id"], g["expected_category"],
                g["expected_sku"], g["expected_amount_paise"], g["expected_currency"], g["expected_quantity"],
                g["expected_quote_revision"], g["expected_reservation_revision"], GrantState(g["state"]), g["version"],
                g["expected_plan_generation"],
            ),
            execution=ExecutionRecord(e["execution_id"], e["buyer_id"], ExecutionState(e["state"]), e["version"]),
            payment=PaymentProjection(),
            commit_count=int(receipt_count),
        )

    def snapshot(self, grant_id: str) -> DomainSnapshot:
        con = self._connect()
        try:
            return self._load_snapshot_locked(con, grant_id)
        finally:
            con.close()

    def commit(self, *, request_id: str, grant_id: str, now_ms: int, fault_hook: FaultHook | None = None) -> CommitReceipt:
        self._valid_now(now_ms)
        # request_id is validated using grant token rules by constructing a temporary ExecutionRecord identifier.
        ExecutionRecord(request_id, "buyer")
        con = self._connect()
        try:
            self._begin(con)
            existing = con.execute("SELECT * FROM commit_receipts WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if existing["grant_id"] != grant_id:
                    raise Conflict("request_id reused for a different grant")
                con.commit()
                return CommitReceipt(existing["request_id"], existing["grant_id"], existing["execution_id"],
                                     existing["reservation_id"], existing["delegation_id"],
                                     existing["amount_paise"], existing["currency"])
            existing_grant = con.execute("SELECT * FROM commit_receipts WHERE grant_id=?", (grant_id,)).fetchone()
            if existing_grant is not None:
                # Same logical action, new request id: return existing durable result rather than create another side effect.
                con.commit()
                return CommitReceipt(existing_grant["request_id"], existing_grant["grant_id"], existing_grant["execution_id"],
                                     existing_grant["reservation_id"], existing_grant["delegation_id"],
                                     existing_grant["amount_paise"], existing_grant["currency"])

            r = con.execute(
                "SELECT r.* FROM reservations r JOIN execution_grants g ON g.reservation_id=r.reservation_id WHERE g.grant_id=?",
                (grant_id,),
            ).fetchone()
            if r is None:
                raise NotFound("grant/reservation")
            if r["state"] == ReservationState.ACTIVE.value and now_ms >= int(r["expires_at_ms"]):
                self._release_reservation_locked(con, r["reservation_id"], terminal=ReservationState.EXPIRED)
                con.commit()
                raise Conflict("reservation expired")

            snapshot = self._load_snapshot_locked(con, grant_id)
            decision = evaluate_commit(snapshot, now_ms=now_ms)
            if not decision.allowed:
                raise Conflict(decision.code.value)
            self._fault(fault_hook, "after_admission")

            d, g, rr, e = snapshot.delegation, snapshot.grant, snapshot.reservation, snapshot.execution
            if con.execute(
                "UPDATE delegations SET state=?,version=version+1 WHERE delegation_id=? AND state=? AND version=?",
                (DelegationState.CONSUMED.value, d.delegation_id, DelegationState.ACTIVE.value, d.version),
            ).rowcount != 1:
                raise Conflict("delegation CAS lost")
            self._fault(fault_hook, "after_delegation")

            if con.execute(
                "UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=? AND state=? AND version=?",
                (GrantState.CONSUMED.value, g.grant_id, GrantState.ACTIVE.value, g.version),
            ).rowcount != 1:
                raise Conflict("grant CAS lost")
            self._fault(fault_hook, "after_grant")

            if con.execute(
                "UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=? AND state=? AND revision=?",
                (ReservationState.CONSUMED.value, rr.reservation_id, ReservationState.ACTIVE.value, rr.revision),
            ).rowcount != 1:
                raise Conflict("reservation CAS lost")
            self._fault(fault_hook, "after_reservation")

            if con.execute(
                "UPDATE executions SET state=?,version=version+1 WHERE execution_id=? AND state=? AND version=?",
                (ExecutionState.CLAIMED.value, e.execution_id, ExecutionState.PLANNED.value, e.version),
            ).rowcount != 1:
                raise Conflict("execution CAS lost")
            self._fault(fault_hook, "after_execution")

            # V3.1: authority consumption and payment dispatch are one durable transaction.
            receipt = deterministic_receipt(e.execution_id)
            con.execute(
                "INSERT INTO payment_dispatch_outbox(execution_id,receipt,state,version,created_at_ms) VALUES(?,?,?,?,?)",
                (e.execution_id, receipt, DispatchState.PENDING.value, 1, now_ms),
            )
            self._fault(fault_hook, "after_payment_outbox")
            con.execute(
                "INSERT INTO inventory_holds(execution_id,reservation_id,merchant_id,sku,quantity,state,hold_until_ms,version) VALUES(?,?,?,?,?,?,?,?)",
                (e.execution_id, rr.reservation_id, rr.merchant_id, rr.sku, rr.quantity,
                 InventoryHoldState.HELD.value, rr.expires_at_ms, 1),
            )
            self._fault(fault_hook, "after_inventory_hold")

            # Losing candidate paths under the same one-shot delegation are cancelled/revoked atomically.
            siblings = con.execute(
                "SELECT g.grant_id,g.reservation_id,g.version AS gversion,r.quantity,r.merchant_id,r.sku,r.revision AS rrevision,r.state AS rstate,g.state AS gstate "
                "FROM execution_grants g JOIN reservations r ON r.reservation_id=g.reservation_id "
                "WHERE g.delegation_id=? AND g.grant_id<>?",
                (d.delegation_id, g.grant_id),
            ).fetchall()
            for sib in siblings:
                if (sib["gstate"] == GrantState.ACTIVE.value and int(sib["gversion"]) >= INT64_MAX) or (sib["rstate"] == ReservationState.ACTIVE.value and int(sib["rrevision"]) >= INT64_MAX):
                    raise Conflict("sibling path counter exhausted")
                if sib["gstate"] == GrantState.ACTIVE.value:
                    con.execute("UPDATE execution_grants SET state=?,version=version+1 WHERE grant_id=? AND state=? AND version<?",
                                (GrantState.REVOKED.value, sib["grant_id"], GrantState.ACTIVE.value, INT64_MAX))
                if sib["rstate"] == ReservationState.ACTIVE.value:
                    con.execute("UPDATE reservations SET state=?,revision=revision+1 WHERE reservation_id=? AND state=? AND revision<?",
                                (ReservationState.CANCELLED.value, sib["reservation_id"], ReservationState.ACTIVE.value, INT64_MAX))
                    con.execute("UPDATE products SET available_quantity=available_quantity+? WHERE merchant_id=? AND sku=?",
                                (sib["quantity"], sib["merchant_id"], sib["sku"]))
            # V2 plan lineage, if present.
            con.execute("UPDATE plans SET state=? WHERE grant_id=? AND state=?",
                        (PlanState.COMMITTED.value, g.grant_id, PlanState.ACTIVE.value))
            con.execute("UPDATE plans SET state=? WHERE delegation_id=? AND grant_id<>? AND state=?",
                        (PlanState.SUPERSEDED.value, d.delegation_id, g.grant_id, PlanState.ACTIVE.value))
            self._fault(fault_hook, "after_sibling_cleanup")

            con.execute(
                "INSERT INTO commit_receipts(request_id,grant_id,execution_id,reservation_id,delegation_id,amount_paise,currency,created_at_ms) VALUES(?,?,?,?,?,?,?,?)",
                (request_id, g.grant_id, e.execution_id, rr.reservation_id, d.delegation_id,
                 g.expected_amount_paise, g.expected_currency, now_ms),
            )
            self._fault(fault_hook, "after_receipt")
            con.execute("INSERT INTO audit_events(event_type,object_id,created_at_ms) VALUES(?,?,?)", ("COMMIT_ADMITTED", g.grant_id, now_ms))
            self._fault(fault_hook, "after_audit")
            con.commit()
            return CommitReceipt(request_id, g.grant_id, e.execution_id, rr.reservation_id, d.delegation_id,
                                 g.expected_amount_paise, g.expected_currency)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def scalar(self, sql: str, params: tuple = ()) -> int | str | None:
        con = self._connect()
        try:
            row = con.execute(sql, params).fetchone()
            return None if row is None else row[0]
        finally:
            con.close()
