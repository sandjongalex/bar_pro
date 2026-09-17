from __future__ import annotations

import os

import pytest


os.environ.setdefault("SECRET_KEY", "test-only-environment-value")
os.environ.setdefault("TEST_DATABASE_URI", "sqlite+pysqlite:///:memory:")


@pytest.fixture()
def app():
    from app import create_app

    application = create_app("testing")
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()
