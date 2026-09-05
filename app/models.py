from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    razorpay_payment_id = Column(String, index=True, nullable=True)
    razorpay_order_id = Column(String, index=True, nullable=True)

    customer_email = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)

    amount = Column(Float, nullable=False)          # in currency units (e.g. rupees)
    currency = Column(String, default="INR")

    # lifecycle status: failed -> analyzing -> action_taken -> recovered / lost
    status = Column(String, default="failed", index=True)

    failure_code = Column(String, nullable=True)      # raw Razorpay error code
    failure_reason = Column(String, nullable=True)     # normalized reason bucket
    failure_description = Column(Text, nullable=True)  # raw description from Razorpay

    recovery_score = Column(Float, nullable=True)      # 0-100 recovery confidence
    decided_action = Column(String, nullable=True)     # retry | reminder | escalate | none
    decision_source = Column(String, nullable=True)    # "rules" | "claude" | "fallback"
    decision_rationale = Column(Text, nullable=True)    # short explanation, shown on dashboard

    retry_count = Column(Integer, default=0)
    recovery_attempt_count = Column(Integer, default=0, nullable=False)
    recovered_amount = Column(Float, default=0.0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    recovered_at = Column(DateTime, nullable=True)

    events = relationship("RecoveryEvent", back_populates="transaction", cascade="all, delete-orphan")


class RecoveryEvent(Base):
    """Audit trail of every action the agent takes on a transaction."""
    __tablename__ = "recovery_events"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"))

    action = Column(String)          # retry | reminder | escalate
    result = Column(String)          # success | failed | sent | pending
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    transaction = relationship("Transaction", back_populates="events")
