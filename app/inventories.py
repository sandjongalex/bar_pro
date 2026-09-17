from flask import Blueprint, jsonify, request, render_template, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from app.extensions import db
from app.inventory_services import inventory_service
from app.models import Inventory, InventoryLine, Product
from app.auth import api_required
from app.permissions import permissions

inventories_bp = Blueprint("inventories", __name__, url_prefix="/bars/<int:bar_id>/inventories")
api_inventories_bp = Blueprint("api_inventories", __name__, url_prefix="/api/v1/bars/<int:bar_id>/inventories")


def payload(inv):
    lines = db.session.execute(select(InventoryLine, Product).join(Product, Product.id == InventoryLine.product_id).where(InventoryLine.inventory_id == inv.id).order_by(InventoryLine.product_id)).all()
    return {"id": str(inv.id), "reference": inv.reference, "reason": inv.reason, "status": inv.status,
            "counted_at": inv.counted_at.isoformat(), "posted_at": inv.posted_at.isoformat() if inv.posted_at else None,
            "cancelled_at": inv.cancelled_at.isoformat() if inv.cancelled_at else None,
            "lines": [{"product_id": str(line.product_id), "product_name": product.name, "unit": product.base_unit,
                       "expected_quantity": str(line.expected_quantity_snapshot),
                       "counted_quantity": str(line.counted_quantity) if line.counted_quantity is not None else None,
                       "difference_quantity": str(line.difference_quantity) if line.difference_quantity is not None else None} for line, product in lines]}


def body():
    value = request.get_json()
    if not isinstance(value, dict):
        raise ValueError("INVALID_REQUEST")
    return value


def result(inv, status=200):
    db.session.commit()
    return jsonify(success=True, data=payload(inv), meta={}), status


@inventories_bp.get("")
@login_required
def listing(bar_id):
    permissions.require(current_user, "inventory.read", bar_id)
    rows = Inventory.query.filter_by(bar_id=bar_id).order_by(Inventory.created_at.desc(), Inventory.id.desc()).paginate(page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    return render_template("inventories.html", rows=rows, bar_id=bar_id)


@inventories_bp.route("/new", methods=["GET", "POST"])
@login_required
def new(bar_id):
    permissions.require(current_user, "inventory.adjust", bar_id)
    if request.method == "POST":
        inv = inventory_service.create(current_user, bar_id, request.form.get("reference"), request.form.getlist("product_ids"), request.form.get("reason"))
        db.session.commit()
        return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))
    products = Product.query.filter_by(bar_id=bar_id).order_by(Product.name).all()
    return render_template("inventory_new.html", products=products, bar_id=bar_id)


@inventories_bp.route("/<int:inventory_id>", methods=["GET", "POST"])
@login_required
def detail(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "counts":
            quantities = {key[9:]: value for key, value in request.form.items() if key.startswith("quantity_") and value.strip()}
            inventory_service.count(current_user, bar_id, inv.id, quantities)
        elif action == "post":
            inventory_service.post(current_user, bar_id, inv.id)
        elif action == "cancel":
            inventory_service.cancel(current_user, bar_id, inv.id)
        else:
            raise ValueError("INVALID_ACTION")
        db.session.commit()
        return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))
    return render_template("inventory_detail.html", inventory=payload(inv), bar_id=bar_id)


@inventories_bp.get("/<int:inventory_id>/print")
@login_required
def printable(bar_id, inventory_id):
    return render_template("inventory_print.html", inventory=payload(inventory_service.get(current_user, bar_id, inventory_id)))


@api_inventories_bp.get("")
@api_required
def api_list(bar_id):
    permissions.require(request.api_user, "inventory.read", bar_id)
    rows = Inventory.query.filter_by(bar_id=bar_id).order_by(Inventory.created_at.desc(), Inventory.id.desc()).paginate(page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    return jsonify(success=True, data=[payload(inv) for inv in rows.items], meta={"page": rows.page, "pages": rows.pages, "total": rows.total})


@api_inventories_bp.get("/<int:inventory_id>")
@api_required
def api_detail(bar_id, inventory_id):
    return jsonify(success=True, data=payload(inventory_service.get(request.api_user, bar_id, inventory_id)), meta={})


@api_inventories_bp.post("")
@api_required
def create(bar_id):
    value = body()
    return result(inventory_service.create(request.api_user, bar_id, value.get("reference"), value.get("product_ids"), value.get("reason", "Inventaire")), 201)


@api_inventories_bp.patch("/<int:inventory_id>/counts")
@api_required
def counts(bar_id, inventory_id):
    return result(inventory_service.count(request.api_user, bar_id, inventory_id, body().get("quantities")))


@api_inventories_bp.post("/<int:inventory_id>/post")
@api_required
def post(bar_id, inventory_id):
    try:
        return result(inventory_service.post(request.api_user, bar_id, inventory_id))
    except ValueError:
        db.session.rollback()
        return jsonify(success=False, error={"code": "STATE_CONFLICT", "message": "Inventaire incomplet, obsolète ou déjà terminé", "details": None}), 409


@api_inventories_bp.post("/<int:inventory_id>/cancel")
@api_required
def cancel(bar_id, inventory_id):
    return result(inventory_service.cancel(request.api_user, bar_id, inventory_id))
