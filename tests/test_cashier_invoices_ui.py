import re

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.shift_service import start_shift
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


def test_cashier_invoices_page_filters_delivery_and_personnel(env):
    app, owner, bar, _, product, server, _, _ = env

    cashier = User(
        email="invoice-ui-cashier@example.invalid",
        display_name="Alex Caisse",
        category="EMPLOYEE",
        is_active=True,
    )
    cashier.set_password("test-password")
    other_cashier = User(
        email="invoice-ui-other-cashier@example.invalid",
        display_name="Ashley Caisse",
        category="EMPLOYEE",
        is_active=True,
    )
    other_cashier.set_password("test-password")
    db.session.add_all([cashier, other_cashier])
    db.session.flush()

    cashier_assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=cashier.id,
        role="CASHIER",
        started_at=utcnow(),
    )
    other_cashier_assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=other_cashier.id,
        role="CASHIER",
        started_at=utcnow(),
    )
    db.session.add_all([cashier_assignment, other_cashier_assignment])
    db.session.flush()

    server_assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server.id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert server_assignment is not None

    # Owner starts cashiers; an on-duty cashier starts the server.
    start_shift(owner, bar.id, cashier_assignment.id)
    start_shift(owner, bar.id, other_cashier_assignment.id)
    start_shift(cashier, bar.id, server_assignment.id)
    db.session.commit()

    waiting = order_service.create(
        server,
        bar.id,
        "INV-WAITING-SERVER",
        [{"product_id": product.id, "quantity": 1}],
    )
    delivered_server = order_service.create(
        server,
        bar.id,
        "INV-DELIVERED-SERVER",
        [{"product_id": product.id, "quantity": 1}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, delivered_server.id)

    counter = order_service.create(
        cashier,
        bar.id,
        "INV-COUNTER-ALEX",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Comptoir Alex",
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, counter.id)

    other_counter = order_service.create(
        other_cashier,
        bar.id,
        "INV-COUNTER-ASHLEY",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Comptoir Ashley",
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, other_counter.id)

    cash_service.open(cashier, bar.id, "INV-UI-CASH", 0)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/cashier/invoices")
    assert page.status_code == 200
    assert "Factures" in page.text
    assert "À livrer" in page.text
    assert "Livrées" in page.text
    assert "Personnel" in page.text
    assert "Tout le personnel" in page.text
    assert "Caisse / comptoir · toutes" in page.text
    assert cashier.display_name in page.text
    assert other_cashier.display_name in page.text
    assert server.display_name in page.text
    assert waiting.reference in page.text
    assert delivered_server.reference in page.text
    assert counter.reference in page.text
    assert other_counter.reference in page.text
    assert f"/bars/{bar.id}/orders/{waiting.id}/detail" in page.text
    assert "✓ Payé" in page.text
    assert "Reste à encaisser" in page.text

    # Human-readable references: the browser promotes the table/name and keeps
    # the technical CMD reference only as secondary traceability information.
    assert "invoice-human-title" in page.text
    assert "Réf. système" in page.text
    assert "COMPTOIR" in page.text

    # The cashier page refreshes counters and invoice cards in place every ten
    # seconds instead of forcing a disruptive full-page reload.
    assert 'id="invoiceStats"' in page.text
    assert 'id="invoiceResults"' in page.text
    assert 'id="invoiceAutoRefresh"' in page.text
    assert "Mise à jour automatique · toutes les 10 s" in page.text
    assert "window.refreshCashierInvoices = refreshInvoices" in page.text
    assert "window.setInterval(refreshInvoices, refreshEveryMs)" in page.text
    assert "currentStats.innerHTML = nextStats.innerHTML" in page.text
    assert "currentResults.innerHTML = nextResults.innerHTML" in page.text

    waiting_only = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=waiting&payment=all"
    )
    assert waiting_only.status_code == 200
    assert waiting.reference in waiting_only.text
    assert delivered_server.reference not in waiting_only.text
    assert counter.reference not in waiting_only.text
    assert other_counter.reference not in waiting_only.text

    # Generic counter view remains available.
    counter_only = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=delivered&payment=all&origin=cashier"
    )
    assert counter_only.status_code == 200
    assert counter.reference in counter_only.text
    assert other_counter.reference in counter_only.text
    assert delivered_server.reference not in counter_only.text

    # Personnel filter distinguishes each cashier even though both orders are
    # counter sales with assigned_staff_id=None.
    alex_only = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=delivered&payment=all&origin=person-{cashier.id}"
    )
    assert alex_only.status_code == 200
    assert counter.reference in alex_only.text
    assert other_counter.reference not in alex_only.text
    assert delivered_server.reference not in alex_only.text

    ashley_only = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=delivered&payment=all&origin=person-{other_cashier.id}"
    )
    assert ashley_only.status_code == 200
    assert other_counter.reference in ashley_only.text
    assert counter.reference not in ashley_only.text
    assert delivered_server.reference not in ashley_only.text

    server_only = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=delivered&payment=all&origin=person-{server.id}"
    )
    assert server_only.status_code == 200
    assert delivered_server.reference in server_only.text
    assert counter.reference not in server_only.text
    assert other_counter.reference not in server_only.text

    # Old assignment-based bookmarks remain compatible.
    legacy_server_only = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=delivered&payment=all&origin=staff-{server_assignment.id}"
    )
    assert legacy_server_only.status_code == 200
    assert delivered_server.reference in legacy_server_only.text
    assert counter.reference not in legacy_server_only.text

    payment = client.post(
        f"/bars/{bar.id}/cashier/invoices",
        data={
            "csrf_token": _csrf(server_only),
            "action": "pay_cash",
            "order_id": str(delivered_server.id),
            "delivery": "delivered",
            "payment": "all",
            "origin": f"person-{server.id}",
        },
        follow_redirects=False,
    )
    assert payment.status_code == 302
    db.session.refresh(delivered_server)
    assert delivered_server.payment_status == "PAID"

    paid_server = client.get(
        f"/bars/{bar.id}/cashier/invoices?delivery=delivered&payment=paid&origin=person-{server.id}"
    )
    assert paid_server.status_code == 200
    assert delivered_server.reference in paid_server.text
    assert "✓ Payée" in paid_server.text

    workspace = client.get(f"/bars/{bar.id}/cashier/workspace")
    assert workspace.status_code == 200
    assert f"/bars/{bar.id}/cashier/invoices" in workspace.text
    assert "Factures" in workspace.text
    assert "cashier-human-order-title" in workspace.text
    assert "Réf. système" in workspace.text
