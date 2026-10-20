"""Inventory V2 approver ceilings, first audit, lock and ledger integrity."""
from decimal import Decimal
import pytest
from sqlalchemy import select
from test_workflows import env, balance
from test_inventory_v2_foundation import on_duty_cashier
from app.auth import issue
from app.extensions import db
from app.models import Inventory, StaffAssignment, StockMovement, User, utcnow
from app.inventory_services import inventory_service
from app.inventory_approval import decide, submit, update_policy, evaluation, policy_data
from app.inventory_approval_models import InventoryApproval
from app.stock_service import stock_service


def make_admin(env):
    user = User(email="inventory-admin@example.invalid",
                display_name="Bar Admin", category="EMPLOYEE")
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(StaffAssignment(bar_id=env[2].id, user_id=user.id,
                                   role="BAR_ADMIN", started_at=utcnow()))
    db.session.commit()
    return user


def make_count(env, *, reference="APPROVAL-V2", quantity="10", justification=""):
    cashier, _, shift = on_duty_cashier(env, reference.lower())
    inv = inventory_service.create(cashier, env[2].id,
                                   reference, [env[4].id], "Audit caisse")
    db.session.commit()
    inventory_service.count_v2_entries(cashier, env[2].id, inv.id, [{
        "product_id": env[4].id, "cases": "", "units": quantity,
        "note": justification, "revision": 0,
    }])
    db.session.commit()
    return cashier, inv


def post_old_baseline(env):
    old = inventory_service.create(env[1], env[2].id, "PREVIOUS-CLOSED",
                                   [env[4].id], "Baseline")
    db.session.commit()
    inventory_service.count(env[1], env[2].id, old.id, {str(env[4].id): "10"})
    inventory_service.post(env[1], env[2].id, old.id)
    db.session.commit()


def test_first_inventory_requires_owner_even_when_no_difference(env):
    admin = make_admin(env)
    cashier, inv = make_count(env, reference="FIRST-V2")
    result = submit(cashier, env[2].id, inv.id)
    db.session.commit()
    assert result.status == "SUBMITTED"
    assert inv.status == "DRAFT"
    with pytest.raises(ValueError, match="INVENTORY_SUBMITTED_LOCKED"):
        inventory_service.count(cashier, env[2].id, inv.id,
                                {str(env[4].id): "10"})
    db.session.rollback()
    with pytest.raises(PermissionError, match="INVENTORY_OWNER_APPROVAL_REQUIRED"):
        decide(admin, env[2].id, inv.id,
               decision="approve", reason="Contrôle conforme")
    db.session.rollback()
    decision = decide(env[1], env[2].id, inv.id,
                      decision="approve", reason="Premier inventaire approuvé")
    db.session.commit()
    assert decision.status == "POSTED"
    assert inv.status == "POSTED"
    assert balance(env) == Decimal(10)
    with pytest.raises(ValueError, match="INVENTORY_REVIEW_STATE"):
        decide(env[1], env[2].id, inv.id,
               decision="approve", reason="Doublon")
    db.session.rollback()


def test_cashier_cannot_approve_but_admin_can_request_recount(env):
    admin = make_admin(env)
    cashier, inv = make_count(env, reference="RECOUNT-V2", quantity="9",
                              justification="Bouteille à vérifier")
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    with pytest.raises(PermissionError):
        decide(cashier, env[2].id, inv.id, decision="approve", reason="Interdit")
    db.session.rollback()
    changed = decide(admin, env[2].id, inv.id, decision="recount",
                     reason="Vérifier le casier de réserve")
    db.session.commit()
    assert changed.status == "RECOUNT"
    inventory_service.count_v2_entries(cashier, env[2].id, inv.id, [{
        "product_id": env[4].id, "units": "10", "cases": "",
        "note": "", "revision": 1,
    }])
    db.session.commit()
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    assert balance(env) == 10


def test_admin_within_owner_limits_can_approve_difference(env):
    post_old_baseline(env)
    admin = make_admin(env)
    policy = update_policy(env[1], env[2].id, max_unit_difference="2",
                           max_product_value="100", max_total_value="100")
    db.session.commit()
    assert policy.max_unit_difference == 2
    assert policy_data(env[2].id)["configured"] is True
    cashier, inv = make_count(env, reference="IN-LIMIT", quantity="9",
                              justification="Casse vérifiée")
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    assert evaluation(admin, env[2].id, inv.id)["owner_required"] is False
    decide(admin, env[2].id, inv.id, decision="approve", reason="Écart de casse approuvé")
    db.session.commit()
    assert inv.status == "POSTED"
    assert balance(env) == Decimal(9)
    moved = db.session.scalars(select(StockMovement).where(
        StockMovement.movement_type == "INVENTORY_ADJUSTMENT",
        StockMovement.bar_id == env[2].id,
    )).all()
    assert len(moved) == 1
    assert moved[0].quantity_delta == -1


def test_admin_over_any_limit_requires_owner(env):
    post_old_baseline(env)
    admin = make_admin(env)
    update_policy(env[1], env[2].id, max_unit_difference="0",
                  max_product_value="0", max_total_value="0")
    db.session.commit()
    cashier, inv = make_count(env, reference="OWNER-ONLY", quantity="8",
                              justification="Deux bouteilles cassées")
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    preview = evaluation(admin, env[2].id, inv.id)
    assert preview["owner_required"] is True
    assert Decimal(preview["total_absolute_value"]) == 80
    with pytest.raises(PermissionError):
        decide(admin, env[2].id, inv.id, decision="approve",
               reason="Pas autorisé")
    db.session.rollback()
    assert balance(env) == 10
    decide(env[1], env[2].id, inv.id, decision="approve",
           reason="Deux bouteilles vérifiées")
    db.session.commit()
    assert balance(env) == 8


def test_unjustified_difference_cannot_be_submitted(env):
    cashier, inv = make_count(env, reference="UNJUSTIFIED", quantity="9")
    with pytest.raises(ValueError, match="INVENTORY_JUSTIFICATION_REQUIRED"):
        submit(cashier, env[2].id, inv.id)
    db.session.rollback()
    assert db.session.scalar(select(InventoryApproval.id)) is None
    assert balance(env) == 10


def test_unrecorded_stock_change_blocks_submit(env):
    cashier, inv = make_count(env, reference="OUT-OF-BAND")
    from app.models import StockBalance
    row = db.session.scalar(select(StockBalance).where(
        StockBalance.bar_id == env[2].id,
        StockBalance.product_id == env[4].id,
    ))
    row.quantity = 11
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_RECOUNT_REQUIRED"):
        submit(cashier, env[2].id, inv.id)
    db.session.rollback()
    assert db.session.scalar(select(InventoryApproval.id)) is None


def test_selling_after_submission_does_not_generate_phantom_gap(env):
    post_old_baseline(env)
    admin = make_admin(env)
    update_policy(env[1], env[2].id, max_unit_difference="2",
                  max_product_value="100", max_total_value="100")
    db.session.commit()
    cashier, inv = make_count(env, reference="WHILE-SELLING",
                              quantity="9", justification="Bouteille endommagée")
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    stock_service.move(env[1], env[2].id, env[4].id, "SALE", "-2",
                       "Service pendant contrôle")
    db.session.commit()
    assert balance(env) == 8
    decide(admin, env[2].id, inv.id, decision="approve",
           reason="Bouteille endommagée contrôlée")
    db.session.commit()
    assert balance(env) == 7
    assert inv.status == "POSTED"


def test_foreign_owner_cannot_approve_or_change_policy(env):
    cashier, inv = make_count(env, reference="TENANT-APPROVAL")
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    with pytest.raises(PermissionError):
        decide(env[1], env[3].id, inv.id, decision="approve",
               reason="Mauvais bar")
    db.session.rollback()
    with pytest.raises(PermissionError):
        update_policy(env[7] if False else db.session.get(User, env[3].owner_id),
                      env[2].id, max_unit_difference="1",
                      max_product_value="1", max_total_value="1")
    db.session.rollback()
    assert balance(env) == 10


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1000000001", "0.0000001"])
def test_invalid_owner_policy_rejected(env, value):
    with pytest.raises(ValueError):
        update_policy(env[1], env[2].id, max_unit_difference="1",
                      max_product_value=value, max_total_value="100")
    db.session.rollback()


def test_api_submit_and_owner_approve(env):
    cashier, inv = make_count(env, reference="API-APPROVAL")
    token_cashier, _ = issue(cashier, env[2].id)
    token_owner, _ = issue(env[1], env[2].id)
    db.session.commit()
    client = env[0].test_client()
    path = f"/api/v1/bars/{env[2].id}/inventories/{inv.id}"
    outcome = client.post(path + "/submit", headers={
        "Authorization": "Bearer " + token_cashier,
    })
    assert outcome.status_code == 200, outcome.text
    assert outcome.json["data"]["status"] == "SUBMITTED"
    rejected = client.post(path + "/decision", headers={
        "Authorization": "Bearer " + token_cashier,
    }, json={"decision": "approve", "reason": "Interdit"})
    assert rejected.status_code == 403
    permitted = client.post(path + "/decision", headers={
        "Authorization": "Bearer " + token_owner,
    }, json={"decision": "approve", "reason": "Premier comptage contrôlé"})
    assert permitted.status_code == 200, permitted.text
    assert permitted.json["data"]["status"] == "POSTED"
    assert balance(env) == 10
