from flask_migrate import upgrade
from sqlalchemy import inspect

from app import create_app
from app.extensions import db


def test_order_suborder_tables_are_migrated(tmp_path):
    app = create_app(
        "testing",
        {
            "SECRET_KEY": "test-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'suborders.sqlite3'}",
        },
    )
    with app.app_context():
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        inspector = inspect(db.engine)

        assert "order_suborders" in inspector.get_table_names()
        assert "order_suborder_lines" in inspector.get_table_names()

        suborder_columns = {item["name"] for item in inspector.get_columns("order_suborders")}
        assert {
            "order_id",
            "sequence_no",
            "assigned_staff_id",
            "status",
            "delivery_status",
            "created_by_id",
            "validated_by_id",
            "validated_at",
            "delivered_by_id",
            "delivered_at",
        }.issubset(suborder_columns)

        line_columns = {item["name"] for item in inspector.get_columns("order_suborder_lines")}
        assert {
            "order_suborder_id",
            "order_id",
            "product_id",
            "line_no",
            "quantity",
            "unit_sale_price_snapshot",
            "total_amount",
        }.issubset(line_columns)

        checks = {item["name"] for item in inspector.get_check_constraints("order_suborders")}
        assert {
            "ck_order_suborders_sequence",
            "ck_order_suborders_status",
            "ck_order_suborders_delivery_status",
            "ck_order_suborders_amounts",
        }.issubset(checks)
