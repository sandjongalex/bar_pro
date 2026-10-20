"""V2 inventory movement reconciliation while the bar continues selling."""
from decimal import Decimal
import re

import pytest
from sqlalchemy import select

from test_workflows import env, balance, order
from test_inventory_v2_foundation import on_duty_cashier
from app.auth import issue
from app.extensions import db
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_count_models import InventoryLineCountState
from app.inventory_reconciliation import inventory_live_reconciliation
from app.inventory_services import inventory_service
from app.models import InventoryLine, Product, ProductCategory, StockBalance, User
from app.stock_service import stock_service


def draft(env, cashier, ref="LIVE"):
    inv = inventory_service.create(cashier, env[2].id, ref, [env[4].id], "Comptage actif")
    db.session.commit()
    return inv


def count(env, cashier, inv, physical, revision=0):
    inventory_service.count_v2_entries(cashier, env[2].id, inv.id, [{
        "product_id": env[4].id,
        "units": physical, "cases": "", "note": "", "revision": revision,
    }])
    db.session.commit()


def preview(env, inv):
    return inventory_live_reconciliation(inv)["lines"][0]


def test_sale_before_and_after_count_are_neutral_in_variance(env):
    cashier, _, _ = on_duty_cashier(env)
    inv = draft(env, cashier)
    assert Decimal(preview(env, inv)["opening_stock"]) == 10
    stock_service.move(env[1], env[2].id, env[4].id, "SALE", "-2", "Vente pendant comptage")
    db.session.commit()
    assert Decimal(preview(env, inv)["expected_now"]) == 8
    count(env, cashier, inv, "8")
    at_count = preview(env, inv)
    assert at_count["status"] == "RECONCILED"
    assert Decimal(at_count["expected_at_count"]) == 8
    assert Decimal(at_count["difference_now"]) == 0
    stock_service.move(env[1], env[2].id, env[4].id, "SALE", "-3", "Autre vente")
    db.session.commit()
    after = preview(env, inv)
    assert after["status"] == "RECONCILED"
    assert Decimal(after["current_machine"]) == 5
    assert Decimal(after["projected_physical_now"]) == 5
    assert Decimal(after["delta_before_count"]) == -2
    assert Decimal(after["delta_after_count"]) == -3
    assert after["movement_breakdown"] == [{"type": "SALE", "quantity_delta": "-5.000000"}]
    assert inv.status == "DRAFT"


def test_actual_order_confirmation_uses_same_stock_movement_ledger(env):
    cashier, _, _ = on_duty_cashier(env, "live-order")
    inv = draft(env, cashier, "LIVE-ORDER")
    order(env, quantity=2, actor=env[1])
    count(env, cashier, inv, "8")
    assert preview(env, inv)["status"] == "RECONCILED"
    stock_service.move(env[1], env[2].id, env[4].id,
                       "SALE", -1, "Nouvelle vente")
    db.session.commit()
    row = preview(env, inv)
    assert Decimal(row["projected_physical_now"]) == 7
    assert Decimal(row["current_machine"]) == 7
    assert row["status"] == "RECONCILED"


def test_purchases_returns_and_losses_after_count_are_not_sales(env):
    cashier, _, _ = on_duty_cashier(env, "live-purchase")
    inv = draft(env, cashier, "LIVE-OTHER")
    count(env, cashier, inv, "10")
    stock_service.move(env[1], env[2].id, env[4].id, "PURCHASE", "2", "Nouvel achat")
    stock_service.move(env[1], env[2].id, env[4].id, "RETURN", "1", "Retour client")
    stock_service.move(env[1], env[2].id, env[4].id, "LOSS", "-1", "Bouteille cassée")
    db.session.commit()
    row = preview(env, inv)
    assert Decimal(row["current_machine"]) == 12
    assert Decimal(row["delta_after_count"]) == 2
    assert Decimal(row["projected_physical_now"]) == 12
    assert row["status"] == "RECONCILED"
    types = {movement["type"] for movement in row["movement_breakdown"]}
    assert types == {"PURCHASE", "RETURN", "LOSS"}


def test_unexplained_shortage_does_not_multiply_after_later_sale(env):
    cashier, _, _ = on_duty_cashier(env, "live-shortage")
    inv = draft(env, cashier, "LIVE-SHORT")
    count(env, cashier, inv, "6")
    first = preview(env, inv)
    assert first["status"] == "DIFFERENCE"
    assert Decimal(first["difference_now"]) == -4
    stock_service.move(env[1], env[2].id, env[4].id, "SALE", "-2", "Vente après comptage")
    db.session.commit()
    after = preview(env, inv)
    assert Decimal(after["current_machine"]) == 8
    assert Decimal(after["projected_physical_now"]) == 4
    assert Decimal(after["difference_now"]) == -4
    assert after["review_required"] is True
    assert balance(env) == 8


def test_blank_and_zero_have_different_reconciliation_states(env):
    cashier, _, _ = on_duty_cashier(env, "live-zero")
    inv = draft(env, cashier, "LIVE-ZERO")
    assert preview(env, inv)["status"] == "NOT_COUNTED"
    count(env, cashier, inv, "0")
    result = preview(env, inv)
    assert result["physical_count"] == "0"
    assert result["status"] == "DIFFERENCE"
    assert Decimal(result["difference_now"]) == -10


def test_preexisting_v2_draft_requires_new_baseline(env):
    cashier, _, _ = on_duty_cashier(env, "live-legacy")
    inv = draft(env, cashier, "LIVE-LEGACY")
    assignment = db.session.scalar(select(InventoryCashierAssignment).where(
        InventoryCashierAssignment.inventory_id == inv.id
    ))
    assignment.movement_checkpoint_id = None
    db.session.commit()
    count(env, cashier, inv, "10")
    result = preview(env, inv)
    assert result["status"] == "REFERENCE_MISSING"
    assert result["review_required"] is True


def test_old_count_without_checkpoint_needs_recount(env):
    cashier, _, _ = on_duty_cashier(env, "live-old-count")
    inv = draft(env, cashier, "LIVE-OLD-COUNT")
    count(env, cashier, inv, "10")
    state = db.session.scalar(select(InventoryLineCountState).where(
        InventoryLineCountState.bar_id == env[2].id,
    ))
    state.movement_checkpoint_id = None
    db.session.commit()
    assert preview(env, inv)["status"] == "COUNT_CHECKPOINT_MISSING"


def test_out_of_band_balance_mutation_is_reported_not_automatically_corrected(env):
    cashier, _, _ = on_duty_cashier(env, "live-mismatch")
    inv = draft(env, cashier, "LIVE-MISMATCH")
    count(env, cashier, inv, "10")
    db.session.scalar(select(StockBalance).where(
        StockBalance.bar_id == env[2].id,
        StockBalance.product_id == env[4].id,
    )).quantity = 9
    db.session.commit()
    row = preview(env, inv)
    assert row["status"] == "LEDGER_MISMATCH"
    assert row["review_required"] is True
    assert Decimal(row["current_machine"]) == 9
    assert Decimal(row["expected_now"]) == 10


def test_other_tenant_stock_movements_are_excluded(env):
    cashier, _, _ = on_duty_cashier(env, "live-tenant")
    inv = draft(env, cashier, "LIVE-TENANT")
    other_owner = db.session.get(User, env[3].owner_id)
    category = ProductCategory(bar_id=env[3].id, name="Other drinks")
    db.session.add(category)
    db.session.flush()
    product = Product(bar_id=env[3].id, category_id=category.id,
                      sku="FOREIGN-LIVE", name="Foreign Water",
                      base_unit="bottle", sale_price=1, valuation_unit_cost=1)
    db.session.add(product)
    db.session.flush()
    stock_service.move(other_owner, env[3].id, product.id, "INITIAL", 5, "Other stock")
    db.session.commit()
    count(env, cashier, inv, "10")
    assert preview(env, inv)["status"] == "RECONCILED"
    assert preview(env, inv)["movement_breakdown"] == []


def test_reconciliation_api_is_read_only_and_cashier_scoped(env):
    cashier_a, _, _ = on_duty_cashier(env, "live-access-a")
    cashier_b, _, _ = on_duty_cashier(env, "live-access-b")
    inv = draft(env, cashier_a, "LIVE-ACCESS")
    token_a, _ = issue(cashier_a, env[2].id)
    token_b, _ = issue(cashier_b, env[2].id)
    db.session.commit()
    client = env[0].test_client()
    path = f"/api/v1/bars/{env[2].id}/inventories/{inv.id}/reconciliation"
    resp = client.get(path, headers={"Authorization": "Bearer " + token_a})
    assert resp.status_code == 200, resp.text
    assert resp.json["data"]["read_only"] is True
    assert resp.json["data"]["lines"][0]["status"] == "NOT_COUNTED"
    assert client.get(path, headers={
        "Authorization": "Bearer " + token_b
    }).status_code == 404
    assert client.get(path.replace(f"/bars/{env[2].id}/",
                                   f"/bars/{env[3].id}/"), headers={
        "Authorization": "Bearer " + token_a
    }).status_code == 404
    assert balance(env) == 10


def test_note_only_change_preserves_stock_checkpoint(env):
    cashier, _, _ = on_duty_cashier(env, "live-notes")
    inv = draft(env, cashier, "LIVE-NOTES")
    count(env, cashier, inv, "10")
    original = preview(env, inv)["count_checkpoint_id"]
    stock_service.move(env[1], env[2].id, env[4].id,
                       "SALE", -2, "After first counting")
    db.session.commit()
    inventory_service.count_v2_entries(cashier, env[2].id, inv.id, [{
        "product_id": env[4].id, "units": "10", "cases": "",
        "note": "Contrôle additionnel", "revision": 1,
    }])
    db.session.commit()
    row = preview(env, inv)
    assert row["count_checkpoint_id"] == original
    assert Decimal(row["projected_physical_now"]) == 8
    assert row["status"] == "RECONCILED"
