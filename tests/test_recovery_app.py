import hashlib
import hmac
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_db
from app.main import app
from app.recovery_agent import decide_recovery_action, validate_claude_response


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_known_failure_classification():
    decision = decide_recovery_action({
        "amount": 2500,
        "currency": "INR",
        "failure_code": "insufficient_funds",
        "failure_description": "insufficient funds",
        "retry_count": 0,
        "customer_email": "a@example.com",
        "customer_phone": "9999999999",
    })
    assert decision.action == "retry"
    assert decision.source == "rules"
    assert 0 <= decision.recovery_score <= 100


def test_claude_response_validator_rejects_invalid_action():
    payload = {"action": "charge_customer", "score": 80, "reason": "x", "rationale": "y"}
    assert validate_claude_response(payload) is False


def test_claude_response_validator_accepts_supported_action():
    payload = {"action": "reminder", "score": 70, "reason": "customer_action_needed", "rationale": "the customer needs to update the card"}
    assert validate_claude_response(payload) is True


def test_invalid_webhook_signature_is_rejected(client):
    payload = {"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_test_1"}}}}
    response = client.post(
        "/webhook/razorpay",
        content='{"event":"payment.failed"}',
        headers={"X-Razorpay-Signature": "bad-signature"},
    )
    assert response.status_code == 400


def test_valid_webhook_creates_transaction(client, monkeypatch):
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test-secret"
    monkeypatch.setattr("app.razorpay_client.RAZORPAY_WEBHOOK_SECRET", "test-secret")

    payload = {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {"id": "pay_valid_123", "order_id": "order_123", "amount": 20000, "currency": "INR", "email": "demo@example.com", "contact": "9999999999", "error_code": "insufficient_funds", "error_description": "Insufficient funds"}}},
    }
    raw = __import__("json").dumps(payload).encode()
    sig = hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()

    response = client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    txns = client.get("/transactions").json()
    assert any(t["razorpay_payment_id"] == "pay_valid_123" for t in txns)


def test_duplicate_webhook_is_idempotent(client, monkeypatch):
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = "dup-secret"
    monkeypatch.setattr("app.razorpay_client.RAZORPAY_WEBHOOK_SECRET", "dup-secret")

    payload = {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {"id": "pay_dup_123", "order_id": "order_dup", "amount": 15000, "currency": "INR", "error_code": "expired_card", "error_description": "Card expired"}}},
    }
    raw = __import__("json").dumps(payload).encode()
    sig = hmac.new(b"dup-secret", raw, hashlib.sha256).hexdigest()

    first = client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    second = client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


def test_dashboard_stats_are_calculated(client, db_session):
    db_session.add(models.Transaction(
        razorpay_payment_id="pay_stats_1",
        amount=200,
        currency="INR",
        failure_code="insufficient_funds",
        failure_description="insufficient funds",
        status="failed",
        failure_reason="insufficient_funds",
        recovery_score=70,
        decided_action="retry",
        decision_source="rules",
        recovered_amount=0,
    ))
    db_session.add(models.Transaction(
        razorpay_payment_id="pay_stats_2",
        amount=500,
        currency="INR",
        failure_code="expired_card",
        failure_description="expired card",
        status="recovered",
        failure_reason="expired_card",
        recovery_score=40,
        decided_action="reminder",
        decision_source="rules",
        recovered_amount=500,
    ))
    db_session.commit()

    response = client.get("/dashboard/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_failed_transactions"] >= 2
    assert payload["total_revenue_recovered"] >= 500
    assert payload["recovery_rate_pct"] >= 0


def test_simulate_failed_payment_returns_transaction(client):
    response = client.post(
        "/simulate/failed-payment",
        json={
            "amount": 900,
            "currency": "INR",
            "failure_code": "issuer_down",
            "failure_description": "Bank unavailable",
            "customer_email": "demo@example.com",
            "customer_phone": "9999999999",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"failed", "analyzing", "action_taken", "recovered", "escalated"}
    assert body["decided_action"] in {"retry", "reminder", "escalate"}
