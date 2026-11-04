"""Owner-controlled warehouse delegation for BAR_ADMIN (or special purchasing)."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.extensions import db
from app.models import ID, Tenant, datetime_type, tenant_args


class WarehouseStaffGrant(Tenant, db.Model):
    __tablename__ = "warehouse_staff_grants"

    id = db.Column(ID, primary_key=True)
    staff_assignment_id = db.Column(ID, nullable=False)
    action = db.Column(db.String(64), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    granted_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    granted_at = db.Column(datetime_type(fsp=6), nullable=False)
    revoked_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    revoked_at = db.Column(datetime_type(fsp=6))

    __table_args__ = tenant_args(
        "warehouse_staff_grants",
        ForeignKeyConstraint(
            ["bar_id", "staff_assignment_id"],
            ["staff_assignments.bar_id", "staff_assignments.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "bar_id", "staff_assignment_id", "action",
            name="uq_warehouse_staff_grant_action",
        ),
        CheckConstraint(
            "(enabled = 1 AND revoked_at IS NULL AND revoked_by_id IS NULL) "
            "OR (enabled = 0 AND revoked_at IS NOT NULL AND revoked_by_id IS NOT NULL)",
            name="ck_warehouse_staff_grant_state",
        ),
    )
