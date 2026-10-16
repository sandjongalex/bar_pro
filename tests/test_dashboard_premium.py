"""Guard dashboard's Flask/Jinja/Chart.js integration during visual restyling."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
DASH = (ROOT / "app/templates/dashboard.html").read_text(encoding="utf-8")
LAYOUT = (ROOT / "app/templates/layout.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/dashboard_premium.css").read_text(encoding="utf-8")
CHART = (ROOT / "app/static/dashboard_charts.js").read_text(encoding="utf-8")


def test_dashboard_visual_change_preserves_contracts():
    Environment().parse(DASH)
    Environment().parse(LAYOUT)
    assert "block page_styles" in LAYOUT
    assert "dashboard_premium.css" in DASH
    assert "dashboard-page" in DASH
    assert "dashboard_data.cards" in DASH
    assert "operational_alerts.counts.low_stock" in DASH
    assert "dashboard-chart-data" in DASH
    assert "chart_data|tojson" in DASH
    for anchor in ("topProductsChart", "waitressChart", "salesTrendChart"):
        assert anchor in DASH and anchor in CHART
    for endpoint in ("bars.web_reset", "bars.web_detail", "stock.history", "checkout_web.checkout", "supplier_debts_web.manage"):
        assert endpoint in DASH
    assert "csrf_token()" in DASH
    assert "dashboard-reset-dialog" in DASH
    assert "data-theme" not in CSS
    assert ".dashboard-page" in CSS
    assert "--ds-primary" in CHART
