"""Inventory V2 count entry regression tests: blanks, zero, cases and secure saves."""
from decimal import Decimal
import re

import pytest
from sqlalchemy import select

from test_workflows import balance, env
from test_inventory_v2_foundation import on_duty_cashier
from app.extensions import db
from app.inventory_count_models import InventoryLineCountState
from app.inventory_services import inventory_service
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventories import payload
from app.models import InventoryLine, StockMovement


def create_v2(env, cashier, reference="COUNT-V2"):
    inv = inventory_service.create(
        cashier, env[2].id, reference, [env[4].id], "Comptage par caissière"
    )
    db.session.commit()
    return inv


def save(env, cashier, inventory, *, cases="", units="", note="", revision=0):
    return inventory_service.count_v2_entries(cashier, env[2].id, inventory.id, [{
        "product_id": env[4].id, "cases": cases, "units": units,
        "note": note, "revision": revision,
    }])


def line(env, inv):
    return db.session.scalar(
        select(InventoryLine).where(
            InventoryLine.bar_id == env[2].id,
            InventoryLine.inventory_id == inv.id,
            InventoryLine.product_id == env[4].id,
        )
    )


def test_blank_stays_uncounted_and_zero_is_real(env):
    cashier, _, _ = on_duty_cashier(env)
    inv = create_v2(env, cashier)
    save(env, cashier, inv)
    db.session.commit()
    assert db.session.query(InventoryLineCountState).count() == 0
    assert line(env, inv).counted_quantity is None
    assert payload(inv)["complete"] is False
    assert payload(inv)["lines"][0]["last_counted_at"] is None
    save(env, cashier, inv, units="0", revision=0)
    db.session.commit()
    assert line(env, inv).counted_quantity == Decimal("0")
    assert payload(inv)["complete"] is True
    assert payload(inv)["lines"][0]["last_counted_at"]
    save(env, cashier, inv, revision=1)
    db.session.commit()
    assert line(env, inv).counted_quantity is None
    assert balance(env) == Decimal(10)
    assert db.session.scalar(select(StockMovement).where(
        StockMovement.inventory_line_id == line(env, inv).id,
    )) is None


def test_cases_and_loose_units_are_converted_once(env):
    cashier, _, _ = on_duty_cashier(env)
    env[4].units_per_case = 12
    db.session.commit()
    inv = create_v2(env, cashier, "CASES")
    save(env, cashier, inv, cases="2", units="3", note="Deux casiers et trois bouteilles")
    db.session.commit()
    current = payload(inv)["lines"][0]
    assert current["physical_quantity"] == "27.000000"
    assert current["counted_cases"] == "2"
    assert Decimal(current["counted_units"]) == 3
    assert current["note"] == "Deux casiers et trois bouteilles"
    assert current["count_revision"] == 1
    assert balance(env) == 10


@pytest.mark.parametrize("cases,units", [
    ("-1", "0"), ("1.5", "1"), ("1", "-1"),
    ("1", "NaN"), ("1", "Infinity"), ("1", "0.0000001"),
    ("99999999999999999", "0"),
])
def test_invalid_values_rejected_without_mutating_stock(env, cases, units):
    cashier, _, _ = on_duty_cashier(env)
    env[4].units_per_case = 12
    db.session.commit()
    inv = create_v2(env, cashier)
    with pytest.raises(ValueError):
        save(env, cashier, inv, cases=cases, units=units)
    db.session.rollback()
    assert line(env, inv).counted_quantity is None
    assert balance(env) == 10


def test_revision_conflict_rejects_second_writer(env):
    cashier, _, _ = on_duty_cashier(env)
    inv = create_v2(env, cashier)
    save(env, cashier, inv, units="4")
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_COUNT_CONFLICT"):
        save(env, cashier, inv, units="7", revision=0)
    db.session.rollback()
    assert line(env, inv).counted_quantity == Decimal(4)
    save(env, cashier, inv, units="7", revision=1)
    db.session.commit()
    assert line(env, inv).counted_quantity == Decimal(7)
    assert payload(inv)["lines"][0]["count_revision"] == 2


def test_wrong_cashier_and_foreign_shift_cannot_count(env):
    cashier_a, _, _ = on_duty_cashier(env, "stage2-a")
    cashier_b, _, _ = on_duty_cashier(env, "stage2-b")
    inv = create_v2(env, cashier_a)
    with pytest.raises(LookupError):
        save(env, cashier_b, inv, units="6")
    db.session.rollback()
    assert line(env, inv).counted_quantity is None
    assert balance(env) == 10


def _login(client, user):
    page = client.get("/login")
    csrf = re.search(
        r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
        page.text,
    ).group(1)
    response = client.post("/login", data={
        "email": user.email, "password": "test-password", "csrf_token": csrf,
    })
    assert response.status_code in (302, 303)


def test_web_count_page_autosave_and_csrf(env):
    cashier, _, _ = on_duty_cashier(env)
    inv = create_v2(env, cashier)
    client = env[0].test_client()
    _login(client, cashier)
    path = f"/bars/{env[2].id}/inventories/{inv.id}"
    response = client.get(path)
    assert response.status_code == 302
    assert "/v2-count" in response.headers["Location"]
    count_page = client.get(path + "/v2-count")
    assert count_page.status_code == 200
    assert "Non compté" in count_page.text
    assert re.search(
        rf'name="units_{env[4].id}"[^>]*value=""',
        count_page.text,
    ) is not None
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', count_page.text).group(1)
    bad = client.post(path + "/v2-count", json={
        "entries": [{"product_id": env[4].id, "units": "0", "revision": 0}]
    })
    assert bad.status_code == 400  # no CSRF token
    good = client.post(
        path + "/v2-count",
        headers={"X-CSRFToken": csrf},
        json={"entries": [{"product_id": env[4].id, "units": "0",
                           "cases": "", "note": "", "revision": "0"}]},
    )
    assert good.status_code == 200, good.text
    assert good.json["data"]["counted"] == 1
    assert good.json["data"]["lines"][0]["count_revision"] == 1
    assert line(env, inv).counted_quantity == 0
    assert balance(env) == 10
    assert client.post(path, data={"action": "post", "csrf_token": csrf,
                                  f"quantity_{env[4].id}": "0"}).status_code == 302
    assert balance(env) == 10


def test_web_html_no_js_round_trip_saves_blank_not_zero(env):
    cashier, _, _ = on_duty_cashier(env)
    inv = create_v2(env, cashier, "NO-JS")
    client = env[0].test_client()
    _login(client, cashier)
    path = f"/bars/{env[2].id}/inventories/{inv.id}/v2-count"
    page = client.get(path)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    result = client.post(path, data={
        "csrf_token": csrf,
        f"units_{env[4].id}": "",
        f"note_{env[4].id}": "Pas encore compté",
        f"revision_{env[4].id}": "0",
    })
    assert result.status_code == 302
    assert line(env, inv).counted_quantity is None
    assert payload(inv)["lines"][0]["note"] == "Pas encore compté"


def test_v2_draft_never_triggers_stock_adjustment(env):
    cashier, _, _ = on_duty_cashier(env)
    inv = create_v2(env, cashier)
    save(env, cashier, inv, units="2")
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_V2_PENDING_APPROVAL"):
        inventory_service.post(env[1], env[2].id, inv.id)
    db.session.rollback()
    assert balance(env) == 10
    assert db.session.query(InventoryLineCountState).count() == 1
    assert db.session.query(InventoryCashierAssignment).count() == 1


def test_api_v2_requires_revision_entries_and_preserves_legacy_contract(env):
    from app.auth import issue
    cashier, _, _ = on_duty_cashier(env, "api-stage2")
    inv = create_v2(env, cashier, "API-STAGE2")
    token, _ = issue(cashier, env[2].id)
    db.session.commit()
    client = env[0].test_client()
    headers = {"Authorization": "Bearer " + token}
    path = f"/api/v1/bars/{env[2].id}/inventories/{inv.id}/counts"
    old_shape = client.patch(path, json={
        "quantities": {str(env[4].id): "0"}
    }, headers=headers)
    assert old_shape.status_code == 422
    assert line(env, inv).counted_quantity is None
    current = client.patch(path, json={
        "entries": [{"product_id": env[4].id, "cases": "",
                     "units": "0", "note": "", "revision": 0}]
    }, headers=headers)
    assert current.status_code == 200, current.text
    assert current.json["data"]["complete"] is True
    assert line(env, inv).counted_quantity == 0
    stale = client.patch(path, json={
        "entries": [{"product_id": env[4].id,
                     "units": "5", "revision": 0}]
    }, headers=headers)
    assert stale.status_code == 422
    assert line(env, inv).counted_quantity == 0


def test_case_counts_without_case_size_cannot_be_added(env):
    cashier, _, _ = on_duty_cashier(env, "no-pack")
    inv = create_v2(env, cashier, "NO-PACK")
    with pytest.raises(ValueError):
        save(env, cashier, inv, cases="1", units="")
    db.session.rollback()
    assert line(env, inv).counted_quantity is None
    assert balance(env) == 10


def test_unchanged_form_or_note_only_does_not_reset_physical_count_time(env):
    cashier, _, _ = on_duty_cashier(env, "time-stable")
    inv = create_v2(env, cashier, "COUNT-TIME")
    save(env, cashier, inv, units="5")
    db.session.commit()
    first = payload(inv)["lines"][0]
    assert first["count_revision"] == 1
    saved_time = first["last_counted_at"]
    save(env, cashier, inv, units="5", revision=1)
    db.session.commit()
    unchanged = payload(inv)["lines"][0]
    assert unchanged["count_revision"] == 1
    assert unchanged["last_counted_at"] == saved_time
    save(env, cashier, inv, units="5", note="Photo vérifiée", revision=1)
    db.session.commit()
    note_only = payload(inv)["lines"][0]
    assert note_only["count_revision"] == 2
    assert note_only["last_counted_at"] == saved_time
    assert note_only["note"] == "Photo vérifiée"
    assert balance(env) == 10
