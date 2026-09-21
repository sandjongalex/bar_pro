import re

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.order_suborder_models import OrderSuborder
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


def test_server_and_cashier_have_optimized_suborder_workflows(env):
    app, _, bar, _, product, server, _, _ = env

    cashier = User(
        email="suborder-ui-cashier@example.invalid",
        display_name="Suborder UI Cashier",
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
        "SUBORDER-UI",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, parent.id)

    waiting_parent = order_service.create(
        server,
        bar.id,
        "SUBORDER-UI-WAITING",
        [{"product_id": product.id, "quantity": 1}],
    )
    db.session.flush()

    cash_service.open(cashier, bar.id, "SUBORDER-UI-CASH", 0)
    db.session.commit()

    builder_url = f"/bars/{bar.id}/orders/{parent.id}/suborders/new"
    waiting_builder_url = f"/bars/{bar.id}/orders/{waiting_parent.id}/suborders/new"
    detail_url = f"/bars/{bar.id}/orders/{parent.id}/detail"
    waiting_detail_url = f"/bars/{bar.id}/orders/{waiting_parent.id}/detail"

    server_client = app.test_client()
    assert _login(server_client, server.email).status_code == 302

    server_workspace = server_client.get(f"/bars/{bar.id}/orders/new")
    assert server_workspace.status_code == 200
    assert "Ajouter une sous-commande" in server_workspace.text
    assert "Ajouter des produits" in server_workspace.text
    assert "Détails" in server_workspace.text
    # Direct actions are prepared on both the delivered order and the order
    # that still waits for cashier delivery.
    assert builder_url in server_workspace.text
    assert waiting_builder_url in server_workspace.text
    assert detail_url in server_workspace.text
    assert waiting_detail_url in server_workspace.text

    initial_detail = server_client.get(detail_url)
    assert initial_detail.status_code == 200
    assert "Détail de la commande" in initial_detail.text
    assert "Commande initiale" in initial_detail.text
    assert parent.reference in initial_detail.text
    assert product.name in initial_detail.text
    assert "Aucune sous-commande" in initial_detail.text

    server_builder = server_client.get(builder_url)
    assert server_builder.status_code == 200
    assert "La serveuse envoie, la caissière livre." in server_builder.text
    assert "Envoyer à la caisse" in server_builder.text
    assert product.name in server_builder.text

    response = server_client.post(
        builder_url,
        data={
            "csrf_token": _csrf(server_builder),
            "product_id": str(product.id),
            "quantity": "1",
            "note": "Ajout depuis l'interface serveuse",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    suborder = db.session.scalar(
        select(OrderSuborder).where(
            OrderSuborder.bar_id == bar.id,
            OrderSuborder.order_id == parent.id,
            OrderSuborder.sequence_no == 1,
        )
    )
    assert suborder is not None
    assert suborder.status == "VALIDATED"
    assert suborder.delivery_status == "PENDING"

    detail_with_suborder = server_client.get(detail_url)
    assert detail_with_suborder.status_code == 200
    assert "Sous-commande 1" in detail_with_suborder.text
    assert "Validée" in detail_with_suborder.text
    assert "À livrer" in detail_with_suborder.text
    assert "Ajout depuis l&#39;interface serveuse" in detail_with_suborder.text

    cashier_client = app.test_client()
    assert _login(cashier_client, cashier.email).status_code == 302

    cashier_workspace = cashier_client.get(f"/bars/{bar.id}/cashier/workspace?order_id={parent.id}")
    assert cashier_workspace.status_code == 200
    assert f"/bars/{bar.id}/cashier/suborders" in cashier_workspace.text
    assert "Sous-commandes" in cashier_workspace.text
    assert "cashier-order-direct-suborder" in cashier_workspace.text
    assert "cashier-order-direct-detail" in cashier_workspace.text
    # The cashier gets direct detail links for every active order, plus the
    # sub-order shortcut when the order belongs to a server.
    assert builder_url in cashier_workspace.text
    assert waiting_builder_url in cashier_workspace.text
    assert detail_url in cashier_workspace.text
    assert waiting_detail_url in cashier_workspace.text

    cashier_detail = cashier_client.get(detail_url)
    assert cashier_detail.status_code == 200
    assert parent.reference in cashier_detail.text
    assert "Sous-commande 1" in cashier_detail.text

    queue = cashier_client.get(f"/bars/{bar.id}/cashier/suborders")
    assert queue.status_code == 200
    assert "À livrer maintenant" in queue.text
    assert "Sous-commande 1" in queue.text
    assert "Livrer cette sous-commande" in queue.text
    assert builder_url in queue.text

    delivery = cashier_client.post(
        f"/bars/{bar.id}/cashier/suborders",
        data={
            "csrf_token": _csrf(queue),
            "order_id": str(parent.id),
            "suborder_id": str(suborder.id),
        },
        follow_redirects=False,
    )
    assert delivery.status_code == 302
    db.session.refresh(suborder)
    assert suborder.delivery_status == "DELIVERED"
    assert suborder.delivered_by_id == cashier.id

    cashier_builder = cashier_client.get(builder_url)
    assert cashier_builder.status_code == 200
    assert "La caissière sert immédiatement, la serveuse valide ensuite." in cashier_builder.text
    assert "Servir cet ajout" in cashier_builder.text