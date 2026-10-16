"""Regression checks for the login CTA and unchanged Flask authentication contract."""
import re
from pathlib import Path

from test_auth import app_with_auth

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app/templates/login.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/login.css").read_text(encoding="utf-8")


def _csrf(html):
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "CSRF form input must remain present"
    return match.group(1)


def test_login_primary_button_has_explicit_opaque_states():
    assert 'filename=\'login.css\'' in HTML
    assert 'filename=\'design-system.css\'' in HTML
    assert HTML.index("design-system.css") < HTML.index("login.css")
    assert 'name="email"' in HTML and 'name="password"' in HTML
    assert 'class="btn btn-primary btn-lg w-100 mt-2 login-submit"' in HTML
    assert 'type="submit">Se connecter</button>' in HTML
    assert "name=\"csrf_token\"" in HTML
    for declaration in (
        "background:#0f725f", "color:#fff", "--bs-btn-hover-color:#fff",
        "--bs-btn-disabled-color:#fff", ":focus-visible", ":disabled",
        "min-height:56px", "opacity:1", "background:#0b3f36",
    ):
        assert declaration in CSS


def test_login_form_success_error_and_logout_still_work(tmp_path):
    app = app_with_auth(tmp_path)
    client = app.test_client()
    initial = client.get("/login")
    assert initial.status_code == 200
    page = initial.get_data(as_text=True)
    assert 'Se connecter' in page
    assert 'id="togglePassword"' in page
    token = _csrf(page)

    invalid = client.post("/login", data={
        "csrf_token": token, "email": "owner.auth.demo@example.invalid",
        "password": "not-the-password"
    })
    assert invalid.status_code == 401
    assert "incorrect" in invalid.get_data(as_text=True)
    assert 'name="password"' in invalid.get_data(as_text=True)

    valid = client.post("/login", data={
        "csrf_token": _csrf(invalid.get_data(as_text=True)),
        "email": "owner.auth.demo@example.invalid",
        "password": "correct-password",
    })
    assert valid.status_code == 302
    assert valid.headers["Location"].endswith("/")
    with client.session_transaction() as session:
        assert "_user_id" in session

    logout_page = client.get("/")
    assert logout_page.status_code == 200
    logout_token = _csrf(logout_page.get_data(as_text=True))
    logout = client.post("/logout", data={"csrf_token": logout_token})
    assert logout.status_code == 302
    assert logout.headers["Location"].endswith("/login")
    with client.session_transaction() as session:
        assert "_user_id" not in session
