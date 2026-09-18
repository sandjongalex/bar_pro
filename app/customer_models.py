"""Customer receivables, returnable cases and persistent user notifications."""
from __future__ import annotations

from app.extensions import db
from app.models import ID, MONEY, DT, Tenant, Timestamped, datetime_type, tenant_args
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint


class CustomerLedgerEntry(Tenant, db.Model):
    __tablename__ = "customer_ledger_entries"

    id = db.Column(ID, primary_key=True)
    customer_id = db.Column(ID, nullable=False)
    order_id = db.Column(ID)
    reference = db.Column(db.String(64), nullable=False)
    entry_kind = db.Column(db.String(16), nullable=False)
    amount_delta = db.Column(MONEY, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    method = db.Column(db.String(16))
    provider_code = db.Column(db.String(32))
    provider_transaction_id = db.Column(db.String(128))
    cash_session_id = db.Column(ID)
    reversal_of_id = db.Column(ID)
    reason = db.Column(db.String(500), nullable=False)
    occurred_at = db.Column(datetime_type(fsp=6), nullable=False)
    recorded_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = tenant_args(
        "customer_ledger_entries",
        ForeignKeyConstraint(
            ["bar_id", "customer_id"],
            ["customers.bar_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "cash_session_id"],
            ["cash_sessions.bar_id", "cash_sessions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "reversal_of_id"],
            ["customer_ledger_entries.bar_id", "customer_ledger_entries.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "reference", name="uq_customer_ledger_reference"),
        UniqueConstraint("bar_id", "reversal_of_id", name="uq_customer_ledger_reversal"),
        CheckConstraint("amount_delta <> 0", name="ck_customer_ledger_amount"),
        CheckConstraint(
            "entry_kind IN ('CREDIT_SALE','PAYMENT','REVERSAL','ADJUSTMENT')",
            name="ck_customer_ledger_kind",
        ),
        Index("ix_customer_ledger_customer_time", "bar_id", "customer_id", "occurred_at", "id"),
        Index("ix_customer_ledger_order", "bar_id", "order_id", "id"),
    )


class CustomerCaseEntry(Tenant, db.Model):
    __tablename__ = "customer_case_entries"

    id = db.Column(ID, primary_key=True)
    customer_id = db.Column(ID, nullable=False)
    product_id = db.Column(ID, nullable=False)
    order_id = db.Column(ID)
    quantity_delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    occurred_at = db.Column(datetime_type(fsp=6), nullable=False)
    recorded_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = tenant_args(
        "customer_case_entries",
        ForeignKeyConstraint(
            ["bar_id", "customer_id"],
            ["customers.bar_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "product_id"],
            ["products.bar_id", "products.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity_delta <> 0", name="ck_customer_case_quantity"),
        Index("ix_customer_case_customer_product", "bar_id", "customer_id", "product_id", "id"),
    )


class UserNotification(Timestamped, db.Model):
    __tablename__ = "user_notifications"

    id = db.Column(ID, primary_key=True)
    bar_id = db.Column(ID, db.ForeignKey("bars.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    order_id = db.Column(ID)
    kind = db.Column(db.String(32), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(500), nullable=False)
    read_at = db.Column(datetime_type(fsp=6))

    __table_args__ = (
        ForeignKeyConstraint(
            ["bar_id", "order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_user_notifications_user_unread", "user_id", "read_at", "created_at", "id"),
        Index("ix_user_notifications_bar_order", "bar_id", "order_id", "id"),
    )
