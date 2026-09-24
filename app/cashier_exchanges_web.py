"""Cashier-facing beer/product exchanges and refunds, independent from invoices."""
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
from app.standalone_refund_service import standalone_refund_service

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
        "FORBIDDEN": "Vous n'êtes pas autorisé à effectuer cette opération.",
        "SAME_PRODUCT": "Choisissez deux boissons différentes.",
        "INVALID_DISPOSITION": "Choisissez si la boisson rendue revient au stock ou devient une perte.",
        "INVALID_METHOD": "Choisissez un mode de règlement valide.",
        "INVALID_CASH_PROVIDER": "Ne saisissez pas de référence de transaction pour un règlement en espèces.",
        "INVALID_REFUND_AMOUNT": "Le montant à rembourser doit être supérieur à zéro.",
        "CASH_SESSION_REQUIRED": "Une session de caisse ouverte est obligatoire pour encaisser ou rembourser en espèces.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse n'est plus ouverte.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas assez d'espèces pour effectuer ce remboursement.",
        "INSUFFICIENT_STOCK": "Le stock de la boisson de remplacement est insuffisant. Aucun mouvement n'a été conservé.",
        "POSITIVE_NUMBER_REQUIRED": "Les quantités doivent être strictement supérieures à zéro.",
        "INVALID_NUMBER": "Une quantité ou un montant est invalide.",
        "INVALID_PRECISION": "La précision d'une quantité est invalide.",
        "INVALID_TEXT": "Le motif est obligatoire et doit rester court.",
    }
    return messages.get(code, "Opération refusée. Vérifiez les produits, les quantités, le stock et le règlement.")


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
                AuditLog.action.in_(["exchanges.record", "exchanges.refund"]),
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
                "operation_type": data.get("operation_type") or ("REFUND" if log.action == "exchanges.refund" else "EXCHANGE"),
                "settlement_amount_value": _decimal(data.get("settlement_amount")),
            }
        )
        items.append(data)
    return items


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    # These workflows deliberately have no Order dependency: the cashier can
    # exchange or refund a returned bottle even when the original invoice is unknown.
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_exchange = permissions.evaluate(current_user, "payments.record", bar_id).allowed
    can_refund = permissions.evaluate(current_user, "refunds.record", bar_id).allowed

    if request.method == "POST":
        action = (request.form.get("action") or "exchange").strip().lower()
        try:
            if action == "refund":
                if not can_refund:
                    raise PermissionError("FORBIDDEN")
                product_id = request.form.get("refund_product_id", type=int)
                if not product_id:
                    raise LookupError("NOT_FOUND")
                result = standalone_refund_service.create(
                    current_user,
                    bar_id,
                    product_id=product_id,
                    quantity=request.form.get("refund_quantity", "1"),
                    returned_disposition=request.form.get("refund_disposition", "RESTOCK"),
                    reason=request.form.get("refund_reason", "").strip(),
                    refund_method=request.form.get("refund_method", ""),
                    provider_transaction_id=request.form.get("refund_provider_transaction_id"),
                )
                db.session.commit()
                stock_note = " remise en stock" if result.returned_disposition == "RESTOCK" else " classée en perte"
                flash(
                    f"Remboursement {result.reference} enregistré : {result.quantity} × {result.product_name}, "
                    f"{result.refund_amount:,.0f} {bar.currency} remboursés en "
                    f"{PAYMENT_LABELS.get(result.refund_method, result.refund_method)} ; bouteille{stock_note}.",
                    "success",
                )
                return redirect(url_for("cashier_exchanges_web.manage", bar_id=bar_id, mode="refund"))

            if action != "exchange":
                raise ValueError("INVALID_ACTION")
            if not can_exchange:
                raise PermissionError("FORBIDDEN")

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
            return redirect(url_for("cashier_exchanges_web.manage", bar_id=bar_id, mode="exchange"))
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
        "cashier_exchanges_v2.html",
        bar=bar,
        products=products,
        stock_balances=stock_balances,
        exchanges=_history(bar),
        payment_labels=PAYMENT_LABELS,
        direction_labels=DIRECTION_LABELS,
        can_exchange=can_exchange,
        can_refund=can_refund,
        initial_mode="refund" if request.args.get("mode") == "refund" else "exchange",
    )
