(function (root) {
  "use strict";

  const PAGE_SIZE = 25;
  const MAX_ROWS = 2000;
  const colors = ["#82e6d6", "#a99cf8", "#80b7f5", "#e7bf7f", "#c393d9", "#9bc8bc"];
  const numeric = new Intl.NumberFormat();
  const list = value => Array.isArray(value) ? value : [];
  const label = (value, fallback = "") => String(value == null ? fallback : value).slice(0, 300);
  const number = value => numeric.format(value);
  const validCount = value => typeof value === "number" && Number.isFinite(value) && value >= 0;

  function el(tag, className, ...children) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false)
        node.append(child instanceof root.Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  function empty(message = "No saved data for this chart yet.") {
    return el("p", "ls-viz-empty", message);
  }
  function safeHref(value) {
    if (typeof value !== "string" || value.length > 4000) return null;
    try {
      const url = new URL(value, root.location.href);
      return ["http:", "https:"].includes(url.protocol) ? value : null;
    } catch (_) { return null; }
  }
  function chart(title, coverage) {
    const node = el("section", "ls-viz-chart");
    if (title) node.append(el("h3", "ls-viz-title", label(title)));
    if (coverage) node.append(el("p", "ls-viz-coverage", label(coverage)));
    return node;
  }
  function limitNote(rows) {
    return list(rows).length > MAX_ROWS
      ? el("p", "ls-viz-coverage", "Chart limited to the first " + number(MAX_ROWS) + " supplied rows.")
      : null;
  }
  function moreControl(container, values, render, size = PAGE_SIZE) {
    let shown = 0;
    const more = el("button", "btn btn-small ls-viz-more");
    more.type = "button";
    more.dataset.vizMore = "";
    function add() {
      const end = Math.min(shown + size, values.length);
      for (; shown < end; shown++) container.append(render(values[shown], shown));
      more.hidden = shown >= values.length;
      more.textContent = "Show " + number(Math.min(size, values.length - shown)) + " more (" + number(shown) + " of " + number(values.length) + ")";
    }
    more.addEventListener("click", add);
    add();
    return more;
  }

  function disclosure(options = {}) {
    const details = el("details", "ls-viz-disclosure");
    const id = label(options.id);
    const key = "leakscope:visualization:" + id;
    details.dataset.vizId = id;
    details.open = options.open !== false;
    if (id) {
      try {
        const saved = root.localStorage.getItem(key);
        if (saved === "open" || saved === "closed") details.open = saved === "open";
      } catch (_) {}
    }
    const summary = el("summary", "",
      el("span", "ls-viz-heading", label(options.title, "Saved evidence")),
      options.summary ? el("span", "ls-viz-summary", label(options.summary)) : null,
    );
    details.append(summary, el("div", "ls-viz-body", options.children));
    let lastOpen = details.open;
    details.addEventListener("toggle", () => {
      if (lastOpen === details.open) return;
      lastOpen = details.open;
      if (id) {
        try { root.localStorage.setItem(key, details.open ? "open" : "closed"); }
        catch (_) {}
      }
    });
    return details;
  }

  function bars(options = {}) {
    const source = list(options.rows);
    const values = source.slice(0, MAX_ROWS).filter(row => row && validCount(row.count));
    const output = chart(options.title, options.coverageLabel);
    if (!values.length) { output.append(empty(options.emptyText)); return output; }
    const max = Math.max(1, ...values.map(row => row.count));
    const body = el("div", "ls-viz-bars");
    const unit = label(options.unit, "observations");
    const more = moreControl(body, values, (row, index) => {
      const href = safeHref(row.href);
      const callback = typeof row.onSelect === "function" ? row.onSelect : options.onSelect;
      const control = el(typeof callback === "function" ? "button" : href ? "a" : "div", "ls-viz-bar-row");
      control.dataset.vizKey = label(row.key, row.label);
      const name = label(row.label, row.key == null ? "Unknown" : row.key);
      const accessible = name + ": " + number(row.count) + " " + unit;
      control.title = accessible;
      control.setAttribute("aria-label", accessible);
      if (typeof callback === "function") {
        control.type = "button";
        control.setAttribute("aria-pressed", "false");
        control.addEventListener("click", () => {
          for (const sibling of body.querySelectorAll("button.ls-viz-bar-row"))
            sibling.setAttribute("aria-pressed", String(sibling === control));
          callback(row);
        });
      } else if (href) {
        control.href = href;
        control.rel = "noopener noreferrer";
      }
      const track = el("span", "ls-viz-bar-track");
      track.setAttribute("aria-hidden", "true");
      const fill = el("span", "ls-viz-bar-fill");
      fill.style.width = (row.count / max * 100) + "%";
      fill.style.setProperty("--ls-viz-color", colors[index % colors.length]);
      track.append(fill);
      control.append(el("span", "ls-viz-bar-label", name), track,
        el("strong", "ls-viz-bar-count", number(row.count)));
      return control;
    });
    output.append(body, more);
    const note = limitNote(source); if (note) output.append(note);
    return output;
  }

  function parsedDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}(?:$|T)/.test(value)) return null;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return null;
    const dateOnly = new Date(value.slice(0, 10) + "T00:00:00Z");
    if (!Number.isFinite(dateOnly.getTime()) || dateOnly.toISOString().slice(0, 10) !== value.slice(0, 10)) return null;
    return date;
  }
  function dateLabel(value) {
    const date = parsedDate(value);
    return date ? new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
    }).format(date) : label(value);
  }

  function timeline(options = {}) {
    const source = list(options.rows);
    const values = source.slice(0, MAX_ROWS)
      .filter(row => row && validCount(row.count) && parsedDate(row.date))
      .sort((a, b) => parsedDate(a.date).getTime() - parsedDate(b.date).getTime());
    const output = chart(options.title || "Collection history", options.coverageLabel);
    if (!values.length) { output.append(empty(options.emptyText || "No saved timeline data yet.")); return output; }
    const max = Math.max(1, ...values.map(row => row.count));
    const body = el("div", "ls-viz-time-series");
    const readout = el("p", "ls-viz-readout", "Select a recorded date to see its count. Missing dates are not shown as zero.");
    readout.setAttribute("aria-live", "polite");
    const unit = label(options.unit, "observations");
    const more = moreControl(body, values, row => {
      const control = el("button", "ls-viz-time-point");
      control.type = "button";
      control.dataset.date = row.date;
      control.setAttribute("aria-pressed", "false");
      const description = dateLabel(row.date) + ": " + number(row.count) + " " + unit;
      control.title = description;
      control.setAttribute("aria-label", description);
      const fill = el("span", "ls-viz-time-fill");
      fill.style.height = (row.count / max * 100) + "%";
      fill.setAttribute("aria-hidden", "true");
      control.append(el("strong", "ls-viz-time-count", number(row.count)),
        el("span", "ls-viz-time-track", fill), el("span", "ls-viz-time-date", dateLabel(row.date)));
      control.addEventListener("click", () => {
        for (const sibling of body.children) sibling.setAttribute("aria-pressed", String(sibling === control));
        readout.textContent = description;
        if (typeof options.onSelect === "function") options.onSelect(row);
      });
      return control;
    });
    output.append(body, more, readout);
    const note = limitNote(source); if (note) output.append(note);
    return output;
  }

  function endpointInsights(options = {}) {
    const source = list(options.rows);
    const rows = source.slice(0, MAX_ROWS).filter(row => row && typeof row === "object");
    const page = Number.isInteger(options.page) && options.page > 0 ? options.page : 1;
    const scope = "Page " + page + " · " + (validCount(options.total)
      ? number(rows.length) + " of " + number(options.total) + " matching endpoints"
      : number(rows.length) + " endpoints on this page · total unavailable");
    const body = el("div", "");
    const disclosureNode = disclosure({ id: options.id || "endpoint-insights", title: "Observation insights", summary: scope,
      open: options.open !== false, children: body });
    if (options.loading) {
      const loading = empty("Loading saved observations…"); loading.setAttribute("role", "status"); body.append(loading); return disclosureNode;
    }
    body.append(el("p", "ls-viz-coverage", scope + ". Charts describe this page, not the entire inventory."));
    if (!rows.length) { body.append(empty("No saved endpoints on this page.")); return disclosureNode; }
    const catalog = list(Array.isArray(options.catalog) ? options.catalog : options.catalog?.results);
    const types = new Map(catalog.map(item => [item.key, item.label || item.key]));
    const groups = new Map();
    for (const item of rows) {
      const key = typeof item.service_type === "string" ? item.service_type : "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    const selected = el("div", "ls-viz-readout");
    selected.setAttribute("aria-live", "polite");
    selected.append(empty("Select a technology or last-seen bucket to inspect saved endpoints."));
    function inspect(items, name) {
      const entries = el("div", "ls-viz-endpoint-list");
      const more = moreControl(entries, items, item => {
        const name = label(item.title || item.address, "Saved endpoint") + (item.port != null ? ":" + label(item.port) : "");
        let control;
        if (typeof options.onEndpoint === "function") {
          control = el("button", "btn btn-small", name); control.type = "button";
          control.addEventListener("click", () => options.onEndpoint(item));
        } else if (item.id != null) {
          control = el("a", "", name); control.href = "/assets/?endpoint=" + encodeURIComponent(item.id);
        } else control = el("span", "", name);
        control.dataset.vizEndpoint = label(item.id);
        return control;
      });
      selected.replaceChildren(el("strong", "", label(name) + " · " + number(items.length) + " endpoints on this page"), entries, more);
    }
    const technology = bars({ title: "Technology mix", unit: "endpoints", rows: [...groups].map(([key, items]) => ({ key, label: types.get(key) || key || "Unclassified", count: items.length })),
      onSelect: row => {
        if (typeof options.onType === "function") options.onType(row.key, row);
        else inspect(groups.get(row.key) || [], row.label);
      } });
    const now = typeof options.now === "number" && Number.isFinite(options.now) ? options.now : Date.now();
    const ages = [
      { key: "last-day", label: "Within 24 hours", until: 86400000 },
      { key: "last-week", label: "1–7 days", until: 7 * 86400000 },
      { key: "last-month", label: "7–30 days", until: 30 * 86400000 },
      { key: "older", label: "Over 30 days", until: Infinity },
      { key: "unknown-age", label: "Unknown / future timestamp", until: null },
    ].map(row => ({ ...row, items: [] }));
    for (const item of rows) {
      const date = parsedDate(item.last_seen);
      const age = date ? now - date.getTime() : -1;
      const bucket = age < 0 ? ages[4] : ages.find(row => row.until !== null && age < row.until);
      bucket.items.push(item);
    }
    const age = bars({ title: "Last seen", unit: "endpoints", coverageLabel: "Saved last-seen timestamps; availability is not verified.",
      rows: ages.map(row => ({ ...row, count: row.items.length })), onSelect: row => inspect(row.items, row.label) });
    body.append(el("div", "ls-viz-grid", technology, age), selected);
    const note = limitNote(source); if (note) body.append(note);
    return disclosureNode;
  }

  function organizationMap(options = {}) {
    const org = options.organization || {};
    const rootItem = { ...org, id: org.id || "organization", kind: "organization", value: org.value || org.label || "Organization", relation: "organization_scope" };
    const domains = list(options.domains).map((item, i) => ({ ...item, id: item.node_id || "domain-" + i,
      kind: "domain", value: item.name || item.value || "Saved domain", label: item.name || item.value,
      relation: item.source || "user_scope", domain_scope: true }));
    const members = list(options.members).map((item, i) => ({ ...item, id: item.id || "member-" + i,
      relation: item.relationship || "saved_result_association" }));
    const source = [...domains, ...members];
    const values = source.slice(0, MAX_ROWS);
    const body = el("div", "");
    const map = disclosure({ id: options.id || "organization:" + label(org.id || org.value, "scope") + ":connections",
      title: "Evidence connections", summary: number(source.length) + " direct associations", open: options.open !== false, children: body });
    body.append(el("p", "ls-viz-coverage", "Direct saved associations only. These links do not establish corporate ownership."));
    const readout = el("div", "ls-viz-readout");
    readout.setAttribute("aria-live", "polite");
    const controls = [];
    function show(item) {
      const explanation = item.domain_scope && item.relation === "user_scope"
        ? "User-supplied scope association. Ownership is unverified."
        : item.kind === "organization" ? "Organization research scope. Select an associated domain or result to inspect its saved context."
          : "Saved relationship: " + label(item.relation).replaceAll("_", " ") + ". This association is retained for evidence review.";
      readout.replaceChildren(el("h3", "ls-viz-title", label(item.label || item.value, "Saved association")),
        el("p", "ls-viz-coverage", explanation));
      let href;
      if (item.kind === "organization" && org.id) href = "/organizations/?id=" + encodeURIComponent(org.id);
      else if (item.domain_scope && item.node_id) href = "/relationships/?root=" + encodeURIComponent(item.node_id);
      else href = safeHref(item.url) || (item.id && !String(item.id).startsWith("member-") ? "/relationships/?root=" + encodeURIComponent(item.id) : null);
      if (href) { const inspect = el("a", "btn btn-small", "Inspect saved evidence →"); inspect.href = href; inspect.rel = "noopener noreferrer"; readout.append(inspect); }
    }
    function node(item, rootNode = false) {
      const control = el("button", "ls-viz-map-node" + (rootNode ? " ls-viz-map-root" : ""),
        el("strong", "ls-viz-map-label", label(item.label || item.value, "Saved association")),
        el("small", "ls-viz-map-relation", item.domain_scope && item.relation === "user_scope" ? "User-supplied scope" : label(item.relation).replaceAll("_", " ")));
      control.type = "button";
      control.dataset.vizNode = label(item.id);
      control.setAttribute("aria-pressed", String(rootNode));
      controls.push(control);
      control.addEventListener("click", () => {
        for (const entry of controls) entry.setAttribute("aria-pressed", String(entry === control));
        show(item);
        if (typeof options.onSelect === "function") options.onSelect(item);
      });
      return control;
    }
    const branches = el("div", "ls-viz-map-branches");
    const more = moreControl(branches, values, item => node(item), 12);
    const canvas = el("div", "ls-viz-map-canvas", node(rootItem, true), branches);
    body.append(canvas);
    if (!values.length) body.append(empty("No saved domain or result associations yet."));
    body.append(more, el("p", "ls-viz-map-caption", "Scope and result associations are retained separately from inferred ownership."), readout);
    const note = limitNote(source); if (note) body.append(note);
    show(rootItem);
    return map;
  }

  root.LeakScopeViz = { disclosure, bars, timeline, endpointInsights, organizationMap };
})(typeof window !== "undefined" ? window : globalThis);
