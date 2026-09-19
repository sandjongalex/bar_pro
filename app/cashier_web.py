"""Cashier shift opening, closing and service cash management."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashMovement, CashSession, Order, Payment, Refund, StaffAssignment, User
from app.permissions import permissions

bp = Blueprint("cashier_web", __name__, url_prefix="/bars/<int:bar_id>/cashier-session")

PAYMENT_METHODS = (
    ("CASH", "Espèces"),
    ("MOBILE_MONEY", "Mobile Money"),
    ("CARD", "Carte"),
    ("BANK_TRANSFER", "Virement"),
)


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


def _movement_breakdown(session: CashSession | None):
    """Return one bounded aggregate row for one drawer session."""
    empty = {
        "cash_sales": Decimal("0"),
        "cash_refunds": Decimal("0"),
        "handovers": Decimal("0"),
        "deposits": Decimal("0"),
        "withdrawals": Decimal("0"),
        "other": Decimal("0"),
    }
    if session is None:
        return empty

    amount = CashMovement.amount_delta
    row = db.session.execute(
        select(
            func.coalesce(func.sum(case((CashMovement.payment_id.is_not(None), amount), else_=0)), 0),
            func.coalesce(func.sum(case((CashMovement.refund_id.is_not(None), -amount), else_=0)), 0),
            func.coalesce(func.sum(case((CashMovement.cash_handover_id.is_not(None), amount), else_=0)), 0),
            func.coalesce(func.sum(case((CashMovement.manual_kind == "DEPOSIT", amount), else_=0)), 0),
            func.coalesce(func.sum(case((CashMovement.manual_kind == "WITHDRAWAL", -amount), else_=0)), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            CashMovement.payment_id.is_(None)
                            & CashMovement.refund_id.is_(None)
                            & CashMovement.cash_handover_id.is_(None)
                            & CashMovement.manual_kind.is_(None),
                            amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(
            CashMovement.bar_id == session.bar_id,
            CashMovement.cash_session_id == session.id,
        )
    ).one()
    return {key: Decimal(value or 0) for key, value in zip(empty, row, strict=True)}


def _period_filters(column, session: CashSession):
    filters = [column >= session.opened_at]
    if session.closed_at is not None:
        filters.append(column <= session.closed_at)
    return filters


def _service_summary(session: CashSession | None):
    """Summarise actual receipts and refunds during a drawer service period."""
    methods = {code: label for code, label in PAYMENT_METHODS}
    gross = {code: Decimal("0") for code in methods}
    refunded = {code: Decimal("0") for code in methods}
    if session is None:
        return {
            "methods": methods,
            "gross": gross,
            "refunded": refunded,
            "net": dict(gross),
            "gross_total": Decimal("0"),
            "refund_total": Decimal("0"),
            "net_total": Decimal("0"),
            "orders_touched": 0,
        }

    for method, amount in db.session.execute(
        select(Payment.method, func.coalesce(func.sum(Payment.amount_applied), 0))
        .where(
            Payment.bar_id == session.bar_id,
            *_period_filters(Payment.received_at, session),
        )
        .group_by(Payment.method)
    ):
        if method in gross:
            gross[method] = Decimal(amount or 0)

    for method, amount in db.session.execute(
        select(Refund.method, func.coalesce(func.sum(Refund.amount), 0))
        .where(
            Refund.bar_id == session.bar_id,
            *_period_filters(Refund.refunded_at, session),
        )
        .group_by(Refund.method)
    ):
        if method in refunded:
            refunded[method] = Decimal(amount or 0)

    net = {code: gross[code] - refunded[code] for code in methods}
    orders_touched = int(
        db.session.scalar(
            select(func.count(func.distinct(Payment.order_id))).where(
                Payment.bar_id == session.bar_id,
                *_period_filters(Payment.received_at, session),
            )
        )
        or 0
    )
    gross_total = sum(gross.values(), Decimal("0"))
    refund_total = sum(refunded.values(), Decimal("0"))
    return {
        "methods": methods,
        "gross": gross,
        "refunded": refunded,
        "net": net,
        "gross_total": gross_total,
        "refund_total": refund_total,
        "net_total": gross_total - refund_total,
        "orders_touched": orders_touched,
    }


def _manual_movements(session: CashSession | None):
    if session is None:
        return []
    return list(
        db.session.scalars(
            select(CashMovement)
            .where(
                CashMovement.bar_id == session.bar_id,
                CashMovement.cash_session_id == session.id,
                (CashMovement.manual_kind.is_not(None)) | (CashMovement.reversal_of_id.is_not(None)),
            )
            .order_by(CashMovement.id.desc())
            .limit(30)
        )
    )


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
        action = request.form.get("action", "open")
        try:
            if action == "open":
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

            if action == "close":
                if current_session is None:
                    raise ValueError("CASH_SESSION_NOT_OPEN")
                closed = cash_service.close(
                    current_user,
                    bar_id,
                    current_session.id,
                    request.form.get("counted_closing_amount", ""),
                    request.form.get("reason", ""),
                )
                db.session.commit()
                db.session.refresh(closed)
                difference = Decimal(closed.closing_difference or 0)
                if difference < 0:
                    flash(f"Caisse clôturée avec un manque de {abs(difference):,.0f} {closed.currency}.", "warning")
                elif difference > 0:
                    flash(f"Caisse clôturée avec un excédent de {difference:,.0f} {closed.currency}.", "warning")
                else:
                    flash("Caisse clôturée sans écart.", "success")
                return redirect(url_for("cashier_web.session", bar_id=bar_id, closed_id=closed.id))

            raise ValueError("INVALID_ACTION")
        except (ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            code = str(exc)
            if "CASH_SESSION_ALREADY_OPEN" in code:
                message = "Une session de caisse est déjà ouverte."
            elif "INVALID_OPENING_AMOUNT" in code:
                message = "Le fonds initial doit être un montant valide supérieur ou égal à zéro."
            elif "INVALID_COUNTED_AMOUNT" in code:
                message = "Le montant compté doit être un montant valide supérieur ou égal à zéro."
            elif "CASH_SESSION_NOT_OPEN" in code:
                message = "Cette session de caisse n'est plus ouverte."
            elif action == "close" and not (request.form.get("reason") or "").strip():
                message = "Un motif est obligatoire lorsqu'il existe un écart de caisse."
            else:
                message = "Impossible d'enregistrer l'opération. Vérifiez les montants puis réessayez."
            flash(message, "danger")

    current_session = _open_session(bar_id)
    opened_by = db.session.get(User, current_session.opened_by_id) if current_session else None
    expected = cash_service.expected(current_session) if current_session else Decimal("0")
    movement_breakdown = _movement_breakdown(current_session)

    closed_session = None
    closed_id = request.args.get("closed_id", type=int)
    if closed_id:
        closed_session = db.session.scalar(
            select(CashSession).where(
                CashSession.id == closed_id,
                CashSession.bar_id == bar_id,
                CashSession.status == "CLOSED",
            )
        )

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
        movement_breakdown=movement_breakdown,
        closed_session=closed_session,
        waiting=waiting,
        to_pay=len(payable),
        amount_due=amount_due,
        is_cashier=_cashier_assignment(bar_id) is not None,
    )


@bp.route("/daily", methods=["GET", "POST"])
@login_required
def daily(bar_id: int):
    """Operational service dashboard for a cashier, separate from advanced finance administration."""
    permissions.require(current_user, "cash.operate", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    current_session = _open_session(bar_id)
    if current_session is None:
        flash("Ouvrez d'abord une session de caisse pour gérer la recette du service.", "info")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "movement":
                kind = request.form.get("kind", "")
                cash_service.movement(
                    current_user,
                    bar_id,
                    current_session.id,
                    kind,
                    request.form.get("amount", ""),
                    request.form.get("reason", ""),
                )
                message = "Dépôt de caisse enregistré." if kind == "DEPOSIT" else "Retrait de caisse enregistré."
            elif action == "send_receipt":
                note = (request.form.get("reason") or "").strip()
                reason = "Versement recette du service"
                if note:
                    reason = f"{reason} — {note}"
                cash_service.movement(
                    current_user,
                    bar_id,
                    current_session.id,
                    "WITHDRAWAL",
                    request.form.get("amount", ""),
                    reason,
                )
                message = "Versement de recette enregistré. La caisse attendue a été mise à jour."
            else:
                raise ValueError("INVALID_ACTION")
            db.session.commit()
            flash(message, "success")
        except (PermissionError, ValueError, LookupError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            code = str(exc)
            if "INSUFFICIENT_DRAWER_CASH" in code:
                message = "Le montant demandé dépasse les espèces théoriquement disponibles dans la caisse."
            elif "CASH_SESSION_NOT_OPEN" in code:
                message = "La session de caisse n'est plus ouverte."
            elif "INVALID_MOVEMENT_KIND" in code:
                message = "Le type de mouvement de caisse est invalide."
            else:
                message = "Opération refusée. Vérifiez le montant et le motif."
            flash(message, "danger")
        return redirect(url_for("cashier_web.daily", bar_id=bar_id))

    expected = Decimal(cash_service.expected(current_session) or 0)
    breakdown = _movement_breakdown(current_session)
    summary = _service_summary(current_session)
    available_receipt = max(expected - Decimal(current_session.opening_amount or 0), Decimal("0"))
    manual_movements = _manual_movements(current_session)
    closed_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "CLOSED")
            .order_by(CashSession.closed_at.desc(), CashSession.id.desc())
            .limit(10)
        )
    )

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
        "cashier_daily.html",
        bar=bar,
        session=current_session,
        expected=expected,
        breakdown=breakdown,
        summary=summary,
        available_receipt=available_receipt,
        manual_movements=manual_movements,
        closed_sessions=closed_sessions,
        waiting=waiting,
        to_pay=len(payable),
        amount_due=amount_due,
    )
