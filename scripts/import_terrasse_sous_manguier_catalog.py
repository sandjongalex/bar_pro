"""One-off, tenant-scoped REPLACEMENT catalog for Terrasse sous manguier.

Preview by default. Physically removes all former product-category rows.
Unreferenced old products are deleted; historically referenced ones are archived
and linked to one of the six new categories, while preserving their old name,
ID and history. Then imports 86 clean products. Does not reset sales or stock.
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


# Reuse the exact canonical starter manifest; do not maintain divergent copies.
from app.default_catalog import DEFAULT_CATALOG as CATALOG


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


def archived_category_for(product: Product, old_categories: dict[int, str]) -> str:
    """Choose a safe existing category for an INACTIVE historical product.

    Archived products cannot retain an old category FK if its row is deleted.
    These choices only organize the hidden archival records; none become saleable.
    """
    name = normalize(product.name)
    category = normalize(old_categories.get(product.category_id, ""))
    candidate = f"{name} {category}"
    if "canette" in candidate:
        return "Bières en canette"
    if any(word in candidate for word in ("whisky", "spiritueux", "liqueur", "gin", "vodka", "rhum")):
        return "Whiskies et spiritueux"
    if any(word in candidate for word in ("vin", "mousseux", "champagne", "moscato", "chateau")):
        return "Vins et mousseux"
    if any(word in candidate for word in ("eau", "water", "tangui", "vital")):
        return "Eaux"
    if any(word in candidate for word in ("biere", "beer", "export", "guinness", "castel", "mutzig")):
        return "Bières en bouteille"
    return "Jus et boissons fruitées"


def apply_catalog(bar_id: int):
    """Clear all old categories and safely create the six-category catalog."""
    db.session.scalar(select(Bar).where(Bar.id == bar_id).with_for_update())
    bar, _, categories, existing, imported, legacy, rows, deletable, archival = inspect(bar_id)

    # FIRST: permanently remove unreferenced old products; historical products
    # are never destroyed. Zero balances attached to deleted rows go too.
    old_category_names = {category.id: category.name for category in categories}
    for item in archival:
        item.is_active = False

    # IMPORTANT: the composite FK stock_balances -> products uses RESTRICT.
    # Deleting a StockBalance and its Product in a single ORM flush can make
    # MySQL delete the parent first. Remove ALL zero balances and flush first.
    # Also avoid per-product queries here, which otherwise trigger autoflush
    # while parent deletions are already pending.
    deletable_ids = [item.id for item in deletable]
    if deletable_ids:
        balances = list(db.session.scalars(select(StockBalance).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id.in_(deletable_ids),
        )))
        for balance in balances:
            if Decimal(balance.quantity) != 0:
                raise ValueError(
                    "Stock non nul détecté pendant la suppression. "
                    "Importation annulée."
                )
            db.session.delete(balance)
        db.session.flush()  # Child rows must be gone BEFORE deleting parents.
        for item in deletable:
            db.session.delete(item)
        db.session.flush()
    else:
        db.session.flush()

    # SECOND: create or activate exactly the desired six categories. Existing
    # categories with the same names are reused; no duplicate names or IDs.
    wanted_categories = {category_name.casefold() for category_name, _, _ in CATALOG}
    by_category = {category.name.casefold(): category for category in categories}
    category_ids = {}
    for category_name, _, _ in CATALOG:
        category = by_category.get(category_name.casefold())
        if category is None:
            category = ProductCategory(bar_id=bar_id, name=category_name, is_active=True)
            db.session.add(category)
            db.session.flush()
        else:
            category.is_active = True
            category.name = category_name
        category_ids[category_name] = category.id

    # THIRD: reclassify only INACTIVE old products still referenced by history.
    # This breaks their FK to removed categories without losing sales records.
    archived_reclassified = 0
    for item in archival:
        old_name = old_category_names.get(item.category_id, "")
        new_category_name = archived_category_for(item, old_category_names)
        item.category_id = category_ids[new_category_name]
        if normalize(old_name) != normalize(new_category_name):
            archived_reclassified += 1
    db.session.flush()

    # FOURTH: there must be no product FK left pointing to an old category.
    # Deleting historical category rows is safe only after the reclassification.
    old_categories = [
        category for category in categories
        if category.name.casefold() not in wanted_categories
    ]
    for category in old_categories:
        remaining = db.session.scalar(select(Product.id).where(
            Product.bar_id == bar_id,
            Product.category_id == category.id,
        ).limit(1))
        if remaining is not None:
            raise ValueError(
                f"La catégorie {category.name} est toujours utilisée ; "
                "importation annulée sans suppression partielle."
            )
        db.session.delete(category)
    db.session.flush()

    # FIFTH: insert 86 independent new products (not reused old records).
    # Re-executions only update rows from the reserved dedicated SKU prefix.
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
    expected_skus = {f"{SKU_PREFIX}{rank:03d}".casefold() for rank in range(1, 87)}
    active_skus = {item.sku.casefold() for item in active_products}
    if len(active_products) != 86 or active_skus != expected_skus:
        raise ValueError("Vérification finale échouée : 86 nouveaux produits actifs attendus.")
    all_categories = list(db.session.scalars(select(ProductCategory).where(
        ProductCategory.bar_id == bar_id
    )))
    category_names = {category.name.casefold() for category in all_categories}
    if len(all_categories) != 6 or category_names != wanted_categories or not all(
        category.is_active for category in all_categories
    ):
        raise ValueError(
            "Vérification finale échouée : exactement six catégories, "
            "aucune ancienne catégorie, même inactive."
        )
    return {
        "created": created,
        "updated_on_rerun": updated,
        "old_deleted": len(deletable),
        "old_archived_with_history": len(archival),
        "archived_reclassified": archived_reclassified,
        "old_categories_deleted": len(old_categories),
        "categories_remaining_total": len(all_categories),
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
            print(f"Anciennes catégories à supprimer définitivement : "
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
