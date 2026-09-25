"""Cashier UI for customer change vouchers."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.change_voucher_models import ChangeVoucher, ChangeVoucherTransaction
from app.change_voucher_service import change_voucher_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, Payment, StaffAssignment, User
from app.payment_services import payment_service
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


def _reference(prefix: str = "PAY") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _number(value, *, allow_zero: bool = False, code: str = "INVALID_PAYMENT_AMOUNTS") -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(code) from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError(code)
    return amount


def _amount(value):
    text = str(value or "").strip()
    if not text:
        return None
    return _number(text, code="INVALID_VOUCHER_AMOUNT")


def _cash_destination(order: Order, session: CashSession):
    """Keep the same drawer/server-custody rule as the primary cashier workspace."""
    holder = (request.form.get("cash_holder_mode") or "DRAWER").strip().upper()
    if holder != "STAFF":
        return session.id, None
    if order.assigned_staff_id is None:
        raise ValueError("INVALID_STAFF_CASH_HOLDER")
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.id == order.assigned_staff_id,
            StaffAssignment.bar_id == order.bar_id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    if not assignment:
        raise ValueError("INVALID_STAFF_CASH_HOLDER")
    return None, assignment.id


def _message(exc) -> str:
    return {
        "VOUCHER_NOT_FOUND": "Bon de monnaie introuvable. Vérifiez le code.",
        "VOUCHER_NOT_ACTIVE": "Ce bon de monnaie est déjà soldé ou n'est plus utilisable.",
        "VOUCHER_AMOUNT_EXCEEDED": "Le montant dépasse le solde disponible du bon ou le reste de la facture.",
        "INVALID_VOUCHER_AMOUNT": "Saisissez un montant valide.",
        "INVALID_ACTUAL_CHANGE": "La monnaie réellement rendue ne peut pas dépasser la monnaie due.",
        "INVALID_PAYMENT_AMOUNTS": "Vérifiez le montant donné par le client et la monnaie réellement rendue.",
        "PAYMENT_LIMIT_EXCEEDED": "Le montant dépasse le reste à payer.",
        "ORDER_NOT_PAYABLE": "Cette facture n'est pas disponible pour ce règlement.",
        "CURRENCY_MISMATCH": "Le bon et la facture n'utilisent pas la même devise.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas assez d'espèces pour rembourser ce montant.",
        "INSUFFICIENT_STAFF_CASH": "Les espèces disponibles sont insuffisantes.",
        "CASH_SESSION_NOT_OPEN": "Ouvrez d'abord la caisse.",
        "CASH_SESSION_REQUIRED": "Ouvrez d'abord la caisse.",
        "INVALID_STAFF_CASH_HOLDER": "La destination des espèces n'est plus valide.",
        "FORBIDDEN": "Opération non autorisée.",
        "NOT_FOUND": "Élément introuvable.",
    }.get(str(exc), "Opération refusée. Vérifiez les informations.")


@bp.post("/pay")
@login_required
def pay_with_change(bar_id: int):
    """Record a cash payment and issue a voucher for any unreturned change."""
    permissions.require(current_user, "payments.record", bar_id)
    if _cashier_assignment(bar_id) is None:
        raise PermissionError("FORBIDDEN")
    session = _open_session(bar_id)
    if session is None:
        flash("Ouvrez d'abord votre caisse.", "danger")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    order_id = request.form.get("order_id", type=int)
    try:
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        ) if order_id else None
        if not order:
            raise LookupError("NOT_FOUND")
        if order.status not in {"CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_PAYABLE")

        due = Decimal(order_balance(order)["amount_due"] or 0)
        if due <= 0:
            raise ValueError("ORDER_NOT_PAYABLE")
        presented = _number(request.form.get("amount_presented"))
        applied = _number(request.form.get("amount_applied"))
        if applied > due or applied > presented:
            raise ValueError("PAYMENT_LIMIT_EXCEEDED" if applied > due else "INVALID_PAYMENT_AMOUNTS")

        total_change = presented - applied
        actual_raw = str(request.form.get("actual_change_given") or "").strip()
        actual_change = total_change if not actual_raw else _number(actual_raw, allow_zero=True)
        if actual_change > total_change:
            raise ValueError("INVALID_ACTUAL_CHANGE")
        voucher_amount = total_change - actual_change
        cash_session_id, staff_assignment_id = _cash_destination(order, session)
        reference = (request.form.get("reference") or "").strip() or _reference()

        payment = payment_service.record(
            current_user,
            bar_id,
            order.id,
            reference,
            "CASH",
            presented,
            applied,
            actual_change,
            cash_session_id=cash_session_id,
            staff_assignment_id=staff_assignment_id,
            voucher_amount=voucher_amount,
        )

        voucher = None
        if voucher_amount > 0:
            voucher = change_voucher_service.issue(
                current_user,
                bar_id,
                payment.id,
                voucher_amount,
                customer_name=request.form.get("voucher_customer_name"),
                customer_phone=request.form.get("voucher_customer_phone"),
            )

        db.session.commit()
        if voucher is not None:
            flash(
                f"Facture encaissée · {actual_change:,.0f} {order.currency} rendus · bon de monnaie {voucher.code} de {voucher.balance_amount:,.0f} {voucher.currency} créé.",
                "success",
            )
            return redirect(
                url_for("change_vouchers_web.print_voucher", bar_id=bar_id, voucher_id=voucher.id)
            )

        remaining = Decimal(order_balance(db.session.get(Order, order.id))["amount_due"] or 0)
        if remaining > 0:
            flash(f"{payment.amount_applied:,.0f} {payment.currency} encaissés · reste {remaining:,.0f}.", "success")
            return redirect(
                url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment")
            )
        flash(f"{order.reference} payée. Commande suivante prête.", "success")
        return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))
    except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
        db.session.rollback()
        message = "Cette référence existe déjà. Réessayez." if isinstance(exc, IntegrityError) else _message(exc)
        flash(message, "danger")
        target = {"bar_id": bar_id}
        if order_id:
            target["order_id"] = order_id
            target["focus"] = "payment"
        return redirect(url_for("cashier_workspace_web.workspace", **target))


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
