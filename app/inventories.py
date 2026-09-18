from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import api_required
from app.extensions import db
from app.inventory_period_models import InventoryLineSnapshot, InventoryPeriodSnapshot
from app.inventory_services import inventory_service
from app.models import Bar, Inventory, InventoryLine, Product
from app.permissions import permissions

inventories_bp = Blueprint("inventories", __name__, url_prefix="/bars/<int:bar_id>/inventories")
api_inventories_bp = Blueprint("api_inventories", __name__, url_prefix="/api/v1/bars/<int:bar_id>/inventories")


def _message(code):
    messages = {
        "INVALID_END_DATE": "La date de fin d'inventaire est invalide ou située dans le futur.",
        "INVENTORY_PERIOD_OVERLAP": "La période chevauche le dernier inventaire validé.",
        "INVALID_INVENTORY": "Aucun produit actif n'est disponible pour cet inventaire.",
        "COUNT_REQUIRED": "Renseignez le stock physique de tous les produits avant de valider.",
        "INVALID_COUNT": "Une quantité physique est invalide.",
        "INVALID_COUNT_PRODUCT": "Un produit du comptage est invalide.",
        "INVENTORY_STALE": "Le stock machine a changé depuis le début de l'inventaire. Annulez ce brouillon et recommencez pour éviter un mauvais ajustement.",
        "INVENTORY_IMMUTABLE": "Cet inventaire est déjà terminé et ne peut plus être modifié.",
        "INVENTORY_PERIOD_DATA_MISSING": "Les données de période de cet inventaire sont incomplètes.",
        "NOTE_TOO_LONG": "Une note dépasse 500 caractères.",
        "NOT_FOUND": "Inventaire ou produit introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer cet inventaire.",
    }
    return messages.get(str(code), "Impossible d'effectuer cette opération d'inventaire.")


def _decimal(value):
    return Decimal(value or 0)


def payload(inv):
    period = db.session.scalar(
        select(InventoryPeriodSnapshot).where(
            InventoryPeriodSnapshot.bar_id == inv.bar_id,
            InventoryPeriodSnapshot.inventory_id == inv.id,
        )
    )
    rows = db.session.execute(
        select(InventoryLine, Product)
        .join(Product, (Product.id == InventoryLine.product_id) & (Product.bar_id == InventoryLine.bar_id))
        .where(InventoryLine.inventory_id == inv.id, InventoryLine.bar_id == inv.bar_id)
        .order_by(Product.name, Product.id)
    ).all()
    line_ids = [line.id for line, _ in rows]
    snapshots = {
        item.inventory_line_id: item
        for item in db.session.scalars(
            select(InventoryLineSnapshot).where(
                InventoryLineSnapshot.bar_id == inv.bar_id,
                InventoryLineSnapshot.inventory_line_id.in_(line_ids or [-1]),
            )
        )
    }

    lines = []
    complete = True
    for line, product in rows:
        snap = snapshots.get(line.id)
        counted = line.counted_quantity
        complete = complete and counted is not None
        theoretical = _decimal(snap.theoretical_quantity) if snap else _decimal(line.expected_quantity_snapshot)
        sold = _decimal(snap.theoretical_sold_quantity) if snap and counted is not None else None
        sales_amount = _decimal(snap.theoretical_sales_amount) if snap and counted is not None else None
        difference = _decimal(counted) - _decimal(line.expected_quantity_snapshot) if counted is not None else None
        lines.append(
            {
                "line_id": str(line.id),
                "product_id": str(line.product_id),
                "product_name": product.name,
                "sku": product.sku,
                "unit": product.base_unit,
                "sale_price": str(snap.sale_price_snapshot if snap else product.sale_price),
                "opening_quantity": str(snap.opening_quantity if snap else line.expected_quantity_snapshot),
                "purchase_quantity": str(snap.purchase_quantity if snap else 0),
                "theoretical_quantity": str(theoretical),
                "machine_quantity": str(line.expected_quantity_snapshot),
                "physical_quantity": str(counted) if counted is not None else None,
                "sold_quantity": str(sold) if sold is not None else None,
                "theoretical_sales_amount": str(sales_amount) if sales_amount is not None else None,
                "difference_quantity": str(difference) if difference is not None else None,
                "note": snap.note if snap else None,
            }
        )

    difference = _decimal(period.cash_difference_amount) if period else Decimal("0")
    return {
        "id": str(inv.id),
        "reference": inv.reference,
        "reason": inv.reason,
        "status": inv.status,
        "counted_at": inv.counted_at.isoformat() if inv.counted_at else None,
        "posted_at": inv.posted_at.isoformat() if inv.posted_at else None,
        "cancelled_at": inv.cancelled_at.isoformat() if inv.cancelled_at else None,
        "period_start_at": period.period_start_at.isoformat() if period and period.period_start_at else None,
        "period_end_at": period.period_end_at.isoformat() if period else None,
        "theoretical_sales_amount": str(period.theoretical_sales_amount) if period else "0",
        "expenses_amount": str(period.expenses_amount) if period else "0",
        "credit_sales_amount": str(period.credit_sales_amount) if period else "0",
        "expected_cash_amount": str(period.expected_cash_amount) if period else "0",
        "recorded_net_amount": str(period.recorded_net_amount) if period else "0",
        "cash_difference_amount": str(difference),
        "cash_shortage_amount": str(max(-difference, Decimal("0"))),
        "cash_surplus_amount": str(max(difference, Decimal("0"))),
        "complete": bool(lines) and complete,
        "lines": lines,
    }


def body():
    value = request.get_json()
    if not isinstance(value, dict):
        raise ValueError("INVALID_REQUEST")
    return value


def result(inv, status=200):
    db.session.commit()
    return jsonify(success=True, data=payload(inv), meta={}), status


@inventories_bp.get("")
@login_required
def listing(bar_id):
    permissions.require(current_user, "inventory.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    rows = Inventory.query.filter_by(bar_id=bar_id).order_by(Inventory.created_at.desc(), Inventory.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    period_by_inventory = {
        item.inventory_id: item
        for item in db.session.scalars(
            select(InventoryPeriodSnapshot).where(
                InventoryPeriodSnapshot.bar_id == bar_id,
                InventoryPeriodSnapshot.inventory_id.in_([row.id for row in rows.items] or [-1]),
            )
        )
    }
    return render_template(
        "inventories.html",
        rows=rows,
        bar=bar,
        bar_id=bar_id,
        period_by_inventory=period_by_inventory,
        can_adjust=permissions.evaluate(current_user, "inventory.adjust", bar_id).allowed,
    )


@inventories_bp.route("/new", methods=["GET", "POST"])
@login_required
def new(bar_id):
    permissions.require(current_user, "inventory.adjust", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    local_now = datetime.now(ZoneInfo(bar.timezone))
    defaults = {
        "period_end": local_now.date().isoformat(),
        "reference": f"INV-{local_now:%Y%m%d-%H%M}",
        "reason": "Inventaire physique",
    }
    product_count = db.session.scalar(
        select(db.func.count(Product.id)).where(Product.bar_id == bar_id, Product.is_active.is_(True))
    ) or 0
    previous = db.session.scalar(
        select(Inventory)
        .where(Inventory.bar_id == bar_id, Inventory.status == "POSTED")
        .order_by(Inventory.posted_at.desc(), Inventory.id.desc())
        .limit(1)
    )

    if request.method == "POST":
        defaults.update(
            period_end=request.form.get("period_end", "").strip(),
            reference=request.form.get("reference", "").strip(),
            reason=request.form.get("reason", "").strip(),
        )
        try:
            inv = inventory_service.create(
                current_user,
                bar_id,
                defaults["reference"],
                None,
                defaults["reason"],
                defaults["period_end"],
            )
            db.session.commit()
            flash("Inventaire créé. Comptez maintenant les bouteilles réellement présentes.", "success")
            return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            flash("Référence déjà utilisée." if isinstance(exc, IntegrityError) else _message(exc), "danger")

    return render_template(
        "inventory_new.html",
        bar=bar,
        bar_id=bar_id,
        defaults=defaults,
        product_count=product_count,
        previous=previous,
    )


@inventories_bp.route("/<int:inventory_id>", methods=["GET", "POST"])
@login_required
def detail(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    bar = db.session.get(Bar, bar_id)
    can_adjust = permissions.evaluate(current_user, "inventory.adjust", bar_id).allowed

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action in {"counts", "post"}:
                quantities = {
                    key[9:]: value
                    for key, value in request.form.items()
                    if key.startswith("quantity_") and value.strip()
                }
                notes = {
                    key[5:]: value
                    for key, value in request.form.items()
                    if key.startswith("note_")
                }
                if quantities:
                    inventory_service.count(current_user, bar_id, inv.id, quantities, notes)
                if action == "post":
                    inventory_service.post(current_user, bar_id, inv.id)
                    flash("Inventaire validé. Le stock physique devient la nouvelle référence.", "success")
                else:
                    flash("Comptage enregistré.", "success")
            elif action == "cancel":
                inventory_service.cancel(current_user, bar_id, inv.id)
                flash("Brouillon d'inventaire annulé.", "success")
            else:
                raise ValueError("INVALID_ACTION")
            db.session.commit()
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")
        return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))

    return render_template(
        "inventory_detail.html",
        inventory=payload(inv),
        bar=bar,
        bar_id=bar_id,
        can_adjust=can_adjust,
    )


@inventories_bp.get("/<int:inventory_id>/print")
@login_required
def printable(bar_id, inventory_id):
    bar = db.session.get(Bar, bar_id)
    return render_template(
        "inventory_print.html",
        inventory=payload(inventory_service.get(current_user, bar_id, inventory_id)),
        bar=bar,
    )


@api_inventories_bp.get("")
@api_required
def api_list(bar_id):
    permissions.require(request.api_user, "inventory.read", bar_id)
    rows = Inventory.query.filter_by(bar_id=bar_id).order_by(Inventory.created_at.desc(), Inventory.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    return jsonify(success=True, data=[payload(inv) for inv in rows.items], meta={"page": rows.page, "pages": rows.pages, "total": rows.total})


@api_inventories_bp.get("/<int:inventory_id>")
@api_required
def api_detail(bar_id, inventory_id):
    return jsonify(success=True, data=payload(inventory_service.get(request.api_user, bar_id, inventory_id)), meta={})


@api_inventories_bp.post("")
@api_required
def create(bar_id):
    value = body()
    return result(
        inventory_service.create(
            request.api_user,
            bar_id,
            value.get("reference"),
            value.get("product_ids"),
            value.get("reason", "Inventaire physique"),
            value.get("period_end"),
        ),
        201,
    )


@api_inventories_bp.patch("/<int:inventory_id>/counts")
@api_required
def counts(bar_id, inventory_id):
    value = body()
    return result(
        inventory_service.count(
            request.api_user,
            bar_id,
            inventory_id,
            value.get("quantities"),
            value.get("notes"),
        )
    )


@api_inventories_bp.post("/<int:inventory_id>/post")
@api_required
def post(bar_id, inventory_id):
    try:
        return result(inventory_service.post(request.api_user, bar_id, inventory_id))
    except ValueError as exc:
        db.session.rollback()
        return jsonify(success=False, error={"code": str(exc), "message": _message(exc), "details": None}), 409


@api_inventories_bp.post("/<int:inventory_id>/cancel")
@api_required
def cancel(bar_id, inventory_id):
    return result(inventory_service.cancel(request.api_user, bar_id, inventory_id))
