"""Central, server-side permission decisions."""
from dataclasses import dataclass

from sqlalchemy import select

from app.extensions import db
from app.models import Bar, StaffAssignment
from app.saas_governance import owner_enabled, suspension_level, bar_governance, trial_expired
from app.warehouse_access import WAREHOUSE_DELEGABLE, WAREHOUSE_WRITES, STOREKEEPER_DEFAULT


ROLE_ACTIONS = {
    "catalog.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "suppliers.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "suppliers.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "purchases.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "inventory.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "inventory.create": {"OWNER", "BAR_ADMIN", "CASHIER"},
    "inventory.count": {"OWNER", "BAR_ADMIN", "CASHIER"},
    "inventory.submit": {"OWNER", "BAR_ADMIN", "CASHIER"},
    "inventory.approve": {"OWNER", "BAR_ADMIN"},
    "inventory.policy": {"OWNER"},
    "inventory.adjust": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "purchases.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "expenses.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "expenses.record": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "expenses.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "cash.operate": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "auth.self.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER", "STOREKEEPER"},
    "bars.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER", "STOREKEEPER"},
    "bars.support.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER", "STOREKEEPER"},
    "notifications.acknowledge": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER", "STOREKEEPER"},
    "bars.governance.manage": {"SUPER_ADMIN"},
    "shifts.manage": {"SUPER_ADMIN", "OWNER", "CASHIER"},
    "bars.update_settings": {"SUPER_ADMIN", "OWNER"},
    "bars.create": {"SUPER_ADMIN"},
    "bars.reset": {"SUPER_ADMIN", "OWNER"},
    "staff.manage": {"SUPER_ADMIN", "OWNER"},
    "staff.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "shifts.read": {"SUPER_ADMIN", "OWNER", "CASHIER"},
    "catalog.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER", "STOREKEEPER"},
    "customers.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "customers.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "customer_credit.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "customer_debt.manage": {"SUPER_ADMIN", "OWNER"},  # Manual off-cash adjustments need owner control.
    "cases.manage": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "orders.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER"},
    # Servers create table orders; cashiers may also create direct counter-sale orders.
    "orders.create": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER"},
    "orders.edit": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER"},
    "orders.deliver": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "payments.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "refunds.record": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "cash.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "reports.read": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN"},
    "subscriptions.read": {"SUPER_ADMIN", "OWNER"},
    "subscriptions.manage": {"SUPER_ADMIN"},
    "payments.record": {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER"},
    "bars.reactivate": {"SUPER_ADMIN"},
    "bars.suspend": {"SUPER_ADMIN"},
}

# Warehouse actions are deliberately distinct from order, purchase, and cash
# permissions. BAR_ADMIN gets each delegated right only after owner approval.
ROLE_ACTIONS.update({
    "warehouse.main.read": {"OWNER", "BAR_ADMIN", "STOREKEEPER", "SUPER_ADMIN"},
    "warehouse.cashier.needs.read": {"OWNER", "BAR_ADMIN", "STOREKEEPER", "CASHIER", "SUPER_ADMIN"},
    "warehouse.requests.read": {"OWNER", "BAR_ADMIN", "STOREKEEPER", "SUPER_ADMIN"},
    "warehouse.notifications.read": {"OWNER", "BAR_ADMIN", "STOREKEEPER", "SUPER_ADMIN"},
    "warehouse.requests.create": {"OWNER", "BAR_ADMIN", "CASHIER"},
    "warehouse.requests.prepare": {"OWNER", "BAR_ADMIN", "STOREKEEPER"},
    "warehouse.transfers.dispatch": {"OWNER", "BAR_ADMIN", "STOREKEEPER"},
    "warehouse.transfers.read": {"OWNER", "BAR_ADMIN", "STOREKEEPER", "CASHIER", "SUPER_ADMIN"},
    "warehouse.transfers.receive": {"OWNER", "BAR_ADMIN", "CASHIER"},
    "warehouse.main.inventory.count": {"OWNER", "BAR_ADMIN", "STOREKEEPER"},
    "warehouse.discrepancies.read": {"OWNER", "BAR_ADMIN", "STOREKEEPER", "SUPER_ADMIN"},
    "warehouse.discrepancies.resolve": {"OWNER", "BAR_ADMIN"},
    "warehouse.purchases.manage": {"OWNER", "BAR_ADMIN", "STOREKEEPER"},
    "warehouse.purchases.receive": {"OWNER", "BAR_ADMIN", "STOREKEEPER"},
    "warehouse.permissions.manage": {"OWNER"},
})

WRITES = {
    "refunds.record",
    "payments.record",
    "bars.suspend",
    "bars.reactivate",
    "bars.update_settings",
    "bars.reset",
    "staff.manage",
    "orders.create",
    "orders.edit",
    "orders.deliver",
}
WRITES.update(
    {
        "catalog.manage",
        "suppliers.manage",
        "inventory.adjust",
        "inventory.create",
        "inventory.count",
        "inventory.submit",
        "inventory.approve",
        "inventory.policy",
        "purchases.manage",
        "expenses.record",
        "expenses.manage",
        "cash.operate",
        "customers.manage",
        "customer_credit.manage",
        "customer_debt.manage",
        "cases.manage",
    }
)
WRITES.add("subscriptions.manage")
WRITES.add("bars.governance.manage")
WRITES.add("shifts.manage")
WRITES.add("notifications.acknowledge")
WRITES.update(WAREHOUSE_WRITES)

# The platform can govern tenants but must not alter their commercial data.
PLATFORM_COMMERCIAL_WRITES = WRITES - {
    "bars.suspend", "bars.reactivate", "bars.update_settings",
    "staff.manage", "subscriptions.manage", "bars.governance.manage",
    "notifications.acknowledge",
}


# Cashiers and servers keep their account/tenant identity while off duty, but
# operational/sales information becomes unavailable until a shift is active.
SHIFT_GATED_ACTIONS = {
    "catalog.read",
    "inventory.read",
    "inventory.create",
    "inventory.count",
    "inventory.submit",
    "notifications.acknowledge",
    "customers.read",
    "customers.manage",
    "customer_credit.manage",
    "cases.manage",
    "orders.read",
    "orders.create",
    "orders.edit",
    "orders.deliver",
    "payments.read",
    "payments.record",
    "refunds.record",
    "cash.read",
    "cash.operate",
    "expenses.read",
    "expenses.record",
    # Cashier warehouse operations have the same shift authorization as sales.
    "warehouse.requests.create",
    "warehouse.transfers.receive",
    "warehouse.cashier.needs.read",
    "warehouse.transfers.read",
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


class PermissionService:
    def evaluate(self, actor, action, bar_id=None):
        if not owner_enabled(actor):
            return Decision(False, "ACCOUNT_INACTIVE")
        if action not in ROLE_ACTIONS:
            return Decision(False, "UNKNOWN_PERMISSION")
        if action.startswith("auth.self."):
            return Decision(True, "ALLOWED")
        if bar_id is None:
            return Decision(False, "BAR_REQUIRED")

        bar = db.session.get(Bar, bar_id)
        if not bar:
            return Decision(False, "NOT_FOUND")

        role = (
            "SUPER_ADMIN"
            if actor.category == "SUPER_ADMIN"
            else "OWNER"
            if bar.owner_id == actor.id and actor.category == "OWNER"
            else None
        )
        assignment = None
        if role is None and actor.category == "EMPLOYEE":
            assignment = db.session.scalar(
                select(StaffAssignment).where(
                    StaffAssignment.bar_id == bar.id,
                    StaffAssignment.user_id == actor.id,
                    StaffAssignment.ended_at.is_(None),
                )
            )
            role = assignment.role if assignment else None

        if bar.status == "SUSPENDED" and actor.category == "EMPLOYEE":
            return Decision(False, "BAR_SUSPENDED")
        if role not in ROLE_ACTIONS[action]:
            return Decision(False, "FORBIDDEN")
        if role == "BAR_ADMIN" and action in WAREHOUSE_DELEGABLE:
            from app.warehouse_permission_models import WarehouseStaffGrant
            enabled = db.session.scalar(
                select(WarehouseStaffGrant.id).where(
                    WarehouseStaffGrant.bar_id == bar_id,
                    WarehouseStaffGrant.staff_assignment_id == assignment.id,
                    WarehouseStaffGrant.action == action,
                    WarehouseStaffGrant.enabled.is_(True),
                )
            )
            if enabled is None:
                return Decision(False, "WAREHOUSE_OWNER_GRANT_REQUIRED")
        if role == "STOREKEEPER":
            if action.startswith("warehouse.") and action not in STOREKEEPER_DEFAULT:
                from app.warehouse_permission_models import WarehouseStaffGrant
                enabled = db.session.scalar(
                    select(WarehouseStaffGrant.id).where(
                        WarehouseStaffGrant.bar_id == bar_id,
                        WarehouseStaffGrant.staff_assignment_id == assignment.id,
                        WarehouseStaffGrant.action == action,
                        WarehouseStaffGrant.enabled.is_(True),
                    )
                )
                if enabled is None:
                    return Decision(False, "WAREHOUSE_OWNER_GRANT_REQUIRED")
        if role == "SUPER_ADMIN" and action in PLATFORM_COMMERCIAL_WRITES:
            return Decision(False, "PLATFORM_COMMERCE_FORBIDDEN")
        governance = bar_governance(bar_id)
        if governance and governance.status in {"PENDING", "DRAFT", "INCOMPLETE", "REJECTED"} and action not in {"bars.support.read", "bars.governance.manage"}:
            return Decision(False, "BAR_PENDING_APPROVAL")
        if action in WRITES and action not in {"subscriptions.manage", "bars.governance.manage"} and trial_expired(bar_id):
            return Decision(False, "TRIAL_EXPIRED")
        level = suspension_level(bar_id)
        if level == 2 and action not in {"bars.support.read", "bars.governance.manage"}:
            return Decision(False, "BAR_ADMIN_LOCKED")
        if level == 1 and action in WRITES and action != "bars.governance.manage":
            return Decision(False, "BAR_ADMIN_READ_ONLY")

        if (
            actor.category == "EMPLOYEE"
            and role in {"CASHIER", "SERVER"}
            and action in SHIFT_GATED_ACTIONS
        ):
            from app.shift_models import EmployeeShift

            active_shift = db.session.scalar(
                select(EmployeeShift.id)
                .where(
                    EmployeeShift.bar_id == bar.id,
                    EmployeeShift.staff_assignment_id == assignment.id,
                    EmployeeShift.status == "OPEN",
                )
                .limit(1)
            )
            if not active_shift:
                return Decision(False, "OFF_DUTY")

        if bar.status == "SUSPENDED" and action in WRITES and action not in {"bars.reactivate", "bars.governance.manage"}:
            return Decision(False, "BAR_SUSPENDED")
        return Decision(True, "ALLOWED")

    def require(self, *args, **kwargs):
        action = args[1] if len(args) > 1 else kwargs.get("action")
        bar_id = args[2] if len(args) > 2 else kwargs.get("bar_id")
        if action in WRITES and bar_id is not None:
            db.session.scalar(
                select(Bar)
                .where(Bar.id == bar_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        decision = self.evaluate(*args, **kwargs)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return decision


permissions = PermissionService()
