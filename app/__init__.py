"""Application factory for Bar Manager Pro."""

from __future__ import annotations

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
from app.customers_web import bp as customers_web_bp
from app.inventories import api_inventories_bp, inventories_bp
from app.orders import bp as orders_bp, web_bp as orders_web_bp
from app.staff import bp as staff_web_bp


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
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_cli(app)
    return app


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

    app.register_blueprint(finance_web_bp)
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
    app.register_blueprint(orders_bp)
    app.register_blueprint(orders_web_bp)

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
        from flask import request, abort
        from flask_login import current_user
        from app.permissions import permissions

        if request.path.startswith("/api/") and request.is_json:
            if not isinstance(request.get_json(), dict):
                abort(400)
        bar_id = (request.view_args or {}).get("bar_id")
        if not request.path.startswith("/api/") and bar_id is not None and current_user.is_authenticated:
            if not permissions.evaluate(current_user, "bars.read", bar_id).allowed:
                abort(404)


def _register_error_handlers(app: Flask) -> None:
    from app.errors import register_error_handlers

    register_error_handlers(app)


def _register_cli(app: Flask) -> None:
    from app.cli import register_cli

    register_cli(app)
