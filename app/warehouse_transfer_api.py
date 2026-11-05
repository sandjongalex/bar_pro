"""Session-authenticated, CSRF-protected internal replenishment API.

Never use these endpoints as anonymous or public token APIs. In particular,
the global web tenant guard and the warehouse permissions remain in force.
"""
from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.permissions import permissions
from app.warehouse_models import WarehouseTransfer
from app.warehouse_service import WarehouseError
from app.warehouse_transfer_service import warehouse_transfer_service as service

bp = Blueprint(
    "warehouse_transfer_api", __name__,
    url_prefix="/bars/<int:bar_id>/warehouse/transfers",
)


def _payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, description="JSON_OBJECT_REQUIRED")
    return value


def _result(voucher, *, status=200):
    return jsonify({
        "id": voucher.id,
        "reference": voucher.reference,
        "status": voucher.status,
        "recipient_user_id": voucher.recipient_user_id,
        "requested_by_id": voucher.requested_by_id,
        "prepared_by_id": voucher.prepared_by_id,
        "dispatched_by_id": voucher.dispatched_by_id,
        "last_received_by_id": voucher.last_received_by_id,
        "closed_by_id": voucher.closed_by_id,
    }), status


def _execute(call):
    try:
        value = call()
        db.session.commit()
        return value
    except PermissionError:
        db.session.rollback()
        abort(403)
    except LookupError:
        db.session.rollback()
        abort(404)
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "TRANSFER_CONFLICT"}), 409
    except (ValueError, TypeError, WarehouseError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 422


@bp.get("")
@login_required
def list_transfers(bar_id):
    permissions.require(current_user, "warehouse.transfers.read", bar_id)
    rows = db.session.scalars(select(WarehouseTransfer).where(
        WarehouseTransfer.bar_id == bar_id,
    ).order_by(WarehouseTransfer.id.desc()).limit(100)).all()
    return jsonify([{
        "id": row.id, "reference": row.reference, "status": row.status,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
    } for row in rows])


@bp.get("/<int:transfer_id>")
@login_required
def detail(bar_id, transfer_id):
    try:
        voucher, lines, events = service.detail(current_user, bar_id, transfer_id)
    except LookupError:
        abort(404)
    return jsonify({
        "id": voucher.id, "reference": voucher.reference,
        "status": voucher.status,
        "requested_by_id": voucher.requested_by_id,
        "prepared_by_id": voucher.prepared_by_id,
        "dispatched_by_id": voucher.dispatched_by_id,
        "recipient_user_id": voucher.recipient_user_id,
        "last_received_by_id": voucher.last_received_by_id,
        "closed_by_id": voucher.closed_by_id,
        "lines": [{
            "id": line.id, "product_id": line.product_id,
            "requested": str(line.quantity_requested),
            "prepared": str(line.quantity_prepared),
            "dispatched": str(line.quantity_dispatched),
            "received": str(line.quantity_received),
            "disputed": str(line.quantity_disputed),
            "lost": str(line.quantity_lost),
            "requested_unit": line.requested_unit,
            "units_per_case_snapshot": str(line.units_per_case_snapshot),
        } for line in lines],
        "history": [{
            "kind": event.event_kind, "product_line_id": event.transfer_line_id,
            "quantity": str(event.quantity), "note": event.note,
            "actor_id": event.actor_id, "occurred_at": event.occurred_at.isoformat(),
        } for event in events],
    })


@bp.post("")
@login_required
def create(bar_id):
    data = _payload()
    def run():
        voucher = service.create_request(
            current_user, bar_id, data.get("reference"),
            data.get("quantities"), data.get("recipient_user_id"),
        )
        return _result(voucher, status=201)
    return _execute(run)


@bp.post("/<int:transfer_id>/prepare")
@login_required
def prepare(bar_id, transfer_id):
    data = _payload()
    return _execute(lambda: _result(service.prepare(
        current_user, bar_id, transfer_id, data.get("quantities"),
    )))


@bp.post("/<int:transfer_id>/ship")
@login_required
def ship(bar_id, transfer_id):
    _payload()
    return _execute(lambda: _result(service.ship(current_user, bar_id, transfer_id)))


@bp.post("/<int:transfer_id>/receive")
@login_required
def receive(bar_id, transfer_id):
    data = _payload()
    def run():
        receipt = service.receive(
            current_user, bar_id, transfer_id, data.get("quantities"),
            data.get("idempotency_key"),
        )
        return jsonify({
            "receipt_id": receipt.id, "transfer_id": receipt.transfer_id,
            "quantities": receipt.quantities,
        })
    return _execute(run)


@bp.post("/<int:transfer_id>/dispute")
@login_required
def dispute(bar_id, transfer_id):
    data = _payload()
    return _execute(lambda: _result(service.report_dispute(
        current_user, bar_id, transfer_id,
        data.get("quantities"), data.get("note"),
    )))


@bp.post("/<int:transfer_id>/resolve")
@login_required
def resolve(bar_id, transfer_id):
    data = _payload()
    return _execute(lambda: _result(service.resolve_dispute(
        current_user, bar_id, transfer_id,
        data.get("product_id"), data.get("quantity"),
        data.get("outcome"), data.get("note"),
    )))


@bp.post("/<int:transfer_id>/close")
@login_required
def close(bar_id, transfer_id):
    data = _payload()
    return _execute(lambda: _result(service.close(
        current_user, bar_id, transfer_id, data.get("note"),
    )))
