"""Warehouse-only read surface and owner-managed delegated grants.

PROMPT 2 does not make requests, dispatch or inventory mutation public.
"""
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.customer_models import UserNotification
from app.extensions import db
from app.models import Bar, Product, Purchase, PurchaseLine, StaffAssignment
from app.permissions import permissions
from app.warehouse_access import WAREHOUSE_DELEGABLE
from app.warehouse_models import WarehouseAllocation, WarehouseTransfer, WarehouseTransferLine
from app.warehouse_purchase_models import WarehousePurchaseReceiptLine
from app.purchase_services import purchase_service
from app.warehouse_permission_models import WarehouseStaffGrant
from app.warehouse_permission_service import set_warehouse_grant
from app.warehouse_service import warehouse_service

bp = Blueprint("warehouse_web", __name__, url_prefix="/bars/<int:bar_id>/warehouse")


@bp.get("")
@login_required
def overview(bar_id):
    permissions.require(current_user, "bars.read", bar_id)
    checks = {
        code: permissions.evaluate(current_user, f"warehouse.{code}", bar_id).allowed
        for code in (
            "main.read",
            "cashier.needs.read",
            "transfers.read",
            "notifications.read",
            "discrepancies.read",
            "purchases.receive",
        )
    }
    if not any(checks.values()):
        abort(403)
    bar = db.session.get(Bar, bar_id)
    if bar is None:
        abort(404)
    allocation = db.session.scalar(
        select(WarehouseAllocation).where(WarehouseAllocation.bar_id == bar_id)
    )
    totals = warehouse_service.totals(bar_id) if allocation else {}
    products = list(db.session.scalars(
        select(Product).where(Product.bar_id == bar_id).order_by(Product.name, Product.id)
    )) if checks["main.read"] or checks["cashier.needs.read"] else []
    rows = []
    for product in products:
        amounts = totals.get(product.id, {})
        main = Decimal(amounts.get("MAIN", 0))
        cashier = Decimal(amounts.get("CASHIER", 0))
        rows.append({
            "product": product,
            "main": main if checks["main.read"] else None,
            "cashier": cashier if checks["cashier.needs.read"] else None,
            "needs_restock": checks["cashier.needs.read"] and cashier <= product.stock_alert_threshold,
        })
    transfers = list(db.session.scalars(
        select(WarehouseTransfer)
        .where(WarehouseTransfer.bar_id == bar_id)
        .order_by(WarehouseTransfer.id.desc()).limit(30)
    )) if checks["transfers.read"] else []
    disputes = list(db.session.scalars(
        select(WarehouseTransferLine)
        .where(
            WarehouseTransferLine.bar_id == bar_id,
            WarehouseTransferLine.quantity_disputed > 0,
        ).order_by(WarehouseTransferLine.id.desc()).limit(30)
    )) if checks["discrepancies.read"] else []
    notifications = list(db.session.scalars(
        select(UserNotification)
        .where(
            UserNotification.bar_id == bar_id,
            UserNotification.user_id == current_user.id,
            UserNotification.kind == "WAREHOUSE_REQUEST",
            UserNotification.read_at.is_(None),
        ).order_by(UserNotification.id.desc()).limit(30)
    )) if checks["notifications.read"] else []
    # Only warehouse quantities, never payment amounts, are exposed here.
    pending_purchases = []
    pending_lines = {}
    if checks["purchases.receive"]:
        pending_purchases = list(db.session.scalars(
            select(Purchase).where(Purchase.bar_id == bar_id, Purchase.status == "DRAFT")
            .order_by(Purchase.id.desc()).limit(30)
        ))
        ids = [p.id for p in pending_purchases]
        if ids:
            for line in db.session.scalars(
                select(PurchaseLine).where(PurchaseLine.bar_id == bar_id,
                    PurchaseLine.purchase_id.in_(ids))
                .order_by(PurchaseLine.purchase_id, PurchaseLine.line_no)
            ):
                pending_lines.setdefault(line.purchase_id, []).append(line)
    recent_receipts = list(db.session.execute(
        select(WarehousePurchaseReceiptLine, Purchase.reference, Product.name)
        .join(PurchaseLine, (PurchaseLine.bar_id == WarehousePurchaseReceiptLine.bar_id)
              & (PurchaseLine.id == WarehousePurchaseReceiptLine.purchase_line_id))
        .join(Purchase, (Purchase.bar_id == PurchaseLine.bar_id)
              & (Purchase.id == PurchaseLine.purchase_id))
        .join(Product, (Product.bar_id == WarehousePurchaseReceiptLine.bar_id)
              & (Product.id == WarehousePurchaseReceiptLine.product_id))
        .where(WarehousePurchaseReceiptLine.bar_id == bar_id)
        .order_by(WarehousePurchaseReceiptLine.id.desc()).limit(30)
    ).all()) if checks["main.read"] else []
    return render_template(
        "warehouse/overview.html", bar=bar, allocation=allocation,
        checks=checks, rows=rows, transfers=transfers, disputes=disputes,
        notifications=notifications, pending_purchases=pending_purchases,
        pending_lines=pending_lines, recent_receipts=recent_receipts,
    )


@bp.route("/permissions", methods=["GET", "POST"])
@login_required
def grants(bar_id):
    permissions.require(current_user, "warehouse.permissions.manage", bar_id)
    bar = db.session.get(Bar, bar_id)
    if bar is None:
        abort(404)
    if request.method == "POST":
        try:
            raw = request.form.get("enabled")
            if raw not in {"0", "1"}:
                raise ValueError("INVALID_GRANT_STATE")
            set_warehouse_grant(
                current_user, bar_id,
                int(request.form.get("assignment_id", "0")),
                request.form.get("action", ""),
                raw == "1",
            )
            db.session.commit()
            flash("Autorisation magasin mise à jour.", "success")
        except (ValueError, PermissionError, LookupError) as exc:
            db.session.rollback()
            flash(f"Modification refusée : {exc}", "danger")
        return redirect(url_for("warehouse_web.grants", bar_id=bar_id))
    assignments = list(db.session.scalars(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.ended_at.is_(None),
            StaffAssignment.role.in_(("BAR_ADMIN", "STOREKEEPER")),
        ).order_by(StaffAssignment.role, StaffAssignment.id)
    ))
    grants = set(db.session.execute(
        select(WarehouseStaffGrant.staff_assignment_id, WarehouseStaffGrant.action)
        .where(WarehouseStaffGrant.bar_id == bar_id, WarehouseStaffGrant.enabled.is_(True))
    ).all())
    return render_template(
        "warehouse/grants.html", bar=bar, assignments=assignments,
        grants=grants, delegable=WAREHOUSE_DELEGABLE,
    )

@bp.post("/purchases/<int:purchase_id>/receive")
@login_required
def receive_supplier_purchase(bar_id, purchase_id):
    """Confirm physical supplier receipt without exposing payment records."""
    permissions.require(current_user, "warehouse.purchases.receive", bar_id)
    try:
        purchase = purchase_service.receive(current_user, bar_id, purchase_id)
        db.session.commit()
        flash(f"Achat {purchase.reference} réceptionné au magasin principal.", "success")
    except (PermissionError, LookupError, ValueError) as exc:
        db.session.rollback()
        flash(f"Réception refusée : {exc}", "danger")
    return redirect(url_for("warehouse_web.overview", bar_id=bar_id))
