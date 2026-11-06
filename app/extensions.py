"""Flask extensions declared without an attached application."""

from flask import request
from flask_login import LoginManager, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect


db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
def _rate_limit_key():
    """Keep logged-in employees separate behind a shared bar network.

    Login attempts always stay limited by client IP, including when the browser
    already has a session. API requests without a web session remain IP-based.
    """
    remote_address = get_remote_address()
    if request.endpoint in {"auth.web_login", "api_auth.token_login"}:
        return f"ip:{remote_address}"
    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return f"ip:{remote_address}"


limiter = Limiter(key_func=_rate_limit_key)
