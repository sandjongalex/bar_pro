"""Purchase-domain columns added after the core declarative models.

The original project keeps most persistence declarations in ``app.models``.
Purchase-specific extensions live here so the purchasing workflow can evolve
without making that already large module harder to maintain. SQLAlchemy
supports adding mapped columns to declarative classes after their declaration;
``create_app`` imports this module immediately after ``app.models`` so Flask-
Migrate and runtime queries see the complete metadata.
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint

from app.extensions import db
from app.models import Purchase, PurchaseLine, Supplier


if not hasattr(Supplier, "note"):
    Supplier.note = db.Column(db.String(500))

if not hasattr(Purchase, "purchase_date"):
    Purchase.purchase_date = db.Column(db.Date, nullable=False)
if not hasattr(Purchase, "notes"):
    Purchase.notes = db.Column(db.String(500))

if not hasattr(PurchaseLine, "purchase_unit"):
    PurchaseLine.purchase_unit = db.Column(db.String(16), nullable=False)
if not hasattr(PurchaseLine, "purchase_quantity"):
    PurchaseLine.purchase_quantity = db.Column(db.Numeric(20, 6), nullable=False)
if not hasattr(PurchaseLine, "units_per_case_snapshot"):
    PurchaseLine.units_per_case_snapshot = db.Column(db.Integer)
if not hasattr(PurchaseLine, "purchase_unit_price_snapshot"):
    PurchaseLine.purchase_unit_price_snapshot = db.Column(db.Numeric(19, 4), nullable=False)


# Keep declarative metadata identical to the Alembic migration.  Tests compare
# database check constraints to metadata by name and SQL expression.
_purchase_line_constraint_names = {
    constraint.name for constraint in PurchaseLine.__table__.constraints if constraint.name
}
for constraint in (
    CheckConstraint(
        "purchase_unit IN ('CASE','BOTTLE')",
        name="ck_purchase_lines_purchase_unit",
    ),
    CheckConstraint(
        "purchase_quantity > 0",
        name="ck_purchase_lines_purchase_quantity",
    ),
    CheckConstraint(
        "(purchase_unit = 'BOTTLE' AND units_per_case_snapshot IS NULL) OR "
        "(purchase_unit = 'CASE' AND units_per_case_snapshot IS NOT NULL AND units_per_case_snapshot > 0)",
        name="ck_purchase_lines_case_size",
    ),
):
    if constraint.name not in _purchase_line_constraint_names:
        PurchaseLine.__table__.append_constraint(constraint)
