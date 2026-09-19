"""Cashier-facing workflow for receiving cash held by servers."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.cash_services import cash_service
from app.extensions import db
from app.models import Bar, CashHandover, CashSession, StaffAssignment, User
from app.permissions import permissions

bp = Blueprint("cashier_handovers_web", __name__, url_prefix="/bars/<int:bar_id>/cashier-handovers")


def _open_session(bar_id: int):
    return db.session.scalar(
        select(CashSession)
        .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
        .order_by(CashSession.id.desc())
    )


def _reference() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"REM-{stamp}-{secrets.token_hex(2).upper()}"


def _local_display(value, timezone_name: str) -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name)).strftime("%d/%m/%Y %H:%M")


def _message(exc) -> str:
    code = str(exc)
    messages = {
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer les remises d'espèces.",
        "NOT_FOUND": "Serveuse ou remise introuvable.",
        "CASH_SESSION_NOT_OPEN": "La session de caisse n'est plus ouverte.",
        "INSUFFICIENT_STAFF_CASH": "Le montant dépasse les espèces actuellement attribuées à cette serveuse.",
        "HANDOVER_NOT_DRAFT": "Cette remise a déjà été traitée.",
        "POSITIVE_NUMBER_REQUIRED": "Le montant doit être strictement supérieur à zéro.",
        "INVALID_NUMBER": "Le montant saisi est invalide.",
    }
    return messages.get(code, "Opération refusée. Vérifiez la serveuse, le montant et l'état de la remise.")


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    permissions.require(current_user, "cash.operate", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    session = _open_session(bar_id)
    if session is None:
        flash("Ouvrez d'abord une session de caisse avant de recevoir une remise de serveuse.", "info")
        return redirect(url_for("cashier_web.session", bar_id=bar_id))

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "create":
                staff_id = request.form.get("staff_assignment_id", type=int)
                if not staff_id:
                    raise LookupError("NOT_FOUND")
                staff = db.session.scalar(
                    select(StaffAssignment).where(
                        StaffAssignment.id == staff_id,
                        StaffAssignment.bar_id == bar_id,
                        StaffAssignment.role == "SERVER",
                        StaffAssignment.ended_at.is_(None),
                    ).with_for_update()
                )
                if not staff:
                    raise LookupError("NOT_FOUND")
                handover = cash_service.handover(
                    current_user,
                    bar_id,
                    staff.id,
                    session.id,
                    _reference(),
                    request.form.get("amount", ""),
                )
                db.session.commit()
                flash(
                    f"Remise {handover.reference} préparée pour {handover.amount:,.0f} {handover.currency}. Comptez les espèces puis confirmez la réception.",
                    "success",
                )

            elif action in {"post", "cancel"}:
                handover_id = request.form.get("handover_id", type=int)
                handover = db.session.scalar(
                    select(CashHandover).where(
                        CashHandover.id == handover_id,
                        CashHandover.bar_id == bar_id,
                        CashHandover.cash_session_id == session.id,
                        CashHandover.status == "DRAFT",
                    ).with_for_update()
                )
                if not handover:
                    raise ValueError("HANDOVER_NOT_DRAFT")
                cash_service.transition_handover(
                    current_user,
                    bar_id,
                    handover.id,
                    cancel=action == "cancel",
                )
                db.session.commit()
                if action == "post":
                    flash(
                        f"Réception confirmée : {handover.amount:,.0f} {handover.currency} ont été transférés dans la caisse.",
                        "success",
                    )
                else:
                    flash("Remise annulée sans mouvement d'espèces.", "info")
            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Une référence identique existe déjà. Réessayez.", "danger")
            else:
                flash(_message(exc), "danger")
        return redirect(url_for("cashier_handovers_web.manage", bar_id=bar_id))

    assignments = list(
        db.session.execute(
            select(StaffAssignment, User)
            .join(User, User.id == StaffAssignment.user_id)
            .where(StaffAssignment.bar_id == bar_id)
            .order_by(User.display_name, StaffAssignment.id)
        ).all()
    )
    name_by_assignment = {assignment.id: user.display_name for assignment, user in assignments}

    server_rows = []
    for assignment, user in assignments:
        if assignment.role != "SERVER" or assignment.ended_at is not None:
            continue
        server_rows.append(
            {
                "assignment_id": assignment.id,
                "name": user.display_name,
                "custody": Decimal(cash_service.custody(bar_id, assignment.id) or 0),
            }
        )

    handovers = list(
        db.session.scalars(
            select(CashHandover)
            .where(
                CashHandover.bar_id == bar_id,
                CashHandover.cash_session_id == session.id,
            )
            .order_by(CashHandover.id.desc())
            .limit(50)
        )
    )

    user_ids = {
        value
        for item in handovers
        for value in (item.requested_by_id, item.received_by_id)
        if value is not None
    }
    users = {
        user.id: user.display_name
        for user in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    total_custody = sum((row["custody"] for row in server_rows), Decimal("0"))
    pending = [item for item in handovers if item.status == "DRAFT"]
    posted = [item for item in handovers if item.status == "POSTED"]

    return render_template(
        "cashier_handovers.html",
        bar=bar,
        session=session,
        drawer_expected=Decimal(cash_service.expected(session) or 0),
        server_rows=server_rows,
        total_custody=total_custody,
        handovers=handovers,
        pending_count=len(pending),
        pending_amount=sum((Decimal(item.amount) for item in pending), Decimal("0")),
        posted_total=sum((Decimal(item.amount) for item in posted), Decimal("0")),
        name_by_assignment=name_by_assignment,
        user_name_by_id=users,
        handover_times={
            item.id: _local_display(item.posted_at or item.cancelled_at or item.created_at, bar.timezone)
            for item in handovers
        },
    )
