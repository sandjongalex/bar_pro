"""Fast cashier workspace: counter sale, order queue and payment in one screen."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.async_ui import wants_async_web_response, web_json_response
from app.cash_services import cash_service
from app.cashier_payment_idempotency import claim as claim_payment_intent, complete as complete_payment_intent
from app.change_voucher_models import ChangeVoucher
from app.change_voucher_service import change_voucher_service
from app.customer_services import customer_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import (
    Bar,
    CashSession,
    Customer,
    Order,
    OrderLine,
    Product,
    ProductCategory,
    StaffAssignment,
    StockBalance,
    User,
    utcnow,
)
from app.order_services import order_service
from app.order_line_views import effective_lines_by_order
from app.order_suborder_models import OrderSuborder
from app.cashier_order_rounds import cashier_rounds_by_order, pending_rounds
from app.payment_services import payment_service
from app.permissions import permissions
from app.product_display_order import product_order_expression
from app.validation import number

bp = Blueprint("cashier_workspace_web", __name__, url_prefix="/bars/<int:bar_id>/cashier")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "MIXED": "Mixte",
    "CREDIT": "Crédit",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _reference(prefix: str = "PAY") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _decimal(value, code: str = "INVALID_PAYMENT_AMOUNTS", allow_zero: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(code) from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError(code)
    return amount


def _cashier_assignment(bar_id: int):
    if not current_user.is_authenticated or current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.role == "CASHIER",
            StaffAssignment.ended_at.is_(None),
        )
    )


def _open_session(bar_id: int, *, lock: bool = False):
    stmt = (select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc()))
    return db.session.scalar(stmt.with_for_update() if lock else stmt)


def _cart_lines():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    lines = []
    for product_id, quantity in zip(product_ids, quantities):
        product_id = str(product_id or "").strip()
        quantity = str(quantity or "").strip()
        if not product_id or not quantity:
            continue
        lines.append({"product_id": int(product_id), "quantity": quantity})
    return lines


def _message(code) -> str:
    messages = {
        "INVALID_LINES": "Ajoutez au moins un produit avec une quantité valide.",
        "ORDER_EMPTY": "La commande ne contient aucun produit.",
        "ORDER_NOT_DRAFT": "Cette commande a déjà été traitée.",
        "ORDER_NOT_PAYABLE": "Cette commande n'est pas prête à être encaissée.",
        "PAYMENT_LIMIT_EXCEEDED": "Le montant dépasse le reste à payer.",
        "INVALID_PAYMENT_AMOUNTS": "Vérifiez les montants saisis.",
        "INVALID_MIXED_PAYMENT": "Indiquez deux montants positifs (espèces et Mobile Money), dont la somme ne dépasse pas le reste à payer.",
        "PROVIDER_REFERENCE_REQUIRED": "Sélectionnez MTN Mobile Money ou Orange Money après avoir confirmé la réception.",
        "CUSTOMER_REQUIRED": "Sélectionnez un client pour une vente à crédit.",
        "CREDIT_DISABLED": "Les ventes à crédit sont désactivées.",
        "CASH_SESSION_REQUIRED": "Ouvrez d'abord votre session de caisse.",
        "INSUFFICIENT_STOCK": "Stock insuffisant pour valider cette vente.",
        "FORBIDDEN": "Opération non autorisée.",
        "NOT_FOUND": "Commande ou produit introuvable.",
        "PAYMENT_ALREADY_PROCESSED": "Cette opération a déjà été enregistrée. Vérifiez le solde avant une nouvelle saisie.",
        "IDEMPOTENCY_CONFLICT": "Cette référence a déjà été utilisée avec des montants différents.",
        "IDEMPOTENCY_KEY_REQUIRED": "Référence de paiement manquante. Rechargez la facture avant de réessayer.",
        "INVALID_CASH_SOURCE": "Sélectionnez correctement la personne qui remet les espèces à la caisse.",
        "INVALID_NUMBER": "Montant remis invalide.",
        "INVALID_PRECISION": "Le montant remis doit respecter l'unité monétaire du bar.",
        "POSITIVE_NUMBER_REQUIRED": "Le montant remis doit être supérieur à zéro.",
        "PAYMENT_STATE_MISMATCH": "Le solde et le statut de la facture ne correspondent pas. Aucune vente n'a été confirmée : vérifiez et réessayez.",
        "ORDER_ALREADY_FINALIZED": "Cette facture a déjà été clôturée.",
        "ORDER_NOT_SETTLED": "Un montant reste à encaisser avant la clôture.",
        "NOT_PREPAID_ORDER": "Cette facture n'a pas d'avance à clôturer.",
    }
    return messages.get(str(code), "Opération refusée. Vérifiez les informations saisies.")


def _cash_destination(order: Order, session: CashSession):
    """Default cashier cash to the drawer; allow explicit server custody only when relevant."""
    holder = (request.form.get("cash_holder_mode") or "DRAWER").strip().upper()
    if holder != "STAFF":
        return session.id, None

    if order.assigned_staff_id is None:
        raise ValueError("INVALID_STAFF_CASH_HOLDER")
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.id == order.assigned_staff_id,
            StaffAssignment.bar_id == order.bar_id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    if not assignment:
        raise ValueError("INVALID_STAFF_CASH_HOLDER")
    return None, assignment.id


def _receipt_source_label(bar_id: int) -> str:
    """Trace who physically handed money to the cash drawer.

    This is not the custody setting: handing cash over to the cashier credits the
    cash session, never the waitress ledger. An existing staff-held payment must
    instead be handed over through the separate cash-handover workflow.
    """
    source = (request.form.get("cash_receipt_source") or "CLIENT").strip().upper()
    if source == "CLIENT":
        return "client"
    if source != "SERVER":
        raise ValueError("INVALID_CASH_SOURCE")
    assignment_id = request.form.get("cash_receipt_server_id", type=int)
    if assignment_id is None:
        raise ValueError("INVALID_CASH_SOURCE")
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.id == assignment_id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    if assignment is None:
        raise ValueError("INVALID_CASH_SOURCE")
    user = db.session.get(User, assignment.user_id)
    if user is None:
        raise ValueError("INVALID_CASH_SOURCE")
    return f"serveuse #{assignment.id} ({user.display_name})"[:120]


@bp.before_app_request
def route_cashier_to_primary_workspace():
    """Make the fast workspace the cashier's default entry for orders and checkout."""
    if request.method != "GET" or request.endpoint not in {"checkout_web.checkout", "orders_web.quick"}:
        return None
    if not current_user.is_authenticated:
        return None
    bar_id = (request.view_args or {}).get("bar_id")
    if bar_id is None or _cashier_assignment(bar_id) is None:
        return None
    if _open_session(bar_id) is None:
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    values = {"bar_id": bar_id}
    if request.endpoint == "orders_web.quick":
        values["sale"] = 1
    else:
        order_id = request.args.get("order_id", type=int)
        if order_id:
            values["order_id"] = order_id
    return redirect(url_for("cashier_workspace_web.workspace", **values))


@bp.route("/workspace", methods=["GET", "POST"])
@login_required
def workspace(bar_id: int):
    permissions.require(current_user, "payments.read", bar_id)
    permissions.require(current_user, "orders.create", bar_id)
    assignment = _cashier_assignment(bar_id)
    if assignment is None:
        raise PermissionError("FORBIDDEN")

    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    session = _open_session(bar_id, lock=request.method == "POST")
    async_web = wants_async_web_response()
    if session is None:
        message = "Ouvrez votre caisse avant de commencer le service."
        if async_web:
            return web_json_response(
                error={"code": "CASH_SESSION_REQUIRED"},
                message=message,
                status=409,
            )
        flash(message, "info")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        order_id = request.form.get("order_id", type=int)
        try:
            payment_intent = None
            if action in {"cash_exact", "payment"}:
                payment_intent = claim_payment_intent(
                    current_user, bar_id, action,
                    request.form.get("idempotency_key") or request.form.get("reference"),
                    request.form, require_key=async_web,
                )
            if action == "create_sale":
                # Cash tender is optional: an empty field creates an unpaid invoice.
                # When provided, invoice, stock movement and payment commit together.
                tender_text = (request.form.get("cash_presented") or "").strip()
                cash_source = _receipt_source_label(bar_id) if tender_text else None
                order = order_service.create(
                    current_user,
                    bar_id,
                    (request.form.get("reference") or "").strip() or _reference("CMD"),
                    _cart_lines(),
                    notes=(request.form.get("notes") or "").strip() or None,
                    invoice_name=(request.form.get("invoice_name") or "").strip() or None,
                )
                # A counter sale is physically delivered by the cashier at creation time.
                order_service.confirm(current_user, bar_id, order.id)
                due = Decimal(order_balance(order, locking=True)["amount_due"] or 0)
                payment_data = None
                if tender_text:
                    received = number(
                        tender_text,
                        scale=0 if order.currency in {"XAF", "XOF"} else 2,
                        positive=True,
                    )
                    if due <= 0:
                        raise ValueError("INVALID_PAYMENT_AMOUNTS")
                    applied = min(received, due)
                    # Excess notes are a *customer advance*, NOT returned change.
                    # Record the whole physical receipt in the cash drawer using
                    # the existing persisted change-voucher liability.
                    advance = max(Decimal("0"), received - due)
                    change = Decimal("0")
                    payment = payment_service.record(
                        current_user, bar_id, order.id, _reference("PAY"), "CASH",
                        received, applied, change,
                        cash_session_id=session.id,
                        cash_received_from=cash_source,
                        voucher_amount=advance,
                    )
                    voucher = (
                        change_voucher_service.issue(
                            current_user, bar_id, payment.id, advance,
                            customer_name=order.customer_name_snapshot,
                            customer_advance=True,
                        )
                        if advance > 0 else None
                    )
                    # Locking totals see the payment inserted in this transaction.
                    # The queue filters on the persisted payment_status, not on
                    # the amount entered into the UI.
                    new_balance = order_balance(order, update=True, locking=True)
                    remaining = Decimal(new_balance["amount_due"] or 0)
                    expected_status = "PAID" if applied == due else "PARTIAL"
                    if order.payment_status != expected_status or remaining != due - applied:
                        raise ValueError("PAYMENT_STATE_MISMATCH")
                    payment_data = {
                        "amount_presented": format(received.normalize(), "f"),
                        "amount_applied": format(applied.normalize(), "f"),
                        "change_given": format(change.normalize(), "f"),
                        "remaining": format(remaining.normalize(), "f"),
                        "source": cash_source,
                        "currency": order.currency,
                        "payment_status": order.payment_status,
                        "advance_balance": format(advance.normalize(), "f"),
                        "advance_code": voucher.code if voucher else None,
                        "order_open": bool(voucher),
                    }
                else:
                    remaining = due
                    order_balance(order, update=True, locking=True)
                db.session.commit()
                if payment_data and payment_data["order_open"]:
                    message = (
                        f"Commande EN COURS · {received:,.0f} {order.currency} reçus "
                        f"(remis par {cash_source}) ; {applied:,.0f} consommés ; "
                        f"avance conservée : {advance:,.0f} {order.currency} (bon {voucher.code}). "
                        "Ajoutez les consommations puis clôturez pour rendre le reliquat."
                    )
                elif payment_data and order.payment_status == "PAID":
                    message = (
                        f"Facture réglée pour les produits actuels · "
                        f"{payment_data['amount_applied']} {order.currency} encaissés "
                        f"(remis par {cash_source})."
                    )
                elif payment_data:
                    message = (
                        f"Facture PARTIELLEMENT PAYÉE · {payment_data['amount_applied']} {order.currency} encaissés "
                        f"(remis par {cash_source}). "
                        f"Reste à payer : {payment_data['remaining']} {order.currency}."
                    )
                else:
                    message = f"Facture créée sans paiement · {order.total_amount:,.0f} {order.currency} à encaisser."
                refresh_url = (
                    url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment")
                    if remaining > 0 else
                    url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id)
                    if payment_data and payment_data["order_open"] else
                    url_for("cashier_workspace_web.workspace", bar_id=bar_id)
                )
                if async_web:
                    return web_json_response(
                        data={
                            "refresh_url": refresh_url,
                            "reset_cart": True,
                            "close_sale_panel": True,
                            "cash_summary": payment_data,
                            "payment_status": order.payment_status,
                            "order_id": order.id,
                        },
                        message=message,
                    )
                flash(message, "success")
                return redirect(refresh_url)

            if action == "close_prepaid":
                if not order_id:
                    raise LookupError("NOT_FOUND")
                order = db.session.scalar(
                    select(Order).where(
                        Order.bar_id == bar_id,
                        Order.id == order_id,
                        Order.cashier_assignment_id == assignment.id,
                    ).with_for_update()
                )
                if not order:
                    raise LookupError("NOT_FOUND")
                if order.closed_at is not None:
                    raise ValueError("ORDER_ALREADY_FINALIZED")
                voucher = db.session.scalar(
                    select(ChangeVoucher).where(
                        ChangeVoucher.bar_id == bar_id,
                        ChangeVoucher.origin_order_id == order.id,
                        ChangeVoucher.code.like("AV-%"),
                    ).with_for_update()
                )
                if voucher is None:
                    raise ValueError("NOT_PREPAID_ORDER")
                if Decimal(order_balance(order, locking=True)["amount_due"] or 0) > 0:
                    raise ValueError("ORDER_NOT_SETTLED")
                pending = db.session.scalar(
                    select(OrderSuborder.id).where(
                        OrderSuborder.bar_id == bar_id,
                        OrderSuborder.order_id == order.id,
                        or_(
                            OrderSuborder.status == "PENDING_VALIDATION",
                            OrderSuborder.delivery_status == "PENDING",
                        ),
                    ).limit(1)
                )
                if pending is not None:
                    raise ValueError("ORDER_NOT_PAYABLE")
                returned = Decimal(voucher.balance_amount or 0)
                if returned > 0:
                    change_voucher_service.refund_cash(
                        current_user, bar_id, voucher.code, session.id,
                        amount=returned,
                        at_order_close=True,
                    )
                order.closed_at = utcnow()
                db.session.commit()
                message = (
                    f"Commande clôturée · monnaie réellement rendue : "
                    f"{returned:,.0f} {order.currency}. Facture terminée."
                )
                refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id)
                if async_web:
                    return web_json_response(
                        data={"refresh_url": refresh_url, "prepaid_closed": True},
                        message=message,
                    )
                flash(message, "success")
                return redirect(refresh_url)

            if not order_id:
                raise LookupError("NOT_FOUND")

            if action == "deliver":
                order = order_service.confirm(current_user, bar_id, order_id)
                db.session.commit()
                message = f"{order.reference} livrée. Encaissez maintenant le client."
                refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment")
                if async_web:
                    return web_json_response(data={"refresh_url": refresh_url}, message=message)
                flash(message, "success")
                return redirect(refresh_url)

            order = db.session.scalar(
                select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
            )
            if not order:
                raise LookupError("NOT_FOUND")
            if order.cashier_assignment_id != assignment.id:
                raise LookupError("NOT_FOUND")
            if order.status not in {"CONFIRMED", "SERVED"}:
                raise ValueError("ORDER_NOT_PAYABLE")
            due = Decimal(order_balance(order, locking=True)["amount_due"] or 0)
            if due <= 0 or order.payment_status == "PAID":
                raise ValueError("ORDER_NOT_PAYABLE")
            # This also protects the credit path, which does not call PaymentService.
            if action in {"cash_exact", "payment"}:
                undelivered = db.session.scalar(
                    select(OrderSuborder.id).where(
                        OrderSuborder.bar_id == bar_id,
                        OrderSuborder.order_id == order.id,
                        OrderSuborder.status == "VALIDATED",
                        OrderSuborder.delivery_status == "PENDING",
                    ).limit(1)
                )
                if undelivered is not None:
                    raise ValueError("ORDER_NOT_PAYABLE")

            if action == "cash_exact":
                payment_service.record(
                    current_user,
                    bar_id,
                    order.id,
                    (request.form.get("reference") or "").strip() or _reference(),
                    "CASH",
                    due,
                    due,
                    0,
                    cash_session_id=session.id,
                )
                complete_payment_intent(payment_intent, order_id=order.id)
                db.session.commit()
                message = f"{order.reference} encaissée en espèces · {due:,.0f} {order.currency}."
                refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id)
                if async_web:
                    return web_json_response(data={"refresh_url": refresh_url}, message=message)
                flash(message, "success")
                return redirect(refresh_url)

            if action != "payment":
                raise ValueError("INVALID_ACTION")

            method = (request.form.get("method") or "CASH").strip().upper()
            base_reference = (request.form.get("reference_override") or request.form.get("reference") or "").strip() or _reference()

            if method == "CREDIT":
                customer_id = request.form.get("customer_id", type=int)
                if not customer_id:
                    raise LookupError("CUSTOMER_REQUIRED")
                customer_service.credit_order(
                    current_user,
                    bar_id,
                    order.id,
                    customer_id,
                    _reference("CRD"),
                )
                complete_payment_intent(payment_intent, order_id=order.id)
                db.session.commit()
                message = f"{order.reference} soldée à crédit."
                refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id)
                if async_web:
                    return web_json_response(data={"refresh_url": refresh_url}, message=message)
                flash(message, "success")
                return redirect(refresh_url)

            if method == "MIXED":
                cash_part = _decimal(request.form.get("mixed_cash"), allow_zero=True)
                mobile_part = _decimal(request.form.get("mixed_mobile"), allow_zero=True)
                if cash_part <= 0 or mobile_part <= 0 or cash_part + mobile_part > due:
                    raise ValueError("INVALID_MIXED_PAYMENT")
                presented = _decimal(request.form.get("amount_presented"))
                if presented < cash_part:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                provider_code = (request.form.get("provider_code") or "").strip().upper()
                provider_transaction_id = None  # No operator transaction ID entered.
                if provider_code not in {"MTN", "ORANGE"}:
                    raise ValueError("PROVIDER_REFERENCE_REQUIRED")
                cash_session_id, staff_assignment_id = _cash_destination(order, session)
                cash_source = _receipt_source_label(bar_id)
                if staff_assignment_id is not None and cash_source != "client":
                    raise ValueError("INVALID_CASH_SOURCE")
                # Extra physical cash is kept as an advance only for an open
                # cashier-created tab which has just been fully covered. On
                # other orders, surplus is ordinary returned change.
                mixed_advance = Decimal("0")
                if (
                    cash_part + mobile_part == due
                    and presented > cash_part
                    and order.assigned_staff_id is None
                    and order.cashier_assignment_id == assignment.id
                    and cash_session_id == session.id
                    and order.closed_at is None
                    and not db.session.scalar(
                        select(ChangeVoucher.id).where(
                            ChangeVoucher.bar_id == bar_id,
                            ChangeVoucher.origin_order_id == order.id,
                            ChangeVoucher.code.like("AV-%"),
                        )
                    )
                ):
                    mixed_advance = presented - cash_part
                cash_payment = payment_service.record(
                    current_user,
                    bar_id,
                    order.id,
                    f"{base_reference[:59]}-ESP",
                    "CASH",
                    presented,
                    cash_part,
                    presented - cash_part - mixed_advance,
                    cash_session_id=cash_session_id,
                    staff_assignment_id=staff_assignment_id,
                    cash_received_from=cash_source if cash_session_id is not None else None,
                    voucher_amount=mixed_advance,
                )
                payment_service.record(
                    current_user,
                    bar_id,
                    order.id,
                    f"{base_reference[:58]}-MOMO",
                    "MOBILE_MONEY",
                    mobile_part,
                    mobile_part,
                    0,
                    provider_code=provider_code,
                    provider_transaction_id=provider_transaction_id,
                )
                mixed_voucher = (
                    change_voucher_service.issue(
                        current_user, bar_id, cash_payment.id, mixed_advance,
                        customer_name=order.customer_name_snapshot,
                        customer_advance=True,
                    )
                    if mixed_advance > 0 else None
                )
                remaining = Decimal(order_balance(order, update=True, locking=True)["amount_due"] or 0)
                complete_payment_intent(payment_intent, order_id=order.id)
                db.session.commit()
                if mixed_voucher is not None:
                    message = (
                        f"Commande EN COURS · {cash_part:,.0f} en espèces + "
                        f"{mobile_part:,.0f} par {provider_code}. "
                        f"Avance conservée : {mixed_advance:,.0f} {order.currency}."
                    )
                    refresh_url = url_for(
                        "cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id,
                    )
                elif remaining > 0:
                    message = (
                        f"Paiement mixte PARTIEL : {cash_part:,.0f} en espèces + "
                        f"{mobile_part:,.0f} par {provider_code}. "
                        f"Reste {remaining:,.0f} {order.currency}."
                    )
                    refresh_url = url_for(
                        "cashier_workspace_web.workspace", bar_id=bar_id,
                        order_id=order.id, focus="payment",
                    )
                else:
                    message = (
                        f"Montant actuel réglé : {cash_part:,.0f} en espèces + "
                        f"{mobile_part:,.0f} par {provider_code}."
                    )
                    refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id)
                if async_web:
                    return web_json_response(data={"refresh_url": refresh_url}, message=message)
                flash(message, "success")
                return redirect(refresh_url)

            applied = _decimal(request.form.get("amount_applied") or due)
            if applied > due:
                raise ValueError("PAYMENT_LIMIT_EXCEEDED")

            provider_code = None
            provider_transaction_id = None
            cash_session_id = None
            staff_assignment_id = None
            advance_amount = Decimal("0")
            if method == "CASH":
                presented = _decimal(request.form.get("amount_presented"))
                if presented < applied:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                change = presented - applied
                cash_session_id, staff_assignment_id = _cash_destination(order, session)
                cash_source = _receipt_source_label(bar_id)
                if staff_assignment_id is not None and cash_source != "client":
                    raise ValueError("INVALID_CASH_SOURCE")
                # A cashier may receive a larger note for an own-counter
                # invoice that remains open for more products. Do not return
                # this money until explicit closing of the customer's tab.
                if (
                    order.assigned_staff_id is None
                    and order.cashier_assignment_id == assignment.id
                    and applied == due and presented > applied
                    and cash_session_id == session.id
                    and not db.session.scalar(
                        select(ChangeVoucher.id).where(
                            ChangeVoucher.bar_id == bar_id,
                            ChangeVoucher.origin_order_id == order.id,
                        )
                    )
                ):
                    advance_amount = presented - applied
                    change = Decimal("0")
            else:
                cash_source = None
                presented = applied
                change = Decimal("0")
                if method == "MOBILE_MONEY":
                    provider_code = (request.form.get("provider_code") or "").strip().upper()
                    provider_transaction_id = None  # No operator transaction ID entered.
                    if provider_code not in {"MTN", "ORANGE"}:
                        raise ValueError("PROVIDER_REFERENCE_REQUIRED")

            payment = payment_service.record(
                current_user,
                bar_id,
                order.id,
                base_reference,
                method,
                presented,
                applied,
                change,
                cash_session_id=cash_session_id,
                staff_assignment_id=staff_assignment_id,
                provider_code=provider_code,
                provider_transaction_id=provider_transaction_id,
                cash_received_from=cash_source if cash_session_id is not None else None,
                voucher_amount=advance_amount,
            )
            advance_voucher = (
                change_voucher_service.issue(
                    current_user, bar_id, payment.id, advance_amount,
                    customer_name=order.customer_name_snapshot,
                    customer_advance=True,
                )
                if advance_amount > 0 else None
            )
            complete_payment_intent(payment_intent, order_id=order.id)
            db.session.commit()
            if advance_voucher is not None:
                message = (
                    f"Commande EN COURS · avance de {advance_amount:,.0f} {order.currency} "
                    f"conservée (bon {advance_voucher.code}). Ajoutez les produits puis clôturez."
                )
                refresh_url = url_for(
                    "cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id,
                )
                if async_web:
                    return web_json_response(data={"refresh_url": refresh_url}, message=message)
                flash(message, "success")
                return redirect(refresh_url)
            remaining = Decimal(order_balance(db.session.get(Order, order.id))["amount_due"] or 0)
            if remaining > 0:
                message = f"{payment.amount_applied:,.0f} {payment.currency} encaissés · reste {remaining:,.0f}."
                refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment")
                if async_web:
                    return web_json_response(data={"refresh_url": refresh_url}, message=message)
                flash(message, "success")
                return redirect(refresh_url)
            message = f"{order.reference} payée. Commande suivante prête."
            refresh_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id)
            if async_web:
                return web_json_response(data={"refresh_url": refresh_url}, message=message)
            flash(message, "success")
            return redirect(refresh_url)

        except OperationalError as exc:
            db.session.rollback()
            if not any(term in str(exc).lower() for term in ("database is locked", "deadlock", "lock wait timeout")):
                raise
            message = "Une autre opération de caisse est en cours. Vérifiez son résultat avant de réessayer."
            if async_web:
                return web_json_response(error={"code": "PAYMENT_BUSY"}, message=message, status=409)
            flash(message, "danger")
            return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                code = "REFERENCE_EXISTS"
                message = (
                    "Cette référence existe déjà. Vérifiez le résultat avant toute nouvelle tentative."
                    if async_web else "Cette référence existe déjà. Réessayez."
                )
                status = 409
            else:
                code = str(exc) or "OPERATION_FAILED"
                message = _message(exc)
                if isinstance(exc, PermissionError):
                    status = 403
                elif isinstance(exc, LookupError) and code == "NOT_FOUND":
                    status = 404
                elif code in {"PAYMENT_ALREADY_PROCESSED", "IDEMPOTENCY_CONFLICT",
                              "ORDER_NOT_PAYABLE", "ORDER_NOT_DRAFT", "PAYMENT_LIMIT_EXCEEDED",
                              "CASH_SESSION_REQUIRED"}:
                    status = 409
                else:
                    status = 422
            if async_web:
                return web_json_response(error={"code": code}, message=message, status=status)
            flash(message, "danger")
            target = {"bar_id": bar_id}
            if order_id:
                target["order_id"] = order_id
            if action == "create_sale":
                target["sale"] = 1
            return redirect(url_for("cashier_workspace_web.workspace", **target))

    # A tab with a retained customer advance remains operationally OPEN even
    # when the currently delivered products have a zero financial balance.
    prepaid_ids = select(ChangeVoucher.origin_order_id).where(
        ChangeVoucher.bar_id == bar_id,
        ChangeVoucher.code.like("AV-%"),
    )
    active_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                or_(
                    Order.payment_status.in_(["UNPAID", "PARTIAL"]),
                    and_(Order.closed_at.is_(None), Order.id.in_(prepaid_ids)),
                ),
                or_(
                    Order.cashier_assignment_id == assignment.id,
                    and_(Order.cashier_assignment_id.is_(None), Order.status == "DRAFT"),
                ),
            )
            .order_by(Order.id.asc())
            .limit(120)
        )
    )
    prepaid_order_ids = set(db.session.scalars(
        select(ChangeVoucher.origin_order_id).where(
            ChangeVoucher.bar_id == bar_id,
            ChangeVoucher.origin_order_id.in_([o.id for o in active_orders]),
            ChangeVoucher.code.like("AV-%"),
        )
    ))
    waiting_orders = [item for item in active_orders if item.status == "DRAFT"]
    prepaid_orders = [
        item for item in active_orders if item.id in prepaid_order_ids
        and item.closed_at is None and item.payment_status == "PAID"
    ]
    payable_orders = [
        item for item in active_orders
        if item.status in {"CONFIRMED", "SERVED"} and item.payment_status != "PAID"
    ]
    prepaid_vouchers = {
        item.origin_order_id: item for item in db.session.scalars(
            select(ChangeVoucher).where(
                ChangeVoucher.bar_id == bar_id,
                ChangeVoucher.origin_order_id.in_(list(prepaid_order_ids)),
                ChangeVoucher.code.like("AV-%"),
            )
        )
    } if prepaid_order_ids else {}

    selected_order = None
    selected_id = request.args.get("order_id", type=int)
    if selected_id:
        selected_order = next((item for item in active_orders if item.id == selected_id), None)
    if selected_order is None and payable_orders:
        selected_order = payable_orders[0]
    if selected_order is None and waiting_orders:
        selected_order = waiting_orders[0]
    if selected_order is None and prepaid_orders:
        selected_order = prepaid_orders[0]

    order_ids = [item.id for item in active_orders]
    lines_by_order = effective_lines_by_order(bar_id, order_ids)
    rounds_by_order = cashier_rounds_by_order(bar_id, order_ids)

    balances = {item.id: order_balance(item) for item in active_orders}

    assignment_ids = {item.assigned_staff_id for item in active_orders if item.assigned_staff_id is not None}
    assignments = {
        item.id: item
        for item in db.session.scalars(select(StaffAssignment).where(StaffAssignment.id.in_(assignment_ids)))
    } if assignment_ids else {}
    user_ids = {item.user_id for item in assignments.values()}
    users = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    server_name_by_order = {}
    for item in active_orders:
        staff = assignments.get(item.assigned_staff_id)
        user = users.get(staff.user_id) if staff else None
        server_name_by_order[item.id] = user.display_name if user else "Comptoir"

    selected_server_assignment = assignments.get(selected_order.assigned_staff_id) if selected_order else None

    products = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar_id, Product.is_active.is_(True))
            .order_by(product_order_expression(Product.name, Product.display_order), Product.name, Product.id)
        )
    )
    categories = list(
        db.session.scalars(
            select(ProductCategory)
            .where(ProductCategory.bar_id == bar_id, ProductCategory.is_active.is_(True))
            .order_by(ProductCategory.name, ProductCategory.id)
        )
    )
    categories_by_id = {item.id: item for item in categories}
    stock_balances = {
        item.product_id: item.quantity
        for item in db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar_id))
    }

    customers = list(
        db.session.scalars(
            select(Customer)
            .where(Customer.bar_id == bar_id, Customer.is_active.is_(True))
            .order_by(Customer.display_name, Customer.id)
        )
    ) if bar.credit_sales_enabled else []

    total_due = sum((Decimal(balances[item.id]["amount_due"] or 0) for item in payable_orders), Decimal("0"))
    stats = {
        "waiting": len(waiting_orders),
        "to_pay": len(payable_orders),
        "due": total_due,
        "expected": Decimal(cash_service.expected(session) or 0),
    }

    server_assignments = list(db.session.execute(
        select(StaffAssignment, User)
        .join(User, StaffAssignment.user_id == User.id)
        .where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
        .order_by(User.display_name, StaffAssignment.id)
        .limit(100)
    ))

    return render_template(
        "cashier_workspace_v2.html",
        bar=bar,
        session=session,
        waiting_orders=waiting_orders,
        payable_orders=payable_orders,
        prepaid_orders=prepaid_orders,
        prepaid_vouchers=prepaid_vouchers,
        selected_order=selected_order,
        selected_server_assignment=selected_server_assignment,
        lines_by_order=lines_by_order,
        rounds_by_order=rounds_by_order,
        can_confirm_round=True,
        selected_pending_delivery=(pending_rounds(rounds_by_order.get(selected_order.id, [])) if selected_order else False),
        balances=balances,
        server_name_by_order=server_name_by_order,
        products=products,
        categories=categories,
        categories_by_id=categories_by_id,
        stock_balances=stock_balances,
        customers=customers,
        server_assignments=server_assignments,
        payment_labels=PAYMENT_LABELS,
        stats=stats,
        sale_mode=request.args.get("sale") == "1",
        focus_payment=request.args.get("focus") == "payment",
        sale_reference=_reference("CMD"),
        cash_exact_reference=_reference("PAY"),
        payment_reference=_reference("PAY"),
    )