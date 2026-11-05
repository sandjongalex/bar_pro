"""Owner-managed product catalogue setup for newly created bars."""
from alembic import op
import sqlalchemy as sa


revision = "0a1b2c3d4e5f"
down_revision = "f9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bars",
        sa.Column(
            "catalog_configured",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Every bar that existed before this feature already had an operational
    # catalogue. Preserve those tenants and only require setup for new bars.
    op.execute(sa.text("UPDATE bars SET catalog_configured = 1"))


def downgrade():
    op.drop_column("bars", "catalog_configured")
