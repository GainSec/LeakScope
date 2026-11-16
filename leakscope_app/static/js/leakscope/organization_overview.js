(function () {
  "use strict";
  const selected = new Map();
  let sequence = 0;
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    children.flat(Infinity).forEach(child => {
      if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }
  function safeHref(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const url = new URL(value, location.origin);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function inspect(reference, fallbackHref, label = "Inspect saved result") {
    const actions = el("span", {class: "asset-actions organization-evidence-actions"});
    if (reference?.type && reference.type !== "asset" && reference.id != null && window.LeakScopeInspector?.open) {
      actions.append(el("button", {type: "button", class: "btn btn-small", onclick: () => window.LeakScopeInspector.open(reference)}, label));
    }
    const href = safeHref(fallbackHref);
    if (href) actions.append(el("a", {href}, "Open full detail →"));
    return actions;
  }
  function layout({key, sections}) {
    const prefix = "organization-view-" + (++sequence);
    const shell = el("div", {class: "organization-workspace"});
    const tabs = el("div", {class: "organization-tabs", role: "tablist", "aria-label": "Organization workspace"});
    const panels = [], controls = [];
    let active = selected.get(String(key));
    if (!sections.some(section => section.id === active)) active = sections[0]?.id;
    function activate(id, focus = false) {
      selected.set(String(key), id);
      controls.forEach((control, index) => {
        const on = sections[index].id === id;
        control.setAttribute("aria-selected", String(on));
        control.tabIndex = on ? 0 : -1;
        panels[index].hidden = !on;
        if (on && focus) control.focus();
      });
    }
    sections.forEach((section, index) => {
      const tabId = prefix + "-tab-" + section.id, panelId = prefix + "-panel-" + section.id;
      const control = el("button", {type: "button", role: "tab", id: tabId, "aria-controls": panelId,
        onclick: () => activate(section.id)}, section.label);
      control.addEventListener("keydown", event => {
        const movement = {ArrowRight: (index + 1) % sections.length, ArrowLeft: (index + sections.length - 1) % sections.length,
          Home: 0, End: sections.length - 1}[event.key];
        if (movement == null) return;
        event.preventDefault();activate(sections[movement].id, true);
      });
      const panel = el("section", {id: panelId, role: "tabpanel", "aria-labelledby": tabId,
        "data-organization-section": section.id, class: "organization-tab-panel", tabindex: "0"}, section.content);
      controls.push(control);panels.push(panel);tabs.append(control);
    });
    shell.append(tabs, ...panels);activate(active);
    if (selected.size > 100) selected.delete(selected.keys().next().value);
    return shell;
  }
  function metric(label, value, scope) {
    return el("div", {class: "organization-metric"}, el("strong", {}, Number(value).toLocaleString()),
      el("span", {}, label), el("small", {}, scope));
  }
  function breakdown(title, rows, key, truncated) {
    const section = el("section", {class: "organization-summary-card"}, el("h3", {}, title));
    const usable = (Array.isArray(rows) ? rows : []).slice(0, 30);
    if (!usable.length) return section.append(el("p", {class: "small muted"}, "No saved breakdown available.")), section;
    const max = Math.max(1, ...usable.map(item => Number(item.count) || 0));
    function bars(items) {
      const list = el("ul", {class: "organization-source-bars"});
      for (const item of items) {
        const count = Math.max(0, Number(item.count) || 0);
        const fill = el("span", {class: "organization-bar-fill"});fill.style.width = (count / max * 100) + "%";
        const label = String(item[key] || "Unknown").replaceAll("_", " ");
        list.append(el("li", {}, el("div", {class: "organization-bar-label"}, el("span", {}, label), el("strong", {}, count.toLocaleString())),
          el("div", {class: "organization-bar-track", "aria-hidden": "true"}, fill)));
      }
      return list;
    }
    section.append(bars(usable.slice(0, 6)));
    if (usable.length > 6) section.append(el("details", {class: "organization-breakdown-more"},
      el("summary", {}, "Show " + (usable.length - 6) + " more groups"), bars(usable.slice(6))));
    if (truncated) section.append(el("p", {class: "small muted"}, "A bounded saved-data breakdown; additional groups may exist."));
    return section;
  }
  function savedTime(label, value) {
    const valid = value && Number.isFinite(Date.parse(value));
    return el("span", {}, label + ": ", valid ? el("time", {datetime: value}, new Date(value).toLocaleString()) : "Unknown");
  }
  function render(data = {}) {
    const overview = data.overview;
    const root = el("div", {class: "organization-overview"});
    const header = el("div", {class: "organization-overview-heading"}, el("div", {}, el("p", {class: "organization-eyebrow"}, "Saved investigation"),
      el("h2", {}, "Organization overview")), el("span", {class: "organization-saved-label"}, "Saved data only"));
    root.append(header, el("p", {class: "small organization-summary-basis"}, overview?.basis || "Loaded domain scopes and linked members. These associations do not establish ownership."));
    if (data.organization?.id) root.append(el("div", {class: "asset-actions"},
      el("a", {class: "btn btn-small", href: "/domain-coverage/?organization_id=" + encodeURIComponent(data.organization.id)}, "Domain evidence coverage")));
    const metrics = el("div", {class: "organization-metrics"});
    if (!overview?.counts) {
      metrics.append(metric("Domain scopes", (data.domains || []).length, "Loaded records"), metric("Linked members", (data.members || []).length, "Loaded records"));
      root.append(metrics, el("p", {class: "notice"}, "No saved evidence summary is available in this response. Review the loaded scope and results in their tab."));
      return root;
    }
    for (const [key, label] of [["scope_domains", "Domain scopes"], ["linked_results", "Linked members"], ["endpoints", "Endpoints"],
      ["evidence", "Evidence records"], ["relationships", "Relationships"], ["assets", "Scoped assets"]]) {
      const value = overview.counts[key];
      if (value != null && Number.isFinite(Number(value))) metrics.append(metric(label, value, "Explicit organization scope"));
    }
    root.append(metrics);
    root.append(el("div", {class: "organization-breakdowns"}, breakdown("Saved evidence sources", overview.sources, "source", overview.breakdown_truncated),
      breakdown("Scoped asset kinds", overview.kinds, "kind", overview.breakdown_truncated), breakdown("Observed technologies", overview.technologies, "technology", overview.breakdown_truncated)));
    const recent = el("section", {class: "organization-summary-card organization-recent"}, el("h3", {}, "Recent saved evidence"),
      el("p", {class: "small muted"}, "Observed time describes the source evidence. Collected time records when LeakScope saved it; it does not confirm current reachability."));
    const rows = Array.isArray(overview.recent_evidence) ? overview.recent_evidence.slice(0, 12) : [];
    if (!rows.length) recent.append(el("p", {class: "small muted"}, "No recent saved evidence is available for this organization scope."));
    else {
      const timeline = el("ol", {class: "organization-evidence-timeline"});
      for (const item of rows) {
        const identity = item.subject || item.subject_kind || "Saved asset";
        const source = el("span", {class: "organization-evidence-source"}, item.source || "Unknown source");
        const times = window.LeakScopeInspector?.provenance ? window.LeakScopeInspector.provenance(item) :
          el("div", {class: "organization-evidence-times"}, savedTime("Observed", item.observed_at), savedTime("Collected", item.collected_at),
            el("span", {}, item.provenance?.label || "Saved evidence"));
        const fallback = item.subject_id ? "/relationships/?root=" + encodeURIComponent(item.subject_id) : null;
        timeline.append(el("li", {}, el("div", {class: "organization-evidence-heading"}, el("strong", {}, identity), source), times,
          inspect(item.reference, fallback, "Inspect evidence")));
      }
      recent.append(timeline);
    }
    if (overview.recent_truncated) recent.append(el("p", {class: "small muted"}, "Showing the latest " + rows.length + " saved evidence records in this response. Explore relationships for the wider saved context."));
    root.append(recent);
    return root;
  }
  window.LeakScopeOrganizationOverview = {render, layout, inspect};
})();
