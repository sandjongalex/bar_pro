from decimal import Decimal, InvalidOperation
import re
import unicodedata

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from sqlalchemy.exc import IntegrityError
from flask_login import current_user, login_required
from sqlalchemy import select

from app.auth import api_required
from app.async_ui import wants_async_web_response, web_json_response
from app.catalog_services import (
    create_category,
    create_product,
    delete_product,
    list_all_products,
    list_categories,
    list_products,
    position_product,
    rename_category,
    set_category_active,
    update_product,
)
from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance
from app.permissions import permissions
from app.product_images import delete_product_image, product_image_directory
from app.stock_service import StockError, stock_service

catalog_bp = Blueprint("catalog", __name__, url_prefix="/bars/<int:bar_id>/catalog")
api_catalog_bp = Blueprint("api_catalog", __name__, url_prefix="/api/v1/bars/<int:bar_id>/products")


def output(item):
    return {
        "id": str(item.id),
        "name": item.name,
        "sku": item.sku,
        "category_id": str(item.category_id),
        "base_unit": item.base_unit,
        "sale_price": str(item.sale_price),
        "valuation_unit_cost": str(item.valuation_unit_cost),
        "stock_alert_threshold": str(item.stock_alert_threshold),
        "units_per_case": item.units_per_case,
        "is_active": item.is_active,
        "image_key": item.image_key,
        "image_url": (
            url_for("catalog.product_image", bar_id=item.bar_id, key=item.image_key)
            if item.image_key
            else None
        ),
        "display_order": item.display_order,
    }


def _message(code):
    messages = {
        "INVALID_CATEGORY_NAME": "Le nom de la catégorie est obligatoire.",
        "CATEGORY_EXISTS": "Cette catégorie existe déjà.",
        "INVALID_CATEGORY": "Sélectionnez une catégorie valide.",
        "INVALID_SKU": "La référence produit est obligatoire.",
        "SKU_EXISTS": "Cette référence produit existe déjà dans ce bar.",
        "INVALID_PRODUCT_NAME": "Le nom du produit est obligatoire.",
        "INVALID_BASE_UNIT": "L'unité de vente est obligatoire.",
        "INVALID_UNITS_PER_CASE": "Le nombre d'unités par casier doit être supérieur à zéro.",
        "sale_price": "Le prix de vente doit être un nombre positif ou nul.",
        "valuation_unit_cost": "Le coût d'achat doit être un nombre positif ou nul.",
        "stock_alert_threshold": "Le seuil d'alerte doit être un nombre positif ou nul.",
        "INITIAL_ALREADY_RECORDED": "Le stock initial de ce produit a déjà été enregistré.",
        "INVALID_DIRECTION": "La quantité de stock initial doit être supérieure à zéro.",
        "INVALID_IMAGE_TYPE": "La photo doit être au format JPG, JPEG, JFIF, PNG ou WebP.",
        "INVALID_IMAGE": "Le fichier sélectionné n'est pas une image valide.",
        "IMAGE_TOO_LARGE": "La photo dépasse la taille maximale de 4 Mo.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à modifier le catalogue.",
        "NOT_FOUND": "Élément introuvable.",
        "INVALID_PRODUCT_POSITION": "La position choisie pour le produit est invalide.",
    }
    return messages.get(str(code), "Opération impossible. Vérifiez les informations saisies.")


def _decimal_or_zero(value):
    try:
        result = Decimal(str(value or "0").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("INITIAL_QUANTITY") from None
    if result < 0:
        raise ValueError("INITIAL_QUANTITY")
    return result


def _product_form_data():
    return {
        "category_id": request.form.get("category_id"),
        "sku": request.form.get("sku", ""),
        "name": request.form.get("name", ""),
        "base_unit": request.form.get("base_unit", "unité"),
        "sale_price": request.form.get("sale_price", "0"),
        "valuation_unit_cost": request.form.get("valuation_unit_cost", "0"),
        "stock_alert_threshold": request.form.get("stock_alert_threshold", "0"),
        "units_per_case": request.form.get("units_per_case", ""),
    }


@catalog_bp.get("/images/<path:key>")
@login_required
def product_image(bar_id, key):
    permissions.require(current_user, "catalog.read", bar_id)
    item = Product.query.filter_by(bar_id=bar_id, image_key=key).first()
    if not item:
        raise LookupError("NOT_FOUND")
    return send_from_directory(str(product_image_directory()), key, conditional=True, max_age=86400)


@catalog_bp.get("/image-map")
@login_required
def product_image_map(bar_id):
    """Return image URLs for active products visible to the current bar employee."""
    permissions.require(current_user, "catalog.read", bar_id)
    rows = db.session.execute(
        select(Product.id, Product.image_key).where(
            Product.bar_id == bar_id,
            Product.is_active.is_(True),
            Product.image_key.is_not(None),
        )
    ).all()
    return jsonify(
        {
            "images": {
                str(product_id): url_for("catalog.product_image", bar_id=bar_id, key=image_key)
                for product_id, image_key in rows
                if image_key
            }
        }
    )



SETUP_CATEGORY_PRESETS = (
    ("Bières", "Bières locales, importées et bières pression"),
    ("Boissons gazeuses", "Sodas, tonic et boissons pétillantes"),
    ("Jus et boissons", "Jus naturels, nectars et boissons fruitées"),
    ("Eaux minérales", "Eaux plates et gazeuses"),
    ("Vins", "Vins rouges, blancs, rosés et mousseux"),
    ("Whisky et spiritueux", "Whisky, gin, rhum et autres spiritueux"),
    ("Boissons énergisantes", "Boissons énergétiques et stimulantes"),
    ("Snacks et accompagnements", "Petite restauration et accompagnements"),
    ("Autres", "Toutes les autres familles de produits"),
)


def _owned_setup_bar(bar_id):
    if current_user.category != "OWNER":
        abort(403)
    bar = db.session.get(Bar, bar_id)
    if bar is None or bar.owner_id != current_user.id:
        abort(404)
    return bar


def _setup_sku(bar_id, product_name):
    normalized = unicodedata.normalize("NFKD", product_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").upper() or "PRODUIT"
    base = ("SET-" + slug)[:64]
    candidate = base
    suffix = 2
    while Product.query.filter(
        Product.bar_id == bar_id,
        db.func.lower(Product.sku) == candidate.lower(),
    ).first():
        tail = f"-{suffix}"
        candidate = base[:64 - len(tail)] + tail
        suffix += 1
    return candidate


def _setup_error_message(error):
    messages = {
        "INVALID_CATEGORY_NAME": "Le nom d'une catégorie est invalide.",
        "CATEGORY_EXISTS": "Une catégorie portant ce nom existe déjà.",
        "INVALID_CATEGORY": "Sélectionnez une catégorie valide pour chaque produit.",
        "INVALID_PRODUCT_NAME": "Le nom de chaque produit est obligatoire.",
        "INVALID_BASE_UNIT": "Sélectionnez une unité de vente valide.",
        "INVALID_UNITS_PER_CASE": "Le nombre d'unités par casier doit être supérieur à zéro.",
        "sale_price": "Le prix de vente doit être un nombre positif ou nul.",
        "valuation_unit_cost": "Le coût d'achat doit être un nombre positif ou nul.",
        "stock_alert_threshold": "Le seuil d'alerte doit être un nombre positif ou nul.",
        "SETUP_CATEGORY_REQUIRED": "Sélectionnez ou créez au moins une catégorie.",
        "SETUP_SALE_PRICE_REQUIRED": "Indiquez le prix de vente de chaque produit.",
        "SETUP_REQUIRES_PRODUCT": "Ajoutez au moins un produit avant de terminer la configuration.",
    }
    if isinstance(error, IntegrityError):
        return "Une référence produit ou une catégorie existe déjà. Vérifiez les informations saisies."
    return messages.get(str(error), "La configuration n'a pas pu être enregistrée. Vérifiez les informations.")


@catalog_bp.route("/setup", methods=["GET", "POST"])
@login_required
def setup_categories(bar_id):
    bar = _owned_setup_bar(bar_id)
    if bar.catalog_configured:
        return redirect(url_for("catalog.web_list", bar_id=bar_id))

    existing_categories = list_categories(current_user, bar_id)
    selected_categories = [item.name for item in existing_categories]
    custom_categories = ""
    error_message = None

    if request.method == "POST":
        selected_categories = request.form.getlist("category_names")
        custom_categories = request.form.get("custom_categories", "")
        requested_names = list(selected_categories)
        requested_names.extend(
            name.strip()
            for name in re.split(r"[\n,;]+", custom_categories)
            if name.strip()
        )
        try:
            existing_by_name = {item.name.casefold(): item for item in existing_categories}
            seen = set()
            for raw_name in requested_names:
                name = str(raw_name or "").strip()
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                category = existing_by_name.get(name.casefold())
                if category is not None:
                    if not category.is_active:
                        set_category_active(current_user, bar_id, category.id, True)
                    continue
                category = create_category(current_user, bar_id, name)
                existing_by_name[category.name.casefold()] = category

            active_categories = list_categories(current_user, bar_id, active=True)
            if not active_categories:
                raise ValueError("SETUP_CATEGORY_REQUIRED")
            db.session.commit()
            return redirect(url_for("catalog.setup_products", bar_id=bar_id))
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            error_message = _setup_error_message(exc)
            existing_categories = list_categories(current_user, bar_id)

    return render_template(
        "catalog_setup_categories.html",
        bar=bar,
        category_presets=SETUP_CATEGORY_PRESETS,
        existing_categories=existing_categories,
        selected_categories=selected_categories,
        custom_categories=custom_categories,
        error=error_message,
    )


@catalog_bp.route("/setup/products", methods=["GET", "POST"])
@login_required
def setup_products(bar_id):
    bar = _owned_setup_bar(bar_id)
    if bar.catalog_configured:
        return redirect(url_for("catalog.web_list", bar_id=bar_id))

    categories = list_categories(current_user, bar_id, active=True)
    if not categories:
        return redirect(url_for("catalog.setup_categories", bar_id=bar_id))

    field_names = (
        "product_name",
        "category_id",
        "sale_price",
        "valuation_unit_cost",
        "base_unit",
        "units_per_case",
        "stock_alert_threshold",
    )
    form_rows = [{
        "product_name": "",
        "category_id": str(categories[0].id),
        "sale_price": "",
        "valuation_unit_cost": "0",
        "base_unit": "bouteille",
        "units_per_case": "",
        "stock_alert_threshold": "0",
    }]
    error_message = None

    if request.method == "POST":
        values = {name: request.form.getlist(name) for name in field_names}
        row_count = max((len(items) for items in values.values()), default=0)
        form_rows = [
            {name: values[name][index] if index < len(values[name]) else "" for name in field_names}
            for index in range(row_count)
        ]
        created_count = 0
        try:
            for row in form_rows:
                name = str(row["product_name"] or "").strip()
                if not name:
                    empty_cost = str(row["valuation_unit_cost"] or "").strip() in {"", "0", "0.0"}
                    empty_threshold = str(row["stock_alert_threshold"] or "").strip() in {"", "0", "0.0"}
                    if not row["sale_price"] and not row["units_per_case"] and empty_cost and empty_threshold:
                        continue
                    raise ValueError("INVALID_PRODUCT_NAME")
                if not str(row["sale_price"] or "").strip():
                    raise ValueError("SETUP_SALE_PRICE_REQUIRED")

                create_product(
                    current_user,
                    bar_id,
                    {
                        "category_id": row["category_id"],
                        "sku": _setup_sku(bar_id, name),
                        "name": name,
                        "base_unit": row["base_unit"] or "bouteille",
                        "sale_price": row["sale_price"],
                        "valuation_unit_cost": row["valuation_unit_cost"] or "0",
                        "units_per_case": row["units_per_case"] or None,
                        "stock_alert_threshold": row["stock_alert_threshold"] or "0",
                    },
                )
                created_count += 1

            if created_count == 0:
                raise ValueError("SETUP_REQUIRES_PRODUCT")
            bar.catalog_configured = True
            db.session.commit()
            return redirect(url_for("web.dashboard", bar_id=bar_id))
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            error_message = _setup_error_message(exc)

    return render_template(
        "catalog_setup_products.html",
        bar=bar,
        categories=categories,
        form_rows=form_rows,
        error=error_message,
    )


@catalog_bp.route("", methods=["GET", "POST"])
@login_required
def web_list(bar_id):
    permissions.require(current_user, "catalog.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_manage = permissions.evaluate(current_user, "catalog.manage", bar_id).allowed

    if request.method == "POST":
        if not can_manage:
            raise PermissionError("FORBIDDEN")

        action = request.form.get("action", "")
        created_image_key = None
        replacement_image_key = None
        old_image_key = None
        try:
            if action == "category_create":
                create_category(current_user, bar_id, request.form.get("category_name", ""))
                db.session.commit()
                flash("Catégorie créée avec succès.", "success")

            elif action in {"category_enable", "category_disable"}:
                set_category_active(
                    current_user,
                    bar_id,
                    int(request.form.get("category_id", "0")),
                    action == "category_enable",
                )
                db.session.commit()
                flash("Catégorie mise à jour.", "success")

            elif action == "product_create":
                item = create_product(
                    current_user,
                    bar_id,
                    _product_form_data(),
                    request.files.get("image"),
                )
                created_image_key = item.image_key
                initial_quantity = _decimal_or_zero(request.form.get("initial_quantity", "0"))
                if initial_quantity > 0:
                    stock_service.move(
                        current_user,
                        bar_id,
                        item.id,
                        "INITIAL",
                        initial_quantity,
                        "Stock initial lors de la création du produit",
                    )
                db.session.commit()
                flash(f"{item.name} a été ajouté au catalogue.", "success")

            elif action == "product_update":
                product_id = int(request.form.get("product_id", "0"))
                existing = Product.query.filter_by(bar_id=bar_id, id=product_id).first()
                if not existing:
                    raise LookupError("NOT_FOUND")
                old_image_key = existing.image_key
                data = _product_form_data()
                data["remove_image"] = request.form.get("remove_image") == "yes"
                item = update_product(
                    current_user,
                    bar_id,
                    product_id,
                    data,
                    request.files.get("image"),
                )
                if item.image_key != old_image_key:
                    replacement_image_key = item.image_key
                db.session.commit()
                if old_image_key and old_image_key != item.image_key:
                    delete_product_image(old_image_key)
                flash(f"{item.name} a été modifié avec succès.", "success")

            elif action in {"product_enable", "product_disable"}:
                item = update_product(
                    current_user,
                    bar_id,
                    int(request.form.get("product_id", "0")),
                    {"is_active": action == "product_enable"},
                )
                db.session.commit()
                flash(
                    f"{item.name} a été {'réactivé' if item.is_active else 'archivé'}. L'historique est conservé.",
                    "success",
                )

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, StockError) as exc:
            db.session.rollback()
            if created_image_key:
                delete_product_image(created_image_key)
            if replacement_image_key and replacement_image_key != old_image_key:
                delete_product_image(replacement_image_key)
            if str(exc) == "INITIAL_QUANTITY":
                flash("Le stock initial doit être un nombre positif ou nul.", "danger")
            else:
                flash(_message(exc), "danger")

        return redirect(url_for("catalog.web_list", bar_id=bar_id))

    category_id = request.args.get("category_id", type=int)
    active_arg = request.args.get("active")
    active = None if active_arg not in {"true", "false"} else active_arg == "true"
    products = list_all_products(
        current_user,
        bar_id,
        request.args.get("q"),
        category_id,
        active,
    )
    categories = list_categories(current_user, bar_id)
    active_categories = [category for category in categories if category.is_active]

    balances = {}
    product_ids = [product.id for product in products]
    if product_ids:
        balances = {
            balance.product_id: balance.quantity
            for balance in db.session.scalars(
                select(StockBalance).where(
                    StockBalance.bar_id == bar_id,
                    StockBalance.product_id.in_(product_ids),
                )
            )
        }

    stats = {
        "products": Product.query.filter_by(bar_id=bar_id, is_active=True).count(),
        "categories": sum(1 for category in categories if category.is_active),
        "low_stock": sum(
            1
            for product in products
            if balances.get(product.id, Decimal("0")) <= product.stock_alert_threshold
        ),
    }

    return render_template(
        "catalog.html",
        products=products,
        categories=categories,
        active_categories=active_categories,
        balances=balances,
        stats=stats,
        bar=bar,
        bar_id=bar_id,
        can_manage=can_manage,
    )


@catalog_bp.post("/async/products/delete")
@login_required
def async_delete_product(bar_id):
    """Delete an unused product or archive one referenced by operational history."""
    if not wants_async_web_response():
        return jsonify({"success": False, "message": "Requête asynchrone invalide."}), 400

    try:
        permissions.require(current_user, "catalog.read", bar_id)
        product_id = request.form.get("product_id", type=int)
        if not product_id:
            raise ValueError("INVALID_PRODUCT_ID")

        result = delete_product(current_user, bar_id, product_id)
        db.session.commit()

        if result["deleted"] and result["image_key"]:
            delete_product_image(result["image_key"])

        if result["archived"]:
            return web_json_response(
                data={
                    "id": result["id"],
                    "deleted": False,
                    "archived": True,
                    "is_active": False,
                },
                message=(
                    f"{result['name']} a été archivé, car il possède du stock "
                    "ou un historique à conserver."
                ),
            )

        return web_json_response(
            data={"id": result["id"], "deleted": True, "archived": False},
            message=f"{result['name']} a été supprimé définitivement.",
        )
    except IntegrityError:
        db.session.rollback()
        # A concurrent operation may have created a reference after the safety check.
        item = Product.query.filter_by(
            bar_id=bar_id, id=request.form.get("product_id", type=int)
        ).first()
        if item:
            item.is_active = False
            db.session.commit()
            return web_json_response(
                data={
                    "id": str(item.id),
                    "deleted": False,
                    "archived": True,
                    "is_active": False,
                },
                message=(
                    f"{item.name} a été archivé, car une opération utilise déjà "
                    "ce produit. Son historique est conservé."
                ),
            )
        return web_json_response(
            error="DELETE_CONFLICT",
            status=409,
            message="Le produit ne peut pas être supprimé.",
        )
    except (PermissionError, LookupError, ValueError, TypeError) as exc:
        db.session.rollback()
        status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError) else 422
        message = (
            "Le produit demandé est invalide."
            if str(exc) == "INVALID_PRODUCT_ID"
            else _message(exc)
        )
        return web_json_response(error=str(exc), status=status, message=message)


@catalog_bp.post("/async/products")
@login_required
def async_products(bar_id):
    """Create or update one product without a page reload; HTML/CSRF session only."""
    if not wants_async_web_response():
        return jsonify({"success": False, "message": "Requête asynchrone invalide."}), 400

    created_image_key = None
    replacement_image_key = None
    old_image_key = None
    try:
        permissions.require(current_user, "catalog.read", bar_id)
        product_id = request.form.get("product_id")
        if product_id:
            existing = Product.query.filter_by(bar_id=bar_id, id=int(product_id)).first()
            if not existing:
                raise LookupError("NOT_FOUND")
            old_image_key = existing.image_key
            data = request.form.to_dict()
            for key in ("csrf_token", "product_id", "insert_before_id"):
                data.pop(key, None)
            if "is_active" in data:
                data["is_active"] = str(data["is_active"]).strip().lower() in {"1", "true", "yes", "on"}
            if data.get("remove_image"):
                data["remove_image"] = str(data["remove_image"]).strip().lower() in {"1", "true", "yes", "on"}
            item = update_product(
                current_user, bar_id, existing.id, data, request.files.get("image")
            )
            if item.image_key != old_image_key:
                replacement_image_key = item.image_key
            db.session.commit()
            if old_image_key and old_image_key != item.image_key:
                delete_product_image(old_image_key)
            quantity = db.session.scalar(
                select(StockBalance.quantity).where(
                    StockBalance.bar_id == bar_id, StockBalance.product_id == item.id
                )
            ) or Decimal("0")
            payload = output(item)
            payload["quantity"] = str(quantity)
            return web_json_response(
                data=payload, message=f"{item.name} a été enregistré."
            )

        item = create_product(
            current_user, bar_id, _product_form_data(), request.files.get("image")
        )
        created_image_key = item.image_key
        initial_quantity = _decimal_or_zero(request.form.get("initial_quantity", "0"))
        if initial_quantity > 0:
            stock_service.move(
                current_user, bar_id, item.id, "INITIAL", initial_quantity,
                "Stock initial lors de la création du produit",
            )
        position_product(
            current_user, bar_id, item.id, request.form.get("insert_before_id") or None
        )
        db.session.commit()
        created_image_key = None
        quantity = db.session.scalar(
            select(StockBalance.quantity).where(
                StockBalance.bar_id == bar_id, StockBalance.product_id == item.id
            )
        ) or Decimal("0")
        category = db.session.get(ProductCategory, item.category_id)
        payload = output(item)
        payload["quantity"] = str(quantity)
        payload["category_name"] = category.name if category else ""
        return web_json_response(
            data=payload, message=f"{item.name} a été ajouté au catalogue.", status=201
        )
    except (PermissionError, LookupError, ValueError, TypeError, StockError, IntegrityError) as exc:
        db.session.rollback()
        if created_image_key:
            delete_product_image(created_image_key)
        if replacement_image_key and replacement_image_key != old_image_key:
            delete_product_image(replacement_image_key)
        message = (
            "Une référence identique existe déjà ou une contrainte du catalogue a été rencontrée."
            if isinstance(exc, IntegrityError) else _message(exc)
        )
        status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError) else 409 if isinstance(exc, IntegrityError) else 422
        return web_json_response(error=str(exc), status=status, message=message)


@catalog_bp.post("/async/categories")
@login_required
def async_categories(bar_id):
    """Create, rename, activate or deactivate a category without a page reload."""
    if not wants_async_web_response():
        return jsonify({"success": False, "message": "Requête asynchrone invalide."}), 400
    try:
        permissions.require(current_user, "catalog.read", bar_id)
        category_id = request.form.get("category_id")
        if category_id:
            category = ProductCategory.query.filter_by(
                bar_id=bar_id, id=int(category_id)
            ).first()
            if not category:
                raise LookupError("NOT_FOUND")
            if "name" in request.form:
                category = rename_category(
                    current_user, bar_id, category.id, request.form.get("name")
                )
            if "is_active" in request.form:
                active = str(request.form.get("is_active", "")).strip().lower() in {"1", "true", "yes", "on"}
                category = set_category_active(current_user, bar_id, category.id, active)
            db.session.commit()
            return web_json_response(
                data={"id": str(category.id), "name": category.name, "is_active": category.is_active},
                message="Catégorie mise à jour.",
            )

        category = create_category(current_user, bar_id, request.form.get("name", ""))
        db.session.commit()
        return web_json_response(
            data={"id": str(category.id), "name": category.name, "is_active": category.is_active},
            message="Catégorie créée.", status=201,
        )
    except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
        db.session.rollback()
        message = (
            "Cette catégorie existe déjà."
            if isinstance(exc, IntegrityError) else _message(exc)
        )
        status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError) else 409 if isinstance(exc, IntegrityError) else 422
        return web_json_response(error=str(exc), status=status, message=message)


@api_catalog_bp.get("")
@api_required
def api_list(bar_id):
    try:
        page = list_products(
            request.api_user,
            bar_id,
            request.args.get("q"),
            request.args.get("category_id", type=int),
            request.args.get("active", type=lambda x: x == "true"),
            request.args.get("page", 1, type=int),
        )
        return jsonify(
            {
                "success": True,
                "data": [output(x) for x in page.items],
                "meta": {"page": page.page, "pages": page.pages, "total": page.total},
            }
        )
    except PermissionError:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "Introuvable", "details": None}}), 404


@api_catalog_bp.post("")
@api_required
def api_create(bar_id):
    image = None
    try:
        item = create_product(
            request.api_user,
            bar_id,
            request.form or request.get_json(),
            request.files.get("image"),
        )
        image = item.image_key
        db.session.commit()
        return jsonify({"success": True, "data": output(item), "meta": {}}), 201
    except (PermissionError, LookupError, ValueError) as exc:
        db.session.rollback()
        if image:
            delete_product_image(image)
        return jsonify(
            {
                "success": False,
                "error": {
                    "code": "BUSINESS_RULE_VIOLATION",
                    "message": _message(exc),
                    "details": None,
                },
            }
        ), 400


@api_catalog_bp.patch("/<int:product_id>")
@api_required
def api_update(bar_id, product_id):
    existing = Product.query.filter_by(bar_id=bar_id, id=product_id).first()
    if not existing:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "Introuvable", "details": None}}), 404
    old_image = existing.image_key
    new_image = None
    try:
        data = dict(request.form or request.get_json() or {})
        if str(data.get("remove_image", "")).lower() in {"1", "true", "yes"}:
            data["remove_image"] = True
        item = update_product(
            request.api_user,
            bar_id,
            product_id,
            data,
            request.files.get("image"),
        )
        if item.image_key != old_image:
            new_image = item.image_key
        db.session.commit()
        if old_image and old_image != item.image_key:
            delete_product_image(old_image)
        return jsonify({"success": True, "data": output(item), "meta": {}})
    except (PermissionError, LookupError, ValueError) as exc:
        db.session.rollback()
        if new_image and new_image != old_image:
            delete_product_image(new_image)
        return jsonify(
            {"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": _message(exc), "details": None}}
        ), 400


@api_catalog_bp.delete("/<int:product_id>")
@api_required
def api_archive(bar_id, product_id):
    try:
        item = update_product(request.api_user, bar_id, product_id, {"is_active": False})
        db.session.commit()
        return jsonify({"success": True, "data": output(item), "meta": {}})
    except PermissionError:
        db.session.rollback()
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "Introuvable", "details": None}}), 404
    except (LookupError, ValueError) as exc:
        db.session.rollback()
        return jsonify(
            {"success": False, "error": {"code": "BUSINESS_RULE_VIOLATION", "message": _message(exc), "details": None}}
        ), 400
