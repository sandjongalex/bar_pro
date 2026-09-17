"""Minimal immutable audit writer used by sensitive service transitions."""
import uuid
from app.extensions import db
from app.models import AuditLog, utcnow
def record(actor, bar_id, action, target_table, target_id, reason):
    db.session.add(AuditLog(scope="BAR",bar_id=bar_id,actor_id=actor.id,action=action,target_table=target_table,target_id=target_id,outcome="SUCCESS",reason=reason,request_id=uuid.uuid4().bytes,occurred_at=utcnow()))
