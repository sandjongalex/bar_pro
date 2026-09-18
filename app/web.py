"""Minimal non-business HTTP blueprints."""

from flask import Blueprint, abort, jsonify, redirect, render_template, url_for
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
    from app.customer_models import UserNotification
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
    notifications = list(
        db.session.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == current_user.id, UserNotification.read_at.is_(None))
            .order_by(UserNotification.id.desc())
            .limit(20)
        )
    )
    return render_template(
        "dashboard.html",
        bars=bars,
        assignments=assignments,
        assignment_by_bar=assignment_by_bar,
        notifications=notifications,
    )


@web_bp.post("/notifications/<int:notification_id>/read")
@login_required
def notification_read(notification_id):
    from app.customer_models import UserNotification
    from app.models import utcnow

    item = db.session.get(UserNotification, notification_id)
    if not item or item.user_id != current_user.id:
        abort(404)
    item.read_at = utcnow()
    db.session.commit()
    return redirect(url_for("web.dashboard"))


@web_bp.get("/health")
@limiter.exempt
def health():
    """Liveness endpoint intentionally independent from unimplemented database models."""
    return jsonify({"status": "ok"}), 200


@api_bp.get("/health")
@limiter.exempt
def api_health():
    return jsonify({"success": True, "data": {"status": "ok"}, "meta": {}}), 200
