"""
xDome API client.

All calls:
- Use HTTPS Bearer auth (token kept server-side, never sent to browser).
- Validate the configured Base URI scheme is http(s) only (defence-in-depth
  against SSRF if an attacker could write to settings.json).
- Have a 30-second timeout (tenacity-friendly default).
- Emit structured logs for SIEM correlation.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Default fields per endpoint. The backend always requests *all* available
# fields it knows about so the frontend column selector can toggle without
# re-querying the API.
ALERT_FIELDS = [
    "id", "alert_name", "alert_type_name", "alert_class", "category",
    "detected_time", "updated_time", "devices_count",
    "unresolved_devices_count", "medical_devices_count", "iot_devices_count",
    "it_devices_count", "ot_devices_count", "status",
    "mitre_technique_ics_ids", "mitre_technique_ics_names",
    "mitre_technique_enterprise_ids", "mitre_technique_enterprise_names",
    "description", "malicious_ip_tags_list",
]

DEVICE_FIELDS = [
    "asset_id", "uid", "device_name", "device_type", "device_category",
    "ip_list", "mac_list", "network_list", "site_name", "risk_score",
    "is_resolved", "manufacturer", "model", "os_name", "os_version",
    "vlan_list", "labels", "assignees", "retired",
]

# Field order intentionally matches the xDome OT activity events example so
# request payloads remain easy to diff against the upstream reference.
EVENT_FIELDS = [
    "event_id", "detection_time", "event_type", "related_alert_ids",
    "description", "source_ip", "source_port", "source_asset_id",
    "source_device_name", "source_device_type", "source_username",
    "source_site_name", "source_network", "dest_ip", "dest_port",
    "dest_asset_id", "dest_device_name", "dest_device_type", "dest_site_name",
    "dest_network", "protocol", "ip_protocol", "mode",
]


class SettingsNotConfigured(Exception):
    """Raised when Base URI / API token are missing."""


class XDomeAPIError(Exception):
    """Wraps upstream xDome failures so routes can return clean 502s."""


# --- Settings persistence -----------------------------------------------------

def _settings_path(instance_path: str) -> Path:
    return Path(instance_path) / "settings.json"


def load_settings(instance_path: str) -> dict:
    """Read settings.json or return empty dict if absent."""
    p = _settings_path(instance_path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load settings.json")
        return {}


def save_settings(instance_path: str, base_uri: str, api_token: str) -> None:
    """Write settings atomically and chmod 600 (token is sensitive)."""
    parsed = urlparse(base_uri)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Base URI must use http or https scheme")
    if not parsed.netloc:
        raise ValueError("Base URI must include a host")

    p = _settings_path(instance_path)
    tmp = p.with_suffix(".tmp")
    payload = {"base_uri": base_uri.rstrip("/"), "api_token": api_token}
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    logger.info(json.dumps({
        "event_type": "settings_updated",
        "outcome": "success",
        "base_uri": base_uri,
    }))


# --- HTTP helpers -------------------------------------------------------------

def _require_settings(instance_path: str) -> tuple[str, str]:
    s = load_settings(instance_path)
    base_uri = s.get("base_uri")
    token = s.get("api_token")
    if not base_uri or not token:
        raise SettingsNotConfigured("Base URI or API token not set")
    return base_uri, token


def _post(instance_path: str, path: str, body: dict, timeout: int = 30) -> dict:
    base_uri, token = _require_settings(instance_path)
    url = f"{base_uri}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.info(json.dumps({
        "event_type": "xdome_api_call",
        "method": "POST",
        "path": path,
        "fields_count": len(body.get("fields") or []),
    }))

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        logger.error(json.dumps({
            "event_type": "xdome_api_error",
            "path": path,
            "outcome": "network_error",
            "error": str(e),
        }))
        raise XDomeAPIError(f"Network error contacting xDome: {e}") from e

    if resp.status_code >= 400:
        logger.warning(json.dumps({
            "event_type": "xdome_api_error",
            "path": path,
            "status_code": resp.status_code,
            "outcome": "http_error",
        }))
        raise XDomeAPIError(
            f"xDome returned HTTP {resp.status_code}: {resp.text[:300]}"
        )

    try:
        return resp.json()
    except ValueError as e:
        raise XDomeAPIError("xDome returned non-JSON response") from e


# --- Public methods -----------------------------------------------------------

def fetch_alerts(
    instance_path: str,
    *,
    only_unresolved: bool = False,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    body: dict[str, Any] = {
        "fields": ALERT_FIELDS,
        "offset": offset,
        "limit": limit,
        "include_count": True,
        "sort_by": [{"field": "detected_time", "order": "desc"}],
    }
    if only_unresolved:
        body["filter_by"] = {
            "field": "status",
            "operation": "in",
            "value": ["Unresolved"],
        }
    return _post(instance_path, "/api/v1/alerts/", body)


def fetch_alert_devices(
    instance_path: str,
    alert_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    # Defence-in-depth: alert_id should be UUID-ish; reject embedded slashes
    # which would alter the URL path.
    if "/" in alert_id or ".." in alert_id:
        raise ValueError("Invalid alert_id")
    body = {
        "fields": DEVICE_FIELDS,
        "offset": offset,
        "limit": limit,
        "include_count": True,
    }
    return _post(instance_path, f"/api/v1/alerts/{alert_id}/devices", body)


def fetch_device_events(
    instance_path: str,
    *,
    asset_id: Optional[str] = None,
    alert_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Fetch OT activity events optionally scoped by alert and/or asset.

    The xDome `/api/v1/ot_activity_events/` endpoint accepts a compound
    `filter_by` of the form::

        {
          "operation": "and",
          "operands": [
            {"field": "related_alert_ids", "operation": "has_any_in", "value": [<int>]},
            {"field": "dest_asset_id",     "operation": "in",         "value": ["<str>"]}
          ]
        }

    Either operand may be omitted independently:
      - If ``alert_id`` is provided, the ``related_alert_ids`` operand is
        added (coerced to int — alert IDs are numeric in xDome).
      - If ``asset_id`` is provided, the ``dest_asset_id`` operand is added
        (asset IDs are opaque strings such as ``"GOPPXOR"``).

    If both are omitted we short-circuit with an empty result rather than
    issuing an unbounded query against the upstream API.
    """
    operands: list[dict] = []

    if alert_id is not None:
        try:
            alert_id_int = int(alert_id)
        except (TypeError, ValueError) as e:
            raise ValueError("alert_id must be numeric") from e
        operands.append({
            "field": "related_alert_ids",
            "operation": "has_any_in",
            "value": [alert_id_int],
        })

    if asset_id is not None:
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("asset_id must be a non-empty string")
        operands.append({
            "field": "dest_asset_id",
            "operation": "in",
            "value": [asset_id],
        })

    if not operands:
        logger.info(json.dumps({
            "event_type": "xdome_api_skip",
            "path": "/api/v1/ot_activity_events/",
            "outcome": "no_filter_provided",
        }))
        return {"ot_activity_events": [], "count": 0}

    body: dict[str, Any] = {
        "fields": EVENT_FIELDS,
        "offset": offset,
        "limit": limit,
        "include_count": True,
        "sort_by": [{"field": "detection_time", "order": "desc"}],
        "filter_by": {
            "operation": "and",
            "operands": operands,
        },
    }
    return _post(instance_path, "/api/v1/ot_activity_events/", body)
