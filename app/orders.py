from datetime import datetime, timezone
import secrets

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import api_required
from app.extensions import db
from app.models import Bar, BarTable, Order, OrderLine, Product, ProductCategory, StockBalance
from app.order_services import order_service
from app.permissions import permissions

bp = Blueprint("orders", __name__, url_prefix="/api/v1/bars/<int:bar_id>/orders")
web_bp = Blueprint("orders_web", __name__, url_prefix="/bars/<int:bar_id>/orders")

STATUS_LABELS = {
    "DRAFT": "Brouillon",
    "CONFIRMED": "Confirmée",
    "SERVED": "Servie",
    "CANCELLED": "Annulée",
}


def _reference():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"CMD-{stamp}-{secrets.token_hex(2).upper()}"


def _message(code):
    messages = {
        "INVALID_LINES": "Ajoutez au moins un produit avec une quantité valide.",
        "ORDER_EMPTY": "La commande ne contient aucun produit.",
        "ORDER_NOT_DRAFT": "Cette commande n'est plus en brouillon.",
        "ORDER_NOT_CONFIRMED": "Cette commande n'est pas en attente de service.",
        "ORDER_NOT_CANCELLABLE": "Cette commande ne peut plus être annulée.",
        "REASON_REQUIRED": "Le motif d'annulation est obligatoire.",
        "INSUFFICIENT_STOCK": "Stock insuffisant pour confirmer cette commande.",
        "NOT_FOUND": "Produit, table ou commande introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à effectuer cette opération.",
        "BAR_SUSPENDED": "Le bar est suspendu : les ventes sont bloquées.",
    }
    return messages.get(str(code), "Opération refusée. Vérifiez la commande et le stock disponible.")


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


@web_bp.route("/new", methods=["GET", "POST"])
@login_required
def quick(bar_id):
    permissions.require(current_user, "orders.create", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

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
                order_service.confirm(current_user, bar_id, order.id)
                db.session.commit()
                flash(
                    f"Commande {order.reference} confirmée · {order.total_amount:,.0f} {order.currency}.",
                    "success",
                )
                if can_pay:
                    return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order.id))

            elif action == "serve":
                if not can_edit:
                    raise PermissionError("FORBIDDEN")
                order = order_service.serve(
                    current_user,
                    bar_id,
                    int(request.form.get("order_id", "0")),
                )
                db.session.commit()
                flash(f"Commande {order.reference} marquée comme servie.", "success")

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
                flash(f"Commande {order.reference} annulée. Le stock a été restauré.", "success")

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
            .order_by(Product.name, Product.id)
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
        for balance in db.session.scalars(
            select(StockBalance).where(StockBalance.bar_id == bar_id)
        )
    }

    tables = list(
        db.session.scalars(
            select(BarTable)
            .where(BarTable.bar_id == bar_id, BarTable.is_active.is_(True))
            .order_by(BarTable.label, BarTable.id)
        )
    )

    recent_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["CONFIRMED", "SERVED", "CANCELLED"]),
            )
            .order_by(Order.id.desc())
            .limit(20)
        )
    )
    recent_ids = [order.id for order in recent_orders]
    lines_by_order = {order_id: [] for order_id in recent_ids}
    if recent_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(recent_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

    stats = {
        "products": len(products),
        "available": sum(1 for product in products if balances.get(product.id, 0) > 0),
        "confirmed": sum(1 for order in recent_orders if order.status == "CONFIRMED"),
        "served": sum(1 for order in recent_orders if order.status == "SERVED"),
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
        status_labels=STATUS_LABELS,
        stats=stats,
        can_edit=can_edit,
        can_pay=can_pay,
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
    except (ValueError,) as e:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Commande refusée", "details": None}}), 400


@bp.post("/<int:order_id>/confirm")
@api_required
def confirm(bar_id, order_id):
    try:
        o = order_service.confirm(request.api_user, bar_id, order_id)
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status}, "meta": {}})
    except (ValueError,) as e:
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
    except (ValueError,) as e:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Modification refusée", "details": None}}), 409


@bp.post("/<int:order_id>/serve")
@api_required
def serve(bar_id, order_id):
    try:
        o = order_service.serve(request.api_user, bar_id, order_id)
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status}, "meta": {}})
    except (ValueError,) as e:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Service refusé", "details": None}}), 409


@bp.post("/<int:order_id>/cancel")
@api_required
def cancel(bar_id, order_id):
    try:
        o = order_service.cancel(request.api_user, bar_id, order_id, (request.get_json() or {}).get("reason", ""))
        db.session.commit()
        return jsonify({"success": True, "data": {"status": o.status}, "meta": {}})
    except (ValueError,) as e:
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
    except (ValueError,) as e:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": "Retour refusé", "details": None}}), 409
