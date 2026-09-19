"""Cashier shift entry page and checkout guard."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, StaffAssignment, User
from app.permissions import permissions

bp = Blueprint("cashier_web", __name__, url_prefix="/bars/<int:bar_id>/cashier-session")


def _cashier_assignment(bar_id: int):
    if not current_user.is_authenticated or current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.role == "CASHIER",
            StaffAssignment.ended_at.is_(None),
        )
    )


def _open_session(bar_id: int):
    return db.session.scalar(
        select(CashSession)
        .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
        .order_by(CashSession.id.desc())
    )


def _reference() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"CAISSE-{stamp}-{secrets.token_hex(2).upper()}"


@bp.before_app_request
def require_cashier_session_before_checkout():
    """Send cashiers to shift opening before they enter the checkout queue."""
    if request.method != "GET" or request.endpoint != "checkout_web.checkout":
        return None
    if not current_user.is_authenticated:
        return None
    bar_id = (request.view_args or {}).get("bar_id")
    if bar_id is None or _cashier_assignment(bar_id) is None:
        return None
    if _open_session(bar_id) is None:
        return redirect(url_for("cashier_web.session", bar_id=bar_id))
    return None


@bp.route("", methods=["GET", "POST"])
@login_required
def session(bar_id: int):
    permissions.require(current_user, "cash.operate", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    current_session = _open_session(bar_id)
    if request.method == "POST":
        try:
            if current_session is not None:
                flash("Une session de caisse est déjà ouverte pour cet établissement.", "info")
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id))
            cash_service.open(
                current_user,
                bar_id,
                _reference(),
                request.form.get("opening_amount", "0"),
            )
            db.session.commit()
            flash("Caisse ouverte. Vous pouvez maintenant traiter et encaisser les commandes.", "success")
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id))
        except (ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            code = str(exc)
            if "CASH_SESSION_ALREADY_OPEN" in code:
                message = "Une session de caisse est déjà ouverte."
            elif "INVALID_OPENING_AMOUNT" in code:
                message = "Le fonds initial doit être un montant valide supérieur ou égal à zéro."
            else:
                message = "Impossible d'ouvrir la caisse. Vérifiez le fonds initial puis réessayez."
            flash(message, "danger")

    current_session = _open_session(bar_id)
    opened_by = db.session.get(User, current_session.opened_by_id) if current_session else None
    expected = cash_service.expected(current_session) if current_session else Decimal("0")

    orders = list(
        db.session.scalars(
            select(Order).where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
        )
    )
    waiting = sum(1 for order in orders if order.status == "DRAFT")
    payable = [order for order in orders if order.status in {"CONFIRMED", "SERVED"}]
    amount_due = sum((order_balance(order)["amount_due"] for order in payable), Decimal("0"))

    return render_template(
        "cashier_session.html",
        bar=bar,
        session=current_session,
        opened_by=opened_by,
        expected=expected,
        waiting=waiting,
        to_pay=len(payable),
        amount_due=amount_due,
        is_cashier=_cashier_assignment(bar_id) is not None,
    )
