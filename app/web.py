"""Minimal non-business HTTP blueprints."""

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
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
    from app.dashboard_activity import dashboard_activity
    from app.dashboard_alerts import dashboard_alerts
    from app.dashboard_charts import dashboard_charts
    from app.dashboard_inventory_control import dashboard_inventory_control
    from app.dashboard_service import dashboard_summary
    from app.employee_context import get_current_employee_context
    from app.models import Bar
    from app.permissions import permissions
    from app.shift_service import get_active_shift_for_assignment

    employee_context = None
    requested_bar_id = request.args.get("bar_id", type=int)
    if current_user.category == "EMPLOYEE":
        try:
            employee_context = get_current_employee_context(current_user)
        except LookupError:
            abort(404)

        if requested_bar_id is not None and requested_bar_id != employee_context.bar_id:
            abort(404)

        assigned_bar = db.session.get(Bar, employee_context.bar_id)
        if not assigned_bar:
            abort(404)

        if employee_context.role in {"CASHIER", "SERVER"}:
            shift = get_active_shift_for_assignment(
                employee_context.bar_id,
                employee_context.assignment.id,
            )
            if not shift:
                return render_template(
                    "off_duty.html",
                    bar=assigned_bar,
                    role=employee_context.role,
                )
            if employee_context.role == "CASHIER":
                from app.cashier_performance import build_cashier_performance

                performance = build_cashier_performance(
                    assigned_bar.id,
                    current_user.id,
                    employee_context.assignment.id,
                    assigned_bar.timezone,
                )
                return render_template(
                    "cashier_dashboard.html",
                    bar=assigned_bar,
                    performance=performance,
                )
            return redirect(url_for("orders_web.quick", bar_id=employee_context.bar_id))

        if employee_context.role != "BAR_ADMIN":
            abort(404)

        bars = [assigned_bar]
        assignments = [employee_context.assignment]
    else:
        bars = [
            bar
            for bar in Bar.query.order_by(Bar.name).all()
            if permissions.evaluate(current_user, "bars.read", bar.id).allowed
        ]
        assignments = []

    assignment_by_bar = {assignment.bar_id: assignment for assignment in assignments}

    reportable_bars = [
        bar for bar in bars if permissions.evaluate(current_user, "reports.read", bar.id).allowed
    ]
    active_bar = None
    if requested_bar_id is not None:
        active_bar = next((bar for bar in reportable_bars if bar.id == requested_bar_id), None)
        if active_bar is None:
            abort(404)
    elif reportable_bars:
        active_bar = next((bar for bar in reportable_bars if bar.status == "ACTIVE"), reportable_bars[0])

    dashboard_data = dashboard_summary(current_user, active_bar.id) if active_bar else None
    operational_alerts = dashboard_alerts(current_user, active_bar.id) if active_bar else None
    chart_data = dashboard_charts(current_user, active_bar.id) if active_bar else None
    inventory_control = dashboard_inventory_control(current_user, active_bar.id) if active_bar else None
    activity_data = dashboard_activity(current_user, active_bar.id) if active_bar else None

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
        reportable_bars=reportable_bars,
        active_bar=active_bar,
        dashboard_data=dashboard_data,
        operational_alerts=operational_alerts,
        chart_data=chart_data,
        inventory_control=inventory_control,
        activity_data=activity_data,
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