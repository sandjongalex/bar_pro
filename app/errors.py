"""Safe HTTP error responses for HTML and API requests."""

from __future__ import annotations

from flask import Flask, jsonify, request
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
    from werkzeug.exceptions import HTTPException

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
        app.logger.error("Unhandled application error: %s", type(error.original_exception).__name__)
        return _response(500, "INTERNAL_SERVER_ERROR", "Erreur interne")
