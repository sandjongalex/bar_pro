from decimal import Decimal

import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Bar, Product, ProductCategory, Purchase, PurchaseLine, Supplier, User
from app.purchase_services import purchase_service


@pytest.fixture()
def draft_purchase_env(tmp_path):
    app = create_app(
        "testing",
        {
            "SECRET_KEY": "purchase-web-edit-test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'purchase-edit.sqlite'}",
            "WTF_CSRF_ENABLED": False,
        },
    )

    with app.app_context():
        db.create_all()
        owner = User(email="owner-edit@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("test-password")
        db.session.add(owner)
        db.session.flush()

        bar = Bar(owner_id=owner.id, name="Test Bar", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()

        category = ProductCategory(bar_id=bar.id, name="Bières")
        db.session.add(category)
        db.session.flush()

        product = Product(
            bar_id=bar.id,
            category_id=category.id,
            name="33 Export",
            sku="DEF-BEER-33-EXPORT",
            base_unit="bouteille",
            sale_price=800,
            valuation_unit_cost=390,
            is_active=True,
        )
        supplier = Supplier(bar_id=bar.id, name="Dépôt test", is_active=True)
        db.session.add_all([product, supplier])
        db.session.flush()

        purchase = purchase_service.create(
            owner,
            bar.id,
            supplier.id,
            "ACH-TEST",
            [{"product_id": product.id, "quantity": "1", "unit_cost": "7800"}],
        )
        db.session.commit()

        yield app, owner, bar, supplier, product, purchase

        db.session.remove()
        db.drop_all()


def test_owner_can_edit_draft_purchase_from_web(draft_purchase_env):
    app, owner, bar, supplier, product, purchase = draft_purchase_env
    client = app.test_client()

    login = client.post(
        "/login",
        data={"email": owner.email, "password": "test-password"},
    )
    assert login.status_code == 302

    response = client.post(
        f"/bars/{bar.id}/purchases",
        data={
            "action": "purchase_update",
            "purchase_id": str(purchase.id),
            "supplier_id": str(supplier.id),
            "reference": "ACH-TEST-CORRIGE",
            "supplier_invoice_reference": "FACT-42",
            "product_id": [str(product.id)],
            "quantity": ["3"],
            "unit_cost": ["8000"],
        },
    )
    assert response.status_code == 302

    db.session.expire_all()
    updated = db.session.get(Purchase, purchase.id)
    line = db.session.scalar(
        select(PurchaseLine).where(
            PurchaseLine.bar_id == bar.id,
            PurchaseLine.purchase_id == purchase.id,
            PurchaseLine.product_id == product.id,
        )
    )

    assert updated.status == "DRAFT"
    assert updated.reference == "ACH-TEST-CORRIGE"
    assert updated.supplier_invoice_reference == "FACT-42"
    assert updated.total_amount == Decimal("24000")
    assert line.quantity == Decimal("3")
    assert line.unit_cost_snapshot == Decimal("8000")


def test_received_purchase_cannot_be_edited(draft_purchase_env):
    app, owner, bar, supplier, product, purchase = draft_purchase_env
    purchase_service.receive(owner, bar.id, purchase.id)
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"email": owner.email, "password": "test-password"})
    response = client.post(
        f"/bars/{bar.id}/purchases",
        data={
            "action": "purchase_update",
            "purchase_id": str(purchase.id),
            "supplier_id": str(supplier.id),
            "reference": "NE-DOIT-PAS-CHANGER",
            "product_id": [str(product.id)],
            "quantity": ["9"],
            "unit_cost": ["9000"],
        },
    )
    assert response.status_code == 302

    db.session.expire_all()
    unchanged = db.session.get(Purchase, purchase.id)
    line = db.session.scalar(
        select(PurchaseLine).where(
            PurchaseLine.bar_id == bar.id,
            PurchaseLine.purchase_id == purchase.id,
            PurchaseLine.product_id == product.id,
        )
    )

    assert unchanged.status == "POSTED"
    assert unchanged.reference == "ACH-TEST"
    assert unchanged.total_amount == Decimal("7800")
    assert line.quantity == Decimal("1")
    assert line.unit_cost_snapshot == Decimal("7800")
