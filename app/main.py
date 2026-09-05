import json
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db, initialize_database
from .razorpay_client import create_recovery_payment_link, verify_webhook_signature
from .recovery_agent import decide_recovery_action

Base.metadata.create_all(bind=engine)
initialize_database()

app = FastAPI(title="AI Payment Revenue Recovery Agent")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def dashboard_page():
    return FileResponse("static/dashboard.html")


def process_failed_transaction(db: Session, txn: models.Transaction):
    decision = decide_recovery_action({
        "amount": txn.amount,
        "currency": txn.currency,
        "failure_code": txn.failure_code,
        "failure_description": txn.failure_description,
        "retry_count": txn.retry_count,
        "customer_email": txn.customer_email,
        "customer_phone": txn.customer_phone,
        "payment_id": txn.razorpay_payment_id,
        "status": txn.status,
    })

    txn.failure_reason = decision.reason
    txn.recovery_score = decision.recovery_score
    txn.decided_action = decision.action
    txn.decision_source = decision.source
    txn.decision_rationale = decision.rationale
    txn.status = "analyzing"
    txn.updated_at = datetime.utcnow()
    db.commit()

    if decision.action in {"retry", "reminder"}:
        txn.retry_count += 1
        txn.recovery_attempt_count = txn.retry_count
        useful_message = (
            "We noticed your payment did not go through — please retry the payment safely."
            if decision.action == "retry"
            else "Your payment needs attention: update your card or retry the checkout to complete the payment."
        )
        try:
            link = create_recovery_payment_link(
                amount_rupees=txn.amount,
                currency=txn.currency,
                description=useful_message,
                customer_email=txn.customer_email,
                customer_phone=txn.customer_phone,
            )
            note = f"Recovery action created: payment link ready for customer follow-up. Link: {link.get('short_url', 'n/a')}"
            result = "sent"
        except Exception as exc:  # pragma: no cover - runtime external integration issue
            note = f"Recovery action created in demo mode because live Razorpay credentials were unavailable ({exc})."
            result = "simulated"
        txn.status = "action_taken"
    elif decision.action == "escalate":
        note = "Escalated for manual review because automated recovery was unsafe or the retry limit was reached."
        result = "pending"
        txn.status = "escalated"
    else:
        note = "No recovery action taken because the scenario was not worth pursuing."
        result = "skipped"
        txn.status = "lost"

    event = models.RecoveryEvent(
        transaction_id=txn.id,
        action=decision.action,
        result=result,
        note=note,
    )
    db.add(event)
    db.commit()
    db.refresh(txn)
    return txn


@app.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed JSON payload")

    event = payload.get("event")
    if event == "payment.failed":
        entity = payload.get("payload", {}).get("payment", {}).get("entity")
        if not entity or not entity.get("id"):
            raise HTTPException(status_code=400, detail="Malformed payment.failed payload")

        existing = db.query(models.Transaction).filter(models.Transaction.razorpay_payment_id == entity.get("id")).first()
        if existing:
            return {"status": "ok", "duplicate": True}

        txn = models.Transaction(
            razorpay_payment_id=entity.get("id"),
            razorpay_order_id=entity.get("order_id"),
            amount=(entity.get("amount", 0) or 0) / 100,
            currency=entity.get("currency", "INR"),
            customer_email=entity.get("email"),
            customer_phone=entity.get("contact"),
            failure_code=entity.get("error_code"),
            failure_description=entity.get("error_description"),
            status="failed",
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)
        process_failed_transaction(db, txn)
        return {"status": "ok", "duplicate": False}

    if event in {"payment_link.paid", "payment.captured"}:
        entity = (payload.get("payload", {}).get("payment", {}).get("entity")
                  or payload.get("payload", {}).get("payment_link", {}).get("entity")
                  or {})
        payment_id = entity.get("id")
        order_id = entity.get("order_id")
        matched = db.query(models.Transaction)
        if payment_id:
            matched = matched.filter(models.Transaction.razorpay_payment_id == payment_id)
        elif order_id:
            matched = matched.filter(models.Transaction.razorpay_order_id == order_id)
        else:
            return {"status": "ok"}

        matched = matched.filter(models.Transaction.status.in_(["action_taken", "analyzing"])).first()
        if matched:
            matched.status = "recovered"
            matched.recovered_amount = matched.amount
            matched.recovered_at = datetime.utcnow()
            matched.updated_at = datetime.utcnow()
            db.commit()
        return {"status": "ok"}

    return {"status": "ok", "ignored": True}


@app.post("/simulate/failed-payment", response_model=schemas.TransactionOut)
def simulate_failed_payment(body: schemas.SimulateFailedPayment, db: Session = Depends(get_db)):
    if body.amount <= 0:
        raise HTTPException(status_code=422, detail="Amount must be greater than zero")
    txn = models.Transaction(
        razorpay_payment_id=f"demo_pay_{uuid.uuid4().hex[:16]}",
        amount=body.amount,
        currency=body.currency,
        failure_code=body.failure_code,
        failure_description=body.failure_description,
        customer_email=body.customer_email,
        customer_phone=body.customer_phone,
        status="failed",
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return process_failed_transaction(db, txn)


@app.post("/transactions/{txn_id}/mark-recovered", response_model=schemas.TransactionOut)
def mark_recovered(txn_id: int, db: Session = Depends(get_db)):
    txn = db.query(models.Transaction).filter(models.Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.status not in {"action_taken", "analyzing"}:
        raise HTTPException(status_code=409, detail="Only an active recovery can be marked recovered")
    txn.status = "recovered"
    txn.recovered_amount = txn.amount
    txn.recovered_at = datetime.utcnow()
    txn.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(txn)
    return txn


@app.get("/transactions", response_model=list[schemas.TransactionOut])
def list_transactions(db: Session = Depends(get_db)):
    return db.query(models.Transaction).order_by(models.Transaction.created_at.desc()).all()


@app.get("/transactions/{txn_id}", response_model=schemas.TransactionOut)
def get_transaction(txn_id: int, db: Session = Depends(get_db)):
    txn = db.query(models.Transaction).filter(models.Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn


@app.get("/dashboard/stats", response_model=schemas.DashboardStats)
def dashboard_stats(db: Session = Depends(get_db)):
    txns = db.query(models.Transaction).all()
    total = len(txns)
    at_risk = sum(t.amount for t in txns if t.status != "recovered")
    recovered = sum(t.recovered_amount or 0 for t in txns)
    rate = (recovered / at_risk * 100) if at_risk > 0 else 0.0
    recovery_attempts = sum((t.recovery_attempt_count or 0) for t in txns)
    escalations = sum(1 for t in txns if (t.decided_action == "escalate") or (t.status == "escalated"))

    by_action = {}
    by_reason = {}
    by_status = {}
    for t in txns:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        if t.decided_action:
            by_action[t.decided_action] = by_action.get(t.decided_action, 0) + 1
        if t.failure_reason:
            by_reason[t.failure_reason] = by_reason.get(t.failure_reason, 0) + 1

    return schemas.DashboardStats(
        total_failed_transactions=total,
        total_revenue_at_risk=round(at_risk, 2),
        total_revenue_recovered=round(recovered, 2),
        recovery_rate_pct=round(rate, 1),
        recovery_attempts=recovery_attempts,
        escalations=escalations,
        by_action=by_action,
        by_reason=by_reason,
        by_status=by_status,
    )
