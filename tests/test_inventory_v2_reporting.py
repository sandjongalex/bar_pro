"""Inventory V2 owner/cashier reporting and immutable audit trail."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from test_workflows import env, balance
from test_inventory_v2_approval import make_admin, make_count, post_old_baseline
from test_inventory_v2_foundation import on_duty_cashier

from app.auth import issue
from app.extensions import db
from app.inventory_approval import decide, submit, update_policy
from app.inventory_services import inventory_service
from app.inventory_v2_reports import inventory_v2_report
from app.models import Product, User
from app.stock_service import stock_service


def test_posted_report_uses_actual_adjustment_movement_snapshot_not_live_stock(env):
    post_old_baseline(env)
    admin = make_admin(env)
    update_policy(env[1], env[2].id, max_unit_difference="2",
                  max_product_value="100", max_total_value="100")
    db.session.commit()
    cashier, inventory = make_count(env, reference="REPORT-V2",
                                    quantity="9", justification="Une bouteille cassée")
    submit(cashier, env[2].id, inventory.id)
    decide(admin, env[2].id, inventory.id, decision="approve",
           reason="Casse physiquement vérifiée")
    db.session.commit()
    report = inventory_v2_report(env[1], env[2].id, inventory.id)
    assert report["status"] == "POSTED"
    assert report["read_only"] is True
    assert report["cash_reconciliation_included"] is False
    assert report["cashier_name"] == cashier.display_name
    assert report["approved_discrepancy_count"] == 1
    assert Decimal(report["approved_absolute_value"]) == 40
    assert Decimal(report["lines"][0]["approved_delta"]) == -1
    assert Decimal(report["lines"][0]["approved_value"]) == 40
    assert Decimal(report["lines"][0]["approval_unit_cost"]) == 40
    assert len(report["history"]) == 2
    assert [item["action"] for item in report["history"]] == [
        "inventory.v2.submit", "inventory.v2.approve"
    ]
    assert balance(env) == 9

    stock_service.move(env[1], env[2].id, env[4].id,
                       "SALE", "-2", "Vente après validation")
    env[4].valuation_unit_cost = 800
    env[4].sale_price = 1500
    db.session.commit()
    later = inventory_v2_report(env[1], env[2].id, inventory.id)
    assert later["approved_absolute_value"] == report["approved_absolute_value"]
    assert later["lines"][0]["approved_delta"] == report["lines"][0]["approved_delta"]
    assert later["lines"][0]["approval_unit_cost"] == report["lines"][0]["approval_unit_cost"]
    assert balance(env) == 7


def test_zero_difference_report_is_not_misrepresented_as_cash_balance(env):
    cashier, inventory = make_count(env, reference="REPORT-ZERO")
    submit(cashier, env[2].id, inventory.id)
    decide(env[1], env[2].id, inventory.id, decision="approve",
           reason="Comptage conforme")
    db.session.commit()
    report = inventory_v2_report(env[1], env[2].id, inventory.id)
    assert report["approved_discrepancy_count"] == 0
    assert Decimal(report["approved_absolute_value"]) == 0
    assert Decimal(report["lines"][0]["approved_delta"]) == 0
    assert report["cash_reconciliation_included"] is False


def test_pending_recount_shows_readonly_progress_and_all_audit_actions(env):
    admin = make_admin(env)
    cashier, inventory = make_count(env, reference="REPORT-RECOUNT",
                                    quantity="9", justification="À vérifier")
    submit(cashier, env[2].id, inventory.id)
    decide(admin, env[2].id, inventory.id, decision="recount",
           reason="Recontrôler le casier")
    db.session.commit()
    report = inventory_v2_report(cashier, env[2].id, inventory.id)
    assert report["status"] == "RECOUNT"
    assert report["counted_count"] == report["product_count"] == 1
    assert report["approved_discrepancy_count"] is None
    assert report["approved_absolute_value"] is None
    assert report["lines"][0]["approved_delta"] is None
    assert [i["action"] for i in report["history"]] == [
        "inventory.v2.submit", "inventory.v2.recount",
    ]
    assert balance(env) == 10


def test_other_cashier_and_bar_cannot_fetch_report(env):
    cashier, inventory = make_count(env, reference="REPORT-SCOPING")
    stranger, _, _ = on_duty_cashier(env, "report-scoping-other")
    with pytest.raises(LookupError):
        inventory_v2_report(stranger, env[2].id, inventory.id)
    with pytest.raises(PermissionError):
        inventory_v2_report(env[1], env[3].id, inventory.id)
    with pytest.raises(PermissionError):
        inventory_v2_report(env[3].owner_id and db.session.get(User, env[3].owner_id),
                            env[2].id, inventory.id)
    db.session.rollback()


def test_api_and_print_use_v2_report_without_v1_cash_reconciliation(env):
    cashier, inventory = make_count(env, reference="REPORT-WEB")
    submit(cashier, env[2].id, inventory.id)
    decide(env[1], env[2].id, inventory.id,
           decision="approve", reason="Contrôle conforme")
    owner_token, _ = issue(env[1], env[2].id)
    stranger, _, _ = on_duty_cashier(env, "report-web-other")
    stranger_token, _ = issue(stranger, env[2].id)
    db.session.commit()
    headers = {"Authorization": "Bearer " + owner_token}
    client = env[0].test_client()
    url = f"/api/v1/bars/{env[2].id}/inventories/{inventory.id}/report"
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json["data"]["status"] == "POSTED"
    assert response.json["data"]["approved_absolute_value"] is not None
    assert response.json["data"]["cash_reconciliation_included"] is False
    assert client.get(url, headers={
        "Authorization": "Bearer " + stranger_token,
    }).status_code == 404
    assert client.get(url.replace(f"/bars/{env[2].id}/",
                                  f"/bars/{env[3].id}/"), headers=headers).status_code == 404

    # Session-authenticated owner, CSRF login, then direct report & printing.
    import re
    csrf = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
                     client.get("/login").text).group(1)
    assert client.post("/login", data={
        "email": env[1].email, "password": "test-password",
        "csrf_token": csrf,
    }).status_code == 302
    web = f"/bars/{env[2].id}/inventories/{inventory.id}"
    report_page = client.get(web + "/v2-report")
    assert report_page.status_code == 200
    assert "Caisse" not in report_page.text.split('class="inventory-table', 1)[-1]
    assert "Rapport de stock" in report_page.text
    printed = client.get(web + "/print", follow_redirects=True)
    assert printed.status_code == 200
    assert "Rapprochement caisse" not in printed.text
    assert "Rapport Inventaire V2" in printed.text
    redirect = client.get(web)
    assert redirect.status_code == 302
    assert "/v2-report" in redirect.location


def test_inventory_listing_distinguishes_v1_and_v2_financial_figures(env):
    post_old_baseline(env)
    cashier, inventory = make_count(env, reference="REPORT-LIST")
    token, _ = issue(env[1], env[2].id)
    db.session.commit()
    assert inventory_v2_report(cashier, env[2].id, inventory.id)["status"] == "DRAFT"
    import re
    client = env[0].test_client()
    csrf = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
                     client.get("/login").text).group(1)
    client.post("/login", data={
        "email": env[1].email, "password": "test-password",
        "csrf_token": csrf,
    })
    resp = client.get(f"/bars/{env[2].id}/inventories")
    assert resp.status_code == 200
    assert "Ventes théoriques V1" in resp.text
    assert "Écart caisse V1" in resp.text
    assert "Caissière :" in resp.text
    # The new V2 row is not attributed any theoretical cash shortage.
    start = resp.text.find("REPORT-LIST")
    end = resp.text.find("</tr>", start)
    assert start >= 0
    row = resp.text[start:end]
    assert "—" in row
    assert "0 XAF" not in row


def test_existing_v1_report_remains_available(env):
    inv = inventory_service.create(env[1], env[2].id,
                                   "V1-NOT-A-V2", [env[4].id], "Historical")
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_V2_REQUIRED"):
        inventory_v2_report(env[1], env[2].id, inv.id)
