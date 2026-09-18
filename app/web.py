"""Minimal non-business HTTP blueprints."""

from flask import Blueprint, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from flask_limiter.util import get_remote_address

from app.extensions import limiter
from app.extensions import db


web_bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


@web_bp.get("/")
def index():
    return redirect(url_for("web.dashboard")) if current_user.is_authenticated else redirect(url_for("auth.web_login"))


@web_bp.get("/dashboard")
@login_required
def dashboard():
    from app.models import Bar, StaffAssignment
    from app.permissions import permissions

    bars = [
        bar
        for bar in Bar.query.order_by(Bar.name).all()
        if permissions.evaluate(current_user, "bars.read", bar.id).allowed
    ]
    assignments = [] if current_user.category != "EMPLOYEE" else list(
        db.session.scalars(
            select(StaffAssignment).where(
                StaffAssignment.user_id == current_user.id,
                StaffAssignment.ended_at.is_(None),
            )
        )
    )
    assignment_by_bar = {assignment.bar_id: assignment for assignment in assignments}
    return render_template(
        "dashboard.html",
        bars=bars,
        assignments=assignments,
        assignment_by_bar=assignment_by_bar,
    )


@web_bp.get("/health")
@limiter.exempt
def health():
    """Liveness endpoint intentionally independent from unimplemented database models."""
    return jsonify({"status": "ok"}), 200


@api_bp.get("/health")
@limiter.exempt
def api_health():
    return jsonify({"success": True, "data": {"status": "ok"}, "meta": {}}), 200
