from decimal import Decimal

import pytest
from flask_migrate import upgrade
from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import Bar, CashMovement, Product, ProductCategory, Purchase, PurchaseLine, StockBalance, Supplier, SupplierPayment, User
from app.purchase_services import purchase_service, supplier_service


@pytest.fixture()
def purchase_env(app):
    with app.app_context():
        upgrade()
        owner = User(email="purchase-owner@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("password")
        db.session.add(owner)
        db.session.flush()
        bar = Bar(owner_id=owner.id, name="Purchase Bar", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        category = ProductCategory(bar_id=bar.id, name="Bières")
        db.session.add(category)
        db.session.flush()
        product = Product(
            bar_id=bar.id,
            category_id=category.id,
            sku="33",
            name="33 Export",
            base_unit="bouteille",
            sale_price=Decimal("800"),
            valuation_unit_cost=Decimal("0"),
            stock_alert_threshold=0,
            units_per_case=12,
            is_active=True,
        )
        db.session.add(product)
        db.session.flush()
        supplier = supplier_service.create(
            owner,
            bar.id,
            {
                "name": "Dépôt test",
                "phone": "600000000",
                "address": "Yaoundé",
                "note": "Livraison le matin",
            },
        )
        db.session.commit()
        yield owner, bar, product, supplier
        db.session.remove()


def test_case_purchase_converts_to_bottles_and_keeps_invoice_price(purchase_env):
    owner, bar, product, supplier = purchase_env
    purchase = purchase_service.create(
        owner,
        bar.id,
        supplier.id,
        "ACH-CASE",
        [
            {
                "product_id": product.id,
                "purchase_unit": "CASE",
                "purchase_quantity": "3",
                "purchase_unit_price": "7800",
                "units_per_case": "12",
            }
        ],
        purchase_date="2026-09-18",
        notes="Trois casiers",
    )
    db.session.flush()
    line = db.session.scalar(select(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))

    assert purchase.purchase_date.isoformat() == "2026-09-18"
    assert purchase.notes == "Trois casiers"
    assert line.purchase_unit == "CASE"
    assert line.purchase_quantity == Decimal("3")
    assert line.units_per_case_snapshot == 12
    assert line.purchase_unit_price_snapshot == Decimal("7800")
    assert line.quantity == Decimal("36")
    assert line.unit_cost_snapshot == Decimal("650")
    assert line.total_amount == Decimal("23400")

    purchase_service.receive(owner, bar.id, purchase.id)
    db.session.commit()
    balance = db.session.scalar(select(StockBalance.quantity).where(StockBalance.product_id == product.id))
    assert balance == Decimal("36")


def test_received_purchase_correction_preserves_history_and_creates_new_draft(purchase_env):
    owner, bar, product, supplier = purchase_env
    purchase = purchase_service.create(
        owner,
        bar.id,
        supplier.id,
        "ACH-CORRECT",
        [{"product_id": product.id, "purchase_unit": "CASE", "purchase_quantity": 3, "purchase_unit_price": 7800, "units_per_case": 12}],
    )
    purchase_service.receive(owner, bar.id, purchase.id)
    db.session.commit()
    original_id = purchase.id

    correction = purchase_service.reopen(owner, bar.id, purchase.id, "Quantité incorrecte")
    db.session.flush()
    original = db.session.get(Purchase, original_id)
    assert original.status == "CANCELLED"
    assert correction.status == "DRAFT"
    assert correction.id != original.id
    assert correction.reference.startswith("ACH-CORRECT-CORR-")
    assert db.session.scalar(select(StockBalance.quantity).where(StockBalance.product_id == product.id)) == 0
    assert db.session.scalar(select(PurchaseLine).where(PurchaseLine.purchase_id == original.id)) is not None

    purchase_service.update(
        owner,
        bar.id,
        correction.id,
        {"lines": [{"product_id": product.id, "purchase_unit": "CASE", "purchase_quantity": 2, "purchase_unit_price": 7800, "units_per_case": 12}]},
    )
    purchase_service.receive(owner, bar.id, correction.id)
    db.session.commit()
    assert db.session.scalar(select(StockBalance.quantity).where(StockBalance.product_id == product.id)) == 24


def test_received_purchase_with_payment_must_reverse_payment_before_reopen(purchase_env):
    owner, bar, product, supplier = purchase_env
    purchase = purchase_service.create(
        owner,
        bar.id,
        supplier.id,
        "ACH-PAID",
        [{"product_id": product.id, "purchase_unit": "BOTTLE", "purchase_quantity": 10, "purchase_unit_price": 650}],
    )
    purchase_service.receive(owner, bar.id, purchase.id)
    purchase_service.pay(owner, bar.id, purchase.id, "PAY-1", 1000, "CARD", "Acompte")
    db.session.commit()

    with pytest.raises(ValueError, match="PURCHASE_HAS_PAYMENTS"):
        purchase_service.reopen(owner, bar.id, purchase.id, "Correction")


def test_supplier_note_can_be_updated(purchase_env):
    owner, bar, _product, supplier = purchase_env
    assert supplier.note == "Livraison le matin"
    supplier_service.update(owner, bar.id, supplier.id, {"note": "Appeler avant livraison"})
    db.session.commit()
    assert supplier.note == "Appeler avant livraison"


def test_supplier_payments_keep_owner_money_out_of_cash_drawer(purchase_env):
    owner, bar, product, supplier = purchase_env
    purchase = purchase_service.create(
        owner, bar.id, supplier.id, "ACH-FUNDS",
        [{"product_id": product.id, "purchase_unit": "BOTTLE",
          "purchase_quantity": 10, "purchase_unit_price": 650}],
    )
    purchase_service.receive(owner, bar.id, purchase.id)
    drawer = cash_service.open(owner, bar.id, "DRAWER-FUNDS", 10000)
    db.session.flush()

    personal = purchase_service.pay(
        owner, bar.id, purchase.id, "PERSO-1", 1000, "CASH",
        "Achat avec fonds propres", funding_source="OWNER_FUNDS",
    )
    db.session.flush()
    assert personal.funding_source == "OWNER_FUNDS"
    assert personal.cash_session_id is None
    assert cash_service.expected(drawer) == Decimal("10000")
    assert db.session.scalar(
        select(CashMovement.id).where(CashMovement.supplier_payment_id == personal.id)
    ) is None

    caisse = purchase_service.pay(
        owner, bar.id, purchase.id, "CAISSE-1", 1500, "CASH",
        "Achat sur caisse", drawer.id, funding_source="CASH_DRAWER",
    )
    db.session.flush()
    assert caisse.funding_source == "CASH_DRAWER"
    assert cash_service.expected(drawer) == Decimal("8500")
    assert purchase_service.due(bar.id, purchase.id) == Decimal("4000")

    purchase_service.reverse_payment(owner, bar.id, personal.id, "REV-PERSO", "Erreur")
    assert cash_service.expected(drawer) == Decimal("8500")
    purchase_service.reverse_payment(owner, bar.id, caisse.id, "REV-CAISSE", "Erreur", drawer.id)
    db.session.flush()
    assert cash_service.expected(drawer) == Decimal("10000")
    assert purchase_service.due(bar.id, purchase.id) == Decimal("6500")
    assert db.session.scalar(
        select(SupplierPayment.funding_source).where(SupplierPayment.reference == "REV-PERSO")
    ) == "OWNER_FUNDS"


def test_purchase_source_rejects_invalid_drawer_payment(purchase_env):
    owner, bar, product, supplier = purchase_env
    purchase = purchase_service.create(
        owner, bar.id, supplier.id, "ACH-BAD-FUNDS",
        [{"product_id": product.id, "purchase_unit": "BOTTLE",
          "purchase_quantity": 10, "purchase_unit_price": 650}],
    )
    purchase_service.receive(owner, bar.id, purchase.id)
    with pytest.raises(ValueError, match="DRAWER_REQUIRES_CASH"):
        purchase_service.pay(
            owner, bar.id, purchase.id, "BAD-1", 1000, "CARD",
            "Invalid", funding_source="CASH_DRAWER",
        )
    with pytest.raises(ValueError, match="INVALID_FUNDING_SOURCE"):
        purchase_service.pay(
            owner, bar.id, purchase.id, "BAD-2", 1000, "CASH",
            "Invalid", funding_source="UNKNOWN",
        )
    with pytest.raises(ValueError, match="CASH_LOCATION_REQUIRED"):
        purchase_service.pay(
            owner, bar.id, purchase.id, "BAD-3", 1000, "CASH",
            "Invalid", funding_source="CASH_DRAWER",
        )
