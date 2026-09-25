"""Persistent customer change vouchers and their settlements."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import DT, ID, MONEY, Tenant, utcnow


class ChangeVoucher(Tenant, db.Model):
    __tablename__ = "change_vouchers"

    id = db.Column(ID, primary_key=True)
    code = db.Column(db.String(32), nullable=False)
    origin_order_id = db.Column(ID, nullable=False)
    origin_payment_id = db.Column(ID, nullable=False)
    initial_amount = db.Column(MONEY, nullable=False)
    balance_amount = db.Column(MONEY, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="ACTIVE")
    customer_name = db.Column(db.String(160))
    customer_phone = db.Column(db.String(32))
    issued_at = db.Column(DT, nullable=False, default=utcnow)
    settled_at = db.Column(DT)
    issued_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        UniqueConstraint("bar_id", "id", name="uq_change_vouchers_bar_id_id"),
        UniqueConstraint("bar_id", "code", name="uq_change_vouchers_code"),
        UniqueConstraint("bar_id", "origin_payment_id", name="uq_change_vouchers_origin_payment"),
        ForeignKeyConstraint(
            ["bar_id", "origin_order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "origin_payment_id"],
            ["payments.bar_id", "payments.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("initial_amount > 0", name="ck_change_vouchers_initial_amount"),
        CheckConstraint(
            "balance_amount >= 0 AND balance_amount <= initial_amount",
            name="ck_change_vouchers_balance",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','PARTIAL','SETTLED','CANCELLED')",
            name="ck_change_vouchers_status",
        ),
        Index("ix_change_vouchers_bar_status", "bar_id", "status", "issued_at", "id"),
        Index("ix_change_vouchers_bar_created", "bar_id", "created_at", "id"),
    )


class ChangeVoucherTransaction(Tenant, db.Model):
    __tablename__ = "change_voucher_transactions"

    id = db.Column(ID, primary_key=True)
    voucher_id = db.Column(ID, nullable=False)
    reference = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    amount = db.Column(MONEY, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    target_order_id = db.Column(ID)
    cash_session_id = db.Column(ID)
    occurred_at = db.Column(DT, nullable=False, default=utcnow)
    recorded_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        UniqueConstraint("bar_id", "id", name="uq_change_voucher_transactions_bar_id_id"),
        UniqueConstraint("bar_id", "reference", name="uq_change_voucher_transactions_reference"),
        ForeignKeyConstraint(
            ["bar_id", "voucher_id"],
            ["change_vouchers.bar_id", "change_vouchers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "target_order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "cash_session_id"],
            ["cash_sessions.bar_id", "cash_sessions.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0", name="ck_change_voucher_transactions_amount"),
        CheckConstraint(
            "kind IN ('REDEEM','CASH_REFUND')",
            name="ck_change_voucher_transactions_kind",
        ),
        CheckConstraint(
            "(kind = 'REDEEM' AND target_order_id IS NOT NULL AND cash_session_id IS NULL) OR "
            "(kind = 'CASH_REFUND' AND target_order_id IS NULL AND cash_session_id IS NOT NULL)",
            name="ck_change_voucher_transactions_destination",
        ),
        Index("ix_change_voucher_transactions_voucher", "bar_id", "voucher_id", "occurred_at", "id"),
        Index("ix_change_voucher_transactions_order", "bar_id", "target_order_id", "kind", "id"),
        Index("ix_change_voucher_transactions_bar_created", "bar_id", "created_at", "id"),
    )
