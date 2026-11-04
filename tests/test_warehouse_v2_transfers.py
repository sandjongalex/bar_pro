"""PROMPT 4: tenant-safe replenishments without stock creation or reverse flows."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import CashMovement, Order, StockBalance, User, utcnow
from app.warehouse_models import (
    WarehouseAllocation, WarehouseTransfer, WarehouseTransferEvent,
    WarehouseTransferLine, WarehouseTransferReceipt, WarehouseStockMovement,
)
from app.warehouse_service import WarehouseError, warehouse_service
from app.warehouse_transfer_service import warehouse_transfer_service as service
from test_workflows import env


def _live(env, main="7", cashier="3"):
    owner, bar, product = env[1], env[2], env[4]
    staged = warehouse_service.stage_allocation(
        owner, bar.id, {product.id: {"MAIN": main, "CASHIER": cashier}},
    )
    db.session.commit()
    staged.status = "LIVE"  # Simulation in isolated test database only.
    db.session.commit()
    return owner, bar.id, product.id


def _voucher(env, quantity="4"):
    owner, bar_id, pid = _live(env)
    ticket = service.create_request(owner, bar_id, "TR-V2-001", {pid: quantity})
    db.session.commit()
    return owner, bar_id, pid, ticket


def _shipped(env, quantity="4"):
    owner, bar_id, pid, ticket = _voucher(env, quantity)
    service.prepare(owner, bar_id, ticket.id, {pid: quantity})
    service.ship(owner, bar_id, ticket.id)
    db.session.commit()
    return owner, bar_id, pid, ticket


def test_request_before_live_has_no_physical_stock_effect(env):
    owner, bar, product = env[1], env[2], env[4]
    request = service.create_request(owner, bar.id, "TR-PENDING", {product.id: "2"})
    db.session.commit()
    assert request.status == "REQUESTED"
    assert request.dispatched_at is None
    assert not warehouse_service.totals(bar.id)
    service.prepare(owner, bar.id, request.id, {product.id: "2"})
    db.session.commit()
    with pytest.raises(WarehouseError, match="WAREHOUSE_NOT_LIVE"):
        service.ship(owner, bar.id, request.id)
    db.session.rollback()
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
    )) == 10
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement)) == 0


def test_request_converts_case_to_bottles_and_rejects_duplicate_reference(env):
    owner, bar, product = env[1], env[2], env[4]
    product.units_per_case = 4
    db.session.commit()
    ticket = service.create_request(owner, bar.id, "TR-CASES", {
        product.id: {"quantity": "2", "unit": "CASE"},
    })
    db.session.commit()
    line = db.session.scalar(select(WarehouseTransferLine).where(
        WarehouseTransferLine.transfer_id == ticket.id,
    ))
    assert line.quantity_requested == Decimal("8")
    assert line.requested_unit == "CASE"
    assert line.units_per_case_snapshot == 4
    assert line.quantity_dispatched == 0
    with pytest.raises(WarehouseError, match="TRANSFER_REFERENCE_EXISTS"):
        service.create_request(owner, bar.id, "TR-CASES", {product.id: 1})
    db.session.rollback()


def test_prepare_is_editable_only_before_ship_and_cannot_exceed_request(env):
    owner, bar_id, pid, ticket = _voucher(env)
    with pytest.raises(WarehouseError, match="PREPARED_EXCEEDS_REQUESTED"):
        service.prepare(owner, bar_id, ticket.id, {pid: "5"})
    db.session.rollback()
    service.prepare(owner, bar_id, ticket.id, {pid: 2})
    service.prepare(owner, bar_id, ticket.id, {pid: 3})
    db.session.commit()
    line = db.session.scalar(select(WarehouseTransferLine).where(
        WarehouseTransferLine.transfer_id == ticket.id,
    ))
    assert line.quantity_prepared == 3
    assert ticket.status == "PREPARING"
    service.ship(owner, bar_id, ticket.id)
    db.session.commit()
    with pytest.raises(WarehouseError, match="TRANSFER_IMMUTABLE_AFTER_DISPATCH"):
        service.prepare(owner, bar_id, ticket.id, {pid: 1})
    db.session.rollback()
    assert ticket.status == "SHIPPED"


def test_ship_never_overdraws_main_or_creates_sale(env):
    owner, bar_id, pid, ticket = _voucher(env, quantity="8")
    service.prepare(owner, bar_id, ticket.id, {pid: 8})
    db.session.commit()
    with pytest.raises(WarehouseError, match="INSUFFICIENT_WAREHOUSE_STOCK"):
        service.ship(owner, bar_id, ticket.id)
    db.session.rollback()
    assert warehouse_service.totals(bar_id)[pid] == {
        "MAIN": 7, "CASHIER": 3, "TRANSIT": 0, "TOTAL": 10,
    }
    assert db.session.scalar(select(func.count()).select_from(CashMovement)) == 0
    assert db.session.scalar(select(func.count()).select_from(Order)) == 0


def test_ship_conserves_quantities_and_cost_and_blocks_retry(env):
    owner, bar_id, pid, ticket = _shipped(env)
    original_cost = env[4].valuation_unit_cost
    assert warehouse_service.totals(bar_id)[pid] == {
        "MAIN": 3, "CASHIER": 3, "TRANSIT": 4, "TOTAL": 10,
    }
    line = db.session.scalar(select(WarehouseTransferLine).where(
        WarehouseTransferLine.transfer_id == ticket.id,
    ))
    assert line.unit_cost_snapshot == original_cost
    with pytest.raises(WarehouseError, match="TRANSFER_NOT_PREPARING"):
        service.ship(owner, bar_id, ticket.id)
    db.session.rollback()
    assert env[4].valuation_unit_cost == original_cost
    assert db.session.scalar(select(func.count()).select_from(CashMovement)) == 0
    assert db.session.scalar(select(func.count()).select_from(Order)) == 0


def test_receipt_is_idempotent_and_remaining_cannot_be_received_twice(env):
    owner, bar_id, pid, ticket = _shipped(env)
    first = service.receive(owner, bar_id, ticket.id, {pid: 2}, "receipt-test-0001")
    db.session.commit()
    count = db.session.scalar(select(func.count()).select_from(WarehouseStockMovement))
    again = service.receive(owner, bar_id, ticket.id, {pid: 2}, "receipt-test-0001")
    db.session.commit()
    assert again.id == first.id
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement)) == count
    assert ticket.status == "PARTIALLY_RECEIVED"
    with pytest.raises(WarehouseError, match="IDEMPOTENCY_CONFLICT"):
        service.receive(owner, bar_id, ticket.id, {pid: 1}, "receipt-test-0001")
    db.session.rollback()
    with pytest.raises(WarehouseError, match="RECEIPT_EXCEEDS_TRANSIT"):
        service.receive(owner, bar_id, ticket.id, {pid: 3}, "receipt-test-0002")
    db.session.rollback()
    service.receive(owner, bar_id, ticket.id, {pid: 2}, "receipt-test-0003")
    db.session.commit()
    assert ticket.status == "RECEIVED"
    assert warehouse_service.totals(bar_id)[pid] == {
        "MAIN": 3, "CASHIER": 7, "TRANSIT": 0, "TOTAL": 10,
    }
    assert db.session.scalar(select(func.count()).select_from(WarehouseTransferReceipt)) == 2


def test_dispute_stays_in_transit_and_late_delivery_reaches_cashier(env):
    owner, bar_id, pid, ticket = _shipped(env)
    service.receive(owner, bar_id, ticket.id, {pid: 2}, "receipt-test-difference")
    service.report_dispute(owner, bar_id, ticket.id, {pid: 2}, "Deux bouteilles manquantes")
    db.session.commit()
    assert ticket.status == "DISPUTED"
    assert warehouse_service.totals(bar_id)[pid]["TRANSIT"] == 2
    with pytest.raises(WarehouseError, match="RECEIPT_EXCEEDS_TRANSIT"):
        service.receive(owner, bar_id, ticket.id, {pid: 1}, "receipt-test-disputed")
    db.session.rollback()
    service.resolve_dispute(
        owner, bar_id, ticket.id, pid, 2, "DELIVERED", "Livraison complémentaire vérifiée",
    )
    db.session.commit()
    assert ticket.status == "RECEIVED"
    assert warehouse_service.totals(bar_id)[pid] == {
        "MAIN": 3, "CASHIER": 7, "TRANSIT": 0, "TOTAL": 10,
    }
    kinds = {x.event_kind for x in db.session.scalars(select(WarehouseTransferEvent).where(
        WarehouseTransferEvent.transfer_id == ticket.id,
    ))}
    assert {"REQUESTED", "PREPARED", "SHIPPED", "RECEIVED", "DISPUTED", "DELIVERED"} <= kinds


def test_justified_loss_cannot_return_to_main_or_fake_cashier_stock(env):
    owner, bar_id, pid, ticket = _shipped(env)
    service.receive(owner, bar_id, ticket.id, {pid: 2}, "receipt-test-loss")
    service.report_dispute(owner, bar_id, ticket.id, {pid: 2}, "Casse constatée à la livraison")
    db.session.commit()
    service.resolve_dispute(
        owner, bar_id, ticket.id, pid, 2, "LOSS_APPROVED", "Procès-verbal de casse signé",
    )
    db.session.commit()
    assert warehouse_service.totals(bar_id)[pid] == {
        "MAIN": 3, "CASHIER": 5, "TRANSIT": 0, "TOTAL": 8,
    }
    row = db.session.scalar(select(WarehouseTransferLine).where(
        WarehouseTransferLine.transfer_id == ticket.id,
    ))
    assert row.quantity_received == 2 and row.quantity_lost == 2
    service.close(owner, bar_id, ticket.id, "Clôture après validation de perte")
    db.session.commit()
    assert ticket.status == "CLOSED"
    with pytest.raises(WarehouseError, match="TRANSFER_NOT_RECEIVABLE"):
        service.receive(owner, bar_id, ticket.id, {pid: 1}, "receipt-test-afterclose")
    db.session.rollback()
    assert not db.session.scalar(select(WarehouseStockMovement.id).where(
        WarehouseStockMovement.movement_type == "TRANSFER_IN",
        WarehouseStockMovement.location_id == warehouse_service.ensure_locations(bar_id)["MAIN"].id,
    ))


def test_unsettled_transfer_cannot_close(env):
    owner, bar_id, pid, ticket = _shipped(env)
    with pytest.raises(WarehouseError, match="TRANSFER_UNSETTLED_TRANSIT"):
        service.close(owner, bar_id, ticket.id, "Clôturer les quantités restantes")
    db.session.rollback()
    assert ticket.status == "SHIPPED"


def test_foreign_bar_and_unprivileged_actor_cannot_mutate_transfers(env):
    owner, bar_id, pid, ticket = _shipped(env)
    with pytest.raises((LookupError, PermissionError)):
        service.receive(env[3].owner, env[3].id, ticket.id, {pid: 1}, "receipt-cross-bar")
    db.session.rollback()
    with pytest.raises(PermissionError):
        service.resolve_dispute(env[5], bar_id, ticket.id, pid, 1, "LOSS_APPROVED", "Fake")
    db.session.rollback()
    with pytest.raises(PermissionError):
        service.ship(env[5], bar_id, ticket.id)
    db.session.rollback()


def test_physical_movement_ledger_is_balanced_and_dispute_auditable(env):
    owner, bar_id, pid, ticket = _shipped(env)
    service.receive(owner, bar_id, ticket.id, {pid: 1}, "receipt-ledger-1")
    service.report_dispute(owner, bar_id, ticket.id, {pid: 3}, "Colis incomplet et non scellé")
    db.session.commit()
    stock = warehouse_service.totals(bar_id)[pid]
    assert stock["TOTAL"] == 10 and stock["TRANSIT"] == 3
    details, lines, events = service.detail(owner, bar_id, ticket.id)
    assert details.id == ticket.id and len(lines) == 1
    assert len(events) >= 5
    assert [e.id for e in events] == sorted(e.id for e in events)
    assert db.session.scalar(select(func.count()).select_from(Order)) == 0


def test_invalid_transfer_units_ids_or_stock_values(env):
    owner, bar, product = env[1], env[2], env[4]
    for val in (-1, "NaN", "Infinity", "0.0000001", 0):
        with pytest.raises((WarehouseError, ValueError)):
            service.create_request(owner, bar.id, "TR-BAD", {product.id: val})
        db.session.rollback()
    with pytest.raises(WarehouseError):
        service.create_request(owner, bar.id, "TR-NOTCASE", {
            product.id: {"quantity": 1, "unit": "PALLET"}
        })
    db.session.rollback()
    with pytest.raises(LookupError):
        service.create_request(owner, bar.id, "TR-CROSS", {999999: 1})
    db.session.rollback()


def test_transfer_web_board_and_csrf_guard(env):
    from test_employee_tenant_auth import _login, _csrf

    owner, bar_id, pid = _live(env)
    client = env[0].test_client()
    assert _login(client, owner.email).status_code == 302
    page = client.get(f"/bars/{bar_id}/warehouse/transfers/manage")
    assert page.status_code == 200
    assert "Bons de transfert" in page.text
    assert client.get(f"/bars/{env[3].id}/warehouse/transfers").status_code == 404
    bare = client.post(
        f"/bars/{bar_id}/warehouse/transfers",
        json={"reference": "WEB-FAIL", "quantities": {str(pid): "1"}},
    )
    assert bare.status_code == 400  # CSRF required also for session JSON.
    csrf = _csrf(page)
    response = client.post(
        f"/bars/{bar_id}/warehouse/transfers/new",
        data={
            "csrf_token": csrf,
            "reference": "WEB-REQ-1",
            "recipient_user_id": str(owner.id),
            f"qty_{pid}": "2",
            f"unit_{pid}": "BOTTLE",
        },
    )
    assert response.status_code == 302
    assert client.get(f"/bars/{bar_id}/warehouse/transfers/manage").status_code == 200
    assert db.session.scalar(select(WarehouseTransfer.id).where(
        WarehouseTransfer.reference == "WEB-REQ-1",
    )) is not None


def test_transfer_internal_api_json_create_prepare_ship_and_receive(env):
    from test_employee_tenant_auth import _login, _csrf

    owner, bar_id, pid = _live(env)
    client = env[0].test_client()
    assert _login(client, owner.email).status_code == 302
    csrf = _csrf(client.get(f"/bars/{bar_id}/warehouse/transfers/manage"))
    headers = {"X-CSRFToken": csrf}
    base = f"/bars/{bar_id}/warehouse/transfers"
    res = client.post(base, headers=headers, json={
        "reference": "API-TR-1",
        "recipient_user_id": owner.id,
        "quantities": {str(pid): {"unit": "BOTTLE", "quantity": "3"}},
    })
    assert res.status_code == 201, res.text
    transfer_id = res.json["id"]
    assert client.get(f"{base}/{transfer_id}").status_code == 200
    assert client.post(f"{base}/{transfer_id}/ship", headers=headers, json={}).status_code == 422
    assert client.post(f"{base}/{transfer_id}/prepare", headers=headers, json={
        "quantities": {str(pid): "3"},
    }).status_code == 200
    assert client.post(f"{base}/{transfer_id}/ship", headers=headers, json={}).status_code == 200
    received = client.post(f"{base}/{transfer_id}/receive", headers=headers, json={
        "quantities": {str(pid): "3"},
        "idempotency_key": "api-receipt-00000001",
    })
    assert received.status_code == 200, received.text
    duplicate = client.post(f"{base}/{transfer_id}/receive", headers=headers, json={
        "quantities": {str(pid): "3"},
        "idempotency_key": "api-receipt-00000001",
    })
    assert duplicate.status_code == 200
    assert duplicate.json["receipt_id"] == received.json["receipt_id"]
    assert warehouse_service.totals(bar_id)[pid] == {
        "MAIN": 4, "CASHIER": 6, "TRANSIT": 0, "TOTAL": 10,
    }


def test_internal_movement_gateway_forbids_reverse_or_fake_credit(env):
    owner, bar_id, pid = _live(env)
    locations = warehouse_service.ensure_locations(bar_id)
    before = warehouse_service.totals(bar_id)[pid].copy()
    for kind, where, delta in (
        ("TRANSFER_IN", locations["MAIN"].id, Decimal("1")),
        ("TRANSFER_OUT", locations["CASHIER"].id, Decimal("-1")),
        ("TRANSFER_OUT", locations["MAIN"].id, Decimal("1")),
        ("TRANSIT_IN", None, Decimal("-1")),
        ("PURCHASE_IN", locations["CASHIER"].id, Decimal("1")),
    ):
        with pytest.raises(WarehouseError, match="INVALID_TRANSFER_DIRECTION"):
            warehouse_service._move(
                actor=owner, bar_id=bar_id, product_id=pid, location_id=where,
                kind=kind, delta=delta, cost=Decimal("40"),
                reason="Ne doit jamais modifier les magasins",
            )
    db.session.rollback()
    assert warehouse_service.totals(bar_id)[pid] == before


def test_cashier_requests_keeper_ships_and_recipient_receives(env):
    from test_employee_tenant_auth import _employee

    owner, bar_id, pid = _live(env)
    cashier, _ = _employee(env[2], "CASHIER", "transfer-cashier")
    keeper = User(
        email="warehouse-transfer-storekeeper@example.invalid",
        display_name="Warehouse Keeper", category="EMPLOYEE",
    )
    keeper.set_password("test-password")
    db.session.add(keeper)
    db.session.flush()
    from app.bar_services import assign_staff
    assign_staff(owner, bar_id, keeper.id, "STOREKEEPER")
    db.session.commit()

    transfer = service.create_request(cashier, bar_id, "CASHIER-ASK-1", {pid: 3})
    db.session.commit()
    assert transfer.requested_by_id == cashier.id
    assert transfer.recipient_user_id == cashier.id

    service.prepare(keeper, bar_id, transfer.id, {pid: 3})
    service.ship(keeper, bar_id, transfer.id)
    db.session.commit()
    assert transfer.dispatched_by_id == keeper.id

    with pytest.raises(PermissionError):
        service.receive(keeper, bar_id, transfer.id, {pid: 1}, "keeper-receipt-rejected")
    db.session.rollback()
    service.receive(cashier, bar_id, transfer.id, {pid: 3}, "cashier-receipt-approved")
    db.session.commit()
    assert transfer.status == "RECEIVED"
    assert transfer.last_received_by_id == cashier.id


def test_delegated_manager_can_resolve_but_keeper_cannot(env):
    from app.bar_services import assign_staff
    from app.warehouse_permission_service import set_warehouse_grant

    owner, bar_id, pid, transfer = _shipped(env)
    admin = User(email="warehouse-resolve-manager@example.invalid",
                 display_name="Manager", category="EMPLOYEE")
    admin.set_password("test-password")
    keeper = User(email="warehouse-noresolve-keeper@example.invalid",
                  display_name="Keeper", category="EMPLOYEE")
    keeper.set_password("test-password")
    db.session.add_all([admin, keeper])
    db.session.flush()
    assignment = assign_staff(owner, bar_id, admin.id, "BAR_ADMIN")
    assign_staff(owner, bar_id, keeper.id, "STOREKEEPER")
    set_warehouse_grant(owner, bar_id, assignment.id, "warehouse.discrepancies.resolve", True)
    db.session.commit()

    service.report_dispute(owner, bar_id, transfer.id, {pid: 2}, "Bouteilles reçues en quantité inférieure")
    db.session.commit()
    with pytest.raises(PermissionError):
        service.resolve_dispute(keeper, bar_id, transfer.id, pid, 2, "LOSS_APPROVED", "Perte")
    db.session.rollback()
    service.resolve_dispute(admin, bar_id, transfer.id, pid, 2, "DELIVERED", "Écart levé après réception")
    db.session.commit()
    assert warehouse_service.totals(bar_id)[pid]["TRANSIT"] == 2
    assert transfer.status == "PARTIALLY_RECEIVED"
