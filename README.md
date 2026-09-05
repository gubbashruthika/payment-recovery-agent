and `rationale`. The application validates the action and score. Missing keys,
the same recovery engine as webhooks, and `Simulate recovery success` marks an
before accepting Razorpay events; an unset secret never bypasses verification.
the sum of stored `recovered_amount` values. Recovery Rate is recovered revenue
divided by revenue at risk before recovery, and actions, reasons, and outcomes
# AI Revenue Recovery Agent

## Problem

Failed payments cause lost revenue and manual recovery effort. Merchants need a fast, explainable system that can classify payment failures, pick the safest recovery action, and track the outcome without risking unauthorized charges.

## Solution

This project detects failed payments, normalizes the failure reason, applies deterministic rules for known cases, and uses Anthropic Claude only for ambiguous or unknown failures. It then chooses a bounded recovery action such as retry, reminder, or escalate, stores the reasoning in SQLite, and updates a live dashboard with the latest metrics and outcomes.

## Architecture

Razorpay
↓
Webhook
↓
FastAPI
↓
Recovery Agent
↓
Rules / Claude / Fallback
↓
Recovery Action
↓
SQLite
↓
Dashboard

## AI Approach

- Rules-first: known payment failures are handled deterministically.
- Claude for ambiguous cases: unknown or unclear failure reasons are sent to Claude when an API key is configured.
- Constrained actions: the system only allows `retry`, `reminder`, and `escalate`.
- Score validation: Claude must return a score between 0 and 100 and a non-empty reason and rationale.
- Safe fallback: missing credentials, network issues, or invalid model output trigger a fallback that escalates safely.

## Recovery Actions

- Retry: used for temporary failures such as insufficient funds or issuer downtime.
- Reminder: used when customer action is required, such as expired cards or authentication failures.
- Escalate: used for fraud risk, repeated failures, or unsafe scenarios.

## Safety

- Webhook signature verification is enforced before processing any Razorpay event.
- Duplicate webhook payloads are rejected as idempotent events.
- Retry attempts stop at a safe limit before escalating.
- No unauthorized charging is performed.
- No production credentials are used.
- Claude fallback never crashes the app and always chooses the safe path.

## Operating Modes

### Demo Mode

This is the default when `.env` is absent or Razorpay credentials are empty. The app runs locally, creates unique simulated failed payments, applies the same recovery engine used by webhooks, and lets you simulate recovery success. No Razorpay API request or real payment is made.

### Razorpay Test Mode

Test Mode is optional and requires Razorpay Test Mode credentials in a local `.env` file. The application reads:

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`
- `ANTHROPIC_API_KEY` (optional, only for ambiguous-case reasoning)

`RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are used only to create customer-authorized Test Mode payment links. `RAZORPAY_WEBHOOK_SECRET` is used only to verify the `X-Razorpay-Signature` header before a webhook is processed. Configure the Razorpay webhook URL to point to `/webhook/razorpay` and subscribe to the relevant Test Mode events.

Live Razorpay Test Mode execution has not been performed for this submission because no credentials or live test event were available. The code path and signature verification are covered by the automated tests.

### Production Mode

Production Mode is not implemented and is intentionally out of scope. Do not use production credentials with this demo. No production credential, live charge, or automatic card charge is required for the project submission.

## Installation

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

## Dashboard

Open:

http://127.0.0.1:8000

## Demo Steps

1. Open the dashboard.
2. Click "Simulate failed payment".
3. Review the transaction row, failure reason, decision, score, and source.
4. Expand the row to inspect the rationale and recovery timeline.
5. Click "Simulate recovery success" on an action-taken transaction.
6. Show how Revenue Recovered and Recovery Rate update in the dashboard.

## Limitations

- This is a local/demo environment.
- Production deployment is not implemented in this codebase.
- Real money is never used.
- Razorpay Test Mode transactions require valid Test Mode credentials and a configured webhook; they have not been live-verified in this environment.

## API Overview

- `POST /webhook/razorpay` – signature-verified Razorpay receiver
- `POST /simulate/failed-payment` – demo transaction generator
- `POST /transactions/{id}/mark-recovered` – mark a recovery as successful
- `GET /transactions` – list transactions and decisions
- `GET /dashboard/stats` – dashboard metrics and aggregates

## Project Layout

payment-recovery-agent/
├── app/
├── static/
├── tests/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
└── recovery.db (local sqlite file used in demo mode)
