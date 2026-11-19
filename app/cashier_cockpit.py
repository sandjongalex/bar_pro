"""Cockpit caissière 2026. All financial/stock writes use BAR_PRO services.

Never infer an opened cash session from the calendar day: it stays OPEN
across midnight until explicitly closed in cashier_web.
"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import secrets

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.datastructures import MultiDict

from app.audit import record
from app.cash_services import cash_service
from app.cashier_payment_idempotency import claim as claim_intent, complete as complete_intent
from app.cashier_performance import build_cashier_performance
from app.cashier_workspace_web import _cashier_assignment, _open_session
from app.change_voucher_models import ChangeVoucher
from app.change_voucher_service import change_voucher_service
from app.customer_models import CustomerLedgerEntry
from app.extensions import db, limiter
from app.finance_totals import order_balance
from app.live_orders_web import orders as live_orders
from app.models import Bar, Expense, Order, Product, StockBalance, utcnow
from app.payment_services import payment_service
from app.permissions import permissions
from app.shift_service import get_active_shift_for_assignment
from app.warehouse_models import WarehouseAllocation, WarehouseTransfer, WarehouseTransferLine
from app.warehouse_notifications import notify_warehouse_request
from app.warehouse_service import warehouse_service

bp = Blueprint("cashier_cockpit", __name__, url_prefix="/bars/<int:bar_id>/cashier/cockpit")
api_bp = Blueprint("cashier_cockpit_api", __name__, url_prefix="/api/caisse")


def response(data=None, message="", status=200, code=None):
    result = {"success": code is None, "data": data or {}, "message": message}
    if code is not None:
        result["error"] = {"code": code}
    reply = jsonify(result)
    reply.status_code = status
    reply.headers["Cache-Control"] = "no-store"
    return reply


def _fail(exc):
    db.session.rollback()
    status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError) else (
        409 if isinstance(exc, (IntegrityError, OperationalError)) or
        str(exc) in {"PAYMENT_ALREADY_PROCESSED", "IDEMPOTENCY_CONFLICT",
                     "RECEIPT_EXCEEDS_TRANSIT", "TRANSFER_ALREADY_RECEIVED"} else 422
    )
    return response(message="Opération refusée : " + str(exc), status=status, code=str(exc)[:80])


def _cashier(bar_id, *, locked=False):
    bar = db.session.get(Bar, bar_id)
    if bar is None:
        abort(404)
    permissions.require(current_user, "payments.read", bar_id)
    permissions.require(current_user, "orders.create", bar_id)
    assignment = _cashier_assignment(bar_id)
    if assignment is None or get_active_shift_for_assignment(bar_id, assignment.id) is None:
        abort(403)
    opened = _open_session(bar_id, lock=locked)
    if opened is None:
        abort(409, description="Ouvrez la caisse pour accéder au cockpit.")
    return bar, assignment, opened


def _money(raw, label):
    if isinstance(raw, bool) or isinstance(raw, (list, dict)) or raw is None:
        raise ValueError("Montant invalide : " + label)
    try:
        val = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Montant invalide : " + label) from exc
    if not val.is_finite() or val < 0 or val != val.to_integral_value() or val > Decimal("999999999999"):
        raise ValueError("Montant FCFA invalide : " + label)
    return val


def _finance(bar, assignment, opened):
    perf = build_cashier_performance(bar.id, current_user.id, assignment.id, bar.timezone)
    expenses = db.session.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.bar_id == bar.id, Expense.cash_session_id == opened.id,
        Expense.entry_kind == "EXPENSE",
    )) or 0
    credits = db.session.scalar(select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
        CustomerLedgerEntry.bar_id == bar.id, CustomerLedgerEntry.entry_kind == "CREDIT_SALE",
        CustomerLedgerEntry.occurred_at >= opened.opened_at,
    )) or 0
    return {
        "sales": str(perf["service_sales"] if perf else 0),
        "collected": str(perf["collected"] - perf["refunds"] if perf else 0),
        "expenses": str(expenses),
        "debts": str(credits),
        "expected_cash": str(cash_service.expected(opened)),
        "session_id": opened.id,
    }


def _stock(bar_id):
    allocation = db.session.scalar(select(WarehouseAllocation).where(WarehouseAllocation.bar_id == bar_id))
    warehouse_live = allocation is not None and allocation.status == "LIVE"
    totals = warehouse_service.totals(bar_id) if warehouse_live else {}
    legacy = {
        row.product_id: Decimal(row.quantity or 0)
        for row in db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar_id))
    } if not warehouse_live else {}
    products = db.session.scalars(select(Product).where(
        Product.bar_id == bar_id, Product.is_active.is_(True),
    ).order_by(Product.name, Product.id))
    return {
        "warehouse_live": warehouse_live,
        "source": "CASHIER" if warehouse_live else "LEGACY",
        "items": [{
            "id": product.id,
            "name": product.name,
            "stock": str(totals.get(product.id, {}).get("CASHIER", 0) if warehouse_live else legacy.get(product.id, 0)),
            "threshold": str(product.stock_alert_threshold or 0),
        } for product in products],
    }


@bp.get("")
@login_required
def cockpit(bar_id):
    # During initial opening, use the existing protected cash-session form.
    if _cashier_assignment(bar_id) is not None and _open_session(bar_id) is None:
        permissions.require(current_user, "cash.operate", bar_id)
        return redirect(url_for("cashier_web.session", bar_id=bar_id))
    bar, assignment, opened = _cashier(bar_id)
    local_open = opened.opened_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(bar.timezone))
    stats = _finance(bar, assignment, opened)
    return render_template(
        "dashboard_caissiere.html", bar=bar,
        session_info={
            "numero": opened.reference,
            "opened_local": local_open.strftime("%d/%m/%Y à %H:%M"),
            "opening_amount": opened.opening_amount,
            "id": opened.id,
        },
        cockpit_stats=stats,
        cockpit_api={
            "orders": url_for("cashier_cockpit.orders", bar_id=bar_id),
            "stock": url_for("cashier_cockpit.stock", bar_id=bar_id),
            "finances": url_for("cashier_cockpit.finances", bar_id=bar_id),
            "pay": url_for("cashier_cockpit_api.pay"),
            "request": url_for("cashier_cockpit.request_stock", bar_id=bar_id),
            "transfers": url_for("cashier_cockpit.transfers", bar_id=bar_id),
            "receive": url_for("cashier_cockpit.receive", bar_id=bar_id),
            "invoice": url_for("cashier_workspace_web.workspace", bar_id=bar_id, sale=1),
            "workspace": url_for("cashier_workspace_web.workspace", bar_id=bar_id),
            "inventories": url_for("inventories.listing", bar_id=bar_id),
            "close": url_for("cashier_web.session", bar_id=bar_id),
        },
    )


@bp.get("/orders")
@limiter.exempt
@login_required
def orders(bar_id):
    _cashier(bar_id)
    # Existing live endpoint already restricts orders to the active cashier.
    payload = live_orders(bar_id).get_json()
    assignment = _cashier_assignment(bar_id)
    known = {int(row["id"]): row for row in payload.get("orders", [])}
    ids = list(known)
    counter = set(db.session.scalars(select(Order.id).where(
        Order.bar_id == bar_id, Order.cashier_assignment_id == assignment.id,
        Order.assigned_staff_id.is_(None), Order.id.in_(ids or [-1]),
    )))
    for item in known.values():
        item["is_counter"] = item["id"] in counter
    vouchers = db.session.execute(
        select(ChangeVoucher, Order).join(Order,
            (Order.bar_id == ChangeVoucher.bar_id) &
            (Order.id == ChangeVoucher.origin_order_id)
        ).where(
            ChangeVoucher.bar_id == bar_id, ChangeVoucher.status.in_(("ACTIVE", "PARTIAL")),
            Order.cashier_assignment_id == assignment.id,
        ).order_by(ChangeVoucher.id.desc()).limit(40)
    ).all()
    for voucher, order in vouchers:
        if order.id in known:
            known[order.id]["open_voucher"] = True
            continue
        known[order.id] = {
            "id": order.id, "reference": order.reference,
            "display_name": order.customer_name_snapshot or order.reference,
            "server_name": "Comptoir", "status": order.status,
            "payment_status": order.payment_status, "state": "voucher",
            "amount_due": "0", "table": order.table_label_snapshot or "Sans table",
            "lines": [], "open_voucher": True,
            "is_counter": order.assigned_staff_id is None,
        }
    payload["orders"] = list(known.values())
    return response(payload)


@bp.get("/stock")
@login_required
def stock(bar_id):
    _cashier(bar_id)
    permissions.require(current_user, "warehouse.cashier.needs.read", bar_id)
    return response(_stock(bar_id))


@bp.get("/finances")
@login_required
def finances(bar_id):
    bar, assignment, opened = _cashier(bar_id)
    permissions.require(current_user, "cash.read", bar_id)
    return response({"financial": _finance(bar, assignment, opened)})


@bp.post("/requests")
@login_required
def request_stock(bar_id):
    _cashier(bar_id)
    permissions.require(current_user, "warehouse.requests.create", bar_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return response(message="JSON requis", status=400, code="INVALID_JSON")
    try:
        product_id = int(payload.get("product_id", 0))
        product = db.session.scalar(select(Product).where(
            Product.bar_id == bar_id, Product.id == product_id, Product.is_active.is_(True),
        ))
        if not product:
            raise LookupError("NOT_FOUND")
        qty = _money(payload.get("quantity"), "Quantité")
        if qty <= 0:
            raise ValueError("Quantité obligatoire")
        note = str(payload.get("note") or "").strip()[:200]
        key = str(payload.get("idempotency_key") or "")
        if len(key) < 12 or len(key) > 128:
            raise ValueError("Clé de demande invalide")
        receipt = claim_intent(current_user, bar_id, "warehouse.request", key, MultiDict([
            ("product", str(product_id)), ("quantity", str(qty)), ("note", note)
        ]), require_key=True)
        reference = "BES-" + secrets.token_hex(8).upper()
        notify_warehouse_request(
            current_user, bar_id, reference,
            f"{product.name} : {qty} bouteille(s). {note}".strip()
        )
        complete_intent(receipt, order_id=0)
        db.session.commit()
        return response({"reference": reference}, "Demande transmise au magasin principal (sans mouvement de stock).")
    except (ValueError, TypeError, PermissionError, LookupError, IntegrityError, OperationalError) as exc:
        return _fail(exc)


@bp.get("/transfers")
@login_required
def transfers(bar_id):
    _cashier(bar_id)
    permissions.require(current_user, "warehouse.transfers.read", bar_id)
    transfers = list(db.session.scalars(select(WarehouseTransfer).where(
        WarehouseTransfer.bar_id == bar_id,
        WarehouseTransfer.status.in_(("IN_TRANSIT", "PARTIAL", "DISPUTED")),
    ).order_by(WarehouseTransfer.id.desc()).limit(30)))
    ids = [item.id for item in transfers]
    lines = db.session.scalars(select(WarehouseTransferLine).where(
        WarehouseTransferLine.bar_id == bar_id,
        WarehouseTransferLine.transfer_id.in_(ids or [-1]),
    ))
    products = {p.id: p.name for p in db.session.scalars(select(Product).where(Product.bar_id == bar_id))}
    by_transfer = {row.id: [] for row in transfers}
    for line in lines:
        remaining = Decimal(line.quantity_dispatched) - Decimal(line.quantity_received) - Decimal(line.quantity_disputed)
        if remaining > 0:
            by_transfer[line.transfer_id].append({
                "product_id": line.product_id, "name": products.get(line.product_id, "Produit"),
                "remaining": str(remaining),
            })
    return response({"transfers": [{
        "id": item.id, "reference": item.reference, "status": item.status,
        "lines": by_transfer[item.id],
    } for item in transfers if by_transfer[item.id]]})


@bp.post("/receive")
@login_required
def receive(bar_id):
    _cashier(bar_id, locked=True)
    permissions.require(current_user, "warehouse.transfers.receive", bar_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return response(message="JSON requis", status=400, code="INVALID_JSON")
    try:
        quantities = payload.get("quantities")
        if not isinstance(quantities, dict) or not quantities:
            raise ValueError("Quantités manquantes")
        lines = {int(pid): str(_money(qty, "Quantité")) for pid, qty in quantities.items()}
        lines = {pid: qty for pid, qty in lines.items() if Decimal(qty) > 0}
        if not lines:
            raise ValueError("Aucune bouteille reçue")
        receipt = warehouse_service.receive(
            current_user, bar_id, int(payload.get("transfer_id", 0)),
            lines, str(payload.get("idempotency_key") or "")
        )
        db.session.commit()
        return response({"receipt_id": receipt.id}, "Réception physique enregistrée dans CASHIER.")
    except (ValueError, TypeError, PermissionError, LookupError, IntegrityError, OperationalError) as exc:
        return _fail(exc)


@api_bp.post("/encaisser")
@login_required
def pay():
    """Atomic cash / MTN / Orange payment, never trust a browser-calculated balance."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return response(message="JSON requis", status=400, code="INVALID_JSON")
    try:
        bar_id = int(payload.get("bar_id", 0))
        bar, assignment, opened = _cashier(bar_id, locked=True)
        permissions.require(current_user, "payments.record", bar_id)
        order_id = int(payload.get("order_id", 0))
        order = db.session.scalar(select(Order).where(
            Order.bar_id == bar_id, Order.id == order_id,
            Order.cashier_assignment_id == assignment.id,
        ).with_for_update())
        if order is None:
            raise LookupError("NOT_FOUND")
        if order.status not in {"CONFIRMED", "SERVED"} or order.payment_status == "PAID" or order.closed_at:
            raise ValueError("ORDER_NOT_PAYABLE")
        due = Decimal(order_balance(order, locking=True)["amount_due"] or 0)
        if due <= 0 or due != _money(payload.get("expected_due"), "Solde attendu"):
            raise ValueError("Le solde de la facture a changé : actualisez.")
        cash, mtn, orange = (
            _money(payload.get(name), name) for name in ("especes", "mtn", "orange")
        )
        given_change = _money(payload.get("cash_change_given"), "Monnaie rendue")
        issue_voucher = payload.get("issue_voucher") is True
        advance = payload.get("retain_advance") is True
        if issue_voucher and advance:
            raise ValueError("Bon de monnaie et avance ne peuvent pas être simultanés")
        if mtn + orange > due:
            raise ValueError("Mobile Money supérieur au montant dû")
        cash_applied = min(cash, due - mtn - orange)
        excess = cash - cash_applied
        if mtn + orange + cash_applied <= 0:
            raise ValueError("Montant nul")
        if excess > 0 and cash_applied == 0:
            raise ValueError("La monnaie ne peut pas être générée par Mobile Money")
        if advance and (order.assigned_staff_id is not None or order.closed_at is not None):
            raise ValueError("Avance réservée aux factures comptoir ouvertes")
        if issue_voucher or advance:
            if excess <= 0 or given_change != 0:
                raise ValueError("Bon ou avance invalide")
            liability = excess
        else:
            if given_change != excess:
                raise ValueError("Montant de monnaie rendue incohérent")
            liability = Decimal(0)
        key = str(payload.get("idempotency_key") or "")
        if not 12 <= len(key) <= 128:
            raise ValueError("Clé d'idempotence invalide")
        receipt = claim_intent(current_user, bar_id, "cockpit.payment", key, MultiDict([
            ("order", str(order_id)), ("due", str(due)), ("cash", str(cash)),
            ("mtn", str(mtn)), ("orange", str(orange)),
            ("change", str(given_change)), ("voucher", str(issue_voucher)),
            ("advance", str(advance)),
        ]), require_key=True)
        reference = "CP-" + secrets.token_hex(10).upper()
        for provider, amount in (("MTN", mtn), ("ORANGE", orange)):
            if amount:
                payment_service.record(
                    current_user, bar_id, order_id, reference + "-" + provider,
                    "MOBILE_MONEY", amount, amount, 0, provider_code=provider
                )
        voucher = None
        if cash_applied:
            payment = payment_service.record(
                current_user, bar_id, order_id, reference + "-ESP", "CASH",
                cash, cash_applied, given_change,
                cash_session_id=opened.id, voucher_amount=liability,
                cash_received_from="client directement",
            )
            if liability:
                voucher = change_voucher_service.issue(
                    current_user, bar_id, payment.id, liability,
                    customer_name=order.customer_name_snapshot, customer_advance=advance
                )
        remaining = Decimal(order_balance(order, update=True, locking=True)["amount_due"] or 0)
        if not remaining and not advance:
            order.closed_at = utcnow()
            record(current_user, bar_id, "orders.cockpit.close", "orders", order.id,
                   "Facture réglée et verrouillée")
        complete_intent(receipt, order_id=order_id)
        db.session.commit()
        return response({
            "remaining": str(remaining),
            "payment_finalized": remaining == 0 and not advance,
            "order_open": bool(advance),
            "receipt_url": url_for("checkout_web.receipt", bar_id=bar_id, order_id=order_id),
            "voucher_url": url_for(
                "change_vouchers_web.print_voucher", bar_id=bar_id, voucher_id=voucher.id
            ) if voucher is not None and not advance else None,
        }, "Paiement enregistré et validé.")
    except (ValueError, TypeError, PermissionError, LookupError, IntegrityError, OperationalError) as exc:
        return _fail(exc)
