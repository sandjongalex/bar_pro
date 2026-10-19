"""Server-rendered customer accounts, receivables and returnable cases."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo
import secrets
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.cashier_secondary_async import begin, succeeded, failed
from app.bar_services import update_bar
from app.cash_services import cash_service
from app.customer_models import CustomerCaseEntry, CustomerLedgerEntry
from app.customer_services import customer_service, outstanding_manual_debts
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
        "MANUAL_DEBT_REPAYMENT_LIMIT": "Le remboursement dépasse la dette manuelle restante.",
        "MANUAL_DEBT_HAS_REPAYMENTS": "Cette dette a eu un remboursement ultérieur. Annulation automatique bloquée : vérification du propriétaire nécessaire.",
        "INVALID_DEBT_DUE_DATE": "La date limite de remboursement est invalide.",
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
    can_manual_debt = permissions.evaluate(current_user, "customer_debt.manage", bar_id).allowed
    can_cases = permissions.evaluate(current_user, "cases.manage", bar_id).allowed
    can_settings = permissions.evaluate(current_user, "bars.update_settings", bar_id).allowed

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            intent = begin(current_user, bar_id, "customer." + action)
            if action in {"credit_enable", "credit_disable"}:
                if not can_settings:
                    raise PermissionError("FORBIDDEN")
                update_bar(current_user, bar_id, {"credit_sales_enabled": action == "credit_enable"})
                response = succeeded(intent, action, "Ventes à crédit activées." if action == "credit_enable" else "Ventes à crédit désactivées.")
                if response is not None: return response

            elif action == "create":
                created = customer_service.create(
                    current_user,
                    bar_id,
                    {
                        "display_name": request.form.get("display_name", ""),
                        "phone": request.form.get("phone", ""),
                        "email": request.form.get("email", ""),
                    },
                )
                response = succeeded(intent, action, "Client créé avec succès.", data={"customer_id": created.id})
                if response is not None: return response

            elif action == "update":
                updated = customer_service.update(
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    {
                        "display_name": request.form.get("display_name", ""),
                        "phone": request.form.get("phone", ""),
                        "email": request.form.get("email", ""),
                    },
                )
                response = succeeded(intent, action, "Informations du client enregistrées.", data={"customer_id": updated.id})
                if response is not None: return response

            elif action in {"enable", "disable"}:
                updated = customer_service.set_active(
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    action == "enable",
                )
                response = succeeded(intent, action, "Client mis à jour.", data={"customer_id": updated.id})
                if response is not None: return response

            elif action in {"manual_debt", "manual_repayment"}:
                reference = request.form.get("reference", "").strip()
                if not reference:
                    reference = _reference("DET" if action == "manual_debt" else "REM")
                params = (
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    reference,
                    request.form.get("amount", ""),
                    request.form.get("reason", ""),
                )
                item = (
                    customer_service.record_manual_debt(*params, due_date=request.form.get("due_date", ""))
                    if action == "manual_debt"
                    else customer_service.record_manual_repayment(*params)
                )
                message = "Dette manuelle enregistrée." if action == "manual_debt" else "Remboursement hors caisse enregistré."
                response = succeeded(intent, action, message, data={"customer_id": item.customer_id, "entry_id": item.id})
                if response is not None: return response

            elif action == "manual_reverse":
                item = customer_service.reverse_manual_debt(
                    current_user,
                    bar_id,
                    int(request.form.get("entry_id", "0")),
                    request.form.get("reference", "").strip() or _reference("ANN-DET"),
                    request.form.get("reason", ""),
                )
                response = succeeded(intent, action, "Dette annulée avec traçabilité.", data={"entry_id": item.id, "customer_id": item.customer_id})
                if response is not None: return response

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
                response = succeeded(intent, action, f"Règlement client de {abs(payment.amount_delta):,.0f} {payment.currency} enregistré.", data={"customer_id": payment.customer_id, "entry_id": payment.id})
                if response is not None: return response

            elif action in {"case_out", "case_return"}:
                case_entry = customer_service.record_case(
                    current_user,
                    bar_id,
                    int(request.form.get("customer_id", "0")),
                    int(request.form.get("product_id", "0")),
                    request.form.get("quantity", ""),
                    "OUT" if action == "case_out" else "RETURN",
                    request.form.get("reason", "").strip(),
                )
                response = succeeded(intent, action, "Mouvement de casier enregistré.", data={"customer_id": case_entry.customer_id, "entry_id": case_entry.id})
                if response is not None: return response

            elif action == "reverse_credit":
                reversal = customer_service.reverse_credit_and_cancel(
                    current_user,
                    bar_id,
                    int(request.form.get("entry_id", "0")),
                    request.form.get("reference", "").strip() or _reference("ANN-CRD"),
                    request.form.get("reason", "").strip(),
                )
                response = succeeded(intent, action, "Vente à crédit annulée et stock restitué.", data={"entry_id": reversal.id})
                if response is not None: return response

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            response = failed(exc, _message(exc))
            if response is not None: return response
        return redirect(url_for("customers_web.manage", bar_id=bar_id))

    customers = list(
        db.session.scalars(
            select(Customer).where(Customer.bar_id == bar_id).order_by(Customer.is_active.desc(), Customer.display_name, Customer.id)
        )
    )
    active_customers = [item for item in customers if item.is_active]
    all_customers = customers

    debt_rows = dict(
        db.session.execute(
            select(CustomerLedgerEntry.customer_id, func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0))
            .where(CustomerLedgerEntry.bar_id == bar_id, CustomerLedgerEntry.entry_kind != "ADJUSTMENT")
            .group_by(CustomerLedgerEntry.customer_id)
        ).all()
    )
    manual_debt_rows = dict(
        db.session.execute(
            select(CustomerLedgerEntry.customer_id, func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0))
            .where(CustomerLedgerEntry.bar_id == bar_id, CustomerLedgerEntry.entry_kind == "ADJUSTMENT")
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
    manual_debt_by_customer = {customer.id: Decimal(manual_debt_rows.get(customer.id, 0) or 0) for customer in customers}
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
    customer_by_id = {customer.id: customer for customer in all_customers}
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

    manual_entries = list(db.session.scalars(
        select(CustomerLedgerEntry)
        .where(CustomerLedgerEntry.bar_id == bar_id, CustomerLedgerEntry.entry_kind == "ADJUSTMENT")
        .order_by(CustomerLedgerEntry.id)
    ))
    by_customer = defaultdict(list)
    for entry in manual_entries:
        by_customer[entry.customer_id].append(entry)
    today = datetime.now(ZoneInfo(bar.timezone)).date()
    aging_by_customer = {
        customer.id: outstanding_manual_debts(by_customer[customer.id], today=today)
        for customer in all_customers
    }
    overdue_total = sum((details["overdue"] for details in aging_by_customer.values()), Decimal(0))
    overdue_count = sum(details["overdue"] > 0 for details in aging_by_customer.values())
    manual_collected = sum((
        -Decimal(entry.amount_delta) for entry in manual_entries
        if entry.amount_delta < 0 and entry.reversal_of_id is None
    ), Decimal(0))
    credit_collected = -Decimal(db.session.scalar(
        select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
            CustomerLedgerEntry.bar_id == bar_id, CustomerLedgerEntry.entry_kind == "PAYMENT"
        )
    ) or 0)
    top_debtors = sorted(
        (customer for customer in all_customers
         if debt_by_customer[customer.id] + manual_debt_by_customer[customer.id] > 0),
        key=lambda customer: debt_by_customer[customer.id] + manual_debt_by_customer[customer.id],
        reverse=True,
    )[:5]

    total_manual_debt = sum((max(value, Decimal("0")) for value in manual_debt_by_customer.values()), Decimal("0"))
    total_debt = sum((max(value, Decimal("0")) for value in debt_by_customer.values()), Decimal("0")) + total_manual_debt
    stats = {
        "customers": len(active_customers),
        "debtors": sum(1 for customer in customers if debt_by_customer[customer.id] > 0 or manual_debt_by_customer[customer.id] > 0),
        "debt": total_debt,
        "manual_debt": total_manual_debt,
        "overdue": overdue_total,
        "overdue_count": overdue_count,
        "manual_collected": manual_collected,
        "credit_collected": credit_collected,
        "cases": sum(max(value, 0) for value in cases_by_customer.values()),
    }

    query_text = request.args.get("q", "").strip()[:120]
    customer_filter = request.args.get("filter", "all")
    if customer_filter not in {"all", "debtors", "manual", "overdue", "settled", "inactive"}:
        customer_filter = "all"
    normalized = query_text.casefold()

    def matches_customer(customer):
        credit = debt_by_customer[customer.id]
        manual = manual_debt_by_customer[customer.id]
        if normalized and not any(
            normalized in str(value or "").casefold()
            for value in (customer.display_name, customer.phone, customer.email)
        ):
            return False
        if customer_filter == "debtors":
            return credit + manual > 0
        if customer_filter == "manual":
            return manual > 0
        if customer_filter == "overdue":
            return aging_by_customer[customer.id]["overdue"] > 0
        if customer_filter == "settled":
            return credit + manual <= 0
        if customer_filter == "inactive":
            return not customer.is_active
        return True

    customers = [customer for customer in all_customers if matches_customer(customer)]

    return render_template(
        "customers.html",
        bar=bar,
        customers=customers,
        active_customers=active_customers,
        debt_by_customer=debt_by_customer,
        query_text=query_text,
        customer_filter=customer_filter,
        manual_debt_by_customer=manual_debt_by_customer,
        aging_by_customer=aging_by_customer,
        top_debtors=top_debtors,
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
        can_manual_debt=can_manual_debt,
        can_cases=can_cases,
        can_settings=can_settings,
        new_intent_key=lambda: secrets.token_urlsafe(32),
    )
