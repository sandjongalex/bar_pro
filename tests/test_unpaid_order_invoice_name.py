from app.extensions import db
from app.order_services import order_service
from app.shift_service import start_shift
from test_unpaid_orders_web import _active_assignment, _employee, _login
from test_workflows import env


def _prepare_shifts(owner, bar, server, cashier, cashier_assignment):
    server_assignment = _active_assignment(bar, server, "SERVER")
    assert server_assignment is not None
    start_shift(owner, bar.id, cashier_assignment.id)
    start_shift(cashier, bar.id, server_assignment.id)
    db.session.commit()
    return server_assignment


def test_server_unpaid_orders_show_given_invoice_name(env):
    app, owner, bar, _, product, server, _, _ = env
    cashier, cashier_assignment = _employee(
        bar,
        "CASHIER",
        "invoice-name-server-test-cashier@example.invalid",
        "Alex",
    )
    _prepare_shifts(owner, bar, server, cashier, cashier_assignment)

    server_order = order_service.create(
        server,
        bar.id,
        "NAME-SERVER",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Zaza",
    )
    order_service.confirm(owner, bar.id, server_order.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert page.status_code == 200
    assert "Zaza" in page.text
    assert "Facture / repère" in page.text

    payload = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    assert len(payload["orders"]) == 1
    assert payload["orders"][0]["invoice_name"] == "Zaza"
    assert payload["orders"][0]["display_name"] == "Zaza"


def test_cashier_unpaid_orders_show_server_and_cashier_invoice_names(env):
    app, owner, bar, _, product, server, _, _ = env
    cashier, cashier_assignment = _employee(
        bar,
        "CASHIER",
        "invoice-name-cashier@example.invalid",
        "Alex",
    )
    _prepare_shifts(owner, bar, server, cashier, cashier_assignment)

    server_order = order_service.create(
        server,
        bar.id,
        "NAME-SERVER",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Zaza",
    )
    cashier_order = order_service.create(
        cashier,
        bar.id,
        "NAME-CASHIER",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Client Baron",
    )
    order_service.confirm(owner, bar.id, server_order.id)
    order_service.confirm(owner, bar.id, cashier_order.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert page.status_code == 200
    assert "Zaza" in page.text
    assert "Client Baron" in page.text
    assert "Facture / repère" in page.text

    payload = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    names_by_reference = {
        item["reference"]: item["display_name"]
        for item in payload["orders"]
    }
    assert names_by_reference["NAME-SERVER"] == "Zaza"
    assert names_by_reference["NAME-CASHIER"] == "Client Baron"
