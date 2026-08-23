from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from agentcommit.domain.models import DomainError, INT64_MAX, _strict_int, _token, _currency


class OrderIntentState(str, Enum):
    PREPARED = "PREPARED"
    CREATING = "CREATING"
    CREATED = "CREATED"
    CREATE_UNKNOWN = "CREATE_UNKNOWN"
    CREATE_FAILED = "CREATE_FAILED"
    PAID = "PAID"
    RECONCILED_FAILED = "RECONCILED_FAILED"


class DispatchState(str, Enum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    CANCELLED = "CANCELLED"


class InventoryHoldState(str, Enum):
    HELD = "HELD"
    FULFILLED = "FULFILLED"
    RELEASED = "RELEASED"


class RemoteOutcome(str, Enum):
    DEFINITE_REJECTION = "DEFINITE_REJECTION"
    AMBIGUOUS = "AMBIGUOUS"


_RECEIPT = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _receipt(value: str) -> str:
    if not isinstance(value, str) or not _RECEIPT.fullmatch(value):
        raise DomainError("receipt must be 1-40 canonical ASCII characters")
    return value


@dataclass(frozen=True, slots=True)
class PaymentOrderIntent:
    local_order_id: str
    execution_id: str
    receipt: str
    amount_paise: int
    currency: str
    state: OrderIntentState
    version: int
    remote_order_id: str | None = None

    def __post_init__(self) -> None:
        _token("local_order_id", self.local_order_id)
        _token("execution_id", self.execution_id)
        _receipt(self.receipt)
        _strict_int("amount_paise", self.amount_paise)
        _currency(self.currency)
        _strict_int("version", self.version)
        if not isinstance(self.state, OrderIntentState):
            raise DomainError("invalid order intent state")
        if self.remote_order_id is not None:
            _token("remote_order_id", self.remote_order_id)
        bound_states={OrderIntentState.CREATED, OrderIntentState.PAID, OrderIntentState.RECONCILED_FAILED}
        if self.state in {OrderIntentState.CREATED, OrderIntentState.PAID} and self.remote_order_id is None:
            raise DomainError("created/paid order intent requires remote_order_id")
        if self.state not in bound_states and self.remote_order_id is not None:
            raise DomainError("pre-bind order intent cannot carry remote_order_id")


@dataclass(frozen=True, slots=True)
class RemoteOrder:
    order_id: str
    receipt: str
    amount_paise: int
    currency: str
    status: str

    def __post_init__(self) -> None:
        _token("order_id", self.order_id)
        _receipt(self.receipt)
        _strict_int("amount_paise", self.amount_paise)
        _currency(self.currency)
        if self.status not in {"created", "attempted", "paid"}:
            raise DomainError("invalid remote order status")


@dataclass(frozen=True, slots=True)
class RemotePayment:
    payment_id: str
    order_id: str
    amount_paise: int
    currency: str
    status: str

    def __post_init__(self) -> None:
        _token("payment_id", self.payment_id)
        _token("order_id", self.order_id)
        _strict_int("amount_paise", self.amount_paise)
        _currency(self.currency)
        if self.status not in {"created", "authorized", "captured", "failed"}:
            raise DomainError("invalid remote payment status")


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    execution_id: str
    receipt: str
    state: DispatchState
    version: int
    created_at_ms: int

    def __post_init__(self) -> None:
        _token("execution_id", self.execution_id)
        _receipt(self.receipt)
        if not isinstance(self.state, DispatchState):
            raise DomainError("invalid dispatch state")
        _strict_int("version", self.version)
        _strict_int("created_at_ms", self.created_at_ms)


@dataclass(frozen=True, slots=True)
class InventoryHold:
    execution_id: str
    reservation_id: str
    merchant_id: str
    sku: str
    quantity: int
    state: InventoryHoldState
    hold_until_ms: int
    version: int

    def __post_init__(self) -> None:
        for name in ("execution_id", "reservation_id", "merchant_id", "sku"):
            _token(name, getattr(self, name))
        _strict_int("quantity", self.quantity)
        if not isinstance(self.state, InventoryHoldState):
            raise DomainError("invalid hold state")
        _strict_int("hold_until_ms", self.hold_until_ms)
        _strict_int("version", self.version)
