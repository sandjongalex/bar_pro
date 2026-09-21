"""Focused web workflows for server/cashier sub-orders."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Bar,
    CashSession,
    Order,
    Product,
    ProductCategory,
    StaffAssignment,
    StockBalance,
    User,
)
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.order_suborder_service import order_suborder_service
from app.permissions import permissions
from app.product_display_order import product_order_expression

bp = Blueprint("suborders_web", __name__)


def _assignment(bar_id: int):
    if not current_user.is_authenticated or current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.ended_at.is_(None),
        )
    )


def _open_cash_session(bar_id: int):
    return db.session.scalar(
        select(CashSession)
        .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
        .order_by(CashSession.id.desc())
    )


def _cart_lines():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    lines = []
    for product_id, quantity in zip(product_ids, quantities):
        product_id = str(product_id or "").strip()
        quantity = str(quantity or "").strip()
        if not product_id or not quantity:
            continue
        try:
            product_id_value = int(product_id)
        except ValueError:
            raise ValueError("INVALID_LINES") from None
        lines.append({"product_id": product_id_value, "quantity": quantity})
    return lines


def _active_order(bar_id: int, order_id: int):
    return db.session.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.bar_id == bar_id,
            Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
            Order.payment_status.in_(["UNPAID", "PARTIAL"]),
        )
    )


def _builder_data(bar_id: int):
    products = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar_id, Product.is_active.is_(True))
            .order_by(product_order_expression(Product.name), Product.name, Product.id)
        )
    )
    categories = list(
        db.session.scalars(
            select(ProductCategory)
            .where(ProductCategory.bar_id == bar_id, ProductCategory.is_active.is_(True))
            .order_by(ProductCategory.name, ProductCategory.id)
        )
    )
    balances = {
        row.product_id: row.quantity
        for row in db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar_id))
    }
    return products, categories, {item.id: item for item in categories}, balances


def _message(code) -> str:
    messages = {
        "INVALID_LINES": "Ajoutez au moins un produit avec une quantité valide.",
        "INSUFFICIENT_STOCK": "Stock insuffisant pour servir cette sous-commande.",
        "ORDER_NOT_EDITABLE": "Cette commande ne peut plus recevoir de sous-commande.",
        "ORDER_PAID": "Cette commande est déjà payée.",
        "ORDER_SERVER_REQUIRED": "Cette commande doit être liée à une serveuse.",
        "SUBORDER_NOT_VALIDATED": "Cette sous-commande n'est pas prête à être livrée.",
        "SUBORDER_ALREADY_DELIVERED": "Cette sous-commande a déjà été livrée.",
        "SUBORDER_EMPTY": "Cette sous-commande ne contient aucun produit.",
        "FORBIDDEN": "Opération non autorisée.",
        "NOT_FOUND": "Commande ou sous-commande introuvable.",
    }
    return messages.get(str(code), "Opération refusée. Vérifiez les informations.")


@bp.route("/bars/<int:bar_id>/orders/<int:order_id>/suborders/new", methods=["GET", "POST"])
@login_required
def new_suborder(bar_id: int, order_id: int):
    assignment = _assignment(bar_id)
    if assignment is None or assignment.role not in {"SERVER", "CASHIER"}:
        abort(404)

    order = _active_order(bar_id, order_id)
    if not order:
        abort(404)

    if assignment.role == "SERVER":
        permissions.require(current_user, "orders.edit", bar_id)
        if order.assigned_staff_id != assignment.id:
            abort(404)
        mode = "server"
        back_url = url_for("orders_web.quick", bar_id=bar_id) + "#mes-commandes"
    else:
        permissions.require(current_user, "orders.deliver", bar_id)
        if _open_cash_session(bar_id) is None:
            flash("Ouvrez votre caisse avant de servir une sous-commande.", "info")
            return redirect(url_for("cashier_web.session", bar_id=bar_id))
        if order.assigned_staff_id is None:
            abort(404)
        server_assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.id == order.assigned_staff_id,
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.role == "SERVER",
                StaffAssignment.ended_at.is_(None),
            )
        )
        if not server_assignment:
            abort(404)
        mode = "cashier"
        back_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id)

    if request.method == "POST":
        try:
            if mode == "server":
                suborder = order_suborder_service.create_server_addition(
                    current_user,
                    bar_id,
                    order.id,
                    _cart_lines(),
                    note=(request.form.get("note") or "").strip() or None,
                )
                db.session.commit()
                flash(
                    f"Sous-commande {suborder.sequence_no} envoyée à la caisse. Elle reste séparée de la commande initiale.",
                    "success",
                )
            else:
                suborder = order_suborder_service.create_cashier_addition(
                    current_user,
                    bar_id,
                    order.id,
                    _cart_lines(),
                    note=(request.form.get("note") or "").strip() or None,
                )
                db.session.commit()
                flash(
                    f"Sous-commande {suborder.sequence_no} servie. La serveuse doit maintenant la valider.",
                    "success",
                )
            return redirect(back_url)
        except LookupError:
            db.session.rollback()
            abort(404)
        except (PermissionError, ValueError, TypeError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")

    bar = db.session.get(Bar, bar_id)
    if not bar:
        abort(404)
    products, categories, categories_by_id, balances = _builder_data(bar_id)
    return render_template(
        "suborder_builder.html",
        bar=bar,
        order=order,
        mode=mode,
        back_url=back_url,
        products=products,
        categories=categories,
        categories_by_id=categories_by_id,
        balances=balances,
    )


@bp.route("/bars/<int:bar_id>/cashier/suborders", methods=["GET", "POST"])
@login_required
def cashier_queue(bar_id: int):
    assignment = _assignment(bar_id)
    if assignment is None or assignment.role != "CASHIER":
        abort(404)
    permissions.require(current_user, "orders.deliver", bar_id)

    if _open_cash_session(bar_id) is None:
        flash("Ouvrez votre caisse avant de traiter les sous-commandes.", "info")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    if request.method == "POST":
        order_id = request.form.get("order_id", type=int)
        suborder_id = request.form.get("suborder_id", type=int)
        if not order_id or not suborder_id:
            abort(404)
        try:
            suborder = order_suborder_service.deliver_by_cashier(
                current_user,
                bar_id,
                order_id,
                suborder_id,
            )
            db.session.commit()
            flash(f"Sous-commande {suborder.sequence_no} livrée. Stock mis à jour.", "success")
        except LookupError:
            db.session.rollback()
            abort(404)
        except (PermissionError, ValueError, TypeError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")
        return redirect(url_for("suborders_web.cashier_queue", bar_id=bar_id))

    bar = db.session.get(Bar, bar_id)
    if not bar:
        abort(404)

    pending = list(
        db.session.scalars(
            select(OrderSuborder)
            .where(
                OrderSuborder.bar_id == bar_id,
                OrderSuborder.status == "VALIDATED",
                OrderSuborder.delivery_status == "PENDING",
            )
            .order_by(OrderSuborder.id.asc())
            .limit(100)
        )
    )
    pending_ids = [item.id for item in pending]
    pending_order_ids = list({item.order_id for item in pending})
    lines_by_suborder = {item.id: [] for item in pending}
    if pending_ids:
        for line in db.session.scalars(
            select(OrderSuborderLine)
            .where(
                OrderSuborderLine.bar_id == bar_id,
                OrderSuborderLine.order_suborder_id.in_(pending_ids),
            )
            .order_by(OrderSuborderLine.order_suborder_id, OrderSuborderLine.line_no)
        ):
            lines_by_suborder.setdefault(line.order_suborder_id, []).append(line)

    parent_orders = {
        item.id: item
        for item in db.session.scalars(
            select(Order).where(Order.bar_id == bar_id, Order.id.in_(pending_order_ids))
        )
    } if pending_order_ids else {}

    staff_ids = {item.assigned_staff_id for item in parent_orders.values() if item.assigned_staff_id}
    staff = {
        item.id: item
        for item in db.session.scalars(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.id.in_(staff_ids),
            )
        )
    } if staff_ids else {}
    user_ids = {item.user_id for item in staff.values()}
    users = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    server_name_by_order = {}
    for order in parent_orders.values():
        staff_assignment = staff.get(order.assigned_staff_id)
        user = users.get(staff_assignment.user_id) if staff_assignment else None
        server_name_by_order[order.id] = user.display_name if user else "Serveuse"

    active_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.assigned_staff_id.is_not(None),
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .order_by(Order.id.desc())
            .limit(80)
        )
    )

    return render_template(
        "cashier_suborders.html",
        bar=bar,
        pending=pending,
        lines_by_suborder=lines_by_suborder,
        parent_orders=parent_orders,
        server_name_by_order=server_name_by_order,
        active_orders=active_orders,
    )
