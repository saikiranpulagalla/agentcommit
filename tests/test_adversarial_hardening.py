from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agentcommit.ai import (
    ConstraintOp,
    IntentEvalMetrics,
    PlanningEvalMetrics,
    ReferenceIntentCompiler,
    ScriptedJsonModel,
    StructuredIntentCompiler,
)
from agentcommit.ai.critical import extract_critical_expectation
from agentcommit.demo.engine import DemoEngine
from agentcommit.domain.models import DomainError, ExecutionState, PaymentState
from agentcommit.payments.models import DispatchState, InventoryHoldState, OrderIntentState, RemoteOrder
from agentcommit.payments.store import PaymentStore

from conftest import NOW
from test_v31_payments import FakeGateway, committed, service_for


def _price_limits(intent):
    return [
        constraint.value
        for constraint in intent.hard_constraints
        if constraint.field == "price_paise" and constraint.op is ConstraintOp.LTE
    ]


@pytest.mark.parametrize(
    ("raw_request", "expected_paise"),
    [
        ("Buy a 27-inch 4K USB-C monitor under INR 20000", 2_000_000),
        ("Buy a 27-inch 4K USB-C monitor costing ₹40,000 at most", 4_000_000),
        ("Buy a 27-inch 4K USB-C monitor for Rs. 35 thousand or less", 3_500_000),
        ("Buy a 27-inch 4K USB-C monitor under 60k and at most ₹50,000", 5_000_000),
    ],
)
def test_reference_compiler_preserves_common_explicit_price_caps(raw_request, expected_paise):
    intent = ReferenceIntentCompiler().compile(intent_id="i", buyer_id="b", raw_request=raw_request)
    assert _price_limits(intent) == [expected_paise]


def test_non_price_at_most_phrase_is_not_misread_as_monetary_authority():
    assert extract_critical_expectation("Need delivery in at most 2 days").max_price_paise is None


def test_demo_refuses_capture_when_edited_request_is_below_every_catalog_price(tmp_path):
    run = DemoEngine(tmp_path).run(
        "happy",
        "Buy me the cheapest 27-inch 4K USB-C monitor under INR 20000. You can choose another model if the first becomes unavailable.",
    )
    assert run.status == "NO_VALID_CANDIDATE"
    assert run.final == {}


def test_structured_compiler_rejects_omitted_trailing_budget_constraint():
    model = ScriptedJsonModel([
        {
            "status": "READY",
            "hard_constraints": [],
            "soft_preferences": [],
            "substitution_allowed": False,
            "unresolved_fields": [],
        },
        {
            "status": "READY",
            "hard_constraints": [],
            "soft_preferences": [],
            "substitution_allowed": False,
            "unresolved_fields": [],
        },
    ])
    with pytest.raises(DomainError, match="critical budget"):
        StructuredIntentCompiler(model).compile(
            intent_id="i",
            buyer_id="b",
            raw_request="Find a monitor costing ₹40,000 at most",
        )


def test_expired_unknown_create_becomes_explicit_manual_review_without_releasing_inventory(store, db):
    path = committed(store, "unknownmanual")
    gateway = FakeGateway()
    gateway.mode = "ambiguous_no_create"
    service = service_for(db, gateway, ttl=50)

    assert service.dispatch_pending(now_ms=NOW + 2) == ["CREATE_UNKNOWN"]
    intent = PaymentStore(db).intent_for_execution(path["execution"])
    assert intent is not None

    assert service.recover_unknown_orders(now_ms=NOW + 52) == 0
    intent = PaymentStore(db).intent(intent.local_order_id)
    payment_store = PaymentStore(db)
    assert intent.state is OrderIntentState.CREATE_REQUIRES_MANUAL_REVIEW
    assert payment_store.dispatch(path["execution"]).state is DispatchState.CANCELLED
    assert payment_store.hold(path["execution"]).state is InventoryHoldState.HELD
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?", (path["execution"],)) == ExecutionState.EXECUTION_UNKNOWN.value
    assert store.scalar("SELECT available_quantity FROM products WHERE sku=?", (path["sku"],)) == 0
    with pytest.raises(Exception, match="unbound"):
        service.close_expired_checkout(local_order_id=intent.local_order_id, now_ms=NOW + 52)

    gateway.orders[intent.receipt] = RemoteOrder(
        "order-late-visible", intent.receipt, intent.amount_paise, intent.currency, "paid"
    )
    assert service.recover_unknown_orders(now_ms=NOW + 53) == 1
    assert payment_store.intent(intent.local_order_id).state is OrderIntentState.PAID
    assert payment_store.hold(path["execution"]).state is InventoryHoldState.FULFILLED
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?", (path["execution"],)) == ExecutionState.SUCCEEDED.value


def test_reconciliation_treats_bound_paid_order_as_captured_when_payment_list_is_temporarily_empty(store, db):
    path = committed(store, "paidorder")
    gateway = FakeGateway()
    service = service_for(db, gateway, ttl=50)
    assert service.dispatch_pending(now_ms=NOW + 2) == ["CREATED"]
    payment_store = PaymentStore(db)
    intent = payment_store.intent_for_execution(path["execution"])
    assert intent is not None and intent.remote_order_id is not None

    paid = RemoteOrder(intent.remote_order_id, intent.receipt, intent.amount_paise, intent.currency, "paid")
    state = payment_store.apply_reconciliation(
        local_order_id=intent.local_order_id,
        expected_intent_version=intent.version,
        remote_order=paid,
        remote_payments=[],
        now_ms=NOW + 3,
    )
    assert state is PaymentState.CAPTURED
    assert payment_store.intent(intent.local_order_id).state is OrderIntentState.PAID
    assert payment_store.hold(path["execution"]).state is InventoryHoldState.FULFILLED
    assert store.scalar("SELECT state FROM executions WHERE execution_id=?", (path["execution"],)) == ExecutionState.SUCCEEDED.value


def _load_live_runner():
    script = Path(__file__).parents[1] / "evals" / "run_v42_live.py"
    spec = importlib.util.spec_from_file_location("run_v42_live_adversarial", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_live_evaluator_fails_when_every_intent_compilation_fails(monkeypatch, tmp_path):
    module = _load_live_runner()

    class FakeModel:
        def __init__(self, **_kwargs):
            self.usage = []

    bad_intents = IntentEvalMetrics(
        cases=60, compiled_cases=0, compile_failures=60,
        exact_status_accuracy=0.0, hard_constraint_exact_match=0.0,
        hard_constraint_precision=1.0, hard_constraint_recall=0.0,
        critical_constraint_exact_match=0.0, soft_preference_exact_match=0.0,
        substitution_accuracy=0.0, clarification_exact_match=0.0,
    )
    safe_planner = PlanningEvalMetrics(
        cases=20, outcome_accuracy=1.0, expected_selection_accuracy=1.0,
        unsafe_selection_rate=0.0, average_model_calls=1.0,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(module, "OUT", tmp_path / "v42_live.json")
    monkeypatch.setattr(module, "OpenAIResponsesJsonModel", FakeModel)
    monkeypatch.setattr(module, "evaluate_intent_compiler", lambda **_kwargs: bad_intents)
    monkeypatch.setattr(module, "evaluate_candidate_planner", lambda **_kwargs: safe_planner)

    assert module.main() == 2
    report = json.loads((tmp_path / "v42_live.json").read_text())
    assert report["status"] == "COMPLETED"
    assert report["evaluation_completed"] is True
    assert report["real_llm_accuracy"] == 0.0
    assert report["promotion_gate"]["passed"] is False
