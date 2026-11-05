"""Regression for the production revision lost by the 2026-10-22 revert.

The real bar database reports a9b0c1d2e3f4 after the snapshot-only
cashier-attribution migration, while the merged warehouse V2 migrations
originally forked from its predecessor. Restore the *same* revision, preserve
nullable fields and continue forward through the latest inventory finance migration without stamping.
"""
from flask_migrate import upgrade
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db


def test_existing_a9b0_database_upgrades_through_current_head(tmp_path):
    app = create_app("testing", {
        "SECRET_KEY": "unit-test-only-a9b0",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'applied_a9b0.db'}",
    })
    with app.app_context():
        # Simulate the actual production state, where a9 was already applied.
        upgrade(revision="a9b0c1d2e3f4", directory=app.config["MIGRATIONS_DIRECTORY"])
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "a9b0c1d2e3f4"
        schema = inspect(db.engine)
        columns = {col["name"]: col for col in schema.get_columns("inventory_cashier_assignments")}
        assert columns["cashier_shift_id"]["nullable"]
        assert columns["cashier_user_id"]["nullable"]
        assert "warehouse_locations" not in schema.get_table_names()

        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "f9c0d1e2f3a4"
        schema = inspect(db.engine)
        for name in (
            "warehouse_locations", "warehouse_stock_balances",
            "warehouse_staff_grants", "warehouse_purchase_receipt_lines",
        ):
            assert name in schema.get_table_names()
        assert "inventory_expense_links" in schema.get_table_names()
        assert "inventory_debt_payment_links" in schema.get_table_names()
        assert "debt_repayments_amount" in {
            column["name"] for column in schema.get_columns("inventory_period_snapshots")
        }
        columns = {col["name"]: col for col in schema.get_columns("inventory_cashier_assignments")}
        assert columns["cashier_shift_id"]["nullable"]
        assert columns["cashier_user_id"]["nullable"]

        # A second upgrade is a no-op and must leave the schema intact.
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "f8b9c0d1e2f3"
        db.session.remove()
        db.engine.dispose()
