"""Editable starter catalogue for newly created BAR_PRO establishments.

The 86 products and 6 families are the reference catalog for a new XAF bar.
Each new bar receives its own tenant-scoped copies, separate prices and zero
initial stock. Existing establishments are never modified automatically.
Source: user-approved Terrasse sous manguier catalogue, November 2026.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata

from sqlalchemy import select

from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance


DEFAULT_CATALOG_CURRENCY = "XAF"

# (Category, base unit, (product name, sale price in XAF)).
# No packaging size is assumed: units_per_case is set individually per bar.
DEFAULT_CATALOG = (
    ("Bières en bouteille", "bouteille", (
        ("33 Export", 900), ("Mutzig", 900), ("Castel", 900),
        ("Doppel", 900), ("Smooth GM", 1500), ("Smooth PM", 900),
        ("Kadji", 1000), ("Beaufort ordinaire", 900),
        ("Beaufort Light", 900), ("Isembeck", 1000),
        ("Guinness GM", 1500), ("Guinness OPM", 900),
        ("Harp", 900), ("Chill", 750), ("Castlé", 900),
        ("Doppol Lader", 1000), ("Manyan", 700),
        ("Doppel Munich", 900), ("K44", 800), ("Heineken", 1000),
        ("Desperado", 1000), ("Scolt bouteille", 1000),
        ("Booster Gin", 900), ("Booster Kola", 900),
        ("Ice GM", 900), ("Ice PM", 900), ("Orijin", 900),
    )),
    ("Bières en canette", "canette", (
        ("Beaufort canette", 1000), ("Isembeck canette", 1000),
        ("Castel canette", 1000), ("33 canette", 1000),
        ("Vody", 1000), ("Bavaria", 1000), ("3X énergie", 1000),
    )),
    ("Jus et boissons fruitées", "bouteille", (
        ("Jus spécial Razzl", 800), ("Jus UCB bouteille", 500),
        ("Extra Fresco", 500), ("Malta", 900),
        ("Petit Top Bt", 400), ("Top bouteille", 500),
        ("Top plastique", 600), ("Orangina plastique", 800),
        ("Djino plastique", 800), ("Tonic", 500), ("Soda", 500),
        ("Fanta", 800), ("Coca-Cola", 800), ("Sweppers GM", 800),
        ("Djino bouteille", 500), ("Vinto bouteille", 500),
        ("Sprite GM", 500), ("Reactor", 500), ("Vinto plastique", 800),
    )),
    ("Eaux", "bouteille", (
        ("Supermon", 500), ("Vital", 500), ("Madiba", 500),
        ("Tangui", 500), ("Eau Pur", 400),
    )),
    ("Vins et mousseux", "bouteille", (
        ("Elvino blanc", 1500), ("Elvino rouge", 1500),
        ("Cantoulou rouge", 4000), ("Cantoulou blanc", 4000),
        ("Cuve de Roi", 1500), ("Perlado", 2000),
        ("Louis Eschauner", 8000), ("Château des Matineaux", 4000),
        ("Mont Meyrac", 4000), ("Vieux Moulin", 4000),
        ("Moscato", 10000), ("Isabel de France", 4000),
    )),
    ("Whiskies et spiritueux", "bouteille", (
        ("Jack Daniel", 20000), ("Black Label", 25000),
        ("Red Label", 15000), ("Grants GM", 15000),
        ("Grants PM", 13000), ("Imperial Blue", 8000),
        ("Monkey", 40000), ("Chivas", 25000),
        ("J&B", 12000), ("Black & White", 8000),
        ("Andy Player", 8000), ("St Henry", 8000),
        ("Royal Circle", 10000), ("Baletine", 10000),
        ("Martin Ricman", 8000), ("Martini", 15000),
    )),
)


def _sku(category: str, name: str) -> str:
    """Stable per-bar reference. Names are only a catalog seed, not identities."""
    normalized = unicodedata.normalize("NFKD", f"{category}-{name}")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").upper()
    return ("DEF-" + slug)[:64]


def seed_default_catalog(bar_id: int) -> dict[str, int]:
    """Add missing starter items to ONE bar, without resetting customized data.

    Intended to run once on new bars. Explicit CLI use on an existing bar
    remains non-destructive: existing same-name or same-SKU products are kept.
    The caller owns commit / rollback.
    """
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("BAR_NOT_FOUND")
    if bar.currency != DEFAULT_CATALOG_CURRENCY:
        raise ValueError("DEFAULT_CATALOG_REQUIRES_XAF")

    categories = {
        category.name.casefold(): category
        for category in db.session.scalars(
            select(ProductCategory).where(ProductCategory.bar_id == bar_id)
        )
    }
    existing = list(db.session.scalars(
        select(Product).where(Product.bar_id == bar_id)
    ))
    products_by_name = {item.name.casefold(): item for item in existing}
    products_by_sku = {item.sku.casefold(): item for item in existing}
    categories_created = 0
    products_created = 0
    rank = 0

    for category_name, unit, rows in DEFAULT_CATALOG:
        category = categories.get(category_name.casefold())
        if category is None:
            category = ProductCategory(
                bar_id=bar_id, name=category_name, is_active=True
            )
            db.session.add(category)
            db.session.flush()
            categories[category_name.casefold()] = category
            categories_created += 1

        for product_name, price in rows:
            rank += 1
            sku = _sku(category_name, product_name)
            if (product_name.casefold() in products_by_name
                    or sku.casefold() in products_by_sku):
                continue
            product = Product(
                bar_id=bar_id,
                category_id=category.id,
                display_order=rank * 1000,
                sku=sku,
                name=product_name,
                base_unit=unit,
                sale_price=Decimal(str(price)),
                valuation_unit_cost=Decimal("0"),
                stock_alert_threshold=Decimal("0"),
                units_per_case=None,
                image_key=None,
                is_active=True,
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(StockBalance(
                bar_id=bar_id,
                product_id=product.id,
                quantity=Decimal("0"),
                version=0,
            ))
            products_by_name[product.name.casefold()] = product
            products_by_sku[product.sku.casefold()] = product
            products_created += 1

    return {
        "categories_created": categories_created,
        "products_created": products_created,
    }
