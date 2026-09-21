from datetime import datetime, timezone
from decimal import Decimal
import secrets

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import api_required
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, BarTable, Order, OrderLine, Product, ProductCategory, StaffAssignment, StockBalance, User
from app.order_services import order_service
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.order_suborder_service import order_suborder_service
from app.permissions import permissions
from app.product_display_order import product_order_expression

bp = Blueprint("orders", __name__, url_prefix="/api/v1/bars/<int:bar_id>/orders")
web_bp = Blueprint("orders_web", __name__, url_prefix="/bars/<int:bar_id>/orders")

STATUS_LABELS = {
    "DRAFT": "En attente",
    "CONFIRMED": "À payer",
    "SERVED": "Servie",
    "CANCELLED": "Annulée",
}

PAYMENT_STATUS_LABELS = {
    "UNPAID": "Non payé",
    "PARTIAL": "Paiement partiel",
    "PAID": "Payée",
}

SUBORDER_STATUS_LABELS = {
    "PENDING_VALIDATION": "À valider",
    "VALIDATED": "Validée",
    "REJECTED": "Rejetée",
    "CANCELLED": "Annulée",
}

SUBORDER_DELIVERY_LABELS = {
    "PENDING": "À livrer",
    "DELIVERED": "Livrée",
}


def _reference():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"CMD-{stamp}-{secrets.token_hex(2).upper()}"


def _message(code):
    messages = {
        "INVALID_LINES": "Ajoutez au moins un produit avec une quantité valide.",
        "ORDER_EMPTY": "La commande ne contient aucun produit.",
        "ORDER_NOT_DRAFT": "Cette commande n'est plus en attente.",
        "ORDER_NOT_CONFIRMED": "Cette commande n'est pas disponible pour cette opération.",
        "ORDER_NOT_CANCELLABLE": "Cette commande ne peut plus être annulée.",
        "ORDER_NOT_EDITABLE": "Cette commande ne peut plus être modifiée.",
        "ORDER_NOTES_TOO_LONG": "Les notes de cette commande sont trop longues.",
        "ORDER_PAID": "Cette facture est déjà payée.",
        "SUBORDER_NOT_PENDING": "Cet ajout a déjà été traité.",
        "REASON_REQUIRED": "Le motif d'annulation est obligatoire.",
        "INSUFFICIENT_STOCK": "Stock insuffisant pour cette commande.",
        "NOT_FOUND": "Produit, table ou commande introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à effectuer cette opération.",
        "BAR_SUSPENDED": "Le bar est suspendu : les ventes sont bloquées.",
    }
    return messages.get(str(code), "Opération refusée. Vérifiez la commande.")


def _cart_lines():
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


def _assignment(bar_id):
    if current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.ended_at.is_(None),
        )
    )


@web_bp.get("/<int:order_id>/detail")
@login_required
def detail(bar_id: int, order_id: int):
    permissions.require(current_user, "orders.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    order = db.session.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.bar_id == bar_id,
        )
    )
    if not order:
        raise LookupError("NOT_FOUND")

    assignment = _assignment(bar_id)
    is_server = bool(assignment and assignment.role == "SERVER")
    is_cashier = bool(assignment and assignment.role == "CASHIER")

    # A server may inspect only orders assigned to that exact active assignment.
    # Return NOT_FOUND instead of exposing whether another server's order exists.
    if is_server and order.assigned_staff_id != assignment.id:
        raise LookupError("NOT_FOUND")

    lines = list(
        db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id == order.id)
            .order_by(OrderLine.line_no, OrderLine.id)
        )
    )
    original_total = sum((line.total_amount or Decimal("0") for line in lines), Decimal("0"))

    suborders = list(
        db.session.scalars(
            select(OrderSuborder)
            .where(OrderSuborder.bar_id == bar_id, OrderSuborder.order_id == order.id)
            .order_by(OrderSuborder.sequence_no, OrderSuborder.id)
        )
    )
    suborder_lines_by_id = {suborder.id: [] for suborder in suborders}
    suborder_ids = [suborder.id for suborder in suborders]
    if suborder_ids:
        for line in db.session.scalars(
            select(OrderSuborderLine)
            .where(
                OrderSuborderLine.bar_id == bar_id,
                OrderSuborderLine.order_id == order.id,
                OrderSuborderLine.order_suborder_id.in_(suborder_ids),
            )
            .order_by(OrderSuborderLine.order_suborder_id, OrderSuborderLine.line_no, OrderSuborderLine.id)
        ):
            suborder_lines_by_id.setdefault(line.order_suborder_id, []).append(line)

    assigned_staff_name = "Comptoir"
    if order.assigned_staff_id:
        assigned_staff = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.id == order.assigned_staff_id,
            )
        )
        if assigned_staff:
            assigned_user = db.session.get(User, assigned_staff.user_id)
            if assigned_user:
                assigned_staff_name = assigned_user.display_name

    balance = order_balance(order)
    if is_cashier:
        return_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id)
    else:
        return_url = url_for("orders_web.quick", bar_id=bar_id) + "#mes-commandes"

    return render_template(
        "order_detail.html",
        bar=bar,
        order=order,
        lines=lines,
        original_total=original_total,
        suborders=suborders,
        suborder_lines_by_id=suborder_lines_by_id,
        assigned_staff_name=assigned_staff_name,
        balance=balance,
        status_labels=STATUS_LABELS,
        payment_status_labels=PAYMENT_STATUS_LABELS,
        suborder_status_labels=SUBORDER_STATUS_LABELS,
        suborder_delivery_labels=SUBORDER_DELIVERY_LABELS,
        return_url=return_url,
        is_server=is_server,
        is_cashier=is_cashier,
    )


@web_bp.route("/new", methods=["GET", "POST"])
@login_required
def quick(bar_id):
    permissions.require(current_user, "orders.create", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    assignment = _assignment(bar_id)
    is_server = bool(assignment and assignment.role == "SERVER")
    can_edit = permissions.evaluate(current_user, "orders.edit", bar_id).allowed
    can_pay = permissions.evaluate(current_user, "payments.read", bar_id).allowed

    if request.method == "POST":
        action = request.form.get("action", "create")
        try:
            if action == "create":
                table_id_raw = request.form.get("table_id", "").strip()
                table_id = int(table_id_raw) if table_id_raw else None
                order = order_service.create(
                    current_user,
                    bar_id,
                    request.form.get("reference", "").strip() or _reference(),
                    _cart_lines(),
                    table_id=table_id,
                    notes=request.form.get("notes", "").strip() or None,
                )
                db.session.commit()
                flash(
                    f"Commande {order.reference} envoyée à la caisse · {order.total_amount:,.0f} {order.currency}.",
                    "success",
                )
                if can_pay and not is_server:
                    return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order.id))

            elif action == "note":
                if not can_edit:
                    raise PermissionError("FORBIDDEN")
                order_service.append_note(
                    current_user,
                    bar_id,
                    int(request.form.get("order_id", "0")),
                    request.form.get("note", "").strip(),
                )
                db.session.commit()
                flash("Note complémentaire envoyée à la caisse.", "success")

            elif action == "cancel":
                if not can_edit:
                    raise PermissionError("FORBIDDEN")
                order = order_service.cancel(
                    current_user,
                    bar_id,
                    int(request.form.get("order_id", "0")),
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash(f"Commande {order.reference} annulée.", "success")

            elif action == "validate_suborder":
                if not is_server:
                    raise PermissionError("FORBIDDEN")
                suborder = order_suborder_service.validate_by_server(
                    current_user,
                    bar_id,
                    int(request.form.get("order_id", "0")),
                    int(request.form.get("suborder_id", "0")),
                )
                db.session.commit()
                flash(
                    f"Sous-commande {suborder.sequence_no} validée. Elle reste séparée de la commande initiale.",
                    "success",
                )

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence de commande existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")

        return redirect(url_for("orders_web.quick", bar_id=bar_id))

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
    categories_by_id = {category.id: category for category in categories}

    balances = {
        balance.product_id: balance.quantity
        for balance in db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar_id))
    }

    tables = list(
        db.session.scalars(
            select(BarTable)
            .where(BarTable.bar_id == bar_id, BarTable.is_active.is_(True))
            .order_by(BarTable.label, BarTable.id)
        )
    )

    recent_query = select(Order).where(
        Order.bar_id == bar_id,
        Order.status.in_(["DRAFT", "CONFIRMED", "SERVED", "CANCELLED"]),
    )
    if is_server:
        recent_query = recent_query.where(Order.assigned_staff_id == assignment.id)
    recent_orders = list(db.session.scalars(recent_query.order_by(Order.id.desc()).limit(30)))

    recent_ids = [order.id for order in recent_orders]
    lines_by_order = {order_id: [] for order_id in recent_ids}
    if recent_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(recent_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

    pending_suborders = []
    suborder_lines_by_id = {}
    suborder_parent_by_id = {}
    if is_server:
        pending_suborders = list(
            db.session.scalars(
                select(OrderSuborder)
                .where(
                    OrderSuborder.bar_id == bar_id,
                    OrderSuborder.assigned_staff_id == assignment.id,
                    OrderSuborder.status == "PENDING_VALIDATION",
                )
                .order_by(OrderSuborder.id.desc())
                .limit(20)
            )
        )
        pending_suborder_ids = [item.id for item in pending_suborders]
        pending_order_ids = list({item.order_id for item in pending_suborders})
        suborder_lines_by_id = {item.id: [] for item in pending_suborders}
        if pending_suborder_ids:
            for line in db.session.scalars(
                select(OrderSuborderLine)
                .where(
                    OrderSuborderLine.bar_id == bar_id,
                    OrderSuborderLine.order_suborder_id.in_(pending_suborder_ids),
                )
                .order_by(OrderSuborderLine.order_suborder_id, OrderSuborderLine.line_no)
            ):
                suborder_lines_by_id.setdefault(line.order_suborder_id, []).append(line)
        if pending_order_ids:
            suborder_parent_by_id = {
                order.id: order
                for order in db.session.scalars(
                    select(Order).where(
                        Order.bar_id == bar_id,
                        Order.id.in_(pending_order_ids),
                        Order.assigned_staff_id == assignment.id,
                    )
                )
            }

    stats = {
        "products": len(products),
        "available": sum(1 for product in products if balances.get(product.id, 0) > 0),
        "waiting": sum(1 for order in recent_orders if order.status == "DRAFT"),
        "to_pay": sum(1 for order in recent_orders if order.status == "CONFIRMED" and order.payment_status != "PAID"),
        "paid": sum(1 for order in recent_orders if order.payment_status == "PAID"),
        "pending_suborders": len(pending_suborders),
    }

    return render_template(
        "order_quick.html",
        bar=bar,
        products=products,
        categories=categories,
        categories_by_id=categories_by_id,
        balances=balances,
        tables=tables,
        recent_orders=recent_orders,
        lines_by_order=lines_by_order,
        pending_suborders=pending_suborders,
        suborder_lines_by_id=suborder_lines_by_id,
        suborder_parent_by_id=suborder_parent_by_id,
        status_labels=STATUS_LABELS,
        stats=stats,
        can_edit=can_edit,
        can_pay=can_pay,
        is_server=is_server,
    )


@bp.post("")
@api_required
def create(bar_id):
    try:
        x = request.get_json()
        o = order_service.create(
            request.api_user,
            bar_id,
            x["reference"],
            x["lines"],
            x.get("table_id"),
            x.get("customer_id"),
            x.get("notes"),
        )
        db.session.commit()
        return jsonify({"success": True, "data": {"id": str(o.id), "status": o.status}, "meta": {}}), 201
    except (ValueError, PermissionError, LookupError):
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Commande refusée", "details": None}}), 400


@bp.post("/<int:order_id>/confirm")
@api_required
def confirm(bar_id, order_id):
    try:
        o = order_service.confirm(request.api_user, bar_id, order_id)
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status}, "meta": {}})
    except PermissionError:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "FORBIDDEN", "message": "Livraison non autorisée", "details": None}}), 403
    except (ValueError, LookupError):
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Confirmation refusée", "details": None}}), 409


@bp.patch("/<int:order_id>")
@api_required
def adjust(bar_id, order_id):
    try:
        x = request.get_json() or {}
        o = order_service.adjust_confirmed(request.api_user, bar_id, order_id, x.get("lines", []), x.get("reason", ""))
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status, "total_amount": str(o.total_amount)}, "meta": {}})
    except LookupError:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "Commande introuvable", "details": None}}), 404
    except (ValueError, PermissionError):
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Modification refusée", "details": None}}), 409


@bp.post("/<int:order_id>/serve")
@api_required
def serve(bar_id, order_id):
    try:
        o = order_service.serve(request.api_user, bar_id, order_id)
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status}, "meta": {}})
    except (ValueError, PermissionError):
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Service refusé", "details": None}}), 409


@bp.post("/<int:order_id>/cancel")
@api_required
def cancel(bar_id, order_id):
    try:
        o = order_service.cancel(request.api_user, bar_id, order_id, (request.get_json() or {}).get("reason", ""))
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status}, "meta": {}})
    except (ValueError, PermissionError):
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Annulation refusée", "details": None}}), 409


@bp.post("/<int:order_id>/returns")
@api_required
def returns(bar_id, order_id):
    try:
        o = order_service.return_lines(
            request.api_user,
            bar_id,
            order_id,
            (request.get_json() or {}).get("lines", []),
            (request.get_json() or {}).get("reason", ""),
        )
        db.session.commit()
        return jsonify({"success": True, "data": {"id": str(o.id)}, "meta": {}})
    except (ValueError, PermissionError):
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Retour refusé", "details": None}}), 409
