import re
from decimal import Decimal

from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.order_suborder_service import order_suborder_service
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    assert page.status_code == 200
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def test_server_sees_and_validates_cashier_suborder_from_workspace(env):
    app, _, bar, _, product, server, _, _ = env

    cashier = User(
        email="suborder-web-cashier@example.invalid",
        display_name="Suborder Web Cashier",
        category="EMPLOYEE",
    )
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=cashier.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.flush()

    parent = order_service.create(
        server,
        bar.id,
        "SUBORDER-WEB",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, parent.id)
    db.session.commit()

    suborder = order_suborder_service.create_cashier_addition(
        cashier,
        bar.id,
        parent.id,
        [{"product_id": product.id, "quantity": 2}],
        note="Ajout servi directement par la caisse",
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    assert "Ajouts de la caisse" in page.text
    assert "Sous-commande 1" in page.text
    assert product.name in page.text
    assert "Valider cet ajout" in page.text
    assert f'data-pending-suborder="{suborder.id}"' in page.text

    response = client.post(
        f"/bars/{bar.id}/orders/new",
        data={
            "csrf_token": _csrf(page),
            "action": "validate_suborder",
            "order_id": str(parent.id),
            "suborder_id": str(suborder.id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    db.session.refresh(suborder)
    db.session.refresh(parent)
    assert suborder.status == "VALIDATED"
    assert suborder.validated_by_id == server.id
    assert parent.total_amount == Decimal("400")

    refreshed = client.get(f"/bars/{bar.id}/orders/new")
    assert refreshed.status_code == 200
    assert f'data-pending-suborder="{suborder.id}"' not in refreshed.text
