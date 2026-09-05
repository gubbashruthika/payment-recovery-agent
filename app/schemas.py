from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SimulateFailedPayment(BaseModel):
    amount: float
    currency: str = "INR"
    failure_code: str
    failure_description: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None

class TransactionOut(BaseModel):
    id: int
    razorpay_payment_id: Optional[str]
    amount: float
    recovered_amount: float
    currency: str
    status: str
    failure_code: Optional[str]
    failure_reason: Optional[str]
    recovery_score: Optional[float]
    decided_action: Optional[str]
    decision_source: Optional[str]
    decision_rationale: Optional[str]
    retry_count: int
    recovery_attempt_count: int
    created_at: datetime
    recovered_at: Optional[datetime]

    class Config:
        from_attributes = True


class DashboardStats(BaseModel):
    total_failed_transactions: int
    total_revenue_at_risk: float
    total_revenue_recovered: float
    recovery_rate_pct: float
    recovery_attempts: int
    escalations: int
    by_action: dict
    by_reason: dict
    by_status: dict
