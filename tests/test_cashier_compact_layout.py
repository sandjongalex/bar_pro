"""Cashier-specific compact POS layout: static regression checks only."""
from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "app/static/cashier_workspace_compact.css"
CASHIER_TEMPLATE = ROOT / "app/templates/cashier_workspace.html"


def test_cashier_only_stylesheet_is_loaded_after_the_existing_workspace_styles():
    template = CASHIER_TEMPLATE.read_text(encoding="utf-8")
    Environment().parse(template)
    assert "cashier_workspace_compact.css" in template
    assert template.index("cashier_workspace_compact.css") > template.index("</style>")
    assert 'id="cashierProductGrid"' in template
    assert 'id="cashierSaleCart"' in template
    assert 'id="cashierCategoryTabs"' in template
    assert 'name="cash_presented"' in template
    assert 'id="cashierTenderServerRow" hidden' in template
    assert 'title="{{ product.name }}"' in template


def test_product_grid_and_category_strip_use_compact_responsive_breakpoints():
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".cashier-sale-panel .cashier-sale-layout" in css
    assert "minmax(0, 7fr) minmax(292px, 3fr)" in css
    assert ".cashier-sale-catalog .pos-category-tabs[data-pos-filter]" in css
    assert "flex-wrap: nowrap" in css
    assert "overflow-x: auto" in css
    assert "scroll-snap-type: x proximity" in css
    assert "repeat(6, minmax(0, 1fr))" in css
    assert "repeat(5, minmax(0, 1fr))" in css
    assert "repeat(4, minmax(0, 1fr))" in css
    assert "repeat(3, minmax(0, 1fr))" in css
    assert "repeat(2, minmax(0, 1fr))" in css
    assert "@media (max-width: 919px)" in css
    assert "@media (max-width: 519px)" in css
    assert "min-height: 105px" in css
    assert "min-height: 44px" in css
    assert css.count("{") == css.count("}")


def test_basket_remains_accessible_with_cash_receipt_ui_intact():
    css = CSS_PATH.read_text(encoding="utf-8")
    template = CASHIER_TEMPLATE.read_text(encoding="utf-8")
    assert ".cashier-sale-cart .pos-cart-empty" in css
    assert ".cashier-sale-cart .cashier-sale-actions" in css
    # The product list is the only intentional nested vertical scroller.
    # When it reaches an edge, scrolling must continue on the main page.
    products = css.split(".cashier-sale-panel .cashier-sale-catalog .pos-products {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto" in products
    assert "overscroll-behavior-y: auto" in products
    assert "overscroll-behavior-y: contain" not in css

    # The entire basket (including long item lists and payment controls)
    # belongs to page scrolling, rather than an additional scroll trap.
    basket = css.split(".cashier-sale-panel .cashier-sale-cart {", 1)[1].split("}", 1)[0]
    assert "max-height: none" in basket
    assert "overflow: visible" in basket
    assert "position: static" in basket
    item_list = css.split(".cashier-sale-panel .cashier-sale-cart .pos-cart-lines {", 1)[1].split("}", 1)[0]
    assert "max-height: none" in item_list
    assert "overflow: visible" in item_list
    assert "position: sticky" not in css
    assert 'id="cashierCreateSale"' in template
    assert 'id="cashierCartJump"' in template
    assert 'id="cashierTenderAmount"' in template
    assert 'id="cashierTenderSource"' in template
    assert "updateTenderPreview();" in template
    assert "BarProProductCategoryFilters" not in css  # Filtering remains in shared JS.
    assert "20261107-scroll-fix-v2" in template  # Browsers load the fresh CSS after deploy.
