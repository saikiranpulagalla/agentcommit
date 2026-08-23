from __future__ import annotations

from .models import (
    INT64_MAX, Decision, DecisionCode, DelegationState, DomainError, DomainSnapshot,
    AuthorizationMode, ExecutionState, GrantState, PaymentState, ReservationState,
)


def evaluate_commit(s: DomainSnapshot, *, now_ms: int) -> Decision:
    try:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms <= 0 or now_ms > INT64_MAX:
            return Decision(False, DecisionCode.DOMAIN_STATE_INVALID)
        # Validate dataclass invariants even if a caller constructed a corrupted object via object.__setattr__.
        for obj in (s.delegation, s.quote, s.reservation, s.grant, s.execution, s.payment):
            obj.__post_init__()
        s.__post_init__()
    except Exception:
        return Decision(False, DecisionCode.DOMAIN_STATE_INVALID)

    d, q, r, g, e, p = s.delegation, s.quote, s.reservation, s.grant, s.execution, s.payment

    if d.status is not DelegationState.ACTIVE:
        return Decision(False, DecisionCode.DELEGATION_NOT_ACTIVE)
    if now_ms >= d.expires_at_ms:
        return Decision(False, DecisionCode.DELEGATION_EXPIRED)
    if g.status is not GrantState.ACTIVE:
        return Decision(False, DecisionCode.GRANT_NOT_ACTIVE)
    if r.status is not ReservationState.ACTIVE:
        return Decision(False, DecisionCode.RESERVATION_NOT_ACTIVE)
    if now_ms >= r.expires_at_ms:
        return Decision(False, DecisionCode.RESERVATION_EXPIRED)
    if e.state is not ExecutionState.PLANNED:
        return Decision(False, DecisionCode.EXECUTION_NOT_PLANNED)
    if p.state is not PaymentState.UNKNOWN:
        return Decision(False, DecisionCode.PAYMENT_PATH_ALREADY_EXISTS)
    if s.commit_count != 0:
        return Decision(False, DecisionCode.PAYMENT_PATH_ALREADY_EXISTS)

    if e.buyer_id != d.buyer_id or g.expected_buyer_id != d.buyer_id:
        return Decision(False, DecisionCode.BUYER_MISMATCH)
    if g.delegation_id != d.delegation_id or g.expected_delegation_version != d.version:
        return Decision(False, DecisionCode.DELEGATION_VERSION_MISMATCH)
    if g.expected_plan_generation != d.plan_generation:
        return Decision(False, DecisionCode.DELEGATION_VERSION_MISMATCH)
    if not (q.merchant_id == r.merchant_id == d.merchant_id == g.expected_merchant_id):
        return Decision(False, DecisionCode.MERCHANT_MISMATCH)
    if not (q.category == r.category == d.category == g.expected_category):
        return Decision(False, DecisionCode.RESOURCE_MISMATCH)
    if not (q.sku == r.sku == g.expected_sku):
        return Decision(False, DecisionCode.RESOURCE_MISMATCH)
    if not (q.currency == r.currency == d.currency == g.expected_currency):
        return Decision(False, DecisionCode.CURRENCY_MISMATCH)
    if q.amount_paise > d.max_amount_paise:
        return Decision(False, DecisionCode.AMOUNT_EXCEEDS_DELEGATION)
    if d.mode is AuthorizationMode.EXACT and (q.sku != d.exact_sku or q.amount_paise != d.exact_amount_paise):
        return Decision(False, DecisionCode.RESOURCE_MISMATCH)
    if q.quantity > d.max_quantity:
        return Decision(False, DecisionCode.QUANTITY_EXCEEDS_DELEGATION)

    if not (
        g.reservation_id == r.reservation_id
        and g.expected_quote_id == q.quote_id == r.quote_id
        and g.expected_amount_paise == q.amount_paise == r.amount_paise
        and g.expected_quantity == q.quantity == r.quantity
        and g.expected_quote_revision == q.quote_revision == r.quote_revision
        and g.expected_reservation_revision == r.revision
    ):
        return Decision(False, DecisionCode.BINDING_MISMATCH)

    # Atomic local advancement consumes four monotonic counters.
    if max(d.version, g.version, r.revision, e.version) >= INT64_MAX:
        return Decision(False, DecisionCode.COUNTER_EXHAUSTED)
    return Decision(True, DecisionCode.ALLOW)
