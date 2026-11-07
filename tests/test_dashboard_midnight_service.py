"""Dashboard service-vs-calendar reporting around midnight.

The cashier's gross posted orders are a service-period view, while the legacy
owner chart is a calendar-day view of settled sales. Neither changes the
underlying sale, payment, cash session or shift at 00:00.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment
from sqlalchemy import select

from app.cash_services import cash_service
from app.current_service_reporting import current_service_snapshot
from app.dashboard_service import dashboard_summary
from app.extensions import db
from app.models import Order, StaffAssignment, User, utcnow
from app.order_services import order_service
from app.owner_bento_dashboard import owner_bento_overview
from app.shift_models import EmployeeShift
from app.shift_service import start_shift
from test_workflows import env


def _open_cashier_shift(env):
    app, owner, bar, *_ = env
    cashier = User(
        email="midnight-cashier@example.invalid",
        display_name="Caisse Nuit",
        category="EMPLOYEE",
        is_active=True,
    )
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id, user_id=cashier.id, role="CASHIER", started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    shift = start_shift(owner, bar.id, assignment.id)
    db.session.commit()
    return cashier, shift


def _sale(env, reference, quantity=1):
    _, owner, bar, _, product, *_ = env
    value = order_service.create(
        owner, bar.id, reference,
        [{"product_id": product.id, "quantity": str(quantity)}],
    )
    order_service.confirm(owner, bar.id, value.id)
    db.session.commit()
    return value


def test_cross_midnight_service_keeps_sales_before_and_after_0000(env):
    _, owner, bar, *_ = env
    cashier, shift = _open_cashier_shift(env)
    first = _sale(env, "SERVICE-PRE-MIDNIGHT", quantity=1)
    second = _sale(env, "SERVICE-POST-MIDNIGHT", quantity=2)
    # Simulate a service that begins at 22:30 local and runs into the next day.
    # DB timestamps are UTC-naive; Africa/Douala is UTC+01:00.
    shift.started_at = datetime(2026, 11, 6, 21, 30)
    first.posted_at = datetime(2026, 11, 6, 22, 45)   # 23:45 local, Nov 6
    second.posted_at = datetime(2026, 11, 6, 23, 20)  # 00:20 local, Nov 7
    db.session.commit()

    service = current_service_snapshot(
        bar, now_utc=datetime(2026, 11, 7, 0, 30, tzinfo=timezone.utc),
    )
    assert service is not None
    assert service["source"] == "cashier_shift"
    assert service["service_date"].isoformat() == "2026-11-06"
    assert service["crossed_midnight"] is True
    assert service["started_label"] == "06/11/2026 à 22:30"
    assert service["sales"] == Decimal("300")
    assert service["order_count"] == 2
    assert service["unpaid_count"] == 2
    assert db.session.scalar(select(EmployeeShift.status).where(
        EmployeeShift.id == shift.id
    )) == "OPEN"

    # The shift is the only thing that determines a live service's existence,
    # never the clock passing midnight.
    shift.status = "CLOSED"
    shift.ended_at = datetime(2026, 11, 7, 0, 30)
    shift.ended_by_id = owner.id
    db.session.commit()
    assert current_service_snapshot(
        bar, now_utc=datetime(2026, 11, 7, 0, 30, tzinfo=timezone.utc)
    ) is None


def test_owner_sees_the_same_live_sales_scope_as_cashier(env):
    app, owner, bar, other_bar, *_ = env
    cashier, shift = _open_cashier_shift(env)
    shift.started_at = utcnow() - timedelta(hours=5)
    db.session.commit()
    first = _sale(env, "SERVICE-OWNER-UNPAID", quantity=2)
    second = _sale(env, "SERVICE-OWNER-PAID", quantity=1)
    from app.payment_services import payment_service
    payment_service.record(owner, bar.id, second.id, "SERVICE-OWNER-CARD", "CARD", 100, 100)
    db.session.commit()

    snapshot = current_service_snapshot(bar)
    assert snapshot["sales"] == Decimal("300")
    assert snapshot["order_count"] == 2
    assert snapshot["unpaid_count"] == 1

    overview = owner_bento_overview(owner, [bar, other_bar])
    assert overview["bar_count"] == 1  # Cannot leak a foreign owner's service
    assert overview["open_service_count"] == 1
    assert overview["current_service_sales"] == Decimal("300")
    assert overview["current_service_order_count"] == 2
    assert overview["current_service_unpaid_count"] == 1
    assert overview["bars"][0]["service"]["sales"] == Decimal("300")
    assert overview["turnover_today"] == Decimal("100")  # Settled, calendar-day only.

    selected = dashboard_summary(owner, bar.id)
    assert selected["current_service"]["sales"] == Decimal("300")
    assert selected["values"]["today_sales"] == Decimal("100")
    client = app.test_client()
    # No mutations or switches on the owner's dashboard.
    assert client.get("/dashboard").status_code in (302, 401)


def test_open_cash_session_without_shift_is_supported(env):
    _, owner, bar, *_ = env
    cash = cash_service.open(owner, bar.id, "MIDNIGHT-FALLBACK-CASH", 0)
    cash.opened_at = utcnow() - timedelta(hours=5)
    db.session.commit()
    one = _sale(env, "SERVICE-FALLBACK", quantity=1)
    service = current_service_snapshot(bar)
    assert service is not None
    assert service["source"] == "cash_session"
    assert service["sales"] == Decimal("100")
    assert service["order_count"] == 1


def test_dashboard_templates_keep_civil_and_live_service_labels():
    root = Path(__file__).resolve().parents[1]
    dashboard = (root / "app/templates/dashboard.html").read_text(encoding="utf-8")
    owner = (root / "app/templates/_owner_bento_overview.html").read_text(encoding="utf-8")
    Environment().parse(dashboard)
    Environment().parse(owner)
    assert 'id="owner-current-service"' in dashboard
    assert "dashboard_data.current_service.sales" in dashboard
    assert "Journée civile" in dashboard
    assert "owner_bento.current_service_sales" in owner
    assert "owner_bento.turnover_today" in owner
    assert "bar.service.started_label" in owner
    assert "Graphique : 7 jours civils" in owner
