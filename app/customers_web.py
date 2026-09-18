"""Server-rendered customer accounts, receivables and returnable cases."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.bar_services import update_bar
from app.cash_services import cash_service
from app.customer_models import CustomerCaseEntry, CustomerLedgerEntry
from app.customer_services import customer_service
from app.extensions import db
from app.models import Bar, CashSession, Customer, Product
from app.permissions import permissions

bp = Blueprint("customers_web", __name__, url_prefix="/bars/<int:bar_id>/customers")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _reference(prefix):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _message(code):
    messages = {
        "NOT_FOUND": "Client, produit ou mouvement introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer les clients.",
        "INVALID_CUSTOMER_PHONE": "Le numéro de téléphone est trop long.",
        "INVALID_CUSTOMER_EMAIL": "L'adresse e-mail est invalide.",
        "CUSTOMER_PAYMENT_LIMIT": "Le règlement dépasse la dette actuelle du client.",
        "INVALID_METHOD": "Le mode de règlement est invalide.",
        "PROVIDER_REFERENCE_REQUIRED": "Renseignez le prestataire et la référence de transaction ensemble.",
        "CASH_LOCATION_REQUIRED": "Sélectionnez une caisse ouverte pour un règlement en espèces.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse sélectionnée n'est plus ouverte.",
        "INVALID_CASE_QUANTITY": "Le nombre de casiers doit être un entier supérieur à zéro.",
        "CASE_RETURN_LIMIT": "Le client ne doit pas autant de casiers pour ce produit.",
        "INVALID_CASE_DIRECTION": "Le type de mouvement de casier est invalide.",
        "CUSTOMER_CREDIT_ALREADY_SETTLED": "Ce crédit a déjà été réglé en tout ou partie et ne peut pas être annulé automatiquement.",
        "ALREADY_REVERSED": "Ce crédit a déjà été annulé.",
        "REASON_REQUIRED": "Le motif est obligatoire.",
    }
    return messages.get(str(code), "Opération impossible. Vérifiez les informations saisies.")


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id):
    permissions.require(current_user, "customers.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_manage = permissions.evaluate(current_user, "customers.manage", bar_id).allowed
    can_credit = permissions.evaluate(current_user, "customer_credit.manage", bar_id).allowed
    can_cases = permissions.evaluate(current_user, "cases.manage", bar_id).allowed
    can_settings = permissions.evaluate(current_user, "bars.update_settings", bar_id).allowed

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action in {"credit_enable", "credit_disable"}:
                if not can_settings:
                    raise PermissionError("FORBIDDEN")
                update_bar(current_user, bar_id, {"credit_sales_enabled": action == "credit_enable"})
                db.session.commit()
                flash("Ventes à crédit activées." if action == "credit_enable" else "Ventes à crédit désactivées.", "success")

            elif action == "create":
                customer_service.create(
                    current_user,
                    bar_id,
                    {
                        "display_name": request.form.get("display_name", ""),
                        "phone": request.form.get("phone", ""),
                        "email": request.form.get("email", ""),
                    },
                )
                db.session.commit()
                flash("Client créé avec succès.", "success")

            elif action in {"enable", "disable"}:
                customer_service.set_active(
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    action == "enable",
                )
                db.session.commit()
                flash("Client mis à jour.", "success")

            elif action == "payment":
                method = request.form.get("method", "").strip()
                session_raw = request.form.get("cash_session_id", "").strip()
                payment = customer_service.record_payment(
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    request.form.get("reference", "").strip() or _reference("CLI"),
                    request.form.get("amount", ""),
                    method,
                    request.form.get("reason", "").strip(),
                    int(session_raw) if session_raw else None,
                    request.form.get("provider_code", "").strip() or None,
                    request.form.get("provider_transaction_id", "").strip() or None,
                )
                db.session.commit()
                flash(f"Règlement client de {abs(payment.amount_delta):,.0f} {payment.currency} enregistré.", "success")

            elif action in {"case_out", "case_return"}:
                customer_service.record_case(
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    int(request.form.get("product_id", "0")),
                    request.form.get("quantity", ""),
                    "OUT" if action == "case_out" else "RETURN",
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash("Mouvement de casier enregistré.", "success")

            elif action == "reverse_credit":
                customer_service.reverse_credit_and_cancel(
                    current_user,
                    bar_id,
                    int(request.form.get("entry_id", "0")),
                    request.form.get("reference", "").strip() or _reference("ANN-CRD"),
                    request.form.get("reason", "").strip(),
                )
                db.session.commit()
                flash("Vente à crédit annulée et stock restitué.", "success")

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")
        return redirect(url_for("customers_web.manage", bar_id=bar_id))

    customers = list(
        db.session.scalars(
            select(Customer).where(Customer.bar_id == bar_id).order_by(Customer.is_active.desc(), Customer.display_name, Customer.id)
        )
    )
    active_customers = [item for item in customers if item.is_active]

    debt_rows = dict(
        db.session.execute(
            select(CustomerLedgerEntry.customer_id, func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0))
            .where(CustomerLedgerEntry.bar_id == bar_id)
            .group_by(CustomerLedgerEntry.customer_id)
        ).all()
    )
    case_rows = dict(
        db.session.execute(
            select(CustomerCaseEntry.customer_id, func.coalesce(func.sum(CustomerCaseEntry.quantity_delta), 0))
            .where(CustomerCaseEntry.bar_id == bar_id)
            .group_by(CustomerCaseEntry.customer_id)
        ).all()
    )
    debt_by_customer = {customer.id: Decimal(debt_rows.get(customer.id, 0) or 0) for customer in customers}
    cases_by_customer = {customer.id: int(case_rows.get(customer.id, 0) or 0) for customer in customers}

    ledger = list(
        db.session.scalars(
            select(CustomerLedgerEntry)
            .where(CustomerLedgerEntry.bar_id == bar_id)
            .order_by(CustomerLedgerEntry.id.desc())
            .limit(100)
        )
    )
    case_history = list(
        db.session.scalars(
            select(CustomerCaseEntry)
            .where(CustomerCaseEntry.bar_id == bar_id)
            .order_by(CustomerCaseEntry.id.desc())
            .limit(100)
        )
    )
    customer_by_id = {customer.id: customer for customer in customers}
    products = list(
        db.session.scalars(
            select(Product).where(Product.bar_id == bar_id, Product.is_active.is_(True)).order_by(Product.name, Product.id)
        )
    )
    product_by_id = {product.id: product for product in products}
    open_sessions = list(
        db.session.scalars(
            select(CashSession).where(CashSession.bar_id == bar_id, CashSession.status == "OPEN").order_by(CashSession.id.desc())
        )
    )
    cash_expected = {session.id: cash_service.expected(session) for session in open_sessions}

    total_debt = sum((max(value, Decimal("0")) for value in debt_by_customer.values()), Decimal("0"))
    stats = {
        "customers": len(active_customers),
        "debtors": sum(1 for value in debt_by_customer.values() if value > 0),
        "debt": total_debt,
        "cases": sum(max(value, 0) for value in cases_by_customer.values()),
    }

    return render_template(
        "customers.html",
        bar=bar,
        customers=customers,
        active_customers=active_customers,
        debt_by_customer=debt_by_customer,
        cases_by_customer=cases_by_customer,
        ledger=ledger,
        case_history=case_history,
        customer_by_id=customer_by_id,
        products=products,
        product_by_id=product_by_id,
        open_sessions=open_sessions,
        cash_expected=cash_expected,
        payment_labels=PAYMENT_LABELS,
        stats=stats,
        can_manage=can_manage,
        can_credit=can_credit,
        can_cases=can_cases,
        can_settings=can_settings,
    )
