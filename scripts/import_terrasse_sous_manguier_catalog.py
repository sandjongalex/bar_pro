"""One-off, tenant-scoped REPLACEMENT catalog for Terrasse sous manguier.

Preview by default. Deletes unused legacy rows or archives linked rows BEFORE
inserting a clean, fresh set of 86 products. Does not reset sales or stock.
Run from the BAR_PRO repository root:
    python -m scripts.import_terrasse_sous_manguier_catalog --list-bars
    python -m scripts.import_terrasse_sous_manguier_catalog --bar-id 123
    python -m scripts.import_terrasse_sous_manguier_catalog --bar-id 123 --apply \
        --confirm-name "Terrasse sous manguier" --confirm-owner-id 456

The --confirm-name value must match the name displayed in the preview.
No casier size is guessed: units_per_case remains NULL for new products.
Existing old products are NEVER reused, even if their name matches a new one.
A repeated run updates only products previously imported by this script.
"""
from __future__ import annotations

import argparse
import os
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


SKU_PREFIX = "TSM2026-"


def product_references(bar_id: int, ids: list[int]) -> set[int]:
    """Products referenced by any table besides removable zero StockBalance.

    This includes historical sales, purchases, inventories and warehouse tables.
    Detect references from SQLAlchemy metadata, not a partial hard-coded list.
    """
    if not ids:
        return set()
    linked = set()
    for table in db.metadata.tables.values():
        if table.name in {"products", "stock_balances"}:
            continue
        product_columns = {
            foreign_key.parent
            for foreign_key in table.foreign_keys
            if foreign_key.target_fullname == "products.id"
        }
        for column in product_columns:
            condition = [column.in_(ids)]
            if "bar_id" in table.c:
                condition.append(table.c.bar_id == bar_id)
            linked.update(db.session.scalars(
                select(column).where(*condition).distinct()
            ).all())
    return linked


def inspect(bar_id: int):
    """Read-only catalog scope, links, and stock preflight."""
    bar = db.session.get(Bar, bar_id)
    if bar is None or not is_target_bar(bar.name):
        raise ValueError("Bar introuvable ou nom inattendu : aucune modification.")
    owner = db.session.get(User, bar.owner_id)
    if owner is None or owner.category != "OWNER":
        raise ValueError("Le propriétaire du bar n'a pas pu être vérifié.")

    rows = expected_rows()
    existing = list(db.session.scalars(
        select(Product).where(Product.bar_id == bar_id)
    ))
    categories = list(db.session.scalars(
        select(ProductCategory).where(ProductCategory.bar_id == bar_id)
    ))

    # Only script-generated SKUs can qualify as already imported on reruns.
    # Same-name OLD products must be removed, NOT updated in place.
    imported = {}
    for item in existing:
        if item.sku.casefold().startswith(SKU_PREFIX.casefold()):
            imported[item.sku.casefold()] = item
    expected_skus = {
        f"{SKU_PREFIX}{rank:03d}".casefold(): name
        for rank, (_, _, name, _) in enumerate(rows, start=1)
    }
    for sku, item in imported.items():
        if sku not in expected_skus or item.name.casefold() != expected_skus[sku].casefold():
            raise ValueError(
                f"SKU réservé incompatible : {item.sku} ({item.name}). "
                "Aucune modification."
            )

    legacy = [item for item in existing if item.id not in {p.id for p in imported.values()}]
    legacy_ids = [item.id for item in legacy]
    if legacy_ids:
        for model in (StockBalance, WarehouseStockBalance, WarehouseTransitBalance):
            balances = db.session.scalars(
                select(model).where(
                    model.bar_id == bar_id, model.product_id.in_(legacy_ids)
                )
            ).all()
            if any(Decimal(balance.quantity) != 0 for balance in balances):
                raise ValueError(
                    "Un ancien produit possède encore du stock (comptoir, "
                    "magasin principal ou transit). Aucun produit ne sera effacé. "
                    "Contrôlez le stock avant l'import."
                )

    linked_ids = product_references(bar_id, legacy_ids)
    deletable = [item for item in legacy if item.id not in linked_ids]
    archival = [item for item in legacy if item.id in linked_ids]
    return bar, owner, categories, existing, imported, legacy, rows, deletable, archival


def apply_catalog(bar_id: int):
    """Remove old products first; then insert 86 new products atomically."""
    db.session.scalar(select(Bar).where(Bar.id == bar_id).with_for_update())
    bar, _, categories, existing, imported, legacy, rows, deletable, archival = inspect(bar_id)

    # FIRST PHASE: physically remove unlinked old products and their zero stock
    # balances, archiving linked products instead so FKs/history remain valid.
    for item in archival:
        item.is_active = False
    for item in deletable:
        balance = db.session.scalar(select(StockBalance).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == item.id,
        ))
        if balance is not None:
            db.session.delete(balance)
        db.session.delete(item)
    db.session.flush()

    # Remove obsolete categories if now empty; otherwise hide them from sales.
    wanted_categories = {category.casefold() for category, _, _ in CATALOG}
    by_category = {category.name.casefold(): category for category in categories}
    deleted_categories = 0
    for category in categories:
        if category.name.casefold() in wanted_categories:
            continue
        has_product = db.session.scalar(
            select(Product.id).where(
                Product.bar_id == bar_id,
                Product.category_id == category.id,
            ).limit(1)
        )
        if has_product is None:
            db.session.delete(category)
            deleted_categories += 1
        else:
            category.is_active = False
    db.session.flush()

    # SECOND PHASE: create/re-enable 6 categories after old data is removed.
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

    # THIRD PHASE: every old product has been removed/archived. Import 86 rows
    # with deterministic, dedicated SKUs; on rerun only these are updated.
    created = 0
    updated = 0
    for rank, (category_name, unit, name, price) in enumerate(rows, start=1):
        sku = f"{SKU_PREFIX}{rank:03d}"
        product = imported.get(sku.casefold())
        if product is None:
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
            created += 1
        else:
            product.category_id = category_ids[category_name]
            product.name = name
            product.sale_price = price
            product.display_order = rank * 1000
            product.is_active = True
            updated += 1

    bar.catalog_configured = True
    db.session.flush()
    active_products = list(db.session.scalars(select(Product).where(
        Product.bar_id == bar_id, Product.is_active.is_(True)
    )))
    active_skus = {item.sku.casefold() for item in active_products}
    if len(active_products) != 86 or active_skus != set(expected_sku.casefold()
            for expected_sku in (f"{SKU_PREFIX}{rank:03d}" for rank in range(1, 87))):
        raise ValueError("Vérification finale échouée : 86 nouveaux produits actifs attendus.")
    active_categories = list(db.session.scalars(select(ProductCategory).where(
        ProductCategory.bar_id == bar_id, ProductCategory.is_active.is_(True)
    )))
    if len(active_categories) != 6:
        raise ValueError("Vérification finale échouée : 6 catégories actives attendues.")
    return {
        "created": created,
        "updated_on_rerun": updated,
        "old_deleted": len(deletable),
        "old_archived_with_history": len(archival),
        "old_categories_deleted": deleted_categories,
    }


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
            bar, owner, categories, products, imported, legacy, rows, deletable, archival = inspect(args.bar_id)
            print(f"Bar : {bar.id} - {bar.name}")
            print(f"Propriétaire : {owner.id} - {owner.display_name}")
            print(f"Catégories cibles : {len(CATALOG)} | produits cibles : {len(rows)}")
            print(f"À créer : {len(rows) - len(imported)} | à mettre à jour (réexécution) : {len(imported)}")
            print(f"Anciens produits : {len(legacy)} | supprimables : {len(deletable)} "
                  f"| à archiver (références conservées) : {len(archival)}")
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
