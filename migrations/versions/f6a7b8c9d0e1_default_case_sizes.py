"""Backfill default bottles-per-case values for known catalogue products.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


DEFAULT_CASE_SIZES = {
    "33 export": 12,
    "mutzig": 12,
    "castel": 12,
    "dopel": 12,
    "isembeck": 12,
    "beaufort": 12,
    "castle": 12,
    "mayan": 12,
    "booster": 12,
    "chil": 12,
    "boster racine": 12,
    "heineken g": 12,
    "heineken": 24,
    "vampur": 24,
    "bavaria": 24,
    "vody": 24,
    "1664": 24,
    "p. guinness": 24,
    "g. guinness": 12,
    "origin": 12,
    "smooth gm": 15,
    "smooth pm": 24,
    "malta": 24,
    "harp": 12,
    "ice g": 12,
    "ice": 24,
    "kadji": 12,
    "k44": 12,
    "booster gin g": 12,
    "tonic": 12,
    "malta tonic": 24,
    "soda": 12,
    "top 12": 12,
    "coca 12": 12,
    "vinto 12": 12,
    "coca pet": 6,
    "top pet": 6,
    "djino pet": 6,
    "kq": 12,
    "ucb": 12,
    "spécial pamplemousse": 12,
    "vimto": 6,
    "ucb pet": 6,
    "reactor": 12,
    "sprite": 6,
    "délice": 24,
    "orangina": 6,
    "eau super m": 6,
    "opur": 6,
    "vital": 6,
    "cuve du roi": 12,
    "el vino": 12,
    "c. morgan": 24,
    "japap": 6,
    "black and": 12,
}


def upgrade():
    products = sa.table(
        "products",
        sa.column("name", sa.String(160)),
        sa.column("units_per_case", sa.Integer()),
    )
    connection = op.get_bind()
    for product_name, case_size in DEFAULT_CASE_SIZES.items():
        connection.execute(
            products.update()
            .where(sa.func.lower(sa.func.trim(products.c.name)) == product_name)
            .where(products.c.units_per_case.is_(None))
            .values(units_per_case=case_size)
        )


def downgrade():
    # Data-only default backfill. Preserve operational product configuration on
    # downgrade rather than erasing values that may have been confirmed/edited
    # after this migration was applied.
    pass
