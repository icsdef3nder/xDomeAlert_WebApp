/* Theme picker — multi-theme switcher for xDome Alert Dashboard.
 *
 * Themes are CSS variable sets selected by the `data-theme` attribute on
 * <html>. Dark themes additionally carry the `dark` class on <html>, which
 * enables the structural dark-mode overlay (monospace, neon glow, scanlines)
 * defined in style.css. Light themes set data-theme and remove `dark`.
 *
 * Persistence: localStorage['xdome_theme'] holds the chosen theme id. A
 * one-time migration upgrades the legacy boolean key xdome_dark_mode=1 to
 * the cyber theme so existing dark-mode users don't suddenly land in slate.
 *
 * Note: the pre-paint inline script in base.html applies the theme BEFORE
 * first paint to avoid a flash-of-wrong-theme. This file provides the shared
 * theme registry and the dropdown UI, plus async-loads custom themes from
 * /api/themes (overrides + extras supplied by instance/themes.json).
 */

(function () {
  "use strict";

  // Built-in registry. Mirror of BUILTIN_THEMES in app/themes.py.
  // `accent` is the swatch color shown in the dropdown.
  const THEMES = [
    { id: "slate",    name: "Slate",        mode: "light", accent: "#2563eb" },
    { id: "rose",     name: "Rose Quartz",  mode: "light", accent: "#be185d" },
    { id: "forest",   name: "Forest",       mode: "light", accent: "#15803d" },
    { id: "cyber",    name: "Cyber",        mode: "dark",  accent: "#00d4ff" },
    { id: "midnight", name: "Midnight",     mode: "dark",  accent: "#a78bfa" },
    { id: "carbon",   name: "Carbon",       mode: "dark",  accent: "#ff7a18" },
  ];
  const DEFAULT_THEME = "slate";
  const STORAGE_KEY = "xdome_theme";
  const LEGACY_KEY = "xdome_dark_mode";
  const CUSTOM_STYLE_ID = "xdome-custom-theme-style";

  // CSS variable name / value validation — defence-in-depth even though the
  // backend already validates these. Reject anything we cannot safely emit
  // inside a CSS rule.
  const CSS_VAR_NAME_RE = /^--[A-Za-z][A-Za-z0-9_-]{0,63}$/;
  const CSS_VAR_VALUE_RE = /^[A-Za-z0-9 .,#%()\-/_]{1,128}$/;
  const THEME_ID_RE = /^[a-z][a-z0-9_-]{0,31}$/;
  const THEME_NAME_RE = /^[A-Za-z0-9 _\-]{1,32}$/;

  function getThemeById(id) {
    return THEMES.find((t) => t.id === id) || null;
  }

  // Resolves the active theme id from storage, applying a one-time migration
  // from the legacy xdome_dark_mode boolean. Safe in private-mode browsers
  // where localStorage may throw on access.
  function resolveStoredThemeId() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && getThemeById(stored)) return stored;
      // Legacy migration: dark-mode=1 -> cyber, dark-mode=0 -> slate.
      const legacy = localStorage.getItem(LEGACY_KEY);
      if (legacy !== null) {
        const migrated = legacy === "1" ? "cyber" : "slate";
        try {
          localStorage.setItem(STORAGE_KEY, migrated);
          localStorage.removeItem(LEGACY_KEY);
        } catch (e) { /* ignore */ }
        return migrated;
      }
    } catch (e) { /* localStorage unavailable */ }
    return DEFAULT_THEME;
  }

  function applyTheme(id) {
    const theme = getThemeById(id) || getThemeById(DEFAULT_THEME);
    document.documentElement.setAttribute("data-theme", theme.id);
    document.documentElement.classList.toggle("dark", theme.mode === "dark");
    return theme;
  }

  function persistTheme(id) {
    try { localStorage.setItem(STORAGE_KEY, id); }
    catch (e) { /* quota / private-mode — ignore */ }
  }

  // Expose a small API for the pre-paint script and any future callers.
  window.xdomeTheme = {
    THEMES: THEMES,
    getCurrent: function () {
      const id = document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
      return getThemeById(id) || getThemeById(DEFAULT_THEME);
    },
    resolveStoredThemeId: resolveStoredThemeId,
    apply: function (id) {
      const theme = applyTheme(id);
      persistTheme(theme.id);
      // Dispatch a custom event so the picker (or anyone else) can react.
      try {
        document.dispatchEvent(new CustomEvent("xdome:themechange", {
          detail: { id: theme.id, mode: theme.mode },
        }));
      } catch (e) { /* IE/old-edge — ignore */ }
      return theme;
    },
  };

  // ---------- Custom-theme injection ----------------------------------------

  // Build a single <style> tag containing both `overrides` rules and full
  // `extra`-theme rules. Replacing this single tag lets us re-merge cleanly
  // if /api/themes is ever re-fetched.
  function buildCustomStylesheet(overrides, extras) {
    const blocks = [];

    if (overrides && typeof overrides === "object") {
      for (const themeId of Object.keys(overrides)) {
        if (!THEME_ID_RE.test(themeId)) continue;
        const vars = overrides[themeId];
        if (!vars || typeof vars !== "object") continue;
        const props = renderVarBlock(vars);
        if (props) blocks.push(`[data-theme="${themeId}"] {\n${props}\n}`);
      }
    }

    if (Array.isArray(extras)) {
      for (const t of extras) {
        if (!t || typeof t !== "object") continue;
        if (!THEME_ID_RE.test(t.id || "")) continue;
        const props = renderVarBlock(t.vars || {});
        if (props) blocks.push(`[data-theme="${t.id}"] {\n${props}\n}`);
      }
    }

    return blocks.join("\n\n");
  }

  function renderVarBlock(vars) {
    const lines = [];
    for (const k of Object.keys(vars)) {
      const v = vars[k];
      if (typeof k !== "string" || typeof v !== "string") continue;
      if (!CSS_VAR_NAME_RE.test(k)) continue;
      if (!CSS_VAR_VALUE_RE.test(v)) continue;
      lines.push(`  ${k}: ${v};`);
    }
    return lines.length ? lines.join("\n") : "";
  }

  function injectCustomStylesheet(css) {
    let tag = document.getElementById(CUSTOM_STYLE_ID);
    if (!tag) {
      tag = document.createElement("style");
      tag.id = CUSTOM_STYLE_ID;
      document.head.appendChild(tag);
    }
    tag.textContent = css;
  }

  function mergeExtraThemes(extras) {
    if (!Array.isArray(extras)) return false;
    let added = false;
    for (const t of extras) {
      if (!t || typeof t !== "object") continue;
      if (!THEME_ID_RE.test(t.id || "")) continue;
      if (!THEME_NAME_RE.test(t.name || "")) continue;
      if (t.mode !== "light" && t.mode !== "dark") continue;
      if (typeof t.accent !== "string" || !CSS_VAR_VALUE_RE.test(t.accent)) continue;
      // Skip if a theme with this id is already known.
      if (getThemeById(t.id)) continue;
      THEMES.push({ id: t.id, name: t.name, mode: t.mode, accent: t.accent });
      added = true;
    }
    return added;
  }

  function reconcileActiveTheme() {
    // After merging extras, ensure the `dark` class matches the active
    // theme's mode (the pre-paint script could not know about extras).
    const id = document.documentElement.getAttribute("data-theme");
    const theme = getThemeById(id);
    if (theme) {
      document.documentElement.classList.toggle("dark", theme.mode === "dark");
    }
  }

  function loadCustomThemes() {
    if (!window.fetch) return Promise.resolve(false);
    return fetch("/api/themes", {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    }).then(function (data) {
      const overrides = data && data.overrides ? data.overrides : {};
      const extras = data && data.extra ? data.extra : [];
      const css = buildCustomStylesheet(overrides, extras);
      if (css) injectCustomStylesheet(css);
      const added = mergeExtraThemes(extras);
      reconcileActiveTheme();
      return added;
    }).catch(function (err) {
      try { console.warn("xdome: failed to load /api/themes", err); }
      catch (e) { /* ignore */ }
      return false;
    });
  }

  // ---------- Picker UI ------------------------------------------------------

  function renderPicker(root) {
    const current = window.xdomeTheme.getCurrent();
    const lights = THEMES.filter((t) => t.mode === "light");
    const darks  = THEMES.filter((t) => t.mode === "dark");

    function group(label, items) {
      if (!items.length) return "";
      const rows = items.map((t) => {
        const active = t.id === current.id ? " is-active" : "";
        const check = t.id === current.id
          ? '<span class="theme-check" aria-hidden="true">&#10003;</span>'
          : '';
        // Escape values that we control via the registry — they have already
        // passed regex validation, but belt-and-braces.
        const safeId = String(t.id).replace(/[^a-z0-9_\-]/gi, "");
        const safeAccent = String(t.accent).replace(/[^A-Za-z0-9 .,#%()\-/_]/g, "");
        const safeName = String(t.name).replace(/[<>&"']/g, function (c) {
          return ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" })[c];
        });
        return (
          `<button type="button" class="theme-picker-item${active}" ` +
          `data-theme-id="${safeId}" role="menuitemradio" ` +
          `aria-checked="${t.id === current.id ? 'true' : 'false'}">` +
            `<span class="theme-swatch" style="background:${safeAccent}"></span>` +
            `<span class="theme-picker-name">${safeName}</span>` +
            check +
          `</button>`
        );
      }).join("");
      return (
        `<div class="theme-picker-group-label">${label}</div>` + rows
      );
    }

    const currentSafeAccent = String(current.accent).replace(/[^A-Za-z0-9 .,#%()\-/_]/g, "");
    const currentSafeName = String(current.name).replace(/[<>&"']/g, function (c) {
      return ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" })[c];
    });

    root.innerHTML =
      `<button type="button" class="theme-picker-btn" aria-haspopup="menu" ` +
        `aria-expanded="false" id="theme-picker-btn">` +
        `<span class="theme-swatch" style="background:${currentSafeAccent}"></span>` +
        `<span class="theme-picker-current">${currentSafeName}</span>` +
        `<span class="theme-picker-caret" aria-hidden="true">&#9662;</span>` +
      `</button>` +
      `<div class="theme-picker-menu" id="theme-picker-menu" role="menu" hidden>` +
        group("Light", lights) +
        group("Dark", darks) +
      `</div>`;

    const btn = root.querySelector("#theme-picker-btn");
    const menu = root.querySelector("#theme-picker-menu");

    function openMenu() {
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
    }
    function closeMenu() {
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }

    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (menu.hidden) openMenu(); else closeMenu();
    });

    menu.addEventListener("click", function (ev) {
      const item = ev.target.closest("[data-theme-id]");
      if (!item) return;
      const id = item.getAttribute("data-theme-id");
      window.xdomeTheme.apply(id);
      // Re-render the picker so the active state, swatch and label update.
      renderPicker(root);
    });

    // Click-outside closes the menu. Capture phase so we beat any other
    // stopPropagation handlers further down the tree.
    document.addEventListener("click", function (ev) {
      if (menu.hidden) return;
      if (root.contains(ev.target)) return;
      closeMenu();
    });

    // Escape key closes the menu when it is open.
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !menu.hidden) closeMenu();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    const root = document.getElementById("theme-picker");
    // Render with the built-in registry first so the picker appears
    // immediately even if /api/themes is slow.
    if (root) renderPicker(root);
    // Then merge any custom themes and re-render if extras were added or
    // overrides were applied (overrides may change the active accent).
    loadCustomThemes().then(function () {
      if (root) renderPicker(root);
    });
  });
})();
