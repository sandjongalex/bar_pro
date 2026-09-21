"""Resolve the single active tenant context for EMPLOYEE users."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.extensions import db
from app.models import StaffAssignment


@dataclass(frozen=True)
class EmployeeContext:
    bar_id: int
    role: str
    assignment: StaffAssignment


def get_current_employee_context(user) -> EmployeeContext | None:
    """Return the employee's single active assignment-backed tenant context.

    OWNER and SUPER_ADMIN are intentionally outside this helper because they may
    legitimately operate across more than one bar.
    """
    if not user or user.category != "EMPLOYEE":
        return None

    assignments = list(
        db.session.scalars(
            select(StaffAssignment)
            .where(
                StaffAssignment.user_id == user.id,
                StaffAssignment.ended_at.is_(None),
            )
            .order_by(StaffAssignment.id)
            .limit(2)
        )
    )
    if len(assignments) != 1:
        raise LookupError("EMPLOYEE_ASSIGNMENT_REQUIRED")

    assignment = assignments[0]
    return EmployeeContext(
        bar_id=assignment.bar_id,
        role=assignment.role,
        assignment=assignment,
    )
