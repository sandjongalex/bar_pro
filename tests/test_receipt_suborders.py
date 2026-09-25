import base64
import html
import re

from sqlalchemy import select

from app.extensions import db
from app.models import Product, StaffAssignment, utcnow
from app.order_services import order_service
from app.order_suborder_service import order_suborder_service
from app.shift_models import EmployeeShift
from app.stock_service import stock_service
from test_cashier_complete_flow import _login
from test_workflows import env


def _rawbt_text(page_text: str) -> str:
    link = html.unescape(
        re.search(r'data-rawbt-intent="([^"]+)"', page_text).group(1)
    )
    encoded, _ = link.removeprefix("intent:base64,").split("#Intent;")
    payload = base64.b64decode(encoded)
    return payload[8:].decode("ascii")


def _put_server_on_duty(owner, bar, server):
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server.id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert assignment is not None
    db.session.add(
        EmployeeShift(
            bar_id=bar.id,
            staff_assignment_id=assignment.id,
            role_snapshot="SERVER",
            status="OPEN",
            started_at=utcnow(),
            started_by_id=owner.id,
        )
    )
    db.session.flush()
    return assignment


def _server_order(owner, bar, server, product, reference="RECEIPT-SUBORDER"):
    value = order_service.create(
        server,
        bar.id,
        reference,
        [{"product_id": product.id, "quantity": 2}],
    )
    # The serveuse creates the order; delivery of the initial round remains a
    # cashier/manager action under the current role rules.
    order_service.confirm(owner, bar.id, value.id)
    db.session.flush()
    return value


def test_printed_receipt_refreshes_with_validated_suborder(env):
    app, owner, bar, _, base_product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)

    extra = Product(
        bar_id=bar.id,
        category_id=base_product.category_id,
        name="Booster ajout facture",
        sku="BOOSTER-RECEIPT",
        base_unit="bottle",
        sale_price=150,
        valuation_unit_cost=75,
    )
    db.session.add(extra)
    db.session.flush()
    stock_service.move(owner, bar.id, extra.id, "INITIAL", 10, "Opening stock")

    value = _server_order(owner, bar, server, base_product)
    addition = order_suborder_service.create_server_addition(
        server,
        bar.id,
        value.id,
        [{"product_id": extra.id, "quantity": 1}],
        "Deuxième tournée",
    )
    db.session.commit()

    assert addition.status == "VALIDATED"
    assert value.total_amount == 350

    order_suborder_service.deliver_by_cashier(
        owner,
        bar.id,
        value.id,
        addition.id,
    )
    db.session.commit()

    client = app.test_client()
    _login(client, owner.email)
    page = client.get(f"/bars/{bar.id}/checkout/orders/{value.id}/receipt")

    assert page.status_code == 200
    assert "Booster ajout facture" in page.text
    assert "350" in page.text

    ticket = _rawbt_text(page.text)
    assert "Booster ajout facture" in ticket
    assert "Total commande: 350 FCFA" in ticket


def test_receipt_does_not_show_unvalidated_suborder(env):
    app, owner, bar, _, base_product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)
    value = _server_order(
        owner,
        bar,
        server,
        base_product,
        reference="RECEIPT-PENDING-SUBORDER",
    )

    # A cashier-created addition is intentionally not part of the invoice until
    # the assigned server validates it.  This guards against printing products
    # that the invoice total does not yet include.
    addition = order_suborder_service.create_cashier_addition(
        owner,
        bar.id,
        value.id,
        [{"product_id": base_product.id, "quantity": 1}],
        "En attente validation serveuse",
    )
    db.session.commit()
    assert addition.status == "PENDING_VALIDATION"
    assert value.total_amount == 200

    client = app.test_client()
    _login(client, owner.email)
    page = client.get(f"/bars/{bar.id}/checkout/orders/{value.id}/receipt")

    assert page.status_code == 200
    ticket = _rawbt_text(page.text)
    assert ticket.count("Water") == 1
    assert "Total commande: 200 FCFA" in ticket
