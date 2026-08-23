from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from agentcommit.domain.models import DomainError
from .models import RemoteOrder, RemotePayment


class RazorpayError(RuntimeError):
    pass


class DefiniteRemoteRejection(RazorpayError):
    """A 4xx response: the attempted request was definitely rejected by Razorpay."""


class AmbiguousRemoteOutcome(RazorpayError):
    """Network/write/5xx uncertainty: the remote side effect may have happened."""


class RemoteContractError(RazorpayError):
    pass


def deterministic_receipt(execution_id: str) -> str:
    if not isinstance(execution_id, str) or not execution_id:
        raise DomainError("execution_id required")
    digest = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:32]
    return f"ac_{digest}"  # 35 chars, stable and within Razorpay's 40-char receipt limit.


def deterministic_local_order_id(execution_id: str) -> str:
    digest = hashlib.sha256(("local:" + execution_id).encode("utf-8")).hexdigest()[:32]
    return f"lo_{digest}"


def _hex_signature(secret: str, message: bytes) -> str:
    if not isinstance(secret, str) or not secret or len(secret) > 4096 or any(ord(c) < 32 for c in secret):
        raise DomainError("invalid HMAC secret")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(*, key_secret: str, server_order_id: str, payment_id: str, signature: str) -> bool:
    if not all(isinstance(x, str) and x for x in (server_order_id, payment_id, signature)):
        return False
    expected = _hex_signature(key_secret, f"{server_order_id}|{payment_id}".encode("utf-8"))
    try:
        return hmac.compare_digest(expected, signature)
    except TypeError:
        return False


def verify_webhook_signature(*, webhook_secret: str, raw_body: bytes, signature: str) -> bool:
    if not isinstance(raw_body, (bytes, bytearray)) or not raw_body or not isinstance(signature, str) or not signature:
        return False
    expected = _hex_signature(webhook_secret, bytes(raw_body))
    try:
        return hmac.compare_digest(expected, signature)
    except TypeError:
        return False


class RazorpayGateway(Protocol):
    def create_order(self, *, amount_paise: int, currency: str, receipt: str) -> RemoteOrder: ...
    def orders_by_receipt(self, *, receipt: str) -> list[RemoteOrder]: ...
    def fetch_order(self, *, order_id: str) -> RemoteOrder: ...
    def payments_for_order(self, *, order_id: str) -> list[RemotePayment]: ...


@dataclass(slots=True)
class HttpRazorpayGateway:
    key_id: str
    key_secret: str
    base_url: str = "https://api.razorpay.com/v1"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id:
            raise DomainError("key_id required")
        if not isinstance(self.key_secret, str) or not self.key_secret:
            raise DomainError("key_secret required")
        if not self.base_url.startswith("https://"):
            raise DomainError("Razorpay API must use HTTPS")
        if self.timeout_seconds <= 0:
            raise DomainError("timeout must be positive")

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base_url.rstrip("/") + path
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        token = (self.key_id + ":" + self.key_secret).encode("utf-8")
        import base64
        headers = {"Authorization": "Basic " + base64.b64encode(token).decode("ascii")}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise DefiniteRemoteRejection(f"Razorpay rejected request with HTTP {exc.code}") from exc
            raise AmbiguousRemoteOutcome(f"Razorpay returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AmbiguousRemoteOutcome("Razorpay request outcome is ambiguous") from exc
        try:
            parsed = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise RemoteContractError("invalid Razorpay JSON") from exc
        if not isinstance(parsed, dict):
            raise RemoteContractError("Razorpay JSON must be an object")
        return parsed

    @staticmethod
    def _order(obj: dict) -> RemoteOrder:
        try:
            return RemoteOrder(
                order_id=obj["id"], receipt=obj["receipt"], amount_paise=obj["amount"],
                currency=obj["currency"], status=obj["status"],
            )
        except Exception as exc:
            raise RemoteContractError("invalid Razorpay order representation") from exc

    @staticmethod
    def _payment(obj: dict) -> RemotePayment:
        try:
            return RemotePayment(
                payment_id=obj["id"], order_id=obj["order_id"], amount_paise=obj["amount"],
                currency=obj["currency"], status=obj["status"],
            )
        except Exception as exc:
            raise RemoteContractError("invalid Razorpay payment representation") from exc

    def create_order(self, *, amount_paise: int, currency: str, receipt: str) -> RemoteOrder:
        obj = self._request("POST", "/orders", {"amount": amount_paise, "currency": currency, "receipt": receipt})
        order = self._order(obj)
        if order.status != "created":
            raise RemoteContractError("new order was not returned in created state")
        return order

    def orders_by_receipt(self, *, receipt: str) -> list[RemoteOrder]:
        q = urllib.parse.urlencode({"receipt": receipt})
        obj = self._request("GET", f"/orders?{q}")
        items = obj.get("items", [])
        if not isinstance(items, list):
            raise RemoteContractError("orders.items must be a list")
        return [self._order(x) for x in items]

    def fetch_order(self, *, order_id: str) -> RemoteOrder:
        return self._order(self._request("GET", f"/orders/{urllib.parse.quote(order_id, safe='')}"))

    def payments_for_order(self, *, order_id: str) -> list[RemotePayment]:
        obj = self._request("GET", f"/orders/{urllib.parse.quote(order_id, safe='')}/payments")
        items = obj.get("items", [])
        if not isinstance(items, list):
            raise RemoteContractError("payments.items must be a list")
        return [self._payment(x) for x in items]
