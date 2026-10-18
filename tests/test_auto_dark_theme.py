"""Color-mode contracts for Bootstrap/Jinja and the sign-in flow."""
from pathlib import Path
import re

from jinja2 import Environment

from test_workflows import env

ROOT = Path(__file__).resolve().parents[1]


def test_layout_and_login_include_early_theme_and_accessible_switches():
    for name in ("layout.html", "login.html"):
        markup = (ROOT / "app/templates" / name).read_text(encoding="utf-8")
        Environment().parse(markup)
        assert 'filename=\'theme.js\'' in markup
        assert 'filename=\'dark_theme.css\'' in markup
        assert 'data-theme-toggle' in markup
        assert 'aria-pressed="false"' in markup
        assert 'data-theme-icon' in markup
        assert 'data-theme-label' in markup
        assert 'type="button"' in markup
        assert markup.index("theme.js") < markup.index("bootstrap.min.css")
        assert markup.index("dark_theme.css") > markup.index("app.css")


def test_dark_theme_tokens_and_accessible_login_and_operational_styles():
    base = (ROOT / "app/static/app.css").read_text(encoding="utf-8")
    css = (ROOT / "app/static/dark_theme.css").read_text(encoding="utf-8")
    js = (ROOT / "app/static/theme.js").read_text(encoding="utf-8")

    assert ':root[data-bs-theme="dark"]' in base
    assert '--page: #121212' in base
    assert '--bs-body-bg: #121212' in base
    for selector in (
        '.login-form .form-control',
        '.login-card',
        '.topbar',
        '.global-mobile-bottom',
        '.panel-card',
        '.pos-product',
        '.checkout-method',
        '.exchange-card',
        '.modal-content.modal-liquid-glass',
    ):
        assert selector in css
    assert 'border-width: 2px' in css
    assert ':focus-visible' in css
    assert "new Date().getHours()" in js
    assert "window.localStorage" in js
    assert "'data-bs-theme'" in js
    assert "window.setInterval" in js


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None
    return match.group(1)


def test_login_and_authenticated_navigation_render_theme_control(env):
    app, owner, *_ = env
    client = app.test_client()

    login = client.get("/login")
    assert login.status_code == 200
    assert "theme.js" in login.text
    assert "dark_theme.css" in login.text
    assert 'data-theme-toggle' in login.text
    assert 'name="password"' in login.text
    assert 'name="csrf_token"' in login.text

    response = client.post(
        "/login",
        data={"email": owner.email, "password": "test-password", "csrf_token": _csrf(login)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    home = client.get("/dashboard")
    assert home.status_code == 200
    assert 'data-theme-toggle' in home.text
    assert 'dark_theme.css' in home.text
    assert "Déconnexion" in home.text
