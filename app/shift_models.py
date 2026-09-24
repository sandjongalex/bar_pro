"""Employee attendance and on-duty state for cashier/server access control."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index

from app.extensions import db
from app.models import ID, Tenant, datetime_type, tenant_args


class EmployeeShift(Tenant, db.Model):
    __tablename__ = "employee_shifts"

    id = db.Column(ID, primary_key=True)
    staff_assignment_id = db.Column(ID, nullable=False)
    role_snapshot = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="OPEN")
    scheduled_start_at = db.Column(datetime_type(fsp=6))
    started_at = db.Column(datetime_type(fsp=6), nullable=False)
    ended_at = db.Column(datetime_type(fsp=6))
    started_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    ended_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))

    assignment = db.relationship("StaffAssignment", foreign_keys=[staff_assignment_id])
    started_by = db.relationship("User", foreign_keys=[started_by_id])
    ended_by = db.relationship("User", foreign_keys=[ended_by_id])

    __table_args__ = tenant_args(
        "employee_shifts",
        ForeignKeyConstraint(
            ["bar_id", "staff_assignment_id"],
            ["staff_assignments.bar_id", "staff_assignments.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "role_snapshot IN ('CASHIER','SERVER')",
            name="ck_employee_shifts_role",
        ),
        CheckConstraint(
            "status IN ('OPEN','CLOSED')",
            name="ck_employee_shifts_status",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND ended_at IS NULL AND ended_by_id IS NULL) OR "
            "(status = 'CLOSED' AND ended_at IS NOT NULL AND ended_by_id IS NOT NULL)",
            name="ck_employee_shifts_close_state",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_employee_shifts_dates",
        ),
        Index(
            "ix_employee_shifts_assignment_status",
            "bar_id",
            "staff_assignment_id",
            "status",
            "started_at",
            "id",
        ),
        Index(
            "ix_employee_shifts_bar_status_role",
            "bar_id",
            "status",
            "role_snapshot",
            "started_at",
            "id",
        ),
    )
