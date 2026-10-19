"""Web endpoints used by the inline server/cashier order editor."""
from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.async_ui import wants_async_web_response, web_json_response
from app.extensions import db
from app.models import Order, Product, StaffAssignment, StockBalance
from app.order_concurrency import order_revision
from app.order_edit_service import order_edit_service
from app.suborder_edit_service import edit_cashier_suborder
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.order_line_views import effective_lines_by_order
from app.permissions import permissions
from app.product_display_order import product_order_expression

bp = Blueprint("order_edit_web", __name__, url_prefix="/bars/<int:bar_id>/order-edits")


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


def _message(code) -> str:
    messages = {
        "INVALID_LINES": "La commande doit garder au moins un produit avec une quantité valide.",
        "ORDER_PAID": "Cette commande est déjà payée et ne peut plus être modifiée.",
        "ORDER_NOT_EDITABLE": "Cette commande ne peut plus être modifiée.",
        "SUBORDER_NOT_EDITABLE": "Cette sous-commande ne peut plus être modifiée.",
        "ORDER_TOTAL_BELOW_SETTLED": "Le nouveau total ne peut pas être inférieur au montant déjà encaissé.",
        "ORDER_LINES_AMBIGUOUS": "Les lignes de cette commande ne peuvent pas être modifiées automatiquement.",
        "ORDER_CONFLICT": "Cette commande a changé sur un autre appareil. Rouvrez « Modifier » pour charger la version actuelle.",
        "ORDER_REVISION_REQUIRED": "Rechargez la commande avant de la modifier.",
        "INSUFFICIENT_STOCK": "Stock insuffisant pour ajouter cette quantité.",
        "NOT_FOUND": "Commande ou produit introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à modifier cette commande.",
        "BAR_SUSPENDED": "Le bar est suspendu : les modifications sont bloquées.",
    }
    return messages.get(str(code), "Modification refusée. Vérifiez la commande.")


def _form_lines():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    lines = []
    for product_id, quantity in zip(product_ids, quantities):
        product_id = str(product_id or "").strip()
        quantity = str(quantity or "").strip()
        if not product_id or not quantity:
            continue
        lines.append({"product_id": int(product_id), "quantity": quantity})
    return lines


def _can_view_and_edit(order: Order, assignment) -> tuple[bool, bool]:
    if assignment and assignment.role == "SERVER":
        if order.assigned_staff_id != assignment.id:
            return False, False
        editable = order.status == "DRAFT" and order.payment_status != "PAID"
        return True, editable

    editable = (
        order.status in {"DRAFT", "CONFIRMED", "SERVED"}
        and order.payment_status != "PAID"
    )
    return True, editable


def _redirect_after_edit(bar_id: int, order_id: int, assignment):
    if assignment and assignment.role == "SERVER":
        return redirect(url_for("orders_web.quick", bar_id=bar_id) + "#mes-commandes")
    if assignment and assignment.role == "CASHIER":
        return redirect(
            url_for(
                "cashier_workspace_web.workspace",
                bar_id=bar_id,
                order_id=order_id,
                focus="payment",
            )
            + "#paymentPanel"
        )
    return redirect(url_for("orders_web.quick", bar_id=bar_id) + "#mes-commandes")


@bp.get("/<int:order_id>")
@login_required
def state(bar_id: int, order_id: int):
    decision = permissions.evaluate(current_user, "orders.edit", bar_id)
    if not decision.allowed:
        return jsonify({"success": False, "error": _message(decision.reason)}), 403

    order = db.session.scalar(
        select(Order).where(Order.id == order_id, Order.bar_id == bar_id)
    )
    if not order:
        return jsonify({"success": False, "error": _message("NOT_FOUND")}), 404

    assignment = _assignment(bar_id)
    visible, editable = _can_view_and_edit(order, assignment)
    if not visible:
        return jsonify({"success": False, "error": _message("FORBIDDEN")}), 403

    lines = effective_lines_by_order(bar_id, [order.id]).get(order.id, [])
    revision = order_revision(order)
    stock = {
        item.product_id: Decimal(item.quantity or 0)
        for item in db.session.scalars(
            select(StockBalance).where(StockBalance.bar_id == bar_id)
        )
    }
    delivered = order.status in {"CONFIRMED", "SERVED"}

    line_payload = []
    for line in lines:
        quantity = Decimal(line["quantity"])
        available = stock.get(line["product_id"], Decimal("0"))
        maximum = quantity + available if delivered else max(quantity, available)
        line_payload.append(
            {
                "product_id": line["product_id"],
                "name": line["product_name_snapshot"],
                "quantity": _decimal_text(quantity),
                "stock": _decimal_text(available),
                "max_quantity": _decimal_text(maximum),
            }
        )

    products = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar_id, Product.is_active.is_(True))
            .order_by(product_order_expression(Product.name), Product.name, Product.id)
        )
    )
    product_payload = []
    for product in products:
        available = stock.get(product.id, Decimal("0"))
        if available <= 0:
            continue
        product_payload.append(
            {
                "id": product.id,
                "name": product.name,
                "stock": _decimal_text(available),
                "unit": product.base_unit,
            }
        )

    rounds = []
    if editable and not (assignment and assignment.role != "CASHIER"):
        suborders = list(db.session.scalars(select(OrderSuborder).where(
            OrderSuborder.bar_id == bar_id,
            OrderSuborder.order_id == order.id,
            OrderSuborder.status.in_(["VALIDATED", "PENDING_VALIDATION"]),
        ).order_by(OrderSuborder.sequence_no, OrderSuborder.id)))
        sub_ids = [sub.id for sub in suborders]
        lines_by_sub = {sub.id: [] for sub in suborders}
        if sub_ids:
            for line in db.session.scalars(select(OrderSuborderLine).where(
                OrderSuborderLine.bar_id == bar_id,
                OrderSuborderLine.order_id == order.id,
                OrderSuborderLine.order_suborder_id.in_(sub_ids),
            ).order_by(OrderSuborderLine.order_suborder_id,
                       OrderSuborderLine.line_no, OrderSuborderLine.id)):
                lines_by_sub[line.order_suborder_id].append(line)
        for sub in suborders:
            is_delivered = sub.delivery_status == "DELIVERED"
            rounds.append({
                "id": sub.id, "sequence_no": sub.sequence_no,
                "status": sub.status, "delivery_status": sub.delivery_status,
                "delivered": is_delivered,
                "lines": [{
                    "product_id": row.product_id,
                    "name": row.product_name_snapshot,
                    "quantity": _decimal_text(row.quantity),
                    "stock": _decimal_text(stock.get(row.product_id, Decimal(0))),
                    "max_quantity": _decimal_text(
                        Decimal(row.quantity) + stock.get(row.product_id, Decimal(0))
                        if is_delivered else max(Decimal(row.quantity), stock.get(row.product_id, Decimal(0)))
                    ),
                } for row in lines_by_sub[sub.id]],
            })

    return jsonify(
        {
            "success": True,
            "order": {
                "id": order.id,
                "reference": order.reference,
                "status": order.status,
                "payment_status": order.payment_status,
                "delivered": delivered,
                "editable": editable,
                "revision": revision,
                "lines": line_payload,
            },
            "products": product_payload,
            "suborders": rounds,
        }
    )


@bp.post("/<int:order_id>")
@login_required
def update(bar_id: int, order_id: int):
    assignment = _assignment(bar_id)
    async_web = wants_async_web_response()
    try:
        expected_revision = (request.form.get("order_revision") or "").strip()
        if not expected_revision:
            raise ValueError("ORDER_REVISION_REQUIRED")
        order = order_edit_service.edit(
            current_user,
            bar_id,
            order_id,
            _form_lines(),
            (request.form.get("reason") or "").strip() or None,
            expected_revision=expected_revision,
        )
        db.session.commit()
        message = f"Commande {order.reference} mise à jour."
        if async_web:
            return web_json_response(
                data={"id": order.id, "reference": order.reference,
                      "revision": order_revision(order)}, message=message,
            )
        flash(message, "success")
    except LookupError:
        db.session.rollback()
        if async_web:
            return web_json_response(
                error={"code": "NOT_FOUND"}, message=_message("NOT_FOUND"), status=404,
            )
        abort(404)
    except (PermissionError, ValueError, TypeError, IntegrityError) as exc:
        db.session.rollback()
        code = "INVALID_LINES" if isinstance(exc, IntegrityError) else str(exc)
        if async_web:
            return web_json_response(
                error={"code": code}, message=_message(code),
                status=403 if isinstance(exc, PermissionError) else (
                    409 if code == "ORDER_CONFLICT" else 422),
            )
        flash(_message(code), "danger")
    return _redirect_after_edit(bar_id, order_id, assignment)


@bp.post("/<int:order_id>/suborders/<int:suborder_id>")
@login_required
def update_suborder(bar_id: int, order_id: int, suborder_id: int):
    """Update exactly one historical round. No edit touches other rounds."""
    async_web = wants_async_web_response()
    try:
        sub = edit_cashier_suborder(
            current_user, bar_id, order_id, suborder_id, _form_lines(),
            expected_revision=(request.form.get("order_revision") or "").strip(),
            reason=(request.form.get("reason") or "").strip() or None,
        )
        db.session.commit()
        message = f"Sous-commande {sub.sequence_no} mise à jour. La serveuse voit les nouveaux produits."
        if async_web:
            return web_json_response(
                data={"id": sub.id, "order_id": order_id, "revision": order_revision(
                    db.session.get(Order, order_id))}, message=message,
            )
        flash(message, "success")
    except LookupError:
        db.session.rollback()
        if async_web:
            return web_json_response(error={"code": "NOT_FOUND"},
                                     message=_message("NOT_FOUND"), status=404)
        abort(404)
    except (ValueError, PermissionError, TypeError, IntegrityError) as exc:
        db.session.rollback()
        code = "INVALID_LINES" if isinstance(exc, IntegrityError) else str(exc)
        if async_web:
            return web_json_response(
                error={"code": code}, message=_message(code),
                status=403 if isinstance(exc, PermissionError) else (
                    409 if code in {"ORDER_CONFLICT", "ORDER_TOTAL_BELOW_SETTLED"} else 422),
            )
        flash(_message(code), "danger")
    return _redirect_after_edit(bar_id, order_id, _assignment(bar_id))
