"""One-off, tenant-scoped catalog import for Terrasse sous manguier.

Preview by default. This does not reset transactions or stock.
Run from the BAR_PRO repository root:
    python -m scripts.import_terrasse_sous_manguier_catalog --list-bars
    python -m scripts.import_terrasse_sous_manguier_catalog --bar-id 123
    python -m scripts.import_terrasse_sous_manguier_catalog --bar-id 123 --apply \
        --confirm-name "Terrasse sous manguier" --confirm-owner-id 456

The --confirm-name value must match the name displayed in the preview.
No casier size is guessed: units_per_case remains NULL for new products.
"""
from __future__ import annotations

import argparse
import os
import re
import unicodedata
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance, User
from app.warehouse_models import WarehouseStockBalance, WarehouseTransitBalance


# The user-provided names/prices, with Fanta/Coca and Elvino blanc/rouge separated.
CATALOG = (
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


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def is_target_bar(name: str) -> bool:
    name = normalize(name)
    return "sous manguier" in name and (
        "terrasse" in name or "terasse" in name
    )


def expected_rows():
    """Validate the data manifest before any database access."""
    if len(CATALOG) != 6:
        raise ValueError("Le catalogue doit contenir 6 catégories.")
    names = set()
    rows = []
    for category, unit, products in CATALOG:
        if not products or len(category) > 100:
            raise ValueError("Catégorie invalide.")
        for name, price in products:
            key = name.casefold()
            if key in names or price <= 0 or len(name) > 160:
                raise ValueError(f"Produit dupliqué ou invalide : {name}")
            names.add(key)
            rows.append((category, unit, name, Decimal(price)))
    if len(rows) != 86:
        raise ValueError("Le catalogue doit contenir 86 produits.")
    return rows


def inspect(bar_id: int):
    """Read-only scope and stock checks; rejects ambiguous product matches."""
    bar = db.session.get(Bar, bar_id)
    if bar is None or not is_target_bar(bar.name):
        raise ValueError("Bar introuvable ou nom inattendu : aucune modification.")
    owner = db.session.get(User, bar.owner_id)
    if owner is None or owner.category != "OWNER":
        raise ValueError("Le propriétaire du bar n'a pas pu être vérifié.")

    rows = expected_rows()
    wanted = {name.casefold() for _, _, name, _ in rows}
    existing = list(db.session.scalars(select(Product).where(Product.bar_id == bar_id)))
    categories = list(db.session.scalars(
        select(ProductCategory).where(ProductCategory.bar_id == bar_id)
    ))
    by_name = {}
    for product in existing:
        if product.name.casefold() in wanted:
            if product.name.casefold() in by_name:
                raise ValueError(f"Nom ambigu dans le catalogue existant : {product.name}")
            by_name[product.name.casefold()] = product

    obsolete = [item for item in existing if item.name.casefold() not in wanted]
    obsolete_ids = [item.id for item in obsolete]
    if obsolete_ids:
        balances = []
        for model in (StockBalance, WarehouseStockBalance, WarehouseTransitBalance):
            balances.extend(db.session.scalars(
                select(model).where(model.bar_id == bar_id, model.product_id.in_(obsolete_ids))
            ).all())
        if any(Decimal(balance.quantity) != 0 for balance in balances):
            raise ValueError(
                "Un ancien produit possède du stock (comptoir, magasin principal "
                "ou transit). Effectuez d'abord une vérification de stock. "
                "Aucune quantité ne sera supprimée ou remise à zéro."
            )
    return bar, owner, categories, existing, by_name, obsolete, rows


def apply_catalog(bar_id: int):
    """Archive unwanted catalog rows and upsert the manifest atomically."""
    # Serialize cooperating import attempts for the same bar.
    db.session.scalar(select(Bar).where(Bar.id == bar_id).with_for_update())
    bar, _, categories, existing, by_name, obsolete, rows = inspect(bar_id)
    by_category = {c.name.casefold(): c for c in categories}
    category_ids = {}
    for category_name, _, _ in CATALOG:
        category = by_category.get(category_name.casefold())
        if category is None:
            category = ProductCategory(bar_id=bar_id, name=category_name, is_active=True)
            db.session.add(category)
            db.session.flush()
        else:
            category.is_active = True
        category_ids[category_name] = category.id

    used_skus = {product.sku.casefold() for product in existing}
    new_count = 0
    updated_count = 0
    for rank, (category_name, unit, name, price) in enumerate(rows, start=1):
        product = by_name.get(name.casefold())
        if product is None:
            sku_base = f"TSM-{rank:03d}"
            sku = sku_base
            suffix = 2
            while sku.casefold() in used_skus:
                sku = f"{sku_base}-{suffix}"
                suffix += 1
            used_skus.add(sku.casefold())
            product = Product(
                bar_id=bar_id, category_id=category_ids[category_name],
                sku=sku, name=name, base_unit=unit, sale_price=price,
                valuation_unit_cost=Decimal("0"),
                stock_alert_threshold=Decimal("0"), units_per_case=None,
                display_order=rank * 1000, is_active=True,
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(StockBalance(
                bar_id=bar_id, product_id=product.id,
                quantity=Decimal("0"), version=0,
            ))
            new_count += 1
        else:
            product.name = name
            product.category_id = category_ids[category_name]
            product.sale_price = price
            product.display_order = rank * 1000
            product.is_active = True
            # Preserve base unit, cost, packaging and historical stock of existing rows.
            updated_count += 1
    for product in obsolete:
        product.is_active = False
    wanted_categories = {category.casefold() for category, _, _ in CATALOG}
    for category in categories:
        if category.name.casefold() not in wanted_categories:
            category.is_active = False
    bar.catalog_configured = True
    db.session.flush()
    return {"created": new_count, "updated": updated_count, "archived": len(obsolete)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-bars", action="store_true", help="Lister les bars dont le nom correspond.")
    parser.add_argument("--bar-id", type=int, help="Identifiant exact du bar cible.")
    parser.add_argument("--apply", action="store_true", help="Écrire après aperçu et double confirmation.")
    parser.add_argument("--confirm-name", help="Nom exact affiché lors de l'aperçu.")
    parser.add_argument("--confirm-owner-id", type=int, help="ID du propriétaire affiché lors de l'aperçu.")
    args = parser.parse_args()

    if not args.list_bars and (args.bar_id is None or args.bar_id <= 0):
        parser.error("--bar-id doit être un entier positif, sauf avec --list-bars.")
    load_dotenv()
    app = create_app(os.environ.get("FLASK_CONFIG", "production"))
    with app.app_context():
        try:
            if args.list_bars:
                for bar in db.session.scalars(select(Bar).order_by(Bar.id)):
                    if is_target_bar(bar.name):
                        owner = db.session.get(User, bar.owner_id)
                        print(f"Bar ID={bar.id} | {bar.name} | propriétaire ID={bar.owner_id} "
                              f"({owner.display_name if owner else 'introuvable'})")
                return
            bar, owner, categories, products, matches, obsolete, rows = inspect(args.bar_id)
            print(f"Bar : {bar.id} - {bar.name}")
            print(f"Propriétaire : {owner.id} - {owner.display_name}")
            print(f"Catégories cibles : {len(CATALOG)} | produits cibles : {len(rows)}")
            print(f"À créer : {len(rows) - len(matches)} | à mettre à jour : {len(matches)} "
                  f"| anciens produits à archiver : {len(obsolete)}")
            print(f"Anciennes catégories hors catalogue : "
                  f"{sum(normalize(c.name) not in {normalize(n) for n, _, _ in CATALOG} for c in categories)}")
            if not args.apply:
                print("APERÇU UNIQUEMENT : aucune écriture.")
                return
            if normalize(args.confirm_name) != normalize(bar.name):
                raise ValueError("La confirmation du nom du bar est incorrecte.")
            if args.confirm_owner_id != owner.id:
                raise ValueError("La confirmation de l'ID du propriétaire est incorrecte.")
            result = apply_catalog(args.bar_id)
            db.session.commit()
            print("IMPORT CONFIRMÉ : " + str(result) + ". 86 produits actifs attendus.")
        except Exception as error:
            db.session.rollback()
            parser.exit(1, f"Import annulé, transaction annulée : {error}\n")
        finally:
            db.session.remove()


if __name__ == "__main__":
    main()
