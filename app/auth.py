"""
Linux PAM authentication and group-based role mapping.

Authentication flow:
1. User submits username/password to ``/login``.
2. ``authenticate_pam`` validates against the local PAM stack
   (service ``login`` by default — see /etc/pam.d/login).
3. ``get_user_role`` resolves the role from POSIX group membership:
       group ``xdome-admin``  -> "admin"
       group ``xdome-view``   -> "viewer"
       neither               -> None (login denied)
   Root (uid 0) is implicitly admin.
4. The Flask session stores ``"<username>:<role>"`` as the user_id;
   on each request ``load_user`` reconstructs a ``User`` instance.

Security controls (OWASP A07 — Identification & Authentication Failures):
- We never store or echo the password; PAM does the verification in C land.
- Failed login events are logged in JSON for SIEM correlation
  (event_type=login, outcome=failure, source_ip).
- ``python-pam`` is imported lazily at call time so the app can still start
  on hosts where ``libpam`` is missing — but a clear startup warning is
  emitted from ``app/__init__.py``.
"""
from __future__ import annotations

import grp
import json
import logging
import pwd
from typing import Optional

logger = logging.getLogger(__name__)

ADMIN_GROUP = "xdome-admin"
VIEW_GROUP = "xdome-view"
DEFAULT_PAM_SERVICE = "login"


# ---- Optional dependency: python-pam ---------------------------------------
# We attempt the import once at module load. If the library or libpam itself
# is unavailable, ``authenticate_pam`` returns False and logs a clear error
# rather than crashing the whole app.
try:
    import pam as _pam_module  # type: ignore
    PAM_AVAILABLE = True
    _PAM_IMPORT_ERROR: Optional[str] = None
except Exception as e:  # ImportError or libpam linkage failure
    _pam_module = None
    PAM_AVAILABLE = False
    _PAM_IMPORT_ERROR = str(e)
    logger.warning(json.dumps({
        "event_type": "pam_unavailable",
        "error": _PAM_IMPORT_ERROR,
    }))


def authenticate_pam(username: str, password: str,
                     service: str = DEFAULT_PAM_SERVICE) -> bool:
    """Authenticate a user against the local PAM stack.

    Returns True on success, False otherwise. Never raises — failures are
    logged structurally so PAM transport errors are visible in the SIEM but
    do not leak to the HTTP response.
    """
    if not PAM_AVAILABLE or _pam_module is None:
        logger.error(json.dumps({
            "event_type": "pam_error",
            "outcome": "module_unavailable",
            "error": _PAM_IMPORT_ERROR or "python-pam not importable",
        }))
        return False

    if not isinstance(username, str) or not isinstance(password, str):
        return False
    if not username or len(username) > 64:
        return False
    if len(password) > 4096:
        return False
    # POSIX usernames: alnum, dot, underscore, dash. Reject anything else
    # so we can never feed a control character through to PAM.
    for ch in username:
        if not (ch.isalnum() or ch in "._-"):
            return False

    try:
        p = _pam_module.pam()
        return bool(p.authenticate(username, password, service=service))
    except Exception as e:  # noqa: BLE001 — PAM can raise broad errors
        logger.error(json.dumps({
            "event_type": "pam_error",
            "outcome": "exception",
            "error": str(e),
        }))
        return False


def get_user_role(username: str) -> Optional[str]:
    """Resolve a POSIX user's xDome role.

    Returns "admin", "viewer", or None (deny login).

    Group resolution checks both supplementary membership (``gr_mem``) and
    primary GID, so a user whose primary group is xdome-admin still qualifies.
    """
    try:
        pw = pwd.getpwnam(username)
    except KeyError:
        return None

    # Root is always admin.
    if pw.pw_uid == 0:
        return "admin"

    # Admin first — wins over viewer if a user is in both.
    try:
        admin_grp = grp.getgrnam(ADMIN_GROUP)
        if username in admin_grp.gr_mem or pw.pw_gid == admin_grp.gr_gid:
            return "admin"
    except KeyError:
        # Group not provisioned yet; not fatal.
        pass

    try:
        view_grp = grp.getgrnam(VIEW_GROUP)
        if username in view_grp.gr_mem or pw.pw_gid == view_grp.gr_gid:
            return "viewer"
    except KeyError:
        pass

    return None
