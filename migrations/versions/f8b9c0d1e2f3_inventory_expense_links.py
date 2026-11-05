"""Tie late inventory expenses to the inventory financial period.

Revision ID: f8b9c0d1e2f3
Revises: f7b8c9d0e1f2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "f8b9c0d1e2f3"
down_revision = "f7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    op.create_table(
        "inventory_expense_links",
        sa.Column("id", ident, primary_key=True, nullable=False),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_id", ident, nullable=False),
        sa.Column("expense_id", ident, nullable=False),
        sa.Column("linked_by_id", ident, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "inventory_id"],
            ["inventories.bar_id", "inventories.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "expense_id"],
            ["expenses.bar_id", "expenses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "bar_id", "id", name="uq_inventory_expense_links_bar_id_id"
        ),
        sa.UniqueConstraint(
            "bar_id", "expense_id", name="uq_inventory_expense_link_expense"
        ),
    )
    op.create_index(
        "ix_inventory_expense_links_bar_id",
        "inventory_expense_links",
        ["bar_id"],
    )
    op.create_index(
        "ix_inventory_expense_links_bar_created",
        "inventory_expense_links",
        ["bar_id", "created_at", "id"],
    )
    op.create_index(
        "ix_inventory_expense_link_inventory",
        "inventory_expense_links",
        ["bar_id", "inventory_id", "expense_id"],
    )

    # Backfill expenses already entered through the inventory finance panel.
    # Their original incurred_at is kept unchanged.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.execute(sa.text("""
            INSERT INTO inventory_expense_links
                (bar_id, inventory_id, expense_id, linked_by_id, created_at, updated_at)
            SELECT e.bar_id, i.id, e.id, e.recorded_by_id, e.created_at, e.updated_at
            FROM expenses AS e
            JOIN inventories AS i ON i.bar_id = e.bar_id
            WHERE e.reference LIKE 'INVEXP-%'
              AND substr(
                    e.description, 1,
                    length('Inventaire ' || i.reference || ' : ')
                  ) = ('Inventaire ' || i.reference || ' : ')
        """))
    else:
        bind.execute(sa.text("""
            INSERT INTO inventory_expense_links
                (bar_id, inventory_id, expense_id, linked_by_id, created_at, updated_at)
            SELECT e.bar_id, i.id, e.id, e.recorded_by_id, e.created_at, e.updated_at
            FROM expenses AS e
            JOIN inventories AS i ON i.bar_id = e.bar_id
            WHERE e.reference LIKE 'INVEXP-%'
              AND LEFT(
                    e.description,
                    CHAR_LENGTH(CONCAT('Inventaire ', i.reference, ' : '))
                  ) = CONCAT('Inventaire ', i.reference, ' : ')
        """))


def downgrade():
    op.drop_index(
        "ix_inventory_expense_link_inventory",
        table_name="inventory_expense_links",
    )
    op.drop_index(
        "ix_inventory_expense_links_bar_id",
        table_name="inventory_expense_links",
    )
    op.drop_index(
        "ix_inventory_expense_links_bar_created",
        table_name="inventory_expense_links",
    )
    op.drop_table("inventory_expense_links")
