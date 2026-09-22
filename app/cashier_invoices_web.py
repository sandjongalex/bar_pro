"""Cashier invoice browser with delivery, payment and origin filters."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, OrderLine, StaffAssignment, User
from app.payment_services import payment_service
from app.permissions import permissions

bp = Blueprint("cashier_invoices_web", __name__, url_prefix="/bars/<int:bar_id>/cashier")

DELIVERY_FILTERS = {"all", "waiting", "delivered"}
PAYMENT_FILTERS = {"open", "all", "unpaid", "partial", "paid"}


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
    return f"PAY-{stamp}-{secrets.token_hex(2).upper()}"


def _normalize_filter(value: str | None, allowed: set[str], default: str) -> str:
    normalized = (value or default).strip().lower()
    return normalized if normalized in allowed else default


def _redirect_to_filters(bar_id: int):
    delivery = _normalize_filter(request.form.get("delivery"), DELIVERY_FILTERS, "all")
    payment = _normalize_filter(request.form.get("payment"), PAYMENT_FILTERS, "open")
    origin = (request.form.get("origin") or "all").strip().lower()
    return redirect(
        url_for(
            "cashier_invoices_web.invoices",
            bar_id=bar_id,
            delivery=delivery,
            payment=payment,
            origin=origin,
        )
    )


@bp.route("/invoices", methods=["GET", "POST"])
@login_required
def invoices(bar_id: int):
    permissions.require(current_user, "orders.read", bar_id)
    if _cashier_assignment(bar_id) is None:
        raise PermissionError("FORBIDDEN")

    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    if request.method == "POST":
        try:
            action = (request.form.get("action") or "").strip()
            if action != "pay_cash":
                raise ValueError("INVALID_ACTION")

            session = _open_session(bar_id)
            if session is None:
                raise ValueError("CASH_SESSION_REQUIRED")

            order_id = request.form.get("order_id", type=int)
            if not order_id:
                raise LookupError("NOT_FOUND")

            order = db.session.scalar(
                select(Order)
                .where(Order.id == order_id, Order.bar_id == bar_id)
                .with_for_update()
            )
            if not order:
                raise LookupError("NOT_FOUND")
            if order.status not in {"CONFIRMED", "SERVED"}:
                raise ValueError("ORDER_NOT_PAYABLE")

            due = Decimal(order_balance(order)["amount_due"] or 0)
            if due <= 0 or order.payment_status == "PAID":
                raise ValueError("ORDER_NOT_PAYABLE")

            payment_service.record(
                current_user,
                bar_id,
                order.id,
                _reference(),
                "CASH",
                due,
                due,
                0,
                cash_session_id=session.id,
            )
            db.session.commit()
            flash(
                f"{order.reference} payée en espèces · {due:,.0f} {order.currency}.",
                "success",
            )
        except (PermissionError, LookupError, ValueError, TypeError) as exc:
            db.session.rollback()
            messages = {
                "CASH_SESSION_REQUIRED": "Ouvrez d'abord votre session de caisse.",
                "ORDER_NOT_PAYABLE": "Cette facture doit être livrée et avoir un solde avant l'encaissement.",
                "NOT_FOUND": "Facture introuvable.",
                "FORBIDDEN": "Encaissement non autorisé.",
            }
            flash(messages.get(str(exc), "Paiement refusé. Vérifiez la facture."), "danger")
        return _redirect_to_filters(bar_id)

    delivery_filter = _normalize_filter(request.args.get("delivery"), DELIVERY_FILTERS, "all")
    payment_filter = _normalize_filter(request.args.get("payment"), PAYMENT_FILTERS, "open")
    origin_filter = (request.args.get("origin") or "all").strip().lower()

    # Operational invoices only: cancelled orders do not belong in the cashier work queue.
    all_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
            )
            .order_by(Order.id.desc())
            .limit(300)
        )
    )

    assignment_ids = {order.assigned_staff_id for order in all_orders if order.assigned_staff_id is not None}
    assignments = {
        assignment.id: assignment
        for assignment in db.session.scalars(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.id.in_(assignment_ids),
                StaffAssignment.role == "SERVER",
            )
        )
    } if assignment_ids else {}

    user_ids = {assignment.user_id for assignment in assignments.values()}
    users = {
        user.id: user
        for user in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    server_filters = []
    for assignment in assignments.values():
        user = users.get(assignment.user_id)
        server_filters.append(
            {
                "assignment_id": assignment.id,
                "name": user.display_name if user else f"Serveuse #{assignment.id}",
            }
        )
    server_filters.sort(key=lambda item: (item["name"].casefold(), item["assignment_id"]))

    selected_staff_id = None
    if origin_filter.startswith("staff-"):
        try:
            selected_staff_id = int(origin_filter.split("-", 1)[1])
        except (TypeError, ValueError):
            origin_filter = "all"
        else:
            if selected_staff_id not in assignments:
                origin_filter = "all"
                selected_staff_id = None
    elif origin_filter not in {"all", "cashier"}:
        origin_filter = "all"

    def delivery_matches(order: Order) -> bool:
        if delivery_filter == "waiting":
            return order.status == "DRAFT"
        if delivery_filter == "delivered":
            return order.status in {"CONFIRMED", "SERVED"}
        return True

    def payment_matches(order: Order) -> bool:
        if payment_filter == "open":
            return order.payment_status in {"UNPAID", "PARTIAL"}
        if payment_filter == "unpaid":
            return order.payment_status == "UNPAID"
        if payment_filter == "partial":
            return order.payment_status == "PARTIAL"
        if payment_filter == "paid":
            return order.payment_status == "PAID"
        return True

    def origin_matches(order: Order) -> bool:
        if origin_filter == "cashier":
            return order.assigned_staff_id is None
        if selected_staff_id is not None:
            return order.assigned_staff_id == selected_staff_id
        return True

    invoice_rows = [
        order
        for order in all_orders
        if delivery_matches(order) and payment_matches(order) and origin_matches(order)
    ]

    invoice_ids = [order.id for order in invoice_rows]
    lines_by_order = {order_id: [] for order_id in invoice_ids}
    if invoice_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(invoice_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no, OrderLine.id)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

    origin_by_order = {}
    balance_by_order = {}
    for order in invoice_rows:
        assignment = assignments.get(order.assigned_staff_id)
        user = users.get(assignment.user_id) if assignment else None
        origin_by_order[order.id] = user.display_name if user else "Caisse / comptoir"
        balance_by_order[order.id] = order_balance(order)

    open_orders = [order for order in all_orders if order.payment_status in {"UNPAID", "PARTIAL"}]
    stats = {
        "waiting": sum(1 for order in open_orders if order.status == "DRAFT"),
        "delivered": sum(1 for order in open_orders if order.status in {"CONFIRMED", "SERVED"}),
        "paid": sum(1 for order in all_orders if order.payment_status == "PAID"),
        "visible": len(invoice_rows),
    }

    return render_template(
        "cashier_invoices_v2.html",
        bar=bar,
        invoices=invoice_rows,
        lines_by_order=lines_by_order,
        origin_by_order=origin_by_order,
        balance_by_order=balance_by_order,
        server_filters=server_filters,
        delivery_filter=delivery_filter,
        payment_filter=payment_filter,
        origin_filter=origin_filter,
        stats=stats,
    )
