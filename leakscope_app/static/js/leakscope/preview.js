(function () {
  "use strict";
  const API = "/api/operations";
  const ACTIONS = [
    ["check", "Connection reachability"],
    ["tcp", "TCP request / response"],
    ["udp", "UDP request / response"],
    ["identity", "Protocol identity"],
    ["http", "HTTP response"],
    ["httpx", "HTTP fingerprint"],
    ["screenshot", "Screenshot"],
  ];
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (["checked", "disabled", "selected"].includes(key)) node[key] = !!value;
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }
  const button = (label, action) => el("button", {type: "button", class: "btn btn-small", onclick: action}, label);
  const note = (message, error = false) => el("p", {class: "notice" + (error ? " notice-error" : ""), role: error ? "alert" : "status"}, message);
  const field = (label, control) => el("label", {class: "field"}, el("span", {class: "field-label"}, label), control);
  const panel = (title, description, ...body) => el("section", {class: "panel"}, el("div", {class: "panel-heading"}, el("div", {}, el("h2", {}, title), el("p", {}, description))), el("div", {class: "panel-body"}, body));
  const select = (name, choices) => el("select", {name, class: "select"}, choices.map(([value, label]) => el("option", {value}, label)));
  async function request(path, data, signal) {
    const response = await fetch(API + path, {
      method: data === undefined ? "GET" : "POST", credentials: "same-origin", signal,
      headers: {Accept: "application/json", ...(data === undefined ? {} : {"Content-Type": "application/json", "X-CSRFToken": document.querySelector("meta[name=csrf-token]")?.content || ""})},
      ...(data === undefined ? {} : {body: JSON.stringify(data)}),
    });
    let result;
    try { result = await response.json(); } catch (_) { throw Error("The server returned an unexpected response."); }
    if (!response.ok) throw Error(result.error || result.detail || "Saved evidence could not be loaded.");
    return result;
  }
  async function render(root, options = {}) {
    const params = new URLSearchParams(location.search);
    let dispose = () => {}, sourceVersion = 0;
    const source = select("preview_source", [["observations", "Saved Observations · all technologies"], ["legacy", "Legacy findings · data preview adapters"]]);
    const sourceBar = el("div", {class: "toolbar preview-source-bar"}, field("Preview source", source));
    // Existing finding links use integer IDs; endpoint captures always use UUIDs.
    const legacyLink = params.get("type") && /^\d+$/.test(params.get("target_id") || "");
    source.value = (params.get("source") || (legacyLink ? "legacy" : "observations")) === "legacy" ? "legacy" : "observations";
    async function showSource() {
      dispose();
      const version = ++sourceVersion;
      root.replaceChildren(sourceBar, note("Loading Preview…"));
      root.setAttribute("aria-busy", "true");
      if (source.value === "legacy") {
        source.disabled = true;
        try {
          if (!options.legacy) throw Error("Legacy preview is unavailable in this workspace.");
          await options.legacy();
          if (version === sourceVersion) root.prepend(sourceBar);
        } catch (error) {
          if (version === sourceVersion) root.replaceChildren(sourceBar, note(error.message, true));
        } finally { source.disabled = false; root.setAttribute("aria-busy", "false"); }
        return;
      }
      const controller = new AbortController();
      dispose = () => controller.abort();
      try {
        const catalog = await request("/catalog", undefined, controller.signal);
        if (version !== sourceVersion) return;
        dispose = modern(root, sourceBar, catalog, params);
      } catch (error) {
        if (version === sourceVersion && error.name !== "AbortError") root.replaceChildren(sourceBar, note(error.message, true), button("Retry Preview", showSource));
      } finally { if (version === sourceVersion) root.setAttribute("aria-busy", "false"); }
    }
    source.addEventListener("change", showSource);
    window.addEventListener("pagehide", () => dispose(), {once: true});
    await showSource();
  }
  function modern(root, sourceBar, catalog, params) {
    const entries = catalog.results || [];
    let page = 1, pages = 1, targets = [], generation = 0, controller, disposed = false, busy = false;
    let initialId = params.get("endpoint") || params.get("endpoint_id") ||
      (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(params.get("target_id") || "") ? params.get("target_id") : "");
    let runController = new AbortController();
    const type = select("technology", []);
    const serviceSearch = el("input", {name: "service_search", class: "input", type: "search", placeholder: "Find a technology, protocol or category", maxlength: 100});
    const query = el("input", {name: "q", class: "input", type: "search", maxlength: 200, placeholder: "Address, title or saved product"});
    query.value = params.get("q") || "";
    const target = select("endpoint_id", [["", "Loading saved endpoints…"]]);
    const status = el("div", {"aria-live": "polite"});
    const errors = el("div", {"aria-live": "polite"});
    const actions = el("div", {class: "ops-actions"});
    const udpEncoding = select("udp_encoding", [["text", "UTF-8 text"], ["hex", "Hexadecimal byte pairs"]]);
    const udpPayload = el("textarea", {name: "udp_payload", class: "input mono", rows: 3, maxlength: 12288, placeholder: "Enter one explicit datagram payload"});
    const udpOptions = el("div", {class: "form-stack"},
      el("p", {class: "small muted"}, "UDP sends one explicit datagram, up to 4 KiB, and waits up to 3 seconds for a response. A UDP send alone does not prove reachability."),
      field("Datagram encoding", udpEncoding), field("UDP request payload", udpPayload));
    udpOptions.hidden = true;
    const consent = el("input", {type: "checkbox", name: "active_probe"});
    const submit = el("button", {type: "submit", class: "btn btn-primary", disabled: true}, "Capture selected evidence");
    const results = el("div", {}, note("Choose a saved endpoint to review its supported capture actions."));
    const previous = button("Previous endpoints", () => {page--; load();});
    const next = button("Next endpoints", () => {page++; load();});
    const position = el("span", {class: "small muted"});
    const selectionSummary = el("p", {class: "small muted"});
    function fillTypes() {
      const chosen = type.value;
      const q = serviceSearch.value.trim().toLowerCase();
      const rows = entries.filter(item => item.key === chosen || [item.key, item.label, item.category, ...(item.categories || [])].join(" ").toLowerCase().includes(q));
      type.replaceChildren(el("option", {value: ""}, "All technologies"), ...rows.map(item => el("option", {value: item.key, selected: item.key === chosen}, item.label || item.key)));
    }
    fillTypes();
    const requestedType = params.get("technology") || params.get("type") || "";
    if (entries.some(item => item.key === requestedType)) type.value = requestedType;
    serviceSearch.addEventListener("input", fillTypes);
    const selected = () => targets.find(item => String(item.id) === target.value);
    const selectedActions = () => [...actions.querySelectorAll("input:checked:not(:disabled)")].map(input => input.value);
    function updateSubmit() {
      udpOptions.hidden = !selectedActions().includes("udp");
      submit.disabled = busy || !selected() || !consent.checked || !selectedActions().length;
    }
    function drawActions() {
      const endpoint = selected();
      consent.checked = false; udpPayload.value = ""; udpEncoding.value = "text";
      const allowed = Array.isArray(endpoint?.fetch_actions) ? endpoint.fetch_actions : [];
      const defaults = Array.isArray(endpoint?.default_actions) ? endpoint.default_actions : [];
      actions.replaceChildren(...ACTIONS.map(([key, label]) => {
        const tools = catalog.tools || {};
        const unavailable = !allowed.includes(key) || key === "http" && tools.curl === false || key === "httpx" && tools.httpx === false || key === "screenshot" && endpoint?.service_type !== "vnc" && tools.playwright !== true && tools.gowitness === false;
        return el("label", {class: "check-row"}, el("input", {type: "checkbox", name: "actions", value: key, disabled: unavailable, checked: !unavailable && defaults.includes(key)}), el("span", {}, label + (unavailable ? " · unavailable" : "")));
      }));
      selectionSummary.textContent = endpoint ? [endpoint.address + ":" + endpoint.port, endpoint.transport, endpoint.service_type].filter(Boolean).join(" · ") : "Actions become available after you select an endpoint.";
      updateSubmit();
    }
    target.addEventListener("change", () => { errors.replaceChildren(); drawActions(); });
    actions.addEventListener("change", updateSubmit);
    consent.addEventListener("change", updateSubmit);
    type.addEventListener("change", () => { page = 1; initialId = ""; load(); });
    const filters = el("form", {class: "form-stack"}, field("Find a service", serviceSearch), field("Service / technology", type), el("div", {class: "preview-target-search"}, field("Search saved endpoints", query), el("button", {type: "submit", class: "btn"}, "Find saved endpoints")));
    filters.addEventListener("submit", event => {event.preventDefault(); page = 1; initialId = ""; load();});
    const form = el("form", {class: "form-stack mt", "data-preview-form": ""},
      field("Saved endpoint", target), status,
      el("nav", {class: "table-pager", "aria-label": "Saved endpoint pages"}, position, previous, next),
      selectionSummary,
      el("fieldset", {}, el("legend", {class: "form-section-title"}, "Evidence to capture"), actions), udpOptions,
      el("label", {class: "check-row"}, consent, el("span", {}, el("strong", {}, "Allow direct target requests"), el("small", {}, "Contact this saved endpoint using only the selected actions when I submit."))), errors, submit);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      errors.replaceChildren();
      const endpoint = selected(), chosen = selectedActions();
      if (busy) return;
      if (!endpoint || !consent.checked || !chosen.length || chosen.some(action => !endpoint.fetch_actions.includes(action))) {
        errors.append(note("Choose an endpoint, a supported action and enable direct requests before submitting.", true));
        return;
      }
      const payload = {actions: chosen};
      if (chosen.includes("udp")) {
        const encoding = udpEncoding.value;
        const hex = udpPayload.value.replace(/\s/g, "");
        const bytes = encoding === "hex" ? hex.length / 2 : new Blob([udpPayload.value]).size;
        if (encoding === "hex" && (!/^[0-9a-f]*$/i.test(hex) || hex.length % 2)) {
          errors.append(note("Hex payload must contain complete byte pairs.", true)); return;
        }
        if (!bytes || bytes > 4096) {
          errors.append(note("UDP capture requires a nonempty request payload of at most 4 KiB.", true)); return;
        }
        payload.options = {udp: {payload: encoding === "hex" ? hex : udpPayload.value, encoding, timeout_seconds: 3}};
      }
      busy = true; updateSubmit();
      try {
        // Do not abort an accepted write when filters change: retain its real run receipt.
        const response = await request("/endpoints/" + encodeURIComponent(endpoint.id) + "/fetch", payload);
        if (disposed) return;
        const runs = response.runs || (response.run ? [response.run] : []);
        if (!runs.length || runs.some(run => !run.id)) throw Error("No run receipt was returned. Check Runs before trying again.");
        runController.abort(); runController = new AbortController();
        results.replaceChildren(note("Submitted for " + endpoint.address + ":" + endpoint.port + ". Capture success is determined by the run outcome."), el("a", {class: "btn btn-small", href: "/runs/"}, "View all runs →"));
        if (window.LeakScopeRunLogs?.preference) results.append(window.LeakScopeRunLogs.preference());
        for (const run of runs) {
          const holder = el("div", {class: "mt", "data-preview-run": run.id});
          results.append(holder);
          if (window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(run, holder, null, {signal: runController.signal});
          else {
            holder.append(el("p", {class: "mono"}, run.id + " · " + (run.state || run.status || "queued")));
            if (run.error) holder.append(note(run.error, true));
            if (window.LeakScopeRunLogs) holder.append(window.LeakScopeRunLogs.panel(run, {key: "preview-" + run.id}));
          }
        }
      } catch (error) {
        if (!disposed) errors.replaceChildren(note(error.message, true));
      } finally { busy = false; consent.checked = false; updateSubmit(); }
    });
    root.replaceChildren(sourceBar,
      el("div", {class: "page-heading"}, el("div", {}, el("h1", {}, "Preview"), el("p", {}, "Review saved endpoints across the full service catalog, then capture evidence deliberately."))),
      note("Browsing saved evidence makes no target requests. Protocol captures return a fingerprint or request / response transcript where supported. Full data previews use the legacy data adapters."),
      el("div", {class: "grid-two"}, panel("Select saved evidence", entries.length + " technologies · database, web, protocol and OT services", filters, form), panel("Capture results", "Real run status, logs and saved output", results)));
    drawActions();
    async function load() {
      const version = ++generation;
      controller?.abort(); controller = new AbortController();
      targets = []; target.disabled = true; consent.checked = false;
      target.replaceChildren(el("option", {value: ""}, "Loading saved endpoints…"));
      previous.disabled = next.disabled = true; position.textContent = "Loading…";
      status.replaceChildren(note("Loading saved endpoints…")); errors.replaceChildren(); drawActions();
      const entry = entries.find(item => item.key === type.value);
      const filters = new URLSearchParams({page: String(page), page_size: "50"});
      if (type.value) filters.set("technology", entry?.base_type || type.value);
      if (query.value.trim()) filters.set("q", query.value.trim());
      try {
        const response = await request("/endpoints?" + filters, undefined, controller.signal);
        if (disposed || version !== generation) return;
        const rows = response.results || [];
        if (initialId && !rows.some(item => String(item.id) === initialId)) {
          const detail = await request("/endpoints/" + encodeURIComponent(initialId), undefined, controller.signal);
          if (disposed || version !== generation) return;
          if (detail.endpoint) rows.push(detail.endpoint);
        }
        targets = rows; pages = response.pages || 1; page = response.page || page;
        target.replaceChildren(el("option", {value: ""}, rows.length ? "Choose a saved endpoint" : "No saved endpoints"), ...rows.map(item => el("option", {value: item.id, selected: String(item.id) === initialId}, item.address + ":" + item.port + " · " + (item.service_type || item.transport || "service"))));
        initialId = ""; target.disabled = !rows.length;
        position.textContent = "Page " + page + " of " + pages + " · " + (response.count ?? rows.length) + " saved endpoints";
        previous.disabled = page <= 1; next.disabled = page >= pages;
        status.replaceChildren(...(!rows.length ? [note("No saved endpoints match these filters. Save observations through Discover, or choose another technology."), el("a", {class: "btn btn-small", href: "/discover/"}, "Open Discover →")] : []));
        if (response.scope?.scope_truncated) status.append(note("This technology filter uses a bounded sample of saved evidence; some endpoints may be omitted."));
        drawActions();
      } catch (error) {
        if (disposed || version !== generation || error.name === "AbortError") return;
        target.replaceChildren(el("option", {value: ""}, "Unable to load saved endpoints"));
        position.textContent = "Saved endpoints unavailable";
        status.replaceChildren(note(error.message, true), button("Retry saved endpoints", load));
      }
    }
    load();
    return () => {disposed = true; generation++; controller?.abort(); runController.abort();};
  }
  window.LeakScopePreview = {render};
})();
