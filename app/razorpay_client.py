import hashlib
import hmac
import os

import razorpay
from dotenv import load_dotenv

load_dotenv()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

_client = None


def get_razorpay_key_id():
    return os.getenv("RAZORPAY_KEY_ID") or RAZORPAY_KEY_ID


def get_razorpay_key_secret():
    return os.getenv("RAZORPAY_KEY_SECRET") or RAZORPAY_KEY_SECRET


def get_webhook_secret():
    return os.getenv("RAZORPAY_WEBHOOK_SECRET") or RAZORPAY_WEBHOOK_SECRET


def get_client():
    global _client
    if _client is None:
        key_id = get_razorpay_key_id()
        key_secret = get_razorpay_key_secret()
        if not key_id or not key_secret:
            raise RuntimeError("Razorpay credentials are not configured; running in DEMO mode")
        _client = razorpay.Client(auth=(key_id, key_secret))
    return _client


def verify_webhook_signature(raw_body: bytes, received_signature: str) -> bool:
    """Verify the Razorpay webhook signature. Missing secrets reject the request."""
    secret = get_webhook_secret()
    if not secret:
        return False
    if not received_signature:
        return False
    expected_signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, received_signature)


def create_recovery_payment_link(amount_rupees: float, currency: str, description: str,
                                  customer_email: str | None, customer_phone: str | None) -> dict:
    """Create a customer-authorized payment link in Razorpay Test Mode when credentials exist."""
    client = get_client()
    payload = {
        "amount": int(round(amount_rupees * 100)),
        "currency": currency,
        "description": description,
        "customer": {
            "email": customer_email or "",
            "contact": customer_phone or "",
        },
        "notify": {
            "sms": bool(customer_phone),
            "email": bool(customer_email),
        },
        "reminder_enable": True,
    }
    return client.payment_link.create(payload)
