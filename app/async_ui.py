"""Opt-in JSON envelope for cookie-authenticated Flask/Jinja web endpoints.

No business service or permission is called here. Each adapted route must continue
using its existing @login_required, tenant, shift, role, and CSRF protections.
The /api/v1 JWT Bearer API must never be treated as a cookie web endpoint.
"""
from __future__ import annotations

from flask import jsonify, request


def wants_async_web_response() -> bool:
    """True only for explicitly opted-in web requests with an AJAX JSON Accept."""
    return (
        request.headers.get("X-BarPro-Async") == "1"
        and request.headers.get("X-Requested-With") == "XMLHttpRequest"
        and request.accept_mimetypes.best == "application/json"
        and not request.path.startswith("/api/v1/")
    )


def web_json_response(*, data=None, message: str = "", status: int = 200, error=None):
    """Construct the shared web response; never use for /api/v1 routes.

    Existing HTML form handlers must branch on wants_async_web_response() and
    otherwise keep their original redirect/render behavior. JSON is not a
    substitute for invoking existing business permissions and validations.
    """
    if not wants_async_web_response():
        raise RuntimeError("ASYNC_WEB_OPT_IN_REQUIRED")
    if error is not None:
        payload = {"success": False, "error": error, "message": message}
    else:
        payload = {"success": True, "data": data, "message": message}
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Accept, X-BarPro-Async, X-Requested-With"
    return response
