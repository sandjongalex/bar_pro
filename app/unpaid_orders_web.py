"""Shared live monitor for delivered orders that still need payment."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from app.extensions import db, limiter
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, StaffAssignment, User
from app.order_line_views import effective_lines_by_order
from app.order_merge_service import order_merge_service
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.order_suborder_service import order_suborder_service
from app.permissions import permissions

bp = Blueprint("unpaid_orders_web", __name__, url_prefix="/bars/<int:bar_id>/unpaid-orders")


def _assignment(bar_id: int):
    if current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.ended_at.is_(None),
        )
    )


def _decimal_text(value) -> str:
    number = Decimal(value or 0)
    if not number.is_finite():
        return str(number)
    return format(number.normalize(), "f")


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _personnel_context(bar_id: int, orders: list[Order]):
    """Resolve personnel referenced by the queue plus currently assigned staff."""
    assigned_staff_ids = {
        order.assigned_staff_id
        for order in orders
        if order.assigned_staff_id is not None
    }
    counter_creator_ids = {
        order.created_by_id
        for order in orders
        if order.assigned_staff_id is None
    }

    stmt = select(StaffAssignment).where(
        StaffAssignment.bar_id == bar_id,
        StaffAssignment.role.in_(["CASHIER", "SERVER"]),
    )
    clauses = [StaffAssignment.ended_at.is_(None)]
    if assigned_staff_ids:
        clauses.append(StaffAssignment.id.in_(assigned_staff_ids))
    if counter_creator_ids:
        clauses.append(StaffAssignment.user_id.in_(counter_creator_ids))

    staff_rows = list(db.session.scalars(stmt.where(or_(*clauses))))
    assignments = {item.id: item for item in staff_rows}
    user_ids = {item.user_id for item in staff_rows}
    users = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    personnel_by_user = {}
    for item in staff_rows:
        user = users.get(item.user_id)
        if not user:
            continue
        previous = personnel_by_user.get(user.id)
        if previous is None or (previous["ended"] and item.ended_at is None):
            personnel_by_user[user.id] = {
                "user_id": user.id,
                "name": user.display_name,
                "role": item.role,
                "ended": item.ended_at is not None,
            }

    personnel = sorted(
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
    return assignments, users, personnel_by_user, personnel


def _validated_suborders(bar_id: int, order_ids: list[int]):
    """Return invoice additions, split between delivered and awaiting cashier confirmation."""
    result = {
        order_id: {"delivered": [], "pending": []}
        for order_id in order_ids
    }
    if not order_ids:
        return result

    suborders = list(
        db.session.scalars(
            select(OrderSuborder)
            .where(
                OrderSuborder.bar_id == bar_id,
                OrderSuborder.order_id.in_(order_ids),
                OrderSuborder.status == "VALIDATED",
            )
            .order_by(OrderSuborder.order_id, OrderSuborder.sequence_no, OrderSuborder.id)
        )
    )
    if not suborders:
        return result

    ids = [item.id for item in suborders]
    lines_by_suborder = {item.id: [] for item in suborders}
    for line in db.session.scalars(
        select(OrderSuborderLine)
        .where(
            OrderSuborderLine.bar_id == bar_id,
            OrderSuborderLine.order_suborder_id.in_(ids),
        )
        .order_by(OrderSuborderLine.order_suborder_id, OrderSuborderLine.line_no, OrderSuborderLine.id)
    ):
        lines_by_suborder.setdefault(line.order_suborder_id, []).append(line)

    for suborder in suborders:
        line_rows = [
            {
                "name": line.product_name_snapshot,
                "quantity": _decimal_text(line.quantity),
                "total_amount": _decimal_text(line.total_amount),
            }
            for line in lines_by_suborder.get(suborder.id, [])
        ]
        if suborder.delivery_status == "DELIVERED":
            result.setdefault(suborder.order_id, {"delivered": [], "pending": []})["delivered"].extend(line_rows)
            continue
        if suborder.delivery_status == "PENDING":
            result.setdefault(suborder.order_id, {"delivered": [], "pending": []})["pending"].append(
                {
                    "id": suborder.id,
                    "sequence_no": suborder.sequence_no,
                    "total_amount": _decimal_text(suborder.total_amount),
                    "currency": suborder.currency,
                    "note": suborder.note or "",
                    "lines": line_rows,
                }
            )
    return result


def _payload(bar_id: int, staff_filter: str | None = None):
    permissions.require(current_user, "orders.create", bar_id)
    assignment = _assignment(bar_id)
    role = assignment.role if assignment else current_user.category

    query = select(Order).where(
        Order.bar_id == bar_id,
        Order.status.in_(["CONFIRMED", "SERVED"]),
        Order.payment_status.in_(["UNPAID", "PARTIAL"]),
    )
    if role == "SERVER":
        query = query.where(Order.assigned_staff_id == assignment.id)

    all_orders = list(
        db.session.scalars(
            query.order_by(Order.posted_at.asc(), Order.id.asc()).limit(200)
        )
    )

    assignments, users, personnel_by_user, personnel = _personnel_context(bar_id, all_orders)

    selected_filter = "all"
    selected_user_id = None
    if role != "SERVER":
        candidate = (staff_filter or "all").strip().lower()
        if candidate.startswith("person-"):
            try:
                candidate_user_id = int(candidate.split("-", 1)[1])
            except (TypeError, ValueError):
                candidate_user_id = None
            if candidate_user_id in personnel_by_user:
                selected_filter = candidate
                selected_user_id = candidate_user_id

    def order_person_id(order: Order):
        if order.assigned_staff_id is not None:
            staff = assignments.get(order.assigned_staff_id)
            return staff.user_id if staff else None
        return order.created_by_id if order.created_by_id in personnel_by_user else None

    orders = (
        [order for order in all_orders if order_person_id(order) == selected_user_id]
        if selected_user_id is not None
        else all_orders
    )

    order_ids = [order.id for order in orders]
    lines_by_order = effective_lines_by_order(bar_id, order_ids)
    suborders_by_order = _validated_suborders(bar_id, order_ids)
    can_collect = role != "SERVER" and permissions.evaluate(current_user, "payments.read", bar_id).allowed
    can_confirm_delivery = role == "CASHIER" and permissions.evaluate(current_user, "orders.deliver", bar_id).allowed

    rows = []
    total_due = Decimal("0")
    partial = 0
    pending_delivery_count = 0
    for order in orders:
        balance = order_balance(order)
        due = Decimal(balance["amount_due"] or 0)
        total_due += due
        if order.payment_status == "PARTIAL":
            partial += 1

        if order.assigned_staff_id is not None:
            staff = assignments.get(order.assigned_staff_id)
            user = users.get(staff.user_id) if staff else None
            person_name = user.display_name if user else "Serveuse"
        else:
            person = personnel_by_user.get(order.created_by_id)
            person_name = f"{person['name']} · Comptoir" if person else "Comptoir"

        additions = suborders_by_order.get(order.id, {"delivered": [], "pending": []})
        pending_deliveries = []
        for item in additions["pending"]:
            pending_delivery_count += 1
            pending_deliveries.append(
                {
                    **item,
                    "confirm_url": (
                        url_for(
                            "unpaid_orders_web.confirm_delivery",
                            bar_id=bar_id,
                            order_id=order.id,
                            suborder_id=item["id"],
                        )
                        if can_confirm_delivery
                        else None
                    ),
                }
            )
        payment_blocked = bool(pending_deliveries)

        if not payment_blocked and role == "CASHIER":
            action_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id) + "#paymentPanel"
        elif not payment_blocked and can_collect:
            action_url = url_for("checkout_web.checkout", bar_id=bar_id, order_id=order.id)
        else:
            action_url = None

        delivered_lines = [
            {
                "name": line["product_name_snapshot"],
                "quantity": _decimal_text(line["quantity"]),
                "total_amount": _decimal_text(line["total_amount"]),
            }
            for line in lines_by_order.get(order.id, [])
        ]
        delivered_lines.extend(additions["delivered"])

        invoice_name = (order.customer_name_snapshot or "").strip()
        table_name = (order.table_label_snapshot or "").strip()
        display_name = invoice_name or table_name or "COMPTOIR"

        rows.append(
            {
                "id": order.id,
                "reference": order.reference,
                "invoice_name": invoice_name,
                "display_name": display_name,
                "table": table_name or "Sans table",
                "server_name": person_name,
                "payment_status": order.payment_status,
                "currency": order.currency,
                "amount_due": _decimal_text(due),
                "net_sale": _decimal_text(balance["net_sale"]),
                "posted_at": _iso(order.posted_at or order.created_at),
                "notes": order.notes or "",
                "action_url": action_url,
                "payment_blocked": payment_blocked,
                "pending_deliveries": pending_deliveries,
                "lines": delivered_lines,
            }
        )

    return {
        "mode": role,
        "orders": rows,
        "personnel": personnel if role != "SERVER" else [],
        "staff_filter": selected_filter,
        "stats": {
            "count": len(rows),
            "partial": partial,
            "due": _decimal_text(total_due),
            "pending_delivery": pending_delivery_count,
        },
    }


@bp.get("")
@login_required
def monitor(bar_id: int):
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    payload = _payload(bar_id, request.args.get("staff"))
    return render_template(
        "unpaid_orders.html",
        bar=bar,
        payload=payload,
        is_server=payload["mode"] == "SERVER",
        is_cashier=payload["mode"] == "CASHIER",
    )


@bp.post("/merge")
@login_required
def merge_preview(bar_id: int):
    assignment = _assignment(bar_id)
    if assignment is not None and assignment.role == "SERVER":
        abort(404)
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        abort(404)
    try:
        summary = order_merge_service.summary(current_user, bar_id, request.form.getlist("order_ids"))
    except LookupError:
        db.session.rollback()
        abort(404)
    except (PermissionError, ValueError, TypeError) as exc:
        db.session.rollback()
        messages = {
            "MERGE_REQUIRES_MULTIPLE_ORDERS": "Sélectionnez au moins deux commandes à fusionner.",
            "INVALID_MERGE_SELECTION": "La sélection de commandes est invalide.",
            "ORDER_NOT_PAYABLE": "Une des commandes sélectionnées n'est pas encore prête à être encaissée.",
            "ORDER_ALREADY_PAID": "Une des commandes sélectionnées est déjà soldée.",
            "MERGE_CURRENCY_MISMATCH": "Les commandes sélectionnées n'utilisent pas la même devise.",
        }
        flash(messages.get(str(exc), "Impossible de fusionner ces commandes."), "danger")
        return redirect(url_for("unpaid_orders_web.monitor", bar_id=bar_id))

    open_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )
    return render_template(
        "unpaid_order_merge.html",
        bar=bar,
        summary=summary,
        open_sessions=open_sessions,
    )


@bp.post("/merge/payment")
@login_required
def merge_payment(bar_id: int):
    assignment = _assignment(bar_id)
    if assignment is not None and assignment.role == "SERVER":
        abort(404)
    permissions.require(current_user, "payments.record", bar_id)
    try:
        result = order_merge_service.record_payment(
            current_user,
            bar_id,
            request.form.getlist("order_ids"),
            request.form.get("method", ""),
            request.form.get("amount_applied", ""),
            presented=request.form.get("amount_presented", ""),
            cash_session_id=request.form.get("cash_session_id", ""),
            provider_code=(request.form.get("provider_code", "").strip() or None),
            provider_transaction_id=(request.form.get("provider_transaction_id", "").strip() or None),
        )
        db.session.commit()
        flash(
            f"Fusion encaissée : {result['amount']:,.0f} {result['currency']} répartis sur {len(result['allocations'])} commande(s).",
            "success",
        )
    except LookupError:
        db.session.rollback()
        abort(404)
    except (PermissionError, ValueError, TypeError) as exc:
        db.session.rollback()
        messages = {
            "MERGE_REQUIRES_MULTIPLE_ORDERS": "Sélectionnez au moins deux commandes à fusionner.",
            "INVALID_MERGE_SELECTION": "La sélection de commandes est invalide.",
            "ORDER_NOT_PAYABLE": "Une commande doit d'abord être entièrement livrée.",
            "ORDER_ALREADY_PAID": "Une des commandes sélectionnées est déjà soldée.",
            "PAYMENT_LIMIT_EXCEEDED": "Le montant dépasse le reste total à payer.",
            "INVALID_PAYMENT_AMOUNTS": "Vérifiez le montant à affecter et le montant reçu.",
            "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
            "CASH_LOCATION_REQUIRED": "Sélectionnez une caisse ouverte pour les espèces.",
            "CASH_SESSION_REQUIRED": "Ouvrez d'abord une session de caisse.",
            "CASH_SESSION_NOT_OPEN": "La caisse sélectionnée n'est plus ouverte.",
            "PROVIDER_REFERENCE_REQUIRED": "Renseignez le prestataire et la référence Mobile Money.",
        }
        flash(messages.get(str(exc), "Impossible d'encaisser cette fusion."), "danger")
    return redirect(url_for("unpaid_orders_web.monitor", bar_id=bar_id))


@bp.post("/<int:order_id>/suborders/<int:suborder_id>/confirm-delivery")
@login_required
def confirm_delivery(bar_id: int, order_id: int, suborder_id: int):
    """Cashier acknowledgement that a server-added round was physically delivered."""
    assignment = _assignment(bar_id)
    if assignment is None or assignment.role != "CASHIER":
        abort(404)
    permissions.require(current_user, "orders.deliver", bar_id)

    opened = db.session.scalar(
        select(CashSession.id).where(
            CashSession.bar_id == bar_id,
            CashSession.status == "OPEN",
        )
    )
    if not opened:
        flash("Ouvrez votre caisse avant de confirmer une livraison.", "info")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    try:
        suborder = order_suborder_service.deliver_by_cashier(
            current_user,
            bar_id,
            order_id,
            suborder_id,
        )
        db.session.commit()
        flash(
            f"Sous-commande {suborder.sequence_no} confirmée comme livrée. Le stock a été mis à jour.",
            "success",
        )
    except LookupError:
        db.session.rollback()
        abort(404)
    except (PermissionError, ValueError, TypeError) as exc:
        db.session.rollback()
        messages = {
            "SUBORDER_NOT_VALIDATED": "Cet ajout n'est pas prêt à être livré.",
            "SUBORDER_ALREADY_DELIVERED": "Cet ajout a déjà été confirmé comme livré.",
            "SUBORDER_EMPTY": "Cet ajout ne contient aucun produit.",
            "ORDER_PAID": "Cette commande est déjà payée.",
            "ORDER_NOT_EDITABLE": "Cette commande ne peut plus être modifiée.",
            "INSUFFICIENT_STOCK": "Stock insuffisant pour confirmer cette livraison.",
        }
        flash(messages.get(str(exc), "Impossible de confirmer cette livraison."), "danger")

    staff_filter = (request.form.get("staff") or "all").strip()
    values = {"bar_id": bar_id}
    if staff_filter != "all":
        values["staff"] = staff_filter
    return redirect(url_for("unpaid_orders_web.monitor", **values))


@bp.get("/data")
@limiter.exempt
@login_required
def data(bar_id: int):
    if not db.session.get(Bar, bar_id):
        raise LookupError("NOT_FOUND")
    return jsonify(_payload(bar_id, request.args.get("staff")))
