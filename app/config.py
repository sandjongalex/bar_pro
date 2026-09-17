"""Configuration loaded exclusively from environment variables and test overrides."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when a configuration is unsafe or incomplete."""


class BaseConfig:
    SECRET_KEY = os.getenv("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    MIGRATIONS_DIRECTORY = str(PROJECT_ROOT / "migrations")

    RATELIMIT_STORAGE_URI = os.getenv(
        "RATELIMIT_STORAGE_URI",
        "memory://",
    )
    RATELIMIT_DEFAULT = [
        "200 per day",
        "50 per hour",
    ]
    RATELIMIT_HEADERS_ENABLED = True

    LOGIN_RATE_LIMIT = os.getenv(
        "LOGIN_RATE_LIMIT",
        "5 per minute",
    )

    JWT_ISSUER = os.getenv(
        "JWT_ISSUER",
        "bar-manager-pro",
    )
    JWT_AUDIENCE = os.getenv(
        "JWT_AUDIENCE",
        "bar-manager-pro-api",
    )

    JWT_PRIVATE_KEY = os.getenv("JWT_PRIVATE_KEY")
    JWT_PUBLIC_KEY = os.getenv("JWT_PUBLIC_KEY")

    LOG_LEVEL = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    WTF_CSRF_TIME_LIMIT = 3600


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    ENVIRONMENT = "development"

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{PROJECT_ROOT / 'bar_manager_pro_dev.sqlite3'}",
    )


class TestingConfig(BaseConfig):
    TESTING = True
    ENVIRONMENT = "testing"

    WTF_CSRF_ENABLED = False

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URI",
        "sqlite+pysqlite:///:memory:",
    )

    RATELIMIT_ENABLED = False

    LOG_LEVEL = "WARNING"


class ProductionConfig(BaseConfig):
    DEBUG = False
    ENVIRONMENT = "production"

    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(config_name: str | None = None) -> type[BaseConfig]:
    """Return the requested Flask configuration class."""

    selected = config_name or os.getenv(
        "FLASK_CONFIG",
        "development",
    )

    try:
        return CONFIGS[selected]

    except KeyError as exc:
        available = ", ".join(CONFIGS)

        raise ConfigError(
            f"FLASK_CONFIG must be one of: {available}."
        ) from exc


def validate_config(config: dict) -> None:
    """
    Validate the final Flask configuration.

    Production configuration is intentionally stricter than
    development/testing configuration.
    """

    if not config.get("SECRET_KEY"):
        raise ConfigError(
            "SECRET_KEY must be supplied through the environment."
        )

    if not config.get("SQLALCHEMY_DATABASE_URI"):
        raise ConfigError(
            "SQLALCHEMY_DATABASE_URI must be supplied through the environment."
        )

    if config.get("LOG_LEVEL") not in {
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
    }:
        raise ConfigError(
            "LOG_LEVEL must be a standard Python log level."
        )

    # ---------------------------------------------------------
    # Production-specific validation
    # ---------------------------------------------------------

    if config.get("ENVIRONMENT") == "production":

        # MySQL is mandatory in production.
        scheme = urlparse(
            config["SQLALCHEMY_DATABASE_URI"]
        ).scheme

        if scheme != "mysql+pymysql":
            raise ConfigError(
                "Production requires a "
                "mysql+pymysql SQLALCHEMY_DATABASE_URI."
            )

        # Require a sufficiently strong Flask secret key.
        if len(config["SECRET_KEY"]) < 32:
            raise ConfigError(
                "Production SECRET_KEY must contain "
                "at least 32 characters."
            )

        # JWT keys are mandatory.
        if (
            not config.get("JWT_PRIVATE_KEY")
            or not config.get("JWT_PUBLIC_KEY")
        ):
            raise ConfigError(
                "Production requires JWT_PRIVATE_KEY "
                "and JWT_PUBLIC_KEY (or key files)."
            )

        # Validate ES256 P-256 key pair.
        try:
            from cryptography.hazmat.primitives.asymmetric.ec import (
                EllipticCurvePrivateKey,
                SECP256R1,
            )
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key,
                load_pem_public_key,
            )

            private = load_pem_private_key(
                config["JWT_PRIVATE_KEY"].encode(),
                password=None,
            )

            public = load_pem_public_key(
                config["JWT_PUBLIC_KEY"].encode()
            )

            if not isinstance(
                private,
                EllipticCurvePrivateKey,
            ):
                raise ValueError()

            if not isinstance(
                private.curve,
                SECP256R1,
            ):
                raise ValueError()

            if (
                private.public_key().public_numbers()
                != public.public_numbers()
            ):
                raise ValueError()

        except (
            ValueError,
            TypeError,
            AttributeError,
        ):
            raise ConfigError(
                "JWT keys must be a matching ES256 P-256 pair."
            ) from None

        # -----------------------------------------------------
        # Rate limiter storage
        # -----------------------------------------------------

        if not config.get("RATELIMIT_STORAGE_URI"):
            raise ConfigError(
                "RATELIMIT_STORAGE_URI is required."
            )

        # Normally production requires Redis or another
        # persistent backend.
        #
        # PythonAnywhere Free currently has constrained external
        # network access and a single-worker deployment can use
        # memory:// temporarily when explicitly authorized.
        if (
            config["RATELIMIT_STORAGE_URI"].startswith(
                "memory://"
            )
            and os.getenv(
                "ALLOW_IN_MEMORY_RATELIMIT"
            )
            != "1"
        ):
            raise ConfigError(
                "Production requires a durable "
                "RATELIMIT_STORAGE_URI. "
                "Set ALLOW_IN_MEMORY_RATELIMIT=1 only "
                "for constrained single-worker deployments."
            )


def load_environment(config) -> None:
    """
    Read secrets at application factory time.

    This makes it possible for the WSGI entry point to load
    environment variables before creating the Flask application.
    """

    environment_variables = (
        "SECRET_KEY",
        "SQLALCHEMY_DATABASE_URI",
        "RATELIMIT_STORAGE_URI",
        "JWT_PRIVATE_KEY",
        "JWT_PUBLIC_KEY",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "LOG_LEVEL",
        "LOGIN_RATE_LIMIT",
    )

    for name in environment_variables:

        if (
            name in os.environ
            and not (
                config.get("TESTING")
                and name
                == "SQLALCHEMY_DATABASE_URI"
            )
        ):
            config[name] = os.environ[name]

    # Testing database override.
    if (
        config.get("TESTING")
        and os.getenv("TEST_DATABASE_URI")
    ):
        config["SQLALCHEMY_DATABASE_URI"] = (
            os.environ["TEST_DATABASE_URI"]
        )

    # Load JWT keys from PEM files when configured.
    for name in (
        "JWT_PRIVATE_KEY",
        "JWT_PUBLIC_KEY",
    ):
        path = os.getenv(
            name + "_FILE"
        )

        if path:
            try:
                config[name] = Path(
                    path
                ).read_text(
                    encoding="utf-8"
                )

            except OSError:
                raise ConfigError(
                    f"Cannot read {name}_FILE."
                ) from None