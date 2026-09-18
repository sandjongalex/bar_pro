"""Server-rendered supplier and purchasing desk for one bar."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets
from types import SimpleNamespace

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Bar, Product, Purchase, PurchaseLine, Supplier
from app.permissions import permissions
from app.purchase_defaults import default_purchase_price
from app.purchase_services import purchase_service, supplier_service

bp = Blueprint("purchases_web", __name__, url_prefix="/bars/<int:bar_id>/purchases")

STATUS_LABELS = {
    "DRAFT": "Brouillon",
    "POSTED": "Reçu",
    "CANCELLED": "Annulé",
}


def _reference() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"ACH-{stamp}-{secrets.token_hex(2).upper()}"


def _message(code) -> str:
    messages = {
        "PURCHASE_EMPTY": "Ajoutez au moins un produit avec une quantité supérieure à zéro.",
        "PURCHASE_NOT_DRAFT": "Cet achat n'est plus modifiable.",
        "NOT_FOUND": "Le fournisseur, le produit ou l'achat est introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer les achats de cet établissement.",
        "BAR_SUSPENDED": "Le bar est suspendu : les achats sont bloqués.",
        "SUPPLIER_NAME_REQUIRED": "Le nom du fournisseur est obligatoire.",
    }
    return messages.get(str(code), "Opération impossible. Vérifiez les informations saisies.")


def _lines_from_form():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    costs = request.form.getlist("unit_cost")
    lines = []
    for product_id, quantity, unit_cost in zip(product_ids, quantities, costs):
        product_id = (product_id or "").strip()
        quantity = (quantity or "").strip()
        unit_cost = (unit_cost or "").strip()
        if not product_id and not quantity and not unit_cost:
            continue
        if not product_id or not quantity:
            continue
        lines.append(
            {
                "product_id": int(product_id),
                "quantity": quantity,
                "unit_cost": unit_cost or "0",
            }
        )
    return lines


@bp.get("/product-meta")
@login_required
def product_meta(bar_id):
    """Small read-only payload used by the purchase calculator in the modal."""
    permissions.require(current_user, "purchases.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    products = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar_id, Product.is_active.is_(True))
            .order_by(Product.name, Product.id)
        )
    )
    return jsonify(
        {
            "currency": bar.currency,
            "products": [
                {
                    "id": product.id,
                    "name": product.name,
                    "sale_price": str(product.sale_price),
                    "units_per_case": product.units_per_case,
                    "base_unit": product.base_unit,
                    "default_purchase_price": str(
                        default_purchase_price(
                            product.name,
                            bar.currency,
                            product.valuation_unit_cost,
                        )
                    ),
                }
                for product in products
            ],
        }
    )


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id):
    permissions.require(current_user, "purchases.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_manage = permissions.evaluate(current_user, "purchases.manage", bar_id).allowed
    can_manage_suppliers = permissions.evaluate(current_user, "suppliers.manage", bar_id).allowed

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "supplier_create":
                if not can_manage_suppliers:
                    raise PermissionError("FORBIDDEN")
                name = request.form.get("supplier_name", "").strip()
                if not name:
                    raise ValueError("SUPPLIER_NAME_REQUIRED")
                supplier_service.create(
                    current_user,
                    bar_id,
                    {
                        "name": name,
                        "phone": request.form.get("supplier_phone", "").strip(),
                        "email": request.form.get("supplier_email", "").strip(),
                        "address": request.form.get("supplier_address", "").strip(),
                    },
                )
                db.session.commit()
                flash("Fournisseur créé avec succès.", "success")

            elif action in {"supplier_enable", "supplier_disable"}:
                if not can_manage_suppliers:
                    raise PermissionError("FORBIDDEN")
                supplier_service.update(
                    current_user,
                    bar_id,
                    int(request.form.get("supplier_id", "0")),
                    {"is_active": action == "supplier_enable"},
                )
                db.session.commit()
                flash("Fournisseur mis à jour.", "success")

            elif action == "purchase_create":
                if not can_manage:
                    raise PermissionError("FORBIDDEN")
                reference = request.form.get("reference", "").strip() or _reference()
                purchase = purchase_service.create(
                    current_user,
                    bar_id,
                    int(request.form.get("supplier_id", "0")),
                    reference,
                    _lines_from_form(),
                    request.form.get("supplier_invoice_reference", "").strip() or None,
                )
                db.session.commit()
                flash(
                    f"Achat {purchase.reference} enregistré en brouillon. Vérifiez-le puis confirmez la réception.",
                    "success",
                )

            elif action == "purchase_receive":
                if not can_manage:
                    raise PermissionError("FORBIDDEN")
                purchase = purchase_service.receive(
                    current_user,
                    bar_id,
                    int(request.form.get("purchase_id", "0")),
                )
                db.session.commit()
                flash(
                    f"Réception {purchase.reference} confirmée. Les quantités ont été ajoutées au stock.",
                    "success",
                )

            elif action == "purchase_cancel":
                if not can_manage:
                    raise PermissionError("FORBIDDEN")
                purchase_service.cancel(
                    current_user,
                    bar_id,
                    int(request.form.get("purchase_id", "0")),
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash("Achat annulé.", "success")

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence d'achat existe déjà. Utilisez une autre référence.", "danger")
            else:
                flash(_message(exc), "danger")

        return redirect(url_for("purchases_web.manage", bar_id=bar_id))

    suppliers = list(
        db.session.scalars(
            select(Supplier)
            .where(Supplier.bar_id == bar_id)
            .order_by(Supplier.is_active.desc(), Supplier.name, Supplier.id)
        )
    )
    active_suppliers = [supplier for supplier in suppliers if supplier.is_active]
    product_rows = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar_id, Product.is_active.is_(True))
            .order_by(Product.name, Product.id)
        )
    )
    products = [
        SimpleNamespace(
            id=product.id,
            name=product.name,
            sku=product.sku,
            valuation_unit_cost=default_purchase_price(
                product.name,
                bar.currency,
                product.valuation_unit_cost,
            ),
        )
        for product in product_rows
    ]

    status = request.args.get("status", "").strip()
    supplier_id = request.args.get("supplier_id", type=int)
    rows, page, page_size = purchase_service.list(
        current_user,
        bar_id,
        status if status in STATUS_LABELS else None,
        supplier_id,
        request.args.get("page", 1, type=int),
        20,
    )
    purchases = rows[:page_size]

    purchase_ids = [purchase.id for purchase in purchases]
    lines_by_purchase = {purchase_id: [] for purchase_id in purchase_ids}
    if purchase_ids:
        for line in db.session.scalars(
            select(PurchaseLine)
            .where(PurchaseLine.bar_id == bar_id, PurchaseLine.purchase_id.in_(purchase_ids))
            .order_by(PurchaseLine.purchase_id, PurchaseLine.line_no)
        ):
            lines_by_purchase.setdefault(line.purchase_id, []).append(line)

    due_by_purchase = {
        purchase.id: purchase_service.due(bar_id, purchase.id)
        for purchase in purchases
        if purchase.status == "POSTED"
    }

    counts = dict(
        db.session.execute(
            select(Purchase.status, func.count(Purchase.id))
            .where(Purchase.bar_id == bar_id)
            .group_by(Purchase.status)
        ).all()
    )
    all_posted_ids = list(
        db.session.scalars(
            select(Purchase.id).where(Purchase.bar_id == bar_id, Purchase.status == "POSTED")
        )
    )
    total_due = sum(
        (purchase_service.due(bar_id, purchase_id) for purchase_id in all_posted_ids),
        Decimal("0"),
    )

    stats = {
        "suppliers": len(active_suppliers),
        "draft": counts.get("DRAFT", 0),
        "posted": counts.get("POSTED", 0),
        "due": total_due,
    }

    has_more = len(rows) > page_size
    return render_template(
        "purchases.html",
        bar=bar,
        suppliers=suppliers,
        active_suppliers=active_suppliers,
        products=products,
        purchases=purchases,
        lines_by_purchase=lines_by_purchase,
        due_by_purchase=due_by_purchase,
        stats=stats,
        status_labels=STATUS_LABELS,
        can_manage=can_manage,
        can_manage_suppliers=can_manage_suppliers,
        page=page,
        has_more=has_more,
    )