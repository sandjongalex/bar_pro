"""PROMPT 3: supplier purchases are physically MAIN-only; legacy sales remain unchanged."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.bar_services import assign_staff
from app.extensions import db
from app.models import (
    AuditLog, Product, ProductCategory, Purchase, PurchaseLine,
    StockBalance, StockMovement, Supplier, User,
)
from app.purchase_services import purchase_service, supplier_service
from app.warehouse_models import (
    WarehouseAllocation, WarehouseLocation, WarehouseStockBalance,
    WarehouseStockMovement,
)
from app.warehouse_permission_service import set_warehouse_grant
from app.warehouse_purchase_models import WarehousePurchaseReceiptLine
from app.warehouse_service import warehouse_service
from test_employee_tenant_auth import _csrf, _login
from test_workflows import env


def _supplier_and_product(env):
    owner, bar = env[1], env[2]
    supplier = supplier_service.create(owner, bar.id, {"name": "Fournisseur V2"})
    product = Product(
        bar_id=bar.id, category_id=env[4].category_id,
        sku="B33", name="33 Export", base_unit="bouteille", sale_price=800,
        valuation_unit_cost=600, units_per_case=12, is_active=True,
    )
    db.session.add(product)
    db.session.commit()
    return supplier, product


def _purchase(env, supplier, product, reference):
    return purchase_service.create(env[1], env[2].id, supplier.id, reference, [
        {"product_id": product.id, "purchase_unit": "CASE",
         "purchase_quantity": 2, "purchase_unit_price": 7200, "units_per_case": 12},
        {"product_id": env[4].id, "purchase_unit": "BOTTLE",
         "purchase_quantity": 5, "purchase_unit_price": 50},
    ])


def _receipts(env, purchase):
    lines = db.session.scalars(select(PurchaseLine).where(
        PurchaseLine.bar_id == env[2].id, PurchaseLine.purchase_id == purchase.id
    )).all()
    receipts = db.session.scalars(select(WarehousePurchaseReceiptLine).where(
        WarehousePurchaseReceiptLine.bar_id == env[2].id,
        WarehousePurchaseReceiptLine.purchase_line_id.in_([line.id for line in lines]),
    )).all()
    return lines, receipts


def _keeper(env, suffix="normal"):
    user = User(
        email=f"purchase-keeper-{suffix}@example.invalid",
        display_name="Magasinier", category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = assign_staff(env[1], env[2].id, user.id, "STOREKEEPER")
    db.session.commit()
    return user, assignment


def test_multi_product_case_bottle_main_receipt_before_cutover(env):
    supplier, beer = _supplier_and_product(env)
    purchase = _purchase(env, supplier, beer, "MAIN-MIX-001")
    db.session.commit()
    lines = db.session.scalars(select(PurchaseLine).where(
        PurchaseLine.purchase_id == purchase.id
    ).order_by(PurchaseLine.line_no)).all()
    assert [item.quantity for item in lines] == [24, 5]
    assert [item.unit_cost_snapshot for item in lines] == [600, 50]
    assert purchase.total_amount == Decimal("14650")
    original_cash = db.session.scalar(select(func.count()).select_from(WarehouseStockMovement))
    assert original_cash == 0

    purchase_service.receive(env[1], env[2].id, purchase.id)
    db.session.commit()
    _lines, receipts = _receipts(env, purchase)
    assert len(receipts) == 2
    locations = {x.id: x.code for x in db.session.scalars(select(WarehouseLocation).where(
        WarehouseLocation.bar_id == env[2].id
    ))}
    assert {locations[r.location_id] for r in receipts} == {"MAIN"}
    assert all(r.mode == "PRE_CUTOVER" and r.warehouse_movement_id is None for r in receipts)
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == env[2].id, StockBalance.product_id == beer.id,
    )) == 24
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == env[2].id, StockBalance.product_id == env[4].id,
    )) == 15
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockBalance).where(
        WarehouseStockBalance.bar_id == env[2].id)) == 0
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement).where(
        WarehouseStockMovement.bar_id == env[2].id)) == 0
    assert purchase.status == "POSTED"
    assert db.session.scalar(select(func.count()).select_from(StockMovement).where(
        StockMovement.bar_id == env[2].id, StockMovement.movement_type == "PURCHASE",
    )) == 2
    assert db.session.get(Product, beer.id).valuation_unit_cost == 600
    with pytest.raises(ValueError, match="PURCHASE_NOT_DRAFT"):
        purchase_service.receive(env[1], env[2].id, purchase.id)
    db.session.rollback()
    assert len(_receipts(env, purchase)[1]) == 2


def test_main_only_live_receipt_conserves_cost_and_no_cashier_credit(env):
    supplier, beer = _supplier_and_product(env)
    # Simulate LIVE only in disposable tests. The deployment has NO activation route.
    staged = warehouse_service.stage_allocation(env[1], env[2].id, {
        env[4].id: {"MAIN": 7, "CASHIER": 3},
        beer.id: {"MAIN": 0, "CASHIER": 0},
    })
    db.session.commit()
    staged.status = "LIVE"
    db.session.commit()
    before = warehouse_service.totals(env[2].id)
    purchase = _purchase(env, supplier, beer, "MAIN-LIVE-001")
    purchase_service.receive(env[1], env[2].id, purchase.id)
    db.session.commit()
    after = warehouse_service.totals(env[2].id)
    assert after[beer.id] == {
        "MAIN": 24, "CASHIER": 0, "TRANSIT": 0, "TOTAL": 24,
    }
    assert after[env[4].id] == {
        "MAIN": 12, "CASHIER": 3, "TRANSIT": 0, "TOTAL": 15,
    }
    assert after[env[4].id]["CASHIER"] == before[env[4].id]["CASHIER"]
    rows = db.session.scalars(select(WarehouseStockMovement).where(
        WarehouseStockMovement.bar_id == env[2].id,
        WarehouseStockMovement.movement_type == "PURCHASE_IN",
    )).all()
    assert sorted(r.quantity_delta for r in rows) == [5, 24]
    assert {r.unit_cost_snapshot for r in rows} == {Decimal(50), Decimal(600)}
    assert all(r.location_id != warehouse_service.ensure_locations(env[2].id)["CASHIER"].id for r in rows)
    assert all(r.warehouse_movement_id is not None for r in _receipts(env, purchase)[1])


def test_owner_correction_preserves_all_receipt_history_and_cost(env):
    supplier, beer = _supplier_and_product(env)
    purchase = _purchase(env, supplier, beer, "MAIN-CORRECT-001")
    purchase_service.receive(env[1], env[2].id, purchase.id)
    db.session.commit()
    correction = purchase_service.reopen(env[1], env[2].id, purchase.id, "Erreur quantité")
    db.session.commit()
    assert purchase.status == "CANCELLED"
    assert correction.status == "DRAFT"
    assert all(r.reversed_at is not None for r in _receipts(env, purchase)[1])
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == env[2].id, StockBalance.product_id == beer.id
    )) == 0
    assert db.session.scalar(select(func.count()).select_from(StockMovement).where(
        StockMovement.bar_id == env[2].id, StockMovement.reversal_of_id.is_not(None),
    )) == 2
    assert db.session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.bar_id == env[2].id,
        AuditLog.action == "warehouse.purchase.main.reverse",
    )) == 1
    purchase_service.receive(env[1], env[2].id, correction.id)
    db.session.commit()
    assert len(_receipts(env, correction)[1]) == 2
    assert len(_receipts(env, purchase)[1]) == 2


def test_cancel_live_only_reverses_main_not_cashier(env):
    supplier, beer = _supplier_and_product(env)
    staged = warehouse_service.stage_allocation(env[1], env[2].id, {
        env[4].id: {"MAIN": 7, "CASHIER": 3},
        beer.id: {"MAIN": 0, "CASHIER": 0},
    })
    db.session.commit()
    staged.status = "LIVE"
    db.session.commit()
    purchase = _purchase(env, supplier, beer, "MAIN-LIVE-CANCEL")
    purchase_service.receive(env[1], env[2].id, purchase.id)
    db.session.commit()
    purchase_service.cancel_received(env[1], env[2].id, purchase.id, "Erreur fournisseur")
    db.session.commit()
    assert warehouse_service.totals(env[2].id)[env[4].id] == {
        "MAIN": 7, "CASHIER": 3, "TRANSIT": 0, "TOTAL": 10,
    }
    assert warehouse_service.totals(env[2].id)[beer.id] == {
        "MAIN": 0, "CASHIER": 0, "TRANSIT": 0, "TOTAL": 0,
    }
    assert all(r.reversal_movement_id is not None for r in _receipts(env, purchase)[1])
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement).where(
        WarehouseStockMovement.bar_id == env[2].id,
        WarehouseStockMovement.movement_type == "PURCHASE_REVERSAL",
    )) == 2


def test_partial_supplier_payment_kept_and_prevents_correction(env):
    supplier, beer = _supplier_and_product(env)
    purchase = _purchase(env, supplier, beer, "MAIN-PARTIAL")
    purchase_service.receive(env[1], env[2].id, purchase.id)
    payment = purchase_service.pay(
        env[1], env[2].id, purchase.id, "MAIN-PAY", 5000, "CARD", "Acompte fournisseur"
    )
    db.session.commit()
    assert payment.amount == 5000
    assert purchase_service.net_paid(env[2].id, purchase.id) == 5000
    assert purchase_service.due(env[2].id, purchase.id) == 9650
    with pytest.raises(ValueError, match="PURCHASE_HAS_PAYMENTS"):
        purchase_service.cancel_received(env[1], env[2].id, purchase.id, "Corriger")
    db.session.rollback()
    assert len(_receipts(env, purchase)[1]) == 2


def test_storekeeper_must_be_explicitly_authorized_and_never_manages_finances(env):
    supplier, beer = _supplier_and_product(env)
    keeper, assignment = _keeper(env)
    purchase = _purchase(env, supplier, beer, "MAIN-KEEPER-001")
    db.session.commit()
    with pytest.raises(PermissionError):
        purchase_service.receive(keeper, env[2].id, purchase.id)
    with pytest.raises(PermissionError):
        purchase_service.create(keeper, env[2].id, supplier.id, "DENIED-001", [
            {"product_id": beer.id, "quantity": 1, "unit_cost": 50}
        ])
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.purchases.receive", True)
    db.session.commit()
    with pytest.raises(PermissionError):
        purchase_service.receive(keeper, env[3].id, purchase.id)
    db.session.rollback()
    purchase_service.receive(keeper, env[2].id, purchase.id)
    db.session.commit()
    assert all(r.received_by_id == keeper.id for r in _receipts(env, purchase)[1])
    assert not env[0].test_client().get(f"/bars/{env[2].id}/purchases").status_code == 200
    assert all(not perm for perm in (
        __import__("app.permissions", fromlist=["permissions"]).permissions.evaluate(
            keeper, action, env[2].id
        ).allowed for action in ("purchases.manage", "payments.record", "orders.edit")
    ))


def test_delegated_manager_can_purchase_but_not_confirm_without_receive_grant(env):
    supplier, beer = _supplier_and_product(env)
    admin = User(
        email="warehouse-admin-buy@example.invalid", display_name="Gérant",
        category="EMPLOYEE",
    )
    admin.set_password("test-password")
    db.session.add(admin)
    db.session.flush()
    assignment = assign_staff(env[1], env[2].id, admin.id, "BAR_ADMIN")
    db.session.commit()
    with pytest.raises(PermissionError):
        purchase_service.create(admin, env[2].id, supplier.id, "NO-GRANT", [
            {"product_id": beer.id, "quantity": 1, "unit_cost": 50}
        ])
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.purchases.manage", True)
    db.session.commit()
    purchase = purchase_service.create(admin, env[2].id, supplier.id, "WITH-GRANT", [
        {"product_id": beer.id, "quantity": 1, "unit_cost": 50}
    ])
    db.session.commit()
    with pytest.raises(PermissionError):
        purchase_service.receive(admin, env[2].id, purchase.id)
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.purchases.receive", True)
    db.session.commit()
    purchase_service.receive(admin, env[2].id, purchase.id)
    db.session.commit()
    assert purchase.status == "POSTED"


def test_storekeeper_web_receipt_requires_csrf_and_never_exposes_payment_details(env):
    supplier, beer = _supplier_and_product(env)
    keeper, assignment = _keeper(env, "web")
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.purchases.receive", True)
    purchase = _purchase(env, supplier, beer, "MAIN-UI")
    db.session.commit()
    client = env[0].test_client()
    assert _login(client, keeper.email).status_code == 302
    page = client.get(f"/bars/{env[2].id}/warehouse")
    assert page.status_code == 200
    assert "MAIN-UI" in page.text
    assert "Confirmer la réception MAIN" in page.text
    assert "Paiement initial achat" not in page.text
    assert client.post(
        f"/bars/{env[2].id}/warehouse/purchases/{purchase.id}/receive"
    ).status_code == 400
    response = client.post(
        f"/bars/{env[2].id}/warehouse/purchases/{purchase.id}/receive",
        data={"csrf_token": _csrf(page)}
    )
    assert response.status_code == 302
    assert db.session.get(Purchase, purchase.id).status == "POSTED"
    assert client.get(f"/bars/{env[3].id}/warehouse").status_code == 404


def test_db_denies_cross_bar_receipt_location_even_with_valid_purchase_line(env):
    supplier, beer = _supplier_and_product(env)
    purchase = _purchase(env, supplier, beer, "MAIN-FK")
    db.session.flush()
    line = db.session.scalar(select(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
    foreign = warehouse_service.ensure_locations(env[3].id)["MAIN"]
    db.session.flush()
    db.session.add(WarehousePurchaseReceiptLine(
        bar_id=env[2].id, purchase_line_id=line.id, location_id=foreign.id,
        product_id=line.product_id, quantity=line.quantity,
        unit_cost_snapshot=line.unit_cost_snapshot, mode="PRE_CUTOVER",
        received_by_id=env[1].id, received_at=__import__(
            "app.models", fromlist=["utcnow"]
        ).utcnow(),
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()
