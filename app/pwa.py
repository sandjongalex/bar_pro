"""Public, unprivileged PWA service worker endpoint (root scope)."""
from flask import Blueprint, make_response, current_app

bp = Blueprint("pwa", __name__)

@bp.get("/sw.js")
def service_worker():
    response = make_response(current_app.send_static_file("sw.js"))
    response.headers["Content-Type"] = "text/javascript; charset=utf-8"
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response
