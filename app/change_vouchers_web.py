"""Cashier UI for customer change vouchers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from app.change_voucher_models import ChangeVoucher, ChangeVoucherTransaction
from app.change_voucher_service import change_voucher_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, Payment, StaffAssignment, User
from app.permissions import permissions


bp = Blueprint("change_vouchers_web", __name__, url_prefix="/bars/<int:bar_id>/cashier/change-vouchers")


def _open_session(bar_id: int):
    return db.session.scalar(
        select(CashSession)
        .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
        .order_by(CashSession.id.desc())
    )


def _cashier_assignment(bar_id: int):
    if current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.role == "CASHIER",
            StaffAssignment.ended_at.is_(None),
        )
    )


def _amount(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        item = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_VOUCHER_AMOUNT") from None
    if not item.is_finite() or item <= 0:
        raise ValueError("INVALID_VOUCHER_AMOUNT")
    return item


def _message(exc) -> str:
    return {
        "VOUCHER_NOT_FOUND": "Bon de monnaie introuvable. Vérifiez le code.",
        "VOUCHER_NOT_ACTIVE": "Ce bon de monnaie est déjà soldé ou n'est plus utilisable.",
        "VOUCHER_AMOUNT_EXCEEDED": "Le montant dépasse le solde disponible du bon ou le reste de la facture.",
        "INVALID_VOUCHER_AMOUNT": "Saisissez un montant valide.",
        "ORDER_NOT_PAYABLE": "Cette facture n'est pas disponible pour ce règlement.",
        "CURRENCY_MISMATCH": "Le bon et la facture n'utilisent pas la même devise.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas assez d'espèces pour rembourser ce montant.",
        "CASH_SESSION_NOT_OPEN": "Ouvrez d'abord la caisse.",
        "FORBIDDEN": "Opération non autorisée.",
        "NOT_FOUND": "Élément introuvable.",
    }.get(str(exc), "Opération refusée. Vérifiez les informations.")


@bp.route("/", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    permissions.require(current_user, "payments.read", bar_id)
    permissions.require(current_user, "payments.record", bar_id)
    if _cashier_assignment(bar_id) is None:
        raise PermissionError("FORBIDDEN")

    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    session = _open_session(bar_id)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        code = (request.form.get("code") or "").strip().upper()
        try:
            if action == "redeem":
                order_id = request.form.get("order_id", type=int)
                if not order_id:
                    raise LookupError("NOT_FOUND")
                tx = change_voucher_service.redeem(
                    current_user,
                    bar_id,
                    code,
                    order_id,
                    _amount(request.form.get("amount")),
                )
                db.session.commit()
                order = db.session.get(Order, tx.target_order_id)
                remaining = Decimal(order_balance(order)["amount_due"] or 0)
                flash(
                    f"Bon {code} utilisé : {tx.amount:,.0f} {tx.currency}. Reste facture : {remaining:,.0f} {tx.currency}.",
                    "success",
                )
                if remaining > 0:
                    return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment"))
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))

            if action == "refund":
                if session is None:
                    raise ValueError("CASH_SESSION_NOT_OPEN")
                tx = change_voucher_service.refund_cash(
                    current_user,
                    bar_id,
                    code,
                    session.id,
                    _amount(request.form.get("amount")),
                )
                db.session.commit()
                flash(f"{tx.amount:,.0f} {tx.currency} remboursés pour le bon {code}.", "success")
                return redirect(url_for("change_vouchers_web.manage", bar_id=bar_id))

            raise ValueError("INVALID_ACTION")
        except (PermissionError, LookupError, ValueError, TypeError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")

    q = (request.args.get("q") or "").strip()
    statement = select(ChangeVoucher).where(ChangeVoucher.bar_id == bar_id)
    if q:
        statement = statement.where(
            or_(
                ChangeVoucher.code.ilike(f"%{q}%"),
                ChangeVoucher.customer_name.ilike(f"%{q}%"),
                ChangeVoucher.customer_phone.ilike(f"%{q}%"),
            )
        )
    vouchers = list(
        db.session.scalars(statement.order_by(ChangeVoucher.id.desc()).limit(120))
    )

    orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .order_by(Order.id.asc())
            .limit(120)
        )
    )
    balances = {item.id: order_balance(item) for item in orders}
    selected_order_id = request.args.get("order_id", type=int)

    return render_template(
        "change_vouchers.html",
        bar=bar,
        session=session,
        vouchers=vouchers,
        orders=orders,
        balances=balances,
        selected_order_id=selected_order_id,
        q=q,
    )


@bp.get("/<int:voucher_id>/print")
@login_required
def print_voucher(bar_id: int, voucher_id: int):
    permissions.require(current_user, "payments.read", bar_id)
    if _cashier_assignment(bar_id) is None:
        raise PermissionError("FORBIDDEN")
    bar = db.session.get(Bar, bar_id)
    voucher = db.session.scalar(
        select(ChangeVoucher).where(
            ChangeVoucher.bar_id == bar_id,
            ChangeVoucher.id == voucher_id,
        )
    )
    if not bar or not voucher:
        raise LookupError("NOT_FOUND")
    payment = db.session.scalar(
        select(Payment).where(
            Payment.bar_id == bar_id,
            Payment.id == voucher.origin_payment_id,
        )
    )
    issuer = db.session.get(User, voucher.issued_by_id)
    transactions = list(
        db.session.scalars(
            select(ChangeVoucherTransaction)
            .where(
                ChangeVoucherTransaction.bar_id == bar_id,
                ChangeVoucherTransaction.voucher_id == voucher.id,
            )
            .order_by(ChangeVoucherTransaction.id)
        )
    )
    return render_template(
        "change_voucher_print.html",
        bar=bar,
        voucher=voucher,
        payment=payment,
        issuer=issuer,
        transactions=transactions,
    )
