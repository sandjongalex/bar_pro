from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, select

from app.auth import api_required
from app.extensions import db
from app.models import Bar, Product, StockBalance, StockMovement, User
from app.permissions import permissions
from app.product_display_order import product_order_expression
from app.stock_service import StockError, stock_service

stock_bp = Blueprint("stock", __name__, url_prefix="/bars/<int:bar_id>/stock")
api_stock_bp = Blueprint("api_stock", __name__, url_prefix="/api/v1/bars/<int:bar_id>/stock")

MOVEMENT_LABELS = {
    "INITIAL": "Stock initial",
    "PURCHASE": "Achat fournisseur",
    "SALE": "Vente",
    "RETURN": "Retour client",
    "LOSS": "Perte / casse",
    "ADJUSTMENT": "Ajustement manuel",
    "INVENTORY_ADJUSTMENT": "Ajustement inventaire",
}


def _positive_quantity(value):
    try:
        quantity = Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("INVALID_QUANTITY") from None
    if quantity <= 0:
        raise ValueError("INVALID_QUANTITY")
    return quantity


def _stock_values(quantity, product):
    """Return current stock value at cost and potential value at sale price."""
    quantity = Decimal(quantity)
    return quantity * product.valuation_unit_cost, quantity * product.sale_price


def _message(code):
    messages = {
        "INVALID_QUANTITY": "La quantité doit être supérieure à zéro.",
        "REASON_REQUIRED": "Le motif du mouvement est obligatoire.",
        "INSUFFICIENT_STOCK": "Le stock disponible est insuffisant pour ce retrait.",
        "NOT_FOUND": "Produit introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à modifier le stock.",
        "BAR_SUSPENDED": "Le bar est suspendu : les mouvements de stock sont bloqués.",
        "ZERO_QUANTITY": "La quantité ne peut pas être nulle.",
        "INVALID_DIRECTION": "Le sens du mouvement est invalide.",
    }
    return messages.get(str(code), "Mouvement refusé. Vérifiez la quantité et le motif.")


@stock_bp.route("", methods=["GET", "POST"])
@login_required
def history(bar_id):
    permissions.require(current_user, "inventory.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_adjust = permissions.evaluate(current_user, "inventory.adjust", bar_id).allowed

    if request.method == "POST":
        if not can_adjust:
            raise PermissionError("FORBIDDEN")

        try:
            product_id = int(request.form.get("product_id", "0"))
            kind = request.form.get("movement_kind", "")
            quantity = _positive_quantity(request.form.get("quantity"))
            reason = request.form.get("reason", "").strip()

            if kind == "ADD":
                movement_type = "ADJUSTMENT"
                delta = quantity
            elif kind == "REMOVE":
                movement_type = "ADJUSTMENT"
                delta = -quantity
            elif kind == "LOSS":
                movement_type = "LOSS"
                delta = -quantity
            else:
                raise ValueError("INVALID_DIRECTION")

            item = stock_service.move(
                current_user,
                bar_id,
                product_id,
                movement_type,
                delta,
                reason,
            )
            db.session.commit()
            flash(
                f"Mouvement enregistré : {item.quantity_delta:+} {item.unit_snapshot}.",
                "success",
            )
        except (StockError, PermissionError, LookupError, ValueError, TypeError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")

        return redirect(url_for("stock.history", bar_id=bar_id))

    balance_rows = db.session.execute(
        select(Product, StockBalance)
        .outerjoin(
            StockBalance,
            and_(
                StockBalance.bar_id == Product.bar_id,
                StockBalance.product_id == Product.id,
            ),
        )
        .where(Product.bar_id == bar_id, Product.is_active.is_(True))
        .order_by(product_order_expression(Product.name), Product.name, Product.id)
    ).all()

    balances = []
    total_quantity = Decimal("0")
    total_value = Decimal("0")
    total_sale_value = Decimal("0")
    low_stock = 0
    out_of_stock = 0

    for product, balance in balance_rows:
        quantity = balance.quantity if balance is not None else Decimal("0")
        value, sale_value = _stock_values(quantity, product)
        total_quantity += quantity
        total_value += value
        total_sale_value += sale_value
        is_low = quantity <= product.stock_alert_threshold
        is_out = quantity <= 0
        if is_low:
            low_stock += 1
        if is_out:
            out_of_stock += 1
        balances.append(
            {
                "product": product,
                "quantity": quantity,
                "value": value,
                "sale_value": sale_value,
                "is_low": is_low,
                "is_out": is_out,
            }
        )

    query = (
        StockMovement.query
        .join(Product, Product.id == StockMovement.product_id)
        .filter(StockMovement.bar_id == bar_id, Product.bar_id == bar_id)
    )

    search = request.args.get("q", "").strip()
    movement_type = request.args.get("movement_type", "").strip()
    product_id = request.args.get("product_id", type=int)

    if search:
        query = query.filter(Product.name.ilike(f"%{search}%") | Product.sku.ilike(f"%{search}%"))
    if movement_type in MOVEMENT_LABELS:
        query = query.filter(StockMovement.movement_type == movement_type)
    if product_id:
        query = query.filter(StockMovement.product_id == product_id)

    rows = query.order_by(StockMovement.occurred_at.desc(), StockMovement.id.desc()).paginate(
        page=request.args.get("page", 1, type=int),
        per_page=25,
        error_out=False,
    )

    product_ids = {row.product_id for row in rows.items}
    products_by_id = {
        product.id: product
        for product in db.session.scalars(select(Product).where(Product.id.in_(product_ids)))
    } if product_ids else {}

    recorder_ids = {row.recorded_by_id for row in rows.items}
    recorders_by_id = {
        user.id: user
        for user in db.session.scalars(select(User).where(User.id.in_(recorder_ids)))
    } if recorder_ids else {}

    stats = {
        "products": len(balances),
        "low_stock": low_stock,
        "out_of_stock": out_of_stock,
        "total_quantity": total_quantity,
        "total_value": total_value,
        "total_sale_value": total_sale_value,
    }

    return render_template(
        "stock.html",
        bar=bar,
        balances=balances,
        rows=rows,
        stats=stats,
        movement_labels=MOVEMENT_LABELS,
        products_by_id=products_by_id,
        recorders_by_id=recorders_by_id,
        can_adjust=can_adjust,
    )


@api_stock_bp.post("/movements")
@api_required
def move(bar_id):
    try:
        body = request.get_json()
        permissions.require(request.api_user, "inventory.adjust", bar_id)
        if body["movement_type"] not in {"INITIAL", "ADJUSTMENT", "LOSS"}:
            raise StockError("MANUAL_TYPE_REQUIRED")
        item = stock_service.move(
            request.api_user,
            bar_id,
            body["product_id"],
            body["movement_type"],
            body["quantity_delta"],
            body.get("reason", ""),
        )
        db.session.commit()
        return jsonify(
            {
                "success": True,
                "data": {"id": str(item.id), "quantity_delta": str(item.quantity_delta)},
                "meta": {},
            }
        ), 201
    except (StockError, PermissionError, LookupError):
        db.session.rollback()
        return jsonify(
            {
                "success": False,
                "error": {
                    "code": "BUSINESS_RULE_VIOLATION",
                    "message": "Mouvement refusé",
                    "details": None,
                },
            }
        ), 400


@api_stock_bp.get("/alerts")
@api_required
def alerts(bar_id):
    permissions.require(request.api_user, "inventory.read", bar_id)
    rows = (
        StockBalance.query
        .join(Product)
        .filter(
            StockBalance.bar_id == bar_id,
            StockBalance.quantity <= Product.stock_alert_threshold,
        )
        .all()
    )
    return jsonify(
        {
            "success": True,
            "data": [
                {"product_id": str(item.product_id), "quantity": str(item.quantity)}
                for item in rows
            ],
            "meta": {},
        }
    )
