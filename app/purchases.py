"""Supplier and purchase API endpoints."""
from flask import Blueprint, jsonify, request

from app.auth import api_required
from app.extensions import db
from app.permissions import permissions
from app.purchase_services import purchase_service, supplier_service

bp = Blueprint("purchases", __name__, url_prefix="/api/v1/bars/<int:bar_id>/purchases")
suppliers_bp = Blueprint("suppliers", __name__, url_prefix="/api/v1/bars/<int:bar_id>/suppliers")
supplier_payments_bp = Blueprint(
    "supplier_payments", __name__, url_prefix="/api/v1/bars/<int:bar_id>/supplier-payments"
)


def serialize(item):
    return {
        column.name: (str(getattr(item, column.name)) if getattr(item, column.name) is not None else None)
        for column in item.__table__.columns
    }


def ok(data, status=200):
    db.session.commit()
    return jsonify({"success": True, "data": data, "meta": {}}), status


def list_result(items, page, page_size):
    return jsonify(
        {
            "success": True,
            "data": [serialize(item) for item in items[:page_size]],
            "meta": {"page": page, "page_size": page_size, "has_more": len(items) > page_size},
        }
    )


@suppliers_bp.get("")
@api_required
def list_suppliers(bar_id):
    active = request.args.get("active")
    if active is not None:
        active = active.lower() == "true"
    items, page, page_size = supplier_service.list(
        request.api_user,
        bar_id,
        request.args.get("q"),
        active,
        request.args.get("page", 1),
        request.args.get("page_size", 50),
    )
    return list_result(items, page, page_size)


@suppliers_bp.post("")
@api_required
def create_supplier(bar_id):
    item = supplier_service.create(request.api_user, bar_id, request.get_json() or {})
    return ok(serialize(item), 201)


@suppliers_bp.get("/<int:supplier_id>")
@api_required
def get_supplier(bar_id, supplier_id):
    return jsonify(
        {"success": True, "data": serialize(supplier_service.get(request.api_user, bar_id, supplier_id)), "meta": {}}
    )


@suppliers_bp.patch("/<int:supplier_id>")
@api_required
def update_supplier(bar_id, supplier_id):
    return ok(serialize(supplier_service.update(request.api_user, bar_id, supplier_id, request.get_json() or {})))


@bp.get("")
@api_required
def list_purchases(bar_id):
    items, page, page_size = purchase_service.list(
        request.api_user,
        bar_id,
        request.args.get("status"),
        request.args.get("supplier_id"),
        request.args.get("page", 1),
        request.args.get("page_size", 50),
    )
    return list_result(items, page, page_size)


@bp.post("")
@api_required
def create(bar_id):
    data = request.get_json() or {}
    item = purchase_service.create(
        request.api_user,
        bar_id,
        data["supplier_id"],
        data["reference"],
        data["lines"],
        data.get("supplier_invoice_reference"),
    )
    return ok({"id": str(item.id), "status": item.status, "total_amount": str(item.total_amount)}, 201)


@bp.get("/<int:purchase_id>")
@api_required
def get_purchase(bar_id, purchase_id):
    return jsonify(
        {"success": True, "data": serialize(purchase_service.get(request.api_user, bar_id, purchase_id)), "meta": {}}
    )


@bp.patch("/<int:purchase_id>")
@api_required
def update_purchase(bar_id, purchase_id):
    return ok(serialize(purchase_service.update(request.api_user, bar_id, purchase_id, request.get_json() or {})))


@bp.post("/<int:purchase_id>/receive")
@api_required
def receive(bar_id, purchase_id):
    item = purchase_service.receive(request.api_user, bar_id, purchase_id)
    return ok({"id": str(item.id), "status": item.status})


@bp.post("/<int:purchase_id>/cancel")
@api_required
def cancel(bar_id, purchase_id):
    item = purchase_service.cancel(request.api_user, bar_id, purchase_id, (request.get_json() or {})["reason"])
    return ok({"id": str(item.id), "status": item.status})


@bp.get("/<int:purchase_id>/balance")
@api_required
def balance(bar_id, purchase_id):
    permissions.require(request.api_user, "purchases.read", bar_id)
    return jsonify({"success": True, "data": {"due_amount": str(purchase_service.due(bar_id, purchase_id))}, "meta": {}})


@bp.post("/<int:purchase_id>/payments")
@api_required
def pay_purchase(bar_id, purchase_id):
    data = request.get_json() or {}
    item = purchase_service.pay(
        request.api_user,
        bar_id,
        purchase_id,
        data["reference"],
        data["amount"],
        data["method"],
        data["reason"],
        data.get("cash_session_id"),
        data.get("provider_code"),
        data.get("provider_transaction_id"),
    )
    return ok(serialize(item), 201)


@supplier_payments_bp.get("")
@api_required
def list_supplier_payments(bar_id):
    permissions.require(request.api_user, "purchases.read", bar_id)
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(1, int(request.args.get("page_size", 50))))
    from app.models import SupplierPayment
    from sqlalchemy import select

    query = select(SupplierPayment).where(SupplierPayment.bar_id == bar_id)
    if request.args.get("purchase_id"):
        query = query.where(SupplierPayment.purchase_id == int(request.args["purchase_id"]))
    items = db.session.scalars(query.order_by(SupplierPayment.id.desc()).offset((page - 1) * page_size).limit(page_size + 1)).all()
    return list_result(items, page, page_size)


@supplier_payments_bp.post("/<int:supplier_payment_id>/reverse")
@api_required
def reverse_supplier_payment(bar_id, supplier_payment_id):
    data = request.get_json() or {}
    item = purchase_service.reverse_payment(
        request.api_user,
        bar_id,
        supplier_payment_id,
        data["reference"],
        data["reason"],
        data.get("cash_session_id"),
    )
    return ok(serialize(item), 201)
