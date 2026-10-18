"""Owner Bento dashboard regression and tenant isolation checks."""
import re
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment

from app.extensions import db
from app.models import Bar
from app.owner_bento_dashboard import owner_bento_overview
from app.payment_services import payment_service
from test_workflows import env, order


def _login(client, email):
    page = client.get("/login")
    csrf = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert csrf
    return client.post(
        "/login",
        data={
            "email": email,
            "password": "test-password",
            "csrf_token": csrf.group(1),
        },
    )


def test_bento_templates_parse_and_keep_existing_dashboard():
    root = Path(__file__).resolve().parents[1]
    page = (root / "app/templates/dashboard.html").read_text()
    partial = (root / "app/templates/_owner_bento_overview.html").read_text()
    Environment().parse(page)
    Environment().parse(partial)
    assert "{% include '_owner_bento_overview.html' %}" in page
    assert "dashboard-reset-dialog" in page
    assert "dashboard_kpi_charts.js" in page
    assert "owner_bento.js" in page
    for label in (
        "Chiffre d'affaires global",
        "Alertes stocks critiques",
        "Performances des établissements",
        "Serveurs en service",
        "Écarts de caisse détectés",
    ):
        assert label in partial


def test_owner_bento_is_isolated_and_uses_actual_paid_sales(env):
    app, owner, bar, foreign_bar, _, _, _, _ = env
    second = Bar(
        owner_id=owner.id,
        name="Second Owned Bar",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(second)
    db.session.commit()

    value = order(env, quantity=2)
    payment_service.record(owner, bar.id, value.id, "BENTO-CARD", "CARD", 200, 200)
    db.session.commit()

    # Pass an unauthorized bar on purpose: the service must exclude it.
    overview = owner_bento_overview(owner, [bar, foreign_bar, second])
    assert {venue["id"] for venue in overview["bars"]} == {bar.id, second.id}
    assert overview["turnover_today"] == Decimal("200")
    assert next(venue["sales_today"] for venue in overview["bars"] if venue["id"] == bar.id) == Decimal("200")
    assert overview["chart"]["values"][-1] == 200
    assert overview["servers_on_duty"] == 0
    assert overview["cash_discrepancy_amount"] == 0

    client = app.test_client()
    assert _login(client, owner.email).status_code in (302, 303)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Chiffre d'affaires global" in response.text
    assert "Second Owned Bar" in response.text
    assert foreign_bar.name not in response.text
    assert 'id="ownerBentoSalesChart"' in response.text
    assert "Réinitialiser le bar" in response.text


def test_owner_bento_does_not_combine_different_currencies(env):
    _, owner, bar, foreign_bar, _, _, _, _ = env
    eur = Bar(
        owner_id=owner.id,
        name="Euro Owned Bar",
        timezone="Europe/Paris",
        currency="EUR",
    )
    db.session.add(eur)
    db.session.commit()

    overview = owner_bento_overview(owner, [bar, foreign_bar, eur])
    assert overview["mixed_currencies"] is True
    assert overview["turnover_today"] is None
    assert overview["cash_discrepancy_amount"] is None
    assert overview["chart"]["values"] == []
    assert {venue["currency"] for venue in overview["bars"]} == {"FCFA", "EUR"}
    assert foreign_bar.id not in {venue["id"] for venue in overview["bars"]}
