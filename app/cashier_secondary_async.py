"""Opt-in responses for secondary cashier financial forms.

Every success response is issued only after the intent, financial postings and
audit have committed together. The HTML route remains the authoritative fallback.
"""
from __future__ import annotations

from flask import flash, request
from sqlalchemy.exc import IntegrityError

from app.async_ui import wants_async_web_response, web_json_response
from app.cashier_financial_intents import financial_claim, financial_complete
from app.extensions import db


CONFLICT_CODES = {
    "PAYMENT_ALREADY_PROCESSED", "IDEMPOTENCY_CONFLICT", "ALREADY_REVERSED",
    "CASH_SESSION_NOT_OPEN", "CASH_SESSION_REQUIRED", "INSUFFICIENT_DRAWER_CASH",
    "INSUFFICIENT_STAFF_CASH", "CUSTOMER_PAYMENT_LIMIT", "CASE_RETURN_LIMIT",
    "CUSTOMER_CREDIT_ALREADY_SETTLED", "MANUAL_DEBT_REPAYMENT_LIMIT",
    "MANUAL_DEBT_HAS_REPAYMENTS", "VOUCHER_NOT_ACTIVE",
    "VOUCHER_AMOUNT_EXCEEDED", "EXPENSE_NOT_REVERSIBLE",
}


def begin(actor, bar_id, action):
    """Reserve this request before invoking the unchanged business service."""
    return financial_claim(
        actor, bar_id, "secondary." + action, request.form,
        require_key=wants_async_web_response(),
    )


def succeeded(intent, action, message, *, data=None):
    """Commit before emitting an AJAX success; no success on an uncertain write."""
    financial_complete(intent, action=action, **(data or {}))
    db.session.commit()
    if wants_async_web_response():
        return web_json_response(data={"action": action, **(data or {})}, message=message)
    flash(message, "success")
    return None


def failed(exc, message):
    db.session.rollback()
    if isinstance(exc, IntegrityError):
        message = "Écriture déjà enregistrée ou contrainte d'unicité rencontrée. Vérifiez l'historique."
    if wants_async_web_response():
        code = "IDEMPOTENCY_CONFLICT" if isinstance(exc, IntegrityError) else str(exc)
        status = (403 if isinstance(exc, PermissionError) else
                  404 if isinstance(exc, LookupError) else
                  409 if isinstance(exc, IntegrityError) or code in CONFLICT_CODES else 422)
        return web_json_response(error=code, status=status, message=message)
    flash(message, "danger")
    return None
