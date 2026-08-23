from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re

INT64_MAX = 2**63 - 1
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class DomainError(ValueError):
    pass


class InvalidTransition(DomainError):
    pass


def _strict_int(name: str, value: int, *, minimum: int = 1, maximum: int = INT64_MAX) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise DomainError(f"{name} must be in [{minimum}, {maximum}]")
    return value


def _token(name: str, value: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise DomainError(f"{name} must be a canonical ASCII token")
    return value


def _currency(value: str) -> str:
    if not isinstance(value, str) or not _CURRENCY.fullmatch(value):
        raise DomainError("currency must be exactly three ASCII uppercase letters")
    return value


class AuthorizationMode(str, Enum):
    DELEGATED = "DELEGATED"
    EXACT = "EXACT"


class PlanState(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    COMMITTED = "COMMITTED"
    CANCELLED = "CANCELLED"


class DelegationState(str, Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class GrantState(str, Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ReservationState(str, Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ExecutionState(str, Enum):
    PLANNED = "PLANNED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    OBSERVED_FAILED = "OBSERVED_FAILED"
    RECONCILED_FAILED = "RECONCILED_FAILED"
    COMPENSATION_REQUIRED = "COMPENSATION_REQUIRED"


class PaymentState(str, Enum):
    UNKNOWN = "UNKNOWN"
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    UNCERTAIN = "UNCERTAIN"
    OBSERVED_FAILED = "OBSERVED_FAILED"
    CAPTURED = "CAPTURED"
    RECONCILED_FAILED = "RECONCILED_FAILED"


class DecisionCode(str, Enum):
    ALLOW = "ALLOW"
    DOMAIN_STATE_INVALID = "DOMAIN_STATE_INVALID"
    DELEGATION_NOT_ACTIVE = "DELEGATION_NOT_ACTIVE"
    DELEGATION_EXPIRED = "DELEGATION_EXPIRED"
    GRANT_NOT_ACTIVE = "GRANT_NOT_ACTIVE"
    RESERVATION_NOT_ACTIVE = "RESERVATION_NOT_ACTIVE"
    RESERVATION_EXPIRED = "RESERVATION_EXPIRED"
    EXECUTION_NOT_PLANNED = "EXECUTION_NOT_PLANNED"
    PAYMENT_PATH_ALREADY_EXISTS = "PAYMENT_PATH_ALREADY_EXISTS"
    BUYER_MISMATCH = "BUYER_MISMATCH"
    DELEGATION_VERSION_MISMATCH = "DELEGATION_VERSION_MISMATCH"
    MERCHANT_MISMATCH = "MERCHANT_MISMATCH"
    RESOURCE_MISMATCH = "RESOURCE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    AMOUNT_EXCEEDS_DELEGATION = "AMOUNT_EXCEEDS_DELEGATION"
    QUANTITY_EXCEEDS_DELEGATION = "QUANTITY_EXCEEDS_DELEGATION"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    QUOTE_STALE = "QUOTE_STALE"
    RESERVATION_STALE = "RESERVATION_STALE"
    COUNTER_EXHAUSTED = "COUNTER_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    code: DecisionCode

    def __post_init__(self) -> None:
        if self.allowed != (self.code is DecisionCode.ALLOW):
            raise DomainError("Decision.allowed must agree with Decision.code")


@dataclass(frozen=True, slots=True)
class DelegationGrant:
    delegation_id: str
    buyer_id: str
    merchant_id: str
    category: str
    max_amount_paise: int
    currency: str
    max_quantity: int
    expires_at_ms: int
    status: DelegationState = DelegationState.ACTIVE
    version: int = 1
    plan_generation: int = 0
    mode: AuthorizationMode = AuthorizationMode.DELEGATED
    exact_sku: str | None = None
    exact_amount_paise: int | None = None
    substitution_allowed: bool = True

    def __post_init__(self) -> None:
        _token("delegation_id", self.delegation_id)
        _token("buyer_id", self.buyer_id)
        _token("merchant_id", self.merchant_id)
        _token("category", self.category)
        _strict_int("max_amount_paise", self.max_amount_paise)
        _currency(self.currency)
        _strict_int("max_quantity", self.max_quantity)
        _strict_int("expires_at_ms", self.expires_at_ms)
        _strict_int("version", self.version)
        _strict_int("plan_generation", self.plan_generation, minimum=0)
        if not isinstance(self.status, DelegationState):
            raise DomainError("invalid delegation state")
        if not isinstance(self.mode, AuthorizationMode):
            raise DomainError("invalid authorization mode")
        if not isinstance(self.substitution_allowed, bool):
            raise DomainError("substitution_allowed must be bool")
        if self.mode is AuthorizationMode.EXACT:
            if self.exact_sku is None or self.exact_amount_paise is None:
                raise DomainError("EXACT authority requires exact_sku and exact_amount_paise")
            _token("exact_sku", self.exact_sku)
            _strict_int("exact_amount_paise", self.exact_amount_paise)
            if self.substitution_allowed:
                raise DomainError("EXACT authority cannot allow substitution")
        else:
            if self.exact_sku is not None or self.exact_amount_paise is not None:
                raise DomainError("DELEGATED authority cannot carry exact binding")


@dataclass(frozen=True, slots=True)
class MerchantQuote:
    quote_id: str
    merchant_id: str
    category: str
    sku: str
    amount_paise: int
    currency: str
    quantity: int
    price_revision: int
    quote_revision: int

    def __post_init__(self) -> None:
        for n in ("quote_id", "merchant_id", "category", "sku"):
            _token(n, getattr(self, n))
        _strict_int("amount_paise", self.amount_paise)
        _currency(self.currency)
        _strict_int("quantity", self.quantity)
        _strict_int("price_revision", self.price_revision)
        _strict_int("quote_revision", self.quote_revision)


@dataclass(frozen=True, slots=True)
class MerchantReservation:
    reservation_id: str
    quote_id: str
    merchant_id: str
    category: str
    sku: str
    amount_paise: int
    currency: str
    quantity: int
    quote_revision: int
    revision: int
    expires_at_ms: int
    status: ReservationState = ReservationState.ACTIVE

    def __post_init__(self) -> None:
        for n in ("reservation_id", "quote_id", "merchant_id", "category", "sku"):
            _token(n, getattr(self, n))
        _strict_int("amount_paise", self.amount_paise)
        _currency(self.currency)
        _strict_int("quantity", self.quantity)
        _strict_int("quote_revision", self.quote_revision)
        _strict_int("revision", self.revision)
        _strict_int("expires_at_ms", self.expires_at_ms)
        if not isinstance(self.status, ReservationState):
            raise DomainError("invalid reservation state")


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    grant_id: str
    delegation_id: str
    expected_delegation_version: int
    expected_buyer_id: str
    reservation_id: str
    expected_quote_id: str
    expected_merchant_id: str
    expected_category: str
    expected_sku: str
    expected_amount_paise: int
    expected_currency: str
    expected_quantity: int
    expected_quote_revision: int
    expected_reservation_revision: int
    status: GrantState = GrantState.ACTIVE
    version: int = 1
    expected_plan_generation: int = 0

    def __post_init__(self) -> None:
        for n in (
            "grant_id", "delegation_id", "expected_buyer_id", "reservation_id",
            "expected_quote_id", "expected_merchant_id", "expected_category", "expected_sku",
        ):
            _token(n, getattr(self, n))
        _strict_int("expected_delegation_version", self.expected_delegation_version)
        _strict_int("expected_amount_paise", self.expected_amount_paise)
        _currency(self.expected_currency)
        _strict_int("expected_quantity", self.expected_quantity)
        _strict_int("expected_quote_revision", self.expected_quote_revision)
        _strict_int("expected_reservation_revision", self.expected_reservation_revision)
        _strict_int("version", self.version)
        _strict_int("expected_plan_generation", self.expected_plan_generation, minimum=0)
        if not isinstance(self.status, GrantState):
            raise DomainError("invalid grant state")


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: str
    buyer_id: str
    state: ExecutionState = ExecutionState.PLANNED
    version: int = 1

    def __post_init__(self) -> None:
        _token("execution_id", self.execution_id)
        _token("buyer_id", self.buyer_id)
        _strict_int("version", self.version)
        if not isinstance(self.state, ExecutionState):
            raise DomainError("invalid execution state")


@dataclass(frozen=True, slots=True)
class PaymentProjection:
    payment_id: str | None = None
    state: PaymentState = PaymentState.UNKNOWN
    version: int = 1

    def __post_init__(self) -> None:
        _strict_int("version", self.version)
        if not isinstance(self.state, PaymentState):
            raise DomainError("invalid payment state")
        if self.state is PaymentState.UNKNOWN:
            if self.payment_id is not None:
                raise DomainError("UNKNOWN payment must not have payment_id")
        else:
            if self.payment_id is None:
                raise DomainError("non-UNKNOWN payment requires payment_id")
            _token("payment_id", self.payment_id)


@dataclass(frozen=True, slots=True)
class DomainSnapshot:
    delegation: DelegationGrant
    quote: MerchantQuote
    reservation: MerchantReservation
    grant: ExecutionGrant
    execution: ExecutionRecord
    payment: PaymentProjection
    commit_count: int = 0

    def __post_init__(self) -> None:
        _strict_int("commit_count", self.commit_count, minimum=0)


def bump(obj, **changes):
    version = getattr(obj, "version", None)
    if version is None:
        raise DomainError("object has no version")
    if version >= INT64_MAX:
        raise DomainError("version exhausted")
    return replace(obj, version=version + 1, **changes)
