import re

from sqlalchemy import select

from app.extensions import db
from app.models import CashMovement, CashSession, StaffAssignment, User, utcnow
from test_workflows import env, order


def _csrf_from(page):
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    csrf = _csrf_from(page)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )


def _cashier(bar, email, name):
    cashier = User(email=email, display_name=name, category="EMPLOYEE")
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
    return cashier


def test_cashier_must_open_session_before_checkout(env):
    app, _, bar, _, _, _, _, _ = env
    cashier = _cashier(bar, "cashier@example.invalid", "Esther")

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

    csrf = _csrf_from(opening)
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

    checkout = client.get(checkout_path, follow_redirects=False)
    assert checkout.status_code == 302
    assert f"/bars/{bar.id}/cashier/workspace" in checkout.headers["Location"]
    workspace = client.get(checkout.headers["Location"])
    assert workspace.status_code == 200
    assert "Poste de caisse" in workspace.text
    assert "Commandes reçues" in workspace.text


def test_cashier_session_page_reuses_existing_open_session(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier = _cashier(bar, "cashier2@example.invalid", "Nancy")
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
    assert "Clôturer ma caisse" in page.text
    assert "Montant réellement compté" in page.text


def test_cashier_closes_balanced_session_and_sees_summary(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier = _cashier(bar, "cashier-close@example.invalid", "Esther Close")
    from app.cash_services import cash_service

    session = cash_service.open(owner, bar.id, "CLOSE-OK", 5000)
    db.session.commit()

    client = app.test_client()
    _login(client, cashier.email)
    page = client.get(f"/bars/{bar.id}/cashier-session")
    csrf = _csrf_from(page)
    response = client.post(
        f"/bars/{bar.id}/cashier-session",
        data={
            "action": "close",
            "counted_closing_amount": "5000",
            "reason": "",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert f"closed_id={session.id}" in response.headers["Location"]

    db.session.refresh(session)
    assert session.status == "CLOSED"
    assert session.expected_closing_amount == 5000
    assert session.counted_closing_amount == 5000
    assert session.closing_difference == 0

    summary = client.get(response.headers["Location"])
    assert summary.status_code == 200
    assert "Caisse clôturée" in summary.text
    assert "Caisse équilibrée" in summary.text
    assert "Ouvrir une nouvelle caisse" in summary.text


def test_cashier_variance_requires_reason_and_records_shortage(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier = _cashier(bar, "cashier-gap@example.invalid", "Nancy Gap")
    from app.cash_services import cash_service

    session = cash_service.open(owner, bar.id, "CLOSE-GAP", 7000)
    db.session.commit()

    client = app.test_client()
    _login(client, cashier.email)
    page = client.get(f"/bars/{bar.id}/cashier-session")
    response = client.post(
        f"/bars/{bar.id}/cashier-session",
        data={
            "action": "close",
            "counted_closing_amount": "6500",
            "reason": "",
            "csrf_token": _csrf_from(page),
        },
    )
    assert response.status_code == 200
    assert "Un motif est obligatoire" in response.text
    db.session.refresh(session)
    assert session.status == "OPEN"

    page = client.get(f"/bars/{bar.id}/cashier-session")
    response = client.post(
        f"/bars/{bar.id}/cashier-session",
        data={
            "action": "close",
            "counted_closing_amount": "6500",
            "reason": "Écart constaté au comptage",
            "csrf_token": _csrf_from(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    db.session.refresh(session)
    assert session.status == "CLOSED"
    assert session.closing_difference == -500

    summary = client.get(response.headers["Location"])
    assert "Manque" in summary.text
    assert "-500" in summary.text


def test_cashier_daily_dashboard_tracks_receipts_and_manual_cash(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier = _cashier(bar, "cashier-daily@example.invalid", "Esther Daily")
    from app.cash_services import cash_service
    from app.payment_services import payment_service

    session = cash_service.open(owner, bar.id, "DAILY", 5000)
    value = order(env)
    payment_service.record(
        owner,
        bar.id,
        value.id,
        "DAILY-CASH",
        "CASH",
        80,
        80,
        0,
        cash_session_id=session.id,
    )
    payment_service.record(
        owner,
        bar.id,
        value.id,
        "DAILY-MOMO",
        "MOBILE_MONEY",
        120,
        120,
        0,
        provider_code="MTN",
        provider_transaction_id="DAILY-TXN",
    )
    cash_service.movement(owner, bar.id, session.id, "DEPOSIT", 1000, "Apport monnaie")
    cash_service.movement(owner, bar.id, session.id, "WITHDRAWAL", 500, "Achat urgent")
    db.session.commit()

    client = app.test_client()
    _login(client, cashier.email)

    finance = client.get(f"/bars/{bar.id}/finance", follow_redirects=False)
    assert finance.status_code == 302
    assert finance.headers["Location"].endswith(f"/bars/{bar.id}/cashier-session/daily")

    page = client.get(finance.headers["Location"])
    assert page.status_code == 200
    assert "Situation de caisse" in page.text
    assert "Encaissement net" in page.text
    assert "Mobile Money net" in page.text
    assert "Versement de caisse" in page.text
    assert "Apport monnaie" in page.text
    assert "5,580" in page.text

    response = client.post(
        f"/bars/{bar.id}/cashier-session/daily",
        data={
            "action": "movement",
            "kind": "DEPOSIT",
            "amount": "200",
            "reason": "Complément monnaie",
            "csrf_token": _csrf_from(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert cash_service.expected(session) == 5780

    page = client.get(f"/bars/{bar.id}/cashier-session/daily")
    response = client.post(
        f"/bars/{bar.id}/cashier-session/daily",
        data={
            "action": "send_receipt",
            "amount": "300",
            "reason": "Remise au gérant",
            "csrf_token": _csrf_from(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert cash_service.expected(session) == 5480
    versement = db.session.scalar(
        select(CashMovement)
        .where(
            CashMovement.bar_id == bar.id,
            CashMovement.cash_session_id == session.id,
            CashMovement.reason.like("Versement recette du service%"),
        )
        .order_by(CashMovement.id.desc())
    )
    assert versement is not None
    assert versement.amount_delta == -300
