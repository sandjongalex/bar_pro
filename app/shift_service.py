"""Business rules for employee on-duty sessions."""
from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.extensions import db
from app.models import Bar, StaffAssignment, utcnow
from app.shift_models import EmployeeShift


class ShiftError(ValueError):
    pass


def get_active_shift_for_assignment(bar_id: int, assignment_id: int, *, lock: bool = False):
    stmt = select(EmployeeShift).where(
        EmployeeShift.bar_id == bar_id,
        EmployeeShift.staff_assignment_id == assignment_id,
        EmployeeShift.status == "OPEN",
    ).order_by(EmployeeShift.started_at.desc(), EmployeeShift.id.desc())
    if lock:
        stmt = stmt.with_for_update()
    return db.session.scalar(stmt)


def get_active_shift_for_user(user_id: int, bar_id: int | None = None):
    stmt = (
        select(EmployeeShift)
        .join(
            StaffAssignment,
            (StaffAssignment.bar_id == EmployeeShift.bar_id)
            & (StaffAssignment.id == EmployeeShift.staff_assignment_id),
        )
        .where(
            StaffAssignment.user_id == user_id,
            StaffAssignment.ended_at.is_(None),
            EmployeeShift.status == "OPEN",
        )
        .order_by(EmployeeShift.started_at.desc(), EmployeeShift.id.desc())
    )
    if bar_id is not None:
        stmt = stmt.where(EmployeeShift.bar_id == bar_id)
    return db.session.scalar(stmt)


def is_on_duty(user_id: int, bar_id: int) -> bool:
    return get_active_shift_for_user(user_id, bar_id) is not None


def _active_assignment_for_actor(actor, bar_id: int):
    if actor.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == actor.id,
            StaffAssignment.ended_at.is_(None),
        )
    )


def _is_owner_or_superadmin(actor, bar: Bar) -> bool:
    return actor.category == "SUPER_ADMIN" or (
        actor.category == "OWNER" and bar.owner_id == actor.id
    )


def _require_manager(actor, bar: Bar, target: StaffAssignment, *, ending: bool = False):
    if target.role == "CASHIER":
        if not _is_owner_or_superadmin(actor, bar):
            raise PermissionError("FORBIDDEN")
        return

    if target.role != "SERVER":
        raise ShiftError("SHIFT_ROLE_UNSUPPORTED")

    # The owner/super-admin may close a forgotten server shift as an emergency
    # override, but starting servers remains the responsibility of an on-duty cashier.
    if ending and _is_owner_or_superadmin(actor, bar):
        return

    actor_assignment = _active_assignment_for_actor(actor, bar.id)
    if not actor_assignment or actor_assignment.role != "CASHIER":
        raise PermissionError("FORBIDDEN")
    if not get_active_shift_for_assignment(bar.id, actor_assignment.id):
        raise PermissionError("CASHIER_OFF_DUTY")


def start_shift(actor, bar_id: int, assignment_id: int):
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    if bar.status == "SUSPENDED":
        raise PermissionError("BAR_SUSPENDED")

    target = db.session.scalar(
        select(StaffAssignment)
        .where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.id == assignment_id,
            StaffAssignment.ended_at.is_(None),
        )
        .with_for_update()
    )
    if not target:
        raise LookupError("SHIFT_ASSIGNMENT_NOT_FOUND")
    if target.role not in {"CASHIER", "SERVER"}:
        raise ShiftError("SHIFT_ROLE_UNSUPPORTED")
    if not target.user.is_active:
        raise ShiftError("SHIFT_USER_INACTIVE")

    _require_manager(actor, bar, target, ending=False)

    if get_active_shift_for_assignment(bar_id, target.id, lock=True):
        raise ShiftError("SHIFT_ALREADY_OPEN")

    item = EmployeeShift(
        bar_id=bar_id,
        staff_assignment_id=target.id,
        role_snapshot=target.role,
        status="OPEN",
        started_at=utcnow(),
        started_by_id=actor.id,
    )
    db.session.add(item)
    db.session.flush()
    return item


def end_shift(actor, bar_id: int, assignment_id: int):
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    target = db.session.scalar(
        select(StaffAssignment)
        .where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.id == assignment_id,
            StaffAssignment.ended_at.is_(None),
        )
        .with_for_update()
    )
    if not target:
        raise LookupError("SHIFT_ASSIGNMENT_NOT_FOUND")

    _require_manager(actor, bar, target, ending=True)

    item = get_active_shift_for_assignment(bar_id, target.id, lock=True)
    if not item:
        raise ShiftError("SHIFT_NOT_OPEN")

    if target.role == "CASHIER":
        server_open = db.session.scalar(
            select(EmployeeShift.id)
            .where(
                EmployeeShift.bar_id == bar_id,
                EmployeeShift.role_snapshot == "SERVER",
                EmployeeShift.status == "OPEN",
            )
            .limit(1)
        )
        other_cashier_open = db.session.scalar(
            select(EmployeeShift.id)
            .where(
                EmployeeShift.bar_id == bar_id,
                EmployeeShift.role_snapshot == "CASHIER",
                EmployeeShift.status == "OPEN",
                EmployeeShift.id != item.id,
            )
            .limit(1)
        )
        if server_open and not other_cashier_open:
            raise ShiftError("SERVERS_STILL_ACTIVE")

    item.status = "CLOSED"
    item.ended_at = utcnow()
    item.ended_by_id = actor.id
    db.session.flush()
    return item


def local_time(value, timezone_name: str, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name)).strftime(fmt)


def duration_minutes(item: EmployeeShift) -> int | None:
    end = item.ended_at or utcnow()
    start = item.started_at
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - start).total_seconds() // 60))
