"""Default catalogue assigned to every bar.

The seed is intentionally idempotent: running it again only creates missing
categories/products and never overwrites a product that already exists in the
bar. Prices come from the default XOF catalogue supplied for Bar Manager Pro.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata

from sqlalchemy import select

from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance
from app.purchase_defaults import default_units_per_case

DEFAULT_CATALOG_CURRENCY = "XOF"

DEFAULT_CATALOG = (
    (
        "Bières locales",
        "BEER",
        (
            ("33 Export", 800),
            ("Mutzig", 800),
            ("Castel", 800),
            ("Dopel", 800),
            ("Isembeck", 900),
            ("Beaufort", 800),
            ("Castle", 800),
            ("Mayan", 600),
            ("Booster", 800),
            ("Chil", 700),
            ("Boster Racine", 600),
            ("Heineken G", 1500),
            ("Heineken", 1000),
            ("Vampur", 800),
            ("Bavaria", 1000),
            ("Vody", 800),
            ("1664", 1000),
            ("P. Guinness", 850),
            ("G. Guinness", 1500),
            ("Origin", 850),
            ("Smooth GM", 850),
            ("Smooth PM", 500),
            ("Malta", 700),
            ("Harp", 850),
            ("Ice G", 1000),
            ("Ice", 800),
            ("Kadji", 800),
            ("K44", 600),
            ("Booster Gin G", 1000),
        ),
    ),
    (
        "Jus et boissons",
        "DRINK",
        (
            ("Tonic", 300),
            ("Malta Tonic", 500),
            ("Soda", 500),
            ("Top 12", 400),
            ("Coca 12", 300),
            ("Vinto 12", 600),
            ("Coca PET", 800),
            ("Top PET", 600),
            ("Djino PET", 800),
            ("KQ", 300),
            ("UCB", 500),
            ("Spécial Pamplemousse", 1000),
            ("Vimto", 800),
            ("UCB PET", 800),
            ("Reactor", 500),
            ("Sprite", 700),
            ("Délice", 800),
            ("Orangina", 800),
        ),
    ),
    (
        "Vins rouges, blancs et roses",
        "WINE-MIX",
        (("Cuve du Roi", 1100),),
    ),
    (
        "Vins rouges",
        "WINE-RED",
        (
            ("El Vino", 1250),
            ("C. Morgan", 1500),
            ("Japap", 650),
        ),
    ),
    (
        "Eaux minérales",
        "WATER",
        (
            ("Eau Super M", 500),
            ("Opur", 350),
            ("Vital", 350),
        ),
    ),
    (
        "Whisky et spiritueux",
        "SPIRIT",
        (("Black And", 2000),),
    ),
)


def _sku(prefix: str, name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").upper()
    return f"DEF-{prefix}-{slug}"[:64]


def seed_default_catalog(bar_id: int) -> dict[str, int]:
    """Create every missing default category/product for one bar.

    Existing products are matched by either name or generated default SKU and
    are left untouched so a bar's manually customised price is never reset.
    """
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("BAR_NOT_FOUND")

    categories = {
        item.name.casefold(): item
        for item in db.session.scalars(
            select(ProductCategory).where(ProductCategory.bar_id == bar_id)
        )
    }
    products = list(
        db.session.scalars(select(Product).where(Product.bar_id == bar_id))
    )
    products_by_name = {item.name.casefold(): item for item in products}
    products_by_sku = {item.sku.upper(): item for item in products}

    categories_created = 0
    products_created = 0

    for category_name, prefix, rows in DEFAULT_CATALOG:
        category = categories.get(category_name.casefold())
        if category is None:
            category = ProductCategory(
                bar_id=bar_id,
                name=category_name,
                is_active=True,
            )
            db.session.add(category)
            db.session.flush()
            categories[category_name.casefold()] = category
            categories_created += 1

        for product_name, price in rows:
            sku = _sku(prefix, product_name)
            if product_name.casefold() in products_by_name or sku.upper() in products_by_sku:
                continue

            product = Product(
                bar_id=bar_id,
                category_id=category.id,
                sku=sku,
                name=product_name,
                base_unit="bouteille",
                sale_price=Decimal(str(price)),
                valuation_unit_cost=Decimal("0"),
                stock_alert_threshold=Decimal("0"),
                units_per_case=default_units_per_case(product_name),
                image_key=None,
                is_active=True,
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(
                StockBalance(
                    bar_id=bar_id,
                    product_id=product.id,
                    quantity=Decimal("0"),
                    version=0,
                )
            )
            products_by_name[product.name.casefold()] = product
            products_by_sku[product.sku.upper()] = product
            products_created += 1

    return {
        "categories_created": categories_created,
        "products_created": products_created,
    }
