"""Administrative restrictions separated from Bar.status and subscriptions."""
import uuid
from sqlalchemy import select

from app.audit import record
from app.extensions import db
from app.models import AuditLog, Bar, Subscription, User, utcnow
from app.saas_governance_models import ADMIN_STATES, BarGovernance, OwnerGovernance


def bar_governance(bar_id):
    return db.session.get(BarGovernance, bar_id)


def suspension_level(bar_id):
    state = bar_governance(bar_id)
    return state.suspension_level if state and state.status == "SUSPENDED" else 0


def owner_enabled(user):
    if not user or not user.is_active:
        return False
    if user.category != "OWNER":
        return True
    state = db.session.get(OwnerGovernance, user.id)
    return state is None or state.status == "APPROVED"


def trial_expired(bar_id):
    """Return True when a bar requires payment before further writes.

    For self-registered owners, the first bar gets exactly 15 days of trial.
    Additional bars require a paid subscription from day one. Existing legacy
    bars with no owner trial are unaffected.
    """
    now = utcnow().replace(tzinfo=None)
    paid = db.session.scalar(
        select(Subscription.id).where(
            Subscription.bar_id == bar_id,
            Subscription.status == "ACTIVE",
            Subscription.plan_code_snapshot != "BARPRO_TRIAL_15D",
            Subscription.starts_at <= now,
            Subscription.ends_at > now,
        ).limit(1)
    )
    if paid is not None:
        return False

    trial = db.session.execute(
        select(Subscription.ends_at, Subscription.status)
        .where(
            Subscription.bar_id == bar_id,
            Subscription.plan_code_snapshot == "BARPRO_TRIAL_15D",
        )
        .order_by(Subscription.ends_at.desc())
        .limit(1)
    ).first()
    if trial is not None:
        return trial.status != "ACTIVE" or trial.ends_at <= now

    bar = db.session.get(Bar, bar_id)
    if bar is None:
        return False
    owner_has_trial = db.session.scalar(
        select(Subscription.id)
        .join(Bar, Bar.id == Subscription.bar_id)
        .where(
            Bar.owner_id == bar.owner_id,
            Subscription.plan_code_snapshot == "BARPRO_TRIAL_15D",
        )
        .limit(1)
    )
    return owner_has_trial is not None


def _validate_reason(reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise ValueError("INVALID_ADMIN_REASON")
    return reason.strip()


def change_bar_governance(actor, bar_id, *, status, suspension_level=0, reason):
    """Metadata only. Lock the bar to serialize with permission.require writes."""
    if not actor or not actor.is_active or actor.category != "SUPER_ADMIN":
        raise PermissionError("FORBIDDEN")
    if status not in ADMIN_STATES or type(suspension_level) is not int:
        raise ValueError("INVALID_ADMIN_STATUS")
    if (status == "SUSPENDED" and suspension_level not in (1, 2)) or (
        status != "SUSPENDED" and suspension_level != 0
    ):
        raise ValueError("INVALID_SUSPENSION_LEVEL")
    reason = _validate_reason(reason)
    if not db.session.scalar(select(Bar.id).where(Bar.id == bar_id).with_for_update()):
        raise LookupError("NOT_FOUND")
    state = db.session.scalar(select(BarGovernance).where(BarGovernance.bar_id == bar_id).with_for_update())
    before = {"status": state.status, "suspension_level": state.suspension_level} if state else {
        "status": "APPROVED", "suspension_level": 0
    }
    if state is None:
        state = BarGovernance(bar_id=bar_id, status=status, suspension_level=suspension_level,
                              reason=reason, changed_by_id=actor.id, changed_at=utcnow())
        db.session.add(state)
    else:
        state.status, state.suspension_level = status, suspension_level
        state.reason, state.changed_by_id, state.changed_at = reason, actor.id, utcnow()
    db.session.flush()
    record(actor, bar_id, "bars.governance.change", "bars", bar_id, reason,
           changes={"before": before, "after": {"status": status, "suspension_level": suspension_level}})
    return state


def change_owner_governance(actor, owner_id, *, status, reason):
    """Account restriction does not cascade to owned bars or their staff."""
    if not actor or not actor.is_active or actor.category != "SUPER_ADMIN":
        raise PermissionError("FORBIDDEN")
    if status not in ADMIN_STATES:
        raise ValueError("INVALID_ADMIN_STATUS")
    reason = _validate_reason(reason)
    owner = db.session.scalar(select(User).where(User.id == owner_id).with_for_update())
    if not owner or owner.category != "OWNER":
        raise LookupError("NOT_FOUND")
    state = db.session.scalar(select(OwnerGovernance).where(OwnerGovernance.owner_id == owner_id).with_for_update())
    before = state.status if state else "APPROVED"
    if state is None:
        state = OwnerGovernance(owner_id=owner_id, status=status, reason=reason,
                                changed_by_id=actor.id, changed_at=utcnow())
        db.session.add(state)
    else:
        state.status, state.reason = status, reason
        state.changed_by_id, state.changed_at = actor.id, utcnow()
    if before != status:
        owner.credentials_version += 1
    db.session.flush()
    db.session.add(AuditLog(
        scope="PLATFORM", bar_id=None, actor_id=actor.id,
        action="owners.governance.change", target_table="users", target_id=owner_id,
        outcome="SUCCESS", reason=reason, changes={"before": before, "after": status},
        request_id=uuid.uuid4().bytes, occurred_at=utcnow(),
    ))
    return state
