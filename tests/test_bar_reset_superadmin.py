from decimal import Decimal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask_migrate import upgrade
from sqlalchemy import select, text

from app import create_app
from app.bar_reset_service import reset_bar
from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance, User
from app.stock_service import stock_service


def test_superadmin_can_reset_one_bar_without_touching_another(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    app = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'reset-superadmin.sqlite'}",
            "JWT_PRIVATE_KEY": pem,
            "WTF_CSRF_ENABLED": False,
        },
    )

    with app.app_context():
        upgrade()
        db.session.execute(text("PRAGMA foreign_keys=ON"))

        admin = User(email="admin@example.invalid", display_name="Admin", category="SUPER_ADMIN")
        admin.set_password("admin-password")
        owner1 = User(email="owner1@example.invalid", display_name="Owner 1", category="OWNER")
        owner1.set_password("owner1-password")
        owner2 = User(email="owner2@example.invalid", display_name="Owner 2", category="OWNER")
        owner2.set_password("owner2-password")
        db.session.add_all([admin, owner1, owner2])
        db.session.flush()

        bar1 = Bar(owner_id=owner1.id, name="Bar 1", timezone="Africa/Douala", currency="XAF")
        bar2 = Bar(owner_id=owner2.id, name="Bar 2", timezone="Africa/Douala", currency="XAF")
        db.session.add_all([bar1, bar2])
        db.session.flush()

        cat1 = ProductCategory(bar_id=bar1.id, name="Boissons")
        cat2 = ProductCategory(bar_id=bar2.id, name="Boissons")
        db.session.add_all([cat1, cat2])
        db.session.flush()

        p1 = Product(
            bar_id=bar1.id,
            category_id=cat1.id,
            sku="P1",
            name="33 Export",
            base_unit="bottle",
            sale_price=Decimal("800"),
            valuation_unit_cost=Decimal("500"),
        )
        p2 = Product(
            bar_id=bar2.id,
            category_id=cat2.id,
            sku="P2",
            name="Castel",
            base_unit="bottle",
            sale_price=Decimal("800"),
            valuation_unit_cost=Decimal("500"),
        )
        db.session.add_all([p1, p2])
        db.session.flush()

        stock_service.move(owner1, bar1.id, p1.id, "INITIAL", 10, "Initial")
        stock_service.move(owner2, bar2.id, p2.id, "INITIAL", 7, "Initial")
        db.session.commit()

        reset_bar(admin, bar1.id, "REINITIALISER", "admin-password")
        db.session.commit()

        b1 = db.session.scalar(select(StockBalance.quantity).where(StockBalance.bar_id == bar1.id, StockBalance.product_id == p1.id))
        b2 = db.session.scalar(select(StockBalance.quantity).where(StockBalance.bar_id == bar2.id, StockBalance.product_id == p2.id))
        assert b1 == 0
        assert b2 == 7
