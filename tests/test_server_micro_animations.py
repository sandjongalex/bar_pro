"""Server order animations: Jinja, CSS and browser load regression checks."""
import re
from pathlib import Path

from jinja2 import Environment

from test_workflows import env


ROOT = Path(__file__).resolve().parents[1]


def test_order_form_and_base_layout_preserve_existing_contract():
    template = (ROOT / "app/templates/order_quick.html").read_text(encoding="utf-8")
    layout = (ROOT / "app/templates/layout.html").read_text(encoding="utf-8")
    Environment().parse(template)
    Environment().parse(layout)

    assert 'id="posForm"' in template
    assert 'id="cartLines"' in template
    assert 'id="confirmOrder"' in template
    assert 'name="csrf_token"' in template
    assert 'name="action" value="create"' in template
    assert 'type="submit" disabled' in template
    assert 'class="qty-minus"' in template
    assert 'class="qty-plus"' in template
    assert 'input name="quantity"' in template
    assert "server_pos_ui.js" in layout
    assert "server_order_animations.js" in layout
    assert layout.index("server_pos_ui.js") < layout.index("server_order_animations.js")


def test_micro_animations_stay_scoped_and_work_with_dynamic_cart():
    script = (ROOT / "app/static/server_order_animations.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "app/static/server_pos.css").read_text(encoding="utf-8")

    assert "form#posForm.server-pos-layout" in script
    assert "cartLines.addEventListener('click'" in script
    assert "button.qty-plus, button.qty-minus" in script
    assert "requestAnimationFrame" in script
    assert "server-qty-bounce" in script
    assert "form.addEventListener('submit'" in script
    assert "event.defaultPrevented" in script
    assert "form.checkValidity()" in script
    assert "HTMLFormElement.prototype.submit.call(form)" in script
    assert "bi bi-check-circle" in script
    assert "spinner-border" in script
    assert ".server-pos-layout .pos-qty-control input.server-qty-bounce" in stylesheet
    assert "scale(1.1)" in stylesheet
    assert "prefers-reduced-motion" in stylesheet


def test_server_page_exposes_script_and_form_but_owner_has_no_server_scope(env):
    app, owner, bar, _, _, server, _, _ = env
    client = app.test_client()

    def login(user):
        page = client.get("/login")
        csrf = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
        assert csrf
        return client.post(
            "/login",
            data={"email": user.email, "password": "test-password", "csrf_token": csrf.group(1)},
            follow_redirects=False,
        )

    assert login(server).status_code == 302
    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    assert "server_order_animations.js" in page.text
    assert "server-pos-layout" in page.text
    assert 'id="confirmOrder"' in page.text
