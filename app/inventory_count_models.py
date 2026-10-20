"""Per-line V2 physical count metadata; existing V1 documents remain unchanged."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import ID, Tenant, datetime_type, tenant_args


class InventoryLineCountState(Tenant, db.Model):
    __tablename__ = "inventory_line_count_states"

    id = db.Column(ID, primary_key=True)
    inventory_line_id = db.Column(ID, nullable=False)
    counted_at = db.Column(datetime_type(fsp=6))
    counted_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    revision = db.Column(ID, nullable=False, default=1)

    __table_args__ = tenant_args(
        "inventory_line_count_states",
        ForeignKeyConstraint(
            ["bar_id", "inventory_line_id"],
            ["inventory_lines.bar_id", "inventory_lines.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "inventory_line_id", name="uq_inventory_line_count_state"),
        CheckConstraint("revision > 0", name="ck_inventory_line_count_revision"),
        Index("ix_inventory_line_count_actor", "bar_id", "counted_by_id", "counted_at"),
    )
