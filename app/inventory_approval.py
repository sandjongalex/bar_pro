"""Inventory V2 human review and bounded approval.

Everything is serialized by the bar row lock before checking live stock.
Approval must be requested explicitly; no cash or historical sales estimates
are modified. Stock adjustment and final status commit atomically.
"""
from decimal import Decimal, InvalidOperation
from sqlalchemy import select

from app.audit import record
from app.extensions import db
from app.inventory_approval_models import InventoryApproval, InventoryApprovalPolicy
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_period_models import InventoryLineSnapshot
from app.inventory_reconciliation import inventory_live_reconciliation
from app.inventory_services import inventory_service
from app.models import Bar, Inventory, InventoryLine, Product, StockMovement, utcnow
from app.permissions import permissions
from app.stock_service import stock_service

ZERO = Decimal("0")
HARD_MAX_UNIT = Decimal("1000000")
HARD_MAX_PRODUCT_VALUE = Decimal("1000000000")
HARD_MAX_TOTAL_VALUE = Decimal("10000000000")
VALID_RECONCILIATIONS = {"RECONCILED", "DIFFERENCE"}


def _limit(value, maximum, *, places):
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_INVENTORY_LIMIT")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n > maximum or n.as_tuple().exponent < -places:
            raise ValueError("INVALID_INVENTORY_LIMIT")
        return n
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise ValueError("INVALID_INVENTORY_LIMIT") from exc


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 400:
        raise ValueError("INVENTORY_REASON_REQUIRED")
    return value.strip()


def _policy(bar_id):
    return db.session.get(InventoryApprovalPolicy, bar_id)


def policy_data(bar_id):
    p = _policy(bar_id)
    return {
        "bar_id": str(bar_id),
        "max_unit_difference": str(p.max_unit_difference if p else ZERO),
        "max_product_value": str(p.max_product_value if p else ZERO),
        "max_total_value": str(p.max_total_value if p else ZERO),
        "configured": p is not None,
    }


def update_policy(actor, bar_id, *, max_unit_difference, max_product_value, max_total_value):
    permissions.require(actor, "inventory.policy", bar_id)
    qty = _limit(max_unit_difference, HARD_MAX_UNIT, places=6)
    product = _limit(max_product_value, HARD_MAX_PRODUCT_VALUE, places=4)
    total = _limit(max_total_value, HARD_MAX_TOTAL_VALUE, places=4)
    old = policy_data(bar_id)
    policy = db.session.scalar(
        select(InventoryApprovalPolicy)
        .where(InventoryApprovalPolicy.bar_id == bar_id).with_for_update()
    )
    if policy is None:
        policy = InventoryApprovalPolicy(bar_id=bar_id, changed_by_id=actor.id)
        db.session.add(policy)
    policy.max_unit_difference = qty
    policy.max_product_value = product
    policy.max_total_value = total
    policy.changed_by_id = actor.id
    record(actor, bar_id, "inventory.policy.change", "inventory_approval_policies",
           bar_id, "Changement des plafonds de validation",
           changes={"before": old, "after": {
               "max_unit_difference": str(qty),
               "max_product_value": str(product),
               "max_total_value": str(total),
           }})
    return policy


def _lock_inventory(bar_id, inventory_id):
    inventory = db.session.scalar(
        select(Inventory).where(Inventory.bar_id == bar_id, Inventory.id == inventory_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if inventory is None:
        raise LookupError("NOT_FOUND")
    assigned = db.session.scalar(
        select(InventoryCashierAssignment).where(
            InventoryCashierAssignment.bar_id == bar_id,
            InventoryCashierAssignment.inventory_id == inventory.id,
        ).with_for_update()
    )
    if not assigned:
        raise ValueError("INVENTORY_V2_REQUIRED")
    review = db.session.scalar(
        select(InventoryApproval).where(
            InventoryApproval.bar_id == bar_id,
            InventoryApproval.inventory_id == inventory.id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    return inventory, assigned, review


def _ready(inv):
    reconciliation = inventory_live_reconciliation(inv, lock=True)
    if reconciliation["total"] == 0 or reconciliation["counted"] != reconciliation["total"]:
        raise ValueError("INVENTORY_COUNT_INCOMPLETE")
    if not reconciliation["baseline_available"]:
        raise ValueError("INVENTORY_RECOUNT_REQUIRED")
    if any(item["status"] not in VALID_RECONCILIATIONS for item in reconciliation["lines"]):
        raise ValueError("INVENTORY_RECOUNT_REQUIRED")
    if db.session.scalar(
        select(StockMovement.id).where(
            StockMovement.bar_id == inv.bar_id,
            StockMovement.inventory_line_id.is_not(None),
            StockMovement.movement_type == "INVENTORY_ADJUSTMENT",
            StockMovement.id > db.session.scalar(
                select(InventoryCashierAssignment.movement_checkpoint_id)
                .where(InventoryCashierAssignment.bar_id == inv.bar_id,
                       InventoryCashierAssignment.inventory_id == inv.id)
            ),
        ).limit(1)
    ) is not None:
        raise ValueError("INVENTORY_RECOUNT_REQUIRED")
    snapshots = {
        line.inventory_line_id: line
        for line in db.session.scalars(
            select(InventoryLineSnapshot).where(
                InventoryLineSnapshot.bar_id == inv.bar_id
            )
        )
    }
    lines = {
        str(line.product_id): line
        for line in db.session.scalars(
            select(InventoryLine).where(
                InventoryLine.bar_id == inv.bar_id,
                InventoryLine.inventory_id == inv.id,
            ).with_for_update()
        )
    }
    products = {
        str(product.id): product for product in db.session.scalars(
            select(Product).where(
                Product.bar_id == inv.bar_id,
                Product.id.in_([line.product_id for line in lines.values()]),
            ).with_for_update()
        )
    }
    summary = []
    total_abs = ZERO
    for item in reconciliation["lines"]:
        product_id = item["product_id"]
        delta = Decimal(item["difference_now"])
        absolute = abs(delta)
        cost = Decimal(products[product_id].valuation_unit_cost)
        amount = (absolute * cost).quantize(Decimal("0.0001"))
        total_abs += amount
        if delta != 0:
            snapshot = snapshots.get(lines[product_id].id)
            if snapshot is None or not snapshot.note or not snapshot.note.strip():
                raise ValueError("INVENTORY_JUSTIFICATION_REQUIRED")
        summary.append({
            "product_id": product_id, "name": item["product_name"],
            "difference_quantity": str(delta),
            "absolute_quantity": absolute,
            "valuation_cost": str(cost), "absolute_value": amount,
            "new_stock": Decimal(item["projected_physical_now"]),
            "status": item["status"],
        })
    return summary, total_abs


def evaluation(actor, bar_id, inventory_id):
    permissions.require(actor, "inventory.read", bar_id)
    inv = inventory_service.get(actor, bar_id, inventory_id)
    review = db.session.scalar(select(InventoryApproval).where(
        InventoryApproval.bar_id == bar_id, InventoryApproval.inventory_id == inventory_id))
    try:
        summary, total = _ready(inv)
        errors = None
    except ValueError as exc:
        summary, total, errors = [], ZERO, str(exc)
    limits = policy_data(bar_id)
    any_exceeded = any(
        item["absolute_quantity"] > Decimal(limits["max_unit_difference"])
        or item["absolute_value"] > Decimal(limits["max_product_value"])
        for item in summary
    ) or total > Decimal(limits["max_total_value"])
    bar = db.session.get(Bar, bar_id)
    has_prior = db.session.scalar(
        select(Inventory.id).where(
            Inventory.bar_id == bar_id, Inventory.status == "POSTED",
            Inventory.id != inv.id,
        ).limit(1)
    ) is not None
    return {
        "inventory_id": str(inv.id),
        "status": review.status if review else "DRAFT",
        "submitted_by": str(review.submitted_by_id) if review else None,
        "decided_by": str(review.decided_by_id) if review and review.decided_by_id else None,
        "error": errors,
        "total_absolute_value": str(total),
        "owner_required": not has_prior or any_exceeded,
        "within_limits": not any_exceeded,
        "first_inventory": not has_prior,
        "policy": limits,
        "lines": [
            {k: str(v) if isinstance(v, Decimal) else v for k, v in item.items()}
            for item in summary
        ],
        "is_owner": actor.category == "OWNER" and bar.owner_id == actor.id,
        "can_approve": permissions.evaluate(actor, "inventory.approve", bar_id).allowed,
    }


def submit(actor, bar_id, inventory_id):
    permissions.require(actor, "inventory.submit", bar_id)
    inv, assigned, review = _lock_inventory(bar_id, inventory_id)
    if inv.status != "DRAFT" or review and review.status not in {"RECOUNT"}:
        raise ValueError("INVENTORY_REVIEW_STATE")
    if inventory_service._is_cashier(actor, bar_id):
        if actor.id != assigned.cashier_user_id:
            raise LookupError("NOT_FOUND")
        from app.shift_service import get_active_shift_for_user
        shift = get_active_shift_for_user(actor.id, bar_id)
        if not shift or shift.id != assigned.cashier_shift_id or assigned.cashier_user_id != actor.id:
            raise PermissionError("INVENTORY_SHIFT_CLOSED")
    _ready(inv)
    if review is None:
        review = InventoryApproval(
            bar_id=bar_id, inventory_id=inv.id,
            submitted_by_id=actor.id, submitted_at=utcnow(), status="SUBMITTED",
        )
        db.session.add(review)
    else:
        review.status, review.submitted_by_id, review.submitted_at = "SUBMITTED", actor.id, utcnow()
        review.decided_by_id = review.decided_at = review.decision_reason = None
    record(actor, bar_id, "inventory.v2.submit", "inventories", inv.id,
           "Comptage soumis au responsable")
    return review


def decide(actor, bar_id, inventory_id, *, decision, reason):
    permissions.require(actor, "inventory.approve", bar_id)
    reason = _reason(reason)
    inv, assigned, review = _lock_inventory(bar_id, inventory_id)
    if inv.status != "DRAFT" or review is None or review.status != "SUBMITTED":
        raise ValueError("INVENTORY_REVIEW_STATE")
    if actor.id == assigned.cashier_user_id:
        raise PermissionError("INVENTORY_SELF_APPROVAL")
    if decision == "recount":
        review.status = "RECOUNT"
        review.decided_by_id, review.decided_at = actor.id, utcnow()
        review.decision_reason = reason
        record(actor, bar_id, "inventory.v2.recount", "inventories", inv.id, reason)
        return review
    if decision != "approve":
        raise ValueError("INVALID_ACTION")
    from app.inventory_finance_services import assert_no_pending_debts
    assert_no_pending_debts(bar_id, inv.id)
    summary, total = _ready(inv)
    bar = db.session.get(Bar, bar_id)
    if actor.category != "OWNER" or bar.owner_id != actor.id:
        limits = policy_data(bar_id)
        first = not db.session.scalar(
            select(Inventory.id).where(
                Inventory.bar_id == bar_id, Inventory.status == "POSTED",
                Inventory.id != inv.id,
            ).limit(1)
        )
        if first or any(
            row["absolute_quantity"] > Decimal(limits["max_unit_difference"])
            or row["absolute_value"] > Decimal(limits["max_product_value"])
            for row in summary
        ) or total > Decimal(limits["max_total_value"]):
            raise PermissionError("INVENTORY_OWNER_APPROVAL_REQUIRED")

    # Bar, inventory, lines and balances are locked in one transaction. A
    # commercial stock write requires the same bar lock through permissions.
    for row in summary:
        delta = Decimal(row["difference_quantity"])
        if delta:
            stock_service.move(actor, bar_id, int(row["product_id"]),
                               "INVENTORY_ADJUSTMENT", delta,
                               f"Inventaire V2 {inv.reference} : {reason}",
                               inventory_line_id=next(
                                   line.id for line in db.session.scalars(
                                       select(InventoryLine).where(
                                           InventoryLine.bar_id == bar_id,
                                           InventoryLine.inventory_id == inv.id,
                                           InventoryLine.product_id == int(row["product_id"]),
                                       )
                                   )
                               ))
    inv.status, inv.posted_at = "POSTED", utcnow()
    review.status, review.decided_by_id, review.decided_at = "POSTED", actor.id, utcnow()
    review.decision_reason = reason
    record(actor, bar_id, "inventory.v2.approve", "inventories", inv.id, reason, changes={
        "total_absolute_value": str(total),
        "reviewed_lines": [
            {"product_id": row["product_id"], "delta": row["difference_quantity"],
             "valuation_cost": row["valuation_cost"], "value": str(row["absolute_value"])}
            for row in summary if Decimal(row["difference_quantity"]) != 0
        ],
    })
    return review
