"""Server-rendered staff management for one bar."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import exists, select

from app.bar_services import (
    assign_staff,
    create_employee,
    end_staff_assignment,
    set_employee_active,
)
from app.extensions import db
from app.models import Bar, StaffAssignment, User
from app.permissions import permissions

bp = Blueprint("staff_web", __name__, url_prefix="/bars/<int:bar_id>/staff")

ROLE_LABELS = {
    "BAR_ADMIN": "Administrateur du bar",
    "CASHIER": "Caissier / Caissière",
    "SERVER": "Serveur / Serveuse",
}


def _message(code):
    messages = {
        "INVALID_STAFF_EMAIL": "L'adresse e-mail est invalide.",
        "INVALID_STAFF_NAME": "Le nom du membre du personnel est obligatoire.",
        "WEAK_STAFF_PASSWORD": "Le mot de passe doit contenir au moins 8 caractères.",
        "STAFF_EMAIL_EXISTS": "Cette adresse e-mail est déjà utilisée.",
        "INVALID_STAFF_ROLE": "Le rôle sélectionné est invalide.",
        "INVALID_STAFF": "Le compte employé est introuvable ou indisponible.",
        "STAFF_ALREADY_ASSIGNED": "Cet employé est déjà affecté à un autre établissement.",
        "STAFF_ASSIGNMENT_NOT_FOUND": "Cette affectation n'est plus active.",
        "PASSWORD_MISMATCH": "Les deux mots de passe ne correspondent pas.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer le personnel de cet établissement.",
    }
    return messages.get(str(code), "Opération impossible. Vérifiez les informations saisies.")


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id):
    permissions.require(current_user, "staff.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    can_manage = permissions.evaluate(current_user, "staff.manage", bar_id).allowed

    if request.method == "POST":
        if not can_manage:
            raise PermissionError("FORBIDDEN")

        action = request.form.get("action", "")
        try:
            if action == "create":
                password = request.form.get("password", "")
                confirmation = request.form.get("password_confirm", "")
                if password != confirmation:
                    raise ValueError("PASSWORD_MISMATCH")
                create_employee(
                    current_user,
                    bar_id,
                    {
                        "display_name": request.form.get("display_name", ""),
                        "email": request.form.get("email", ""),
                        "password": password,
                    },
                    request.form.get("role", ""),
                )
                db.session.commit()
                flash("Le compte employé a été créé et affecté au bar.", "success")

            elif action == "assign":
                assign_staff(
                    current_user,
                    bar_id,
                    int(request.form.get("user_id", "0")),
                    request.form.get("role", ""),
                )
                db.session.commit()
                flash("L'employé a été affecté au bar.", "success")

            elif action == "role":
                assign_staff(
                    current_user,
                    bar_id,
                    int(request.form.get("user_id", "0")),
                    request.form.get("role", ""),
                )
                db.session.commit()
                flash("Le rôle a été mis à jour.", "success")

            elif action == "end":
                end_staff_assignment(
                    current_user,
                    bar_id,
                    int(request.form.get("assignment_id", "0")),
                )
                db.session.commit()
                flash("L'affectation a été terminée.", "success")

            elif action == "deactivate":
                set_employee_active(
                    current_user,
                    bar_id,
                    int(request.form.get("user_id", "0")),
                    False,
                )
                db.session.commit()
                flash("Le compte employé a été désactivé.", "success")

            elif action == "reactivate":
                set_employee_active(
                    current_user,
                    bar_id,
                    int(request.form.get("user_id", "0")),
                    True,
                )
                db.session.commit()
                flash("Le compte employé a été réactivé. Vous pouvez maintenant le réaffecter.", "success")

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")

        return redirect(url_for("staff_web.manage", bar_id=bar_id))

    assignments = list(
        db.session.scalars(
            select(StaffAssignment)
            .where(StaffAssignment.bar_id == bar_id)
            .order_by(StaffAssignment.started_at.desc(), StaffAssignment.id.desc())
        )
    )

    active_assignments = [item for item in assignments if item.ended_at is None]
    history = [item for item in assignments if item.ended_at is not None]

    available_employees = []
    if can_manage:
        active_assignment_exists = exists().where(
            StaffAssignment.user_id == User.id,
            StaffAssignment.ended_at.is_(None),
        )
        available_employees = list(
            db.session.scalars(
                select(User)
                .where(
                    User.category == "EMPLOYEE",
                    User.is_active.is_(True),
                    ~active_assignment_exists,
                )
                .order_by(User.display_name, User.email)
            )
        )

    inactive_people = []
    seen = set()
    for item in assignments:
        if not item.user.is_active and item.user_id not in seen:
            inactive_people.append(item.user)
            seen.add(item.user_id)

    counts = {
        "total": len(active_assignments),
        "BAR_ADMIN": sum(1 for item in active_assignments if item.role == "BAR_ADMIN"),
        "CASHIER": sum(1 for item in active_assignments if item.role == "CASHIER"),
        "SERVER": sum(1 for item in active_assignments if item.role == "SERVER"),
    }

    return render_template(
        "bars/staff.html",
        bar=bar,
        assignments=active_assignments,
        history=history,
        available_employees=available_employees,
        inactive_people=inactive_people,
        counts=counts,
        role_labels=ROLE_LABELS,
        can_manage=can_manage,
    )
