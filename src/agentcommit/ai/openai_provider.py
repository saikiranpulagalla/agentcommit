from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agentcommit.ai.model import JsonModel, ModelFailure
from agentcommit.domain.models import DomainError


class HttpJsonTransport(Protocol):
    def post_json(self, *, url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> bytes:
        ...


@dataclass(slots=True)
class UrllibHttpJsonTransport:
    def post_json(self, *, url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> bytes:
        request = Request(url=url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - URL is validated by adapter
                return response.read()
        except HTTPError as exc:
            # Model inference is side-effect free from AgentCommit's perspective. Treat all provider
            # HTTP failures as ModelFailure; bounded caller retry policy decides whether to retry.
            try:
                detail = exc.read(1024).decode("utf-8", "replace")
            except Exception:
                detail = ""
            raise ModelFailure(f"OpenAI HTTP {exc.code}: {detail[:300]}") from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ModelFailure("OpenAI transport failure") from exc


@dataclass(frozen=True, slots=True)
class OpenAIUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class OpenAIResponsesJsonModel(JsonModel):
    """Strict JSON-schema adapter for the OpenAI Responses API.

    This adapter has no authority semantics. It only turns a system/user prompt into a
    decoded JSON value. AgentCommit's deterministic parsers and commit kernel remain the
    authority boundary.
    """

    api_key: str
    model: str
    response_schema: Mapping[str, Any]
    response_name: str
    base_url: str = "https://api.openai.com/v1"
    timeout_s: float = 45.0
    max_output_tokens: int = 1200
    transport: HttpJsonTransport = field(default_factory=UrllibHttpJsonTransport)
    _usage: list[OpenAIUsage] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key or len(self.api_key) > 4096:
            raise DomainError("OpenAI API key must be a non-empty bounded string")
        if not isinstance(self.model, str) or not 1 <= len(self.model) <= 128:
            raise DomainError("OpenAI model must be bounded string")
        if not isinstance(self.response_name, str) or not 1 <= len(self.response_name) <= 64:
            raise DomainError("response_name must be bounded string")
        if not all(c.isalnum() or c in "_-" for c in self.response_name):
            raise DomainError("response_name contains unsupported characters")
        if not isinstance(self.base_url, str) or not self.base_url.startswith("https://"):
            raise DomainError("OpenAI base_url must use https")
        if type(self.timeout_s) not in {int, float} or not 1 <= float(self.timeout_s) <= 120:
            raise DomainError("timeout_s must be in [1,120]")
        if type(self.max_output_tokens) is not int or not 64 <= self.max_output_tokens <= 8000:
            raise DomainError("max_output_tokens must be int in [64,8000]")
        self._validate_schema(self.response_schema)

    @staticmethod
    def _validate_schema(schema: Mapping[str, Any]) -> None:
        if not isinstance(schema, Mapping):
            raise DomainError("response_schema must be object")
        try:
            encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise DomainError("response_schema must be JSON serializable") from exc
        if len(encoded) > 32_000:
            raise DomainError("response_schema exceeds size limit")
        if schema.get("type") != "object":
            raise DomainError("response_schema root must be object")

    @property
    def usage(self) -> tuple[OpenAIUsage, ...]:
        return tuple(self._usage)

    def complete_json(self, *, system: str, user: str) -> Any:
        if not isinstance(system, str) or not isinstance(user, str):
            raise DomainError("model prompts must be strings")
        if not system or len(system) > 32_000 or len(user) > 128_000:
            raise DomainError("model prompt size invalid")

        payload = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": self.response_name,
                    "schema": self.response_schema,
                    "strict": True,
                }
            },
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        raw = self.transport.post_json(
            url=self.base_url.rstrip("/") + "/responses",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            body=body,
            timeout_s=float(self.timeout_s),
        )
        if len(raw) > 2_000_000:
            raise ModelFailure("OpenAI response exceeds size limit")
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelFailure("OpenAI returned invalid JSON envelope") from exc
        if not isinstance(response, dict):
            raise ModelFailure("OpenAI response envelope must be object")
        status = response.get("status")
        if status != "completed":
            error = response.get("error")
            detail = str(error)[:300] if error is not None else str(status)[:80]
            raise ModelFailure(f"OpenAI response not completed: {detail}")

        text: str | None = None
        refusal: str | None = None
        output = response.get("output")
        if not isinstance(output, list):
            raise ModelFailure("OpenAI response output missing")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    refusal = part["refusal"]
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    if text is not None:
                        raise ModelFailure("OpenAI returned multiple output_text parts")
                    text = part["text"]
        if refusal is not None:
            raise ModelFailure("OpenAI model refused structured request")
        if text is None or not text or len(text) > 256_000:
            raise ModelFailure("OpenAI structured output text missing or oversized")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelFailure("OpenAI structured output was not valid JSON") from exc

        usage = response.get("usage")
        if isinstance(usage, dict):
            def token(name: str) -> int:
                value = usage.get(name, 0)
                return value if type(value) is int and value >= 0 else 0
            self._usage.append(OpenAIUsage(
                input_tokens=token("input_tokens"),
                output_tokens=token("output_tokens"),
                total_tokens=token("total_tokens"),
            ))
        return decoded


def intent_output_schema(*, constraint_fields: list[str], clarification_fields: list[str]) -> dict[str, Any]:
    if not constraint_fields or not clarification_fields:
        raise DomainError("schema vocabularies cannot be empty")
    scalar = {"anyOf": [
        {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
    ]}
    scalar_or_array = {"anyOf": [scalar, {"type": "array", "items": scalar}]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "hard_constraints", "soft_preferences", "substitution_allowed", "unresolved_fields"],
        "properties": {
            "status": {"type": "string", "enum": ["READY", "NEEDS_CLARIFICATION"]},
            "hard_constraints": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["field", "op", "value"],
                    "properties": {
                        "field": {"type": "string", "enum": constraint_fields},
                        "op": {"type": "string", "enum": ["EQ", "NEQ", "LTE", "GTE", "IN", "NOT_IN"]},
                        "value": scalar_or_array,
                    },
                },
            },
            "soft_preferences": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["field", "direction"],
                    "properties": {
                        "field": {"type": "string", "enum": constraint_fields},
                        "direction": {"type": "string", "enum": ["MINIMIZE", "MAXIMIZE"]},
                    },
                },
            },
            "substitution_allowed": {"type": "boolean"},
            "unresolved_fields": {
                "type": "array",
                "items": {"type": "string", "enum": clarification_fields},
            },
        },
    }


def planner_output_schema(*, max_ranked_skus: int = 64) -> dict[str, Any]:
    if type(max_ranked_skus) is not int or not 1 <= max_ranked_skus <= 256:
        raise DomainError("max_ranked_skus must be int in [1,256]")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["ranked_skus", "reason"],
        "properties": {
            "ranked_skus": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reason": {"type": "string"},
        },
    }
