from __future__ import annotations

from dataclasses import dataclass
import re

from agentcommit.ai.intent import ConstraintOp, IntentSpec
from agentcommit.domain.models import DomainError, INT64_MAX

_BUDGET = re.compile(
    r"(?:under|below|less\s+than|up\s+to|max(?:imum)?(?:\s+of)?|within|budget(?:\s+of|\s+is|\s*=|\s*:)?|not\s+above|no\s+more\s+than)"
    r"\s*(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|thousand)?\b",
    re.IGNORECASE,
)
_QUANTITY = re.compile(
    r"\b(?:buy|get|need|want|order)\s+(?:me\s+)?([0-9]+|one|two|three|four|five|six|seven|eight|nine|ten)\s+([a-z][a-z0-9_-]*)",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


@dataclass(frozen=True, slots=True)
class CriticalExpectation:
    max_price_paise: int | None = None
    quantity: int | None = None


def extract_critical_expectation(raw_request: str) -> CriticalExpectation:
    if not isinstance(raw_request, str):
        raise DomainError("raw_request must be string")
    max_price: int | None = None
    budget_match = _BUDGET.search(raw_request)
    if budget_match:
        number = budget_match.group(1).replace(",", "")
        try:
            rupees = float(number) if "." in number else int(number)
        except ValueError as exc:
            raise DomainError("invalid explicit budget") from exc
        if budget_match.group(2):
            rupees *= 1000
        paise_float = rupees * 100
        if paise_float != int(paise_float):
            raise DomainError("budget cannot be represented exactly in paise")
        max_price = int(paise_float)
        if not 0 < max_price <= INT64_MAX:
            raise DomainError("explicit budget out of range")

    quantity: int | None = None
    quantity_match = _QUANTITY.search(raw_request)
    if quantity_match:
        token, noun = quantity_match.groups()
        # Only explicit numeric/number-word counts are treated as critical authority.
        if noun.lower() not in {"inch", "inches", "cm", "mm", "gb", "tb", "hz"}:
            quantity = _WORD_NUMBERS.get(token.lower(), int(token) if token.isdigit() else 0)
            if not 0 < quantity <= INT64_MAX:
                raise DomainError("explicit quantity out of range")
    return CriticalExpectation(max_price_paise=max_price, quantity=quantity)


def validate_critical_extraction(*, raw_request: str, intent: IntentSpec) -> None:
    expected = extract_critical_expectation(raw_request)
    constraints = list(intent.hard_constraints)
    if expected.max_price_paise is not None:
        matches = [
            c for c in constraints
            if c.field == "price_paise" and c.op is ConstraintOp.LTE and type(c.value) is int
        ]
        if len(matches) != 1 or matches[0].value != expected.max_price_paise:
            raise DomainError("critical budget constraint missing or mismatched")
    if expected.quantity is not None:
        matches = [
            c for c in constraints
            if c.field == "quantity" and c.op is ConstraintOp.EQ and type(c.value) is int
        ]
        if len(matches) != 1 or matches[0].value != expected.quantity:
            raise DomainError("critical quantity constraint missing or mismatched")
