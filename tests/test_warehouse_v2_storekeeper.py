"""PROMPT 2: STOREKEEPER RBAC, owner delegations, web routes and tenant boundaries."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
import pytest

from app.auth import issue
from app.bar_services import assign_staff, create_employee, end_staff_assignment
from app.customer_models import UserNotification
from app.extensions import db
from app.models import AuditLog, StaffAssignment, User
from app.permissions import ROLE_ACTIONS, WRITES, permissions
from app.warehouse_access import STOREKEEPER_DEFAULT, WAREHOUSE_DELEGABLE
from app.warehouse_notifications import notify_warehouse_request
from app.warehouse_permission_models import WarehouseStaffGrant
from app.warehouse_permission_service import set_warehouse_grant
from test_employee_tenant_auth import _csrf, _login
from test_workflows import env


def _employee(env, role, suffix):
    user = User(
        email=f"warehouse-rbac-{suffix}@example.invalid",
        display_name=f"Warehouse {suffix}", category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = assign_staff(env[1], env[2].id, user.id, role)
    db.session.commit()
    return user, assignment


@pytest.mark.parametrize("forbidden", [
    "orders.read", "orders.create", "orders.edit", "orders.deliver",
    "payments.read", "payments.record", "cash.read", "cash.operate",
    "expenses.read", "expenses.record", "expenses.manage",
    "customers.read", "customers.manage", "customer_debt.manage",
    "refunds.record", "purchases.read", "purchases.manage",
    "suppliers.manage", "inventory.approve", "inventory.adjust",
    "inventory.create", "inventory.count",
    "warehouse.discrepancies.resolve", "warehouse.transfers.receive",
    "warehouse.requests.create", "warehouse.permissions.manage",
    "warehouse.transfers.return",  # not a supported action
])
def test_storekeeper_explicitly_denied_finance_sales_and_approval(env, forbidden):
    keeper, _ = _employee(env, "STOREKEEPER", forbidden.replace(".", "-"))
    assert not permissions.evaluate(keeper, forbidden, env[2].id).allowed


def test_keeper_allowed_warehouse_only_and_no_other_bar_access(env):
    keeper, assignment = _employee(env, "STOREKEEPER", "scope")
    assert assignment.role == "STOREKEEPER"
    assert all(permissions.evaluate(keeper, right, env[2].id).allowed for right in STOREKEEPER_DEFAULT)
    assert all(not permissions.evaluate(keeper, right, env[3].id).allowed for right in STOREKEEPER_DEFAULT)
    assert not permissions.evaluate(keeper, "warehouse.purchases.manage", env[2].id).allowed
    assert permissions.evaluate(keeper, "bars.read", env[2].id).allowed
    assert not permissions.evaluate(keeper, "bars.read", env[3].id).allowed


def test_storekeeper_login_web_and_anti_tenant_bypass(env):
    keeper, _ = _employee(env, "STOREKEEPER", "web")
    client = env[0].test_client()
    response = _login(client, keeper.email)
    assert response.status_code == 302
    assert response.location.endswith(f"/bars/{env[2].id}/warehouse")
    own = client.get(f"/bars/{env[2].id}/warehouse")
    assert own.status_code == 200
    assert "Espace magasin" in own.text
    assert "stock des deux magasins" in own.text
    assert client.get(f"/bars/{env[3].id}/warehouse").status_code == 404
    assert client.get(f"/bars/{env[3].id}/warehouse/permissions").status_code == 404
    assert client.get(f"/bars/{env[2].id}/warehouse/permissions").status_code in (403, 404)
    assert client.get(f"/bars/{env[2].id}/orders/new").status_code in (302, 403, 404)
    assert client.get(f"/bars/{env[2].id}/checkout").status_code in (302, 403, 404)
    assert client.get("/dashboard").status_code == 302  # no owner KPIs/finance


def test_storekeeper_api_bearer_cannot_read_other_tenant_or_order(env):
    keeper, _ = _employee(env, "STOREKEEPER", "jwt")
    token, _ = issue(keeper, env[2].id)
    db.session.commit()
    client = env[0].test_client()
    headers = {"Authorization": "Bearer " + token}
    assert client.get(f"/api/v1/bars/{env[3].id}/inventories", headers=headers).status_code in (401, 403, 404)
    assert not permissions.evaluate(keeper, "orders.read", env[2].id).allowed


def test_owner_can_delegate_specific_actions_to_manager_only(env):
    admin, assignment = _employee(env, "BAR_ADMIN", "manager")
    assert not permissions.evaluate(admin, "warehouse.main.read", env[2].id).allowed
    assert not permissions.evaluate(admin, "warehouse.transfers.dispatch", env[2].id).allowed
    assert permissions.evaluate(admin, "orders.read", env[2].id).allowed
    first = set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.main.read", True)
    second = set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.transfers.dispatch", True)
    db.session.commit()
    assert first.id != second.id
    assert permissions.evaluate(admin, "warehouse.main.read", env[2].id).allowed
    assert permissions.evaluate(admin, "warehouse.transfers.dispatch", env[2].id).allowed
    assert not permissions.evaluate(admin, "warehouse.main.inventory.count", env[2].id).allowed
    assert not permissions.evaluate(admin, "warehouse.discrepancies.resolve", env[2].id).allowed
    assert not permissions.evaluate(admin, "warehouse.main.read", env[3].id).allowed
    assert set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.main.read", True).id == first.id
    db.session.commit()
    assert db.session.scalar(select(func.count()).select_from(WarehouseStaffGrant)) == 2
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.transfers.dispatch", False)
    db.session.commit()
    assert not permissions.evaluate(admin, "warehouse.transfers.dispatch", env[2].id).allowed
    assert db.session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.bar_id == env[2].id, AuditLog.action == "warehouse.grant.revoke"
    )) >= 1


def test_delegation_denies_cross_bar_self_escalation_and_non_owner(env):
    admin, assignment = _employee(env, "BAR_ADMIN", "grant-denied")
    foreign_owner = User(email="warehouse-foreign-owner@example.invalid", display_name="Other", category="OWNER")
    foreign_owner.set_password("test-password")
    db.session.add(foreign_owner)
    db.session.flush()
    with pytest.raises(PermissionError):
        set_warehouse_grant(admin, env[2].id, assignment.id, "warehouse.main.read", True)
    with pytest.raises(PermissionError):
        set_warehouse_grant(foreign_owner, env[2].id, assignment.id, "warehouse.main.read", True)
    with pytest.raises(LookupError):
        set_warehouse_grant(env[1], env[2].id, 99999999, "warehouse.main.read", True)
    # PROMPT 4: only an owner may delegate dispute resolution to BAR_ADMIN.
    grant = set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.discrepancies.resolve", True)
    assert grant is not None and grant.enabled
    db.session.commit()
    assert permissions.evaluate(admin, "warehouse.discrepancies.resolve", env[2].id).allowed
    with pytest.raises(ValueError):
        set_warehouse_grant(env[1], env[2].id, assignment.id, "payments.record", True)


def test_keeper_special_purchasing_does_not_grant_legacy_supplier_access(env):
    keeper, assignment = _employee(env, "STOREKEEPER", "purchase")
    with pytest.raises(ValueError):
        set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.transfers.receive", True)
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.purchases.manage", True)
    db.session.commit()
    assert permissions.evaluate(keeper, "warehouse.purchases.manage", env[2].id).allowed
    assert not permissions.evaluate(keeper, "purchases.manage", env[2].id).allowed
    assert not permissions.evaluate(keeper, "purchases.read", env[2].id).allowed


def test_role_change_immediately_revokes_grants_and_old_jwt(env):
    admin, assignment = _employee(env, "BAR_ADMIN", "role-change")
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.main.read", True)
    db.session.commit()
    token, _ = issue(admin, env[2].id)
    db.session.commit()
    prior_version = admin.credentials_version
    assign_staff(env[1], env[2].id, admin.id, "STOREKEEPER")
    db.session.commit()
    assert admin.credentials_version == prior_version + 1
    assert not db.session.scalar(select(WarehouseStaffGrant.enabled).where(
        WarehouseStaffGrant.staff_assignment_id == assignment.id
    ))
    assert env[0].test_client().get(
        f"/api/v1/bars/{env[2].id}/inventories",
        headers={"Authorization": "Bearer " + token},
    ).status_code == 401
    assign_staff(env[1], env[2].id, admin.id, "BAR_ADMIN")
    db.session.commit()
    assert not permissions.evaluate(admin, "warehouse.main.read", env[2].id).allowed
    end_staff_assignment(env[1], env[2].id, assignment.id)
    db.session.commit()


def test_cashier_request_and_receipt_depend_on_active_shift(env):
    cashier, assignment = _employee(env, "CASHIER", "cashier")
    assert not permissions.evaluate(cashier, "warehouse.requests.create", env[2].id).allowed
    assert not permissions.evaluate(cashier, "warehouse.transfers.receive", env[2].id).allowed
    from app.shift_service import start_shift
    start_shift(env[1], env[2].id, assignment.id)
    db.session.commit()
    assert permissions.evaluate(cashier, "warehouse.requests.create", env[2].id).allowed
    assert permissions.evaluate(cashier, "warehouse.transfers.receive", env[2].id).allowed
    assert not permissions.evaluate(cashier, "warehouse.transfers.dispatch", env[2].id).allowed


def test_request_notification_targets_only_authorized_staff_in_bar(env):
    keeper, _ = _employee(env, "STOREKEEPER", "notify-keeper")
    admin, assignment = _employee(env, "BAR_ADMIN", "notify-manager")
    first = notify_warehouse_request(env[1], env[2].id, "REQ-001", "Besoin de boissons")
    db.session.commit()
    assert {n.user_id for n in first} == {env[1].id, keeper.id}
    assert not permissions.evaluate(admin, "warehouse.notifications.read", env[2].id).allowed
    set_warehouse_grant(env[1], env[2].id, assignment.id, "warehouse.notifications.read", True)
    second = notify_warehouse_request(env[1], env[2].id, "REQ-002", "Besoin de verres")
    db.session.commit()
    assert {n.user_id for n in second} == {env[1].id, keeper.id, admin.id}
    assert all(n.bar_id == env[2].id and n.kind == "WAREHOUSE_REQUEST" for n in second)
    assert db.session.scalar(select(func.count()).select_from(UserNotification).where(
        UserNotification.bar_id == env[3].id,
        UserNotification.kind == "WAREHOUSE_REQUEST",
    )) == 0


def test_owner_web_grant_requires_csrf_and_tenant_bound(env):
    admin, assignment = _employee(env, "BAR_ADMIN", "ui-grant")
    owner_client = env[0].test_client()
    assert _login(owner_client, env[1].email).status_code == 302
    grant_page = owner_client.get(f"/bars/{env[2].id}/warehouse/permissions")
    assert grant_page.status_code == 200
    csrf = _csrf(grant_page)
    response = owner_client.post(
        f"/bars/{env[2].id}/warehouse/permissions",
        data={
            "csrf_token": csrf, "assignment_id": assignment.id,
            "action": "warehouse.main.read", "enabled": "1",
        },
    )
    assert response.status_code == 302
    assert permissions.evaluate(admin, "warehouse.main.read", env[2].id).allowed
    assert owner_client.post(
        f"/bars/{env[2].id}/warehouse/permissions",
        data={"assignment_id": assignment.id, "action": "warehouse.transfers.dispatch", "enabled": "1"},
    ).status_code == 400
    assert not permissions.evaluate(admin, "warehouse.transfers.dispatch", env[2].id).allowed


def test_keeper_dispatch_is_audited_and_cannot_cross_tenant(env):
    """Simulate LIVE only in this disposable test DB, not in production."""
    from app.warehouse_service import warehouse_service

    keeper, _ = _employee(env, "STOREKEEPER", "dispatch-audit")
    staged = warehouse_service.stage_allocation(
        env[1], env[2].id,
        {env[4].id: {"MAIN": "8", "CASHIER": "2"}},
    )
    db.session.commit()
    staged.status = "LIVE"
    db.session.commit()

    with pytest.raises(PermissionError):
        warehouse_service.dispatch(keeper, env[3].id, "TR-CROSS-BAR", {env[4].id: 1})
    db.session.rollback()

    transfer = warehouse_service.dispatch(
        keeper, env[2].id, "TR-AUDITED-001", {env[4].id: "3"},
    )
    db.session.commit()

    events = list(db.session.scalars(
        select(AuditLog).where(
            AuditLog.bar_id == env[2].id,
            AuditLog.action == "warehouse.transfer.dispatch",
            AuditLog.target_table == "warehouse_transfers",
            AuditLog.target_id == transfer.id,
            AuditLog.actor_id == keeper.id,
        )
    ))
    assert len(events) == 1
    assert warehouse_service.totals(env[2].id)[env[4].id] == {
        "MAIN": 5, "CASHIER": 2, "TRANSIT": 3, "TOTAL": 10,
    }
    assert db.session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.bar_id == env[3].id,
            AuditLog.action == "warehouse.transfer.dispatch",
        )
    ) == 0
