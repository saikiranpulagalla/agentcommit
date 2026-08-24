from fastapi.testclient import TestClient

from agentcommit.demo.app import create_app
from agentcommit.demo.engine import DEFAULT_REQUEST, DemoEngine


def test_happy_path_succeeds(tmp_path):
    run = DemoEngine(tmp_path).run("happy", DEFAULT_REQUEST)
    assert run.status == "SUCCEEDED"
    assert run.final["payment_state"] == "CAPTURED"
    assert run.final["execution_state"] == "SUCCEEDED"
    assert run.final["inventory_hold_state"] == "FULFILLED"


def test_stale_product_is_denied_then_replanned(tmp_path):
    run = DemoEngine(tmp_path).run("stale_replan", DEFAULT_REQUEST)
    assert run.status == "SUCCEEDED"
    titles = [e.title for e in run.events]
    assert "Old plan denied at commit" in titles
    assert "Bounded replan succeeded" in titles
    denied = next(e for e in run.events if e.title == "Old plan denied at commit")
    assert "HARD_CONSTRAINT_VIOLATION" in denied.detail or "STALE_PRODUCT_FACTS" in denied.detail


def test_crash_unknown_order_recovers_without_second_create(tmp_path):
    run = DemoEngine(tmp_path).run("crash_recovery", DEFAULT_REQUEST)
    assert run.status == "RECOVERED"
    assert run.final["remote_create_calls"] == 1
    assert any(e.title == "Recovered by deterministic receipt" for e in run.events)


def test_late_capture_becomes_compensation_required(tmp_path):
    run = DemoEngine(tmp_path).run("late_capture", DEFAULT_REQUEST)
    assert run.status == "COMPENSATION_REQUIRED"
    assert run.final["payment_state"] == "CAPTURED"
    assert run.final["execution_state"] == "COMPENSATION_REQUIRED"
    assert run.final["inventory_hold_state"] == "RELEASED"


def test_ambiguous_request_requests_clarification(tmp_path):
    run = DemoEngine(tmp_path).run("happy", "Buy me a good monitor")
    assert run.status == "NEEDS_CLARIFICATION"
    assert run.events[0].title == "Clarification required"


def test_http_demo_contract_and_offline_badge(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "mode": "OFFLINE_DEMO", "real_llm": False, "real_razorpay": False}
    config = client.get("/api/config").json()
    assert len(config["scenarios"]) == 4
    page = client.get("/")
    assert page.status_code == 200
    assert "OFFLINE DEMO" in page.text
    result = client.post("/api/run", json={"scenario": "happy", "request": DEFAULT_REQUEST})
    assert result.status_code == 200
    assert result.json()["status"] == "SUCCEEDED"


def test_api_rejects_unknown_scenario(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path))
    response = client.post("/api/run", json={"scenario": "wrong", "request": DEFAULT_REQUEST})
    assert response.status_code == 422


def test_concurrent_demo_runs_are_isolated_by_demo_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    engine = DemoEngine(tmp_path)
    scenarios = ["happy", "stale_replan", "crash_recovery", "late_capture"] * 2
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda s: engine.run(s, DEFAULT_REQUEST), scenarios))
    assert [r.status for r in runs].count("SUCCEEDED") == 4
    assert [r.status for r in runs].count("RECOVERED") == 2
    assert [r.status for r in runs].count("COMPENSATION_REQUIRED") == 2


def test_ui_escapes_event_payloads_before_rendering(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path))
    page = client.get("/").text
    assert "const esc=" in page
    assert "innerHTML=html" in page
    # Rendering interpolations pass through esc(); raw request/event data is never inserted directly.
    assert "${esc(e.title)}" in page
    assert "${esc(e.detail)}" in page


def test_offline_gateway_failure_modes_and_fetch_states():
    from agentcommit.demo.engine import OfflineDemoGateway
    from agentcommit.payments.razorpay import AmbiguousRemoteOutcome, DefiniteRemoteRejection
    import pytest

    gw = OfflineDemoGateway()
    gw.mode = "definite_reject"
    with pytest.raises(DefiniteRemoteRejection):
        gw.create_order(amount_paise=100, currency="INR", receipt="r-definite")

    gw.mode = "ambiguous_no_create"
    with pytest.raises(AmbiguousRemoteOutcome):
        gw.create_order(amount_paise=100, currency="INR", receipt="r-amb")

    gw.mode = "success"
    order = gw.create_order(amount_paise=100, currency="INR", receipt="r-ok")
    assert gw.fetch_order(order_id=order.order_id).status == "created"
    gw.add_payment(order_id=order.order_id, payment_id="p1", status="failed", amount_paise=100)
    assert gw.fetch_order(order_id=order.order_id).status == "attempted"
    gw.add_payment(order_id=order.order_id, payment_id="p2", status="captured", amount_paise=100)
    assert gw.fetch_order(order_id=order.order_id).status == "paid"
    with pytest.raises(KeyError):
        gw.fetch_order(order_id="missing")


def test_demo_engine_rejects_bad_direct_inputs(tmp_path):
    import pytest
    engine = DemoEngine(tmp_path)
    with pytest.raises(ValueError, match="unsupported demo scenario"):
        engine.run("unknown", DEFAULT_REQUEST)
    with pytest.raises(ValueError, match="invalid buyer request"):
        engine.run("happy", "")
    with pytest.raises(ValueError, match="invalid buyer request"):
        engine.run("happy", "x" * 2001)


def test_no_valid_candidate_is_reported_without_authority(tmp_path):
    engine = DemoEngine(tmp_path)
    engine.PRODUCTS = (
        {
            "sku": "only-bad",
            "price_paise": 3_000_000,
            "attributes": {"screen_size_inches": 27, "resolution": "4K", "usb_c": False, "brand": "Bad"},
            "description": "not USB-C",
        },
    )
    run = engine.run("happy", DEFAULT_REQUEST)
    assert run.status == "NO_VALID_CANDIDATE"
    assert run.events[-1].title == "No valid product"


def test_stale_replan_requires_a_real_alternative(tmp_path):
    import pytest
    engine = DemoEngine(tmp_path)
    engine.PRODUCTS = (
        {
            "sku": "only-good",
            "price_paise": 3_899_900,
            "attributes": {"screen_size_inches": 27, "resolution": "4K", "usb_c": True, "brand": "Solo"},
            "description": "single option",
        },
    )
    with pytest.raises(RuntimeError, match="valid substitute"):
        engine.run("stale_replan", DEFAULT_REQUEST)


def test_demo_internal_missing_row_and_missing_payment_fail_closed(tmp_path):
    import pytest
    from agentcommit.payments.store import PaymentStore
    from agentcommit.payments.service import PaymentService
    from agentcommit.demo.engine import OfflineDemoGateway

    engine = DemoEngine(tmp_path)
    store, payment_store, gateway, service = engine._reset_db()
    with pytest.raises(RuntimeError, match="no row"):
        engine._fetchone("SELECT * FROM intent_specs WHERE 1=0")
    with pytest.raises(RuntimeError, match="payment order missing"):
        engine._capture(payment_store=payment_store, service=service, gateway=gateway, execution_id="never-created", now_ms=engine.NOW)


def test_api_maps_unexpected_demo_exception_to_409(tmp_path, monkeypatch):
    def boom(self, scenario, request=DEFAULT_REQUEST):
        raise RuntimeError("controlled demo failure")
    monkeypatch.setattr(DemoEngine, "run", boom)
    client = TestClient(create_app(state_dir=tmp_path))
    response = client.post("/api/run", json={"scenario": "happy", "request": DEFAULT_REQUEST})
    assert response.status_code == 409
    assert "controlled demo failure" in response.json()["detail"]


def test_demo_uses_process_scoped_database(tmp_path):
    import os
    engine = DemoEngine(tmp_path)
    assert str(os.getpid()) in engine.db_path.name


def test_security_headers_are_set(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path))
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "connect-src 'self'" in csp
