/* Overview page: 3-tier table drill-down (alerts -> devices -> events).
 *
 * Design notes:
 * - All data flows through the Flask proxy; we never call xDome directly.
 * - Column visibility persists in localStorage per table; the data set
 *   we fetch from the server already includes ALL fields, so toggling
 *   columns is a pure render operation.
 * - Search is client-side over visible columns (case-insensitive substring).
 * - Sorting is client-side on already-fetched rows; we never re-fetch when
 *   the sort changes. Sort state is reset on every fresh load() so the
 *   server-supplied row order is what users see by default.
 */

(function () {
  "use strict";

  const META = window.__XDOME_META__ || { fields: {}, defaults: {} };

  // ---------- generic helpers ------------------------------------------------

  function escapeHtml(v) {
    if (v === null || v === undefined) return "";
    if (Array.isArray(v)) return v.map(escapeHtml).join(", ");
    if (typeof v === "object") return escapeHtml(JSON.stringify(v));
    return String(v).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function getCols(name) {
    try {
      const raw = localStorage.getItem(`${name}_columns`);
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr) && arr.length) return arr;
      }
    } catch (e) { /* fall through */ }
    return META.defaults[name] || [];
  }

  function setCols(name, cols) {
    localStorage.setItem(`${name}_columns`, JSON.stringify(cols));
  }

  async function fetchJSON(url) {
    const resp = await fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    let body = null;
    try { body = await resp.json(); } catch (e) { /* non-JSON */ }
    if (!resp.ok) {
      const msg = (body && body.error) || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return body || {};
  }

  // ---------- sorting --------------------------------------------------------

  // Strict ISO 8601 detector: yyyy-mm-dd, optionally with a time + offset.
  // Used by classifyValue() to route date-like strings to chronological sort
  // instead of falling back to lexicographic compare.
  const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

  // Classifies a value into a (kind, sortable) tuple. The sort comparator
  // uses `kind` to decide how to compare and `sortable` as the actual key.
  // Null/undefined fall into kind "nullish" which always sorts last.
  function classifyValue(v) {
    if (v === null || v === undefined || v === "") {
      return { kind: "nullish", key: null };
    }
    if (typeof v === "number" && !Number.isNaN(v)) {
      return { kind: "number", key: v };
    }
    if (typeof v === "boolean") {
      return { kind: "number", key: v ? 1 : 0 };
    }
    if (typeof v === "string") {
      // Numeric string -> number sort. Use a strict regex so "10 Mbps" stays
      // a string and "00123" still compares as 123.
      if (/^-?\d+(\.\d+)?$/.test(v)) {
        const n = Number(v);
        if (!Number.isNaN(n)) return { kind: "number", key: n };
      }
      if (ISO_DATE_RE.test(v)) {
        const t = Date.parse(v);
        if (!Number.isNaN(t)) return { kind: "date", key: t };
      }
      return { kind: "string", key: v.toLowerCase() };
    }
    if (Array.isArray(v)) {
      return { kind: "string", key: v.join(", ").toLowerCase() };
    }
    if (typeof v === "object") {
      try { return { kind: "string", key: JSON.stringify(v).toLowerCase() }; }
      catch (e) { return { kind: "string", key: String(v).toLowerCase() }; }
    }
    return { kind: "string", key: String(v).toLowerCase() };
  }

  function compareValues(a, b) {
    const ca = classifyValue(a);
    const cb = classifyValue(b);
    // Nullish always sorts last regardless of direction (we apply the
    // direction flip only to non-nullish pairs in sortRows).
    if (ca.kind === "nullish" && cb.kind === "nullish") return 0;
    if (ca.kind === "nullish") return 1;
    if (cb.kind === "nullish") return -1;
    // If kinds disagree (e.g. number vs string), fall back to string
    // compare on the original values so the result is at least stable.
    if (ca.kind !== cb.kind) {
      const sa = String(ca.key).toLowerCase();
      const sb = String(cb.key).toLowerCase();
      return sa < sb ? -1 : sa > sb ? 1 : 0;
    }
    if (ca.key < cb.key) return -1;
    if (ca.key > cb.key) return 1;
    return 0;
  }

  function sortRows(rows, col, dir) {
    if (!col || (dir !== "asc" && dir !== "desc")) return rows;
    const sign = dir === "asc" ? 1 : -1;
    // Copy first so the underlying state.rows order (server order) is
    // preserved for "no sort" or for paging extra rows in.
    const copy = rows.slice();
    copy.sort((ra, rb) => {
      const va = ra ? ra[col] : undefined;
      const vb = rb ? rb[col] : undefined;
      const ca = classifyValue(va);
      const cb = classifyValue(vb);
      // Nullish always sorts last — do not multiply by `sign`.
      if (ca.kind === "nullish" && cb.kind === "nullish") return 0;
      if (ca.kind === "nullish") return 1;
      if (cb.kind === "nullish") return -1;
      return sign * compareValues(va, vb);
    });
    return copy;
  }

  // ---------- per-table state ------------------------------------------------

  function makeTable(opts) {
    /* opts:
         name: "alerts" | "devices" | "events"
         tableEl, theadEl, tbodyEl
         statusEl, loadMoreBtn, searchInput, colsBtn, colsPanel
         rowKeyField (string)
         danger: (row) => bool
         onRowClick(row)
         fetcher(offset, limit) -> Promise<{rows, count}>
    */
    const state = {
      cols: getCols(opts.name),
      rows: [],
      total: null,
      offset: 0,
      limit: 100,
      selectedKey: null,
      search: "",
      // Sort is client-side on state.rows only; cleared when rows reset.
      sortCol: null,
      sortDir: null, // "asc" | "desc" | null
    };

    function indicatorFor(col) {
      if (state.sortCol !== col) return "";
      if (state.sortDir === "asc")
        return ' <span class="sort-indicator" aria-hidden="true">&#9650;</span>';
      if (state.sortDir === "desc")
        return ' <span class="sort-indicator" aria-hidden="true">&#9660;</span>';
      return "";
    }

    function ariaSortFor(col) {
      if (state.sortCol !== col) return "none";
      if (state.sortDir === "asc") return "ascending";
      if (state.sortDir === "desc") return "descending";
      return "none";
    }

    function render() {
      const cols = state.cols;
      // Header — clickable cells with sort indicators.
      opts.theadEl.innerHTML =
        "<tr>" + cols.map((c) =>
          `<th data-sort-col="${escapeHtml(c)}" aria-sort="${ariaSortFor(c)}">` +
            `${escapeHtml(c)}${indicatorFor(c)}` +
          `</th>`
        ).join("") + "</tr>";

      // Sort first (over a copy so server order is preserved for paging),
      // then filter for search.
      const sorted = sortRows(state.rows, state.sortCol, state.sortDir);
      const q = state.search.trim().toLowerCase();
      const visibleRows = q
        ? sorted.filter((r) =>
            cols.some((c) => {
              const v = r[c];
              if (v === null || v === undefined) return false;
              const s = Array.isArray(v) ? v.join(", ") : (typeof v === "object" ? JSON.stringify(v) : String(v));
              return s.toLowerCase().includes(q);
            })
          )
        : sorted;

      if (!visibleRows.length) {
        opts.tbodyEl.innerHTML = `<tr><td class="empty" colspan="${cols.length || 1}">No rows.</td></tr>`;
      } else {
        opts.tbodyEl.innerHTML = visibleRows.map((r, idx) => {
          const key = opts.rowKeyField ? r[opts.rowKeyField] : idx;
          const danger = opts.danger && opts.danger(r) ? " row-danger" : "";
          const selected = (state.selectedKey !== null && key === state.selectedKey) ? " selected" : "";
          const cells = cols.map((c) => `<td title="${escapeHtml(r[c])}">${escapeHtml(r[c])}</td>`).join("");
          return `<tr data-key="${escapeHtml(key)}" class="${(danger + selected).trim()}">${cells}</tr>`;
        }).join("");
      }

      const loaded = state.rows.length;
      const total = state.total != null ? state.total : "?";
      opts.statusEl.textContent = `Showing ${visibleRows.length} of ${loaded} loaded (${total} total)`;
      if (opts.loadMoreBtn) {
        const more = state.total != null ? loaded < state.total : false;
        opts.loadMoreBtn.hidden = !more;
      }
    }

    // Sort cycle: unsorted -> asc -> desc -> unsorted -> ...
    function cycleSort(col) {
      if (state.sortCol !== col) {
        state.sortCol = col;
        state.sortDir = "asc";
      } else if (state.sortDir === "asc") {
        state.sortDir = "desc";
      } else if (state.sortDir === "desc") {
        state.sortCol = null;
        state.sortDir = null;
      } else {
        state.sortDir = "asc";
      }
    }

    // Pending-selection set for the column picker. We stage edits here and
    // only commit to state.cols (and persist) when the user clicks "Apply".
    // This avoids re-rendering the whole table on every checkbox toggle and
    // gives the user a clear "click Apply to confirm" UX.
    let pendingCols = null;

    // ----- Column picker drag-to-reorder helpers -----------------------------
    // We let users reorder columns by dragging the labels in the picker grid.
    // Only the *checked* columns are reorder-able among themselves; unchecked
    // columns stay parked in the field-catalogue order at the bottom (i.e. in
    // the order they appear in META.fields[opts.name]). On drop we splice the
    // dragged id to its new position within the checked set, then reflow the
    // grid so checked items appear in the new order followed by unchecked.

    function isChecked(field) {
      return pendingCols.includes(field);
    }

    function reorderPending(srcField, targetField) {
      if (!isChecked(srcField) || !isChecked(targetField)) return;
      if (srcField === targetField) return;
      const next = pendingCols.filter((c) => c !== srcField);
      const targetIdx = next.indexOf(targetField);
      if (targetIdx < 0) return;
      // Drop the dragged item *before* the target — the user dragged the
      // source onto the target, so we treat the target as the new neighbour.
      next.splice(targetIdx, 0, srcField);
      pendingCols = next;
    }

    function reflowPickerGrid(grid) {
      // Order: checked items in pendingCols order, then unchecked in
      // META.fields[opts.name] order. Only the DOM order changes — we
      // already updated pendingCols above.
      const all = META.fields[opts.name] || [];
      const labels = Array.from(grid.querySelectorAll("label[data-field]"));
      const byField = new Map(labels.map((el) => [el.getAttribute("data-field"), el]));
      const ordered = [];
      pendingCols.forEach((f) => { if (byField.has(f)) ordered.push(byField.get(f)); });
      all.forEach((f) => {
        if (!pendingCols.includes(f) && byField.has(f)) ordered.push(byField.get(f));
      });
      ordered.forEach((el) => grid.appendChild(el));
    }

    function wireDragHandlers(label, grid) {
      label.addEventListener("dragstart", (ev) => {
        const field = label.getAttribute("data-field");
        if (!isChecked(field)) {
          // Unchecked items aren't draggable to other positions. Cancel
          // the drag so the user gets the not-allowed cursor.
          ev.preventDefault();
          return;
        }
        label.classList.add("dragging");
        ev.dataTransfer.effectAllowed = "move";
        try { ev.dataTransfer.setData("text/plain", field); }
        catch (e) { /* IE compatibility — value preserved via closure below */ }
        // Stash on the grid as a fallback in case dataTransfer access is
        // blocked (some browsers restrict reads during dragover).
        grid.__draggingField = field;
      });
      label.addEventListener("dragend", () => {
        label.classList.remove("dragging");
        grid.querySelectorAll("label.drag-over").forEach((el) =>
          el.classList.remove("drag-over"));
        grid.__draggingField = null;
      });
      label.addEventListener("dragover", (ev) => {
        const src = grid.__draggingField;
        const targetField = label.getAttribute("data-field");
        if (!src || !isChecked(targetField) || src === targetField) return;
        ev.preventDefault(); // allow drop
        ev.dataTransfer.dropEffect = "move";
        label.classList.add("drag-over");
      });
      label.addEventListener("dragleave", () => {
        label.classList.remove("drag-over");
      });
      label.addEventListener("drop", (ev) => {
        ev.preventDefault();
        const targetField = label.getAttribute("data-field");
        let srcField = grid.__draggingField;
        if (!srcField) {
          try { srcField = ev.dataTransfer.getData("text/plain"); }
          catch (e) { srcField = null; }
        }
        label.classList.remove("drag-over");
        if (!srcField) return;
        reorderPending(srcField, targetField);
        reflowPickerGrid(grid);
      });
    }

    function renderColsPanel() {
      const all = META.fields[opts.name] || [];
      // Snapshot current cols into the pending set every time we open the
      // panel. Closing and reopening discards uncommitted edits.
      pendingCols = state.cols.slice();

      // Render checked items first (in pendingCols order), then unchecked
      // (in field-catalogue order). This matches the post-drop reflow logic
      // in reflowPickerGrid() so the panel opens consistent on every open.
      const ordered = pendingCols.slice();
      all.forEach((f) => { if (!pendingCols.includes(f)) ordered.push(f); });

      const grid = ordered.map((f) => {
        const checked = pendingCols.includes(f) ? "checked" : "";
        return (
          `<label data-field="${escapeHtml(f)}" draggable="true">` +
            `<span class="drag-handle" aria-hidden="true">&#x2630;</span>` +
            `<input type="checkbox" data-field="${escapeHtml(f)}" ${checked}>` +
            `${escapeHtml(f)}` +
          `</label>`
        );
      }).join("");

      opts.colsPanel.innerHTML =
        `<div class="cols-panel-grid">${grid}</div>` +
        `<div class="cols-panel-actions">` +
          `<button type="button" class="btn" data-cols-action="close">&#10005; Close</button>` +
          `<button type="button" class="btn btn-primary" data-cols-action="apply">Apply</button>` +
        `</div>`;

      const gridEl = opts.colsPanel.querySelector(".cols-panel-grid");

      opts.colsPanel.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        cb.addEventListener("change", () => {
          const field = cb.getAttribute("data-field");
          if (cb.checked) {
            if (!pendingCols.includes(field)) pendingCols.push(field);
          } else {
            pendingCols = pendingCols.filter((c) => c !== field);
          }
          // Newly-unchecked items must drift to the bottom; newly-checked
          // ones to the end of the checked list. Reflow does both for us.
          if (gridEl) reflowPickerGrid(gridEl);
        });
        // Stop the click from bubbling to the label drag handlers — clicks
        // on the checkbox itself shouldn't initiate a drag.
        cb.addEventListener("mousedown", (ev) => ev.stopPropagation());
      });

      // Wire HTML5 drag-and-drop on every label. Only checked labels will
      // actually accept or originate drags; the dragstart handler aborts
      // for unchecked items.
      if (gridEl) {
        gridEl.querySelectorAll("label[data-field]").forEach((lbl) =>
          wireDragHandlers(lbl, gridEl));
      }

      const applyBtn = opts.colsPanel.querySelector('[data-cols-action="apply"]');
      if (applyBtn) {
        applyBtn.addEventListener("click", () => {
          // Commit pending -> state, persist, re-render, close.
          // pendingCols already reflects the drag order, so the user's
          // reordered columns flow straight into the table.
          state.cols = pendingCols.slice();
          setCols(opts.name, state.cols);
          // If the active sort column was just hidden, drop the sort.
          if (state.sortCol && !state.cols.includes(state.sortCol)) {
            state.sortCol = null;
            state.sortDir = null;
          }
          render();
          opts.colsPanel.hidden = true;
        });
      }
      const closeBtn = opts.colsPanel.querySelector('[data-cols-action="close"]');
      if (closeBtn) {
        closeBtn.addEventListener("click", () => {
          // Discard pending edits — just hide.
          opts.colsPanel.hidden = true;
        });
      }
    }

    async function load(reset) {
      if (reset) {
        state.rows = [];
        state.offset = 0;
        state.total = null;
        state.selectedKey = null;
        // Fresh data set -> drop any sort the user had applied so server
        // order shows through. Per spec: clear sort on rows reset.
        state.sortCol = null;
        state.sortDir = null;
      }
      opts.statusEl.textContent = "Loading...";
      try {
        const { rows, count } = await opts.fetcher(state.offset, state.limit);
        state.rows = state.rows.concat(rows || []);
        state.offset += (rows || []).length;
        if (typeof count === "number") state.total = count;
      } catch (e) {
        opts.statusEl.textContent = `Error: ${e.message}`;
        opts.tbodyEl.innerHTML = `<tr><td class="empty" colspan="${state.cols.length || 1}">${escapeHtml(e.message)}</td></tr>`;
        return;
      }
      render();
    }

    function clear(message) {
      state.rows = [];
      state.offset = 0;
      state.total = null;
      state.selectedKey = null;
      state.sortCol = null;
      state.sortDir = null;
      opts.tbodyEl.innerHTML = `<tr><td class="empty" colspan="${state.cols.length || 1}">${escapeHtml(message || "")}</td></tr>`;
      opts.statusEl.textContent = "";
      if (opts.loadMoreBtn) opts.loadMoreBtn.hidden = true;
    }

    // Event wiring
    opts.tbodyEl.addEventListener("click", (ev) => {
      const tr = ev.target.closest("tr[data-key]");
      if (!tr) return;
      const key = tr.getAttribute("data-key");
      state.selectedKey = key;
      // Find the actual row object (use string compare since data-key is a string)
      const row = state.rows.find((r) => String(opts.rowKeyField ? r[opts.rowKeyField] : "") === key);
      render();
      if (opts.onRowClick && row) opts.onRowClick(row);
    });

    // Header clicks cycle the sort on that column. We delegate from theadEl
    // since the inner <th>s are re-rendered on every render() call.
    opts.theadEl.addEventListener("click", (ev) => {
      const th = ev.target.closest("th[data-sort-col]");
      if (!th) return;
      const col = th.getAttribute("data-sort-col");
      cycleSort(col);
      render();
    });

    if (opts.searchInput) {
      opts.searchInput.addEventListener("input", () => {
        state.search = opts.searchInput.value || "";
        render();
      });
    }

    if (opts.colsBtn && opts.colsPanel) {
      opts.colsBtn.addEventListener("click", () => {
        opts.colsPanel.hidden = !opts.colsPanel.hidden;
        if (!opts.colsPanel.hidden) renderColsPanel();
      });

      // Click-outside-to-close. Discards any pending (unapplied) edits.
      // We check both the panel and the toggle button so clicking the
      // button itself doesn't fight with its own toggle handler above.
      document.addEventListener("click", (ev) => {
        if (opts.colsPanel.hidden) return;
        if (opts.colsPanel.contains(ev.target)) return;
        if (opts.colsBtn.contains(ev.target)) return;
        opts.colsPanel.hidden = true;
      });
    }

    if (opts.loadMoreBtn) {
      opts.loadMoreBtn.addEventListener("click", () => load(false));
    }

    return { load, clear, render, state };
  }

  // ---------- bind to DOM ----------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    // Alerts table
    const alerts = makeTable({
      name: "alerts",
      tableEl: document.getElementById("alerts-table"),
      theadEl: document.querySelector("#alerts-table thead"),
      tbodyEl: document.querySelector("#alerts-table tbody"),
      statusEl: document.getElementById("alerts-status"),
      loadMoreBtn: document.getElementById("alerts-load-more"),
      searchInput: document.getElementById("alerts-search"),
      colsBtn: document.querySelector('[data-cols-target="alerts"]'),
      colsPanel: document.getElementById("alerts-cols-panel"),
      rowKeyField: "id",
      danger: (r) => r.status === "Unresolved",
      onRowClick: (row) => {
        document.getElementById("devices-context").textContent =
          row && row.id ? `for alert ${row.id}` : "";
        devices.clear("Loading devices...");
        devices.fetcherAlertId = row.id;
        devices.load(true);
        events.clear("Select a device above.");
        document.getElementById("events-context").textContent = "";
      },
      fetcher: async (offset, limit) => {
        const onlyUn = document.getElementById("alerts-only-unresolved").checked;
        const params = new URLSearchParams({ offset, limit });
        if (onlyUn) params.set("filter_status", "Unresolved");
        const data = await fetchJSON(`/api/alerts?${params}`);
        return { rows: data.alerts || [], count: data.count };
      },
    });

    const devices = makeTable({
      name: "devices",
      tableEl: document.getElementById("devices-table"),
      theadEl: document.querySelector("#devices-table thead"),
      tbodyEl: document.querySelector("#devices-table tbody"),
      statusEl: document.getElementById("devices-status"),
      loadMoreBtn: document.getElementById("devices-load-more"),
      searchInput: document.getElementById("devices-search"),
      colsBtn: document.querySelector('[data-cols-target="devices"]'),
      colsPanel: document.getElementById("devices-cols-panel"),
      rowKeyField: "asset_id",
      danger: (r) => r.is_resolved === false,
      onRowClick: (row) => {
        document.getElementById("events-context").textContent =
          row && row.asset_id ? `for device ${row.asset_id}` : "";
        events.fetcherAssetId = row.asset_id;
        events.clear("Loading events...");
        events.load(true);
      },
      fetcher: async (offset, limit) => {
        const id = devices.fetcherAlertId;
        if (!id) return { rows: [], count: 0 };
        const params = new URLSearchParams({ offset, limit });
        const data = await fetchJSON(`/api/alerts/${encodeURIComponent(id)}/devices?${params}`);
        return { rows: data.devices || [], count: data.count };
      },
    });

    const events = makeTable({
      name: "events",
      tableEl: document.getElementById("events-table"),
      theadEl: document.querySelector("#events-table thead"),
      tbodyEl: document.querySelector("#events-table tbody"),
      statusEl: document.getElementById("events-status"),
      loadMoreBtn: document.getElementById("events-load-more"),
      searchInput: document.getElementById("events-search"),
      colsBtn: document.querySelector('[data-cols-target="events"]'),
      colsPanel: document.getElementById("events-cols-panel"),
      rowKeyField: "event_id",
      danger: () => false,
      onRowClick: () => { /* leaf */ },
      fetcher: async (offset, limit) => {
        // The mid (devices) table drives the asset filter for the OT activity
        // events endpoint. Asset ID -> dest_asset_id; alert ID (still selected
        // in the top table) -> related_alert_ids. Both are sent as query
        // params to the Flask proxy, which builds the compound `and` filter
        // body for /api/v1/ot_activity_events.
        const assetId = events.fetcherAssetId;
        const alertId = devices.fetcherAlertId;
        if (!assetId) return { rows: [], count: 0 };
        const params = new URLSearchParams({ offset, limit, asset_id: assetId });
        if (alertId) params.set("alert_id", alertId);
        const data = await fetchJSON(`/api/ot_activity_events?${params}`);
        return { rows: data.ot_activity_events || [], count: data.count };
      },
    });

    document.getElementById("alerts-only-unresolved").addEventListener("change", () => alerts.load(true));
    document.getElementById("alerts-refresh").addEventListener("click", () => alerts.load(true));

    alerts.load(true);
  });
})();
