"""Add supplier notes and purchase unit/conversion metadata.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    money = sa.Numeric(19, 4)
    qty = sa.Numeric(20, 6)

    with op.batch_alter_table("suppliers") as batch:
        batch.add_column(sa.Column("note", sa.String(500), nullable=True))

    with op.batch_alter_table("purchases") as batch:
        batch.add_column(sa.Column("purchase_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("notes", sa.String(500), nullable=True))

    with op.batch_alter_table("purchase_lines") as batch:
        batch.add_column(sa.Column("purchase_unit", sa.String(16), nullable=True))
        batch.add_column(sa.Column("purchase_quantity", qty, nullable=True))
        batch.add_column(sa.Column("units_per_case_snapshot", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("purchase_unit_price_snapshot", money, nullable=True))

    # Existing purchases were previously entered directly in the base stock unit.
    op.execute("UPDATE purchases SET purchase_date = DATE(created_at) WHERE purchase_date IS NULL")
    op.execute("UPDATE purchase_lines SET purchase_unit = 'BOTTLE' WHERE purchase_unit IS NULL")
    op.execute("UPDATE purchase_lines SET purchase_quantity = quantity WHERE purchase_quantity IS NULL")
    op.execute(
        "UPDATE purchase_lines SET purchase_unit_price_snapshot = unit_cost_snapshot "
        "WHERE purchase_unit_price_snapshot IS NULL"
    )

    with op.batch_alter_table("purchases") as batch:
        batch.alter_column("purchase_date", existing_type=sa.Date(), nullable=False)

    with op.batch_alter_table("purchase_lines") as batch:
        batch.alter_column("purchase_unit", existing_type=sa.String(16), nullable=False)
        batch.alter_column("purchase_quantity", existing_type=qty, nullable=False)
        batch.alter_column("purchase_unit_price_snapshot", existing_type=money, nullable=False)
        batch.create_check_constraint(
            "ck_purchase_lines_purchase_unit",
            "purchase_unit IN ('CASE','BOTTLE')",
        )
        batch.create_check_constraint(
            "ck_purchase_lines_purchase_quantity",
            "purchase_quantity > 0",
        )
        batch.create_check_constraint(
            "ck_purchase_lines_case_size",
            "(purchase_unit = 'BOTTLE' AND units_per_case_snapshot IS NULL) OR "
            "(purchase_unit = 'CASE' AND units_per_case_snapshot IS NOT NULL AND units_per_case_snapshot > 0)",
        )


def downgrade():
    with op.batch_alter_table("purchase_lines") as batch:
        batch.drop_constraint("ck_purchase_lines_case_size", type_="check")
        batch.drop_constraint("ck_purchase_lines_purchase_quantity", type_="check")
        batch.drop_constraint("ck_purchase_lines_purchase_unit", type_="check")
        batch.drop_column("purchase_unit_price_snapshot")
        batch.drop_column("units_per_case_snapshot")
        batch.drop_column("purchase_quantity")
        batch.drop_column("purchase_unit")

    with op.batch_alter_table("purchases") as batch:
        batch.drop_column("notes")
        batch.drop_column("purchase_date")

    with op.batch_alter_table("suppliers") as batch:
        batch.drop_column("note")
