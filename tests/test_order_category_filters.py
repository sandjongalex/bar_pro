"""Contract checks for category shortcuts in order and sub-order forms.

These checks deliberately do not trigger a live GitHub Actions workflow.
"""
from pathlib import Path

import pytest


ROOT = Path("app")
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


@pytest.mark.parametrize(
    "template,tabs,grid,search,status,empty",
    [
        ("order_quick.html", "categoryTabs", "productGrid", "posSearch",
         "serverProductFilterStatus", "serverProductFilterEmpty"),
        ("cashier_workspace.html", "cashierCategoryTabs", "cashierProductGrid",
         "cashierProductSearch", "cashierProductFilterStatus",
         "cashierProductFilterEmpty"),
        ("suborder_builder.html", "suborderTabs", "suborderProducts",
         "suborderSearch", "suborderProductFilterStatus",
         "suborderProductFilterEmpty"),
    ],
)
def test_order_surfaces_connect_accessible_category_filter(
    template, tabs, grid, search, status, empty
):
    markup = (TEMPLATES / template).read_text(encoding="utf-8")
    assert f'id="{tabs}" data-pos-filter="{grid}"' in markup
    assert f'data-pos-search="{search}"' in markup
    assert f'data-pos-count="{status}"' in markup
    assert f'data-pos-empty="{empty}"' in markup
    assert f'id="{status}" role="status" aria-live="polite"' in markup
    assert f'id="{empty}" hidden' in markup
    assert "data-category=\"all\" aria-pressed=\"true\"" in markup
    assert "data-filter-count aria-hidden=\"true\"" in markup
    assert 'data-pos-filter-reset>Tout afficher</button>' in markup


def test_shared_filter_keeps_cart_intact_and_supports_live_stock():
    script = (STATIC / "order_category_filters.js").read_text(encoding="utf-8")
    assert "card.hidden = !show;" in script
    assert "card.getAttribute('data-category')" in script
    assert "input.addEventListener('input', render)" in script
    assert "instances[gridId] = render;" in script
    assert "refreshByGrid: function (gridId)" in script
    assert "aria-pressed" in script
    assert "addEventListener('click', function ()" in script
    assert "fetch(" not in script
    assert "cart.clear" not in script
    assert "window.location" not in script


def test_responsive_category_buttons_do_not_show_hidden_products():
    css = (STATIC / "order_category_filters.css").read_text(encoding="utf-8")
    assert "@media (max-width: 800px)" in css
    assert "@media (max-width: 360px)" in css
    assert "overflow-x: auto;" in css
    assert "min-height: 44px" in css
    assert ".pos-products .pos-product[hidden]" in css
    assert ".suborder-products .suborder-product[hidden]" in css
    assert "display: none !important;" in css

    layout = (TEMPLATES / "layout.html").read_text(encoding="utf-8")
    assert "order_category_filters.css" in layout
    assert "order_category_filters.js" in layout
    assert layout.index("order_category_filters.js") < layout.index("server_pos_ui.js")


@pytest.mark.parametrize(
    "file,grid",
    [
        ("server_pos_ui.js", "productGrid"),
        ("cashier_live_ui.js", "cashierProductGrid"),
    ],
)
def test_live_stock_refresh_preserves_category_selection(file, grid):
    js = (STATIC / file).read_text(encoding="utf-8")
    assert f"BarProProductCategoryFilters?.refreshByGrid('{grid}')" in js
