"""Stage 1 inventory V2 foundation and isolation regressions.

The final approval, temporal reconciliation and financial calculations are
intentionally not implemented in this milestone.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from test_workflows import balance, env
from app.extensions import db
from app.auth import issue
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_services import inventory_service
from app.models import Inventory, StaffAssignment, User, utcnow
from app.shift_service import end_shift, start_shift


def on_duty_cashier(env, suffix="one"):
    owner, bar = env[1], env[2]
    user = User(
        email=f"cashier-v2-{suffix}@example.invalid",
        display_name=f"Cashier {suffix}",
        category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id, user_id=user.id, role="CASHIER", started_at=utcnow()
    )
    db.session.add(assignment)
    db.session.flush()
    shift = start_shift(owner, bar.id, assignment.id)
    db.session.commit()
    return user, assignment, shift


def test_cashier_creates_count_but_cannot_validate_or_adjust_stock(env):
    cashier, _, shift = on_duty_cashier(env)
    inventory = inventory_service.create(
        cashier, env[2].id, "V2-1", [env[4].id], "Comptage V2"
    )
    db.session.commit()
    assigned = db.session.scalar(
        select(InventoryCashierAssignment).where(
            InventoryCashierAssignment.inventory_id == inventory.id
        )
    )
    assert assigned.cashier_shift_id == shift.id
    assert assigned.cashier_user_id == cashier.id
    assert assigned.cashier_name_snapshot == cashier.display_name
    assert assigned.bar_id == env[2].id
    inventory_service.count(
        cashier, env[2].id, inventory.id, {str(env[4].id): "8"}
    )
    db.session.commit()
    assert balance(env) == Decimal(10)
    assert inventory.status == "DRAFT"
    with pytest.raises(PermissionError):
        inventory_service.post(cashier, env[2].id, inventory.id)
    assert balance(env) == Decimal(10)
    with pytest.raises(ValueError, match="INVENTORY_V2_PENDING_APPROVAL"):
        inventory_service.post(env[1], env[2].id, inventory.id)
    assert balance(env) == Decimal(10)


def test_owner_explicitly_selects_correct_cashier_shift(env):
    cashier, _, shift = on_duty_cashier(env)
    inv = inventory_service.create(
        env[1], env[2].id, "V2-OWNER", [env[4].id],
        "Session sélectionnée", cashier_shift_id=str(shift.id),
    )
    db.session.commit()
    record = db.session.scalar(select(InventoryCashierAssignment).where(
        InventoryCashierAssignment.inventory_id == inv.id
    ))
    assert record.cashier_user_id == cashier.id
    assert record.assigned_by_id == env[1].id
    with pytest.raises((LookupError, ValueError)):
        inventory_service.create(
            env[1], env[2].id, "BAD", [env[4].id],
            "Session absente", cashier_shift_id=99999999,
        )


def test_cashier_cannot_select_another_cashier_or_read_other_inventories(env):
    cashier_a, _, shift_a = on_duty_cashier(env, "a")
    cashier_b, _, shift_b = on_duty_cashier(env, "b")
    a = inventory_service.create(
        cashier_a, env[2].id, "V2-A", [env[4].id], "A"
    )
    b = inventory_service.create(
        cashier_b, env[2].id, "V2-B", [env[4].id], "B"
    )
    db.session.commit()
    assert inventory_service.get(cashier_a, env[2].id, a.id).id == a.id
    with pytest.raises(LookupError):
        inventory_service.get(cashier_a, env[2].id, b.id)
    with pytest.raises(LookupError):
        inventory_service.count(cashier_a, env[2].id, b.id, {env[4].id: 1})
    with pytest.raises(LookupError):
        inventory_service.create(
            cashier_a, env[2].id, "V2-FOREIGN-SHIFT",
            [env[4].id], "A", cashier_shift_id=shift_b.id,
        )
    assert shift_a.id != shift_b.id


def test_cashier_cannot_create_or_count_when_off_duty(env):
    cashier, assignment, _ = on_duty_cashier(env)
    inv = inventory_service.create(
        cashier, env[2].id, "V2-SHIFT", [env[4].id], "Comptage"
    )
    db.session.commit()
    end_shift(env[1], env[2].id, assignment.id)
    db.session.commit()
    with pytest.raises(PermissionError):
        inventory_service.count(cashier, env[2].id, inv.id, {env[4].id: 3})
    with pytest.raises(PermissionError):
        inventory_service.create(
            cashier, env[2].id, "V2-CLOSED", [env[4].id], "Comptage"
        )
    assert balance(env) == Decimal(10)


def test_legacy_inventory_keeps_existing_path_and_no_assignment(env):
    legacy = inventory_service.create(
        env[1], env[2].id, "V1-OLD", [env[4].id], "Legacy"
    )
    db.session.commit()
    assert db.session.scalar(
        select(InventoryCashierAssignment.id).where(
            InventoryCashierAssignment.inventory_id == legacy.id
        )
    ) is None
    inventory_service.count(env[1], env[2].id, legacy.id, {env[4].id: 9})
    inventory_service.post(env[1], env[2].id, legacy.id)
    db.session.commit()
    assert legacy.status == "POSTED"
    assert balance(env) == Decimal(9)


def test_cashier_api_read_is_scoped_to_own_inventory(env):
    cashier_a, _, _ = on_duty_cashier(env, "scope-a")
    cashier_b, _, _ = on_duty_cashier(env, "scope-b")
    inventory_service.create(cashier_a, env[2].id, "SCOPE-A", [env[4].id], "A")
    other = inventory_service.create(cashier_b, env[2].id, "SCOPE-B", [env[4].id], "B")
    db.session.commit()
    token, _ = issue(cashier_a, env[2].id)
    db.session.commit()
    headers = {"Authorization": "Bearer " + token}
    client = env[0].test_client()
    base = f"/api/v1/bars/{env[2].id}/inventories"
    result = client.get(base, headers=headers)
    assert result.status_code == 200
    assert len(result.json["data"]) == 1
    assert result.json["data"][0]["cashier_user_id"] == str(cashier_a.id)
    assert client.get(f"{base}/{other.id}", headers=headers).status_code == 404
    assert client.post(f"{base}/{other.id}/post", headers=headers).status_code == 403


def test_owner_can_attribute_v2_inventory_without_cashier_shift(env):
    cashier, staff, shift = on_duty_cashier(env, "offline")
    end_shift(env[1], env[2].id, staff.id)
    db.session.commit()
    inventory = inventory_service.create(
        env[1], env[2].id, "V2-OFFLINE", [env[4].id],
        "Contrôle du propriétaire", cashier_user_id=str(cashier.id),
    )
    db.session.commit()
    linked = db.session.scalar(select(InventoryCashierAssignment).where(
        InventoryCashierAssignment.inventory_id == inventory.id,
    ))
    assert linked.cashier_shift_id is None
    assert linked.cashier_user_id == cashier.id
    assert linked.cashier_name_snapshot == cashier.display_name
    inventory_service.count_v2_entries(env[1], env[2].id, inventory.id, [{
        "product_id": env[4].id, "units": "8", "cases": "",
        "note": "", "revision": 0,
    }])
    db.session.commit()
    assert balance(env) == Decimal(10)


def test_owner_manual_cashier_name_without_user(env):
    inventory = inventory_service.create(
        env[1], env[2].id, "V2-NAME", [env[4].id],
        "Comptage attribution manuelle", cashier_name="Nancy",
    )
    db.session.commit()
    linked = db.session.scalar(select(InventoryCashierAssignment).where(
        InventoryCashierAssignment.inventory_id == inventory.id,
    ))
    assert linked.cashier_name_snapshot == "Nancy"
    assert linked.cashier_shift_id is None
    assert linked.cashier_user_id is None
    assert inventory_service.get(env[1], env[2].id, inventory.id).id == inventory.id
    assert balance(env) == Decimal(10)


def test_owner_manual_cashier_rejects_invalid_selection(env):
    with pytest.raises(ValueError, match="INVALID_CASHIER_NAME"):
        inventory_service.create(env[1], env[2].id, "BAD-NAME", [env[4].id],
                                 cashier_name="N")
    db.session.rollback()
    with pytest.raises(LookupError):
        inventory_service.create(env[1], env[2].id, "BAD-USER", [env[4].id],
                                 cashier_user_id=999999)
    db.session.rollback()
    with pytest.raises(ValueError, match="INVALID_CASHIER_SHIFT"):
        inventory_service.create(env[1], env[2].id, "BAD-DOUBLE", [env[4].id],
                                 cashier_user_id=999999, cashier_name="Nancy")
    db.session.rollback()
