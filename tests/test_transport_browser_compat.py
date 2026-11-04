"""Host-independent HTTPS transport and browser compatibility includes."""

from pathlib import Path

import pytest

from app import create_app
from app.config import ConfigError, validate_config
from test_config import production

ROOT = Path(__file__).resolve().parents[1]


def _app(**values):
    return create_app("testing", {
        "SECRET_KEY": "transport-test-secret",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        **values,
    })


def test_hsts_https_only_and_host_independent():
    client = _app(HSTS_MAX_AGE=86400).test_client()
    plain = client.get("/login", base_url="http://example.test")
    assert plain.headers.get("Strict-Transport-Security") is None
    secure = client.get("/login", base_url="https://example.test")
    assert secure.headers["Strict-Transport-Security"] == "max-age=86400"


def test_forwarded_proto_is_untrusted_by_default():
    client = _app(HSTS_MAX_AGE=86400).test_client()
    result = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert result.headers.get("Strict-Transport-Security") is None


def test_single_controlled_proxy_may_signal_https():
    client = _app(HSTS_MAX_AGE=86400, TRUSTED_PROXY_PROTO_HOPS=1).test_client()
    result = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert result.headers["Strict-Transport-Security"] == "max-age=86400"


@pytest.mark.parametrize("setting,value", [
    ("TRUSTED_PROXY_PROTO_HOPS", "-1"),
    ("TRUSTED_PROXY_PROTO_HOPS", "2"),
    ("TRUSTED_PROXY_PROTO_HOPS", "invalid"),
    ("HSTS_MAX_AGE", "-1"),
    ("HSTS_MAX_AGE", "invalid"),
    ("HSTS_MAX_AGE", "63072001"),
])
def test_invalid_proxy_security_configuration_rejected(setting, value):
    config = production()
    config[setting] = value
    with pytest.raises(ConfigError):
        validate_config(config)


def test_compatibility_bootstrap_precedes_other_scripts():
    for name in ("layout.html", "login.html"):
        content = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert "browser_compat.css" in content
        assert content.index("browser_compat.js") < content.index("theme.js")
    layout = (ROOT / "app" / "templates" / "layout.html").read_text(encoding="utf-8")
    assert layout.index("browser_compat.js") < layout.index("barpro_async.js")
