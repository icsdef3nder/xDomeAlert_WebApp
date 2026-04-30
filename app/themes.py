"""
Theme override loader.

Admins may customise/extend the built-in themes by placing a `themes.json`
file in the Flask `instance/` directory. The file format::

    {
      "overrides": {
        "<theme-id>": { "--cssvar": "<value>", ... }
      },
      "extra": [
        {
          "id":     "<theme-id>",
          "name":   "<Display Name>",
          "mode":   "light" | "dark",
          "accent": "#hexcolor",
          "vars":   { "--bg": "...", ... }
        }
      ]
    }

This module deliberately lives outside ``api_client.py`` so the xDome API
client stays focused on the upstream API and never reads UI-only files.

Security notes (OWASP A05 — Security Misconfiguration):
- The JSON file is parsed with ``json.load`` (safe). On parse error we log
  a warning and return empty defaults rather than crashing the request.
- The endpoint that serves this data is read-only and public; CSS variable
  values are returned as JSON and rendered as a server-built ``<style>``
  string in the browser by ``theme.js``. We DO sanitise CSS variable
  values to a tight character class to prevent CSS injection / breakout.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

THEMES_FILENAME = "themes.json"

# CSS variable name: must start with -- and contain only safe identifier chars.
_CSS_VAR_NAME_RE = re.compile(r"^--[A-Za-z][A-Za-z0-9_-]{0,63}$")
# CSS variable VALUE: limit to a permissive but bounded set; explicitly forbid
# braces, semicolons and angle brackets which would let an attacker break out
# of the rule and inject arbitrary CSS or HTML.
_CSS_VAR_VALUE_RE = re.compile(r"^[A-Za-z0-9 .,#%()\-/_]{1,128}$")

_VALID_MODES = {"light", "dark"}
_THEME_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_THEME_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,32}$")


def _themes_path(instance_path: str | Path) -> Path:
    return Path(instance_path) / THEMES_FILENAME


def _sanitise_vars(raw: Any) -> dict[str, str]:
    """Return only well-formed --name: value pairs from raw input."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if not _CSS_VAR_NAME_RE.match(k):
            logger.warning(json.dumps({
                "event_type": "themes_invalid_var_name",
                "name": k[:64],
            }))
            continue
        if not _CSS_VAR_VALUE_RE.match(v):
            logger.warning(json.dumps({
                "event_type": "themes_invalid_var_value",
                "name": k,
            }))
            continue
        out[k] = v
    return out


def _sanitise_extra(items: Any) -> list[dict[str, Any]]:
    """Validate the shape of each extra-theme entry."""
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id")
        name = entry.get("name")
        mode = entry.get("mode")
        accent = entry.get("accent")
        vars_ = entry.get("vars")
        if not (isinstance(tid, str) and _THEME_ID_RE.match(tid)):
            continue
        if not (isinstance(name, str) and _THEME_NAME_RE.match(name)):
            continue
        if mode not in _VALID_MODES:
            continue
        if not (isinstance(accent, str) and _CSS_VAR_VALUE_RE.match(accent)):
            continue
        clean_vars = _sanitise_vars(vars_)
        if not clean_vars:
            # An extra theme with no usable variables wouldn't render; skip.
            continue
        out.append({
            "id": tid,
            "name": name,
            "mode": mode,
            "accent": accent,
            "vars": clean_vars,
        })
    return out


def load_custom_themes(instance_path: str | Path) -> dict[str, Any]:
    """Read instance/themes.json and return validated overrides + extras.

    Always returns ``{"overrides": {...}, "extra": [...]}``.
    Missing file → empty defaults. Invalid JSON → warning + empty defaults.
    """
    p = _themes_path(instance_path)
    if not p.exists():
        return {"overrides": {}, "extra": []}

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(json.dumps({
            "event_type": "themes_load_error",
            "outcome": "parse_error",
            "error": str(e),
        }))
        return {"overrides": {}, "extra": []}

    if not isinstance(raw, dict):
        logger.warning(json.dumps({
            "event_type": "themes_load_error",
            "outcome": "wrong_root_type",
        }))
        return {"overrides": {}, "extra": []}

    overrides_raw = raw.get("overrides", {}) or {}
    overrides_clean: dict[str, dict[str, str]] = {}
    if isinstance(overrides_raw, dict):
        for tid, vars_ in overrides_raw.items():
            if not (isinstance(tid, str) and _THEME_ID_RE.match(tid)):
                continue
            cleaned = _sanitise_vars(vars_)
            if cleaned:
                overrides_clean[tid] = cleaned

    extras_clean = _sanitise_extra(raw.get("extra", []))

    return {"overrides": overrides_clean, "extra": extras_clean}


_EXAMPLE_THEMES_FILE = """{
  "_comment": "xDome Alert Dashboard — custom theme overrides. Remove the underscore-prefixed keys to activate. See app/themes.py for the full schema. CSS variable values are restricted to a safe character set; semicolons and braces are forbidden.",
  "overrides": {
    "_cyber": {
      "--accent": "#00ff88",
      "--accent-rgb": "0, 255, 136"
    }
  },
  "extra": [
    {
      "_id": "oceanic",
      "_name": "Oceanic",
      "_mode": "dark",
      "_accent": "#00bcd4",
      "_vars": {
        "--bg": "#0a1628",
        "--page-bg": "#0d1f38",
        "--panel": "#112240",
        "--panel-alt": "#1a2f52",
        "--ink": "#ccd6f6",
        "--muted": "#8892b0",
        "--border": "#1e3a5f",
        "--accent": "#00bcd4",
        "--accent-ink": "#001f30",
        "--accent-rgb": "0, 188, 212",
        "--hover": "#162840",
        "--selected": "rgba(0, 188, 212, 0.13)",
        "--danger-bg": "#2a0d14",
        "--danger-ink": "#ff5577",
        "--danger-rgb": "255, 85, 119",
        "--danger-hover": "rgba(255, 60, 90, 0.15)",
        "--danger-selected": "rgba(255, 60, 90, 0.22)",
        "--warn-bg": "#2a230d",
        "--warn-ink": "#fbbf24",
        "--info-bg": "#0d1d2a",
        "--table-row": "#102035",
        "--table-alt": "#132540"
      }
    }
  ]
}
"""


def save_example_themes(instance_path: str | Path) -> None:
    """Write a commented example themes.json if it doesn't already exist.

    Idempotent — never overwrites an existing file.
    """
    p = _themes_path(instance_path)
    if p.exists():
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            f.write(_EXAMPLE_THEMES_FILE)
        # File mode 0644 is fine here — themes are not sensitive.
        logger.info(json.dumps({
            "event_type": "themes_example_written",
            "path": str(p),
        }))
    except OSError as e:
        logger.warning(json.dumps({
            "event_type": "themes_example_write_failed",
            "error": str(e),
        }))


# Built-in registry. Kept in sync with theme.js / style.css. Returned to the
# frontend so it has a single source of truth for theme metadata even if the
# inline JS list ever drifts.
BUILTIN_THEMES = [
    {"id": "slate",    "name": "Slate",       "mode": "light", "accent": "#2563eb"},
    {"id": "rose",     "name": "Rose Quartz", "mode": "light", "accent": "#be185d"},
    {"id": "forest",   "name": "Forest",      "mode": "light", "accent": "#15803d"},
    {"id": "cyber",    "name": "Cyber",       "mode": "dark",  "accent": "#00d4ff"},
    {"id": "midnight", "name": "Midnight",    "mode": "dark",  "accent": "#a78bfa"},
    {"id": "carbon",   "name": "Carbon",      "mode": "dark",  "accent": "#ff7a18"},
]


def build_theme_payload(instance_path: str | Path) -> dict[str, Any]:
    """Return the merged payload exposed by the /api/themes route."""
    custom = load_custom_themes(instance_path)
    return {
        "builtin": BUILTIN_THEMES,
        "overrides": custom["overrides"],
        "extra": custom["extra"],
    }
