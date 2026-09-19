from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Product, Purchase, StockBalance, Supplier
from app.purchase_services import purchase_service, supplier_service
from app.stock_service import StockError
from app.stock_valuation import rebuild_bar_valuations, replay_product_valuation
from test_workflows import env, order


def _supplier(owner, bar):
    supplier = supplier_service.create(owner, bar.id, {"name": "Valuation Supplier"})
    db.session.flush()
    return supplier


def _receive(owner, bar, product, supplier, reference, quantity, unit_cost):
    purchase = purchase_service.create(
        owner,
        bar.id,
        supplier.id,
        reference,
        [
            {
                "product_id": product.id,
                "purchase_unit": "BOTTLE",
                "purchase_quantity": quantity,
                "purchase_unit_price": unit_cost,
            }
        ],
    )
    purchase_service.receive(owner, bar.id, purchase.id)
    db.session.flush()
    return purchase


def test_purchase_receipts_update_moving_weighted_average(env):
    _, owner, bar, _, product, _, _, _ = env
    supplier = _supplier(owner, bar)

    assert db.session.scalar(
        select(StockBalance.quantity).where(StockBalance.product_id == product.id)
    ) == Decimal("10")
    assert product.valuation_unit_cost == Decimal("40")

    _receive(owner, bar, product, supplier, "VAL-1", 10, 60)
    assert product.valuation_unit_cost == Decimal("50.0000")

    _receive(owner, bar, product, supplier, "VAL-2", 10, 80)
    assert product.valuation_unit_cost == Decimal("60.0000")
    assert db.session.scalar(
        select(StockBalance.quantity).where(StockBalance.product_id == product.id)
    ) == Decimal("30")


def test_stock_outflow_keeps_current_moving_average(env):
    _, owner, bar, _, product, _, _, _ = env
    supplier = _supplier(owner, bar)
    _receive(owner, bar, product, supplier, "VAL-SALE", 10, 60)
    assert product.valuation_unit_cost == Decimal("50.0000")

    order(env, quantity=3)
    db.session.refresh(product)
    assert product.valuation_unit_cost == Decimal("50.0000")
    assert db.session.scalar(
        select(StockBalance.quantity).where(StockBalance.product_id == product.id)
    ) == Decimal("17")


def test_immediate_purchase_correction_restores_previous_valuation(env):
    _, owner, bar, _, product, _, _, _ = env
    supplier = _supplier(owner, bar)
    purchase = _receive(owner, bar, product, supplier, "VAL-CORR", 10, 60)
    db.session.commit()
    assert product.valuation_unit_cost == Decimal("50.0000")

    correction = purchase_service.reopen(owner, bar.id, purchase.id, "Prix ou quantité incorrecte")
    db.session.flush()

    db.session.refresh(product)
    assert db.session.get(Purchase, purchase.id).status == "CANCELLED"
    assert correction.status == "DRAFT"
    assert product.valuation_unit_cost == Decimal("40.0000")
    assert db.session.scalar(
        select(StockBalance.quantity).where(StockBalance.product_id == product.id)
    ) == Decimal("10")


def test_purchase_reversal_after_real_outflow_is_blocked(env):
    _, owner, bar, _, product, _, _, _ = env
    supplier = _supplier(owner, bar)
    purchase = _receive(owner, bar, product, supplier, "VAL-BLOCK", 10, 60)
    db.session.commit()

    order(env, quantity=1)

    with pytest.raises(StockError, match="PURCHASE_REVERSAL_AFTER_OUTFLOW"):
        purchase_service.reopen(owner, bar.id, purchase.id, "Correction tardive")
    db.session.rollback()

    assert db.session.get(Purchase, purchase.id).status == "POSTED"


def test_legacy_valuation_can_be_previewed_and_rebuilt(env):
    _, owner, bar, _, product, _, _, _ = env
    supplier = _supplier(owner, bar)
    _receive(owner, bar, product, supplier, "VAL-REBUILD-1", 10, 60)
    _receive(owner, bar, product, supplier, "VAL-REBUILD-2", 10, 80)
    db.session.commit()
    assert product.valuation_unit_cost == Decimal("60.0000")

    product.valuation_unit_cost = Decimal("0")
    db.session.commit()

    preview = replay_product_valuation(bar.id, product.id)
    assert preview["quantity_matches"] is True
    assert preview["balance_quantity"] == Decimal("30")
    assert preview["new_unit_cost"] == Decimal("60.0000")

    result = rebuild_bar_valuations(bar.id, apply=True)
    db.session.commit()
    db.session.refresh(product)

    assert result["changed"] >= 1
    assert product.valuation_unit_cost == Decimal("60.0000")
