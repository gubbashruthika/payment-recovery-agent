"""AI payment recovery decision engine."""

import json
import logging
import os
from dataclasses import dataclass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
_client = None
ALLOWED_ACTIONS = {"retry", "reminder", "escalate"}


@dataclass
class RecoveryDecision:
    reason: str
    action: str
    recovery_score: float
    source: str
    rationale: str


def get_claude_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
        _client = Anthropic(api_key=api_key)
    return _client


RULE_TABLE = {
    "insufficient_funds": {
        "action": "retry",
        "recovery_score": 68,
        "rationale": "Insufficient funds is often temporary; a carefully timed retry is a plausible recovery path.",
    },
    "issuer_down": {
        "action": "retry",
        "recovery_score": 76,
        "rationale": "The issuer or gateway was temporarily unavailable, so a brief retry has a strong chance of succeeding.",
    },
    "expired_card": {
        "action": "reminder",
        "recovery_score": 42,
        "rationale": "The card is expired and cannot be retried without customer action; a reminder is safer than silent charges.",
    },
    "authentication_failed": {
        "action": "reminder",
        "recovery_score": 51,
        "rationale": "The customer likely failed a 3DS or OTP step; a reminder to retry the payment is the safest next move.",
    },
    "card_declined_generic": {
        "action": "reminder",
        "recovery_score": 35,
        "rationale": "The decline reason is not specific enough to rely on an automatic retry; a customer reminder is the safer route.",
    },
    "restricted_or_fraud_suspected": {
        "action": "escalate",
        "recovery_score": 8,
        "rationale": "The payment appears restricted or fraud-risky; automatic reattempts are not safe and should be escalated.",
    },
    "customer_cancelled": {
        "action": "escalate",
        "recovery_score": 12,
        "rationale": "This looks like a customer-initiated cancellation or abandonment, so human review is safer than automated recovery.",
    },
}

CODE_TO_REASON = {
    "insufficient_funds": "insufficient_funds",
    "insufficient-funds": "insufficient_funds",
    "expired_card": "expired_card",
    "card_expired": "expired_card",
    "authentication_failed": "authentication_failed",
    "otp_failed": "authentication_failed",
    "issuer_down": "issuer_down",
    "issuer_unavailable": "issuer_down",
    "gateway_error": "issuer_down",
    "card_declined": "card_declined_generic",
    "payment_declined": "card_declined_generic",
    "restricted_card": "restricted_or_fraud_suspected",
    "fraudulent": "restricted_or_fraud_suspected",
    "restricted_or_fraud_suspected": "restricted_or_fraud_suspected",
    "cancelled": "customer_cancelled",
}


def normalize_reason(failure_code: str | None, failure_description: str | None) -> str | None:
    code = (failure_code or "").strip().lower().replace(" ", "_")
    if code in CODE_TO_REASON:
        return CODE_TO_REASON[code]

    desc = (failure_description or "").lower()
    for keyword, bucket in [
        ("insufficient", "insufficient_funds"),
        ("expired", "expired_card"),
        ("otp", "authentication_failed"),
        ("authentication", "authentication_failed"),
        ("issuer", "issuer_down"),
        ("timeout", "issuer_down"),
        ("bank unavailable", "issuer_down"),
        ("fraud", "restricted_or_fraud_suspected"),
        ("restricted", "restricted_or_fraud_suspected"),
        ("block", "restricted_or_fraud_suspected"),
        ("cancel", "customer_cancelled"),
    ]:
        if keyword in desc:
            return bucket
    return None


def validate_claude_response(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    action = str(payload.get("action", "")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        return False
    score = payload.get("score")
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        return False
    if not 0 <= score_value <= 100:
        return False
    reason = str(payload.get("reason", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    if not reason or not rationale:
        return False
    return True


CLAUDE_SYSTEM_PROMPT = """You are a payment revenue recovery analyst for an e-commerce platform.
Your job is to recommend the safest revenue-preserving action for a failed payment.

Return ONLY valid JSON with this exact schema:
{
  "action": "retry",
  "score": 70,
  "reason": "short_snake_case_reason",
  "rationale": "one or two sentences explaining the decision"
}

Rules:
- Allowed actions: retry, reminder, escalate.
- Never invent payment information. Do not allow arbitrary financial actions.
- Do not retry fraud, restricted, or suspicious payments.
- Maximize recoverable revenue while avoiding unsafe or repeated payment actions.
- Use a score from 0 to 100. Higher means greater likelihood of successful recovery.
- Keep the reasoning concise and explainable.
- Return only JSON and no markdown fences.
"""


def claude_decide(transaction: dict) -> RecoveryDecision:
    client = get_claude_client()
    payment_id = transaction.get("payment_id") or transaction.get("razorpay_payment_id") or "unknown"
    amount = transaction.get("amount", 0)
    currency = transaction.get("currency", "INR")
    failure_code = transaction.get("failure_code") or "unknown"
    failure_description = transaction.get("failure_description") or "unknown"
    retry_count = int(transaction.get("retry_count", 0) or 0)
    allowed_actions = ["retry", "reminder", "escalate"]

    user_prompt = f"""Payment details:
- payment_id: {payment_id}
- amount: {amount} {currency}
- failure_code: {failure_code}
- failure_description: {failure_description}
- previous_recovery_attempts: {retry_count}
- current_status: {transaction.get('status', 'failed')}
- allowed_actions: {', '.join(allowed_actions)}
- safety_constraints: Never invent financial details, never silently charge a customer, never retry fraud or restricted payments, and only return allowed actions.

Goal: maximize recoverable revenue without unsafe or repeated payment actions.
"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Claude response was not valid JSON")

    if not validate_claude_response(parsed):
        raise ValueError("Claude response failed validation")

    action = str(parsed["action"]).strip().lower()
    reason = str(parsed["reason"]).strip()
    rationale = str(parsed["rationale"]).strip()
    score = float(parsed["score"])

    return RecoveryDecision(
        reason=reason,
        action=action,
        recovery_score=min(100.0, max(0.0, score)),
        source="claude",
        rationale=rationale,
    )


def decide_recovery_action(transaction: dict) -> RecoveryDecision:
    failure_code = transaction.get("failure_code") or ""
    failure_description = transaction.get("failure_description") or ""
    retry_count = int(transaction.get("retry_count", 0) or 0)
    bucket = normalize_reason(failure_code, failure_description)

    if retry_count >= 3:
        return RecoveryDecision(
            reason=bucket or "repeated_failure",
            action="escalate",
            recovery_score=12,
            source="rules",
            rationale="The payment has reached the retry limit. Further automated retries are unsafe and have been escalated.",
        )

    if bucket in {"restricted_or_fraud_suspected", "customer_cancelled"}:
        return RecoveryDecision(
            reason=bucket,
            action="escalate",
            recovery_score=8 if bucket == "restricted_or_fraud_suspected" else 12,
            source="rules",
            rationale="This failure category is not safe for automatic recovery and requires manual review.",
        )

    if bucket and bucket in RULE_TABLE:
        rule = RULE_TABLE[bucket]
        return RecoveryDecision(
            reason=bucket,
            action=rule["action"],
            recovery_score=float(rule["recovery_score"]),
            source="rules",
            rationale=rule["rationale"],
        )

    try:
        return claude_decide(transaction)
    except Exception as exc:
        logger.warning("Claude unavailable or invalid; using safe fallback: %s", exc)
        return RecoveryDecision(
            reason=bucket or "unclassified",
            action="escalate",
            recovery_score=15,
            source="fallback",
            rationale="Claude was unavailable or produced an invalid decision, so the payment was safely escalated for manual review.",
        )
