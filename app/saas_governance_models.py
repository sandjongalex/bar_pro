"""Optional administrative states; legacy accounts and bars have no row."""
from sqlalchemy import CheckConstraint, ForeignKey, Index
from app.extensions import db
from app.models import ID, Timestamped, datetime_type

ADMIN_STATES = ("DRAFT", "PENDING", "INCOMPLETE", "APPROVED", "REJECTED", "SUSPENDED")
STATES_SQL = "'DRAFT','PENDING','INCOMPLETE','APPROVED','REJECTED','SUSPENDED'"

class BarGovernance(Timestamped, db.Model):
    __tablename__ = "bar_governance"
    bar_id = db.Column(ID, ForeignKey("bars.id", ondelete="RESTRICT"), primary_key=True)
    status = db.Column(db.String(16), nullable=False)
    suspension_level = db.Column(db.SmallInteger, nullable=False, default=0)
    reason = db.Column(db.String(500), nullable=False)
    changed_by_id = db.Column(ID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    changed_at = db.Column(datetime_type(fsp=6), nullable=False)
    __table_args__ = (
        CheckConstraint(f"status IN ({STATES_SQL})", name="ck_bar_governance_status"),
        CheckConstraint("(status = 'SUSPENDED' AND suspension_level IN (1,2)) OR "
                        "(status <> 'SUSPENDED' AND suspension_level = 0)", name="ck_bar_governance_level"),
        Index("ix_bar_governance_status", "status", "suspension_level", "bar_id"),
    )

class OwnerGovernance(Timestamped, db.Model):
    __tablename__ = "owner_governance"
    owner_id = db.Column(ID, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    status = db.Column(db.String(16), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    changed_by_id = db.Column(ID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    changed_at = db.Column(datetime_type(fsp=6), nullable=False)
    __table_args__ = (
        CheckConstraint(f"status IN ({STATES_SQL})", name="ck_owner_governance_status"),
        Index("ix_owner_governance_status", "status", "owner_id"),
    )
