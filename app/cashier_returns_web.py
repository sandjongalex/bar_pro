"""Simplified returns and refunds workflow for the cashier."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.finance_totals import order_balance
from app.models import (
    Bar,
    CashSession,
    Order,
    OrderLine,
    OrderReturn,
    OrderReturnLine,
    Payment,
    Refund,
)
from app.order_services import order_service
from app.payment_services import payment_service
from app.permissions import permissions

bp = Blueprint("cashier_returns_web", __name__, url_prefix="/bars/<int:bar_id>/cashier-returns")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _reference(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _local_display(value, timezone_name: str) -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name)).strftime("%d/%m/%Y %H:%M")


def _open_session(bar_id: int):
    return db.session.scalar(
        select(CashSession)
        .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
        .order_by(CashSession.id.desc())
    )


def _message(exc) -> str:
    code = str(exc)
    messages = {
        "NOT_FOUND": "Commande, paiement ou retour introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à effectuer cette opération.",
        "ORDER_NOT_RETURNABLE": "Cette commande n'est pas disponible pour un retour.",
        "INVALID_RETURN": "Sélectionnez au moins un produit et une quantité valide.",
        "RETURN_LIMIT_EXCEEDED": "La quantité retournée dépasse la quantité encore retournable.",
        "REASON_REQUIRED": "Le motif du retour ou du remboursement est obligatoire.",
        "REFUND_PAYMENT_LIMIT": "Le remboursement dépasse le montant encore remboursable de ce paiement.",
        "REFUND_RETURN_LIMIT": "Le remboursement dépasse le montant restant de ce retour.",
        "REFUND_OVERPAYMENT_LIMIT": "Ce montant ne correspond pas au crédit créé par le retour.",
        "CASH_SESSION_REQUIRED": "Une caisse ouverte est obligatoire pour rembourser un paiement espèces.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse n'est plus ouverte.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas assez d'espèces théoriques pour ce remboursement.",
        "CREDIT_RETURN_NOT_SUPPORTED": "Les retours sur une commande financée à crédit client doivent être traités depuis Clients & crédits.",
        "POSITIVE_NUMBER_REQUIRED": "Le montant doit être strictement supérieur à zéro.",
        "INVALID_NUMBER": "Le montant saisi est invalide.",
    }
    return messages.get(code, "Opération refusée. Vérifiez les quantités, le montant et la caisse ouverte.")


def _selected_order(bar_id: int, order_id: int | None, query_text: str):
    query = select(Order).where(
        Order.bar_id == bar_id,
        Order.status.in_(["CONFIRMED", "SERVED"]),
        Order.payment_status == "PAID",
    )
    if query_text:
        pattern = f"%{query_text}%"
        query = query.where(
            or_(
                Order.reference.ilike(pattern),
                Order.customer_name_snapshot.ilike(pattern),
                Order.table_label_snapshot.ilike(pattern),
            )
        )
    orders = list(db.session.scalars(query.order_by(Order.updated_at.desc(), Order.id.desc()).limit(80)))
    selected = None
    if order_id:
        selected = db.session.scalar(
            select(Order).where(
                Order.bar_id == bar_id,
                Order.id == order_id,
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.payment_status == "PAID",
            )
        )
    if selected is None and orders:
        selected = orders[0]
    if selected and all(item.id != selected.id for item in orders):
        orders.insert(0, selected)
    return orders, selected


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_manage = (
        permissions.evaluate(current_user, "refunds.record", bar_id).allowed
        and permissions.evaluate(current_user, "orders.edit", bar_id).allowed
    )

    if request.method == "POST":
        if not can_manage:
            raise PermissionError("FORBIDDEN")
        action = request.form.get("action", "")
        order_id = request.form.get("order_id", type=int)
        try:
            if not order_id:
                raise LookupError("NOT_FOUND")
            order = db.session.scalar(
                select(Order).where(Order.bar_id == bar_id, Order.id == order_id).with_for_update()
            )
            if not order:
                raise LookupError("NOT_FOUND")
            balance = order_balance(order)
            if balance["customer_credit"] > 0:
                raise ValueError("CREDIT_RETURN_NOT_SUPPORTED")

            if action == "return_create":
                line_ids = request.form.getlist("order_line_id")
                quantities = request.form.getlist("quantity")
                dispositions = request.form.getlist("disposition")
                lines = []
                for line_id, quantity, disposition in zip(line_ids, quantities, dispositions):
                    raw = (quantity or "").strip()
                    if not raw or Decimal(raw) <= 0:
                        continue
                    lines.append(
                        {
                            "order_line_id": int(line_id),
                            "quantity": raw,
                            "disposition": disposition,
                        }
                    )
                returned = order_service.return_lines(
                    current_user,
                    bar_id,
                    order.id,
                    lines,
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash(
                    f"Retour {returned.reference} enregistré pour {returned.total_amount:,.0f} {returned.currency}. Le stock a été corrigé selon la disposition choisie.",
                    "success",
                )
                return redirect(url_for("cashier_returns_web.manage", bar_id=bar_id, order_id=order.id, return_id=returned.id))

            if action == "refund_return":
                return_id = request.form.get("return_id", type=int)
                payment_id = request.form.get("payment_id", type=int)
                if not return_id or not payment_id:
                    raise LookupError("NOT_FOUND")
                returned = db.session.scalar(
                    select(OrderReturn).where(
                        OrderReturn.bar_id == bar_id,
                        OrderReturn.id == return_id,
                        OrderReturn.order_id == order.id,
                        OrderReturn.status == "POSTED",
                    )
                )
                payment = db.session.scalar(
                    select(Payment).where(
                        Payment.bar_id == bar_id,
                        Payment.id == payment_id,
                        Payment.order_id == order.id,
                    )
                )
                if not returned or not payment:
                    raise LookupError("NOT_FOUND")
                cash_session_id = None
                if payment.method == "CASH":
                    session = _open_session(bar_id)
                    if session is None:
                        raise ValueError("CASH_SESSION_REQUIRED")
                    cash_session_id = session.id
                refund = payment_service.refund(
                    current_user,
                    bar_id,
                    payment.id,
                    _reference("RMB"),
                    request.form.get("amount", ""),
                    request.form.get("reason", "").strip(),
                    order_return_id=returned.id,
                    cash_session_id=cash_session_id,
                )
                db.session.commit()
                flash(
                    f"Remboursement de {refund.amount:,.0f} {refund.currency} enregistré en {PAYMENT_LABELS.get(refund.method, refund.method)}.",
                    "success",
                )
                return redirect(url_for("cashier_returns_web.manage", bar_id=bar_id, order_id=order.id, return_id=returned.id))

            raise ValueError("INVALID_ACTION")
        except (PermissionError, LookupError, ValueError, TypeError, ArithmeticError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Une référence identique existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")
            return redirect(url_for("cashier_returns_web.manage", bar_id=bar_id, order_id=order_id or ""))

    query_text = (request.args.get("q") or "").strip()
    selected_id = request.args.get("order_id", type=int)
    orders, selected = _selected_order(bar_id, selected_id, query_text)
    open_session = _open_session(bar_id)

    lines = []
    payments = []
    returns = []
    balance = None
    return_refunded = {}
    payment_refunded = {}
    return_times = {}
    refund_times = {}
    refunds_by_return = {}
    selected_return_id = request.args.get("return_id", type=int)

    if selected:
        balance = order_balance(selected)
        lines = list(
            db.session.scalars(
                select(OrderLine)
                .where(OrderLine.bar_id == bar_id, OrderLine.order_id == selected.id)
                .order_by(OrderLine.line_no, OrderLine.id)
            )
        )
        returned_by_line = dict(
            db.session.execute(
                select(
                    OrderReturnLine.order_line_id,
                    func.coalesce(func.sum(OrderReturnLine.quantity), 0),
                )
                .join(OrderReturn, OrderReturn.id == OrderReturnLine.order_return_id)
                .where(
                    OrderReturnLine.bar_id == bar_id,
                    OrderReturnLine.order_id == selected.id,
                    OrderReturn.status == "POSTED",
                )
                .group_by(OrderReturnLine.order_line_id)
            ).all()
        )
        for line in lines:
            line.returned_quantity = Decimal(returned_by_line.get(line.id, 0) or 0)
            line.returnable_quantity = max(Decimal(line.quantity) - line.returned_quantity, Decimal("0"))

        payments = list(
            db.session.scalars(
                select(Payment)
                .where(Payment.bar_id == bar_id, Payment.order_id == selected.id)
                .order_by(Payment.received_at, Payment.id)
            )
        )
        returns = list(
            db.session.scalars(
                select(OrderReturn)
                .where(
                    OrderReturn.bar_id == bar_id,
                    OrderReturn.order_id == selected.id,
                    OrderReturn.status == "POSTED",
                )
                .order_by(OrderReturn.posted_at.desc(), OrderReturn.id.desc())
            )
        )
        refunds = list(
            db.session.scalars(
                select(Refund)
                .where(Refund.bar_id == bar_id, Refund.order_id == selected.id)
                .order_by(Refund.refunded_at.desc(), Refund.id.desc())
            )
        )
        for item in refunds:
            payment_refunded[item.payment_id] = payment_refunded.get(item.payment_id, Decimal("0")) + Decimal(item.amount)
            if item.order_return_id is not None:
                return_refunded[item.order_return_id] = return_refunded.get(item.order_return_id, Decimal("0")) + Decimal(item.amount)
                refunds_by_return.setdefault(item.order_return_id, []).append(item)
            refund_times[item.id] = _local_display(item.refunded_at, bar.timezone)
        for item in returns:
            return_times[item.id] = _local_display(item.posted_at, bar.timezone)
            item.refunded_amount = return_refunded.get(item.id, Decimal("0"))
            item.refund_due = max(Decimal(item.total_amount) - item.refunded_amount, Decimal("0"))
        for payment in payments:
            payment.refunded_amount = payment_refunded.get(payment.id, Decimal("0"))
            payment.refundable_amount = max(Decimal(payment.amount_applied) - payment.refunded_amount, Decimal("0"))

    return render_template(
        "cashier_returns.html",
        bar=bar,
        orders=orders,
        selected_order=selected,
        balance=balance,
        lines=lines,
        payments=payments,
        returns=returns,
        refunds_by_return=refunds_by_return,
        return_times=return_times,
        refund_times=refund_times,
        open_session=open_session,
        payment_labels=PAYMENT_LABELS,
        can_manage=can_manage,
        query_text=query_text,
        selected_return_id=selected_return_id,
    )
