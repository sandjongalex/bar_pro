"""Server-rendered supplier and purchasing desk for one bar."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.models import Bar, CashSession, Product, Purchase, PurchaseLine, Supplier, SupplierPayment
from app.permissions import permissions
from app.purchase_defaults import default_purchase_price
from app.purchase_services import purchase_service, supplier_service

bp = Blueprint("purchases_web", __name__, url_prefix="/bars/<int:bar_id>/purchases")

STATUS_LABELS = {
    "DRAFT": "Brouillon",
    "POSTED": "Reçu",
    "CANCELLED": "Annulé",
}
PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement bancaire",
}


def _reference(prefix: str = "ACH") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _optional_int(value):
    value = str(value or "").strip()
    return int(value) if value else None


def _local_today(bar):
    try:
        return datetime.now(ZoneInfo(bar.timezone)).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def _decimal(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError):
        raise ValueError("INVALID_NUMBER") from None


def _message(code) -> str:
    messages = {
        "PURCHASE_EMPTY": "Saisissez une quantité sur au moins un produit.",
        "PURCHASE_NOT_DRAFT": "Seuls les achats en brouillon peuvent être modifiés.",
        "PURCHASE_NOT_POSTED": "Cette opération nécessite un achat déjà réceptionné.",
        "PURCHASE_HAS_PAYMENTS": "Cet achat possède déjà un règlement. Annulez d'abord ses paiements avant de corriger ou supprimer la réception.",
        "UNITS_PER_CASE_REQUIRED": "Renseignez le nombre de bouteilles par casier pour chaque produit acheté en casier.",
        "INVALID_PURCHASE_UNIT": "L'unité d'achat doit être Casier ou Bouteille.",
        "INVALID_PURCHASE_DATE": "La date d'achat est invalide.",
        "PAYMENT_REQUIRES_RECEIPT": "Un paiement initial ne peut être enregistré que si l'achat est réceptionné.",
        "SUPPLIER_PAYMENT_LIMIT": "Le montant payé dépasse le total restant dû sur cet achat.",
        "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
        "CASH_LOCATION_REQUIRED": "Pour un paiement en espèces, sélectionnez une session de caisse ouverte.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse sélectionnée n'est plus ouverte.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas suffisamment d'espèces pour ce règlement.",
        "PROVIDER_REFERENCE_REQUIRED": "Renseignez à la fois le prestataire et la référence de transaction, ou laissez les deux champs vides.",
        "INSUFFICIENT_STOCK": "Impossible d'annuler cette réception : une partie du stock acheté a déjà été vendue ou sortie.",
        "NOT_FOUND": "Le fournisseur, le produit ou l'achat est introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer les achats de cet établissement.",
        "BAR_SUSPENDED": "Le bar est suspendu : les achats sont bloqués.",
        "SUPPLIER_NAME_REQUIRED": "Le nom du fournisseur est obligatoire.",
    }
    return messages.get(str(code), "Opération impossible. Vérifiez les informations saisies.")


def _lines_from_form():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("purchase_quantity") or request.form.getlist("quantity")
    prices = request.form.getlist("purchase_unit_price") or request.form.getlist("unit_cost")
    units = request.form.getlist("purchase_unit")
    case_sizes = request.form.getlist("units_per_case")

    lines = []
    for index, product_id in enumerate(product_ids):
        product_id = (product_id or "").strip()
        quantity = (quantities[index] if index < len(quantities) else "").strip()
        price = (prices[index] if index < len(prices) else "").strip()
        unit = (units[index] if index < len(units) else "BOTTLE").strip().upper() or "BOTTLE"
        case_size = (case_sizes[index] if index < len(case_sizes) else "").strip()
        if not product_id or not quantity:
            continue
        if _decimal(quantity) <= 0:
            continue
        lines.append(
            {
                "product_id": int(product_id),
                "purchase_quantity": quantity,
                "purchase_unit": unit,
                "purchase_unit_price": price or "0",
                "units_per_case": case_size or None,
            }
        )
    return lines


def _supplier_payload():
    return {
        "name": request.form.get("supplier_name", "").strip(),
        "phone": request.form.get("supplier_phone", "").strip(),
        "email": request.form.get("supplier_email", "").strip(),
        "address": request.form.get("supplier_address", "").strip(),
        "note": request.form.get("supplier_note", "").strip(),
    }


def _record_initial_payment(bar_id, purchase):
    amount = _decimal(request.form.get("amount_paid", "0"))
    if amount <= 0:
        return None
    method = request.form.get("payment_method", "").strip()
    cash_session_id = _optional_int(request.form.get("cash_session_id")) if method == "CASH" else None
    provider_code = request.form.get("provider_code", "").strip() or None
    provider_transaction_id = request.form.get("provider_transaction_id", "").strip() or None
    return purchase_service.pay(
        current_user,
        bar_id,
        purchase.id,
        request.form.get("payment_reference", "").strip() or _reference("FOU"),
        amount,
        method,
        request.form.get("payment_note", "").strip() or f"Paiement initial achat {purchase.reference}",
        cash_session_id,
        provider_code,
        provider_transaction_id,
    )


def _purchase_form_data():
    return {
        "purchase_date": request.form.get("purchase_date", "").strip(),
        "notes": request.form.get("notes", "").strip() or None,
        "supplier_invoice_reference": request.form.get("supplier_invoice_reference", "").strip() or None,
        "lines": _lines_from_form(),
    }


@bp.get("/product-meta")
@login_required
def product_meta(bar_id):
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
                        default_purchase_price(product.name, bar.currency, product.valuation_unit_cost)
                    ),
                }
                for product in products
            ],
        }
    )


@bp.route("/suppliers", methods=["GET", "POST"])
@login_required
def suppliers(bar_id):
    """Supplier directory, account position and purchase history."""
    permissions.require(current_user, "purchases.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    can_manage = permissions.evaluate(current_user, "suppliers.manage", bar_id).allowed

    if request.method == "POST":
        if not can_manage:
            raise PermissionError("FORBIDDEN")
        action = request.form.get("action", "")
        try:
            if action == "supplier_create":
                data = _supplier_payload()
                if not data["name"]:
                    raise ValueError("SUPPLIER_NAME_REQUIRED")
                supplier_service.create(current_user, bar_id, data)
                db.session.commit()
                flash("Fournisseur créé avec succès.", "success")
            elif action == "supplier_update":
                data = _supplier_payload()
                if not data["name"]:
                    raise ValueError("SUPPLIER_NAME_REQUIRED")
                supplier_service.update(
                    current_user,
                    bar_id,
                    int(request.form.get("supplier_id", "0")),
                    data,
                )
                db.session.commit()
                flash("Informations du fournisseur mises à jour.", "success")
            elif action in {"supplier_enable", "supplier_disable"}:
                supplier_service.update(
                    current_user,
                    bar_id,
                    int(request.form.get("supplier_id", "0")),
                    {"is_active": action == "supplier_enable"},
                )
                db.session.commit()
                flash("État du fournisseur mis à jour.", "success")
            else:
                raise ValueError("INVALID_ACTION")
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")
        return redirect(url_for("purchases_web.suppliers", bar_id=bar_id, supplier_id=request.form.get("supplier_id", "")))

    supplier_rows = list(
        db.session.scalars(
            select(Supplier).where(Supplier.bar_id == bar_id).order_by(Supplier.is_active.desc(), Supplier.name, Supplier.id)
        )
    )
    purchases = list(
        db.session.scalars(
            select(Purchase)
            .where(Purchase.bar_id == bar_id)
            .order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        )
    )
    payments = list(db.session.scalars(select(SupplierPayment).where(SupplierPayment.bar_id == bar_id)))
    signed_payment = {}
    for payment in payments:
        amount = Decimal(payment.amount)
        signed_payment[payment.purchase_id] = signed_payment.get(payment.purchase_id, Decimal("0")) + (
            amount if payment.entry_kind == "PAYMENT" else -amount
        )

    summaries = {}
    histories = {supplier.id: [] for supplier in supplier_rows}
    for supplier in supplier_rows:
        summaries[supplier.id] = {
            "supplier": supplier,
            "purchases": 0,
            "total": Decimal("0"),
            "paid": Decimal("0"),
            "debt": Decimal("0"),
            "credit": Decimal("0"),
        }

    for purchase in purchases:
        histories.setdefault(purchase.supplier_id, []).append(purchase)
        row = summaries.get(purchase.supplier_id)
        if not row:
            continue
        row["purchases"] += 1
        if purchase.status != "POSTED":
            continue
        paid = Decimal(signed_payment.get(purchase.id, 0))
        row["total"] += Decimal(purchase.total_amount)
        row["paid"] += paid

    for row in summaries.values():
        balance = row["total"] - row["paid"]
        row["debt"] = max(balance, Decimal("0"))
        row["credit"] = max(-balance, Decimal("0"))

    selected_id = request.args.get("supplier_id", type=int)
    selected_supplier = next((s for s in supplier_rows if s.id == selected_id), None)
    if not selected_supplier and supplier_rows:
        selected_supplier = supplier_rows[0]
    selected_history = histories.get(selected_supplier.id, [])[:100] if selected_supplier else []
    selected_summary = summaries.get(selected_supplier.id) if selected_supplier else None

    stats = {
        "suppliers": len(supplier_rows),
        "active": sum(1 for supplier in supplier_rows if supplier.is_active),
        "total": sum((row["total"] for row in summaries.values()), Decimal("0")),
        "paid": sum((row["paid"] for row in summaries.values()), Decimal("0")),
        "debt": sum((row["debt"] for row in summaries.values()), Decimal("0")),
        "credit": sum((row["credit"] for row in summaries.values()), Decimal("0")),
    }
    return render_template(
        "suppliers.html",
        bar=bar,
        suppliers=supplier_rows,
        summaries=summaries,
        selected_supplier=selected_supplier,
        selected_summary=selected_summary,
        selected_history=selected_history,
        stats=stats,
        status_labels=STATUS_LABELS,
        can_manage=can_manage,
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
                data = _supplier_payload()
                if not data["name"]:
                    raise ValueError("SUPPLIER_NAME_REQUIRED")
                supplier_service.create(current_user, bar_id, data)
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
                submit_mode = request.form.get("submit_mode", "draft")
                amount_paid = _decimal(request.form.get("amount_paid", "0"))
                if amount_paid > 0 and submit_mode != "receive":
                    raise ValueError("PAYMENT_REQUIRES_RECEIPT")
                data = _purchase_form_data()
                purchase = purchase_service.create(
                    current_user,
                    bar_id,
                    int(request.form.get("supplier_id", "0")),
                    request.form.get("reference", "").strip() or _reference(),
                    data["lines"],
                    data["supplier_invoice_reference"],
                    data["purchase_date"],
                    data["notes"],
                )
                if submit_mode == "receive":
                    purchase_service.receive(current_user, bar_id, purchase.id)
                    _record_initial_payment(bar_id, purchase)
                db.session.commit()
                flash(
                    f"Achat {purchase.reference} {'réceptionné et ajouté au stock' if purchase.status == 'POSTED' else 'enregistré en brouillon'}.",
                    "success",
                )

            elif action == "purchase_update":
                if not can_manage:
                    raise PermissionError("FORBIDDEN")
                purchase_id = int(request.form.get("purchase_id", "0"))
                purchase = purchase_service.get(current_user, bar_id, purchase_id)
                submit_mode = request.form.get("submit_mode", "draft")
                amount_paid = _decimal(request.form.get("amount_paid", "0"))
                if amount_paid > 0 and submit_mode != "receive":
                    raise ValueError("PAYMENT_REQUIRES_RECEIPT")
                requested_supplier_id = int(request.form.get("supplier_id", "0"))
                data = _purchase_form_data()
                data["reference"] = request.form.get("reference", "").strip() or purchase.reference
                if requested_supplier_id != purchase.supplier_id:
                    data["supplier_id"] = requested_supplier_id
                purchase = purchase_service.update(current_user, bar_id, purchase_id, data)
                if submit_mode == "receive":
                    purchase_service.receive(current_user, bar_id, purchase.id)
                    _record_initial_payment(bar_id, purchase)
                db.session.commit()
                flash(
                    f"Achat {purchase.reference} {'corrigé puis réceptionné' if purchase.status == 'POSTED' else 'mis à jour en brouillon'}.",
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
                flash(f"Réception {purchase.reference} confirmée. Le stock a été mis à jour.", "success")

            elif action == "purchase_reopen":
                if not can_manage:
                    raise PermissionError("FORBIDDEN")
                purchase = purchase_service.reopen(
                    current_user,
                    bar_id,
                    int(request.form.get("purchase_id", "0")),
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash(f"Réception {purchase.reference} annulée. L'achat est de nouveau modifiable en brouillon.", "success")

            elif action == "purchase_cancel_received":
                if not can_manage:
                    raise PermissionError("FORBIDDEN")
                purchase_service.cancel_received(
                    current_user,
                    bar_id,
                    int(request.form.get("purchase_id", "0")),
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash("Achat réceptionné annulé et stock corrigé.", "success")

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
                flash("Brouillon annulé.", "success")

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence existe déjà ou les données saisies sont incompatibles.", "danger")
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
            base_unit=product.base_unit,
            units_per_case=product.units_per_case,
            sale_price=product.sale_price,
            valuation_unit_cost=default_purchase_price(product.name, bar.currency, product.valuation_unit_cost),
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
    line_lookup = {purchase_id: {} for purchase_id in purchase_ids}
    if purchase_ids:
        for line in db.session.scalars(
            select(PurchaseLine)
            .where(PurchaseLine.bar_id == bar_id, PurchaseLine.purchase_id.in_(purchase_ids))
            .order_by(PurchaseLine.purchase_id, PurchaseLine.line_no)
        ):
            lines_by_purchase.setdefault(line.purchase_id, []).append(line)
            line_lookup.setdefault(line.purchase_id, {})[line.product_id] = line

    due_by_purchase = {
        purchase.id: purchase_service.due(bar_id, purchase.id)
        for purchase in purchases
        if purchase.status == "POSTED"
    }
    paid_by_purchase = {
        purchase.id: purchase_service.net_paid(bar_id, purchase.id)
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
        db.session.scalars(select(Purchase.id).where(Purchase.bar_id == bar_id, Purchase.status == "POSTED"))
    )
    total_due = sum((max(purchase_service.due(bar_id, pid), Decimal("0")) for pid in all_posted_ids), Decimal("0"))

    open_cash_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )
    cash_expected = {session.id: cash_service.expected(session) for session in open_cash_sessions}

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
        line_lookup=line_lookup,
        due_by_purchase=due_by_purchase,
        paid_by_purchase=paid_by_purchase,
        stats=stats,
        status_labels=STATUS_LABELS,
        payment_labels=PAYMENT_LABELS,
        open_cash_sessions=open_cash_sessions,
        cash_expected=cash_expected,
        today=_local_today(bar),
        can_manage=can_manage,
        can_manage_suppliers=can_manage_suppliers,
        page=page,
        has_more=has_more,
    )
