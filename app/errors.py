"""Safe HTTP error responses for HTML and API requests."""

from __future__ import annotations

from pathlib import Path
from traceback import extract_tb

from flask import Flask, jsonify, render_template, request
from flask_login import current_user
from werkzeug.exceptions import Forbidden, InternalServerError, NotFound


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def _response(status: int, code: str, message: str):
    if _wants_json():
        return jsonify(
            {"success": False, "error": {"code": code, "message": message, "details": None}}
        ), status
    return message, status


def register_error_handlers(app: Flask) -> None:
    from app.extensions import db
    from sqlalchemy.exc import IntegrityError
    from flask_wtf.csrf import CSRFError
    from werkzeug.exceptions import HTTPException, TooManyRequests

    @app.errorhandler(TooManyRequests)
    def rate_limit_error(error):
        return _response(
            429, "RATE_LIMITED",
            "Trop de requêtes pour le moment. Patientez un instant avant de réessayer.",
        )

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        # Never retry a rejected business POST: its result must be verified first.
        db.session.rollback()
        if request.path == "/login" or (
            not current_user.is_authenticated
            and (request.path.startswith("/bars/") or request.path == "/logout")
        ):
            from app.auth import _web_unauthorized_response
            return _web_unauthorized_response(expired=True)
        message = (
            "Le formulaire de sécurité a expiré. Actualisez la page et vérifiez "
            "l'état de l'opération avant une nouvelle tentative."
        )
        if _wants_json():
            return _response(400, "CSRF_REJECTED", message)
        return render_template("csrf_error.html"), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return _response(error.code, error.name.upper().replace(" ", "_"), "Requête refusée")

    @app.errorhandler(PermissionError)
    def permission_error(error):
        db.session.rollback()
        return _response(403, "FORBIDDEN", "Accès interdit")

    @app.errorhandler(LookupError)
    def lookup_error(error):
        db.session.rollback()
        return _response(404, "NOT_FOUND", "Ressource indisponible")

    @app.errorhandler(KeyError)
    def missing_field(error):
        db.session.rollback()
        return _response(400, "INVALID_REQUEST", "Champ requis manquant")

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def validation_error(error):
        db.session.rollback()
        return _response(422, "VALIDATION_FAILED", "Données invalides")

    @app.errorhandler(IntegrityError)
    def integrity_error(error):
        db.session.rollback()
        return _response(409, "STATE_CONFLICT", "Conflit de données")
    @app.errorhandler(NotFound)
    def not_found(_: NotFound):
        return _response(404, "NOT_FOUND", "Ressource indisponible")

    @app.errorhandler(Forbidden)
    def forbidden(_: Forbidden):
        return _response(403, "FORBIDDEN", "Accès interdit")

    @app.errorhandler(InternalServerError)
    def internal_error(error: InternalServerError):
        # Log only exception type and code locations: never SQL parameters,
        # HTTP payloads or exception messages that might contain private data.
        original = error.original_exception
        frames = extract_tb(original.__traceback__) if original is not None else []
        location = " -> ".join(
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in frames[-8:]
        ) or "unknown"
        app.logger.error(
            "Unhandled application error: type=%s endpoint=%s trace=%s",
            type(original).__name__ if original is not None else "Unknown",
            request.endpoint or "unknown",
            location,
        )
        return _response(500, "INTERNAL_SERVER_ERROR", "Erreur interne")
