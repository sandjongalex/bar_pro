import re

from sqlalchemy import select

from app.extensions import db
from app.models import CashSession, StaffAssignment, User, utcnow
from test_workflows import env


def _login(client, email, password="test-password"):
    page = client.get("/login")
    csrf = re.search("name='csrf_token' value='([^']+)'", page.text).group(1)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )


def test_cashier_must_open_session_before_checkout(env):
    app, _, bar, _, _, _, _, _ = env
    cashier = User(email="cashier@example.invalid", display_name="Esther", category="EMPLOYEE")
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=cashier.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    checkout_path = f"/bars/{bar.id}/checkout"
    response = client.get(checkout_path)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/bars/{bar.id}/cashier-session")

    opening = client.get(response.headers["Location"])
    assert opening.status_code == 200
    assert "Ouvrir ma caisse" in opening.text
    assert "Esther" in opening.text

    csrf = re.search('name="csrf_token" value="([^"]+)"', opening.text).group(1)
    response = client.post(
        f"/bars/{bar.id}/cashier-session",
        data={"opening_amount": "25000", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(checkout_path)

    session = db.session.scalar(
        select(CashSession).where(CashSession.bar_id == bar.id, CashSession.status == "OPEN")
    )
    assert session is not None
    assert session.opening_amount == 25000
    assert session.opened_by_id == cashier.id
    assert session.reference.startswith("CAISSE-")

    checkout = client.get(checkout_path)
    assert checkout.status_code == 200
    assert "Commandes reçues" in checkout.text


def test_cashier_session_page_reuses_existing_open_session(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier = User(email="cashier2@example.invalid", display_name="Nancy", category="EMPLOYEE")
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=cashier.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    from app.cash_services import cash_service

    cash_service.open(owner, bar.id, "EXISTING", 5000)
    db.session.commit()

    client = app.test_client()
    _login(client, cashier.email)
    page = client.get(f"/bars/{bar.id}/cashier-session")
    assert page.status_code == 200
    assert "Caisse ouverte" in page.text
    assert "5,000" in page.text
    assert "Voir les commandes" in page.text
