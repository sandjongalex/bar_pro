from app import create_app


def test_create_app_uses_testing_configuration(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-environment-value")
    app = create_app("testing")

    assert app.config["TESTING"] is True
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")


def test_healthcheck_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_api_healthcheck_returns_ok(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.get_json() == {"success": True, "data": {"status": "ok"}, "meta": {}}


def test_api_not_found_uses_the_api_error_envelope(client):
    response = client.get("/api/v1/not-implemented")

    assert response.status_code == 404
    assert response.get_json() == {
        "success": False,
        "error": {"code": "NOT_FOUND", "message": "Ressource indisponible", "details": None},
    }
