"""Central, server-side permission decisions."""
from dataclasses import dataclass
from sqlalchemy import select
from app.extensions import db
from app.models import Bar, StaffAssignment

ROLE_ACTIONS = {
 "catalog.manage":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "suppliers.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "suppliers.manage":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "purchases.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "inventory.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "inventory.adjust":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "purchases.manage":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "cash.operate":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER"},
 "auth.self.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER","SERVER"},
 "bars.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER","SERVER"},
 "bars.update_settings":{"SUPER_ADMIN","OWNER"}, "bars.create":{"SUPER_ADMIN"},
 "staff.manage":{"SUPER_ADMIN","OWNER"}, "staff.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "catalog.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER","SERVER"},
 "orders.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER","SERVER"},
 # A cashier receives and settles orders; the server is the operational creator.
 "orders.create":{"SUPER_ADMIN","OWNER","BAR_ADMIN","SERVER"},
 "orders.edit":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER","SERVER"},
 "orders.deliver":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER"},
 "payments.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER"},
 "refunds.record":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER"},
 "cash.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER"},
 "reports.read":{"SUPER_ADMIN","OWNER","BAR_ADMIN"},
 "subscriptions.read":{"SUPER_ADMIN","OWNER"},
 "subscriptions.manage":{"SUPER_ADMIN"},
 "payments.record":{"SUPER_ADMIN","OWNER","BAR_ADMIN","CASHIER"},
 "bars.reactivate":{"SUPER_ADMIN"}, "bars.suspend":{"SUPER_ADMIN"},
}
WRITES = {"refunds.record","payments.record", "bars.suspend", "bars.reactivate","bars.update_settings","staff.manage","orders.create","orders.edit","orders.deliver"}
WRITES.update({"catalog.manage", "suppliers.manage", "inventory.adjust", "purchases.manage", "cash.operate"})
WRITES.add("subscriptions.manage")

@dataclass(frozen=True)
class Decision: allowed: bool; reason: str

class PermissionService:
    def evaluate(self, actor, action, bar_id=None):
        if not actor or not actor.is_active: return Decision(False,"ACCOUNT_INACTIVE")
        if action not in ROLE_ACTIONS: return Decision(False,"UNKNOWN_PERMISSION")
        if action.startswith("auth.self."): return Decision(True,"ALLOWED")
        if bar_id is None: return Decision(False,"BAR_REQUIRED")
        bar=db.session.get(Bar,bar_id)
        if not bar: return Decision(False,"NOT_FOUND")
        role = "SUPER_ADMIN" if actor.category=="SUPER_ADMIN" else "OWNER" if bar.owner_id==actor.id and actor.category=="OWNER" else None
        if role is None and actor.category=="EMPLOYEE":
            assignment=db.session.scalar(select(StaffAssignment).where(StaffAssignment.bar_id==bar.id,StaffAssignment.user_id==actor.id,StaffAssignment.ended_at.is_(None)))
            role=assignment.role if assignment else None
        if bar.status=="SUSPENDED" and actor.category=="EMPLOYEE": return Decision(False,"BAR_SUSPENDED")
        if role not in ROLE_ACTIONS[action]: return Decision(False,"FORBIDDEN")
        if bar.status=="SUSPENDED" and action in WRITES and action not in {"bars.reactivate"}: return Decision(False,"BAR_SUSPENDED")
        return Decision(True,"ALLOWED")
    def require(self,*args,**kwargs):
        action=args[1] if len(args)>1 else kwargs.get("action")
        bar_id=args[2] if len(args)>2 else kwargs.get("bar_id")
        if action in WRITES and bar_id is not None:
            db.session.scalar(select(Bar).where(Bar.id==bar_id).execution_options(populate_existing=True).with_for_update())
        decision=self.evaluate(*args,**kwargs)
        if not decision.allowed: raise PermissionError(decision.reason)
        return decision

permissions=PermissionService()
