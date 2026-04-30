/* Tree view: lazy-loaded alerts -> devices -> events.
 *
 * Each node renders a clickable header. First click expands and fetches
 * children; second click collapses (children stay cached). Color rules
 * mirror the table view (Unresolved alerts and unresolved devices = red).
 */

(function () {
  "use strict";

  function escapeHtml(v) {
    if (v === null || v === undefined) return "";
    if (Array.isArray(v)) return v.map(escapeHtml).join(", ");
    if (typeof v === "object") return escapeHtml(JSON.stringify(v));
    return String(v).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  async function fetchJSON(url) {
    const resp = await fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    let body = null;
    try { body = await resp.json(); } catch (e) {}
    if (!resp.ok) throw new Error((body && body.error) || `HTTP ${resp.status}`);
    return body || {};
  }

  function makeNode(label, danger) {
    const wrap = document.createElement("div");
    wrap.className = "tree-node";
    const toggle = document.createElement("div");
    toggle.className = "tree-toggle" + (danger ? " danger" : "");
    toggle.innerHTML = `<span class="tree-arrow">&#9656;</span> ${label}`;
    const children = document.createElement("div");
    children.className = "tree-children";
    children.hidden = true;
    wrap.appendChild(toggle);
    wrap.appendChild(children);

    let loaded = false;
    return {
      el: wrap,
      toggle,
      children,
      onExpand(handler) {
        toggle.addEventListener("click", async () => {
          const willOpen = children.hidden;
          children.hidden = !willOpen;
          toggle.querySelector(".tree-arrow").innerHTML = willOpen ? "&#9662;" : "&#9656;";
          if (willOpen && !loaded) {
            children.innerHTML = '<div class="tree-empty">Loading&hellip;</div>';
            try {
              await handler(children);
              loaded = true;
            } catch (e) {
              children.innerHTML = `<div class="tree-empty">Error: ${escapeHtml(e.message)}</div>`;
            }
          }
        });
      },
    };
  }

  function alertLabel(a) {
    const status = a.status || "?";
    return `<strong>${escapeHtml(a.alert_type_name || a.alert_name || a.id)}</strong>` +
           `<span class="tree-meta">[${escapeHtml(status)}] ` +
           `${escapeHtml(a.detected_time || "")} &middot; ` +
           `${escapeHtml(a.devices_count || 0)} devices</span>`;
  }

  function deviceLabel(d) {
    const ip = Array.isArray(d.ip_list) ? d.ip_list.join(", ") : (d.ip_list || "");
    return `<strong>${escapeHtml(d.device_name || d.asset_id)}</strong>` +
           `<span class="tree-meta">${escapeHtml(d.device_type || "")} &middot; ` +
           `${escapeHtml(ip)} &middot; ` +
           `risk ${escapeHtml(d.risk_score || "?")}` +
           `${d.is_resolved === false ? " &middot; UNRESOLVED" : ""}</span>`;
  }

  function eventLabel(ev) {
    return `<strong>${escapeHtml(ev.event_type || ev.event_id || "event")}</strong>` +
           `<span class="tree-meta">${escapeHtml(ev.detection_time || "")} &middot; ` +
           `${escapeHtml(ev.source_ip || "?")} &rarr; ${escapeHtml(ev.dest_ip || "?")} ` +
           `(${escapeHtml(ev.protocol || "")})</span>`;
  }

  async function loadAlerts(rootEl, onlyUnresolved) {
    rootEl.innerHTML = '<div class="empty">Loading alerts&hellip;</div>';
    try {
      const params = new URLSearchParams({ limit: 200 });
      if (onlyUnresolved) params.set("filter_status", "Unresolved");
      const data = await fetchJSON(`/api/alerts?${params}`);
      const alerts = data.alerts || [];
      if (!alerts.length) {
        rootEl.innerHTML = '<div class="empty">No alerts.</div>';
        return;
      }
      rootEl.innerHTML = "";
      alerts.forEach((a) => {
        const node = makeNode(alertLabel(a), a.status === "Unresolved");
        node.onExpand(async (childContainer) => {
          const dData = await fetchJSON(`/api/alerts/${encodeURIComponent(a.id)}/devices?limit=200`);
          const devices = dData.devices || [];
          if (!devices.length) {
            childContainer.innerHTML = '<div class="tree-empty">No devices.</div>';
            return;
          }
          childContainer.innerHTML = "";
          devices.forEach((d) => {
            const dnode = makeNode(deviceLabel(d), d.is_resolved === false);
            dnode.onExpand(async (eventContainer) => {
              // Events are filtered by the parent alert's related_alert_ids,
              // so route the call under the alert that owns this device.
              const eUrl =
                `/api/alerts/${encodeURIComponent(a.id)}` +
                `/devices/${encodeURIComponent(d.asset_id)}/events?limit=200`;
              const eData = await fetchJSON(eUrl);
              const events = eData.ot_activity_events || [];
              if (!events.length) {
                eventContainer.innerHTML = '<div class="tree-empty">No events.</div>';
                return;
              }
              eventContainer.innerHTML = "";
              events.forEach((ev) => {
                const enode = makeNode(eventLabel(ev), false);
                eventContainer.appendChild(enode.el);
              });
            });
            childContainer.appendChild(dnode.el);
          });
        });
        rootEl.appendChild(node.el);
      });
    } catch (e) {
      rootEl.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message)}</div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("tree-root");
    const onlyUn = document.getElementById("tree-only-unresolved");
    const refresh = document.getElementById("tree-refresh");
    const reload = () => loadAlerts(root, onlyUn.checked);
    onlyUn.addEventListener("change", reload);
    refresh.addEventListener("click", reload);
    reload();
  });
})();
