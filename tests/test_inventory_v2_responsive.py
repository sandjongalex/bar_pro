"""V2 UI quantity and mobile-layout regressions, without touching stock."""
from decimal import Decimal
from pathlib import Path

import pytest

from test_inventory_v2_approval import make_count
from test_workflows import env
from app.inventory_reconciliation import inventory_live_reconciliation
from app.inventory_v2_reports import inventory_v2_report


@pytest.mark.parametrize("value,expected", [
    ("22.000000", "22"),
    ("0.000000", "0"),
    ("-0.000000", "0"),
    ("-2.500000", "-2.5"),
    ("22.125000", "22.125"),
    ("22.000001", "22.000001"),
    ("1000000000.000000", "1000000000"),
    (Decimal("12.000000"), "12"),
    (None, "—"),
])
def test_inventory_quantity_html_filter_precise_and_without_padding(env, value, expected):
    app = env[0]
    with app.app_context():
        assert app.jinja_env.from_string('{{ value|inventory_qty }}').render(value=value) == expected


def test_inventory_v2_count_screen_uses_compact_database_quantities(env):
    cashier, inv = make_count(env, reference="RESPONSIVE-QTY", quantity="9",
                             justification="Contrôle bouteille")
    data = inventory_live_reconciliation(inv)
    assert data["lines"][0]["current_machine"] == "10.000000"
    with env[0].app_context():
        rendered = env[0].jinja_env.from_string(
            '{{ value|inventory_qty }}'
        ).render(value=data["lines"][0]["current_machine"])
        assert rendered == "10"
    assert inv.status == "DRAFT"


def test_report_quantity_formatter_is_not_a_data_mutation(env):
    cashier, inv = make_count(env, reference="KEEP-PRECISION", quantity="9")
    data = inventory_v2_report(env[1], env[2].id, inv.id)
    raw = data["lines"][0]["counted_quantity"]
    assert Decimal(raw) == Decimal("9")
    assert raw == "9.000000"  # API retains the exact stored scale; UI strips padding
    assert env[0].jinja_env.from_string('{{ v|inventory_qty }}').render(v="9.000000") == "9"


def test_responsive_v2_source_has_no_unbounded_grid_children():
    css = Path("app/static/inventory_count_v2.css").read_text(encoding="utf-8")
    count = Path("app/templates/inventory_count_v2.html").read_text(encoding="utf-8")
    report = Path("app/templates/inventory_v2_report.html").read_text(encoding="utf-8")
    js = Path("app/static/inventory_count_v2.js").read_text(encoding="utf-8")
    assert "min-width:0" in css
    assert "overflow-wrap:anywhere" in css
    assert "overflow-x:auto" in css
    assert "@media(max-width:520px)" in css
    assert 'class="table-responsive inv2-table-scroll"' in report
    assert 'class="inv2-reconcile"' in count
    assert "if (!form) return;" in js
    assert js.index('refreshBtn.addEventListener("click", refreshPreview)') < js.index("if (!form) return;")
    assert "qty(line.current_machine)" in js
    assert "movement.quantity_delta)" in js
    assert "inventory_qty" in report
