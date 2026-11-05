"""Persist the per-bar product display order used by catalogue and operations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from app.product_display_order import PRODUCT_DISPLAY_ORDER, _PRODUCT_RANK


revision = "a0b1c2d3e4f5"
down_revision = "f9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    display_order_type = mysql.INTEGER(unsigned=True).with_variant(sa.Integer(), "sqlite")
    op.add_column(
        "products",
        sa.Column(
            "display_order",
            display_order_type,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # A SQL CASE keeps the migration usable in both online and --sql/offline mode.
    # The rank matches the former hard-coded Kape product sequence; unknown names
    # remain after known products and are then ordered by name/id by callers.
    cases = []
    for name, rank in _PRODUCT_RANK.items():
        escaped_name = name.replace("'", "''")
        cases.append(f"WHEN '{escaped_name}' THEN {rank * 1000}")
    fallback = len(PRODUCT_DISPLAY_ORDER) * 1000
    expression = "CASE LOWER(TRIM(name)) " + " ".join(cases) + f" ELSE {fallback} END"
    op.execute(sa.text(f"UPDATE products SET display_order = {expression}"))

    op.create_index(
        "ix_products_bar_display_order",
        "products",
        ["bar_id", "display_order", "id"],
    )


def downgrade():
    op.drop_index("ix_products_bar_display_order", table_name="products")
    op.drop_column("products", "display_order")
