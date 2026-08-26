from __future__ import annotations

from dataclasses import dataclass

from agentcommit.domain.models import DomainError, PaymentState
from .models import OrderIntentState
from .razorpay import AmbiguousRemoteOutcome, DefiniteRemoteRejection, RazorpayGateway
from .store import PaymentConflict, PaymentStore


@dataclass(slots=True)
class PaymentService:
    store: PaymentStore
    gateway: RazorpayGateway
    checkout_ttl_ms: int = 10 * 60 * 1000

    def __post_init__(self) -> None:
        if isinstance(self.checkout_ttl_ms,bool) or not isinstance(self.checkout_ttl_ms,int) or self.checkout_ttl_ms<=0:
            raise DomainError("checkout_ttl_ms must be positive")

    def dispatch_pending(self, *, now_ms: int, limit: int = 100) -> list[str]:
        results=[]
        for d in self.store.pending_dispatches(limit):
            intent=self.store.prepare_from_dispatch(d.execution_id,now_ms=now_ms)
            try:
                claimed=self.store.claim_remote_create(intent.local_order_id,now_ms=now_ms,checkout_hold_until_ms=now_ms+self.checkout_ttl_ms)
            except PaymentConflict:
                continue
            try:
                remote=self.gateway.create_order(amount_paise=claimed.amount_paise,currency=claimed.currency,receipt=claimed.receipt)
            except DefiniteRemoteRejection:
                self.store.mark_create_failed(claimed.local_order_id,now_ms=now_ms)
                results.append("CREATE_FAILED")
                continue
            except AmbiguousRemoteOutcome:
                self.store.mark_create_unknown(claimed.local_order_id,now_ms=now_ms)
                results.append("CREATE_UNKNOWN")
                continue
            try:
                self.store.bind_remote_order(claimed.local_order_id,remote,now_ms=now_ms)
                results.append("CREATED")
            except Exception:
                # Remote POST returned success but local binding failed: remote side effect may exist.
                try:
                    self.store.mark_create_unknown(claimed.local_order_id,now_ms=now_ms)
                finally:
                    raise
        return results

    def recover_unknown_orders(self, *, now_ms: int, limit: int = 100) -> int:
        recovered=0
        for intent in self.store.unresolved_dispatches(limit):
            if intent.state is OrderIntentState.CREATING:
                self.store.mark_create_unknown(intent.local_order_id,now_ms=now_ms)
                intent=self.store.intent(intent.local_order_id)
            matches=self.gateway.orders_by_receipt(receipt=intent.receipt)
            exact=[o for o in matches if o.receipt==intent.receipt and o.amount_paise==intent.amount_paise and o.currency==intent.currency]
            if len(exact)>1:
                raise PaymentConflict("multiple remote orders found for one deterministic receipt")
            if len(exact)==1:
                self.store.bind_remote_order(intent.local_order_id,exact[0],now_ms=now_ms); recovered+=1
        return recovered

    def resolve_expired_unknown_orders(self, *, now_ms: int, limit: int = 100) -> list[str]:
        """Perform one final receipt lookup, then stop automatic progress safely.

        An ambiguous remote write cannot be assumed absent merely because a lookup is
        empty.  Once the checkout hold expires, move it to an explicit manual-review
        state while retaining inventory and authority.  A later discovered remote order
        can still bind by its deterministic receipt.
        """
        results: list[str] = []
        for intent in self.store.unresolved_dispatches(limit):
            if intent.state is OrderIntentState.CREATING:
                self.store.mark_create_unknown(intent.local_order_id, now_ms=now_ms)
                intent = self.store.intent(intent.local_order_id)
            matches = self.gateway.orders_by_receipt(receipt=intent.receipt)
            exact = [
                order for order in matches
                if order.receipt == intent.receipt
                and order.amount_paise == intent.amount_paise
                and order.currency == intent.currency
            ]
            if len(exact) > 1:
                raise PaymentConflict("multiple remote orders found for one deterministic receipt")
            if len(exact) == 1:
                self.store.bind_remote_order(intent.local_order_id, exact[0], now_ms=now_ms)
                results.append("RECOVERED")
                continue
            if intent.state is OrderIntentState.CREATE_REQUIRES_MANUAL_REVIEW:
                continue
            if now_ms >= self.store.hold(intent.execution_id).hold_until_ms:
                self.store.mark_create_requires_manual_review(intent.local_order_id, now_ms=now_ms)
                results.append("MANUAL_REVIEW")
        return results

    def reconcile(self, *, local_order_id: str, now_ms: int) -> PaymentState:
        before=self.store.intent(local_order_id)
        if before.remote_order_id is None:
            raise PaymentConflict("cannot reconcile unbound order")
        remote_order=self.gateway.fetch_order(order_id=before.remote_order_id)
        payments=self.gateway.payments_for_order(order_id=before.remote_order_id)
        return self.store.apply_reconciliation(local_order_id=local_order_id,expected_intent_version=before.version,
                                               remote_order=remote_order,remote_payments=payments,now_ms=now_ms)

    def close_expired_checkout(self, *, local_order_id: str, now_ms: int) -> PaymentState:
        state=self.reconcile(local_order_id=local_order_id,now_ms=now_ms)
        if state is PaymentState.CAPTURED:
            return state
        current=self.store.intent(local_order_id)
        self.store.release_checkout_hold_after_reconcile(local_order_id=local_order_id,expected_intent_version=current.version,now_ms=now_ms)
        return PaymentState.RECONCILED_FAILED
