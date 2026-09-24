from pathlib import Path


def test_catalogue_table_becomes_cards_on_tablet_and_mobile():
    css = Path("app/static/catalog.css").read_text(encoding="utf-8")

    assert "@media (max-width: 920px)" in css
    assert ".catalog-main-panel .table-responsive { overflow:visible; }" in css
    assert ".catalog-table thead { display:none; }" in css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert '.catalog-table tbody td:nth-child(7)::before { content:"Actions"; }' in css

    assert "@media (max-width: 760px)" in css
    assert ".catalog-table tbody { grid-template-columns:1fr;" in css


def test_catalogue_small_screen_actions_remain_visible():
    css = Path("app/static/catalog.css").read_text(encoding="utf-8")

    assert "@media (max-width: 520px)" in css
    assert ".catalog-hero-actions .btn { width:100%; }" in css
    assert ".catalog-table .product-actions { width:100%; display:grid; grid-template-columns:1fr 1fr;" in css
    assert ".catalog-table .product-actions form .btn { width:100%; }" in css


def test_catalogue_modal_footer_stays_visible_on_short_screens():
    css = Path("app/static/catalog.css").read_text(encoding="utf-8")

    assert ".catalog-modal > form { display:flex; flex-direction:column;" in css
    assert "max-height:calc(100dvh - 3.5rem)" in css
    assert ".catalog-modal > form > .modal-body { min-height:0; overflow-y:auto;" in css
    assert ".catalog-modal > form > .modal-header,.catalog-modal > form > .modal-footer { flex:0 0 auto; }" in css
    assert "max-height:calc(100dvh - 2rem)" in css
    assert ".catalog-modal .modal-footer .btn { width:100%; margin:0; }" in css
