from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.stock import _stock_values


def test_stock_values_keep_cost_and_sale_values_separate():
    product = SimpleNamespace(
        valuation_unit_cost=Decimal("500"),
        sale_price=Decimal("800"),
    )

    cost_value, sale_value = _stock_values(Decimal("45"), product)

    assert cost_value == Decimal("22500")
    assert sale_value == Decimal("36000")


def test_stock_template_displays_sale_price_and_potential_value():
    template = Path("app/templates/stock.html").read_text(encoding="utf-8")

    assert "Prix de vente" in template
    assert "Valeur potentielle" in template
    assert "stats.total_sale_value" in template
    assert "item.sale_value" in template
