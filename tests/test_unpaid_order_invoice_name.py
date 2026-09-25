from app.extensions import db
from app.order_services import order_service
from app.shift_service import start_shift
from test_unpaid_orders_web import _active_assignment, _employee, _login
from test_workflows import env


def test_unpaid_orders_show_invoice_name_for_server_and_cashier(env):
    app, owner, bar, _, product, server, _, _ = env
    cashier, cashier_assignment = _employee(
        bar,
        "CASHIER",
        "invoice-name-cashier@example.invalid",
        "Alex",
    )
    server_assignment = _active_assignment(bar, server, "SERVER")
    assert server_assignment is not None

    start_shift(owner, bar.id, cashier_assignment.id)
    start_shift(cashier, bar.id, server_assignment.id)
    db.session.commit()

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

    server_client = app.test_client()
    assert _login(server_client, server.email).status_code == 302

    server_page = server_client.get(f"/bars/{bar.id}/unpaid-orders")
    assert server_page.status_code == 200
    assert "Zaza" in server_page.text
    assert "Facture / repère" in server_page.text
    assert "Client Baron" not in server_page.text

    server_payload = server_client.get(
        f"/bars/{bar.id}/unpaid-orders/data"
    ).get_json()
    assert len(server_payload["orders"]) == 1
    assert server_payload["orders"][0]["invoice_name"] == "Zaza"
    assert server_payload["orders"][0]["display_name"] == "Zaza"

    cashier_client = app.test_client()
    assert _login(cashier_client, cashier.email).status_code == 302

    cashier_page = cashier_client.get(f"/bars/{bar.id}/unpaid-orders")
    assert cashier_page.status_code == 200
    assert "Zaza" in cashier_page.text
    assert "Client Baron" in cashier_page.text

    cashier_payload = cashier_client.get(
        f"/bars/{bar.id}/unpaid-orders/data"
    ).get_json()
    names_by_reference = {
        item["reference"]: item["display_name"]
        for item in cashier_payload["orders"]
    }
    assert names_by_reference["NAME-SERVER"] == "Zaza"
    assert names_by_reference["NAME-CASHIER"] == "Client Baron"
