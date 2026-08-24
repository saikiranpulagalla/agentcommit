from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol, Sequence

from agentcommit.domain.models import DomainError


class ModelFailure(RuntimeError):
    """Provider/model failure. Never interpreted as authorization or a valid model answer."""


class JsonModel(Protocol):
    def complete_json(self, *, system: str, user: str) -> Any:
        """Return a decoded JSON-compatible value or raise ModelFailure."""
        ...


@dataclass
class ScriptedJsonModel:
    """Deterministic test/eval model. It is not a production LLM adapter."""

    responses: list[Any]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, *, system: str, user: str) -> Any:
        self.calls.append((system, user))
        if not self.responses:
            raise ModelFailure("scripted model exhausted")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def bounded_json(value: Any, *, max_chars: int = 16_000) -> str:
    """Serialize model-facing structured data with a strict size ceiling."""
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise DomainError("model payload is not JSON serializable") from exc
    if len(text) > max_chars:
        raise DomainError("model payload exceeds size limit")
    return text
