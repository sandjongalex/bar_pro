from sqlalchemy import select
from app.extensions import db
from app.models import AuditLog
from app.bar_services import assign_staff
from test_workflows import env

def test_role_assignment_is_audited_without_sensitive_values(env):
    _, owner, bar, _, _, employee, _, _ = env
    assignment=assign_staff(owner,bar.id,employee.id,"CASHIER")
    db.session.commit()
    log=db.session.scalar(select(AuditLog).where(AuditLog.target_id==assignment.id,AuditLog.action=="staff.role.change"))
    assert log.actor_id==owner.id and log.bar_id==bar.id
    assert "password" not in (log.reason or "").lower()
    assert "token" not in (log.reason or "").lower()
