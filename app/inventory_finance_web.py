"""POST-only inventory financial reconciliation actions."""
from flask import Blueprint, flash, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_finance_services import (
    count_cash, add_expense, request_debt, decide_debt,
)
from app.inventory_services import inventory_service
from app.inventories import _message


bp = Blueprint(
    "inventories_finance", __name__,
    url_prefix="/bars/<int:bar_id>/inventories/<int:inventory_id>/finance",
)


@bp.post("")
@login_required
def act(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    action = (request.form.get("action") or "").strip()
    try:
        if action == "count_cash":
            count_cash(current_user, bar_id, inventory_id,
                       request.form.get("counted_amount", ""),
                       request.form.get("reason", ""))
            message = "Recette physique enregistrée. Écart de caisse calculé."
        elif action == "expense":
            add_expense(
                current_user, bar_id, inventory_id,
                category_id=int(request.form.get("category_id", 0)),
                amount=request.form.get("amount", ""),
                description=request.form.get("description", ""),
                method=request.form.get("method", ""),
                nonce=request.form.get("nonce", ""),
            )
            message = "Dépense oubliée enregistrée dans les écritures de caisse."
        elif action == "debt_request":
            request_debt(
                current_user, bar_id, inventory_id,
                customer_id=int(request.form.get("customer_id", 0)),
                order_id=request.form.get("order_id", ""),
                amount=request.form.get("amount", ""),
                reason=request.form.get("reason", ""),
                nonce=request.form.get("nonce", ""),
            )
            message = "Dette signalée : confirmation du propriétaire requise."
        elif action in ("approve_debt", "reject_debt"):
            decide_debt(
                current_user, bar_id, inventory_id,
                request_id=int(request.form.get("request_id", 0)),
                decision="approve" if action == "approve_debt" else "reject",
                reason=request.form.get("reason", ""),
            )
            message = "Décision du propriétaire enregistrée."
        else:
            raise ValueError("INVALID_ACTION")
        db.session.commit()
        flash(message, "success")
    except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
        db.session.rollback()
        flash(_message(exc), "danger")

    v2 = db.session.scalar(select(InventoryCashierAssignment.id).where(
        InventoryCashierAssignment.bar_id == bar_id,
        InventoryCashierAssignment.inventory_id == inventory_id,
    )) is not None
    refreshed = inventory_service.get(current_user, bar_id, inventory_id)
    endpoint = (
        ("inventories.v2_count" if refreshed.status == "DRAFT"
         else "inventories.v2_report") if v2 else "inventories.detail"
    )
    return redirect(url_for(endpoint, bar_id=bar_id, inventory_id=inventory_id,
                            _anchor="finance-inventory"))
