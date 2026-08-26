from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import Any, Mapping
from types import MappingProxyType

from agentcommit.domain.models import DomainError, INT64_MAX, _strict_int, _token

_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED = {
    "merchant_id", "sku", "category", "currency", "price_paise", "quantity",
}
_ALLOWED_SCALARS = (str, int, bool)


class IntentStatus(str, Enum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class ConstraintOp(str, Enum):
    EQ = "EQ"
    NEQ = "NEQ"
    LTE = "LTE"
    GTE = "GTE"
    IN = "IN"
    NOT_IN = "NOT_IN"


class PreferenceDirection(str, Enum):
    MINIMIZE = "MINIMIZE"
    MAXIMIZE = "MAXIMIZE"


def _field(name: str) -> str:
    if not isinstance(name, str) or not _FIELD.fullmatch(name):
        raise DomainError("field must be canonical snake_case ASCII")
    return name


def _scalar(name: str, value: Any) -> str | int | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -INT64_MAX <= value <= INT64_MAX:
            raise DomainError(f"{name} integer out of int64 range")
        return value
    if isinstance(value, str):
        if not value or len(value) > 128 or any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
            raise DomainError(f"{name} must be printable bounded ASCII")
        return value
    raise DomainError(f"{name} must be str/int/bool")


@dataclass(frozen=True, slots=True)
class HardConstraint:
    field: str
    op: ConstraintOp
    value: Any

    def __post_init__(self) -> None:
        _field(self.field)
        if not isinstance(self.op, ConstraintOp):
            raise DomainError("invalid constraint op")
        if self.op in {ConstraintOp.IN, ConstraintOp.NOT_IN}:
            if not isinstance(self.value, tuple) or not self.value:
                raise DomainError("set constraint requires non-empty tuple")
            normalized = tuple(_scalar("constraint value", x) for x in self.value)
            if len(set((type(x).__name__, x) for x in normalized)) != len(normalized):
                raise DomainError("duplicate values in set constraint")
        else:
            _scalar("constraint value", self.value)


@dataclass(frozen=True, slots=True)
class SoftPreference:
    field: str
    direction: PreferenceDirection

    def __post_init__(self) -> None:
        _field(self.field)
        if not isinstance(self.direction, PreferenceDirection):
            raise DomainError("invalid preference direction")


@dataclass(frozen=True, slots=True)
class IntentSpec:
    intent_id: str
    buyer_id: str
    raw_request: str
    hard_constraints: tuple[HardConstraint, ...]
    soft_preferences: tuple[SoftPreference, ...] = ()
    substitution_allowed: bool = True
    status: IntentStatus = IntentStatus.READY
    unresolved_fields: tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        _token("intent_id", self.intent_id)
        _token("buyer_id", self.buyer_id)
        if not isinstance(self.raw_request, str) or not self.raw_request.strip() or len(self.raw_request) > 2000:
            raise DomainError("raw_request must be non-empty and bounded")
        if not isinstance(self.hard_constraints, tuple) or not all(isinstance(x, HardConstraint) for x in self.hard_constraints):
            raise DomainError("hard_constraints must be tuple[HardConstraint]")
        if not isinstance(self.soft_preferences, tuple) or not all(isinstance(x, SoftPreference) for x in self.soft_preferences):
            raise DomainError("soft_preferences must be tuple[SoftPreference]")
        if not isinstance(self.substitution_allowed, bool):
            raise DomainError("substitution_allowed must be bool")
        if not isinstance(self.status, IntentStatus):
            raise DomainError("invalid intent status")
        _strict_int("intent version", self.version)
        if not isinstance(self.unresolved_fields, tuple):
            raise DomainError("unresolved_fields must be tuple")
        for field_name in self.unresolved_fields:
            _field(field_name)
        def constraint_key(c: HardConstraint) -> tuple:
            if isinstance(c.value, tuple):
                value_key = tuple((type(x).__name__, x) for x in c.value)
            else:
                value_key = (type(c.value).__name__, c.value)
            return (c.field, c.op.value, value_key)
        keys = [constraint_key(c) for c in self.hard_constraints]
        if len(keys) != len(set(keys)):
            raise DomainError("exact duplicate hard constraints are not allowed")
        if self.status is IntentStatus.READY and self.unresolved_fields:
            raise DomainError("READY intent cannot have unresolved fields")
        if self.status is IntentStatus.NEEDS_CLARIFICATION and not self.unresolved_fields:
            raise DomainError("NEEDS_CLARIFICATION requires unresolved fields")

    def canonical_json(self) -> str:
        payload = {
            "intent_id": self.intent_id,
            "buyer_id": self.buyer_id,
            "raw_request": self.raw_request,
            "hard_constraints": [
                {"field": c.field, "op": c.op.value, "value": list(c.value) if isinstance(c.value, tuple) else c.value}
                for c in self.hard_constraints
            ],
            "soft_preferences": [
                {"field": p.field, "direction": p.direction.value} for p in self.soft_preferences
            ],
            "substitution_allowed": self.substitution_allowed,
            "status": self.status.value,
            "unresolved_fields": list(self.unresolved_fields),
            "version": self.version,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_canonical_json(cls, text: str) -> "IntentSpec":
        if not isinstance(text, str) or len(text) > 16000:
            raise DomainError("invalid intent JSON")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DomainError("invalid intent JSON") from exc
        if not isinstance(raw, dict):
            raise DomainError("intent JSON must be object")
        allowed = {"intent_id", "buyer_id", "raw_request", "hard_constraints", "soft_preferences", "substitution_allowed", "status", "unresolved_fields", "version"}
        if set(raw) != allowed:
            raise DomainError("intent JSON schema mismatch")
        hard: list[HardConstraint] = []
        for item in raw["hard_constraints"]:
            if not isinstance(item, dict) or set(item) != {"field", "op", "value"}:
                raise DomainError("invalid hard constraint JSON")
            op = ConstraintOp(item["op"])
            value = tuple(item["value"]) if op in {ConstraintOp.IN, ConstraintOp.NOT_IN} else item["value"]
            hard.append(HardConstraint(item["field"], op, value))
        soft: list[SoftPreference] = []
        for item in raw["soft_preferences"]:
            if not isinstance(item, dict) or set(item) != {"field", "direction"}:
                raise DomainError("invalid soft preference JSON")
            soft.append(SoftPreference(item["field"], PreferenceDirection(item["direction"])))
        return cls(
            intent_id=raw["intent_id"], buyer_id=raw["buyer_id"], raw_request=raw["raw_request"],
            hard_constraints=tuple(hard), soft_preferences=tuple(soft),
            substitution_allowed=raw["substitution_allowed"], status=IntentStatus(raw["status"]),
            unresolved_fields=tuple(raw["unresolved_fields"]), version=raw["version"],
        )


@dataclass(frozen=True, slots=True)
class ProductFacts:
    merchant_id: str
    sku: str
    category: str
    currency: str
    price_paise: int
    quantity: int
    revision: int
    attributes: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        for n in ("merchant_id", "sku", "category", "currency"):
            _token(n, getattr(self, n))
        _strict_int("price_paise", self.price_paise)
        _strict_int("quantity", self.quantity)
        _strict_int("facts revision", self.revision)
        if not isinstance(self.attributes, Mapping):
            raise DomainError("attributes must be mapping")
        copied: dict[str, str | int | bool] = {}
        for key, value in self.attributes.items():
            _field(key)
            if key in _RESERVED:
                raise DomainError(f"reserved commerce field cannot appear in attributes: {key}")
            copied[key] = _scalar(f"attribute {key}", value)
        object.__setattr__(self, "attributes", MappingProxyType(copied))

    def all_facts(self) -> dict[str, str | int | bool]:
        out: dict[str, str | int | bool] = dict(self.attributes)
        out.update({
            "merchant_id": self.merchant_id,
            "sku": self.sku,
            "category": self.category,
            "currency": self.currency,
            "price_paise": self.price_paise,
            "quantity": self.quantity,
        })
        return out

    def canonical_attributes_json(self) -> str:
        return json.dumps(dict(self.attributes), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class ConstraintEvaluation:
    satisfied: bool
    violations: tuple[str, ...]


def _same_type_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def evaluate_hard_constraints(intent: IntentSpec, facts: ProductFacts) -> ConstraintEvaluation:
    if intent.status is not IntentStatus.READY:
        return ConstraintEvaluation(False, ("INTENT_NEEDS_CLARIFICATION",))
    all_facts = facts.all_facts()
    violations: list[str] = []
    for c in intent.hard_constraints:
        if c.field not in all_facts:
            violations.append(f"MISSING:{c.field}")
            continue
        actual = all_facts[c.field]
        expected = c.value
        ok = False
        if c.op is ConstraintOp.EQ:
            ok = _same_type_equal(actual, expected)
        elif c.op is ConstraintOp.NEQ:
            ok = not _same_type_equal(actual, expected)
        elif c.op in {ConstraintOp.LTE, ConstraintOp.GTE}:
            if type(actual) is int and type(expected) is int:
                ok = actual <= expected if c.op is ConstraintOp.LTE else actual >= expected
        elif c.op in {ConstraintOp.IN, ConstraintOp.NOT_IN}:
            matched = any(_same_type_equal(actual, x) for x in expected)
            ok = matched if c.op is ConstraintOp.IN else not matched
        if not ok:
            violations.append(f"{c.op.value}:{c.field}")
    return ConstraintEvaluation(not violations, tuple(violations))


class IntentCompiler:
    def compile(self, *, intent_id: str, buyer_id: str, raw_request: str) -> IntentSpec:
        raise NotImplementedError


class ReferenceIntentCompiler(IntentCompiler):
    """Narrow deterministic compiler used as a reference fixture, not presented as the final LLM."""

    def compile(self, *, intent_id: str, buyer_id: str, raw_request: str) -> IntentSpec:
        # Keep the deterministic reference fixture aligned with the structured-model
        # boundary: an explicit monetary cap must always become a hard constraint.
        from agentcommit.ai.critical import extract_critical_expectation

        text = raw_request.lower()
        hard: list[HardConstraint] = []
        soft: list[SoftPreference] = []
        unresolved: list[str] = []
        if "27" in text and "monitor" in text:
            hard.append(HardConstraint("screen_size_inches", ConstraintOp.EQ, 27))
        if "4k" in text:
            hard.append(HardConstraint("resolution", ConstraintOp.EQ, "4K"))
        if "usb-c" in text or "usb c" in text:
            hard.append(HardConstraint("usb_c", ConstraintOp.EQ, True))
        critical = extract_critical_expectation(raw_request)
        if critical.max_price_paise is not None:
            hard.append(HardConstraint("price_paise", ConstraintOp.LTE, critical.max_price_paise))
        if "cheapest" in text:
            soft.append(SoftPreference("price_paise", PreferenceDirection.MINIMIZE))
        substitution_allowed = any(phrase in text for phrase in ("another model", "substitute", "alternative"))
        if "monitor" in text and not hard:
            unresolved.extend(("screen_size_inches", "resolution", "budget"))
        status = IntentStatus.NEEDS_CLARIFICATION if unresolved else IntentStatus.READY
        return IntentSpec(
            intent_id=intent_id, buyer_id=buyer_id, raw_request=raw_request,
            hard_constraints=tuple(hard), soft_preferences=tuple(soft),
            substitution_allowed=substitution_allowed, status=status,
            unresolved_fields=tuple(unresolved),
        )
