import re

from app.extensions import db
from app.models import Bar
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    assert page.status_code == 200
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def test_owner_can_still_access_multiple_owned_bars(env):
    app, owner, first_bar, _, _, _, _, _ = env
    second_bar = Bar(
        owner_id=owner.id,
        name="Owner Second Bar",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(second_bar)
    db.session.commit()

    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    listing = client.get("/bars/")
    assert listing.status_code == 200
    assert first_bar.name in listing.text
    assert second_bar.name in listing.text

    first_detail = client.get(f"/bars/{first_bar.id}")
    second_detail = client.get(f"/bars/{second_bar.id}")
    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
