"""Server-rendered supplier debt and settlement desk for one bar."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.models import Bar, CashSession, Purchase, SupplierPayment
from app.permissions import permissions
from app.purchase_services import purchase_service

bp = Blueprint("supplier_debts_web", __name__, url_prefix="/bars/<int:bar_id>/supplier-debts")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement bancaire",
}


def _reference(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _optional_int(value):
    value = str(value or "").strip()
    return int(value) if value else None


def _message(code) -> str:
    messages = {
        "NOT_FOUND": "L'achat ou le paiement fournisseur est introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer les paiements fournisseurs.",
        "BAR_SUSPENDED": "Le bar est suspendu : les paiements sont bloqués.",
        "PURCHASE_NOT_PAYABLE": "Seuls les achats réceptionnés peuvent être payés.",
        "SUPPLIER_PAYMENT_LIMIT": "Le montant dépasse le solde restant dû.",
        "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
        "CASH_LOCATION_REQUIRED": "Pour un paiement en espèces, ouvrez d'abord une session de caisse et sélectionnez-la.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse sélectionnée n'est plus ouverte.",
        "CURRENCY_MISMATCH": "La devise de la caisse ne correspond pas à celle de l'achat.",
        "INSUFFICIENT_DRAWER_CASH": "La caisse ne contient pas suffisamment d'espèces pour ce paiement.",
        "PROVIDER_REFERENCE_REQUIRED": "Renseignez à la fois le prestataire et la référence de transaction, ou laissez les deux champs vides.",
        "ONLY_PAYMENT_REVERSIBLE": "Ce mouvement ne peut pas être annulé.",
        "ALREADY_REVERSED": "Ce paiement a déjà été annulé.",
    }
    return messages.get(str(code), "Opération impossible. Vérifiez le montant, le mode de paiement et les références.")


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id):
    permissions.require(current_user, "purchases.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_manage = permissions.evaluate(current_user, "purchases.manage", bar_id).allowed

    if request.method == "POST":
        if not can_manage:
            raise PermissionError("FORBIDDEN")

        action = request.form.get("action", "")
        try:
            if action == "pay":
                method = request.form.get("method", "").strip()
                provider_code = request.form.get("provider_code", "").strip() or None
                provider_transaction_id = request.form.get("provider_transaction_id", "").strip() or None
                cash_session_id = _optional_int(request.form.get("cash_session_id")) if method == "CASH" else None

                payment = purchase_service.pay(
                    current_user,
                    bar_id,
                    int(request.form.get("purchase_id", "0")),
                    request.form.get("reference", "").strip() or _reference("FOU"),
                    request.form.get("amount", ""),
                    method,
                    request.form.get("reason", "").strip(),
                    cash_session_id,
                    provider_code,
                    provider_transaction_id,
                )
                db.session.commit()
                flash(
                    f"Paiement fournisseur {payment.reference} enregistré : {payment.amount:,.0f} {payment.currency}.",
                    "success",
                )

            elif action == "reverse":
                source = db.session.scalar(
                    select(SupplierPayment).where(
                        SupplierPayment.bar_id == bar_id,
                        SupplierPayment.id == int(request.form.get("supplier_payment_id", "0")),
                    )
                )
                if not source:
                    raise LookupError("NOT_FOUND")
                cash_session_id = _optional_int(request.form.get("cash_session_id")) if source.method == "CASH" else None
                reversal = purchase_service.reverse_payment(
                    current_user,
                    bar_id,
                    source.id,
                    request.form.get("reference", "").strip() or _reference("ANN-FOU"),
                    request.form.get("reason", "").strip(),
                    cash_session_id,
                )
                db.session.commit()
                flash(f"Paiement {source.reference} annulé par {reversal.reference}.", "success")

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence de paiement existe déjà. Utilisez une autre référence.", "danger")
            else:
                flash(_message(exc), "danger")

        return redirect(url_for("supplier_debts_web.manage", bar_id=bar_id))

    posted_purchases = list(
        db.session.scalars(
            select(Purchase)
            .where(Purchase.bar_id == bar_id, Purchase.status == "POSTED")
            .order_by(Purchase.id.desc())
        )
    )

    signed_amount = case(
        (SupplierPayment.entry_kind == "PAYMENT", SupplierPayment.amount),
        else_=-SupplierPayment.amount,
    )
    net_by_purchase = dict(
        db.session.execute(
            select(SupplierPayment.purchase_id, func.coalesce(func.sum(signed_amount), 0))
            .where(SupplierPayment.bar_id == bar_id)
            .group_by(SupplierPayment.purchase_id)
        ).all()
    )
    due_by_purchase = {
        purchase.id: max(Decimal("0"), purchase.total_amount - Decimal(net_by_purchase.get(purchase.id, 0)))
        for purchase in posted_purchases
    }

    outstanding = [purchase for purchase in posted_purchases if due_by_purchase[purchase.id] > 0]
    paid_purchases = [purchase for purchase in posted_purchases if due_by_purchase[purchase.id] == 0]

    supplier_summary = {}
    for purchase in outstanding:
        name = purchase.supplier_name_snapshot
        row = supplier_summary.setdefault(name, {"name": name, "due": Decimal("0"), "purchases": 0})
        row["due"] += due_by_purchase[purchase.id]
        row["purchases"] += 1
    supplier_debts = sorted(supplier_summary.values(), key=lambda item: (-item["due"], item["name"].casefold()))

    payments = list(
        db.session.scalars(
            select(SupplierPayment)
            .where(SupplierPayment.bar_id == bar_id)
            .order_by(SupplierPayment.id.desc())
            .limit(100)
        )
    )
    purchase_by_id = {purchase.id: purchase for purchase in posted_purchases}
    reversed_ids = {payment.reversal_of_id for payment in payments if payment.reversal_of_id is not None}

    total_due = sum((due_by_purchase[purchase.id] for purchase in outstanding), Decimal("0"))
    total_net_paid = Decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(signed_amount), 0)).where(SupplierPayment.bar_id == bar_id)
        )
        or 0
    )

    open_cash_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )
    cash_expected = {session.id: cash_service.expected(session) for session in open_cash_sessions}

    stats = {
        "due": total_due,
        "suppliers": len(supplier_debts),
        "unpaid_purchases": len(outstanding),
        "net_paid": total_net_paid,
        "paid_purchases": len(paid_purchases),
    }

    return render_template(
        "supplier_debts.html",
        bar=bar,
        outstanding=outstanding,
        paid_purchases=paid_purchases,
        due_by_purchase=due_by_purchase,
        supplier_debts=supplier_debts,
        payments=payments,
        purchase_by_id=purchase_by_id,
        reversed_ids=reversed_ids,
        open_cash_sessions=open_cash_sessions,
        cash_expected=cash_expected,
        payment_labels=PAYMENT_LABELS,
        stats=stats,
        can_manage=can_manage,
    )
