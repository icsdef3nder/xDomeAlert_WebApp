---
name: Custom theme overlay
description: Themes are extensible via instance/themes.json (overrides + extras). Loaded by app/themes.py and served by GET /api/themes.
type: project
---

Built-in themes (slate / rose / forest / cyber / midnight / carbon) are defined in three places that must stay in sync: ``app/static/css/style.css`` (CSS variables), ``app/static/js/theme.js`` (registry for the picker), and ``BUILTIN_THEMES`` in ``app/themes.py`` (the metadata returned by ``/api/themes``). Custom overrides and extra themes live in ``instance/themes.json`` and are sanitised in ``app/themes.py`` against tight regexes for variable names and values before being injected into the page as a single ``<style>`` tag with id ``xdome-custom-theme-style``.

**Why:** Operators wanted to brand the dashboard per deployment without redeploying the Python package. Putting the file in ``instance/`` keeps it outside version control and lets each install own it.

**How to apply:** When changing a built-in theme's variables, update style.css AND ``BUILTIN_THEMES`` accent colour if it changed AND the inline registry in theme.js. When adding new theme-controlled CSS variables, also extend the safe variable name/value allow-lists in ``app/themes.py`` and the mirror regexes in ``theme.js`` so custom themes can supply them. Never widen the allowed CSS-value charset to include ``;`` or ``{}`` — that would enable CSS injection.
