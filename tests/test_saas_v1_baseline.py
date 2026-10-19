"""SaaS V1 baseline tests: codify observed behaviour, not target features."""
from decimal import Decimal

from sqlalchemy import func, select

from app.customer_models import CustomerLedgerEntry
from app.customer_services import customer_service
from app.extensions import db
from app.models import CashMovement, Customer, Order, StockBalance, StockMovement
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.permissions import ROLE_ACTIONS, permissions
from app.models import Product, Purchase, Inventory, CashSession, Subscription
from test_workflows import env


def test_baseline_tenant_models_have_required_bar_id():
    models = (Customer, CustomerLedgerEntry, Order, OrderSuborder,
              OrderSuborderLine, Product, Purchase, Inventory,
              CashSession, Subscription, StockMovement, StockBalance)
    for model in models:
        column = model.__table__.c.bar_id
        assert not column.nullable, model.__name__
        assert any(key.column.table.name == "bars" for key in column.foreign_keys), model.__name__


def test_baseline_permissions_roles_and_manual_debt_scope():
    actual = set().union(*ROLE_ACTIONS.values())
    assert actual == {"SUPER_ADMIN", "OWNER", "BAR_ADMIN", "CASHIER", "SERVER"}
    assert ROLE_ACTIONS["customer_debt.manage"] == {"SUPER_ADMIN", "OWNER"}
    assert "SERVER" not in ROLE_ACTIONS["orders.deliver"]


def test_baseline_foreign_owner_is_denied(env):
    _, _, first_bar, _, _, _, _, _ = env
    # The second user owns the other tenant in the shared workflow fixture.
    from app.models import User
    foreign_owner = db.session.scalar(select(User).where(User.email == "other@example.invalid"))
    assert foreign_owner is not None
    decision = permissions.evaluate(foreign_owner, "orders.read", first_bar.id)
    assert not decision.allowed
    assert decision.reason == "FORBIDDEN"


def test_baseline_manual_debt_does_not_create_sales_cash_or_stock(env):
    _, owner, bar, _, product, _, _, _ = env
    customer = Customer(bar_id=bar.id, display_name="Dette de reference")
    db.session.add(customer)
    db.session.flush()
    count = lambda cls: db.session.scalar(select(func.count()).select_from(cls).where(cls.bar_id == bar.id))
    before = (count(Order), count(CashMovement), count(StockMovement))
    quantity = db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id))
    entry = customer_service.record_manual_debt(
        owner, bar.id, customer.id, "BASELINE-DETTE-001", "1500",
        "Dette de reference hors caisse")
    db.session.flush()
    assert entry.entry_kind == "ADJUSTMENT"
    assert entry.order_id is None
    assert Decimal(entry.amount_delta) == Decimal("1500")
    assert (count(Order), count(CashMovement), count(StockMovement)) == before
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id)) == quantity
