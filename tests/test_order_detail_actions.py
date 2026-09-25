import re
from decimal import Decimal

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import OrderReturnLine, Product, StaffAssignment, StockBalance, User, utcnow
from app.order_line_views import effective_lines_by_order
from app.order_services import order_service
from app.shift_service import start_shift
from app.stock_service import stock_service
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _cashier(owner, bar):
    user = User(
        email="detail-actions-cashier@example.invalid",
        display_name="Caisse Détail",
        category="EMPLOYEE",
        is_active=True,
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=user.id,
        role="CASHIER",
        started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    start_shift(owner, bar.id, assignment.id)
    db.session.commit()
    cash_service.open(user, bar.id, "DETAIL-ACTIONS-CASH", 2000)
    db.session.commit()
    return user


def _second_product(owner, bar, source_product):
    product = Product(
        bar_id=bar.id,
        category_id=source_product.category_id,
        name="Castel Detail",
        sku="CASTEL-DETAIL-ACTIONS",
        base_unit="bottle",
        sale_price=150,
        valuation_unit_cost=60,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()
    stock_service.move(owner, bar.id, product.id, "INITIAL", 5, "Stock test détail")
    db.session.commit()
    return product


def _stock(bar_id, product_id):
    return Decimal(
        db.session.scalar(
            select(StockBalance.quantity).where(
                StockBalance.bar_id == bar_id,
                StockBalance.product_id == product_id,
            )
        )
        or 0
    )


def test_cashier_detail_exposes_actions_and_can_remove_delivered_beer(env):
    app, owner, bar, _, product, _, _, _ = env
    cashier = _cashier(owner, bar)
    second = _second_product(owner, bar, product)
    order = order_service.create(
        owner,
        bar.id,
        "DETAIL-ACTIONS",
        [
            {"product_id": product.id, "quantity": 2},
            {"product_id": second.id, "quantity": 1},
        ],
        invoice_name="Table test détail",
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    detail_url = f"/bars/{bar.id}/orders/{order.id}/detail"
    page = client.get(detail_url)
    assert page.status_code == 200
    assert 'id="paymentPanel"' in page.text
    assert "order_edit_ui.js" in page.text
    assert "Livrer la commande" in page.text
    assert "Payer / encaisser" not in page.text

    delivered = client.post(
        f"/bars/{bar.id}/cashier/workspace",
        data={
            "csrf_token": _csrf(page),
            "action": "deliver",
            "order_id": str(order.id),
        },
        follow_redirects=False,
    )
    assert delivered.status_code == 302
    db.session.refresh(order)
    assert order.status == "CONFIRMED"
    assert _stock(bar.id, product.id) == Decimal("8")
    assert _stock(bar.id, second.id) == Decimal("4")

    page = client.get(detail_url)
    assert page.status_code == 200
    assert "Payer / encaisser" in page.text
    assert "Imprimer" in page.text
    assert "Commande déjà livrée" in page.text
    assert "supprimer une bière" in page.text
    assert 'id="paymentPanel"' in page.text

    state = client.get(f"/bars/{bar.id}/order-edits/{order.id}")
    assert state.status_code == 200
    payload = state.get_json()
    assert payload["order"]["editable"] is True
    assert payload["order"]["delivered"] is True

    edited = client.post(
        f"/bars/{bar.id}/order-edits/{order.id}",
        data={
            "csrf_token": _csrf(page),
            "order_revision": payload["order"]["revision"],
            "product_id": str(product.id),
            "quantity": "2",
            "reason": "Le client retire une Castel",
        },
        follow_redirects=False,
    )
    assert edited.status_code == 302
    assert "cashier/workspace" in edited.headers["Location"]

    effective = effective_lines_by_order(bar.id, [order.id])[order.id]
    assert len(effective) == 1
    assert effective[0]["product_id"] == product.id
    assert Decimal(effective[0]["quantity"]) == Decimal("2")
    assert _stock(bar.id, second.id) == Decimal("5")
    assert db.session.scalar(
        select(OrderReturnLine).where(
            OrderReturnLine.bar_id == bar.id,
            OrderReturnLine.order_id == order.id,
            OrderReturnLine.product_id == second.id,
        )
    ) is not None


def test_paid_order_detail_hides_editor_and_offers_return_path(env):
    # Static state rule regression: the template must never expose the inline
    # editor container for a fully paid order. Payment/refund math is covered by
    # the dedicated payment and return suites.
    template = open("app/templates/order_detail.html", encoding="utf-8").read()
    assert "order.payment_status != 'PAID'" in template
    assert "Retour / remboursement" in template
    assert "commande totalement payée ne se modifie plus directement" in template
