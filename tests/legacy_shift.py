"""Helpers for legacy workflow tests which intentionally exercise on-duty staff.

Never bypass application permissions: seed the same persisted EmployeeShift state
the production shift service requires, before exercising authorized workflows.
Tests specifically asserting OFF_DUTY use their own independent fixtures.
"""
from sqlalchemy import select
from app.extensions import db
from app.models import StaffAssignment, utcnow
from app.shift_models import EmployeeShift


def activate_employee(bar, employee):
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == employee.id,
            StaffAssignment.ended_at.is_(None),
        )
    )
    if assignment is None:
        db.session.flush()
        assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar.id,
                StaffAssignment.user_id == employee.id,
                StaffAssignment.ended_at.is_(None),
            )
        )
    if assignment is None:
        raise AssertionError("Legacy test fixture must assign employee before opening shift")
    if assignment.role not in {"CASHIER", "SERVER"}:
        return
    active = db.session.scalar(select(EmployeeShift.id).where(
        EmployeeShift.bar_id == bar.id,
        EmployeeShift.staff_assignment_id == assignment.id,
        EmployeeShift.status == "OPEN",
    ))
    if active is None:
        db.session.add(EmployeeShift(
            bar_id=bar.id, staff_assignment_id=assignment.id,
            role_snapshot=assignment.role, status="OPEN",
            started_at=utcnow(), started_by_id=bar.owner_id,
        ))
        db.session.flush()
