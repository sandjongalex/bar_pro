"""Executable SaaS foundations: roles, tenant scopes, suspensions and tokens."""
from decimal import Decimal
import pytest
from sqlalchemy import select, func

from app.auth import issue
from app.bar_reset_service import reset_bar
from app.bar_services import assign_staff
from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import AuditLog, Bar, Order, StockBalance, User, utcnow
from app.order_services import order_service
from app.permissions import permissions
from app.saas_governance import (
    bar_governance, change_bar_governance, change_owner_governance,
    owner_enabled, suspension_level,
)
from app.saas_governance_models import BarGovernance, OwnerGovernance
from test_employee_tenant_auth import _login, _csrf
from test_workflows import env


def platform_user():
    actor = User(email="saas-platform@example.invalid", display_name="Platform",
                 category="SUPER_ADMIN")
    actor.set_password("test-password")
    db.session.add(actor)
    db.session.flush()
    return actor


def bearer(user, bar_id):
    jwt, _ = issue(user, bar_id)
    db.session.commit()
    return {"Authorization": "Bearer " + jwt}


def test_five_roles_are_existing_roles_and_multi_bar_access_is_scoped(env):
    _, owner, bar, foreign, _, server, _, _ = env
    assert permissions.evaluate(owner, "bars.read", bar.id).allowed
    assert not permissions.evaluate(owner, "bars.read", foreign.id).allowed
    second = Bar(owner_id=owner.id, name="Second", timezone="Africa/Douala", currency="XAF")
    db.session.add(second)
    db.session.flush()
    assert permissions.evaluate(owner, "bars.read", second.id).allowed
    assert not permissions.evaluate(server, "bars.read", second.id).allowed
    admin = platform_user()
    assert permissions.evaluate(admin, "bars.read", bar.id).allowed
    for role in ("BAR_ADMIN", "CASHIER", "SERVER"):
        u = User(email="saas-"+role.lower()+"@example.invalid", display_name=role, category="EMPLOYEE")
        u.set_password("test-password")
        db.session.add(u)
        db.session.flush()
        assignment = assign_staff(owner, bar.id, u.id, role)
        assert assignment.role == role
        assert permissions.evaluate(u, "bars.read", bar.id).allowed
        assert not permissions.evaluate(u, "bars.read", foreign.id).allowed


def test_legacy_default_has_no_restrictive_governance(env):
    _, owner, bar, _, _, _, _, _ = env
    assert bar_governance(bar.id) is None
    assert suspension_level(bar.id) == 0
    assert permissions.evaluate(owner, "orders.create", bar.id).allowed


def test_superadmin_read_only_for_commerce_even_before_suspension(env):
    _, _, bar, _, product, _, _, _ = env
    admin = platform_user()
    for action in ("orders.create", "orders.edit", "orders.deliver", "payments.record",
                   "refunds.record", "cash.operate", "inventory.adjust", "bars.reset",
                   "customer_debt.manage", "purchases.manage"):
        assert not permissions.evaluate(admin, action, bar.id).allowed, action
    with pytest.raises(PermissionError):
        order_service.create(admin, bar.id, "NO-COMMERCE",
                             [{"product_id": product.id, "quantity": 1}])
    with pytest.raises(PermissionError):
        reset_bar(admin, bar.id, "REINITIALISER", "test-password")
    assert db.session.scalar(select(func.count()).select_from(Order).where(Order.bar_id == bar.id)) == 0


def test_suspension_level_one_preserves_read_but_denies_web_and_api_writes(env):
    app, owner, bar, _, _, _, headers, _ = env
    platform = platform_user()
    change_bar_governance(platform, bar.id, status="SUSPENDED",
                          suspension_level=1, reason="Audit administratif")
    db.session.commit()
    assert permissions.evaluate(owner, "customers.read", bar.id).allowed
    assert not permissions.evaluate(owner, "orders.create", bar.id).allowed
    assert permissions.evaluate(platform, "bars.governance.manage", bar.id).allowed
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/customers")
    assert page.status_code == 200
    assert client.post(f"/bars/{bar.id}/customers", data={
        "action": "manual_debt", "csrf_token": _csrf(page),
    }).status_code == 403
    assert client.get(f"/api/v1/bars/{bar.id}/governance", headers=headers).status_code == 200
    blocked = client.post(f"/api/v1/bars/{bar.id}/orders", headers=headers, json={})
    assert blocked.status_code == 403
    assert blocked.get_json()["error"]["code"] == "BAR_ADMIN_READ_ONLY"


def test_level_two_blocks_all_commercial_views_and_allows_support(env):
    app, owner, bar, _, _, server, headers, _ = env
    platform = platform_user()
    change_bar_governance(platform, bar.id, status="SUSPENDED",
                          suspension_level=2, reason="Incident de sécurité")
    db.session.commit()
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302
    assert client.get(f"/bars/{bar.id}/support").status_code == 200
    assert client.get(f"/bars/{bar.id}/customers").status_code in (403, 404)
    assert client.get(f"/dashboard?bar_id={bar.id}").status_code == 200
    assert client.get(f"/api/v1/bars/{bar.id}/governance", headers=headers).status_code == 200
    assert client.get("/api/v1/bars", headers=headers).status_code == 403
    assert not permissions.evaluate(server, "orders.read", bar.id).allowed
    assert permissions.evaluate(server, "bars.support.read", bar.id).allowed
    change_bar_governance(platform, bar.id, status="APPROVED",
                          suspension_level=0, reason="Incident clos")
    db.session.commit()
    assert permissions.evaluate(owner, "orders.create", bar.id).allowed


def test_governance_api_requires_platform_role_and_matching_jwt_tenant(env):
    app, owner, bar, foreign, _, _, headers, _ = env
    platform = platform_user()
    platform_headers = bearer(platform, bar.id)
    url = f"/api/v1/bars/{bar.id}/governance"
    client = app.test_client()
    mutation = {"status": "SUSPENDED", "suspension_level": 1, "reason": "Contrôle plateforme"}
    assert client.patch(url, headers=headers, json=mutation).status_code == 403
    assert client.patch(f"/api/v1/bars/{foreign.id}/governance",
                        headers=platform_headers, json=mutation).status_code == 404
    assert client.patch(url, headers=platform_headers, json=mutation).status_code == 200
    assert db.session.get(BarGovernance, bar.id).suspension_level == 1
    assert db.session.get(BarGovernance, foreign.id) is None
    assert client.patch(url, json=mutation).status_code == 401
    assert client.post(f"/api/v1/bars/{bar.id}/orders", headers=platform_headers, json={}).status_code == 403


def test_owner_suspension_revokes_credentials_but_does_not_suspend_bars_or_staff(env):
    app, owner, bar, _, _, server, headers, _ = env
    platform = platform_user()
    change_owner_governance(platform, owner.id, status="SUSPENDED", reason="Compte compromis")
    db.session.commit()
    assert not owner_enabled(owner)
    assert not permissions.evaluate(owner, "bars.read", bar.id).allowed
    assert permissions.evaluate(server, "bars.support.read", bar.id).allowed
    assert bar.status == "ACTIVE"
    assert bar_governance(bar.id) is None
    assert db.session.get(OwnerGovernance, owner.id).status == "SUSPENDED"
    c = app.test_client()
    assert c.post("/api/v1/auth/tokens", json={
        "email": owner.email, "password": "test-password", "bar_id": bar.id
    }).status_code == 401
    assert c.get("/api/v1/bars", headers=headers).status_code == 401
    change_owner_governance(platform, owner.id, status="APPROVED", reason="Identité rétablie")
    db.session.commit()
    assert permissions.evaluate(owner, "bars.read", bar.id).allowed
    assert c.get("/api/v1/bars", headers=headers).status_code == 401


def test_staff_role_change_invalidates_old_cookie_and_jwt(env):
    app, owner, bar, _, _, server, _, _ = env
    old_headers = bearer(server, bar.id)
    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    version = server.credentials_version
    assign_staff(owner, bar.id, server.id, "CASHIER")
    db.session.commit()
    assert server.credentials_version == version + 1
    assert client.get("/api/v1/bars", headers=old_headers).status_code == 401
    assert client.get(f"/bars/{bar.id}/orders/new").status_code in (302, 401, 403)


def test_bar_status_independent_of_subscription_and_owner_governance(env):
    _, owner, bar, _, _, _, _, _ = env
    platform = platform_user()
    change_bar_governance(platform, bar.id, status="PENDING",
                          suspension_level=0, reason="Pièces demandées")
    db.session.commit()
    assert bar.status == "ACTIVE"
    assert db.session.get(OwnerGovernance, owner.id) is None
    assert db.session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "bars.governance.change", AuditLog.bar_id == bar.id)) == 1
