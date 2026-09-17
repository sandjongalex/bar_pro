"""WSGI entry point for a production WSGI server."""

from app import create_app

application = create_app()
