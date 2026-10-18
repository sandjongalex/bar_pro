"""Durable intents for cashier financial actions.

The existing idempotency_records unique constraint protects each (bar, actor,
operation, key). Claim, journal entries, audit and receipt share ONE commit.
Legacy HTML POSTs without an intent key keep their original behavior.
"""
from __future__ import annotations

from app.cashier_payment_idempotency import claim
from app.extensions import db
from app.models import utcnow


def financial_claim(actor, bar_id, action, form, *, require_key=False):
    return claim(
        actor, bar_id, "financial." + action, form.get("idempotency_key"),
        form, require_key=require_key,
    )


def financial_complete(receipt, **details):
    receipt.status = "COMPLETED"
    receipt.response_status = 200
    receipt.response_body = details
    receipt.completed_at = utcnow()
    db.session.flush()
