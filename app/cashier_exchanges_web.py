"""Cashier-facing beer/product exchanges, independent from invoices."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.exchange_services import exchange_service
from app.extensions import db
from app.models import AuditLog, Bar, Product, StockBalance
from app.permissions import permissions

bp = Blueprint("cashier_exchanges_web", __name__, url_prefix="/bars/<int:bar_id>/cashier-exchanges")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}

DIRECTION_LABELS = {
    "COLLECT": "À encaisser",
    "REFUND": "À rembourser",
    "NONE": "Sans différence",
}


def _message(exc) -> str:
    code = str(exc)
    messages = {
        "NOT_FOUND": "Produit introuvable ou indisponible.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à enregistrer cet échange.",
        "SAME_PRODUCT": "Choisissez deux boissons différentes.",
        "INVALID_DISPOSITION": "Choisissez si la boisson rendue revient au stock ou devient une perte.",
        "INVALID_METHOD": "Choisissez un mode de règlement valide pour la différence de prix.",
        "INVALID_CASH_PROVIDER": "Ne saisissez pas de référence de transaction pour un règlement en espèces.",
        "CASH_SESSION_REQUIRED": "Une session de caisse ouverte est obligatoire pour encaisser ou rembourser en espèces.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse n'est plus ouverte.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas assez d'espèces pour rembourser cette différence.",
        "INSUFFICIENT_STOCK": "Le stock de la boisson de remplacement est insuffisant. Aucun mouvement n'a été conservé.",
        "POSITIVE_NUMBER_REQUIRED": "Les quantités doivent être strictement supérieures à zéro.",
        "INVALID_NUMBER": "Une quantité ou un montant est invalide.",
        "INVALID_PRECISION": "La précision d'une quantité est invalide.",
        "INVALID_TEXT": "Le motif est obligatoire et doit rester court.",
    }
    return messages.get(code, "Échange refusé. Vérifiez les produits, les quantités, le stock et le règlement.")


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _history(bar: Bar):
    logs = list(
        db.session.scalars(
            select(AuditLog)
            .where(
                AuditLog.bar_id == bar.id,
                AuditLog.action == "exchanges.record",
                AuditLog.outcome == "SUCCESS",
            )
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(60)
        )
    )
    items = []
    for log in logs:
        data = dict(log.changes or {})
        aware = log.occurred_at
        if aware is not None:
            if aware.tzinfo is None:
                aware = aware.replace(tzinfo=timezone.utc)
            display_time = aware.astimezone(ZoneInfo(bar.timezone)).strftime("%d/%m/%Y %H:%M")
        else:
            display_time = "—"
        data.update(
            {
                "date": display_time,
                "reason": log.reason,
                "settlement_amount_value": _decimal(data.get("settlement_amount")),
            }
        )
        items.append(data)
    return items


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    # This workflow deliberately has no Order dependency: the cashier can
    # exchange a bottle at the counter even when the original invoice is unknown.
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_exchange = permissions.evaluate(current_user, "payments.record", bar_id).allowed

    if request.method == "POST":
        if not can_exchange:
            raise PermissionError("FORBIDDEN")
        try:
            returned_product_id = request.form.get("returned_product_id", type=int)
            replacement_product_id = request.form.get("replacement_product_id", type=int)
            if not returned_product_id or not replacement_product_id:
                raise LookupError("NOT_FOUND")

            result = exchange_service.create(
                current_user,
                bar_id,
                returned_product_id=returned_product_id,
                replacement_product_id=replacement_product_id,
                returned_quantity=request.form.get("returned_quantity", "1"),
                replacement_quantity=request.form.get("replacement_quantity", "1"),
                returned_disposition=request.form.get("returned_disposition", "RESTOCK"),
                reason=request.form.get("reason", "").strip(),
                settlement_method=request.form.get("settlement_method"),
                provider_transaction_id=request.form.get("provider_transaction_id"),
            )
            db.session.commit()

            if result.settlement_direction == "COLLECT":
                settlement = f" Supplément encaissé : {result.settlement_amount:,.0f} {bar.currency}."
            elif result.settlement_direction == "REFUND":
                settlement = f" Différence remboursée : {result.settlement_amount:,.0f} {bar.currency}."
            else:
                settlement = " Aucun supplément à régler."
            flash(
                f"Échange {result.reference} enregistré : {result.returned_product_name} → "
                f"{result.replacement_product_name}.{settlement}",
                "success",
            )
            return redirect(url_for("cashier_exchanges_web.manage", bar_id=bar_id))
        except (PermissionError, LookupError, ValueError, TypeError, ArithmeticError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")

    products = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar_id, Product.is_active.is_(True))
            .order_by(Product.name, Product.id)
        )
    )
    stock_balances = dict(
        db.session.execute(
            select(StockBalance.product_id, StockBalance.quantity).where(StockBalance.bar_id == bar_id)
        ).all()
    )

    return render_template(
        "cashier_exchanges.html",
        bar=bar,
        products=products,
        stock_balances=stock_balances,
        exchanges=_history(bar),
        payment_labels=PAYMENT_LABELS,
        direction_labels=DIRECTION_LABELS,
        can_exchange=can_exchange,
    )
