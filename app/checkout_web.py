"""Fast server-rendered checkout for bar orders."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, OrderLine, Payment
from app.payment_services import payment_service
from app.permissions import permissions

bp = Blueprint("checkout_web", __name__, url_prefix="/bars/<int:bar_id>/checkout")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _reference() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"PAY-{stamp}-{secrets.token_hex(2).upper()}"


def _positive_decimal(value, code="INVALID_PAYMENT_AMOUNTS") -> Decimal:
    try:
        amount = Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(code) from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError(code)
    return amount


def _message(code) -> str:
    messages = {
        "NOT_FOUND": "Commande introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à enregistrer ce paiement.",
        "BAR_SUSPENDED": "Le bar est suspendu : les encaissements sont bloqués.",
        "ORDER_NOT_PAYABLE": "Cette commande ne peut pas être encaissée.",
        "PAYMENT_LIMIT_EXCEEDED": "Le montant dépasse le reste à payer.",
        "INVALID_PAYMENT_AMOUNTS": "Vérifiez le montant encaissé et le montant reçu.",
        "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
        "CASH_LOCATION_REQUIRED": "Sélectionnez une caisse ouverte pour un paiement en espèces.",
        "CASH_SESSION_NOT_OPEN": "La caisse sélectionnée n'est plus ouverte.",
        "CURRENCY_MISMATCH": "La devise de la caisse ne correspond pas à celle de la commande.",
        "PROVIDER_REFERENCE_REQUIRED": "Renseignez le prestataire et la référence de transaction ensemble.",
        "INVALID_NONCASH_PAYMENT": "Les informations du paiement non espèces sont invalides.",
    }
    return messages.get(str(code), "Paiement refusé. Vérifiez les informations saisies.")


@bp.route("", methods=["GET", "POST"])
@login_required
def checkout(bar_id):
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_record = permissions.evaluate(current_user, "payments.record", bar_id).allowed

    if request.method == "POST":
        if not can_record:
            raise PermissionError("FORBIDDEN")

        order_id = int(request.form.get("order_id", "0"))
        method = request.form.get("method", "").strip()
        try:
            applied = _positive_decimal(request.form.get("amount_applied"))
            provider_code = request.form.get("provider_code", "").strip() or None
            provider_transaction_id = request.form.get("provider_transaction_id", "").strip() or None

            if method == "CASH":
                presented = _positive_decimal(request.form.get("amount_presented"))
                if presented < applied:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                change = presented - applied
                cash_session_raw = request.form.get("cash_session_id", "").strip()
                cash_session_id = int(cash_session_raw) if cash_session_raw else None
                provider_code = None
                provider_transaction_id = None
            else:
                presented = applied
                change = Decimal("0")
                cash_session_id = None

            payment = payment_service.record(
                current_user,
                bar_id,
                order_id,
                request.form.get("reference", "").strip() or _reference(),
                method,
                presented,
                applied,
                change,
                cash_session_id=cash_session_id,
                provider_code=provider_code,
                provider_transaction_id=provider_transaction_id,
            )
            db.session.commit()

            order = db.session.get(Order, order_id)
            remaining = order_balance(order)["amount_due"] if order else Decimal("0")
            if remaining > 0:
                flash(
                    f"Paiement de {payment.amount_applied:,.0f} {payment.currency} enregistré. Reste {remaining:,.0f} {payment.currency}.",
                    "success",
                )
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

            flash(
                f"Commande {order.reference if order else order_id} entièrement encaissée.",
                "success",
            )
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id))

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence de paiement existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

    orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .order_by(Order.id.desc())
            .limit(100)
        )
    )
    balances = {order.id: order_balance(order) for order in orders}

    order_ids = [order.id for order in orders]
    lines_by_order = {order_id: [] for order_id in order_ids}
    if order_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(order_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

    selected_order = None
    selected_id = request.args.get("order_id", type=int)
    if selected_id:
        selected_order = next((order for order in orders if order.id == selected_id), None)
    if selected_order is None and orders:
        selected_order = orders[0]

    open_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )
    cash_expected = {session.id: cash_service.expected(session) for session in open_sessions}

    recent_payments = list(
        db.session.scalars(
            select(Payment)
            .where(Payment.bar_id == bar_id)
            .order_by(Payment.id.desc())
            .limit(30)
        )
    )
    order_by_id = {
        order.id: order
        for order in db.session.scalars(
            select(Order).where(
                Order.bar_id == bar_id,
                Order.id.in_({payment.order_id for payment in recent_payments} or {-1}),
            )
        )
    }

    total_due = sum((balances[order.id]["amount_due"] for order in orders), Decimal("0"))
    stats = {
        "orders": len(orders),
        "due": total_due,
        "open_cash": len(open_sessions),
        "partial": sum(1 for order in orders if order.payment_status == "PARTIAL"),
    }

    return render_template(
        "checkout.html",
        bar=bar,
        orders=orders,
        balances=balances,
        lines_by_order=lines_by_order,
        selected_order=selected_order,
        open_sessions=open_sessions,
        cash_expected=cash_expected,
        recent_payments=recent_payments,
        order_by_id=order_by_id,
        payment_labels=PAYMENT_LABELS,
        can_record=can_record,
        stats=stats,
    )
