"""
Application routes.

Layout:
- HTML pages: /, /tree, /settings, /login (GET/POST), /logout
- JSON proxy API consumed by the frontend JS:
    GET /api/alerts
    GET /api/alerts/<alert_id>/devices
    GET /api/devices/<asset_id>/events
- Public theme registry: GET /api/themes
- Logo: GET /logo, POST /settings/logo (admin)

Authentication / authorisation:
- All HTML pages and /api/* routes (except /api/themes) require login.
- Admin-only routes use the @admin_required decorator.

The xDome API token is only ever read server-side (in api_client.py).
"""
import json
import logging
import os
import re
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, render_template, request, jsonify, redirect, url_for,
    flash, current_app, abort, send_file,
)
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from flask_wtf.csrf import validate_csrf
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, URL, Length
from werkzeug.utils import secure_filename

from . import User
from .auth import authenticate_pam, get_user_role
from .themes import build_theme_payload, save_example_themes
from .api_client import (
    load_settings, save_settings,
    fetch_alerts, fetch_alert_devices, fetch_device_events,
    SettingsNotConfigured, XDomeAPIError,
    ALERT_FIELDS, DEVICE_FIELDS, EVENT_FIELDS,
)

logger = logging.getLogger(__name__)
bp = Blueprint("main", __name__)

# Permissive but bounded validation patterns. Defence-in-depth — the API
# client also checks these.
ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")

# ----- Logo handling ---------------------------------------------------------

# Allowed extensions and their canonical Content-Type.
LOGO_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}
LOGO_EXT_TO_MIME = {v: k for k, v in LOGO_MIME_TO_EXT.items()}
# JPEG can also arrive as "jpeg"; track it separately for filename suffix matching.
LOGO_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "svg")
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB


def _logo_path() -> Path | None:
    """Return the path of the existing logo file, if any."""
    inst = Path(current_app.instance_path)
    for ext in LOGO_EXTS:
        p = inst / f"logo.{ext}"
        if p.exists():
            return p
    return None


def logo_exists() -> bool:
    return _logo_path() is not None


def _delete_existing_logo() -> None:
    inst = Path(current_app.instance_path)
    for ext in LOGO_EXTS:
        p = inst / f"logo.{ext}"
        if p.exists():
            try:
                p.unlink()
            except OSError as e:
                logger.warning(json.dumps({
                    "event_type": "logo_delete_failed",
                    "path": str(p),
                    "error": str(e),
                }))


# ----- Decorators ------------------------------------------------------------

def admin_required(f):
    """Require an authenticated user with role == 'admin'."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(current_user, "role", None) == "admin":
            logger.warning(json.dumps({
                "event_type": "authz_denied",
                "username": getattr(current_user, "username", None),
                "role": getattr(current_user, "role", None),
                "path": request.path,
                "source_ip": request.remote_addr,
            }))
            abort(403)
        return f(*args, **kwargs)
    return decorated


# --- Forms --------------------------------------------------------------------

class LoginForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[DataRequired(), Length(max=64)],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(max=4096)],
    )
    submit = SubmitField("Sign in")


class SettingsForm(FlaskForm):
    base_uri = StringField(
        "Base URI",
        validators=[DataRequired(), URL(require_tld=False), Length(max=512)],
    )
    api_token = StringField(
        "API Token",
        validators=[DataRequired(), Length(max=4096)],
    )
    submit = SubmitField("Save")


# --- HTML pages ---------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        logo_exists=logo_exists(),
        alert_fields=ALERT_FIELDS,
        device_fields=DEVICE_FIELDS,
        event_fields=EVENT_FIELDS,
        default_alert_columns=[
            "id", "alert_type_name", "category", "status",
            "detected_time", "devices_count", "unresolved_devices_count",
            "description",
        ],
        default_device_columns=[
            "asset_id", "device_name", "device_type", "device_category",
            "ip_list", "site_name", "risk_score", "is_resolved",
        ],
        default_event_columns=[
            "detection_time", "event_type", "source_ip",
            "source_device_type", "dest_ip", "dest_device_type",
            "protocol", "description",
        ],
    )


@bp.route("/tree")
@login_required
def tree():
    return render_template("tree.html", logo_exists=logo_exists())


# --- Authentication routes ---------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    # Already-authenticated users skip the form entirely.
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        password = form.password.data or ""

        ok = authenticate_pam(username, password)
        role = get_user_role(username) if ok else None

        if ok and role:
            login_user(User(username, role))
            logger.info(json.dumps({
                "event_type": "login",
                "outcome": "success",
                "username": username,
                "role": role,
                "source_ip": request.remote_addr,
            }))
            return redirect(url_for("main.index"))

        # Generic failure message — never disclose whether the username
        # exists or whether the role check failed (avoids user enumeration).
        logger.warning(json.dumps({
            "event_type": "login",
            "outcome": "failure",
            "username": username,
            "pam_ok": bool(ok),
            "had_role": role is not None,
            "source_ip": request.remote_addr,
        }))
        flash("Invalid credentials or insufficient privileges.", "error")

    return render_template(
        "login.html",
        form=form,
        logo_exists=logo_exists(),
    )


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    username = getattr(current_user, "username", None)
    logout_user()
    logger.info(json.dumps({
        "event_type": "logout",
        "outcome": "success",
        "username": username,
        "source_ip": request.remote_addr,
    }))
    flash("Signed out.", "info")
    return redirect(url_for("main.login"))


# --- Settings (admin only) ---------------------------------------------------

@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    form = SettingsForm()
    current = load_settings(current_app.instance_path)

    # Make sure an example themes.json exists so admins know the format.
    save_example_themes(current_app.instance_path)

    if request.method == "GET":
        form.base_uri.data = current.get("base_uri", "")
        # Don't reflect the existing token to the page; show a placeholder hint.
        form.api_token.data = ""

    if form.validate_on_submit():
        try:
            save_settings(
                current_app.instance_path,
                form.base_uri.data.strip(),
                form.api_token.data.strip(),
            )
        except ValueError as e:
            flash(f"Invalid Base URI: {e}", "error")
        else:
            flash("Settings saved.", "info")
            return redirect(url_for("main.settings"))

    return render_template(
        "settings.html",
        form=form,
        mode="edit",
        configured=bool(current.get("base_uri") and current.get("api_token")),
        base_uri=current.get("base_uri", ""),
        logo_exists=logo_exists(),
    )


# --- Logo --------------------------------------------------------------------

@bp.route("/logo")
def logo():
    """Serve the uploaded logo, if any. Public — used on the login page."""
    p = _logo_path()
    if p is None:
        abort(404)
    ext = p.suffix.lstrip(".").lower()
    # Normalise jpg/jpeg → image/jpeg.
    mimetype = LOGO_EXT_TO_MIME.get(ext, "application/octet-stream")
    if ext == "jpeg":
        mimetype = "image/jpeg"
    return send_file(p, mimetype=mimetype, max_age=300)


@bp.route("/settings/logo", methods=["POST"])
@admin_required
def upload_logo():
    """Accept a multipart logo upload from an admin."""
    # Validate CSRF manually because the route is part of a blueprint that
    # csrf.exempt()s JSON endpoints; HTML forms still must carry the token.
    token = request.form.get("csrf_token", "")
    try:
        validate_csrf(token)
    except Exception:
        logger.warning(json.dumps({
            "event_type": "csrf_failure",
            "path": request.path,
            "username": getattr(current_user, "username", None),
            "source_ip": request.remote_addr,
        }))
        abort(400, description="CSRF validation failed")

    file = request.files.get("logo")
    if file is None or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("main.settings"))

    # MIME type whitelist — the browser sends this; we don't trust it as the
    # only check (extension is also validated, and we limit size).
    mimetype = (file.mimetype or "").lower()
    if mimetype not in LOGO_MIME_TO_EXT:
        flash(f"Unsupported file type: {mimetype or 'unknown'}.", "error")
        return redirect(url_for("main.settings"))

    # Size cap: read into memory once (max 2 MB). MAX_CONTENT_LENGTH on the
    # app blocks the worst case at the WSGI layer; we still verify here.
    data = file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        flash("File too large (max 2 MB).", "error")
        return redirect(url_for("main.settings"))
    if not data:
        flash("Uploaded file is empty.", "error")
        return redirect(url_for("main.settings"))

    # Determine target extension — prefer original filename's extension if
    # it matches the MIME type's allowed extensions; otherwise use the
    # canonical extension for the MIME type.
    safe_name = secure_filename(file.filename) or ""
    orig_ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    canonical_ext = LOGO_MIME_TO_EXT[mimetype]
    if mimetype == "image/jpeg" and orig_ext in ("jpg", "jpeg"):
        ext = orig_ext
    elif orig_ext == canonical_ext:
        ext = canonical_ext
    else:
        ext = canonical_ext

    inst = Path(current_app.instance_path)
    target = inst / f"logo.{ext}"

    # Remove any prior logo (other extensions) before writing the new one.
    _delete_existing_logo()

    try:
        with target.open("wb") as fh:
            fh.write(data)
        try:
            os.chmod(target, 0o640)
        except OSError:
            pass
    except OSError as e:
        logger.error(json.dumps({
            "event_type": "logo_upload_failed",
            "error": str(e),
            "username": getattr(current_user, "username", None),
        }))
        flash("Failed to save logo.", "error")
        return redirect(url_for("main.settings"))

    logger.info(json.dumps({
        "event_type": "logo_uploaded",
        "outcome": "success",
        "ext": ext,
        "size": len(data),
        "username": getattr(current_user, "username", None),
        "source_ip": request.remote_addr,
    }))
    flash("Logo updated.", "info")
    return redirect(url_for("main.settings"))


# --- Public theme registry ---------------------------------------------------

@bp.route("/api/themes")
def api_themes():
    """Public read-only theme registry (built-ins + custom overrides/extras)."""
    return jsonify(build_theme_payload(current_app.instance_path))


# --- JSON proxy API -----------------------------------------------------------

def _parse_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = request.args.get(name, default)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@bp.route("/api/alerts")
@login_required
def api_alerts():
    only_unresolved = request.args.get("filter_status") == "Unresolved"
    offset = _parse_int("offset", 0, lo=0, hi=1_000_000)
    limit = _parse_int("limit", 100, lo=1, hi=500)
    try:
        data = fetch_alerts(
            current_app.instance_path,
            only_unresolved=only_unresolved,
            offset=offset,
            limit=limit,
        )
        return jsonify(data)
    except SettingsNotConfigured:
        return jsonify({"error": "Settings not configured"}), 503
    except XDomeAPIError as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/alerts/<alert_id>/devices")
@login_required
def api_alert_devices(alert_id: str):
    if not ID_RE.match(alert_id):
        abort(400, description="Invalid alert id")
    offset = _parse_int("offset", 0, lo=0, hi=1_000_000)
    limit = _parse_int("limit", 100, lo=1, hi=500)
    try:
        data = fetch_alert_devices(
            current_app.instance_path, alert_id, offset=offset, limit=limit,
        )
        return jsonify(data)
    except SettingsNotConfigured:
        return jsonify({"error": "Settings not configured"}), 503
    except XDomeAPIError as e:
        return jsonify({"error": str(e)}), 502
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/ot_activity_events")
@login_required
def api_ot_activity_events():
    """Fetch OT activity events filtered by query-string parameters.

    Query params:
      - ``asset_id`` (string, optional): filter by ``dest_asset_id``.
      - ``alert_id`` (int,    optional): filter by ``related_alert_ids``.

    At least one of the two must be supplied; the API client short-circuits
    to an empty result if neither is provided so we never issue an
    unbounded query upstream.
    """
    asset_id = request.args.get("asset_id")
    alert_id_raw = request.args.get("alert_id")

    if asset_id is not None:
        asset_id = asset_id.strip()
        if not asset_id or not ID_RE.match(asset_id):
            return jsonify({"error": "Invalid asset_id"}), 400

    alert_id_int = None
    if alert_id_raw is not None and alert_id_raw != "":
        if not ID_RE.match(alert_id_raw):
            return jsonify({"error": "Invalid alert_id"}), 400
        try:
            alert_id_int = int(alert_id_raw)
        except ValueError:
            return jsonify({"error": "alert_id must be numeric"}), 400

    offset = _parse_int("offset", 0, lo=0, hi=1_000_000)
    limit = _parse_int("limit", 100, lo=1, hi=500)
    try:
        data = fetch_device_events(
            current_app.instance_path,
            asset_id=asset_id,
            alert_id=alert_id_int,
            offset=offset,
            limit=limit,
        )
        return jsonify(data)
    except SettingsNotConfigured:
        return jsonify({"error": "Settings not configured"}), 503
    except XDomeAPIError as e:
        return jsonify({"error": str(e)}), 502
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/alerts/<alert_id>/devices/<asset_id>/events")
@login_required
def api_device_events(alert_id: str, asset_id: str):
    """Fetch OT activity events scoped to the parent alert and asset.

    The xDome OT activity events endpoint accepts a compound `and` filter,
    so we narrow by both:
      - ``related_alert_ids has_any_in [<alert_id>]``
      - ``dest_asset_id      in          [<asset_id>]``

    Both URL components are required so that selecting a different row in the
    asset/device table re-queries with a different `dest_asset_id`.
    """
    if not ID_RE.match(alert_id):
        abort(400, description="Invalid alert id")
    if not ID_RE.match(asset_id):
        abort(400, description="Invalid asset id")

    # xDome alert IDs are integers; coerce here so the downstream payload is
    # well-typed regardless of how the JS serialised the value.
    try:
        alert_id_int = int(alert_id)
    except ValueError:
        return jsonify({"error": "alert_id must be numeric"}), 400

    offset = _parse_int("offset", 0, lo=0, hi=1_000_000)
    limit = _parse_int("limit", 100, lo=1, hi=500)
    try:
        data = fetch_device_events(
            current_app.instance_path,
            asset_id=asset_id,
            alert_id=alert_id_int,
            offset=offset,
            limit=limit,
        )
        return jsonify(data)
    except SettingsNotConfigured:
        return jsonify({"error": "Settings not configured"}), 503
    except XDomeAPIError as e:
        return jsonify({"error": str(e)}), 502
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# Expose the field catalogues to JS so the column selector on /tree can be
# built dynamically.
@bp.route("/api/meta/fields")
@login_required
def api_meta_fields():
    return jsonify({
        "alerts": ALERT_FIELDS,
        "devices": DEVICE_FIELDS,
        "events": EVENT_FIELDS,
        "default_alert_columns": [
            "id", "alert_type_name", "category", "status",
            "detected_time", "devices_count", "unresolved_devices_count",
            "description",
        ],
        "default_device_columns": [
            "asset_id", "device_name", "device_type", "device_category",
            "ip_list", "site_name", "risk_score", "is_resolved",
        ],
        "default_event_columns": [
            "detection_time", "event_type", "source_ip",
            "source_device_type", "dest_ip", "dest_device_type",
            "protocol", "description",
        ],
    })
