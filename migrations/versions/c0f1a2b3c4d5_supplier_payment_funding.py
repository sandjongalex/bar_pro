"""Distinguish personal owner funds from operational cash-drawer supplier payments.

Revision ID: c0f1a2b3c4d5
Revises: b1c2d3e4f5a6

Classify known legacy cash payments as CASH_DRAWER. Historical non-cash
payment provenance stays UNSPECIFIED, never invented. Payment amounts and cash
movements are preserved without change.
"""
from alembic import op
import sqlalchemy as sa

revision = "c0f1a2b3c4d5"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supplier_payments") as batch:
        batch.add_column(sa.Column(
            "funding_source", sa.String(20), nullable=False, server_default="UNSPECIFIED",
        ))
    op.execute(
        "UPDATE supplier_payments SET funding_source = 'CASH_DRAWER' "
        "WHERE method = 'CASH' AND cash_session_id IS NOT NULL"
    )
    with op.batch_alter_table("supplier_payments") as batch:
        batch.create_check_constraint(
            "ck_supplier_payments_funding_source",
            "funding_source IN ('UNSPECIFIED','OWNER_FUNDS','CASH_DRAWER')",
        )


def downgrade():
    raise RuntimeError(
        "Refusing to erase supplier payment funding provenance; restore from backup."
    )
