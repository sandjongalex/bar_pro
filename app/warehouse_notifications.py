"""Tenant-safe notification fan-out for future warehouse replenishment requests.

The request workflow will call this helper once its request has been persisted.
This milestone intentionally exposes no request-submission endpoint.
"""
from sqlalchemy import select

from app.audit import record
from app.customer_models import UserNotification
from app.extensions import db
from app.models import Bar, StaffAssignment
from app.permissions import permissions


def notify_warehouse_request(actor, bar_id, reference, message):
    """Create per-user notifications for the owner and authorized warehouse staff.

    Requires authorization to create a request for this bar. The caller commits.
    The content must never include financial or cross-tenant information.
    """
    permissions.require(actor, "warehouse.requests.create", bar_id)
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 64:
        raise ValueError("INVALID_REQUEST_REFERENCE")
    if not isinstance(message, str) or not message.strip() or len(message.strip()) > 400:
        raise ValueError("INVALID_REQUEST_MESSAGE")
    bar = db.session.get(Bar, bar_id)
    if bar is None:
        raise LookupError("NOT_FOUND")
    recipients = {bar.owner_id}
    assignments = db.session.scalars(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.role.in_(("STOREKEEPER", "BAR_ADMIN")),
            StaffAssignment.ended_at.is_(None),
        )
    )
    for assignment in assignments:
        if not assignment.user.is_active:
            continue
        if permissions.evaluate(assignment.user, "warehouse.notifications.read", bar_id).allowed:
            recipients.add(assignment.user_id)
    title = f"Demande magasin : {reference.strip()}"[:160]
    created = []
    for user_id in sorted(recipients):
        row = UserNotification(
            bar_id=bar_id, user_id=user_id, kind="WAREHOUSE_REQUEST",
            title=title, body=message.strip(),
        )
        db.session.add(row)
        created.append(row)
    db.session.flush()
    if created:
        record(actor, bar_id, "warehouse.request.notify", "user_notifications", created[0].id,
               f"{reference.strip()} : {len(created)} destinataire(s)")
    return created
