"""Cashier invoice browser with delivery, payment and personnel filters."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from decimal import Decimal
import secrets

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.async_ui import wants_async_web_response, web_json_response
from app.cashier_payment_idempotency import claim as claim_payment_intent, complete as complete_payment_intent
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
        async_web = wants_async_web_response()
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

            due = Decimal(order_balance(order, locking=True)["amount_due"] or 0)
            if due <= 0 or order.payment_status == "PAID":
                raise ValueError("ORDER_NOT_PAYABLE")

            receipt = claim_payment_intent(
                current_user, bar_id, "invoice_pay_cash",
                request.form.get("idempotency_key"), request.form,
                require_key=async_web,
            )
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
            complete_payment_intent(receipt, order_id=order.id)
            db.session.commit()
            message = f"{order.reference} payée en espèces · {due:,.0f} {order.currency}."
            if async_web:
                return web_json_response(
                    data={"order_id": order.id, "refresh_url": url_for(
                        "cashier_invoices_web.invoices", bar_id=bar_id,
                        delivery=_normalize_filter(request.form.get("delivery"), DELIVERY_FILTERS, "all"),
                        payment=_normalize_filter(request.form.get("payment"), PAYMENT_FILTERS, "open"),
                        origin=(request.form.get("origin") or "all").strip().lower(),
                    )},
                    message=message,
                )
            flash(message, "success")
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError, OperationalError) as exc:
            db.session.rollback()
            messages = {
                "CASH_SESSION_REQUIRED": "Ouvrez d'abord votre session de caisse.",
                "ORDER_NOT_PAYABLE": "Cette facture doit être livrée et avoir un solde avant l'encaissement.",
                "NOT_FOUND": "Facture introuvable.",
                "FORBIDDEN": "Encaissement non autorisé.",
                "PAYMENT_ALREADY_PROCESSED": "Ce paiement a déjà été enregistré. Vérifiez la facture.",
                "IDEMPOTENCY_CONFLICT": "Cette tentative de paiement a déjà été utilisée avec d'autres données.",
                "IDEMPOTENCY_KEY_REQUIRED": "Identifiant de paiement manquant. Actualisez la page avant de recommencer.",
            }
            code = str(exc)
            message = messages.get(code, "Paiement refusé. Vérifiez la facture.")
            if async_web:
                status = 403 if code == "FORBIDDEN" else 404 if code == "NOT_FOUND" else (
                    422 if code in {"INVALID_ACTION", "IDEMPOTENCY_KEY_REQUIRED"} else 409
                )
                return web_json_response(error={"code": code if code in messages else "PAYMENT_CONFLICT"},
                                         message=message, status=status)
            flash(message, "danger")
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

    assigned_staff_ids = {
        order.assigned_staff_id
        for order in all_orders
        if order.assigned_staff_id is not None
    }
    counter_creator_ids = {
        order.created_by_id
        for order in all_orders
        if order.assigned_staff_id is None
    }

    # The selector shows current cashiers/servers even before they create an
    # order, while retaining former personnel referenced by the visible orders.
    staff_stmt = select(StaffAssignment).where(
        StaffAssignment.bar_id == bar_id,
        StaffAssignment.role.in_(["CASHIER", "SERVER"]),
    )
    reference_clauses = [StaffAssignment.ended_at.is_(None)]
    if assigned_staff_ids:
        reference_clauses.append(StaffAssignment.id.in_(assigned_staff_ids))
    if counter_creator_ids:
        reference_clauses.append(StaffAssignment.user_id.in_(counter_creator_ids))
    staff_rows = list(db.session.scalars(staff_stmt.where(or_(*reference_clauses))))
    assignments = {assignment.id: assignment for assignment in staff_rows}

    user_ids = {assignment.user_id for assignment in staff_rows}
    users = {
        user.id: user
        for user in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    # One person may have historical assignments. The current assignment wins
    # for the role label, but filtering by person covers all of that person's
    # orders in the bar.
    personnel_by_user = {}
    for assignment in staff_rows:
        user = users.get(assignment.user_id)
        if not user:
            continue
        previous = personnel_by_user.get(user.id)
        if previous is None or (previous["ended"] and assignment.ended_at is None):
            personnel_by_user[user.id] = {
                "user_id": user.id,
                "name": user.display_name,
                "role": assignment.role,
                "ended": assignment.ended_at is not None,
            }

    personnel_filters = sorted(
        (
            {
                "user_id": item["user_id"],
                "name": item["name"],
                "role": item["role"],
            }
            for item in personnel_by_user.values()
        ),
        key=lambda item: (
            0 if item["role"] == "CASHIER" else 1,
            item["name"].casefold(),
            item["user_id"],
        ),
    )

    # Keep the former assignment-based URLs functional for bookmarks and forms
    # already rendered before deployment.
    selected_staff_id = None
    selected_person_id = None
    if origin_filter.startswith("person-"):
        try:
            selected_person_id = int(origin_filter.split("-", 1)[1])
        except (TypeError, ValueError):
            origin_filter = "all"
        else:
            if selected_person_id not in personnel_by_user:
                origin_filter = "all"
                selected_person_id = None
    elif origin_filter.startswith("staff-"):
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

    def order_person_id(order: Order):
        if order.assigned_staff_id is not None:
            assignment = assignments.get(order.assigned_staff_id)
            return assignment.user_id if assignment else None
        return order.created_by_id if order.created_by_id in personnel_by_user else None

    def origin_matches(order: Order) -> bool:
        if selected_person_id is not None:
            return order_person_id(order) == selected_person_id
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
        if order.assigned_staff_id is not None:
            assignment = assignments.get(order.assigned_staff_id)
            user = users.get(assignment.user_id) if assignment else None
            origin_by_order[order.id] = user.display_name if user else "Serveuse"
        else:
            person = personnel_by_user.get(order.created_by_id)
            origin_by_order[order.id] = (
                f"{person['name']} · Caisse / comptoir"
                if person
                else "Caisse / comptoir"
            )
        balance_by_order[order.id] = order_balance(order)

    open_orders = [order for order in all_orders if order.payment_status in {"UNPAID", "PARTIAL"}]
    stats = {
        "waiting": sum(1 for order in open_orders if order.status == "DRAFT"),
        "delivered": sum(1 for order in open_orders if order.status in {"CONFIRMED", "SERVED"}),
        "paid": sum(1 for order in all_orders if order.payment_status == "PAID"),
        "visible": len(invoice_rows),
    }

    context = dict(
        bar=bar,
        invoices=invoice_rows,
        lines_by_order=lines_by_order,
        origin_by_order=origin_by_order,
        balance_by_order=balance_by_order,
        personnel_filters=personnel_filters,
        delivery_filter=delivery_filter,
        payment_filter=payment_filter,
        origin_filter=origin_filter,
        stats=stats,
    )
    if request.args.get("snapshot") == "1":
        # Transfer only the invoice fragment and counters, never the whole layout.
        # ETag avoids sending unchanged cards during the 10-second background poll.
        response = jsonify({
            "html": render_template("cashier_invoices_results.html", **context),
            "stats": stats,
        })
        response.set_etag(sha256(response.get_data()).hexdigest())
        response.cache_control.no_store = True
        response.make_conditional(request)
        return response
    return render_template("cashier_invoices_v2.html", **context)
