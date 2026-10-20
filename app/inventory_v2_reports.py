"""Immutable, tenant-scoped inventory V2 review history.

Historical approval corrections are read from posted stock movements, never
recomputed from today's stock balance, product cost, or future sales.
This report does not write operational tables and must not be used as
a cash-accounting statement.
"""
from decimal import Decimal
from sqlalchemy import select

from app.extensions import db
from app.inventory_approval_models import InventoryApproval
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_count_models import InventoryLineCountState
from app.inventory_period_models import InventoryLineSnapshot
from app.inventory_services import inventory_service
from app.models import AuditLog, InventoryLine, Product, StockMovement, User


ACTIONS = {
    "inventory.v2.submit": "Soumission du comptage",
    "inventory.v2.recount": "Demande de recomptage",
    "inventory.v2.approve": "Approbation définitive",
}

STATUS_LABELS = {
    "DRAFT": "Comptage en cours",
    "SUBMITTED": "En attente de validation",
    "RECOUNT": "Recomptage demandé",
    "POSTED": "Approuvé",
    "CANCELLED": "Annulé",
}


def _decimal(value):
    return str(Decimal(value)) if value is not None else None


def _date(value):
    return value.isoformat() if value is not None else None


def inventory_v2_report(actor, bar_id, inventory_id):
    """Render the versioned, read-only report after permission and ownership checks."""
    inv = inventory_service.get(actor, bar_id, inventory_id)
    assignment = db.session.scalar(select(InventoryCashierAssignment).where(
        InventoryCashierAssignment.bar_id == bar_id,
        InventoryCashierAssignment.inventory_id == inv.id,
    ))
    if assignment is None:
        raise ValueError("INVENTORY_V2_REQUIRED")
    approval = db.session.scalar(select(InventoryApproval).where(
        InventoryApproval.bar_id == bar_id,
        InventoryApproval.inventory_id == inv.id,
    ))

    rows = db.session.execute(
        select(InventoryLine, Product)
        .join(Product, (Product.bar_id == InventoryLine.bar_id)
              & (Product.id == InventoryLine.product_id))
        .where(InventoryLine.bar_id == bar_id, InventoryLine.inventory_id == inv.id)
        .order_by(Product.name, Product.id)
    ).all()
    line_ids = [line.id for line, _ in rows]
    count_states = {
        row.inventory_line_id: row
        for row in db.session.scalars(select(InventoryLineCountState).where(
            InventoryLineCountState.bar_id == bar_id,
            InventoryLineCountState.inventory_line_id.in_(line_ids or [-1]),
        ))
    }
    line_notes = {
        row.inventory_line_id: row.note
        for row in db.session.scalars(select(InventoryLineSnapshot).where(
            InventoryLineSnapshot.bar_id == bar_id,
            InventoryLineSnapshot.inventory_line_id.in_(line_ids or [-1]),
        ))
    }

    # Only the actual adjustment movements attached to this inventory can
    # establish historic approved differences. Later sales cannot change them.
    approved_movements = {
        movement.inventory_line_id: movement
        for movement in db.session.scalars(select(StockMovement).where(
            StockMovement.bar_id == bar_id,
            StockMovement.inventory_line_id.in_(line_ids or [-1]),
            StockMovement.movement_type == "INVENTORY_ADJUSTMENT",
        ))
    } if inv.status == "POSTED" else {}

    audit_rows = db.session.scalars(select(AuditLog).where(
        AuditLog.scope == "BAR",
        AuditLog.bar_id == bar_id,
        AuditLog.target_table == "inventories",
        AuditLog.target_id == inv.id,
        AuditLog.action.in_(tuple(ACTIONS)),
        AuditLog.outcome == "SUCCESS",
    ).order_by(AuditLog.occurred_at, AuditLog.id)).all()

    user_ids = {assignment.cashier_user_id, assignment.assigned_by_id}
    if approval:
        user_ids.add(approval.submitted_by_id)
        if approval.decided_by_id:
            user_ids.add(approval.decided_by_id)
    for state in count_states.values():
        user_ids.add(state.counted_by_id)
    for audit in audit_rows:
        user_ids.add(audit.actor_id)
    users = {
        user.id: user.display_name
        for user in db.session.scalars(
            select(User).where(User.id.in_(user_ids))
        )
    }

    lines = []
    total_approved_value = Decimal("0")
    discrepancy_count = 0
    for line, product in rows:
        state = count_states.get(line.id)
        movement = approved_movements.get(line.id)
        approved = inv.status == "POSTED"
        delta = Decimal(movement.quantity_delta) if movement else Decimal("0")
        value = abs(delta) * Decimal(movement.unit_cost_snapshot) if movement else Decimal("0")
        if movement:
            discrepancy_count += 1
            total_approved_value += value
        lines.append({
            "product_id": str(product.id),
            "product_name": product.name,  # Current catalogue name; not a frozen SKU snapshot.
            "sku": product.sku,
            "unit": product.base_unit,
            "start_machine_quantity": _decimal(line.expected_quantity_snapshot),
            "counted_quantity": _decimal(line.counted_quantity),
            "counted_at": _date(state.counted_at) if state else None,
            "counted_by": users.get(state.counted_by_id) if state else None,
            "count_revision": state.revision if state else 0,
            "note": line_notes.get(line.id),
            "approved_delta": _decimal(delta) if approved else None,
            "approved_value": _decimal(value) if approved else None,
            "approval_unit_cost": _decimal(movement.unit_cost_snapshot) if movement else None,
            "stock_movement_id": str(movement.id) if movement else None,
        })

    history = [{
        "action": audit.action,
        "label": ACTIONS[audit.action],
        "actor": users.get(audit.actor_id, "Compte indisponible"),
        "at": _date(audit.occurred_at),
        "reason": audit.reason,
        "id": str(audit.id),
    } for audit in audit_rows]
    status = "POSTED" if inv.status == "POSTED" else (
        approval.status if approval else inv.status
    )
    return {
        "inventory_id": str(inv.id),
        "reference": inv.reference,
        "version": "V2",
        "bar_id": str(bar_id),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "read_only": True,
        "cash_reconciliation_included": False,
        "cashier_name": assignment.cashier_name_snapshot,
        "shift_id": str(assignment.cashier_shift_id),
        "submitted_at": _date(approval.submitted_at) if approval else None,
        "submitted_by": users.get(approval.submitted_by_id) if approval else None,
        "decided_at": _date(approval.decided_at) if approval else None,
        "decided_by": users.get(approval.decided_by_id) if approval and approval.decided_by_id else None,
        "decision_reason": approval.decision_reason if approval else None,
        "created_at": _date(inv.created_at),
        "posted_at": _date(inv.posted_at),
        "product_count": len(lines),
        "counted_count": sum(line["counted_quantity"] is not None for line in lines),
        "approved_discrepancy_count": discrepancy_count if inv.status == "POSTED" else None,
        "approved_absolute_value": _decimal(total_approved_value) if inv.status == "POSTED" else None,
        "valuation_method": "stock_movement_unit_cost_snapshot",
        "historical_note": (
            "Écarts historiques issus des mouvements d'ajustement enregistrés à l'approbation."
            if inv.status == "POSTED" else
            "Document provisoire : aucun ajustement de stock encore approuvé."
        ),
        "lines": lines,
        "history": history,
    }
