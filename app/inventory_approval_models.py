"""BAR_PRO V2 inventory approval policies and immutable decision history."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from app.extensions import db
from app.models import ID, QTY, MONEY, Tenant, Timestamped, datetime_type, tenant_args


class InventoryApprovalPolicy(Timestamped, db.Model):
    __tablename__ = "inventory_approval_policies"
    bar_id = db.Column(ID, db.ForeignKey("bars.id", ondelete="RESTRICT"), primary_key=True)
    max_unit_difference = db.Column(QTY, nullable=False, default=0)
    max_product_value = db.Column(MONEY, nullable=False, default=0)
    max_total_value = db.Column(MONEY, nullable=False, default=0)
    changed_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "max_unit_difference >= 0 AND max_product_value >= 0 AND max_total_value >= 0",
            name="ck_inventory_policy_nonnegative",
        ),
    )


class InventoryApproval(Tenant, db.Model):
    __tablename__ = "inventory_approvals"
    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="SUBMITTED")
    submitted_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submitted_at = db.Column(datetime_type(fsp=6), nullable=False)
    decided_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at = db.Column(datetime_type(fsp=6))
    decision_reason = db.Column(db.String(500))
    __table_args__ = tenant_args(
        "inventory_approvals",
        ForeignKeyConstraint(
            ["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_approval_per_inventory"),
        CheckConstraint(
            "status IN ('SUBMITTED','RECOUNT','POSTED')", name="ck_inventory_approval_status",
        ),
        Index("ix_inventory_approvals_status", "bar_id", "status", "submitted_at"),
    )
