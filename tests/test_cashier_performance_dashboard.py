import re
from decimal import Decimal

from app.cash_services import cash_service
from app.cashier_performance import build_cashier_performance
from app.expense_services import expense_service
from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.payment_services import payment_service
from app.shift_service import start_shift
from test_workflows import env


def _employee(email, name, bar, role):
    user = User(email=email, display_name=name, category="EMPLOYEE", is_active=True)
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=user.id,
        role=role,
        started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    return user, assignment


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None
    return match.group(1)


def test_cashier_dashboard_metrics_are_current_shift_and_per_server(env):
    app, owner, bar, _, product, _, _, _ = env
    cashier, cashier_assignment = _employee(
        "performance-cashier@example.invalid", "Esther", bar, "CASHIER"
    )
    server, server_assignment = _employee(
        "performance-server@example.invalid", "Marie", bar, "SERVER"
    )
    start_shift(owner, bar.id, cashier_assignment.id)
    start_shift(cashier, bar.id, server_assignment.id)
    db.session.commit()

    cash_session = cash_service.open(cashier, bar.id, "PERF-CASH", 10000)
    db.session.flush()

    server_order = order_service.create(
        server,
        bar.id,
        "PERF-SERVER",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(cashier, bar.id, server_order.id)

    counter_order = order_service.create(
        cashier,
        bar.id,
        "PERF-COUNTER",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Comptoir test",
    )
    order_service.confirm(cashier, bar.id, counter_order.id)

    payment_service.record(
        cashier,
        bar.id,
        server_order.id,
        "PERF-PAY-SERVER",
        "CARD",
        200,
        200,
    )
    payment_service.record(
        cashier,
        bar.id,
        counter_order.id,
        "PERF-PAY-COUNTER",
        "CASH",
        100,
        100,
        0,
        cash_session.id,
    )

    category = expense_service.create_category(owner, bar.id, "Transport")
    expense_service.create(
        cashier,
        bar.id,
        category.id,
        "PERF-EXP",
        "Taxi",
        50,
        "CASH",
        cash_session_id=cash_session.id,
    )
    db.session.commit()

    metrics = build_cashier_performance(
        bar.id,
        cashier.id,
        cashier_assignment.id,
        bar.timezone,
    )
    assert metrics is not None
    assert metrics["service_sales"] == Decimal("300")
    assert metrics["counter_sales"] == Decimal("100")
    assert metrics["collected"] == Decimal("300")
    assert metrics["net_collected"] == Decimal("300")
    assert metrics["expense_total"] == Decimal("50")
    assert metrics["expense_count"] == 1
    assert metrics["order_count"] == 2
    assert metrics["average_ticket"] == Decimal("150")
    assert metrics["unpaid_count"] == 0
    assert len(metrics["servers"]) == 1
    assert metrics["servers"][0]["name"] == "Marie"
    assert metrics["servers"][0]["sales"] == Decimal("200")
    assert metrics["servers"][0]["order_count"] == 1

    client = app.test_client()
    login_page = client.get("/login")
    login = client.post(
        "/login",
        data={
            "email": cashier.email,
            "password": "test-password",
            "csrf_token": _csrf(login_page),
        },
        follow_redirects=False,
    )
    assert login.status_code == 302
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Tableau de bord caissière" in page.text
    assert "Ventes du service" in page.text
    assert "Vos ventes comptoir" in page.text
    assert "Performance des serveuses en service" in page.text
    assert "Marie" in page.text
    assert "300" in page.text
