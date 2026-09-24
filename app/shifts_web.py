"""Owner/cashier employee service management."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.extensions import db
from app.models import Bar, StaffAssignment
from app.permissions import permissions
from app.shift_models import EmployeeShift
from app.shift_service import (
    ShiftError,
    duration_minutes,
    end_shift,
    get_active_shift_for_assignment,
    local_time,
    start_shift,
)

bp = Blueprint("shifts_web", __name__, url_prefix="/bars/<int:bar_id>/shifts")

ROLE_LABELS = {
    "CASHIER": "Caissier / Caissière",
    "SERVER": "Serveur / Serveuse",
}


def _message(code):
    messages = {
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer ce service.",
        "BAR_SUSPENDED": "Le bar est suspendu.",
        "SHIFT_ASSIGNMENT_NOT_FOUND": "L'employé n'est plus affecté à ce bar.",
        "SHIFT_ROLE_UNSUPPORTED": "Ce rôle n'utilise pas le pointage de service.",
        "SHIFT_USER_INACTIVE": "Le compte de cet employé est désactivé.",
        "SHIFT_ALREADY_OPEN": "Cette personne est déjà en service.",
        "SHIFT_NOT_OPEN": "Cette personne n'est pas actuellement en service.",
        "CASHIER_OFF_DUTY": "Une caissière doit d'abord être mise en service par le propriétaire.",
        "SERVERS_STILL_ACTIVE": "Des serveuses sont encore en service. Terminez d'abord leurs services.",
        "NOT_FOUND": "Élément introuvable.",
    }
    return messages.get(str(code), "Opération impossible.")


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    permissions.require(current_user, "shifts.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    actor_assignment = None
    if current_user.category == "EMPLOYEE":
        actor_assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.user_id == current_user.id,
                StaffAssignment.ended_at.is_(None),
            )
        )

    is_owner = current_user.category == "SUPER_ADMIN" or (
        current_user.category == "OWNER" and bar.owner_id == current_user.id
    )
    is_cashier = bool(actor_assignment and actor_assignment.role == "CASHIER")
    actor_shift = (
        get_active_shift_for_assignment(bar_id, actor_assignment.id)
        if is_cashier
        else None
    )

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            assignment_id = int(request.form.get("assignment_id", "0"))
            if action == "start":
                item = start_shift(current_user, bar_id, assignment_id)
                db.session.commit()
                flash(
                    f"Service commencé à {local_time(item.started_at, bar.timezone, '%H:%M')}. L'heure a été enregistrée automatiquement.",
                    "success",
                )
            elif action == "end":
                item = end_shift(current_user, bar_id, assignment_id)
                db.session.commit()
                flash(
                    f"Service terminé à {local_time(item.ended_at, bar.timezone, '%H:%M')}.",
                    "success",
                )
            else:
                raise ValueError("INVALID_ACTION")
        except (PermissionError, LookupError, ShiftError, ValueError, TypeError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")
        return redirect(url_for("shifts_web.manage", bar_id=bar_id))

    all_assignments = list(
        db.session.scalars(
            select(StaffAssignment)
            .where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.ended_at.is_(None),
                StaffAssignment.role.in_(["CASHIER", "SERVER"]),
            )
            .order_by(StaffAssignment.role, StaffAssignment.id)
        )
    )

    if is_owner:
        visible_assignments = all_assignments
    elif is_cashier:
        visible_assignments = [item for item in all_assignments if item.role == "SERVER"]
    else:
        visible_assignments = []

    active_shifts = {}
    for assignment in all_assignments:
        shift = get_active_shift_for_assignment(bar_id, assignment.id)
        if shift:
            active_shifts[assignment.id] = shift

    recent = list(
        db.session.scalars(
            select(EmployeeShift)
            .where(EmployeeShift.bar_id == bar_id)
            .order_by(EmployeeShift.started_at.desc(), EmployeeShift.id.desc())
            .limit(50)
        )
    )

    return render_template(
        "shifts.html",
        bar=bar,
        assignments=visible_assignments,
        active_shifts=active_shifts,
        recent=recent,
        role_labels=ROLE_LABELS,
        is_owner=is_owner,
        is_cashier=is_cashier,
        cashier_on_duty=bool(actor_shift),
        local_time=local_time,
        duration_minutes=duration_minutes,
    )
