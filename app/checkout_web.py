"""Fast server-rendered cashier queue and checkout for bar orders."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.customer_services import customer_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Customer, Order, OrderLine, Payment, Refund, StaffAssignment, User
from app.order_services import order_service
from app.payment_services import payment_service
from app.permissions import permissions

bp = Blueprint("checkout_web", __name__, url_prefix="/bars/<int:bar_id>/checkout")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "MIXED": "Paiement mixte",
    "CREDIT": "Crédit client",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _reference(prefix="PAY") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _decimal(value, code="INVALID_PAYMENT_AMOUNTS", allow_zero=False) -> Decimal:
    try:
        amount = Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(code) from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError(code)
    return amount


def _positive_decimal(value, code="INVALID_PAYMENT_AMOUNTS") -> Decimal:
    return _decimal(value, code, allow_zero=False)


def _current_assignment(bar_id):
    if current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.ended_at.is_(None),
        )
    )


def _cash_session_open(bar_id):
    return bool(
        db.session.scalar(
            select(CashSession.id).where(
                CashSession.bar_id == bar_id,
                CashSession.status == "OPEN",
            )
        )
    )


def _cash_location_from_form(bar_id: int, order: Order):
    """Resolve where physical cash is currently held: drawer or assigned server."""
    mode = (request.form.get("cash_holder_mode") or "DRAWER").strip().upper()
    if mode == "STAFF":
        raw = (request.form.get("staff_assignment_id") or "").strip()
        if not raw:
            raise ValueError("INVALID_STAFF_CASH_HOLDER")
        staff_id = int(raw)
        staff = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.id == staff_id,
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.role == "SERVER",
                StaffAssignment.ended_at.is_(None),
            )
        )
        if not staff or order.assigned_staff_id != staff.id:
            raise ValueError("INVALID_STAFF_CASH_HOLDER")
        return None, staff.id
    if mode != "DRAWER":
        raise ValueError("INVALID_CASH_HOLDER_MODE")
    raw = (request.form.get("cash_session_id") or "").strip()
    return (int(raw) if raw else None), None


def _local_display(value, timezone_name: str) -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name)).strftime("%d/%m/%Y %H:%M")


def _message(code) -> str:
    messages = {
        "NOT_FOUND": "Commande introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à effectuer cette opération.",
        "BAR_SUSPENDED": "Le bar est suspendu : les encaissements sont bloqués.",
        "ORDER_NOT_DRAFT": "Cette commande a déjà été livrée ou annulée.",
        "ORDER_NOT_PAYABLE": "La commande doit d'abord être marquée Livrée.",
        "ORDER_ALREADY_CREDITED": "Cette commande a déjà été portée au compte d'un client.",
        "PAYMENT_LIMIT_EXCEEDED": "Le montant dépasse le reste à payer.",
        "INVALID_PAYMENT_AMOUNTS": "Vérifiez le montant encaissé et le montant reçu.",
        "INVALID_MIXED_PAYMENT": "Le paiement mixte doit répartir exactement le reste entre espèces et Mobile Money.",
        "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
        "CASH_LOCATION_REQUIRED": "Sélectionnez où se trouvent réellement les espèces.",
        "INVALID_CASH_HOLDER_MODE": "Le détenteur des espèces sélectionné est invalide.",
        "INVALID_STAFF_CASH_HOLDER": "Les espèces ne peuvent être attribuées qu'à la serveuse affectée à cette commande.",
        "CASH_SESSION_REQUIRED": "Ouvrez d'abord une session de caisse avant de traiter les commandes.",
        "CASH_SESSION_NOT_OPEN": "La caisse sélectionnée n'est plus ouverte.",
        "CURRENCY_MISMATCH": "La devise de la caisse ne correspond pas à celle de la commande.",
        "PROVIDER_REFERENCE_REQUIRED": "Pour Mobile Money, renseignez le prestataire et la référence de transaction.",
        "INVALID_NONCASH_PAYMENT": "Les informations du paiement non espèces sont invalides.",
        "INSUFFICIENT_STOCK": "Stock insuffisant : la livraison ne peut pas être confirmée.",
        "CREDIT_DISABLED": "Les ventes à crédit sont désactivées pour cet établissement.",
        "CUSTOMER_REQUIRED": "Sélectionnez obligatoirement un client pour une vente à crédit.",
        "CUSTOMER_MISMATCH": "La commande est déjà rattachée à un autre client.",
    }
    return messages.get(str(code), "Opération refusée. Vérifiez les informations saisies.")


@bp.get("/orders/<int:order_id>/receipt")
@login_required
def receipt(bar_id: int, order_id: int):
    """Printable receipt for a delivered order, including partial payments and credit."""
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    order = db.session.scalar(select(Order).where(Order.bar_id == bar_id, Order.id == order_id))
    if not order or order.status == "DRAFT":
        raise LookupError("NOT_FOUND")

    lines = list(
        db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id == order_id)
            .order_by(OrderLine.line_no, OrderLine.id)
        )
    )
    payments = list(
        db.session.scalars(
            select(Payment)
            .where(Payment.bar_id == bar_id, Payment.order_id == order_id)
            .order_by(Payment.received_at, Payment.id)
        )
    )
    refunds = list(
        db.session.scalars(
            select(Refund)
            .where(Refund.bar_id == bar_id, Refund.order_id == order_id)
            .order_by(Refund.refunded_at, Refund.id)
        )
    )
    balance = order_balance(order)

    server_name = "Sans serveuse"
    if order.assigned_staff_id is not None:
        assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.id == order.assigned_staff_id,
            )
        )
        if assignment:
            server = db.session.get(User, assignment.user_id)
            if server:
                server_name = server.display_name

    customer_name = order.customer_name_snapshot
    if not customer_name and order.customer_id is not None:
        customer = db.session.scalar(
            select(Customer).where(Customer.bar_id == bar_id, Customer.id == order.customer_id)
        )
        customer_name = customer.display_name if customer else None

    payment_times = {item.id: _local_display(item.received_at, bar.timezone) for item in payments}
    refund_times = {item.id: _local_display(item.refunded_at, bar.timezone) for item in refunds}
    sale_time = _local_display(order.closed_at or order.updated_at or order.posted_at, bar.timezone)
    issued_at = datetime.now(ZoneInfo(bar.timezone)).strftime("%d/%m/%Y %H:%M")

    return render_template(
        "checkout_receipt.html",
        bar=bar,
        order=order,
        lines=lines,
        payments=payments,
        refunds=refunds,
        balance=balance,
        server_name=server_name,
        customer_name=customer_name,
        payment_times=payment_times,
        refund_times=refund_times,
        payment_labels=PAYMENT_LABELS,
        sale_time=sale_time,
        issued_at=issued_at,
    )


@bp.route("", methods=["GET", "POST"])
@login_required
def checkout(bar_id):
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    assignment = _current_assignment(bar_id)
    is_cashier = bool(assignment and assignment.role == "CASHIER")
    can_record = permissions.evaluate(current_user, "payments.record", bar_id).allowed
    can_deliver = permissions.evaluate(current_user, "orders.deliver", bar_id).allowed
    can_credit = permissions.evaluate(current_user, "customer_credit.manage", bar_id).allowed

    if request.method == "POST":
        action = request.form.get("action", "payment")
        order_id = int(request.form.get("order_id", "0"))
        try:
            if action == "deliver":
                if not can_deliver:
                    raise PermissionError("FORBIDDEN")
                if is_cashier and not _cash_session_open(bar_id):
                    raise ValueError("CASH_SESSION_REQUIRED")
                order = order_service.confirm(current_user, bar_id, order_id)
                db.session.commit()
                flash(
                    f"Commande {order.reference} livrée. Le stock a été déduit et la commande est maintenant à payer.",
                    "success",
                )
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

            if action != "payment":
                raise ValueError("INVALID_ACTION")
            if not can_record:
                raise PermissionError("FORBIDDEN")
            if is_cashier and not _cash_session_open(bar_id):
                raise ValueError("CASH_SESSION_REQUIRED")

            order = db.session.scalar(
                select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
            )
            if not order:
                raise LookupError("NOT_FOUND")
            if order.status not in {"CONFIRMED", "SERVED"}:
                raise ValueError("ORDER_NOT_PAYABLE")
            due_before = order_balance(order)["amount_due"]
            method = request.form.get("method", "").strip()
            base_reference = request.form.get("reference", "").strip() or _reference()
            provider_code = request.form.get("provider_code", "").strip() or None
            provider_transaction_id = request.form.get("provider_transaction_id", "").strip() or None

            if method == "CREDIT":
                if not can_credit:
                    raise PermissionError("FORBIDDEN")
                customer_raw = request.form.get("customer_id", "").strip()
                if not customer_raw:
                    raise LookupError("CUSTOMER_REQUIRED")
                entry = customer_service.credit_order(
                    current_user,
                    bar_id,
                    order_id,
                    int(customer_raw),
                    request.form.get("reference", "").strip() or _reference("CRD"),
                )
                db.session.commit()
                flash(
                    f"Commande {order.reference} portée au compte client pour {entry.amount_delta:,.0f} {entry.currency}.",
                    "success",
                )
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

            if method == "MIXED":
                cash_part = _decimal(request.form.get("mixed_cash"), allow_zero=True)
                mobile_part = _decimal(request.form.get("mixed_mobile"), allow_zero=True)
                if cash_part <= 0 or mobile_part <= 0 or cash_part + mobile_part != due_before:
                    raise ValueError("INVALID_MIXED_PAYMENT")
                presented = _positive_decimal(request.form.get("amount_presented"))
                if presented < cash_part:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                cash_session_id, staff_assignment_id = _cash_location_from_form(bar_id, order)
                if not provider_code or not provider_transaction_id:
                    raise ValueError("PROVIDER_REFERENCE_REQUIRED")

                payment_service.record(
                    current_user,
                    bar_id,
                    order_id,
                    f"{base_reference[:59]}-ESP",
                    "CASH",
                    presented,
                    cash_part,
                    presented - cash_part,
                    cash_session_id=cash_session_id,
                    staff_assignment_id=staff_assignment_id,
                )
                payment_service.record(
                    current_user,
                    bar_id,
                    order_id,
                    f"{base_reference[:58]}-MOMO",
                    "MOBILE_MONEY",
                    mobile_part,
                    mobile_part,
                    Decimal("0"),
                    provider_code=provider_code,
                    provider_transaction_id=provider_transaction_id,
                )
                db.session.commit()
                flash(
                    f"Paiement mixte validé : {cash_part:,.0f} espèces + {mobile_part:,.0f} Mobile Money.",
                    "success",
                )
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

            applied = _positive_decimal(request.form.get("amount_applied"))
            if method == "MOBILE_MONEY" and (not provider_code or not provider_transaction_id):
                raise ValueError("PROVIDER_REFERENCE_REQUIRED")

            staff_assignment_id = None
            if method == "CASH":
                presented = _positive_decimal(request.form.get("amount_presented"))
                if presented < applied:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                change = presented - applied
                cash_session_id, staff_assignment_id = _cash_location_from_form(bar_id, order)
                provider_code = None
                provider_transaction_id = None
            else:
                presented = applied
                change = Decimal("0")
                cash_session_id = None

            payment = payment_service.record(
                current_user,
                bar_id,
                order_id,
                base_reference,
                method,
                presented,
                applied,
                change,
                cash_session_id=cash_session_id,
                staff_assignment_id=staff_assignment_id,
                provider_code=provider_code,
                provider_transaction_id=provider_transaction_id,
            )
            db.session.commit()

            order = db.session.get(Order, order_id)
            remaining = order_balance(order)["amount_due"] if order else Decimal("0")
            if remaining > 0:
                flash(
                    f"Paiement de {payment.amount_applied:,.0f} {payment.currency} enregistré. Reste {remaining:,.0f} {payment.currency}.",
                    "success",
                )
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

            flash(
                f"Commande {order.reference if order else order_id} payée. La serveuse peut maintenant voir la validation du paiement.",
                "success",
            )
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

    active_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .order_by(Order.id.asc())
            .limit(100)
        )
    )
    waiting_orders = [order for order in active_orders if order.status == "DRAFT"]
    payable_orders = [order for order in active_orders if order.status in {"CONFIRMED", "SERVED"}]
    paid_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.payment_status == "PAID",
            )
            .order_by(Order.updated_at.desc(), Order.id.desc())
            .limit(20)
        )
    )
    display_orders = [*active_orders, *paid_orders]
    balances = {order.id: order_balance(order) for order in display_orders}

    order_ids = [order.id for order in display_orders]
    lines_by_order = {order_id: [] for order_id in order_ids}
    if order_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(order_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

    assignment_ids = {order.assigned_staff_id for order in display_orders if order.assigned_staff_id is not None}
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
    for order in display_orders:
        staff = assignments.get(order.assigned_staff_id)
        user = users.get(staff.user_id) if staff else None
        server_name_by_order[order.id] = user.display_name if user else "Sans serveuse"

    selected_order = None
    selected_id = request.args.get("order_id", type=int)
    if selected_id:
        selected_order = next((order for order in display_orders if order.id == selected_id), None)
    if selected_order is None and active_orders:
        selected_order = active_orders[0]
    if selected_order is None and paid_orders:
        selected_order = paid_orders[0]

    selected_server_assignment = assignments.get(selected_order.assigned_staff_id) if selected_order else None
    selected_server_user = users.get(selected_server_assignment.user_id) if selected_server_assignment else None
    selected_server_custody = (
        Decimal(cash_service.custody(bar_id, selected_server_assignment.id) or 0)
        if selected_server_assignment else Decimal("0")
    )

    open_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )
    cash_expected = {session.id: cash_service.expected(session) for session in open_sessions}

    customers = list(
        db.session.scalars(
            select(Customer)
            .where(Customer.bar_id == bar_id, Customer.is_active.is_(True))
            .order_by(Customer.display_name, Customer.id)
        )
    ) if can_credit else []

    recent_payments = list(
        db.session.scalars(
            select(Payment)
            .where(Payment.bar_id == bar_id)
            .order_by(Payment.id.desc())
            .limit(30)
        )
    )
    recent_order_ids = {payment.order_id for payment in recent_payments}
    order_by_id = {
        order.id: order
        for order in db.session.scalars(
            select(Order).where(
                Order.bar_id == bar_id,
                Order.id.in_(recent_order_ids or {-1}),
            )
        )
    }

    total_due = sum((balances[order.id]["amount_due"] for order in payable_orders), Decimal("0"))
    stats = {
        "waiting": len(waiting_orders),
        "to_pay": len(payable_orders),
        "paid_recent": len(paid_orders),
        "due": total_due,
        "open_cash": len(open_sessions),
        "partial": sum(1 for order in payable_orders if order.payment_status == "PARTIAL"),
    }

    return render_template(
        "checkout.html",
        bar=bar,
        orders=active_orders,
        waiting_orders=waiting_orders,
        payable_orders=payable_orders,
        paid_orders=paid_orders,
        balances=balances,
        lines_by_order=lines_by_order,
        selected_order=selected_order,
        server_name_by_order=server_name_by_order,
        selected_server_assignment=selected_server_assignment,
        selected_server_user=selected_server_user,
        selected_server_custody=selected_server_custody,
        open_sessions=open_sessions,
        cash_expected=cash_expected,
        customers=customers,
        recent_payments=recent_payments,
        order_by_id=order_by_id,
        payment_labels=PAYMENT_LABELS,
        can_record=can_record,
        can_deliver=can_deliver,
        can_credit=can_credit,
        is_cashier=is_cashier,
        stats=stats,
    )
