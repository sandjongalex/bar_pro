"""Browser session expiry: safe HTML redirects, JSON 401, and CSRF separation."""

import re

from test_auth import app_with_auth


def _csrf(html):
    found = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert found
    return found.group(1)


def test_protected_web_get_redirects_to_login(tmp_path):
    client = app_with_auth(tmp_path).test_client()
    result = client.get("/dashboard", follow_redirects=False)
    assert result.status_code == 302
    assert result.headers["Location"].endswith("/login")
    assert result.headers["Cache-Control"] == "no-store"


def test_protected_ajax_get_returns_json_401_without_redirect(tmp_path):
    client = app_with_auth(tmp_path).test_client()
    result = client.get("/dashboard", headers={
        "Accept": "application/json", "X-BarPro-Async": "1",
        "X-Requested-With": "XMLHttpRequest",
    }, follow_redirects=False)
    assert result.status_code == 401
    assert result.json["error"]["code"] == "SESSION_EXPIRED"
    assert result.headers["Cache-Control"] == "no-store"


def test_login_expired_csrf_redirects_to_fresh_form(tmp_path):
    client = app_with_auth(tmp_path).test_client()
    original = _csrf(client.get("/login").get_data(as_text=True))
    with client.session_transaction() as session:
        session["csrf_token"] = "rotated-session-token"
    rejected = client.post("/login", data={
        "email": "owner.auth.demo@example.invalid",
        "password": "correct-password", "csrf_token": original,
    }, follow_redirects=False)
    assert rejected.status_code == 303
    assert rejected.headers["Location"].endswith("/login?session=expired")
    refreshed = client.get(rejected.headers["Location"])
    assert refreshed.status_code == 200
    assert "formulaire a expiré" in refreshed.get_data(as_text=True)
    assert _csrf(refreshed.get_data(as_text=True)) != original


def test_authenticated_csrf_expiry_does_not_logout_or_replay(tmp_path):
    client = app_with_auth(tmp_path).test_client()
    token = _csrf(client.get("/login").get_data(as_text=True))
    accepted = client.post("/login", data={
        "email": "owner.auth.demo@example.invalid",
        "password": "correct-password", "csrf_token": token,
    }, follow_redirects=False)
    assert accepted.status_code == 302
    rejected = client.post("/logout", data={"csrf_token": "invalid-token"}, follow_redirects=False)
    assert rejected.status_code == 400
    assert "Le formulaire a expiré" in rejected.get_data(as_text=True)
    with client.session_transaction() as session:
        assert session.get("_user_id") is not None


def test_anonymous_post_without_csrf_is_safe_303_to_login(tmp_path):
    client = app_with_auth(tmp_path).test_client()
    rejected = client.post("/logout", follow_redirects=False)
    assert rejected.status_code == 303
    assert rejected.headers["Location"].endswith("/login?session=expired")
