"""Persistence model for Bar Manager Pro.

This module deliberately contains persistence only.  State transitions and
cross-row rules belong to services; database constraints below defend the
invariants that MySQL can enforce without business context.
"""
from datetime import datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Computed, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BINARY as MYSQL_BINARY, VARBINARY as MYSQL_VARBINARY, DATETIME as MYSQL_DATETIME, INTEGER as MYSQL_INTEGER, SMALLINT as MYSQL_SMALLINT
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

ID = db.BigInteger().with_variant(db.Integer, "sqlite")
MONEY = db.Numeric(19, 4)
QTY = db.Numeric(20, 6)
DT = MYSQL_DATETIME(fsp=6).with_variant(db.DateTime(), "sqlite")
def integer(unsigned=False):
    return MYSQL_INTEGER(unsigned=unsigned).with_variant(db.Integer(), "sqlite")


def smallinteger(unsigned=False):
    return MYSQL_SMALLINT(unsigned=unsigned).with_variant(db.SmallInteger(), "sqlite")


def datetime_type(fsp=6):
    return MYSQL_DATETIME(fsp=fsp).with_variant(db.DateTime(), "sqlite")


def utcnow():
    return datetime.now(timezone.utc)


class Timestamped:
    created_at = db.Column(DT, nullable=False, default=utcnow)
    updated_at = db.Column(DT, nullable=False, default=utcnow, onupdate=utcnow)


class Tenant(Timestamped):
    """Columns and the stable composite key carried by every bar resource."""
    bar_id = db.Column(ID, db.ForeignKey("bars.id", ondelete="RESTRICT"), nullable=False, index=True)


class User(UserMixin, Timestamped, db.Model):
    __tablename__ = "users"
    id = db.Column(ID, primary_key=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    display_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(16), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    credentials_version = db.Column(integer(unsigned=True), nullable=False, default=1)
    last_login_at = db.Column(datetime_type(fsp=6))
    disabled_at = db.Column(datetime_type(fsp=6))
    __table_args__ = (CheckConstraint("category IN ('SUPER_ADMIN','OWNER','EMPLOYEE')", name="ck_users_category"), CheckConstraint("credentials_version > 0", name="ck_users_credentials_version"), Index("ix_users_category_active", "category", "is_active", "id"))

    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)


class Bar(Timestamped, db.Model):
    __tablename__ = "bars"
    id = db.Column(ID, primary_key=True)
    owner_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    address = db.Column(db.String(500))
    phone = db.Column(db.String(32))
    logo_key = db.Column(db.String(255))
    status = db.Column(db.String(16), nullable=False, default="ACTIVE")
    timezone = db.Column(db.String(64), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="XAF")
    stock_alert_threshold = db.Column(QTY, nullable=False, default=Decimal("0"))
    credit_sales_enabled = db.Column(db.Boolean, nullable=False, default=False)
    suspended_at = db.Column(datetime_type(fsp=6)); suspension_reason = db.Column(db.String(500))
    owner = db.relationship("User", foreign_keys=[owner_id], backref="owned_bars")
    __table_args__ = (CheckConstraint("status IN ('ACTIVE','SUSPENDED')", name="ck_bars_status"), Index("ix_bars_owner_status", "owner_id", "status", "id"))


def tenant_args(name, *extra):
    return (UniqueConstraint("bar_id", "id", name=f"uq_{name}_bar_id_id"), *extra, Index(f"ix_{name}_bar_created", "bar_id", "created_at", "id"))


class StaffAssignment(Tenant, db.Model):
    __tablename__="staff_assignments"; id=db.Column(ID, primary_key=True); user_id=db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True); role=db.Column(db.String(16), nullable=False); started_at=db.Column(datetime_type(fsp=6), nullable=False); ended_at=db.Column(datetime_type(fsp=6)); active_user_id=db.Column(ID, Computed("CASE WHEN ended_at IS NULL THEN user_id ELSE NULL END")); user=db.relationship("User")
    __table_args__=tenant_args("staff_assignments", UniqueConstraint("active_user_id", name="uq_staff_assignments_active_user"), CheckConstraint("role IN ('BAR_ADMIN','CASHIER','SERVER')", name="ck_staff_role"), CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_staff_dates"), Index("ix_staff_bar_role_ended", "bar_id","role","ended_at","id"))

class ProductCategory(Tenant, db.Model):
    __tablename__="product_categories"; id=db.Column(ID, primary_key=True); name=db.Column(db.String(100), nullable=False); is_active=db.Column(db.Boolean, nullable=False, default=True)
    __table_args__=tenant_args("product_categories", UniqueConstraint("bar_id","name",name="uq_product_categories_name"), Index("ix_product_categories_active_name","bar_id","is_active","name","id"))

class Product(Tenant, db.Model):
    __tablename__="products"; id=db.Column(ID, primary_key=True); category_id=db.Column(ID, nullable=False); sku=db.Column(db.String(64),nullable=False); name=db.Column(db.String(160),nullable=False); base_unit=db.Column(db.String(16),nullable=False); sale_price=db.Column(MONEY,nullable=False); valuation_unit_cost=db.Column(MONEY,nullable=False); stock_alert_threshold=db.Column(QTY,nullable=False,default=Decimal("0")); units_per_case=db.Column(integer(unsigned=True)); image_key=db.Column(db.String(255)); is_active=db.Column(db.Boolean,nullable=False,default=True)
    __table_args__=tenant_args("products", ForeignKeyConstraint(["bar_id","category_id"],["product_categories.bar_id","product_categories.id"],ondelete="RESTRICT"), UniqueConstraint("bar_id","sku",name="uq_products_sku"), CheckConstraint("sale_price >= 0 AND valuation_unit_cost >= 0",name="ck_products_prices"), Index("ix_products_bar_category_active","bar_id","category_id","is_active","id"))

class StockBalance(Tenant, db.Model):
    __tablename__="stock_balances"; id=db.Column(ID,primary_key=True); product_id=db.Column(ID,nullable=False); quantity=db.Column(QTY,nullable=False,default=Decimal("0")); version=db.Column(ID,nullable=False,default=0)
    __table_args__=tenant_args("stock_balances", ForeignKeyConstraint(["bar_id","product_id"],["products.bar_id","products.id"],ondelete="RESTRICT"), UniqueConstraint("bar_id","product_id",name="uq_stock_balances_product"))

class Supplier(Tenant, db.Model):
    __tablename__="suppliers"; id=db.Column(ID,primary_key=True); name=db.Column(db.String(160),nullable=False); phone=db.Column(db.String(32)); email=db.Column(db.String(254)); address=db.Column(db.String(500)); is_active=db.Column(db.Boolean,nullable=False,default=True)
    __table_args__=tenant_args("suppliers", Index("ix_suppliers_active_name","bar_id","is_active","name","id"))

class Purchase(Tenant, db.Model):
    __tablename__="purchases"; id=db.Column(ID,primary_key=True); supplier_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); supplier_invoice_reference=db.Column(db.String(100)); status=db.Column(db.String(16),nullable=False,default="DRAFT"); currency=db.Column(db.String(3),nullable=False); supplier_name_snapshot=db.Column(db.String(160),nullable=False); subtotal_amount=db.Column(MONEY,nullable=False,default=0); discount_amount=db.Column(MONEY,nullable=False,default=0); tax_amount=db.Column(MONEY,nullable=False,default=0); total_amount=db.Column(MONEY,nullable=False,default=0); posted_at=db.Column(datetime_type(fsp=6)); cancelled_at=db.Column(datetime_type(fsp=6)); created_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("purchases",ForeignKeyConstraint(["bar_id","supplier_id"],["suppliers.bar_id","suppliers.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_purchases_reference"),CheckConstraint("status IN ('DRAFT','POSTED','CANCELLED')",name="ck_purchases_status"),CheckConstraint("subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 AND total_amount = subtotal_amount - discount_amount + tax_amount AND discount_amount <= subtotal_amount",name="ck_purchases_amounts"))

class PurchaseLine(Tenant, db.Model):
    __tablename__="purchase_lines"; id=db.Column(ID,primary_key=True); purchase_id=db.Column(ID,nullable=False); product_id=db.Column(ID,nullable=False); line_no=db.Column(integer(unsigned=True),nullable=False); product_name_snapshot=db.Column(db.String(160),nullable=False); unit_snapshot=db.Column(db.String(16),nullable=False); quantity=db.Column(QTY,nullable=False); unit_cost_snapshot=db.Column(MONEY,nullable=False); subtotal_amount=db.Column(MONEY,nullable=False); discount_amount=db.Column(MONEY,nullable=False,default=0); tax_amount=db.Column(MONEY,nullable=False,default=0); total_amount=db.Column(MONEY,nullable=False)
    __table_args__=tenant_args("purchase_lines",ForeignKeyConstraint(["bar_id","purchase_id"],["purchases.bar_id","purchases.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","product_id"],["products.bar_id","products.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","purchase_id","line_no",name="uq_purchase_lines_no"),UniqueConstraint("bar_id","id","product_id",name="uq_purchase_lines_id_product"),CheckConstraint("line_no > 0 AND quantity > 0 AND unit_cost_snapshot >= 0 AND subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 AND discount_amount <= subtotal_amount AND total_amount = subtotal_amount-discount_amount+tax_amount",name="ck_purchase_lines_amounts"))

class Inventory(Tenant, db.Model):
    __tablename__="inventories"; id=db.Column(ID,primary_key=True); reference=db.Column(db.String(64),nullable=False); status=db.Column(db.String(16),nullable=False,default="DRAFT"); counted_at=db.Column(datetime_type(fsp=6),nullable=False); posted_at=db.Column(datetime_type(fsp=6)); cancelled_at=db.Column(datetime_type(fsp=6)); created_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); reason=db.Column(db.String(500),nullable=False)
    __table_args__=tenant_args("inventories",UniqueConstraint("bar_id","reference",name="uq_inventories_reference"),CheckConstraint("status IN ('DRAFT','POSTED','CANCELLED')",name="ck_inventories_status"))

class InventoryLine(Tenant, db.Model):
    __tablename__="inventory_lines"; id=db.Column(ID,primary_key=True); inventory_id=db.Column(ID,nullable=False); product_id=db.Column(ID,nullable=False); expected_quantity_snapshot=db.Column(QTY,nullable=False); balance_version_snapshot=db.Column(ID,nullable=False); counted_quantity=db.Column(QTY); difference_quantity=db.Column(QTY,Computed("counted_quantity - expected_quantity_snapshot"))
    __table_args__=tenant_args("inventory_lines",ForeignKeyConstraint(["bar_id","inventory_id"],["inventories.bar_id","inventories.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","product_id"],["products.bar_id","products.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","inventory_id","product_id",name="uq_inventory_lines_product"),UniqueConstraint("bar_id","id","product_id",name="uq_inventory_lines_id_product"),CheckConstraint("counted_quantity IS NULL OR counted_quantity >= 0",name="ck_inventory_lines_counted"))

class BarTable(Tenant,db.Model):
    __tablename__="bar_tables"; id=db.Column(ID,primary_key=True); label=db.Column(db.String(64),nullable=False); capacity=db.Column(smallinteger(unsigned=True)); is_active=db.Column(db.Boolean,nullable=False,default=True)
    __table_args__=tenant_args("bar_tables",UniqueConstraint("bar_id","label",name="uq_bar_tables_label"),CheckConstraint("capacity IS NULL OR capacity > 0",name="ck_bar_tables_capacity"))

class Customer(Tenant,db.Model):
    __tablename__="customers"; id=db.Column(ID,primary_key=True); display_name=db.Column(db.String(160),nullable=False); phone=db.Column(db.String(32)); email=db.Column(db.String(254)); is_active=db.Column(db.Boolean,nullable=False,default=True)
    __table_args__=tenant_args("customers",Index("ix_customers_name","bar_id","display_name","id"))

# Financial and operational models use the same explicit tenant composite keys.
class Order(Tenant,db.Model):
    __tablename__="orders"; id=db.Column(ID,primary_key=True); reference=db.Column(db.String(64),nullable=False); table_id=db.Column(ID); customer_id=db.Column(ID); assigned_staff_id=db.Column(ID); status=db.Column(db.String(16),nullable=False,default="DRAFT"); payment_status=db.Column(db.String(16),nullable=False,default="UNPAID"); notes=db.Column(db.String(500)); currency=db.Column(db.String(3),nullable=False); customer_name_snapshot=db.Column(db.String(160)); table_label_snapshot=db.Column(db.String(64)); subtotal_amount=db.Column(MONEY,nullable=False,default=0); discount_amount=db.Column(MONEY,nullable=False,default=0); tax_amount=db.Column(MONEY,nullable=False,default=0); total_amount=db.Column(MONEY,nullable=False,default=0); posted_at=db.Column(datetime_type(fsp=6)); closed_at=db.Column(datetime_type(fsp=6)); cancelled_at=db.Column(datetime_type(fsp=6)); created_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("orders",ForeignKeyConstraint(["bar_id","table_id"],["bar_tables.bar_id","bar_tables.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","customer_id"],["customers.bar_id","customers.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","assigned_staff_id"],["staff_assignments.bar_id","staff_assignments.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_orders_reference"),CheckConstraint("status IN ('DRAFT','CONFIRMED','SERVED','CANCELLED')",name="ck_orders_status"),CheckConstraint("payment_status IN ('UNPAID','PARTIAL','PAID')",name="ck_orders_payment_status"),CheckConstraint("subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 AND discount_amount <= subtotal_amount AND total_amount = subtotal_amount-discount_amount+tax_amount",name="ck_orders_amounts"))

class OrderLine(Tenant,db.Model):
    __tablename__="order_lines"; id=db.Column(ID,primary_key=True); order_id=db.Column(ID,nullable=False); product_id=db.Column(ID,nullable=False); line_no=db.Column(integer(unsigned=True),nullable=False); product_name_snapshot=db.Column(db.String(160),nullable=False); unit_snapshot=db.Column(db.String(16),nullable=False); quantity=db.Column(QTY,nullable=False); note=db.Column(db.String(500)); unit_sale_price_snapshot=db.Column(MONEY,nullable=False); unit_cost_snapshot=db.Column(MONEY,nullable=False); subtotal_amount=db.Column(MONEY,nullable=False); discount_amount=db.Column(MONEY,nullable=False,default=0); tax_amount=db.Column(MONEY,nullable=False,default=0); total_amount=db.Column(MONEY,nullable=False)
    __table_args__=tenant_args("order_lines",ForeignKeyConstraint(["bar_id","order_id"],["orders.bar_id","orders.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","product_id"],["products.bar_id","products.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","order_id","line_no",name="uq_order_lines_no"),UniqueConstraint("bar_id","order_id","id",name="uq_order_lines_order_id"),UniqueConstraint("bar_id","id","product_id",name="uq_order_lines_id_product"),CheckConstraint("line_no > 0 AND quantity > 0 AND unit_sale_price_snapshot >= 0 AND unit_cost_snapshot >= 0 AND subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 AND discount_amount <= subtotal_amount AND total_amount = subtotal_amount-discount_amount+tax_amount",name="ck_order_lines_amounts"))

class CashSession(Tenant,db.Model):
    __tablename__="cash_sessions"; id=db.Column(ID,primary_key=True); reference=db.Column(db.String(64),nullable=False); status=db.Column(db.String(16),nullable=False,default="OPEN"); opened_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); closed_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT")); opened_at=db.Column(datetime_type(fsp=6),nullable=False); closed_at=db.Column(datetime_type(fsp=6)); currency=db.Column(db.String(3),nullable=False); opening_amount=db.Column(MONEY,nullable=False); expected_closing_amount=db.Column(MONEY); counted_closing_amount=db.Column(MONEY); closing_difference=db.Column(MONEY,Computed("counted_closing_amount - expected_closing_amount")); open_bar_id=db.Column(ID,Computed("CASE WHEN status = 'OPEN' THEN bar_id ELSE NULL END"))
    __table_args__=tenant_args("cash_sessions",UniqueConstraint("bar_id","reference",name="uq_cash_sessions_reference"),UniqueConstraint("open_bar_id",name="uq_cash_sessions_open_bar"),CheckConstraint("status IN ('OPEN','CLOSED')",name="ck_cash_sessions_status"),CheckConstraint("opening_amount >= 0 AND (counted_closing_amount IS NULL OR counted_closing_amount >= 0)",name="ck_cash_sessions_amounts"))

class ExpenseCategory(Tenant,db.Model):
    __tablename__="expense_categories"; id=db.Column(ID,primary_key=True); name=db.Column(db.String(100),nullable=False); is_active=db.Column(db.Boolean,nullable=False,default=True)
    __table_args__=tenant_args("expense_categories",UniqueConstraint("bar_id","name",name="uq_expense_categories_name"))

class Plan(Timestamped,db.Model):
    __tablename__="plans"; id=db.Column(ID,primary_key=True); code=db.Column(db.String(32),nullable=False,unique=True); name=db.Column(db.String(100),nullable=False); price_amount=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False,default="XAF"); duration_days=db.Column(integer(unsigned=True),nullable=False); is_active=db.Column(db.Boolean,nullable=False,default=True)
    __table_args__=(CheckConstraint("price_amount >= 0 AND duration_days > 0",name="ck_plans_values"),Index("ix_plans_active_name","is_active","name","id"))

class Subscription(Tenant,db.Model):
    __tablename__="subscriptions"; id=db.Column(ID,primary_key=True); plan_id=db.Column(ID,db.ForeignKey("plans.id",ondelete="RESTRICT"),nullable=False); reference=db.Column(db.String(64),nullable=False); status=db.Column(db.String(16),nullable=False,default="PENDING"); plan_code_snapshot=db.Column(db.String(32),nullable=False); plan_name_snapshot=db.Column(db.String(100),nullable=False); price_amount_snapshot=db.Column(MONEY,nullable=False); duration_days_snapshot=db.Column(integer(unsigned=True),nullable=False); currency=db.Column(db.String(3),nullable=False); starts_at=db.Column(datetime_type(fsp=6),nullable=False); ends_at=db.Column(datetime_type(fsp=6),nullable=False); activated_at=db.Column(datetime_type(fsp=6)); expired_at=db.Column(datetime_type(fsp=6)); cancelled_at=db.Column(datetime_type(fsp=6)); active_bar_id=db.Column(ID,Computed("CASE WHEN status = 'ACTIVE' THEN bar_id ELSE NULL END")); created_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("subscriptions",UniqueConstraint("bar_id","reference",name="uq_subscriptions_reference"),UniqueConstraint("active_bar_id",name="uq_subscriptions_active_bar"),CheckConstraint("status IN ('PENDING','ACTIVE','EXPIRED','CANCELLED') AND price_amount_snapshot >= 0 AND duration_days_snapshot > 0 AND ends_at > starts_at",name="ck_subscriptions_values"))

class AuditLog(db.Model):
    __tablename__="audit_logs"; id=db.Column(ID,primary_key=True); scope=db.Column(db.String(16),nullable=False); bar_id=db.Column(ID,db.ForeignKey("bars.id",ondelete="RESTRICT")); actor_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); action=db.Column(db.String(96),nullable=False); target_table=db.Column(db.String(64)); target_id=db.Column(ID); outcome=db.Column(db.String(16),nullable=False); reason=db.Column(db.String(500),nullable=False); request_id=db.Column(MYSQL_BINARY(16).with_variant(db.LargeBinary(16), "sqlite"),nullable=False); changes=db.Column(db.JSON); occurred_at=db.Column(datetime_type(fsp=6),nullable=False); created_at=db.Column(datetime_type(fsp=6),nullable=False,default=utcnow)
    __table_args__=(CheckConstraint("(scope = 'BAR' AND bar_id IS NOT NULL) OR (scope = 'PLATFORM' AND bar_id IS NULL)",name="ck_audit_scope"),CheckConstraint("outcome IN ('SUCCESS','DENIED')",name="ck_audit_outcome"),CheckConstraint("(target_table IS NULL AND target_id IS NULL) OR (target_table IS NOT NULL AND target_id IS NOT NULL)",name="ck_audit_target"),Index("ix_audit_bar_occurred","bar_id","occurred_at","id"),Index("ix_audit_request","request_id"))

class ApiToken(Tenant,db.Model):
    __tablename__="api_tokens"; id=db.Column(ID,primary_key=True); user_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); label=db.Column(db.String(100),nullable=False); token_digest=db.Column(MYSQL_BINARY(32).with_variant(db.LargeBinary(32), "sqlite"),nullable=False,unique=True); family_id=db.Column(MYSQL_BINARY(16).with_variant(db.LargeBinary(16), "sqlite"),nullable=False); rotated_from_id=db.Column(ID); credentials_version_snapshot=db.Column(integer(unsigned=True),nullable=False); issued_at=db.Column(datetime_type(fsp=6),nullable=False); expires_at=db.Column(datetime_type(fsp=6),nullable=False); last_used_at=db.Column(datetime_type(fsp=6))
    __table_args__=tenant_args("api_tokens",ForeignKeyConstraint(["bar_id","rotated_from_id"],["api_tokens.bar_id","api_tokens.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","id","user_id",name="uq_api_tokens_id_user"),CheckConstraint("expires_at > issued_at",name="ck_api_tokens_dates"),Index("ix_api_tokens_family","bar_id","family_id","expires_at","id"))

class TokenRevocation(Tenant,db.Model):
    __tablename__="token_revocations"; id=db.Column(ID,primary_key=True); api_token_id=db.Column(ID,nullable=False); revoked_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); revoked_at=db.Column(datetime_type(fsp=6),nullable=False); reason=db.Column(db.String(500),nullable=False)
    __table_args__=tenant_args("token_revocations",ForeignKeyConstraint(["bar_id","api_token_id"],["api_tokens.bar_id","api_tokens.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","api_token_id",name="uq_token_revocations_token"))

class IdempotencyRecord(Tenant,db.Model):
    __tablename__="idempotency_records"; id=db.Column(ID,primary_key=True); actor_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); operation=db.Column(db.String(64),nullable=False); idempotency_key=db.Column(MYSQL_VARBINARY(128).with_variant(db.LargeBinary(128), "sqlite"),nullable=False); request_hash=db.Column(MYSQL_BINARY(32).with_variant(db.LargeBinary(32), "sqlite"),nullable=False); status=db.Column(db.String(16),nullable=False); response_status=db.Column(smallinteger(unsigned=True)); response_body=db.Column(db.JSON); completed_at=db.Column(datetime_type(fsp=6))
    __table_args__=tenant_args("idempotency_records",UniqueConstraint("bar_id","actor_id","operation","idempotency_key",name="uq_idempotency_actor_key"),CheckConstraint("status IN ('PROCESSING','COMPLETED')",name="ck_idempotency_status"),Index("ix_idempotency_completed","bar_id","completed_at","id"))

class UserSession(Timestamped,db.Model):
    __tablename__="user_sessions"; id=db.Column(ID,primary_key=True); user_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT")); session_digest=db.Column(MYSQL_BINARY(32).with_variant(db.LargeBinary(32), "sqlite"),nullable=False,unique=True); csrf_digest=db.Column(MYSQL_BINARY(32).with_variant(db.LargeBinary(32), "sqlite"),nullable=False); credentials_version_snapshot=db.Column(integer(unsigned=True)); expires_at=db.Column(datetime_type(fsp=6),nullable=False); last_seen_at=db.Column(datetime_type(fsp=6)); revoked_at=db.Column(datetime_type(fsp=6))
    __table_args__=(CheckConstraint("expires_at > created_at",name="ck_user_sessions_dates"),CheckConstraint("(user_id IS NULL AND credentials_version_snapshot IS NULL) OR (user_id IS NOT NULL AND credentials_version_snapshot IS NOT NULL)",name="ck_user_sessions_auth"),Index("ix_user_sessions_user_expiry","user_id","expires_at","id"),Index("ix_user_sessions_expiry","expires_at","id"))

class StockMovement(Tenant, db.Model):
    __tablename__="stock_movements"; id=db.Column(ID,primary_key=True); product_id=db.Column(ID,nullable=False); movement_type=db.Column(db.String(24),nullable=False); quantity_delta=db.Column(QTY,nullable=False); unit_snapshot=db.Column(db.String(16),nullable=False); unit_cost_snapshot=db.Column(MONEY,nullable=False); purchase_line_id=db.Column(ID); order_line_id=db.Column(ID); order_return_line_id=db.Column(ID); inventory_line_id=db.Column(ID); reversal_of_id=db.Column(ID); manual_kind=db.Column(db.String(16)); reason=db.Column(db.String(500),nullable=False); occurred_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("stock_movements",ForeignKeyConstraint(["bar_id","product_id"],["products.bar_id","products.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","purchase_line_id"],["purchase_lines.bar_id","purchase_lines.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","order_line_id"],["order_lines.bar_id","order_lines.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","order_return_line_id"],["order_return_lines.bar_id","order_return_lines.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","inventory_line_id"],["inventory_lines.bar_id","inventory_lines.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","reversal_of_id"],["stock_movements.bar_id","stock_movements.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","purchase_line_id",name="uq_stock_purchase_line"),UniqueConstraint("bar_id","order_line_id",name="uq_stock_order_line"),UniqueConstraint("bar_id","order_return_line_id",name="uq_stock_return_line"),UniqueConstraint("bar_id","inventory_line_id",name="uq_stock_inventory_line"),UniqueConstraint("bar_id","reversal_of_id",name="uq_stock_reversal"),CheckConstraint("quantity_delta <> 0 AND unit_cost_snapshot >= 0",name="ck_stock_movement_values"),Index("ix_stock_movements_product_time","bar_id","product_id","occurred_at","id"))

class OrderReturn(Tenant,db.Model):
    __tablename__="order_returns"; id=db.Column(ID,primary_key=True); order_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); status=db.Column(db.String(16),nullable=False,default="DRAFT"); currency=db.Column(db.String(3),nullable=False); total_amount=db.Column(MONEY,nullable=False,default=0); reason=db.Column(db.String(500),nullable=False); posted_at=db.Column(datetime_type(fsp=6)); cancelled_at=db.Column(datetime_type(fsp=6)); created_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("order_returns",ForeignKeyConstraint(["bar_id","order_id"],["orders.bar_id","orders.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_order_returns_reference"),UniqueConstraint("bar_id","order_id","id",name="uq_order_returns_order_id"),CheckConstraint("status IN ('DRAFT','POSTED','CANCELLED') AND total_amount >= 0",name="ck_order_returns_values"))

class OrderReturnLine(Tenant,db.Model):
    __tablename__="order_return_lines"; id=db.Column(ID,primary_key=True); order_return_id=db.Column(ID,nullable=False); order_id=db.Column(ID,nullable=False); order_line_id=db.Column(ID,nullable=False); product_id=db.Column(ID,nullable=False); quantity=db.Column(QTY,nullable=False); restock_quantity=db.Column(QTY,nullable=False); credit_amount=db.Column(MONEY,nullable=False)
    __table_args__=tenant_args("order_return_lines",ForeignKeyConstraint(["bar_id","order_return_id"],["order_returns.bar_id","order_returns.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","order_id"],["orders.bar_id","orders.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","order_line_id"],["order_lines.bar_id","order_lines.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","product_id"],["products.bar_id","products.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","order_return_id","order_line_id",name="uq_order_return_lines_source"),UniqueConstraint("bar_id","id","product_id",name="uq_order_return_lines_id_product"),CheckConstraint("quantity > 0 AND restock_quantity >= 0 AND restock_quantity <= quantity AND credit_amount >= 0",name="ck_order_return_lines_values"))

class Payment(Tenant,db.Model):
    __tablename__="payments"; id=db.Column(ID,primary_key=True); order_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); amount=db.Column(MONEY,nullable=False); amount_presented=db.Column(MONEY,nullable=False); amount_applied=db.Column(MONEY,nullable=False); change_given=db.Column(MONEY,nullable=False,default=0); currency=db.Column(db.String(3),nullable=False); method=db.Column(db.String(16),nullable=False); provider_code=db.Column(db.String(32)); provider_transaction_id=db.Column(db.String(128)); cash_session_id=db.Column(ID); cash_holder=db.Column(db.String(16)); staff_assignment_id=db.Column(ID); received_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("payments",ForeignKeyConstraint(["bar_id","order_id"],["orders.bar_id","orders.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","cash_session_id"],["cash_sessions.bar_id","cash_sessions.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","staff_assignment_id"],["staff_assignments.bar_id","staff_assignments.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_payments_reference"),UniqueConstraint("bar_id","provider_code","provider_transaction_id",name="uq_payments_provider"),UniqueConstraint("bar_id","order_id","id",name="uq_payments_order_id"),CheckConstraint("amount > 0 AND method IN ('CASH','MOBILE_MONEY','CARD','BANK_TRANSFER')",name="ck_payments_values"))

class Refund(Tenant,db.Model):
    __tablename__="refunds"; id=db.Column(ID,primary_key=True); payment_id=db.Column(ID,nullable=False); order_id=db.Column(ID,nullable=False); order_return_id=db.Column(ID); reference=db.Column(db.String(64),nullable=False); amount=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); method=db.Column(db.String(16),nullable=False); provider_code=db.Column(db.String(32)); provider_transaction_id=db.Column(db.String(128)); cash_session_id=db.Column(ID); cash_holder=db.Column(db.String(16)); staff_assignment_id=db.Column(ID); reason=db.Column(db.String(500),nullable=False); refunded_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("refunds",ForeignKeyConstraint(["bar_id","payment_id"],["payments.bar_id","payments.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","order_id"],["orders.bar_id","orders.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","order_return_id"],["order_returns.bar_id","order_returns.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","cash_session_id"],["cash_sessions.bar_id","cash_sessions.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","staff_assignment_id"],["staff_assignments.bar_id","staff_assignments.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_refunds_reference"),UniqueConstraint("bar_id","provider_code","provider_transaction_id",name="uq_refunds_provider"),CheckConstraint("amount > 0 AND method IN ('CASH','MOBILE_MONEY','CARD','BANK_TRANSFER')",name="ck_refunds_values"))

class SupplierPayment(Tenant,db.Model):
    __tablename__="supplier_payments"; id=db.Column(ID,primary_key=True); purchase_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); amount=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); entry_kind=db.Column(db.String(16),nullable=False); reversal_of_id=db.Column(ID); method=db.Column(db.String(16),nullable=False); provider_code=db.Column(db.String(32)); provider_transaction_id=db.Column(db.String(128)); cash_session_id=db.Column(ID); paid_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); reason=db.Column(db.String(500),nullable=False)
    __table_args__=tenant_args("supplier_payments",ForeignKeyConstraint(["bar_id","purchase_id"],["purchases.bar_id","purchases.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","reversal_of_id"],["supplier_payments.bar_id","supplier_payments.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","cash_session_id"],["cash_sessions.bar_id","cash_sessions.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_supplier_payments_reference"),UniqueConstraint("bar_id","reversal_of_id",name="uq_supplier_payments_reversal"),CheckConstraint("amount > 0 AND entry_kind IN ('PAYMENT','REVERSAL')",name="ck_supplier_payments_values"))

class CashHandover(Tenant,db.Model):
    __tablename__="cash_handovers"; id=db.Column(ID,primary_key=True); staff_assignment_id=db.Column(ID,nullable=False); cash_session_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); amount=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); status=db.Column(db.String(16),nullable=False,default="DRAFT"); requested_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); received_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT")); posted_at=db.Column(datetime_type(fsp=6)); cancelled_at=db.Column(datetime_type(fsp=6))
    __table_args__=tenant_args("cash_handovers",ForeignKeyConstraint(["bar_id","staff_assignment_id"],["staff_assignments.bar_id","staff_assignments.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","cash_session_id"],["cash_sessions.bar_id","cash_sessions.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_cash_handovers_reference"),UniqueConstraint("bar_id","id","staff_assignment_id",name="uq_cash_handovers_id_staff"),CheckConstraint("amount > 0 AND status IN ('DRAFT','POSTED','CANCELLED')",name="ck_cash_handovers_values"))

class CashMovement(Tenant,db.Model):
    __tablename__="cash_movements"; id=db.Column(ID,primary_key=True); cash_session_id=db.Column(ID,nullable=False); amount_delta=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); payment_id=db.Column(ID); refund_id=db.Column(ID); supplier_payment_id=db.Column(ID); expense_id=db.Column(ID); cash_handover_id=db.Column(ID); reversal_of_id=db.Column(ID); manual_kind=db.Column(db.String(16)); reason=db.Column(db.String(500),nullable=False); occurred_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("cash_movements",ForeignKeyConstraint(["bar_id","cash_session_id"],["cash_sessions.bar_id","cash_sessions.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","payment_id",name="uq_cash_movements_payment"),UniqueConstraint("bar_id","refund_id",name="uq_cash_movements_refund"),UniqueConstraint("bar_id","supplier_payment_id",name="uq_cash_movements_supplier_payment"),UniqueConstraint("bar_id","expense_id",name="uq_cash_movements_expense"),UniqueConstraint("bar_id","cash_handover_id",name="uq_cash_movements_handover"),UniqueConstraint("bar_id","reversal_of_id",name="uq_cash_movements_reversal"),CheckConstraint("amount_delta <> 0",name="ck_cash_movements_amount"))

class StaffCashLedger(Tenant,db.Model):
    __tablename__="staff_cash_ledgers"; id=db.Column(ID,primary_key=True); staff_assignment_id=db.Column(ID,nullable=False); amount_delta=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); payment_id=db.Column(ID); refund_id=db.Column(ID); cash_handover_id=db.Column(ID); reversal_of_id=db.Column(ID); occurred_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False); reason=db.Column(db.String(500),nullable=False)
    __table_args__=tenant_args("staff_cash_ledgers",ForeignKeyConstraint(["bar_id","staff_assignment_id"],["staff_assignments.bar_id","staff_assignments.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","payment_id",name="uq_staff_ledger_payment"),UniqueConstraint("bar_id","refund_id",name="uq_staff_ledger_refund"),UniqueConstraint("bar_id","cash_handover_id",name="uq_staff_ledger_handover"),UniqueConstraint("bar_id","reversal_of_id",name="uq_staff_ledger_reversal"),UniqueConstraint("bar_id","id","staff_assignment_id",name="uq_staff_ledger_id_staff"),CheckConstraint("amount_delta <> 0",name="ck_staff_ledger_amount"))

class Expense(Tenant,db.Model):
    __tablename__="expenses"; id=db.Column(ID,primary_key=True); expense_category_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); description=db.Column(db.String(500),nullable=False); category_name_snapshot=db.Column(db.String(100),nullable=False); amount=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); entry_kind=db.Column(db.String(16),nullable=False); reversal_of_id=db.Column(ID); method=db.Column(db.String(16),nullable=False); provider_code=db.Column(db.String(32)); provider_transaction_id=db.Column(db.String(128)); cash_session_id=db.Column(ID); incurred_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("expenses",ForeignKeyConstraint(["bar_id","expense_category_id"],["expense_categories.bar_id","expense_categories.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","cash_session_id"],["cash_sessions.bar_id","cash_sessions.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_expenses_reference"),UniqueConstraint("bar_id","reversal_of_id",name="uq_expenses_reversal"),CheckConstraint("amount > 0 AND entry_kind IN ('EXPENSE','REVERSAL')",name="ck_expenses_values"))

class SubscriptionPayment(Tenant,db.Model):
    __tablename__="subscription_payments"; id=db.Column(ID,primary_key=True); subscription_id=db.Column(ID,nullable=False); reference=db.Column(db.String(64),nullable=False); amount=db.Column(MONEY,nullable=False); currency=db.Column(db.String(3),nullable=False); entry_kind=db.Column(db.String(16),nullable=False); reversal_of_id=db.Column(ID); provider_code=db.Column(db.String(32),nullable=False); provider_transaction_id=db.Column(db.String(128),nullable=False); paid_at=db.Column(datetime_type(fsp=6),nullable=False); recorded_by_id=db.Column(ID,db.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    __table_args__=tenant_args("subscription_payments",ForeignKeyConstraint(["bar_id","subscription_id"],["subscriptions.bar_id","subscriptions.id"],ondelete="RESTRICT"),ForeignKeyConstraint(["bar_id","reversal_of_id"],["subscription_payments.bar_id","subscription_payments.id"],ondelete="RESTRICT"),UniqueConstraint("bar_id","reference",name="uq_subscription_payments_reference"),UniqueConstraint("provider_code","provider_transaction_id",name="uq_subscription_payments_provider"),UniqueConstraint("bar_id","reversal_of_id",name="uq_subscription_payments_reversal"),CheckConstraint("amount > 0 AND entry_kind IN ('PAYMENT','REVERSAL')",name="ck_subscription_payments_values"))


# Explicit tenant references on financial journals; no unbound source identifiers.
for model, sources in (
    (CashMovement, {"payment_id":"payments", "refund_id":"refunds", "supplier_payment_id":"supplier_payments", "expense_id":"expenses", "cash_handover_id":"cash_handovers", "reversal_of_id":"cash_movements"}),
    (StaffCashLedger, {"payment_id":"payments", "refund_id":"refunds", "cash_handover_id":"cash_handovers", "reversal_of_id":"staff_cash_ledgers"}),
    (Expense, {"reversal_of_id":"expenses"}),
):
    for column, target in sources.items():
        model.__table__.append_constraint(ForeignKeyConstraint(["bar_id", column], [f"{target}.bar_id", f"{target}.id"], name=f"fk_{model.__tablename__}_{column}", ondelete="RESTRICT"))

Payment.__table__.append_constraint(CheckConstraint("amount = amount_applied AND amount_applied > 0 AND change_given >= 0 AND amount_presented = amount_applied + change_given", name="ck_payments_tender"))
StockBalance.__table__.append_constraint(CheckConstraint("quantity >= 0 AND version >= 0", name="ck_stock_balances_nonnegative"))
StockMovement.__table__.append_constraint(CheckConstraint("movement_type IN ('INITIAL','PURCHASE','SALE','RETURN','LOSS','ADJUSTMENT','INVENTORY_ADJUSTMENT')", name="ck_stock_movement_type"))
for columns, targets, name in (
    (["bar_id","order_id","order_return_id"],["order_returns.bar_id","order_returns.order_id","order_returns.id"],"fk_return_line_order_return"),
    (["bar_id","order_id","order_line_id"],["order_lines.bar_id","order_lines.order_id","order_lines.id"],"fk_return_line_order"),
    (["bar_id","order_line_id","product_id"],["order_lines.bar_id","order_lines.id","order_lines.product_id"],"fk_return_line_product"),
):
    OrderReturnLine.__table__.append_constraint(ForeignKeyConstraint(columns,targets,name=name,ondelete="RESTRICT"))


class BeverageExchange(Tenant, db.Model):
    """Independent, quoted exchange; journals are created only on cashier approval."""
    __tablename__ = "beverage_exchanges"
    id = db.Column(ID, primary_key=True)
    reference = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="PENDING")
    staff_assignment_id = db.Column(ID, nullable=False)
    returned_product_id = db.Column(ID, nullable=False)
    replacement_product_id = db.Column(ID, nullable=False)
    returned_name = db.Column(db.String(160), nullable=False)
    replacement_name = db.Column(db.String(160), nullable=False)
    returned_quantity = db.Column(QTY, nullable=False)
    replacement_quantity = db.Column(QTY, nullable=False)
    returned_price = db.Column(MONEY, nullable=False)
    replacement_price = db.Column(MONEY, nullable=False)
    supplement = db.Column(MONEY, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    created_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decided_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at = db.Column(DT)
    return_movement_id = db.Column(ID)
    replacement_movement_id = db.Column(ID)
    cash_movement_id = db.Column(ID)
    __table_args__ = tenant_args(
        "beverage_exchanges",
        ForeignKeyConstraint(["bar_id", "staff_assignment_id"], ["staff_assignments.bar_id", "staff_assignments.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "returned_product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "replacement_product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "return_movement_id"], ["stock_movements.bar_id", "stock_movements.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "replacement_movement_id"], ["stock_movements.bar_id", "stock_movements.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "cash_movement_id"], ["cash_movements.bar_id", "cash_movements.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "reference", name="uq_beverage_exchanges_reference"),
        UniqueConstraint("bar_id", "return_movement_id", name="uq_beverage_exchanges_return"),
        UniqueConstraint("bar_id", "replacement_movement_id", name="uq_beverage_exchanges_replacement"),
        UniqueConstraint("bar_id", "cash_movement_id", name="uq_beverage_exchanges_cash"),
        CheckConstraint("status IN ('PENDING','POSTED','CANCELLED')", name="ck_beverage_exchanges_status"),
        CheckConstraint("returned_product_id <> replacement_product_id AND returned_quantity > 0 AND replacement_quantity > 0 AND returned_price >= 0 AND replacement_price >= 0 AND supplement >= 0", name="ck_beverage_exchanges_values"),
        CheckConstraint("(status = 'PENDING' AND decided_by_id IS NULL AND decided_at IS NULL) OR (status <> 'PENDING' AND decided_by_id IS NOT NULL AND decided_at IS NOT NULL)", name="ck_beverage_exchanges_decision"),
        CheckConstraint("(status = 'POSTED' AND return_movement_id IS NOT NULL AND replacement_movement_id IS NOT NULL AND ((supplement = 0 AND cash_movement_id IS NULL) OR (supplement > 0 AND cash_movement_id IS NOT NULL))) OR (status <> 'POSTED' AND return_movement_id IS NULL AND replacement_movement_id IS NULL AND cash_movement_id IS NULL)", name="ck_beverage_exchanges_journals"),
    )
