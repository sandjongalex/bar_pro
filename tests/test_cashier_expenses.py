import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.cash_services import cash_service
from app.expense_services import expense_service
from app.extensions import db
from app.models import Expense, ExpenseCategory, StaffAssignment, User, utcnow
from app.permissions import permissions
from app.shift_service import end_shift, start_shift
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
        email="expense-cashier@example.invalid",
        display_name="Esther Dépenses",
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


def test_on_duty_cashier_records_expense_without_admin_rights(env):
    app, owner, bar, foreign, _, _, _, _ = env
    category = expense_service.create_category(owner, bar.id, "Transport")
    db.session.commit()

    cashier, assignment = _cashier(owner, bar)
    session = cash_service.open(cashier, bar.id, "EXPENSE-CASH", 5000)
    db.session.commit()

    assert permissions.evaluate(cashier, "expenses.read", bar.id).allowed
    assert permissions.evaluate(cashier, "expenses.record", bar.id).allowed
    assert not permissions.evaluate(cashier, "expenses.manage", bar.id).allowed
    assert not permissions.evaluate(cashier, "expenses.record", foreign.id).allowed

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/expenses")
    assert page.status_code == 200
    assert "Enregistrer une dépense" in page.text
    assert "Enregistrées automatiquement à la validation" in page.text
    assert "Nouvelle catégorie" not in page.text
    assert "Voir les rapports" not in page.text
    assert "Dépenses" in page.text

    response = client.post(
        f"/bars/{bar.id}/expenses",
        data={
            "csrf_token": _csrf(page),
            "action": "expense_create",
            "expense_category_id": str(category.id),
            "amount": "1000",
            "method": "CASH",
            "incurred_at": "2000-01-01T00:00",
            "description": "Taxi pour achat de glaçons",
            "cash_session_id": str(session.id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Dépense enregistrée" in response.text

    item = db.session.scalar(
        select(Expense)
        .where(Expense.bar_id == bar.id, Expense.recorded_by_id == cashier.id)
        .order_by(Expense.id.desc())
    )
    assert item is not None
    assert item.description == "Taxi pour achat de glaçons"
    assert Decimal(item.amount) == Decimal("1000")
    assert item.method == "CASH"
    assert item.cash_session_id == session.id
    assert item.incurred_at.year != 2000
    assert cash_service.expected(session) == Decimal("4000")

    # A cashier may record an expense but cannot administer categories.
    page = client.get(f"/bars/{bar.id}/expenses")
    response = client.post(
        f"/bars/{bar.id}/expenses",
        data={
            "csrf_token": _csrf(page),
            "action": "category_create",
            "name": "Interdit caissière",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert db.session.scalar(
        select(ExpenseCategory).where(
            ExpenseCategory.bar_id == bar.id,
            ExpenseCategory.name == "Interdit caissière",
        )
    ) is None

    # Ending the shift immediately removes expense access/recording permission.
    end_shift(owner, bar.id, assignment.id)
    db.session.commit()
    decision = permissions.evaluate(cashier, "expenses.record", bar.id)
    assert not decision.allowed
    assert decision.reason == "OFF_DUTY"
    decision = permissions.evaluate(cashier, "expenses.read", bar.id)
    assert not decision.allowed
    assert decision.reason == "OFF_DUTY"
