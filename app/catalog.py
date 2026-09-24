from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.auth import api_required
from app.catalog_services import (
    create_category,
    create_product,
    list_categories,
    list_products,
    set_category_active,
    update_product,
)
from app.extensions import db
from app.models import Bar, Product, StockBalance
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
    products = list_products(
        current_user,
        bar_id,
        request.args.get("q"),
        category_id,
        active,
        request.args.get("page", 1, type=int),
    )
    categories = list_categories(current_user, bar_id)
    active_categories = [category for category in categories if category.is_active]

    balances = {}
    product_ids = [product.id for product in products.items]
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
            for product in products.items
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
