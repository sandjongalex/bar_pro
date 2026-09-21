"""Fast cashier workspace: counter sale, order queue and payment in one screen."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
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
)
from app.order_services import order_service
from app.payment_services import payment_service
from app.permissions import permissions
from app.product_display_order import product_order_expression

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


def _open_session(bar_id: int):
    return db.session.scalar(
        select(CashSession)
        .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
        .order_by(CashSession.id.desc())
    )


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
        "INVALID_MIXED_PAYMENT": "Les parts espèces et Mobile Money doivent totaliser exactement le reste à payer.",
        "PROVIDER_REFERENCE_REQUIRED": "Renseignez le prestataire et la référence Mobile Money.",
        "CUSTOMER_REQUIRED": "Sélectionnez un client pour une vente à crédit.",
        "CREDIT_DISABLED": "Les ventes à crédit sont désactivées.",
        "CASH_SESSION_REQUIRED": "Ouvrez d'abord votre session de caisse.",
        "INSUFFICIENT_STOCK": "Stock insuffisant pour valider cette vente.",
        "FORBIDDEN": "Opération non autorisée.",
        "NOT_FOUND": "Commande ou produit introuvable.",
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
    session = _open_session(bar_id)
    if session is None:
        flash("Ouvrez votre caisse avant de commencer le service.", "info")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        order_id = request.form.get("order_id", type=int)
        try:
            if action == "create_sale":
                order = order_service.create(
                    current_user,
                    bar_id,
                    (request.form.get("reference") or "").strip() or _reference("CMD"),
                    _cart_lines(),
                    notes=(request.form.get("notes") or "").strip() or None,
                )
                # A counter sale is physically delivered by the cashier at creation time.
                order_service.confirm(current_user, bar_id, order.id)
                db.session.commit()
                flash(f"Vente comptoir prête à encaisser · {order.total_amount:,.0f} {order.currency}.", "success")
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment"))

            if not order_id:
                raise LookupError("NOT_FOUND")

            if action == "deliver":
                order = order_service.confirm(current_user, bar_id, order_id)
                db.session.commit()
                flash(f"{order.reference} livrée. Encaissez maintenant le client.", "success")
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment"))

            order = db.session.scalar(
                select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
            )
            if not order:
                raise LookupError("NOT_FOUND")
            if order.status not in {"CONFIRMED", "SERVED"}:
                raise ValueError("ORDER_NOT_PAYABLE")
            due = Decimal(order_balance(order)["amount_due"] or 0)
            if due <= 0:
                raise ValueError("ORDER_NOT_PAYABLE")

            if action == "cash_exact":
                payment_service.record(
                    current_user,
                    bar_id,
                    order.id,
                    _reference(),
                    "CASH",
                    due,
                    due,
                    0,
                    cash_session_id=session.id,
                )
                db.session.commit()
                flash(f"{order.reference} encaissée en espèces · {due:,.0f} {order.currency}.", "success")
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))

            if action != "payment":
                raise ValueError("INVALID_ACTION")

            method = (request.form.get("method") or "CASH").strip().upper()
            base_reference = (request.form.get("reference") or "").strip() or _reference()

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
                db.session.commit()
                flash(f"{order.reference} soldée à crédit.", "success")
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))

            if method == "MIXED":
                cash_part = _decimal(request.form.get("mixed_cash"), allow_zero=True)
                mobile_part = _decimal(request.form.get("mixed_mobile"), allow_zero=True)
                if cash_part <= 0 or mobile_part <= 0 or cash_part + mobile_part != due:
                    raise ValueError("INVALID_MIXED_PAYMENT")
                presented = _decimal(request.form.get("amount_presented"))
                if presented < cash_part:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                provider_code = (request.form.get("provider_code") or "").strip()
                provider_transaction_id = (request.form.get("provider_transaction_id") or "").strip()
                if not provider_code or not provider_transaction_id:
                    raise ValueError("PROVIDER_REFERENCE_REQUIRED")
                cash_session_id, staff_assignment_id = _cash_destination(order, session)
                payment_service.record(
                    current_user,
                    bar_id,
                    order.id,
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
                    order.id,
                    f"{base_reference[:58]}-MOMO",
                    "MOBILE_MONEY",
                    mobile_part,
                    mobile_part,
                    0,
                    provider_code=provider_code,
                    provider_transaction_id=provider_transaction_id,
                )
                db.session.commit()
                flash(f"{order.reference} encaissée en paiement mixte.", "success")
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))

            applied = _decimal(request.form.get("amount_applied") or due)
            if applied > due:
                raise ValueError("PAYMENT_LIMIT_EXCEEDED")

            provider_code = None
            provider_transaction_id = None
            cash_session_id = None
            staff_assignment_id = None
            if method == "CASH":
                presented = _decimal(request.form.get("amount_presented") or applied)
                if presented < applied:
                    raise ValueError("INVALID_PAYMENT_AMOUNTS")
                change = presented - applied
                cash_session_id, staff_assignment_id = _cash_destination(order, session)
            else:
                presented = applied
                change = Decimal("0")
                if method == "MOBILE_MONEY":
                    provider_code = (request.form.get("provider_code") or "").strip()
                    provider_transaction_id = (request.form.get("provider_transaction_id") or "").strip()
                    if not provider_code or not provider_transaction_id:
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
            )
            db.session.commit()
            remaining = Decimal(order_balance(db.session.get(Order, order.id))["amount_due"] or 0)
            if remaining > 0:
                flash(f"{payment.amount_applied:,.0f} {payment.currency} encaissés · reste {remaining:,.0f}.", "success")
                return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id, focus="payment"))
            flash(f"{order.reference} payée. Commande suivante prête.", "success")
            return redirect(url_for("cashier_workspace_web.workspace", bar_id=bar_id))

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                message = "Cette référence existe déjà. Réessayez."
            else:
                message = _message(exc)
            flash(message, "danger")
            target = {"bar_id": bar_id}
            if order_id:
                target["order_id"] = order_id
            if action == "create_sale":
                target["sale"] = 1
            return redirect(url_for("cashier_workspace_web.workspace", **target))

    active_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .order_by(Order.id.asc())
            .limit(120)
        )
    )
    waiting_orders = [item for item in active_orders if item.status == "DRAFT"]
    payable_orders = [item for item in active_orders if item.status in {"CONFIRMED", "SERVED"}]

    selected_order = None
    selected_id = request.args.get("order_id", type=int)
    if selected_id:
        selected_order = next((item for item in active_orders if item.id == selected_id), None)
    if selected_order is None and payable_orders:
        selected_order = payable_orders[0]
    if selected_order is None and waiting_orders:
        selected_order = waiting_orders[0]

    order_ids = [item.id for item in active_orders]
    lines_by_order = {order_id: [] for order_id in order_ids}
    if order_ids:
        for line in db.session.scalars(
            select(OrderLine)
            .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(order_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
        ):
            lines_by_order.setdefault(line.order_id, []).append(line)

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
            .order_by(product_order_expression(Product.name), Product.name, Product.id)
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

    return render_template(
        "cashier_workspace_v2.html",
        bar=bar,
        session=session,
        waiting_orders=waiting_orders,
        payable_orders=payable_orders,
        selected_order=selected_order,
        selected_server_assignment=selected_server_assignment,
        lines_by_order=lines_by_order,
        balances=balances,
        server_name_by_order=server_name_by_order,
        products=products,
        categories=categories,
        categories_by_id=categories_by_id,
        stock_balances=stock_balances,
        customers=customers,
        payment_labels=PAYMENT_LABELS,
        stats=stats,
        sale_mode=request.args.get("sale") == "1",
        focus_payment=request.args.get("focus") == "payment",
    )