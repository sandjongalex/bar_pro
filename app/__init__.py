"""Application factory for Bar Manager Pro."""

from __future__ import annotations

from decimal import Decimal
import logging
from typing import Any

from flask import Flask

from app.config import get_config, validate_config, load_environment
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.web import api_bp, web_bp
from app.auth import api_auth_bp, auth_bp
from app.bars import api_bars_bp, bars_bp
from app.catalog import api_catalog_bp, catalog_bp
from app.stock import api_stock_bp, stock_bp
from app.purchases import bp as purchases_bp, suppliers_bp, supplier_payments_bp
from app.purchases_web import bp as purchases_web_bp
from app.supplier_debts_web import bp as supplier_debts_web_bp
from app.checkout_web import bp as checkout_web_bp
from app.cashier_returns_web import bp as cashier_returns_web_bp
from app.cashier_exchanges_web import bp as cashier_exchanges_web_bp
from app.cashier_handovers_web import bp as cashier_handovers_web_bp
from app.cashier_history_web import bp as cashier_history_web_bp
from app.cashier_workspace_web import bp as cashier_workspace_web_bp
from app.cashier_invoices_web import bp as cashier_invoices_web_bp
from app.customers_web import bp as customers_web_bp
from app.expenses_web import bp as expenses_web_bp
from app.inventories import api_inventories_bp, inventories_bp
from app.live_orders_web import bp as live_orders_web_bp
from app.order_edit_web import bp as order_edit_web_bp
from app.unpaid_orders_web import bp as unpaid_orders_web_bp
from app.orders import bp as orders_bp, web_bp as orders_web_bp
from app.suborder_web import bp as suborders_web_bp
from app.staff import bp as staff_web_bp
from app.shifts_web import bp as shifts_web_bp
from app.reports_web import bp as reports_web_bp


def create_app(config_name: str | None = None, test_config: dict[str, Any] | None = None) -> Flask:
    """Create and configure an application without performing database I/O."""
    app = Flask(__name__, instance_relative_config=True)
    config_class = get_config(config_name)
    app.config.from_object(config_class)
    load_environment(app.config)

    if test_config:
        app.config.update(test_config)

    validate_config(app.config)
    _configure_logging(app)
    _init_extensions(app)
    from app import models  # noqa: F401 - registers core metadata for Flask-Migrate
    from app import purchase_models  # noqa: F401 - purchase/supplier domain extensions
    from app import customer_models  # noqa: F401 - customer receivables/cases/notifications metadata
    from app import inventory_period_models  # noqa: F401 - inventory period reconciliation metadata
    from app import order_suborder_models  # noqa: F401 - cashier sub-orders and validation metadata
    from app import shift_models  # noqa: F401 - employee attendance/on-duty metadata
    _register_blueprints(app)
    _register_template_context(app)
    app.jinja_env.finalize = _template_finalize
    _register_error_handlers(app)
    _register_cli(app)
    return app


def _template_finalize(value):
    """Render Decimal values without database scale padding in HTML.

    Quantities are stored with fixed precision, so a value such as Decimal('2.000000')
    should be shown to users as ``2`` while a real fractional quantity keeps its
    significant decimals (for example ``2.5``).
    """
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        return format(value.normalize(), "f")
    return value


def _configure_logging(app: Flask) -> None:
    """Configure process logging without including request data or configuration."""
    level = getattr(logging, app.config["LOG_LEVEL"], logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(level)
    app.logger.propagate = False


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db, directory=app.config["MIGRATIONS_DIRECTORY"])
    login_manager.init_app(app)
    csrf.init_app(app)
    csrf.exempt(api_bp)
    csrf.exempt(api_auth_bp)
    limiter.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from app.finance import bp as finance_bp
    from app.reports import bp as reports_bp
    from app.subscriptions import bp as subscriptions_bp
    from app.finance_web import bp as finance_web_bp
    from app.cashier_web import bp as cashier_web_bp

    app.register_blueprint(finance_web_bp)
    app.register_blueprint(cashier_web_bp)
    app.register_blueprint(cashier_workspace_web_bp)
    app.register_blueprint(cashier_invoices_web_bp)
    app.register_blueprint(cashier_returns_web_bp)
    app.register_blueprint(cashier_exchanges_web_bp)
    app.register_blueprint(cashier_handovers_web_bp)
    app.register_blueprint(cashier_history_web_bp)
    app.register_blueprint(reports_web_bp)
    app.register_blueprint(expenses_web_bp)
    app.register_blueprint(finance_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(subscriptions_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_auth_bp)
    app.register_blueprint(bars_bp)
    app.register_blueprint(api_bars_bp)
    app.register_blueprint(staff_web_bp)
    app.register_blueprint(shifts_web_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(api_catalog_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(api_stock_bp)
    app.register_blueprint(purchases_web_bp)
    app.register_blueprint(supplier_debts_web_bp)
    app.register_blueprint(checkout_web_bp)
    app.register_blueprint(customers_web_bp)
    app.register_blueprint(suppliers_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(supplier_payments_bp)
    app.register_blueprint(inventories_bp)
    app.register_blueprint(api_inventories_bp)
    app.register_blueprint(live_orders_web_bp)
    app.register_blueprint(order_edit_web_bp)
    app.register_blueprint(unpaid_orders_web_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(orders_web_bp)
    app.register_blueprint(suborders_web_bp)

    for blueprint in (
        api_bars_bp,
        api_catalog_bp,
        api_stock_bp,
        suppliers_bp,
        purchases_bp,
        supplier_payments_bp,
        api_inventories_bp,
        orders_bp,
        finance_bp,
        reports_bp,
        subscriptions_bp,
    ):
        csrf.exempt(blueprint)

    @app.before_request
    def tenant_web_guard():
        from flask import abort, redirect, request, url_for
        from flask_login import current_user
        from app.employee_context import get_current_employee_context
        from app.permissions import permissions
        from app.shift_service import get_active_shift_for_assignment

        if request.path.startswith("/api/") and request.is_json:
            if not isinstance(request.get_json(), dict):
                abort(400)

        bar_id = (request.view_args or {}).get("bar_id")
        if not request.path.startswith("/api/") and bar_id is not None and current_user.is_authenticated:
            if not permissions.evaluate(current_user, "bars.read", bar_id).allowed:
                abort(404)

            # A cashier/server may keep a valid account while off duty, but no
            # operational bar page is available until a supervisor opens a shift.
            if current_user.category == "EMPLOYEE" and request.endpoint != "shifts_web.manage":
                try:
                    context = get_current_employee_context(current_user)
                except LookupError:
                    abort(404)
                if context.role in {"CASHIER", "SERVER"}:
                    active_shift = get_active_shift_for_assignment(bar_id, context.assignment.id)
                    if not active_shift:
                        if request.method in {"GET", "HEAD"}:
                            return redirect(url_for("web.dashboard"))
                        abort(403)
        return None


def _register_template_context(app: Flask) -> None:
    """Expose lightweight, request-scoped navigation helpers to server templates."""

    @app.context_processor
    def navigation_helpers():
        from flask_login import current_user
        from sqlalchemy import select
        from app.models import Bar, StaffAssignment
        from app.permissions import permissions

        permission_cache: dict[tuple[str, int], bool] = {}
        role_cache: dict[int, str | None] = {}
        performance_cache: dict[int, dict | None] = {}

        def _bar_id(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def nav_can(action: str, bar_id) -> bool:
            if not current_user.is_authenticated:
                return False
            normalized_bar_id = _bar_id(bar_id)
            if normalized_bar_id is None:
                return False
            key = (action, normalized_bar_id)
            if key not in permission_cache:
                permission_cache[key] = permissions.evaluate(current_user, action, normalized_bar_id).allowed
            return permission_cache[key]

        def nav_role(bar_id):
            """Return the effective role for role-specific navigation only."""
            if not current_user.is_authenticated:
                return None
            normalized_bar_id = _bar_id(bar_id)
            if normalized_bar_id is None:
                return None
            if normalized_bar_id in role_cache:
                return role_cache[normalized_bar_id]

            if current_user.category == "SUPER_ADMIN":
                role = "SUPER_ADMIN"
            elif current_user.category == "OWNER":
                role = "OWNER"
            elif current_user.category == "EMPLOYEE":
                assignment = db.session.scalar(
                    select(StaffAssignment).where(
                        StaffAssignment.bar_id == normalized_bar_id,
                        StaffAssignment.user_id == current_user.id,
                        StaffAssignment.ended_at.is_(None),
                    )
                )
                role = assignment.role if assignment else None
            else:
                role = None

            role_cache[normalized_bar_id] = role
            return role

        def cashier_performance(bar_id):
            """Current-shift cashier/team KPIs, available only to that cashier."""
            if not current_user.is_authenticated or current_user.category != "EMPLOYEE":
                return None
            normalized_bar_id = _bar_id(bar_id)
            if normalized_bar_id is None:
                return None
            if normalized_bar_id in performance_cache:
                return performance_cache[normalized_bar_id]

            assignment = db.session.scalar(
                select(StaffAssignment).where(
                    StaffAssignment.bar_id == normalized_bar_id,
                    StaffAssignment.user_id == current_user.id,
                    StaffAssignment.role == "CASHIER",
                    StaffAssignment.ended_at.is_(None),
                )
            )
            bar = db.session.get(Bar, normalized_bar_id)
            if not assignment or not bar:
                performance_cache[normalized_bar_id] = None
                return None

            from app.cashier_performance import build_cashier_performance

            performance_cache[normalized_bar_id] = build_cashier_performance(
                normalized_bar_id,
                current_user.id,
                assignment.id,
                bar.timezone,
            )
            return performance_cache[normalized_bar_id]

        return {
            "nav_can": nav_can,
            "nav_role": nav_role,
            "cashier_performance": cashier_performance,
        }


def _register_error_handlers(app: Flask) -> None:
    from app.errors import register_error_handlers

    register_error_handlers(app)


def _register_cli(app: Flask) -> None:
    from app.cli import register_cli

    register_cli(app)
