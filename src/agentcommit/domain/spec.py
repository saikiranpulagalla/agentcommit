"""Independent declarative V1 admission specification.

This intentionally does not call production policy helpers. It is used as a differential oracle.
"""
from __future__ import annotations
from .models import *


def spec_allows_commit(s: DomainSnapshot, *, now_ms: int) -> bool:
    try:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or not (1 <= now_ms <= INT64_MAX):
            return False
        for o in (s.delegation, s.quote, s.reservation, s.grant, s.execution, s.payment):
            o.__post_init__()
        s.__post_init__()
    except Exception:
        return False

    d, q, r, g, e, p = s.delegation, s.quote, s.reservation, s.grant, s.execution, s.payment
    conditions = (
        d.status is DelegationState.ACTIVE,
        now_ms < d.expires_at_ms,
        g.status is GrantState.ACTIVE,
        r.status is ReservationState.ACTIVE,
        now_ms < r.expires_at_ms,
        e.state is ExecutionState.PLANNED,
        p.state is PaymentState.UNKNOWN,
        s.commit_count == 0,
        e.buyer_id == d.buyer_id == g.expected_buyer_id,
        g.delegation_id == d.delegation_id,
        g.expected_delegation_version == d.version,
        g.expected_plan_generation == d.plan_generation,
        q.merchant_id == r.merchant_id == d.merchant_id == g.expected_merchant_id,
        q.category == r.category == d.category == g.expected_category,
        q.sku == r.sku == g.expected_sku,
        q.currency == r.currency == d.currency == g.expected_currency,
        q.amount_paise <= d.max_amount_paise,
        (d.mode is not AuthorizationMode.EXACT or (q.sku == d.exact_sku and q.amount_paise == d.exact_amount_paise)),
        q.quantity <= d.max_quantity,
        g.reservation_id == r.reservation_id,
        g.expected_quote_id == q.quote_id == r.quote_id,
        g.expected_amount_paise == q.amount_paise == r.amount_paise,
        g.expected_quantity == q.quantity == r.quantity,
        g.expected_quote_revision == q.quote_revision == r.quote_revision,
        g.expected_reservation_revision == r.revision,
        max(d.version, g.version, r.revision, e.version) < INT64_MAX,
    )
    return all(conditions)
