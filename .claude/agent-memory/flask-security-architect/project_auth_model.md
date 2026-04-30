---
name: Auth model — PAM + POSIX groups
description: Login authenticates against Linux PAM; role mapped from POSIX group membership. Replaces the prior single-password SETTINGS_PASSWORD.
type: project
---

Authentication is delegated to the local Linux PAM stack via ``python-pam`` (service ``login``). The session stores the user id encoded as ``"<username>:<role>"``; ``app.load_user`` reconstructs a ``User`` instance from it on each request.

Roles are resolved from POSIX group membership in ``app/auth.py``:
- ``xdome-admin`` (or root, uid 0) → "admin"
- ``xdome-view`` → "viewer"
- neither → login denied.

**Why:** The prior model relied on a single shared ``SETTINGS_PASSWORD``, which has no audit trail per user and conflicts with the SOC requirement that every console action be attributable to a named operator. Mapping to local Linux groups means provisioning is handled by existing IT processes and SSH-equivalent access policies.

**How to apply:** When adding a new sensitive route, gate it with ``@admin_required`` (for write/config actions) or ``@login_required`` (any authenticated user). Never reintroduce ``SETTINGS_PASSWORD`` or any shared credential. The PAM module is imported lazily — if ``libpam`` is missing the app starts but logs a startup warning and every login fails closed.
