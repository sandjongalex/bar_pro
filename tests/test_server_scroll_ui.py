"""Static regression checks for scroll behavior in the waitress order entry.

CSS-only adjustment: order creation, prices and stock are unaffected.
"""
from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]


def _css_rule(source: str, selector: str) -> str:
    return source.split(selector + "{", 1)[1].split("}", 1)[0]


def test_waitress_product_list_scroll_chains_to_document():
    css = (ROOT / "app/static/server_pos.css").read_text(encoding="utf-8")
    products = _css_rule(css, ".server-pos-layout .pos-products")
    assert "overflow-y:auto" in products
    assert "overscroll-behavior-y:auto" in products
    assert "overscroll-behavior:contain" not in css
    assert "-webkit-overflow-scrolling:touch" in products


def test_waitress_basket_does_not_have_a_second_vertical_scrollbar():
    css = (ROOT / "app/static/server_pos.css").read_text(encoding="utf-8")
    basket = _css_rule(css, ".server-pos-layout .pos-cart-panel")
    lines = _css_rule(css, ".server-pos-layout .pos-cart-lines")
    assert "position:static" in basket
    assert "max-height:none" in basket
    assert "overflow:visible" in basket
    assert "max-height:none" in lines
    assert "overflow:visible" in lines
    assert "overflow-y:auto" not in lines
    assert "position:sticky" not in basket


def test_waitress_mobile_form_and_stylesheet_version_are_preserved():
    css = (ROOT / "app/static/server_pos.css").read_text(encoding="utf-8")
    layout = (ROOT / "app/templates/layout.html").read_text(encoding="utf-8")
    order_page = (ROOT / "app/templates/order_quick.html").read_text(encoding="utf-8")
    Environment().parse(order_page)
    assert "server_pos.css', v='20261107-server-scroll-v3'" in layout
    assert ".server-cart-dock:not([hidden])" in css
    assert "@media(max-width:767px)" in css
    assert 'id="productGrid"' in order_page
    assert 'id="cartLines"' in order_page
    assert 'id="confirmOrder"' in order_page
