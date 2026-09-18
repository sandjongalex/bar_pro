"""Fast server-rendered cashier queue and checkout for bar orders."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, CashSession, Order, OrderLine, Payment, StaffAssignment, User
from app.order_services import order_service
from app.payment_services import payment_service
from app.permissions import permissions

bp = Blueprint("checkout_web", __name__, url_prefix="/bars/<int:bar_id>/checkout")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "MIXED": "Paiement mixte",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _reference() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"PAY-{stamp}-{secrets.token_hex(2).upper()}"


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


def _message(code) -> str:
    messages = {
        "NOT_FOUND": "Commande introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à effectuer cette opération.",
        "BAR_SUSPENDED": "Le bar est suspendu : les encaissements sont bloqués.",
        "ORDER_NOT_DRAFT": "Cette commande a déjà été livrée ou annulée.",
        "ORDER_NOT_PAYABLE": "La commande doit d'abord être marquée Livrée.",
        "PAYMENT_LIMIT_EXCEEDED": "Le montant dépasse le reste à payer.",
        "INVALID_PAYMENT_AMOUNTS": "Vérifiez le montant encaissé et le montant reçu.",
        "INVALID_MIXED_PAYMENT": "Le paiement mixte doit répartir exactement le reste entre espèces et Mobile Money.",
        "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
        "CASH_LOCATION_REQUIRED": "Sélectionnez une caisse ouverte pour un paiement en espèces.",
        "CASH_SESSION_REQUIRED": "Ouvrez d'abord une session de caisse avant de traiter les commandes.",
        "CASH_SESSION_NOT_OPEN": "La caisse sélectionnée n'est plus ouverte.",
        "CURRENCY_MISMATCH": "La devise de la caisse ne correspond pas à celle de la commande.",
        "PROVIDER_REFERENCE_REQUIRED": "Pour Mobile Money, renseignez le prestataire et la référence de transaction.",
        "INVALID_NONCASH_PAYMENT": "Les informations du paiement non espèces sont invalides.",
        "INSUFFICIENT_STOCK": "Stock insuffisant : la livraison ne peut pas être confirmée.",
    }
    return messages.get(str(code), "Opération refusée. Vérifiez les informations saisies.")


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

            if method == "MIXED":
                cash_part = _decimal(request.form.get("mixed_cash"), allow_zero=True)
                mobile_part = _decimal(request.form.get("mixed_mobile"), allow_zero=True)
                if cash_part <= 0 or mobile_part <= 0 or cash_part + mobile_part != due_before:
                    raise ValueError("INVALID_MIXED_PAYMENT")
                presented = _positive_decimal(request.form.get("amount_presented"))
                if presented < cash_part:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                cash_session_raw = request.form.get("cash_session_id", "").strip()
                cash_session_id = int(cash_session_raw) if cash_session_raw else None
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
                return redirect(url_for("checkout_web.checkout", bar_id=bar_id))

            applied = _positive_decimal(request.form.get("amount_applied"))
            if method == "MOBILE_MONEY" and (not provider_code or not provider_transaction_id):
                raise ValueError("PROVIDER_REFERENCE_REQUIRED")

            if method == "CASH":
                presented = _positive_decimal(request.form.get("amount_presented"))
                if presented < applied:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                change = presented - applied
                cash_session_raw = request.form.get("cash_session_id", "").strip()
                cash_session_id = int(cash_session_raw) if cash_session_raw else None
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
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id))

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette référence existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")
            return redirect(url_for("checkout_web.checkout", bar_id=bar_id, order_id=order_id))

    orders = list(
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
    balances = {order.id: order_balance(order) for order in orders}

    order_ids = [order.id for order in orders]
    lines_by_order = {order_id: [] for order_id in order_ids}
    if order_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(order_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

    assignment_ids = {order.assigned_staff_id for order in orders if order.assigned_staff_id is not None}
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
    for order in orders:
        staff = assignments.get(order.assigned_staff_id)
        user = users.get(staff.user_id) if staff else None
        server_name_by_order[order.id] = user.display_name if user else "Sans serveuse"

    selected_order = None
    selected_id = request.args.get("order_id", type=int)
    if selected_id:
        selected_order = next((order for order in orders if order.id == selected_id), None)
    if selected_order is None and orders:
        selected_order = orders[0]

    open_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )
    cash_expected = {session.id: cash_service.expected(session) for session in open_sessions}

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

    total_due = sum((balances[order.id]["amount_due"] for order in orders if order.status != "DRAFT"), Decimal("0"))
    stats = {
        "waiting": sum(1 for order in orders if order.status == "DRAFT"),
        "to_pay": sum(1 for order in orders if order.status in {"CONFIRMED", "SERVED"}),
        "due": total_due,
        "open_cash": len(open_sessions),
        "partial": sum(1 for order in orders if order.payment_status == "PARTIAL"),
    }

    return render_template(
        "checkout.html",
        bar=bar,
        orders=orders,
        balances=balances,
        lines_by_order=lines_by_order,
        selected_order=selected_order,
        server_name_by_order=server_name_by_order,
        open_sessions=open_sessions,
        cash_expected=cash_expected,
        recent_payments=recent_payments,
        order_by_id=order_by_id,
        payment_labels=PAYMENT_LABELS,
        can_record=can_record,
        can_deliver=can_deliver,
        is_cashier=is_cashier,
        stats=stats,
    )
