"""Opt-in web AJAX contract: Flask-WTF CSRF, session cookies and HTML fallback."""
from pathlib import Path

from flask import Flask, request, redirect, jsonify
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import Environment

from app.async_ui import wants_async_web_response, web_json_response

ROOT = Path(__file__).resolve().parents[1]


def _app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY="isolated-async-unit-test", TESTING=True, WTF_CSRF_ENABLED=True)
    CSRFProtect(app)

    @app.get("/bars/1/form")
    def form():
        return f'<form method="POST" action="/bars/1/order"><input name="csrf_token" value="{generate_csrf()}"></form>'

    @app.post("/bars/1/order")
    def order():
        if wants_async_web_response():
            return web_json_response(data={"id": 123, "reference": "T-123"}, message="Enregistrée", status=201)
        return redirect("/bars/1/form")

    @app.post("/bars/1/failure")
    def failure():
        if wants_async_web_response():
            return web_json_response(message="Stock insuffisant", error={"code": "INSUFFICIENT_STOCK"}, status=422)
        return redirect("/bars/1/form")

    @app.post("/api/v1/bars/1/orders")
    def jwt_api():
        return jsonify({"only": "jwt-api"})

    return app


def _token(client):
    import re
    response = client.get("/bars/1/form")
    assert response.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)


def _headers(token):
    return {"X-CSRFToken": token, "X-BarPro-Async": "1",
            "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


def test_csrf_enforced_and_json_opt_in_never_assumed():
    client = _app().test_client()
    csrf = _token(client)
    forbidden = client.post("/bars/1/order", headers={k:v for k,v in _headers(csrf).items() if k != "X-CSRFToken"})
    assert forbidden.status_code == 400
    accepted = client.post("/bars/1/order", headers=_headers(csrf))
    assert accepted.status_code == 201
    assert accepted.json["data"] == {"id": 123, "reference": "T-123"}
    assert accepted.json["success"] is True
    assert accepted.headers["Cache-Control"] == "no-store"


def test_html_form_remains_fallback_and_error_returns_json():
    client = _app().test_client()
    csrf = _token(client)
    fallback = client.post("/bars/1/order", data={"csrf_token": csrf})
    assert fallback.status_code == 302
    invalid = client.post("/bars/1/failure", headers=_headers(csrf))
    assert invalid.status_code == 422
    assert invalid.json["error"]["code"] == "INSUFFICIENT_STOCK"


def test_jwt_api_does_not_become_cookie_web_endpoint():
    client = _app().test_client()
    csrf = _token(client)
    response = client.post("/api/v1/bars/1/orders", headers=_headers(csrf))
    assert response.json == {"only": "jwt-api"}


def test_layout_load_order_and_no_automatic_business_form_optin():
    layout = (ROOT / "app/templates/layout.html").read_text(encoding="utf-8")
    Environment().parse(layout)
    assert "barpro_async.js" in layout
    assert "barpro_async.css" in layout
    assert layout.index("barpro_async.js") < layout.index("server_pos_ui.js")
    assert layout.index("skeleton_loading.js") < layout.index("server_pos_ui.js")
    assert 'data-bp-ajax="json"' not in layout
