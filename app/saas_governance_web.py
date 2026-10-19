"""Dedicated support view and ES256 platform governance API."""
from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required
from app.auth import api_required
from app.extensions import db
from app.permissions import permissions
from app.saas_governance import bar_governance, change_bar_governance, change_owner_governance

bp = Blueprint("saas_governance_web", __name__)
api_bp = Blueprint("saas_governance_api", __name__, url_prefix="/api/v1")

@bp.get("/bars/<int:bar_id>/support")
@login_required
def bar_support(bar_id):
    if not permissions.evaluate(current_user, "bars.support.read", bar_id).allowed:
        abort(404)
    state = bar_governance(bar_id)
    return render_template("saas_bar_support.html", state=state.status if state else "APPROVED",
                           level=state.suspension_level if state else 0)

@api_bp.get("/bars/<int:bar_id>/governance")
@api_required
def read_bar_governance(bar_id):
    permissions.require(request.api_user, "bars.support.read", bar_id)
    state = bar_governance(bar_id)
    return jsonify({"success": True, "data": {
        "bar_id": bar_id, "status": state.status if state else "APPROVED",
        "suspension_level": state.suspension_level if state else 0}, "meta": {}})

@api_bp.patch("/bars/<int:bar_id>/governance")
@api_required
def update_bar_governance(bar_id):
    permissions.require(request.api_user, "bars.governance.manage", bar_id)
    data = request.get_json(silent=True) or {}
    state = change_bar_governance(request.api_user, bar_id, status=data.get("status"),
                                   suspension_level=data.get("suspension_level", 0), reason=data.get("reason"))
    db.session.commit()
    return jsonify({"success": True, "data": {
        "bar_id": state.bar_id, "status": state.status,
        "suspension_level": state.suspension_level}, "meta": {}})

@api_bp.patch("/platform/owners/<int:owner_id>/governance")
@api_required
def update_owner_governance(owner_id):
    if request.api_user.category != "SUPER_ADMIN":
        abort(403)
    data = request.get_json(silent=True) or {}
    state = change_owner_governance(request.api_user, owner_id,
                                     status=data.get("status"), reason=data.get("reason"))
    db.session.commit()
    return jsonify({"success": True, "data": {
        "owner_id": state.owner_id, "status": state.status}, "meta": {}})
