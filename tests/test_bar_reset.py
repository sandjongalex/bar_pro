from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask_migrate import upgrade
from sqlalchemy import func, select, text

from app import create_app
from app.bar_reset_service import reset_bar
from app.cash_services import cash_service
from app.extensions import db
from app.models import (
    AuditLog,
    Bar,
    CashSession,
    Order,
    Payment,
    Product,
    ProductCategory,
    StaffAssignment,
    StockBalance,
    StockMovement,
    User,
    utcnow,
)
from app.order_services import order_service
from app.payment_services import payment_service
from app.stock_service import stock_service


@pytest.fixture
def reset_env(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    app = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'reset.sqlite'}",
            "JWT_PRIVATE_KEY": pem,
            "WTF_CSRF_ENABLED": False,
        },
    )

    with app.app_context():
        upgrade()
        db.session.execute(text("PRAGMA foreign_keys=ON"))

        owner = User(email="owner-reset@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("owner-password")
        other_owner = User(email="other-reset@example.invalid", display_name="Other", category="OWNER")
        other_owner.set_password("other-password")
        server = User(email="server-reset@example.invalid", display_name="Server", category="EMPLOYEE")
        server.set_password("server-password")
        db.session.add_all([owner, other_owner, server])
        db.session.flush()

        bar = Bar(owner_id=owner.id, name="Reset One", timezone="Africa/Douala", currency="XAF")
        foreign = Bar(owner_id=other_owner.id, name="Reset Two", timezone="Africa/Douala", currency="XAF")
        db.session.add_all([bar, foreign])
        db.session.flush()

        category = ProductCategory(bar_id=bar.id, name="Boissons")
        foreign_category = ProductCategory(bar_id=foreign.id, name="Boissons")
        db.session.add_all([category, foreign_category])
        db.session.flush()

        product = Product(
            bar_id=bar.id,
            category_id=category.id,
            name="33 Export",
            sku="33",
            base_unit="bottle",
            sale_price=Decimal("800"),
            valuation_unit_cost=Decimal("500"),
        )
        foreign_product = Product(
            bar_id=foreign.id,
            category_id=foreign_category.id,
            name="Castel",
            sku="CASTEL",
            base_unit="bottle",
            sale_price=Decimal("800"),
            valuation_unit_cost=Decimal("500"),
        )
        assignment = StaffAssignment(
            bar_id=bar.id,
            user_id=server.id,
            role="SERVER",
            started_at=utcnow(),
        )
        db.session.add_all([product, foreign_product, assignment])
        db.session.flush()

        stock_service.move(owner, bar.id, product.id, "INITIAL", 10, "Stock initial")
        stock_service.move(other_owner, foreign.id, foreign_product.id, "INITIAL", 7, "Stock initial")
        db.session.commit()

        yield app, owner, other_owner, server, bar, foreign, product, foreign_product, assignment

        db.session.remove()
        db.engine.dispose()


def _create_paid_order(actor, bar, product, reference):
    order = order_service.create(
        actor,
        bar.id,
        reference,
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(actor, bar.id, order.id)
    cash = cash_service.open(actor, bar.id, f"CASH-{reference}", 1000)
    db.session.flush()
    payment_service.record(
        actor,
        bar.id,
        order.id,
        f"PAY-{reference}",
        "CASH",
        product.sale_price,
        product.sale_price,
        0,
        cash.id,
    )
    db.session.commit()
    return order


def _balance(bar_id, product_id):
    return db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == product_id,
        )
    )


def test_owner_reset_clears_only_own_bar(reset_env):
    _, owner, other_owner, _, bar, foreign, product, foreign_product, assignment = reset_env
    _create_paid_order(owner, bar, product, "OWN")
    _create_paid_order(other_owner, foreign, foreign_product, "FOREIGN")

    assert _balance(bar.id, product.id) == 9
    assert _balance(foreign.id, foreign_product.id) == 6

    result = reset_bar(owner, bar.id, "REINITIALISER", "owner-password")
    db.session.commit()

    assert result["stock_balances_reset"] == 1
    assert db.session.get(Bar, bar.id) is not None
    assert db.session.get(Product, product.id) is not None
    assert db.session.get(StaffAssignment, assignment.id) is not None
    assert _balance(bar.id, product.id) == 0

    assert db.session.scalar(select(func.count(Order.id)).where(Order.bar_id == bar.id)) == 0
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.bar_id == bar.id)) == 0
    assert db.session.scalar(select(func.count(CashSession.id)).where(CashSession.bar_id == bar.id)) == 0
    assert db.session.scalar(select(func.count(StockMovement.id)).where(StockMovement.bar_id == bar.id)) == 0

    reset_audits = list(
        db.session.scalars(
            select(AuditLog).where(AuditLog.bar_id == bar.id).order_by(AuditLog.id)
        )
    )
    assert len(reset_audits) == 1
    assert reset_audits[0].action == "bars.reset"

    # The other tenant is untouched.
    assert _balance(foreign.id, foreign_product.id) == 6
    assert db.session.scalar(select(func.count(Order.id)).where(Order.bar_id == foreign.id)) == 1
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.bar_id == foreign.id)) == 1


def test_reset_requires_exact_confirmation_and_password(reset_env):
    _, owner, _, _, bar, _, product, _, _ = reset_env
    _create_paid_order(owner, bar, product, "SAFE")

    with pytest.raises(ValueError, match="RESET_CONFIRMATION_REQUIRED"):
        reset_bar(owner, bar.id, "reset", "owner-password")
    db.session.rollback()

    with pytest.raises(ValueError, match="INVALID_PASSWORD"):
        reset_bar(owner, bar.id, "REINITIALISER", "wrong-password")
    db.session.rollback()

    assert _balance(bar.id, product.id) == 9
    assert db.session.scalar(select(func.count(Order.id)).where(Order.bar_id == bar.id)) == 1


def test_employee_and_other_owner_cannot_reset(reset_env):
    _, owner, other_owner, server, bar, _, product, _, _ = reset_env
    _create_paid_order(owner, bar, product, "DENIED")

    with pytest.raises(PermissionError):
        reset_bar(server, bar.id, "REINITIALISER", "server-password")
    db.session.rollback()

    with pytest.raises(PermissionError):
        reset_bar(other_owner, bar.id, "REINITIALISER", "other-password")
    db.session.rollback()

    assert _balance(bar.id, product.id) == 9
    assert db.session.scalar(select(func.count(Order.id)).where(Order.bar_id == bar.id)) == 1
