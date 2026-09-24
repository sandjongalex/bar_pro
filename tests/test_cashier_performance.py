from decimal import Decimal

from sqlalchemy import select

from app.cash_services import cash_service
from app.cashier_performance import build_cashier_performance
from app.expense_services import expense_service
from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.payment_services import payment_service
from app.shift_service import start_shift
from test_workflows import env


def _cashier(owner, bar):
    user = User(
        email="performance-cashier@example.invalid",
        display_name="Esther Performance",
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
    return user, assignment


def test_cashier_performance_uses_current_shift_and_server_sales(env):
    app, owner, bar, _, product, server, _, _ = env
    cashier, cashier_assignment = _cashier(owner, bar)
    server_assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server.id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert server_assignment is not None
    start_shift(cashier, bar.id, server_assignment.id)
    session = cash_service.open(cashier, bar.id, "PERF-CASH", 1000)

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
        invoice_name="Client comptoir",
    )
    order_service.confirm(cashier, bar.id, counter_order.id)

    payment_service.record(
        cashier,
        bar.id,
        server_order.id,
        "PERF-PAY-1",
        "CASH",
        200,
        200,
        0,
        cash_session_id=session.id,
    )
    payment_service.record(
        cashier,
        bar.id,
        counter_order.id,
        "PERF-PAY-2",
        "CARD",
        100,
        100,
    )

    category = expense_service.create_category(owner, bar.id, "Transport")
    expense_service.create(
        cashier,
        bar.id,
        category.id,
        "PERF-EXPENSE",
        "Taxi",
        50,
        "CASH",
        cash_session_id=session.id,
    )
    db.session.commit()

    performance = build_cashier_performance(
        bar.id,
        cashier.id,
        cashier_assignment.id,
        bar.timezone,
    )

    assert performance is not None
    assert performance["service_sales"] == Decimal("300")
    assert performance["counter_sales"] == Decimal("100")
    assert performance["net_collected"] == Decimal("300")
    assert performance["order_count"] == 2
    assert performance["average_ticket"] == Decimal("150")
    assert performance["unpaid_count"] == 0
    assert performance["expense_total"] == Decimal("50")
    assert performance["expense_count"] == 1
    assert len(performance["servers"]) == 1
    assert performance["servers"][0]["name"] == server.display_name
    assert performance["servers"][0]["sales"] == Decimal("200")
    assert performance["servers"][0]["order_count"] == 1
    assert performance["servers"][0]["average_ticket"] == Decimal("200")
    assert performance["servers"][0]["unpaid_count"] == 0

    client = app.test_client()
    login = client.post(
        "/login",
        data={"email": cashier.email, "password": "test-password"},
        follow_redirects=True,
    )
    assert login.status_code == 200
    assert "Performance du service" in login.text
    assert "Mes ventes comptoir" in login.text
    assert "Encaissé par moi" in login.text
    assert "Ticket moyen" in login.text
    assert "Dépenses saisies" in login.text
    assert "Serveuses actuellement en service" in login.text
    assert server.display_name in login.text
