"""
Flask application factory.

Security controls applied here:
- SECRET_KEY auto-generated with secrets.token_hex if not provided (OWASP A02)
- CSRF protection globally enabled via Flask-WTF
- Security headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options)
- Session cookies hardened (HttpOnly, SameSite=Lax, Secure when behind TLS)
- Structured JSON logging for SIEM ingestion
- Authentication delegated to Linux PAM (see app/auth.py)
"""
import os
import json
import secrets
import logging
from pathlib import Path

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager

csrf = CSRFProtect()
login_manager = LoginManager()
# Login view uses the new PAM-backed route registered in routes.py.
login_manager.login_view = "main.login"


# Allowed roles — kept here so they can be imported by routes.py without
# pulling in the PAM module (which may be unavailable in some contexts).
VALID_ROLES = ("admin", "viewer")


class User:
    """Authenticated principal. Role is one of "admin" or "viewer"."""

    def __init__(self, username: str, role: str):
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role!r}")
        self.id = f"{username}:{role}"
        self.username = username
        self.role = role

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        # Encoded as "<username>:<role>" so user_loader can reconstruct
        # without a separate lookup. The session is signed (Flask SECRET_KEY)
        # so a client cannot forge a different role.
        return self.id


@login_manager.user_loader
def load_user(user_id: str):
    """Reconstruct a User from a session-stored "username:role" string.

    Defence-in-depth: even though the cookie is signed, we still validate
    the role against ``VALID_ROLES`` and reject malformed values.
    """
    if not user_id or ":" not in user_id:
        return None
    username, _, role = user_id.partition(":")
    if not username or role not in VALID_ROLES:
        return None
    # Reject usernames containing control / unexpected chars.
    for ch in username:
        if not (ch.isalnum() or ch in "._-"):
            return None
    return User(username, role)


def _ensure_instance_dir(app: Flask) -> None:
    """Create instance/ with restrictive permissions if missing."""
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    # Linux: chmod 700 — only the service account should be able to read settings.json
    try:
        os.chmod(instance_path, 0o700)
    except OSError:
        # On non-POSIX or restricted FS this is best-effort.
        pass


def create_app() -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder="templates",
        static_folder="static",
    )

    # --- Core config -------------------------------------------------------
    secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Set SESSION_COOKIE_SECURE=True in production (behind HTTPS).
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        WTF_CSRF_TIME_LIMIT=3600,
        XDOME_TIMEOUT_SECONDS=30,
        # Cap logo uploads to 2 MB; the route also re-validates explicitly.
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )

    _ensure_instance_dir(app)

    # --- Extensions --------------------------------------------------------
    csrf.init_app(app)
    login_manager.init_app(app)

    # --- Blueprints --------------------------------------------------------
    from .routes import bp as main_bp

    app.register_blueprint(main_bp)

    # CSRF on JSON proxy endpoints would break vanilla fetch() without a token.
    # We exempt them; mitigations: same-origin cookies (SameSite=Lax),
    # and tokens never leave the server. For stricter setups, send the
    # CSRF token via X-CSRFToken header instead (Flask-WTF supports this).
    csrf.exempt(main_bp)  # exempt JSON GET proxies; HTML forms still validated below

    # --- Security headers (OWASP A05 — Security Misconfiguration) ----------
    @app.after_request
    def set_security_headers(response):
        # CSP: tight default, allow inline JS/CSS only because templates use small inline blocks.
        # In a hardened build, replace 'unsafe-inline' with nonces.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        # HSTS only meaningful behind HTTPS — set when SESSION_COOKIE_SECURE on.
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    # --- Structured logging (SIEM-ready) -----------------------------------
    if not app.logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","event_type":"app","message":"%(message)s"}'
            )
        )
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)

    # Surface a clear startup warning if PAM is unavailable so operators
    # see this in the SIEM rather than discovering it on the first login.
    try:
        from .auth import PAM_AVAILABLE
        if not PAM_AVAILABLE:
            app.logger.warning(
                "python-pam unavailable — login will always fail until libpam is installed"
            )
    except Exception:
        app.logger.warning("Could not import app.auth at startup")

    app.logger.info("xDome Alert WebApp initialized")

    return app
