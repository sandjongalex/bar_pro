import re

from app.cash_services import cash_service
from app.extensions import db
from app.models import BarTable, Order, StaffAssignment, User, utcnow
from app.order_services import order_service
from app.order_suborder_service import order_suborder_service
from app.payment_services import payment_service
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


def test_table_name_is_primary_order_label_across_cashier_workflows(env):
    app, _, bar, _, product, server, _, _ = env

    cashier = User(
        email="human-label-cashier@example.invalid",
        display_name="Human Label Cashier",
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
    table = BarTable(bar_id=bar.id, label="LÉO", capacity=4)
    db.session.add(table)
    db.session.flush()

    active_order = order_service.create(
        server,
        bar.id,
        "SYS-HUMAN-ACTIVE",
        [{"product_id": product.id, "quantity": 1}],
        table_id=table.id,
    )
    db.session.flush()
    order_suborder_service.create_server_addition(
        server,
        bar.id,
        active_order.id,
        [{"product_id": product.id, "quantity": 1}],
        "Tour supplémentaire",
    )

    history_order = order_service.create(
        server,
        bar.id,
        "SYS-HISTORY",
        [{"product_id": product.id, "quantity": 1}],
        table_id=table.id,
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, history_order.id)
    session = cash_service.open(cashier, bar.id, "HUMAN-LABEL-CASH", 0)
    db.session.flush()
    payment_service.record(
        cashier,
        bar.id,
        history_order.id,
        "HUMAN-LABEL-PAY",
        "CASH",
        history_order.total_amount,
        history_order.total_amount,
        0,
        cash_session_id=session.id,
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    workspace = client.get(
        f"/bars/{bar.id}/cashier/workspace?order_id={active_order.id}"
    )
    assert workspace.status_code == 200
    assert "cashier-human-order-title" in workspace.text
    assert "Réf. système :" in workspace.text
    assert "LÉO" in workspace.text

    detail = client.get(
        f"/bars/{bar.id}/orders/{active_order.id}/detail"
    )
    assert detail.status_code == 200
    assert "<h1>LÉO</h1>" in detail.text
    assert "Réf. système : SYS-HUMAN-ACTIVE" in detail.text

    suborders = client.get(f"/bars/{bar.id}/cashier/suborders")
    assert suborders.status_code == 200
    assert "LÉO · Sous-commande 1" in suborders.text
    assert "Réf. système : SYS-HUMAN-ACTIVE" in suborders.text

    history = client.get(f"/bars/{bar.id}/cashier-history")
    assert history.status_code == 200
    assert "<strong>LÉO</strong>" in history.text
    assert "Réf. système : SYS-HISTORY" in history.text

    # Switch roles explicitly in the same browser session. The login route
    # redirects authenticated users, so a second login must be preceded by a
    # real logout instead of relying on a fresh test client to clear context.
    logout = client.post(
        "/logout",
        data={"csrf_token": _csrf(history)},
        follow_redirects=False,
    )
    assert logout.status_code == 302

    assert _login(client, server.email).status_code == 302
    server_workspace = client.get(f"/bars/{bar.id}/orders/new")
    assert server_workspace.status_code == 200
    assert "LÉO" in server_workspace.text
    assert "Réf. système : SYS-HUMAN-ACTIVE" in server_workspace.text


def test_server_can_enter_a_dedicated_invoice_name(env):
    app, _, bar, _, product, server, _, _ = env
    table = BarTable(bar_id=bar.id, label="VIP 2", capacity=4)
    db.session.add(table)
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    assert 'name="invoice_name"' in page.text
    assert "Nom de la facture" in page.text
    assert "La caisse verra" in page.text
    assert "serverInvoicePreview" in page.text
    assert "si vous laissez ce champ vide" in page.text.lower()

    created = client.post(
        f"/bars/{bar.id}/orders/new",
        data={
            "csrf_token": _csrf(page),
            "action": "create",
            "reference": "SYS-INVOICE-NAME",
            "invoice_name": "VIP JEAN",
            "table_id": str(table.id),
            "product_id": str(product.id),
            "quantity": "1",
            "notes": "Client près de la fenêtre",
        },
        follow_redirects=False,
    )
    assert created.status_code == 302

    order = db.session.query(Order).filter_by(
        bar_id=bar.id,
        reference="SYS-INVOICE-NAME",
    ).one()
    assert order.customer_name_snapshot == "VIP JEAN"
    assert order.table_label_snapshot == "VIP 2"

    refreshed = client.get(f"/bars/{bar.id}/orders/new")
    assert refreshed.status_code == 200
    assert "VIP JEAN" in refreshed.text
    assert "Table : VIP 2" in refreshed.text
    assert "Réf. système : SYS-INVOICE-NAME" in refreshed.text
