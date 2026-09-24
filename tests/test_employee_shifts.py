from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask_migrate import upgrade
from jinja2 import Environment
from sqlalchemy import select, text

from app import create_app
from app.extensions import db
from app.models import Bar, StaffAssignment, User
from app.permissions import permissions
from app.shift_models import EmployeeShift
from app.shift_service import ShiftError, end_shift, start_shift


def _app(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'employee-shifts.sqlite'}",
            "JWT_PRIVATE_KEY": pem,
            "WTF_CSRF_ENABLED": False,
        },
    )


def _user(email, name, category, password):
    item = User(
        email=email,
        display_name=name,
        category=category,
        is_active=True,
    )
    item.set_password(password)
    db.session.add(item)
    db.session.flush()
    return item


def _assignment(bar, user, role):
    item = StaffAssignment(
        bar_id=bar.id,
        user_id=user.id,
        role=role,
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(item)
    db.session.flush()
    return item


def test_shift_hierarchy_access_and_tenant_isolation(tmp_path):
    app = _app(tmp_path)

    with app.app_context():
        upgrade()
        db.session.execute(text("PRAGMA foreign_keys=ON"))

        owner = _user("owner@example.invalid", "Owner", "OWNER", "owner-password")
        owner2 = _user("owner2@example.invalid", "Owner 2", "OWNER", "owner2-password")
        cashier = _user("cashier@example.invalid", "Esther", "EMPLOYEE", "cashier-password")
        cashier2 = _user("cashier2@example.invalid", "Nancy", "EMPLOYEE", "cashier2-password")
        server = _user("server@example.invalid", "Marie", "EMPLOYEE", "server-password")
        foreign_server = _user("foreign@example.invalid", "Other Server", "EMPLOYEE", "foreign-password")

        bar = Bar(owner_id=owner.id, name="KAPE BAR", timezone="Africa/Douala", currency="XAF")
        other_bar = Bar(owner_id=owner2.id, name="OTHER BAR", timezone="Africa/Douala", currency="XAF")
        db.session.add_all([bar, other_bar])
        db.session.flush()

        cashier_assignment = _assignment(bar, cashier, "CASHIER")
        cashier2_assignment = _assignment(bar, cashier2, "CASHIER")
        server_assignment = _assignment(bar, server, "SERVER")
        foreign_assignment = _assignment(other_bar, foreign_server, "SERVER")
        db.session.commit()

        # Being employed is not enough: operational/sales data is denied off duty.
        decision = permissions.evaluate(cashier, "payments.read", bar.id)
        assert not decision.allowed
        assert decision.reason == "OFF_DUTY"
        assert permissions.evaluate(cashier, "shifts.read", bar.id).allowed

        decision = permissions.evaluate(server, "orders.read", bar.id)
        assert not decision.allowed
        assert decision.reason == "OFF_DUTY"

        # An off-duty cashier cannot activate a server.
        with pytest.raises(PermissionError, match="CASHIER_OFF_DUTY"):
            start_shift(cashier, bar.id, server_assignment.id)
        db.session.rollback()

        # The owner starts a cashier. The recorded time and actor are automatic.
        cashier_shift = start_shift(owner, bar.id, cashier_assignment.id)
        db.session.commit()
        assert cashier_shift.status == "OPEN"
        assert cashier_shift.started_at is not None
        assert cashier_shift.started_by_id == owner.id
        assert cashier_shift.role_snapshot == "CASHIER"
        assert permissions.evaluate(cashier, "payments.read", bar.id).allowed

        # A cashier cannot open a shift for an employee in another tenant.
        with pytest.raises(PermissionError, match="FORBIDDEN"):
            start_shift(cashier, other_bar.id, foreign_assignment.id)
        db.session.rollback()

        # The on-duty cashier starts the server and sales access becomes available.
        server_shift = start_shift(cashier, bar.id, server_assignment.id)
        db.session.commit()
        assert server_shift.started_by_id == cashier.id
        assert permissions.evaluate(server, "orders.read", bar.id).allowed
        assert permissions.evaluate(server, "orders.create", bar.id).allowed

        # The same assignment can never have a second simultaneous open shift.
        with pytest.raises(ShiftError, match="SHIFT_ALREADY_OPEN"):
            start_shift(cashier, bar.id, server_assignment.id)
        db.session.rollback()

        # The last cashier cannot leave while servers are still working.
        with pytest.raises(ShiftError, match="SERVERS_STILL_ACTIVE"):
            end_shift(owner, bar.id, cashier_assignment.id)
        db.session.rollback()

        # A second cashier enables a clean handover.
        start_shift(owner, bar.id, cashier2_assignment.id)
        db.session.commit()
        first_cashier_closed = end_shift(owner, bar.id, cashier_assignment.id)
        db.session.commit()
        assert first_cashier_closed.status == "CLOSED"
        assert first_cashier_closed.ended_by_id == owner.id
        assert not permissions.evaluate(cashier, "payments.read", bar.id).allowed

        # Owner may close a forgotten server shift; access disappears immediately.
        closed_server = end_shift(owner, bar.id, server_assignment.id)
        db.session.commit()
        assert closed_server.status == "CLOSED"
        assert closed_server.ended_at is not None
        decision = permissions.evaluate(server, "orders.read", bar.id)
        assert not decision.allowed
        assert decision.reason == "OFF_DUTY"

        # History remains available for later attendance/report calculations.
        history = list(
            db.session.scalars(
                select(EmployeeShift)
                .where(EmployeeShift.bar_id == bar.id)
                .order_by(EmployeeShift.id)
            )
        )
        assert len(history) == 3
        assert {item.role_snapshot for item in history} == {"CASHIER", "SERVER"}


def test_off_duty_employee_lands_on_blocked_screen(tmp_path):
    app = _app(tmp_path)

    with app.app_context():
        upgrade()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        owner = _user("owner-web@example.invalid", "Owner", "OWNER", "owner-password")
        server = _user("server-web@example.invalid", "Ashley", "EMPLOYEE", "server-password")
        bar = Bar(owner_id=owner.id, name="KAPE BAR", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        _assignment(bar, server, "SERVER")
        db.session.commit()

    client = app.test_client()
    response = client.post(
        "/login",
        data={"email": "server-web@example.invalid", "password": "server-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Vous n&#39;êtes pas en service" in response.text or "Vous n'êtes pas en service" in response.text
    assert "commandes, paiements, caisse" in response.text


def test_shift_templates_parse_and_owner_entry_point_exists():
    env = Environment()
    shifts = Path("app/templates/shifts.html").read_text(encoding="utf-8")
    off_duty = Path("app/templates/off_duty.html").read_text(encoding="utf-8")
    bar_detail = Path("app/templates/bars/detail.html").read_text(encoding="utf-8")

    env.parse(shifts)
    env.parse(off_duty)
    env.parse(bar_detail)

    assert "Mettre en service" in shifts
    assert "Fin de service" in shifts
    assert "Personnel en service" in bar_detail
    assert "shifts_web.manage" in bar_detail
    assert "Vous n'êtes pas en service" in off_duty
