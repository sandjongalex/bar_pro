"""Manage warehouse-specific permissions without mutating existing employee accounts."""
from sqlalchemy import select

from app.audit import record
from app.extensions import db
from app.models import Bar, StaffAssignment, utcnow
from app.permissions import permissions
from app.warehouse_access import WAREHOUSE_DELEGABLE
from app.warehouse_permission_models import WarehouseStaffGrant


def set_warehouse_grant(actor, bar_id, assignment_id, action, enabled):
    """Only the owner can authorize/revoke a BAR_ADMIN warehouse operation.

    STOREKEEPER may receive only an *additional* supplier-purchase capability.
    No grant can enable sales, cash operations, dispute approval or reverse
    warehouse transfers. Caller commits the transaction.
    """
    permissions.require(actor, "warehouse.permissions.manage", bar_id)
    bar = db.session.scalar(select(Bar).where(Bar.id == bar_id).with_for_update())
    if not bar or actor.category != "OWNER" or bar.owner_id != actor.id:
        raise PermissionError("OWNER_REQUIRED")
    if action not in WAREHOUSE_DELEGABLE:
        raise ValueError("UNKNOWN_WAREHOUSE_GRANT")
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.id == assignment_id,
            StaffAssignment.ended_at.is_(None),
        ).with_for_update()
    )
    if not assignment:
        raise LookupError("NOT_FOUND")
    if assignment.role not in {"BAR_ADMIN", "STOREKEEPER"}:
        raise ValueError("WAREHOUSE_GRANT_ROLE_INVALID")
    if assignment.role == "STOREKEEPER" and action not in {
        "warehouse.purchases.manage", "warehouse.purchases.receive",
    }:
        raise ValueError("STOREKEEPER_ONLY_SPECIAL_PURCHASING_OR_RECEIPT")
    if not isinstance(enabled, bool):
        raise ValueError("INVALID_GRANT_STATE")
    grant = db.session.scalar(
        select(WarehouseStaffGrant).where(
            WarehouseStaffGrant.bar_id == bar_id,
            WarehouseStaffGrant.staff_assignment_id == assignment.id,
            WarehouseStaffGrant.action == action,
        ).with_for_update()
    )
    if grant and bool(grant.enabled) == enabled:
        return grant

    now = utcnow()
    if grant is None:
        if not enabled:
            return None
        grant = WarehouseStaffGrant(
            bar_id=bar_id,
            staff_assignment_id=assignment.id,
            action=action,
            enabled=True,
            granted_by_id=actor.id,
            granted_at=now,
        )
        db.session.add(grant)
    elif enabled:
        grant.enabled = True
        grant.granted_by_id = actor.id
        grant.granted_at = now
        grant.revoked_by_id = None
        grant.revoked_at = None
    else:
        grant.enabled = False
        grant.revoked_by_id = actor.id
        grant.revoked_at = now

    db.session.flush()
    record(
        actor, bar_id,
        "warehouse.grant.enable" if enabled else "warehouse.grant.revoke",
        "warehouse_staff_grants", grant.id,
        f"Employé {assignment.user_id}: {action}",
    )
    return grant


def revoke_warehouse_grants_on_role_change(actor, assignment):
    """A later reassignment must never resurrect previous delegated privileges."""
    for grant in db.session.scalars(
        select(WarehouseStaffGrant).where(
            WarehouseStaffGrant.bar_id == assignment.bar_id,
            WarehouseStaffGrant.staff_assignment_id == assignment.id,
            WarehouseStaffGrant.enabled.is_(True),
        ).with_for_update()
    ):
        grant.enabled = False
        grant.revoked_at = utcnow()
        grant.revoked_by_id = actor.id
        record(
            actor, assignment.bar_id, "warehouse.grant.revoke",
            "warehouse_staff_grants", grant.id,
            f"Révocation à la modification d'affectation : {grant.action}",
        )
