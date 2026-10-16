from pathlib import Path
from jinja2 import Environment
ROOT = Path(__file__).resolve().parents[1]
def test_kpi_charts_use_existing_values_and_preserve_controls():
    template=(ROOT/"app/templates/dashboard.html").read_text()
    Environment().parse(template)
    assert "dashboard_data.values.today_sales" in template
    assert "dashboard_data.values.stock_purchase_value" in template
    assert "dashboard_data.values.stock_sale_value" in template
    assert "dashboard-kpi-data" in template
    assert 'dashboard-reset-dialog' in template
    assert "csrf_token()" in template
    assert "chart_data|tojson" in template
    assert "dashboard_kpi_charts.js" in template
    js=(ROOT/"app/static/dashboard_kpi_charts.js").read_text()
    assert "kpiFinanceChart" in js and "kpiStockChart" in js
    assert "Number.isFinite" in js
