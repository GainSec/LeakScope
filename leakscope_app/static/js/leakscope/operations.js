(function () {
  "use strict";
  const API = "/api/operations";
  let disposePage = () => {};
  const visualOpen = () => typeof window.innerWidth !== "number" || window.innerWidth > 800;
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, text(value));
  }
  function text(v) {
    return v == null
      ? "—"
      : typeof v === "object"
        ? JSON.stringify(v, null, 2)
        : String(v);
  }
  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") e.className = value;
      else if (key === "text") e.textContent = text(value);
      else if (key.startsWith("on") && typeof value === "function")
        e.addEventListener(key.slice(2), value);
      else if (["checked", "disabled", "selected"].includes(key))
        e[key] = !!value;
      else e.setAttribute(key, value === true ? "" : text(value));
    }
    children.flat(Infinity).forEach((child) => {
      if (child != null && child !== false)
        e.append(
          child instanceof Node ? child : document.createTextNode(text(child)),
        );
    });
    return e;
  }
  function btn(label, action, cls = "btn") {
    return el("button", { type: "button", class: cls, onclick: action }, label);
  }
  function note(message, error = false) {
    return el(
      "div",
      {
        class: "notice" + (error ? " notice-error" : ""),
        role: error ? "alert" : "status",
      },
      message,
    );
  }
  function input(name, value = "", attrs = {}) {
    const e = el("input", {
      name,
      class: "input",
      maxlength: "1000",
      ...attrs,
    });
    e.value = value;
    return e;
  }
  function select(name, choices, value) {
    return el(
      "select",
      { name, class: "select" },
      choices.map(([key, label]) =>
        el(
          "option",
          { value: key, selected: String(value) === String(key) },
          label,
        ),
      ),
    );
  }
  function field(label, control, cls = "") {
    return el(
      "label",
      { class: "field " + cls },
      el("span", { class: "field-label" }, label),
      control,
    );
  }
  function check(label, name, checked = false, disabled = false) {
    return el(
      "label",
      { class: "check-row" },
      el("input", { type: "checkbox", name, checked, disabled }),
      el("span", {}, label),
    );
  }
  function panel(title, body, description) {
    return el(
      "section",
      { class: "panel" },
      el(
        "div",
        { class: "panel-body" },
        el("h2", {}, title),
        description ? el("p", { class: "muted" }, description) : null,
        body,
      ),
    );
  }
  function heading(title, description) {
    return el(
      "div",
      { class: "page-heading" },
      el("div", {}, el("h1", {}, title), el("p", {}, description)),
    );
  }
  function link(label, path, cls = "btn") {
    return el("a", { href: path, class: cls }, label);
  }
  function safeURL(value, local = false) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const u = new URL(value, local ? location.origin : undefined);
      return ["http:", "https:"].includes(u.protocol) &&
        !u.username &&
        !u.password
        ? u.href
        : null;
    } catch (_) {
      return null;
    }
  }
  function date(value) {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? text(value) : d.toLocaleString();
  }
  function number(value) {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString()
      : "—";
  }
  function list(value) {
    return [
      ...new Set(
        String(value || "")
          .split(/[,\n]/)
          .map((x) => x.trim())
          .filter(Boolean),
      ),
    ];
  }
  function table(headers, rows, options = {}) {
    const grid = el("table", {}, el("thead", {}, el("tr", {}, headers.map(h => el("th", {scope:"col"}, h)))), el("tbody", {}, rows));
    window.LeakScopeTables?.enhance(grid, options);
    return el("div", {class:"table-scroll"}, grid);
  }
  function badge(value) {
    return el("span", { class: "badge" }, text(value));
  }
  async function request(path, data, signal) {
    const response = await fetch(API + path, {
      credentials: "same-origin",
      signal,
      method: data === undefined ? "GET" : "POST",
      headers: {
        Accept: "application/json",
        ...(data === undefined
          ? {}
          : {
              "Content-Type": "application/json",
              "X-CSRFToken":
                document.querySelector("meta[name=csrf-token]")?.content || "",
            }),
      },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
    });
    let result;
    try {
      result = await response.json();
    } catch (_) {
      throw new Error("The server returned an unexpected response.");
    }
    if (!response.ok)
      throw new Error(
        text(result.error || result.detail || "Request failed. Try again."),
      );
    return result;
  }
  async function action(button, output, callback) {
    button.disabled = true;
    output.replaceChildren();
    try {
      await callback();
    } catch (error) {
      output.replaceChildren(note(error.message, true));
    } finally {
      button.disabled = false;
    }
  }
  function showRuns(output, response) {
    const runs = response.runs || (response.run ? [response.run] : []);
    output.replaceChildren(
      note(
        runs.length
          ? runs.length +
              " job(s) submitted. View Runs for progress and terminal outcomes."
          : "Request completed.",
      ),
      link("View runs →", "/runs/", "btn btn-small"),
    );
    for (const run of runs) {
      if(run.state === "failed")output.append(note(run.error || "The job failed to dispatch.", true));
      if(window.LeakScopeRunLogs)output.append(window.LeakScopeRunLogs.panel(run,{key:"operation-"+run.id}));
    }
  }
  function profileFor(record, catalog) {
    const entry =
      (catalog.results || []).find(
        (item) => item.key === (record.service_type || record.key),
      ) || {};
    const http =
      ["http", "https"].includes(record.scheme) ||
      ["http", "https"].includes(record.transport) ||
      entry.http === true ||
      entry.http_capture === true;
    const allowed = Array.isArray(record.fetch_actions)
      ? record.fetch_actions
      : Array.isArray(entry.fetch_actions)
        ? entry.fetch_actions
        : [
            ...(http ? ["http", "httpx", "screenshot"] : []),
            ...(entry.identity === true ? ["identity"] : []),
            ...(record.transport === "tcp" || entry.transport === "tcp"
              ? ["tcp"]
              : []),
            ...(record.transport === "udp" || entry.transport === "udp"
              ? ["udp"]
              : []),
          ];
    const defaults = Array.isArray(record.default_actions)
      ? record.default_actions
      : Array.isArray(entry.default_actions)
        ? entry.default_actions
        : http
          ? ["http"]
          : entry.identity === true
            ? ["identity"]
            : [];
    return { allowed, defaults, nativeVnc: (record.service_type || record.key) === "vnc" };
  }
  function actionsControl(
    tools,
    name = "actions",
    allowed = null,
    defaults = ["http"],
    manual = false,
    nativeVnc = false,
  ) {
    const group = el("div", { class: "ops-actions" });
    for (const [key, label, tool] of [
      ["http", "Fetch response (curl)", "curl"],
      ["httpx", "HTTP fingerprint (httpx)", "httpx"],
      ["screenshot", nativeVnc ? "Screenshot (native VNC)" : "Screenshot (browser)", null],
      ["identity", "Protocol identity (where supported)", null],
      ["check", "Connection reachability", null],
      ["tcp", "TCP request / response", null],
      ["udp", "UDP request / response", null],
    ]) {
      const missing =
        (key === "udp" && !manual) ||
        (key === "screenshot" && !nativeVnc && tools?.playwright !== true && tools?.gowitness === false) ||
        (tool && tools?.[tool] === false) ||
        (allowed !== null && !allowed.includes(key));
      const box = check(
        label + (missing ? " · unavailable" : ""),
        name,
        defaults.includes(key) && !missing,
        !!missing,
      );
      box.querySelector("input").value = key;
      group.append(box);
    }
    return group;
  }
  function selectedActions(group) {
    return [...group.querySelectorAll("input:checked:not(:disabled)")].map(
      (x) => x.value,
    );
  }
  function tagsEditor(kind, key, tags = [], onSaved, assignments = null) {
    const tagsInput = input("tags", "", {
      placeholder: "Manual tags, comma-separated",
      maxlength: "1000",
      "aria-label": "Manual tags",
    });
    tagsInput.disabled = true;
    const inherited = el("div", { class: "ops-inherited-tags" });
    const output = el("div", {}, note("Loading tag assignments…"));
    const names = (values) =>
      (values || [])
        .map((value) =>
          typeof value === "string" ? value : value.name || value.tag || "",
        )
        .filter(Boolean);
    function showAssignments(data) {
      if (
        !Array.isArray(data.manual_tags) ||
        !Array.isArray(data.inherited_tags)
      )
        throw new Error(
          "Tag assignment details are unavailable. Refresh before editing tags.",
        );
      tagsInput.value = names(data.manual_tags).join(", ");
      inherited.replaceChildren(
        ...(data.inherited_tags.length
          ? [
              el(
                "span",
                { class: "small muted" },
                "Inherited from source rules · read-only",
              ),
              el(
                "div",
                { class: "row-actions" },
                ...names(data.inherited_tags).map((name) => badge(name)),
              ),
            ]
          : []),
      );
      tagsInput.disabled = false;
      save.disabled = false;
    }
    const save = btn(
      "Save tags",
      () =>
        action(save, output, async () => {
          const manual = list(tagsInput.value);
          const data = await request("/tags", {
            target_kind: kind,
            target_key: String(key),
            tags: manual,
          });
          tagsInput.value = names(data.manual_tags || manual).join(", ");
          if (Array.isArray(data.inherited_tags)) showAssignments(data);
          if (onSaved) onSaved(data.tags || manual);
          output.replaceChildren(
            note(
              "Manual tags saved. Inherited rule assignments are unchanged.",
            ),
          );
        }),
      "btn btn-small",
    );
    save.disabled = true;
    async function load() {
      try {
        const data =
          assignments &&
          Array.isArray(assignments.manual_tags) &&
          Array.isArray(assignments.inherited_tags)
            ? assignments
            : await request(
                "/tags?" +
                  new URLSearchParams({
                    target_kind: kind,
                    target_key: String(key),
                  }),
              );
        showAssignments(data);
        output.replaceChildren();
      } catch (error) {
        output.replaceChildren(
          note(error.message, true),
          btn("Retry loading tags", load, "btn btn-small"),
        );
      }
    }
    load();
    return el(
      "div",
      { class: "ops-tags" },
      inherited,
      el("div", { class: "ops-inline" }, tagsInput, save),
      output,
    );
  }
  function openTags(kind, key, currentTags = [], onSaved) {
    document
      .querySelectorAll("dialog.ops-tags-dialog")
      .forEach((dialog) => dialog.remove());
    const dialog = el("dialog", {
      class: "run-dialog ops-tags-dialog",
      "aria-labelledby": "ops-tags-title",
    });
    const close = () => {
      if (typeof dialog.close === "function") dialog.close();
      dialog.remove();
    };
    const closeButton = btn("×", close, "icon-button");
    closeButton.setAttribute("aria-label", "Close tags");
    dialog.append(
      el(
        "div",
        { class: "dialog-heading" },
        el("h2", { id: "ops-tags-title" }, "Edit tags"),
        closeButton,
      ),
      tagsEditor(kind, key, currentTags, (tags) => {
        currentTags.splice(0, currentTags.length, ...tags);
        if (onSaved) onSaved(tags);
      }),
    );
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    document.body.append(dialog);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }
  function preservePagerFocus(control, oppositeLabel, container, redraw) {
    const focused = document.activeElement === control;
    const label = control.textContent;
    redraw();
    if (!focused) return;
    const enabled = [...container.querySelectorAll("button")].filter(button => !button.disabled);
    (enabled.find(button => button.textContent === label) || enabled.find(button => button.textContent === oppositeLabel))?.focus({preventScroll:true});
  }
  async function catalogPage(root) {
    const data = await request("/catalog");
    const query = input("catalog_search", "", {
      placeholder: "Search technologies, protocols and ports",
      "aria-label": "Search technologies",
    });
    const categoryNames = {
      database: "Databases",
      storage: "Data storage",
      ot: "ICS / SCADA / OT",
      iot: "IoT / embedded",
      messaging: "Messaging",
      web: "Web / applications",
      other: "Other services",
    };
    const categoriesFor = (row) =>
      row.categories?.length ? row.categories : [row.category || "other"];
    const categories = [
      ...new Set((data.results || []).flatMap(categoriesFor)),
    ].sort();
    const category = select(
      "category",
      [
        ["", "All categories"],
        ...categories.map((value) => [value, categoryNames[value] || value]),
      ],
      "",
    );
    const queryMode = select(
      "catalog_query_mode",
      [
        ["fingerprint", "All indexed ports · response fingerprints"],
        ["nonstandard", "Exclude reference ports · optional"],
        ["ports", "Only reference ports · optional candidates"],
      ],
      "fingerprint",
    );
    const results = el("div");
    let catalogPageNumber = 1;
    function draw() {
      const q = query.value.trim().toLowerCase();
      const rows = (data.results || []).filter(
        (item) =>
          (!category.value || categoriesFor(item).includes(category.value)) &&
          [
            item.key,
            item.label,
            ...categoriesFor(item).flatMap((value) => [
              value,
              categoryNames[value],
            ]),
            ...(item.ports || []),
          ]
            .join(" ")
            .toLowerCase()
            .includes(q),
      );
      const pages = Math.max(1,Math.ceil(rows.length / 25));
      catalogPageNumber = Math.max(1,Math.min(pages,catalogPageNumber));
      const start = (catalogPageNumber - 1) * 25;
      results.replaceChildren(
        el(
          "p",
          { class: "small muted" },
          rows.length +
            " technologies · showing " + (rows.length ? start+1 : 0) + "–" + Math.min(start+25,rows.length) + " · fingerprints identify indexed evidence; conventional ports provide unverified candidates.",
        ),
      );
      if (!rows.length) {
        results.append(note("No technologies match these filters."));
        return;
      }
      const grid = el("div", { class: "service-card-grid" });
      results.append(grid);
      const officialLink = (label, value) => {
        if (!value) return null;
        try {
          const url = new URL(value);
          if (!["https:", "http:"].includes(url.protocol)) return null;
          return el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", class: "service-card-official-link" }, label);
        } catch (_) { return null; }
      };
      rows.slice(start, start + 25).forEach((item, index) => {
        const queries = el(
          "details",
          { class: "ops-query-details" },
          el("summary", {}, "Provider queries · " + queryMode.selectedOptions[0].textContent),
        );
        const variants =
          item.query_variants?.[queryMode.value] ||
          (!item.query_variants ? item.queries || {} : {});
        if (!Object.keys(variants).length)
          queries.append(note("No verified query is available for this strategy."));
        for (const [provider, value] of Object.entries(variants)) {
          const queryText = typeof value === "string" ? value : value?.query;
          const supported = value?.supported !== false && !!queryText;
          queries.append(
            el("div", { class: "ops-provider-preset" },
              el("strong", {}, provider),
              supported && value?.reason
                ? el("p", { class: "small muted" }, "Matches: " + value.reason) : null,
              supported ? el("pre", { class: "code-box" }, queryText)
                : note(value?.reason || "No verified query is available for this provider and strategy."),
              value?.basis ? el("p", { class: "small muted" }, "Basis: " + value.basis) : null,
              supported ? link("Create lookup", "/lookups/?" + new URLSearchParams({
                service_type: item.key, provider, query: queryText,
              }), "btn btn-small") : null,
            ),
          );
        }
        const metadata = item.presentation || {};
        const localIcon = /^\/static\/img\/service-icons\/[a-z0-9_-]+\.(?:svg|jpg)$/.test(metadata.icon_url || "");
        const backId = "catalog-card-back-" + (start + index);
        const capabilities = el("dl", { class: "service-card-capabilities" });
        for (const [label, value] of [
          ["Detection", item.detection_confidence], ["Identity", item.identity],
          ["Inspection", item.inspection], ["Export", item.export],
        ]) {
          if (value !== false && value != null && value !== "")
            capabilities.append(el("dt", {}, label), el("dd", {}, value === true ? "Supported" : text(value)));
        }
        let flipped = false;
        const setFlipped = (value) => {
          flipped = value;
          card.classList.toggle("is-flipped", value);
          const active = value ? back : front;
          const inactive = value ? front : back;
          active.removeAttribute("inert");
          active.setAttribute("aria-hidden", "false");
          front.setAttribute("aria-expanded", String(value));
          (value ? close : front).focus({ preventScroll: true });
          inactive.setAttribute("inert", "");
          inactive.setAttribute("aria-hidden", "true");
        };
        const front = el("button", {
          type: "button", class: "service-card-face service-card-front",
          "aria-label": "Show " + item.label + " details", "aria-controls": backId,
          "aria-expanded": "false", "aria-hidden": "false", onclick: () => setFlipped(true),
        },
          el("span", { class: "service-card-kicker" }, categoriesFor(item).map(value => categoryNames[value] || value).join(" · ")),
          el("span", { class: "service-card-art", "aria-hidden": "true" },
            localIcon ? el("img", { src: metadata.icon_url, alt: "", width: "72", height: "72", loading: "lazy", decoding: "async" })
              : el("span", { class: "service-card-emblem" }, "◇")),
          el("span", { class: "service-card-name" + (item.label.length > 28 ? " is-long" : "") }, item.label),
          el("span", { class: "service-card-kind" }, metadata.icon_kind === "brand" ? "Technology profile" : metadata.icon_kind === "protocol" ? "Generic protocol emblem" : "Generic technology emblem"),
          el("span", { class: "service-card-hint", "aria-hidden": "true" }, "Explore profile", el("span", {}, "↗")),
        );
        const close = btn("↶ Front", () => setFlipped(false), "service-card-close");
        close.setAttribute("aria-label", "Show " + item.label + " front");
        const official = el("nav", { class: "service-card-official", "aria-label": item.label + " official resources" },
          officialLink("Documentation", metadata.documentation_url), officialLink("Download", metadata.download_url));
        const captureLabels = {
          tcp: "TCP request / response", udp: "UDP request / response", http: "HTTP response",
          screenshot: "Screenshot for HTTP interfaces", identity: "Protocol identity", httpx: "HTTP fingerprint",
        };
        const back = el("section", {
          id: backId, class: "service-card-face service-card-back", "aria-label": item.label + " details",
          "aria-hidden": "true", inert: true,
        },
          el("div", { class: "service-card-back-heading" }, el("h2", {}, item.label), close),
          el("div", { class: "service-card-back-content", tabindex: "0", "aria-label": item.label + " profile content" },
            (item.ports || []).length ? el("p", { class: "service-card-ports" }, "Reference ports · " + (item.transport ? item.transport + ": " : "") + item.ports.join(", ")) : null,
            capabilities.childElementCount ? capabilities : null,
            item.fingerprint_summary ? el("p", { class: "small muted" }, item.fingerprint_summary) : null,
            item.fetch_actions?.length ? el("p", { class: "small muted" }, "Evidence capture: " + item.fetch_actions.map(value => captureLabels[value] || value).join(" · ")) : null,
            official.childElementCount ? official : null,
            link("Open in Discover", "/discover/?" + new URLSearchParams({service_type: item.key, query_mode: queryMode.value}), "btn btn-small"),
            queries,
          ),
        );
        const card = el("article", { class: "service-card", onkeydown: (event) => {
          if (event.key === "Escape" && flipped) { event.preventDefault(); event.stopPropagation(); setFlipped(false); }
        } }, el("div", { class: "service-card-rotor" }, front, back));
        grid.append(card);
      });
      const previous = btn("Previous technologies",()=>preservePagerFocus(previous,"Next technologies",results,()=>{catalogPageNumber--;draw();}),"btn btn-small");
      const next = btn("Next technologies",()=>preservePagerFocus(next,"Previous technologies",results,()=>{catalogPageNumber++;draw();}),"btn btn-small");
      previous.disabled=catalogPageNumber<=1;next.disabled=catalogPageNumber>=pages;
      results.append(el("div",{class:"pager-actions"},previous,el("span",{class:"small muted"},"Page "+catalogPageNumber+" of "+pages),next));
    }
    const reset = () => {catalogPageNumber=1;draw();};
    query.addEventListener("input", reset);
    category.addEventListener("change", reset);
    queryMode.addEventListener("change", reset);
    const signaturesHost=el('div');
    root.replaceChildren(
      heading(
        "Technology catalog",
        "Database, storage, ICS / SCADA / OT and IoT profiles with provider-specific evidence and capture capabilities.",
      ),
      signaturesHost,
      el(
        "div",
        { class: "toolbar" },
        field("Search", query),
        field("Category", category),
        field("Query strategy", queryMode),
      ),
      results,
    );
    draw();
    if(window.LeakScopeSignatures)await window.LeakScopeSignatures.mount(signaturesHost);
  }
  async function lookupsPage(root, signal, seed = null) {
    const data = await request("/catalog", undefined, signal);
    if (signal?.aborted) return;
    const params = new URLSearchParams(location.search);
    const form = el("form", { class: "ops-form" });
    const name = input("name", "", {
      required: true,
      maxlength: "200",
      placeholder: "Name this lookup",
    });
    const provider = select(
      "provider",
      [
        ["shodan", "Shodan"],
        ["zoomeye", "ZoomEye"],
        ["netlas", "Netlas"],
        ["research", "Web research"],
      ],
      params.get("provider") || "netlas",
    );
    const service = select(
      "service_type",
      [
        ["", "Generic service"],
        ...(data.results || []).map((item) => [item.key, item.label]),
      ],
      params.get("service_type") || "",
    );
    const query = el("textarea", {
      name: "query",
      class: "input ops-query-editor",
      rows: "4",
      maxlength: "8000",
      required: true,
    });
    query.value = params.get("query") || "";
    const preset = btn("Use catalog query", () => {
      const item = (data.results || []).find(
        (item) => item.key === service.value,
      );
      const value = item?.query_variants?.fingerprint?.[provider.value] ||
        (!item?.query_variants ? item?.queries?.[provider.value] : null);
      if (!value || (typeof value === "object" && !value.supported)) {
        output.replaceChildren(el("p", { class: "muted" }, value?.reason || "No catalog query is available for this provider."));
        return;
      }
      output.replaceChildren();
      query.value = typeof value === "string" ? value : value?.query || "";
    });
    const network = input("network", "", {
      placeholder: "Optional IP or CIDR",
    });
    const country = input("country", "", {
      maxlength: "2",
      placeholder: "Country code",
    });
    const exclude = input("exclude", "", {
      placeholder: "Comma-separated exclusions",
    });
    const schedule = select(
      "schedule",
      [
        ["manual", "Manual only"],
        ["interval", "Every interval"],
        ["cron", "Cron schedule"],
      ],
      "interval",
    );
    const interval = input("interval_seconds", 3600, {
      type: "number",
      min: "60",
      max: "31536000",
    });
    const cron = input("cron", "", {
      placeholder: "0 */6 * * *",
      maxlength: "100",
    });
    const timezone = input("timezone", "America/New_York", {
      maxlength: "100",
    });
    const tags = input("tags", "", { placeholder: "database, review" });
    const inheritedTags = el("div", { class: "row-actions" });
    const pages = input("page_count", 1, {
      type: "number",
      min: "1",
      max: "20",
    });
    const pageSize = input("page_size", 100, {
      type: "number",
      min: "1",
      max: "100",
      step: "1",
    });
    const pageSizeField = field("ZoomEye results per page", pageSize);
    const maxResults = input("max_results", 100, {
      type: "number",
      min: "1",
      max: "10000",
    });
    const maxRuns = input("max_daily_runs", 24, {
      type: "number",
      min: "1",
      max: "1440",
    });
    const cooldown = input("cooldown_seconds", 3600, {
      type: "number",
      min: "60",
      max: "31536000",
    });
    const auto = check(
      "Automatically fetch new matching targets",
      "auto_fetch",
      true,
    );
    const enabled = check("Enable this lookup", "enabled", true);
    const collectionLabels = {
      demo: "Demo", passive: "Passive", semi_passive: "Semi-passive", screenshot: "Screenshot", full: "Full",
    };
    const collectionMode = select("collection_mode", [
      ["", "Custom actions"], ...Object.entries(collectionLabels),
    ], Object.hasOwn(collectionLabels, params.get("collection_mode")) ? params.get("collection_mode") : "");
    const collectionHint = el("p", { class: "hint", "aria-live": "polite" });
    let customActions = null, customAuto = true, previousCollection = collectionMode.value;
    const actions = actionsControl(data.tools);
    const actionsField = el("div", { class: "ops-wide" }, el("h3", {}, "Automatic fetch profile"), actions);
    function collectionControls() {
      const research = provider.value === "research";
      const presetMode = !!collectionMode.value;
      const demoMode = collectionMode.value === "demo";
      pages.disabled = maxResults.disabled = research || demoMode;
      for (const control of [pages,maxResults]) {const group=control.closest(".field"); if (group) group.hidden=demoMode;}
      pageSizeField.hidden=demoMode || research || provider.value !== "zoomeye";
      pageSize.disabled = research || demoMode || provider.value !== "zoomeye";
      collectionMode.disabled = false;
      for (const option of collectionMode.options) option.disabled = research && !["", "passive", "demo"].includes(option.value);
      auto.hidden = presetMode || research;
      auto.querySelector("input").disabled = presetMode || research;
      if (presetMode) auto.querySelector("input").checked = !research && !["passive", "demo"].includes(collectionMode.value);
      actionsField.hidden = presetMode || research;
      if (presetMode || research) actions.querySelectorAll("input").forEach(control => { control.disabled = true; });
      collectionHint.textContent = collectionMode.value === "demo"
        ? "Demo uses real minimal requests: one provider page/result per query, or one research source and a short synthesis. No automatic target captures. Provider minimum billing still applies; normal limits are retained."
        : research
        ? "Web research produces query suggestions and makes no direct target requests."
        : ({
            passive: "Provider results only. No direct target requests.",
            semi_passive: "Bounded HTTP fingerprints, native handshakes or TCP reachability checks after collection.",
            screenshot: "Screenshots of matching web interfaces; unsupported targets are reported as skipped.",
            full: "All applicable capture actions after collection, with unsupported operations reported as skipped.",
          }[collectionMode.value] || "Choose automatic capture actions explicitly. Existing custom lookup behavior is preserved.");
    }

    function lookupActions(savedActions) {
      const entry = (data.results || []).find(
        (item) => item.key === service.value,
      );
      const profile = entry
        ? profileFor(entry, data)
        : { allowed: null, defaults: [] };
      const controls = actionsControl(
        data.tools,
        "actions",
        profile.allowed,
        savedActions || profile.defaults,
        false,
        profile.nativeVnc,
      );
      actions.replaceChildren(...controls.children);
      if (collectionMode.value || provider.value === "research")
        actions.querySelectorAll("input").forEach(control => { control.disabled = true; });
    }
    collectionMode.addEventListener("change", () => {
      if (!previousCollection) {
        customActions = selectedActions(actions);
        customAuto = auto.querySelector("input").checked;
      }
      previousCollection = collectionMode.value;
      if (!collectionMode.value) {
        lookupActions(customActions || undefined);
        auto.querySelector("input").checked = customAuto;
      } else lookupActions();
      collectionControls();
    });
    service.addEventListener("change", () => { lookupActions(); collectionControls(); });
    lookupActions();
    const output = el("div", { class: "ops-wide" });
    const account = el("div", { class: "ops-wide" });
    function providerNotice() {
      const research = provider.value === "research";
      if (research && collectionMode.value && !["passive", "demo"].includes(collectionMode.value)) collectionMode.value = "passive";
      previousCollection = collectionMode.value;
      pageSize.disabled = provider.value !== "zoomeye";
      pageSizeField.hidden = pageSize.disabled;
      query.maxLength = research ? 300 : 8000;
      service.disabled = preset.disabled = research;
      for (const control of [network, country, exclude, pages, maxResults])
        control.disabled = research;
      auto.querySelector("input").disabled = research;
      if (research) {
        auto.querySelector("input").checked = false;
        actions.querySelectorAll("input").forEach((checkbox) => {
          checkbox.checked = false;
          checkbox.disabled = true;
        });
      } else lookupActions(collectionMode.value ? undefined : selectedActions(actions));
      collectionControls();
      account.replaceChildren(
        research
          ? note(
              "Web research schedules search public documentation through Firecrawl and optional model synthesis. Enter a topic of at most 300 characters; target auto-fetch and asset scope do not apply. Configure Firecrawl and the model on the server.",
            )
          : data.providers?.[provider.value]?.configured
            ? note(
                "The provider is configured. Running or enabling this lookup may consume provider credits.",
              )
            : note(
                "This provider is not configured yet. Save the lookup, then configure its private API key before running it.",
              ),
      );
    }
    provider.addEventListener("change", providerNotice);
    providerNotice();
    const intervalField = field("Interval in seconds", interval);
    const cronField = field("Cron expression", cron);
    function scheduleFields() {
      intervalField.hidden = schedule.value !== "interval";
      cronField.hidden = schedule.value !== "cron";
    }
    schedule.addEventListener("change", scheduleFields);
    scheduleFields();
    const submit = el(
      "button",
      { type: "submit", class: "btn btn-primary" },
      "Save lookup",
    );
    const listArea = el("div", { class: "ops-list" });
    const lookupFilter = input("lookup_filter", "", {placeholder:"Filter saved lookups", "aria-label":"Filter saved lookups"});
    const lookupItems = el("div", {class:"ops-lookup-items"});
    const lookupCount = el("p", {class:"small muted", "aria-live":"polite"});
    const lookupPages = el("div", {class:"pager-actions"});
    let loadedLookups = [], lookupPage = 1, editor;
    const lookupCards = new Map();
    function drawLookups() {
      const term = lookupFilter.value.trim().toLowerCase();
      const matches = loadedLookups.filter(item => [item.name,item.provider,item.service_type,item.query,...(item.tags || [])].join(" ").toLowerCase().includes(term));
      const pages = Math.max(1, Math.ceil(matches.length / 12));
      lookupPage = Math.min(pages, Math.max(1, lookupPage));
      const start = (lookupPage - 1) * 12;
      lookupCount.textContent = matches.length + " matching · " + loadedLookups.length + " loaded lookups · showing "
        + (matches.length ? start + 1 : 0) + "–" + Math.min(start + 12,matches.length) + ".";
      lookupItems.replaceChildren(...matches.slice(start,start+12).map(item => {
        if (!lookupCards.has(item.id)) lookupCards.set(item.id,lookupCard(item,load,data.providers,seedLookup));
        return lookupCards.get(item.id);
      }));
      if (!matches.length) lookupItems.append(note(loadedLookups.length ? "No loaded lookups match this filter." : "No saved lookups yet. Create one or choose a provider preset from the catalog."));
      const previous = btn("Previous lookups",()=>preservePagerFocus(previous,"Next lookups",lookupPages,()=>{lookupPage--;drawLookups();}),"btn btn-small");
      const next = btn("Next lookups",()=>preservePagerFocus(next,"Previous lookups",lookupPages,()=>{lookupPage++;drawLookups();}),"btn btn-small");
      previous.disabled = lookupPage <= 1; next.disabled = lookupPage >= pages;
      lookupPages.replaceChildren(previous,el("span",{class:"small muted"},"Page "+lookupPage+" of "+pages),next);
    }
    lookupFilter.addEventListener("input",()=>{lookupPage=1;drawLookups();});
    listArea.append(el("div",{class:"toolbar"},field("Saved lookup filter",lookupFilter)),lookupCount,lookupItems,lookupPages);
    let editingId = null;
    const cancelEdit = btn(
      "Cancel edit",
      () => seedLookup(null),
      "btn btn-quiet",
    );
    cancelEdit.hidden = true;
    function seedLookup(item) {
      if (editor) editor.open = true;
      editingId = item?.id || null;
      name.value = item?.name || "";
      provider.value = item?.provider || params.get("provider") || "netlas";
      service.value = item?.service_type || params.get("service_type") || "";
      query.value = item?.query || (item ? "" : params.get("query") || "");
      network.value = item?.scope?.network || "";
      country.value = item?.scope?.country || "";
      exclude.value = item?.scope?.exclude || "";
      schedule.value = item
        ? item.cron
          ? "cron"
          : item.interval_seconds
            ? "interval"
            : "manual"
        : "interval";
      interval.value = item?.interval_seconds || 3600;
      cron.value = item?.cron || "";
      timezone.value = item?.timezone || "America/New_York";
      pages.value = item?.page_count || 1;
      pageSize.value = item?.page_size || 100;
      maxResults.value = item?.max_results || 100;
      maxRuns.value = item?.max_daily_runs || 24;
      cooldown.value = item?.cooldown_seconds || 3600;
      tags.value = (item?.manual_tags || []).join(", ");
      inheritedTags.replaceChildren(
        ...(item?.inherited_tags?.length
          ? [
              el(
                "span",
                { class: "small muted" },
                "Inherited tags · read-only",
              ),
              ...item.inherited_tags.map((tag) => badge(tag)),
            ]
          : []),
      );
      collectionMode.value = item?.collection_mode || (item ? "" : params.get("collection_mode") || "");
      if (collectionMode.value && !Object.hasOwn(collectionLabels, collectionMode.value)) collectionMode.value = "";
      previousCollection = collectionMode.value;
      customActions = item?.actions || null;
      customAuto = item?.auto_fetch ?? true;
      auto.querySelector("input").checked = customAuto;
      enabled.querySelector("input").checked = item?.enabled ?? true;
      lookupActions(item?.actions);
      submit.textContent = item ? "Save changes" : "Save lookup";
      cancelEdit.hidden = !item;
      output.replaceChildren();
      providerNotice();
      scheduleFields();
      if (item) form.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }
    async function load() {
      const result = await request("/lookups");
      loadedLookups = Array.isArray(result.results) ? result.results : [];
      lookupCards.clear();drawLookups();
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      action(submit, output, async () => {
        const payload = {
          name: name.value.trim(),
          provider: provider.value,
          query: query.value.trim(),
          service_type: provider.value === "research" ? "" : service.value,
          scope:
            provider.value === "research"
              ? {}
              : {
                  network: network.value.trim(),
                  country: country.value.trim(),
                  exclude: exclude.value.trim(),
                },
          collection_mode: collectionMode.value,
          actions:
            provider.value === "research" || collectionMode.value ? [] : selectedActions(actions),
          auto_fetch:
            provider.value !== "research" && (collectionMode.value
              ? !["passive", "demo"].includes(collectionMode.value) : auto.querySelector("input").checked),
          enabled: enabled.querySelector("input").checked,
          interval_seconds:
            schedule.value === "interval" ? Number(interval.value) : null,
          cron: schedule.value === "cron" ? cron.value.trim() : "",
          timezone: timezone.value.trim(),
          page_count: Number(pages.value),
          page_size:
            provider.value === "zoomeye" ? Number(pageSize.value) : 100,
          max_results: Number(maxResults.value),
          max_daily_runs: Number(maxRuns.value),
          cooldown_seconds: Number(cooldown.value),
          tags: list(tags.value),
        };
        await request(
          editingId
            ? "/lookups/" + encodeURIComponent(editingId) + "/save"
            : "/lookups",
          payload,
        );
        output.replaceChildren(
          note(
            "Lookup saved. Its configured schedule and enabled state now apply.",
          ),
        );
        if (!seed) await load();
        else
          output.append(link("Manage lookups →", "/lookups/", "btn btn-small"));
      });
    });
    form.append(
      field("Name", name),
      field("Provider", provider),
      field("Service type", service),
      field("Schedule", schedule),
      field("Collection mode", collectionMode),
      el("div", { class: "ops-wide" }, collectionHint),
      field("Provider query", query, "ops-wide"),
      el("div", { class: "ops-wide" }, preset),
      field("Network scope", network),
      field("Country scope", country),
      field("Exclusions", exclude),
      intervalField,
      cronField,
      field("Timezone", timezone),
      field("Provider pages", pages),
      pageSizeField,
      field("Result limit", maxResults),
      field("Daily run limit", maxRuns),
      field("Fetch cooldown in seconds", cooldown),
      field("Search tags", tags),
      inheritedTags,
      el("div", { class: "ops-wide ops-actions" }, enabled, auto),
      actionsField,
      account,
      el("div", { class: "ops-wide row-actions" }, submit, cancelEdit),
      output,
    );
    collectionControls();
    if (seed) {
      name.value = String(seed.name || "").slice(0, 200);
      provider.value = seed.provider || "netlas";
      service.value = seed.service_type || "";
      query.value = seed.query || "";
      network.value = seed.scope?.network || "";
      country.value = seed.scope?.country || "";
      exclude.value = seed.scope?.exclude || "";
      schedule.value = "manual";
      auto.querySelector("input").checked = false;
      enabled.querySelector("input").checked = false;
      lookupActions();
      providerNotice();
      scheduleFields();
    }
    editor = el("details",{class:"panel ops-lookup-editor",open:!!seed || !!params.get("query") || !!params.get("service_type")},
      el("summary",{},"Create lookup"),el("div",{class:"panel-body"},form));
    form.addEventListener("invalid",()=>{editor.open=true;},true);
    root.replaceChildren(
      heading(
        "Scheduled lookups",
        "Save provider searches, tag them and define when matching targets are fetched.",
      ),
      editor,
      ...(!seed ? [el("h2", { class: "ops-section-title" }, "Saved lookups"),listArea] : []),
    );
    if (!seed) await load();
    else {
      root.querySelector(".page-heading")?.remove();
      root.querySelector(".ops-section-title")?.remove();
    }
  }
  function openLookup(seed) {
    const controller = new AbortController();
    const dialog = el("dialog", {
      class: "run-dialog ops-lookup-dialog",
      "aria-labelledby": "ops-lookup-title",
    });
    const body = el("div", {}, note("Loading lookup editor…"));
    const close = () => {
      controller.abort();
      if (typeof dialog.close === "function") dialog.close();
      dialog.remove();
    };
    const closeButton = btn("×", close, "icon-button");
    closeButton.setAttribute("aria-label", "Close lookup editor");
    dialog.append(
      el(
        "div",
        { class: "dialog-heading" },
        el("h2", { id: "ops-lookup-title" }, "Create lookup"),
        closeButton,
      ),
      body,
    );
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("close", () => {
      controller.abort();
      dialog.remove();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    document.body.append(dialog);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    const captured = JSON.parse(JSON.stringify(seed || {}));
    lookupsPage(body, controller.signal, captured).catch((error) => {
      if (!controller.signal.aborted)
        body.replaceChildren(note(error.message, true));
    });
  }
  function lookupCard(item, refresh, providers, onEdit) {
    const output = el("div");
    const actions = el("div", { class: "row-actions ops-lookup-actions" });
    const run = btn("Run now", () =>
      action(run, output, async () => {
        showRuns(
          output,
          await request("/lookups/" + encodeURIComponent(item.id) + "/run", {}),
        );
      }),
    );
    run.disabled = providers?.[item.provider]?.configured === false;
    const demoRun = btn("Run Demo", () => action(demoRun, output, async () => {
      showRuns(output, await request("/lookups/" + encodeURIComponent(item.id) + "/run", {demo:true}));
    }));
    demoRun.disabled = run.disabled;
    demoRun.title = "Run the saved query with minimal API usage; its saved limits and schedule are unchanged";
    const toggle = btn(item.enabled ? "Pause" : "Enable", () =>
      action(toggle, output, async () => {
        await request("/lookups/" + encodeURIComponent(item.id) + "/toggle", {
          enabled: !item.enabled,
        });
        await refresh();
      }),
    );
    const remove = btn(
      "Delete",
      () =>
        action(remove, output, async () => {
          await request(
            "/lookups/" + encodeURIComponent(item.id) + "/delete",
            {},
          );
          await refresh();
        }),
      "btn btn-small btn-quiet",
    );
    actions.append(
      run,
      demoRun,
      btn("Edit", () => onEdit(item), "btn btn-small"),
      toggle,
      remove,
    );
    const schedule = item.cron ? item.cron + " " + text(item.timezone)
      : item.interval_seconds ? "Every " + item.interval_seconds + " seconds" : "Manual only";
    const configuration = el("details",{class:"ops-lookup-details"},el("summary",{},"Query, scope, tags and limits"));
    let expanded = false;
    configuration.addEventListener("toggle",()=>{
      if (!configuration.open || expanded) return;
      expanded = true;
      configuration.append(el("div",{class:"ops-lookup-configuration"},
        el("pre",{class:"code-box"},item.query || ""),
        item.last_error ? note(item.last_error,true) : null,
        dataView(item,"Saved lookup configuration"),
        tagsEditor("lookup",item.id,item.tags || [],undefined,item)));
    });
    return el("section",{class:"panel ops-lookup-row","data-lookup-id":item.id},
      el("div",{class:"ops-lookup-summary"},
        el("div",{},el("h3",{},item.name),
          el("p",{class:"ops-lookup-meta"},text(item.provider)+" · "+(item.service_type || "Generic service")+" · "+(item.enabled ? "Enabled" : "Paused")+" · Last result: "+(item.last_state || "not run")),
          el("p",{class:"small muted"},schedule+" · Next: "+date(item.next_run_at)+" · Last: "+date(item.last_run_at))),
        actions),configuration,output);
  }

  // One inline action per row; everything else goes in this menu. Items after a null entry sit
  // below a separator, which is where destructive actions belong.
  function rowMenu(items) {
    const body = el("div", {class: "ls-popover-body"});
    const menu = el("details", {class: "ls-popover ls-rowmenu"}, el("summary", {"aria-label": "More actions", title: "More actions"}, "⋯"), body);
    // Tables scroll inside their own box, which would clip an absolutely placed menu.
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      const anchor = menu.getBoundingClientRect();
      body.style.position = "fixed";
      body.style.left = "auto";
      body.style.right = Math.max(8, innerWidth - anchor.right) + "px";
      const below = anchor.bottom + 6 + body.offsetHeight <= innerHeight;
      body.style.top = (below ? anchor.bottom + 6 : Math.max(8, anchor.top - 6 - body.offsetHeight)) + "px";
    });
    let danger = false;
    for (const item of items) {
      if (item == null) { danger = true; body.append(el("hr")); continue; }
      const cls = "ls-menuitem" + (danger ? " is-danger" : "");
      const node = item.href ? el("a", {class: cls, href: item.href}, item.label)
        : el("button", {type: "button", class: cls, onclick: () => { menu.open = false; item.action(); }}, item.label);
      body.append(node);
    }
    return menu;
  }
  async function assetsPage(root, signal) {
    const params = new URLSearchParams(location.search);
    const runId = params.get("run") || "";
    const incomingScope = new Map(["technology", "category", "organization_id", "country", "continent", "since", "until"]
      .filter(key => params.get(key)).map(key => [key, params.get(key)]));
    const scopeStatus = el("div", {"aria-live": "polite"});
    const scopeLabels = {technology:"Technology", category:"Category", organization_id:"Organization", country:"Country", continent:"Continent", since:"Saved after", until:"Saved before"};
    const sorting = window.LeakScopeTables?.fromParams(params, ["endpoint","technology","tags","observations","last_seen"], "last_seen") || {key:"last_seen",direction:"desc"};
    let loadVersion = 0;
    const runContext = el("section", { "data-run-results": "", "aria-live": "polite" });
    const catalog = await request("/catalog");
    if (params.get("endpoint")) {
      await endpointDetail(root, params.get("endpoint"), catalog);
      return;
    }
    const q = input("q", params.get("q") || "", {
      placeholder: "Address, title or technology",
    });
    const type = select(
      "type",
      [
        ["", "All technologies"],
        ...(catalog.results || []).map((item) => [item.key, item.label]),
      ],
      params.get("type") || "",
    );
    const tag = input("tag", params.get("tag") || "", {
      placeholder: "Filter by tag",
    });
    const provider = select(
      "provider",
      [
        ["", "All sources"],
        ["netlas", "Netlas"],
        ["shodan", "Shodan"],
        ["zoomeye", "ZoomEye"],
        ["certstream", "Certstream"],
        ["direct", "Direct captures"],
      ],
      params.get("provider") || "",
    );
    const selected = new Map();
    const body = el("div");
    const output = el("div");
    const actions = actionsControl(catalog.tools, "actions", [], []);
    const ask = btn("Ask", () =>
      window.LeakScopeIntelligence?.openAsk(
        [...selected.keys()].map((id) => ({ type: "endpoint", id })),
      ),
    );
    const similar = btn("Find similar", () =>
      window.LeakScopeIntelligence?.openSimilar(
        [...selected.keys()].map((id) => ({ type: "endpoint", id })),
      ),
    );
    const fetch = btn("Fetch selected actions", () =>
      action(fetch, output, async () => {
        showRuns(
          output,
          await request("/fetch", {
            ids: [...selected.keys()],
            actions: selectedActions(actions),
          }),
        );
      }),
    );
    function update() {
      bar.setSelected(selected.size);
      ask.disabled = similar.disabled = !selected.size || selected.size > 10;
      const profiles = [...selected.values()].map((item) =>
        profileFor(item, catalog),
      );
      const allowed = profiles.length
        ? profiles[0].allowed.filter((key) =>
            profiles.every((profile) => profile.allowed.includes(key)),
          )
        : [];
      const defaults = profiles.length
        ? profiles[0].defaults.filter((key) =>
            profiles.every((profile) => profile.defaults.includes(key)),
          )
        : [];
      const controls = actionsControl(
        catalog.tools,
        "actions",
        allowed,
        defaults,
        false,
        profiles.length > 0 && profiles.every(profile => profile.nativeVnc),
      );
      actions.replaceChildren(...controls.children);
      fetch.disabled = !selected.size || !selectedActions(actions).length;
    }
    actions.addEventListener("change", () => {
      fetch.disabled = !selected.size || !selectedActions(actions).length;
    });
    let page = Math.max(1, Number(params.get("page") || 1));
    async function load() {
      const version = ++loadVersion;
      selected.clear();
      update();
      bar.pager.querySelectorAll("button").forEach(control => { control.disabled = true; });
      body.replaceChildren(note("Loading observations…"));
      try {
        const query = new URLSearchParams({
          page,
          sort: sorting.key,
          direction: sorting.direction,
          q: q.value,
          type: type.value,
          tag: tag.value,
          provider: provider.value,
        });
        if (runId) query.set("run", runId);
        for (const [key, value] of incomingScope) query.set(key, value);
        const result = await request("/endpoints?" + query, undefined, signal);
        if (signal?.aborted || version !== loadVersion) return;
        { const all=new URLSearchParams(query);all.delete("page");all.delete("page_size"); body.dataset.exportUrl = "/export/table/observations/?" + all; }
        if (page > Number(result.pages || 1)) { page = Number(result.pages || 1); return load(); }
        history.replaceState(null, "", "?" + query);
        scopeStatus.replaceChildren(...(result.scope?.scope_truncated ? [note("This incoming scope uses a bounded saved-evidence sample; additional matches may exist.")] : []));
        const run = result.run;
        if (run) {
          runContext.replaceChildren(...[el("h2", {}, "Results from this search"),
            el("p", { class: "small" }, (run.provider || "Provider") + " · " + (run.state || "unknown")
              + (typeof run.stored === "number" ? " · " + run.stored + " results saved" : "")),
            run.query ? el("pre", { class: "code-box" }, text(run.query)) : null,
            run.error ? (typeof run.error === "object" ? dataView(run.error, "Run error") : note(text(run.error), true)) : null,
            run.partial ? note("Partial results: some collection did not complete.") : null,
            el("div", { class: "row-actions" }, link("Run details", "/runs/?id=" + encodeURIComponent(run.id)),
              btn("Refresh results", load), link("All observations", "/assets/"))].filter((child) => child != null));
        }

        const insights = window.LeakScopeViz?.endpointInsights({
          id: "assets-insights", rows: result.results, total: result.count,
          page: result.page || page, catalog, open: visualOpen(),
          onType: (key) => {
            if (!key) {
              const unclassified = (result.results || []).filter(item => !item.service_type);
              insights.querySelector(".ls-viz-readout").replaceChildren(
                el("strong", {}, "Unclassified · " + unclassified.length + " endpoints on this page"),
                el("div", {class:"ls-viz-endpoint-list"}, unclassified.map(item =>
                  link(item.title || item.address || "Saved endpoint", "/assets/?endpoint=" + encodeURIComponent(item.id), "btn btn-small"))),
              );
              return;
            }
            if (![...type.options].some(option => option.value === key))
              type.append(el("option", {value:key}, key));
            type.value = key; page = 1; load();
          },
        });

        insightsHost.replaceChildren(...(insights ? [insights] : []));
        bar.refreshChips();
        bar.setCount(number(result.count || 0) + " matching endpoint" + (Number(result.count) === 1 ? "" : "s"));
        bar.pager.replaceChildren();
        if (!(result.results || []).length) {
          body.replaceChildren(
            note(
              run?.state === "succeeded" && run.total === 0 && !run.partial
                ? "This search returned no matches. Its exact query is shown above. No new search has been run."
                : run && ["queued", "running"].includes(run.state)
                  ? "This search has not completed. Refresh results to check for saved observations."
                  : run
                    ? "No saved observations match this run and the current filters. Check the run details for collection errors or limits."
                    : "No observations match. Run a lookup or enable a scoped certificate watch to collect evidence.",
            ),
          );
          return;
        }
        const rows = result.results.map((item) => {
          const box = el("input", {
            type: "checkbox",
            "aria-label": "Select " + text(item.address),
            onchange: (event) => {
              if (event.target.checked && selected.size >= 10) {
                event.target.checked = false;
                output.replaceChildren(
                  note(
                    "Select at most 10 endpoints for one fetch batch.",
                    true,
                  ),
                );
                return;
              }
              if (event.target.checked) selected.set(item.id, item);
              else selected.delete(item.id);
              update();
            },
          });
          return el(
            "tr",
            {},
            el("td", {}, box),
            el(
              "td",
              {},
              link(
                item.title || item.address,
                "/assets/?endpoint=" + encodeURIComponent(item.id),
                "cell-title",
              ),
              el(
                "span",
                { class: "cell-sub mono" },
                item.address + ":" + item.port,
              ),
            ),
            el("td", {}, item.service_type || "unclassified"),
            el("td", {}, (item.tags || []).join(", ") || "—"),
            el("td", {}, number(item.observation_count), typeof item.collection_count === "number" && item.collection_count > 0 ? el("span", {class:"cell-sub", title:"Times a collection saved a result for this endpoint"}, "captured " + item.collection_count + "×") : null),
            el("td", { class: "small muted", "data-sort-value": item.last_seen || "" }, date(item.last_seen)),
            el("td", {}, el("div", {class:"ls-rowactions"},
              window.LeakScopeInspector ? btn("Inspect",()=>window.LeakScopeInspector.open({type:"endpoint",id:item.id}), "btn btn-small") : null,
              rowMenu([
                {label: "Add to organization", action: () => window.LeakScopeAssets?.addToOrganization?.({type:"endpoint",id:item.id})},
                {label: "Create discovery preset", action: () => window.LeakScopePresets?.openCreate?.([{type:"endpoint",id:item.id}])},
                {label: "Open full detail →", href: "/assets/?endpoint=" + encodeURIComponent(item.id)},
              ]))),
          );
        });
        const previous = btn(
          "← Previous",
          () => {
            page--;
            load();
          },
          "btn btn-small",
        );
        const next = btn(
          "Next →",
          () => {
            page++;
            load();
          },
          "btn btn-small",
        );
        previous.disabled = page <= 1;
        next.disabled = page >= Number(result.pages || 1);
        const grid = table(
            [
              "Select",
              "Endpoint",
              "Technology",
              "Tags",
              "Observations",
              "Last seen",
              "Actions",
            ],
            rows,
            {columns:[null,{key:"endpoint"},{key:"technology"},{key:"tags",description:"First alphabetical tag; untagged endpoints last"},{key:"observations",type:"number"},{key:"last_seen",type:"date"},null],
              state:sorting,onSort:()=>{page=1;load();},scope:"Sort all matching saved endpoints"},
          );
        // The table is this page's main content, so it is never folded into a results disclosure.
        const gridTable = grid.matches("table") ? grid : grid.querySelector("table");
        gridTable.dataset.collapsible = "false";
        // Fits the page at every width: cells wrap, and on narrow screens each row stacks with its column names.
        gridTable.classList.add("ls-fit");
        const names = ["Select", "Endpoint", "Technology", "Tags", "Observations", "Last seen", "Actions"];
        for (const row of gridTable.tBodies[0].rows) [...row.cells].forEach((cell, index) => { cell.dataset.label = names[index]; });
        body.replaceChildren(grid);
        bar.pager.replaceChildren(el("span", {}, "Page " + page + " of " + (result.pages || 1)), previous, next);
      } catch (error) {
        if (signal?.aborted || version !== loadVersion) return;
        bar.setCount("");
        bar.pager.replaceChildren();
        body.replaceChildren(note(error.message, true));
      }
    }
    const fetchMenu = el("details", {class: "ls-popover ls-fetchmenu"}, el("summary", {}, "Fetch evidence"),
      el("div", {class: "ls-popover-body"}, actions,
        el("p", {class: "small muted"}, "Up to 10 endpoints per batch. Unavailable actions are disabled for this selection."), fetch));
    const insightsHost = el("div");
    const views = window.LeakScopeWorkspace?.savedViews ? window.LeakScopeWorkspace.savedViews({page: "assets", root, variant: "menu"}) : null;
    const bar = window.LeakScopeListBar.create({
      page: "assets", search: q,
      quick: [{label: "Technology", control: type}, {label: "Source", control: provider}],
      more: [{label: "Tag", control: tag}],
      extraChips: [...incomingScope].map(([key, value]) => ({text: "Scope · " + scopeLabels[key] + ": " + value, remove: () => incomingScope.delete(key)})),
      onChange: () => { page = 1; load(); },
      views, insights: insightsHost,
      about: el("p", {class: "small muted"}, "Saved provider and live-fetch evidence grouped by endpoint, with preserved source history. Sorting and counts cover every matching saved endpoint; charts describe the page shown. Last-seen times are when evidence was collected, not live availability checks."),
      selection: {limit: 10, actions: [fetchMenu, similar, ask]},
    });
    bar.element.addEventListener("ls:clear-selection", () => {
      selected.clear();
      body.querySelectorAll("tbody input[type=checkbox]").forEach(box => { box.checked = false; });
      update();
    });
    // Enter in a filter still applies it; there is no Apply button because changes apply as they are made.
    const form = el("form", {class: "ls-listform"}, bar.element);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      page = 1;
      load();
    });
    update();
    root.replaceChildren(
      heading(
        "Observations",
        "Provider and live-fetch evidence grouped by endpoint, with preserved source history.",
      ),
      runContext,
      form,
      scopeStatus,
      output,
      body,
      bar.footer,
    );
    await load();
  }
  function openEnrichment(endpoint, catalog) {
    if (!catalog.providers?.netlas?.configured) {
      window.LeakScopeIntelligence?.openSettings((settings) => {
        catalog.providers = {
          ...catalog.providers,
          netlas: { configured: !!settings.netlas?.configured },
        };
      });
      return;
    }
    const controller = new AbortController();
    const dialog = el("dialog", {
      class: "run-dialog ops-enrichment-dialog",
      "aria-labelledby": "ops-enrichment-title",
    });
    const output = el("div", { "aria-live": "polite" });
    const choices = el("div", { class: "ops-actions" });
    const estimate = el("p", { class: "small muted" });
    const demo = check("Demo · minimal API use", "demo", false);
    const demoBox = demo.querySelector("input");
    const ipv6 = endpoint.address.includes(":");
    const ip = ipv6 || /^(?:\d+\.){3}\d+$/.test(endpoint.address);
    const run = btn(
      "Run enrichment",
      () =>
        action(run, output, async () => {
          const collections = [
            ...choices.querySelectorAll("input:checked"),
          ].map((box) => box.value);
          if (!collections.length)
            throw new Error("Select at least one collection.");
          const response = await request(
            "/endpoints/" + encodeURIComponent(endpoint.id) + "/enrich",
            { collections, ...(demoBox.checked ? {demo:true} : {}) },
            controller.signal,
          );
          if (controller.signal.aborted) return;
          showRuns(output, response);
          const progress = el("div");
          output.append(progress);
          if (response.run && window.LeakScopeWorkbench?.watchRun) {
            window.LeakScopeWorkbench.watchRun(
              response.run,
              progress,
              () => {
                if (!controller.signal.aborted)
                  output.append(
                    link(
                      "Refresh observation →",
                      "/assets/?endpoint=" + encodeURIComponent(endpoint.id),
                      "btn btn-small",
                    ),
                  );
              },
              { signal: controller.signal },
            );
          }
        }),
      "btn btn-primary",
    );
    for (const [value, label, supported] of [
      ["host", "Host summary", !ipv6],
      ["dns", "DNS records", true],
      ["certificates", "Certificates by exact hostname", !ip],
    ]) {
      const choice = check(
        label + (supported ? "" : " · requires a hostname"),
        "enrichment_collection",
        supported,
        !supported,
      );
      choice.querySelector("input").value = value;
      choices.append(choice);
    }
    function update() {
      const count = choices.querySelectorAll("input:checked").length;
      estimate.textContent = demoBox.checked
        ? "Demo makes one real Netlas request for the first selected collection and saves at most one result. Other selected collections are reported as omitted. Minimum provider billing may still apply."
        : "Up to " +
        count +
        " Netlas requests. DNS and certificates return at most 20 records each. Provider credits may be consumed; no automatic retries are made.";
      run.disabled = !count;
    }
    choices.addEventListener("change", update);
    demoBox.addEventListener("change", update);
    update();
    const close = () => {
      controller.abort();
      if (typeof dialog.close === "function") dialog.close();
      dialog.remove();
    };
    const closeButton = btn("×", close, "icon-button");
    closeButton.setAttribute("aria-label", "Close enrichment");
    dialog.append(
      el(
        "div",
        { class: "dialog-heading" },
        el("h2", { id: "ops-enrichment-title" }, "Enrich with Netlas"),
        closeButton,
      ),
      el("p", { class: "mono" }, endpoint.address),
      note(
        "Choose the provider collections to attach as timestamped observation evidence.",
      ),
      choices,
      demo,
      estimate,
      run,
      output,
    );
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("close", () => {
      controller.abort();
      dialog.remove();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    window.addEventListener("pagehide", close, {
      once: true,
      signal: controller.signal,
    });
    document.body.append(dialog);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }
  function screenshotQuality(metadata = {}) {
    const quality = metadata.capture_quality || {};
    const blocked = Number(metadata.blocked_external_requests) > 0 ? Number(metadata.blocked_external_requests) : Array.isArray(metadata.blocked_requests) ? metadata.blocked_requests.length : 0;
    const state = ["challenge", "http_error", "restricted"].includes(quality.state) ? quality.state : blocked ? "restricted" : "unassessed";
    const status = metadata.status_code ?? metadata.status;
    const label = [401,407].includes(Number(status)) ? "Authentication required page captured" : state === "challenge" ? "Browser challenge or interstitial captured" : state === "http_error" ? "HTTP error page captured" : state === "restricted" ? (blocked ? "Partial screenshot — page resources were blocked" : "Partial screenshot — review browser diagnostics") : metadata.partial === true ? "Partial screenshot captured" : "Saved screenshot";
    const output = el("div", {class:"notice ops-capture-quality", role:"status", "data-capture-quality":state}, el("strong", {}, label));
    const details = [];
    if (/^[1-5][0-9]{2}$/.test(String(status))) details.push("HTTP " + status);
    if (metadata.backend === "playwright" || metadata.tool === "playwright") details.push("Playwright · " + (metadata.profile === "web_page" ? "Web page" : "Strict endpoint"));
    if (metadata.tls?.certificate_errors_ignored === true) details.push("TLS certificate validity unverified");
    if (typeof metadata.title === "string" && metadata.title.trim()) details.push("Page title: " + metadata.title.slice(0,500));
    if (blocked) details.push(blocked + " external page requests blocked by capture policy");
    if (details.length) output.append(el("p", {class:"small"}, details.join(" · ")));
    if (metadata.render && typeof metadata.render === "object") {
      const render = metadata.render, rendering = [];
      if (["loading","interactive","complete","unknown"].includes(render.ready_state)) rendering.push("Document: " + render.ready_state);
      if (["loaded","loading","unavailable"].includes(render.fonts_status)) rendering.push("Fonts: " + render.fonts_status);
      if (Number.isInteger(render.incomplete_images) && render.incomplete_images > 0) rendering.push(render.incomplete_images + " incomplete image(s)");
      if (typeof render.webgl === "boolean") rendering.push("WebGL: " + (render.webgl ? "available" : "unavailable"));
      if (rendering.length) output.append(el("p", {class:"small"}, rendering.join(" · ")));
    }
    const reasons = Array.isArray(quality.reasons) ? [...new Set(quality.reasons.filter(item => typeof item === "string" && item.trim()).map(item => item.slice(0,500)))].slice(0,5) : [];
    if (reasons.length) output.append(el("p", {class:"small"}, reasons.join(" · ")));
    output.append(el("p", {class:"small"}, "Visual completeness unverified. Review the saved image and capture evidence."));
    return output;
  }
  async function endpointDetail(root, id, catalog) {
    const data = await request("/endpoints/" + encodeURIComponent(id));
    const endpoint = data.endpoint;
    const output = el("div");
    const profile = profileFor(endpoint, catalog);
    const actions = actionsControl(
      catalog.tools,
      "actions",
      profile.allowed,
      profile.defaults,
      true,
      profile.nativeVnc,
    );
    const wireTransport = endpoint.transport === "udp" ? "udp" : "tcp";
    const wirePayload = el("textarea", {
      name: wireTransport + "_payload",
      rows: "4",
      maxlength: "12288",
      placeholder:
        wireTransport === "tcp"
          ? "Leave empty to receive a banner without sending application data"
          : "Enter a single request payload",
    });
    const wireEncoding = select(
      wireTransport + "_encoding",
      [
        ["hex", "Hex bytes"],
        ["text", "UTF-8 text"],
      ],
      "hex",
    );
    const wireTimeout = input(wireTransport + "_timeout", "3", {
      type: "number",
      min: "1",
      max: "10",
      step: "1",
    });
    const wireTLS = check("Use TLS", "tcp_tls", endpoint.scheme === "https");
    const wireEditor = el(
      "div",
      { class: "ops-wire-editor" },
      el("h3", {}, wireTransport.toUpperCase() + " request / response"),
      el(
        "p",
        { class: "small muted" },
        "One bounded request and response. Payload: up to 4 KiB; response: up to 64 KiB; timeout: 1–10 seconds. Manual payloads apply only to this fetch.",
      ),
      field("Request payload", wirePayload),
      el(
        "div",
        { class: "field-grid" },
        field("Payload encoding", wireEncoding),
        field("Timeout (seconds)", wireTimeout),
      ),
      wireTransport === "tcp" ? wireTLS : null,
    );
    function manualWireOptions() {
      if (!selectedActions(actions).includes(wireTransport)) return {};
      const timeout = Number(wireTimeout.value);
      if (!Number.isInteger(timeout) || timeout < 1 || timeout > 10)
        throw new Error("Timeout must be a whole number from 1 to 10 seconds.");
      const payload = wirePayload.value;
      let bytes;
      if (wireEncoding.value === "hex") {
        const hex = payload.replace(/\s/g, "");
        if (!/^(?:[0-9a-f]{2})*$/i.test(hex))
          throw new Error("Hex payload must contain complete byte pairs.");
        bytes = hex.length / 2;
      } else bytes = new Blob([payload]).size;
      if (bytes > 4096) throw new Error("Request payload exceeds 4 KiB.");
      if (wireTransport === "udp" && !bytes)
        throw new Error("UDP capture requires a nonempty request payload.");
      const options = {
        payload,
        encoding: wireEncoding.value,
        timeout_seconds: timeout,
      };
      if (wireTransport === "tcp")
        options.tls = wireTLS.querySelector("input").checked;
      return { options: { [wireTransport]: options } };
    }
    const screenshotBackend = select("screenshot_backend", [["playwright", "Playwright"], ["gowitness", "gowitness fallback"]], catalog.tools?.playwright === false ? "gowitness" : "playwright");
    for (const option of screenshotBackend.options) option.disabled = catalog.tools?.[option.value] === false;
    const publicName = endpoint.address && !endpoint.address.includes(":") && !/^(?:\d+\.){3}\d+$/.test(endpoint.address) && !/(?:^|\.)(?:localhost|local|internal|lan)$/i.test(endpoint.address);
    const screenshotProfile = select("screenshot_profile", [["strict", "Strict endpoint"], ["web_page", "Web page"]], publicName ? "web_page" : "strict");
    const screenshotWait = input("screenshot_wait", "2", {type:"number",min:"0",max:"10",step:"1"});
    const screenshotTimeout = input("screenshot_timeout", "35", {type:"number",min:"10",max:"60",step:"1"});
    const screenshotFullPage = check("Capture full page (bounded height)", "screenshot_full_page");
    const browserOutput = el("div", {"aria-live":"polite"});
    function screenshotOptions() {
      if (screenshotBackend.value === "gowitness") return {backend:"gowitness",profile:"strict"};
      const wait = Number(screenshotWait.value), timeout = Number(screenshotTimeout.value);
      if (!Number.isInteger(wait) || wait < 0 || wait > 10) throw new Error("Browser wait must be a whole number from 0 to 10 seconds.");
      if (!Number.isInteger(timeout) || timeout < 10 || timeout > 60) throw new Error("Browser timeout must be a whole number from 10 to 60 seconds.");
      return {backend:"playwright",profile:screenshotProfile.value,wait_seconds:wait,timeout_seconds:timeout,full_page:screenshotFullPage.querySelector("input").checked};
    }
    const manualBrowser = btn("Open verification browser", () => action(manualBrowser, browserOutput, async () => {
      if (!window.LeakScopeBrowser?.open) throw new Error("The verification browser UI is unavailable. Refresh this page.");
      const options = screenshotOptions();
      await window.LeakScopeBrowser.open(endpoint, {...options,backend:"playwright"});
    }));
    const browserEditor = profile.nativeVnc ? null : el("div", {class:"ops-browser-editor"},
      el("h3", {}, "Browser screenshot"),
      el("p", {class:"small muted"}, "Strict loads only this endpoint. Web page also allows public web dependencies and same-site canonical redirects. Rendering is bounded; challenges and incomplete pages remain visible in diagnostics."),
      el("div", {class:"field-grid"}, field("Capture backend", screenshotBackend), field("Resource profile", screenshotProfile), field("Settle wait (seconds)", screenshotWait), field("Timeout (seconds)", screenshotTimeout)),
      screenshotFullPage,
      el("p", {class:"small muted"}, "Playwright waits for the document, fonts and a short settle interval. The verification browser lets you interact with this target in an ephemeral session, then save its actual screenshot."),
      manualBrowser,browserOutput);
    function updateBackend() {
      const fallback = screenshotBackend.value === "gowitness";
      if (fallback) screenshotProfile.value = "strict";
      for (const control of [screenshotProfile,screenshotWait,screenshotTimeout,screenshotFullPage.querySelector("input")]) control.disabled = fallback;
      manualBrowser.disabled = fallback || catalog.tools?.playwright === false;
    }
    screenshotBackend.addEventListener("change", updateBackend);updateBackend();
    const fetch = btn(
      "Fetch selected actions",
      () =>
        action(fetch, output, async () => {
          showRuns(
            output,
            await request("/endpoints/" + encodeURIComponent(id) + "/fetch", {
              actions: selectedActions(actions),
              options: {...(manualWireOptions().options || {}), ...(!profile.nativeVnc && selectedActions(actions).includes("screenshot") ? {screenshot:screenshotOptions()} : {})},
            }),
          );
        }),
      "btn btn-primary",
    );
    function updateActions() {
      fetch.disabled = !selectedActions(actions).length;
      wireEditor.hidden = !selectedActions(actions).includes(wireTransport);
      if (browserEditor) browserEditor.hidden = !selectedActions(actions).includes("screenshot");
    }
    actions.addEventListener("change", updateActions);
    updateActions();
    const refs = [{ type: "endpoint", id }];
    const buttons = el(
      "div",
      { class: "row-actions" },
      fetch,
      window.LeakScopeInspector ? btn("Inspect saved evidence",()=>window.LeakScopeInspector.open({type:"endpoint",id})) : null,
      window.LeakScopeInspector ? btn("Compare saved history",()=>window.LeakScopeInspector.open({type:"endpoint",id},{tab:"History"})) : null,
      btn("Find similar", () =>
        window.LeakScopeIntelligence?.openSimilar(refs),
      ),
      btn("Create discovery preset", () => window.LeakScopePresets?.openCreate?.(refs)),
      btn("Ask", () => window.LeakScopeIntelligence?.openAsk(refs)),
      btn("Enrich with Netlas", () => openEnrichment(endpoint, catalog)),
      btn("Names, DNS & netblock", () =>
        window.LeakScopeAssets?.openTools?.({ type: "endpoint", id }),
      ),
      btn("Add to organization", () => window.LeakScopeAssets?.addToOrganization?.({type:"endpoint",id})),
      btn("Related services / TCP inventory",()=>window.LeakScopeICS?.openRelated?.({type:"endpoint",id})),
      ...(['modbus','bacnet'].includes(endpoint.service_type) ? [btn("Read-only device inspection",()=>window.LeakScopeICS?.openInspection?.({type:"endpoint",id}))] : []),
    );
    const history = (data.observations || []).map((row) =>
      el(
        "tr",
        {},
        el("td", {}, el("strong",{},row.provider || "unknown"),row.provenance?.label ? el("span",{class:"cell-sub provenance-badge"},row.provenance.label) : null),
        el("td", {"data-sort-value":row.observed_at || ""}, date(row.observed_at)),
        el("td", {"data-sort-value":row.first_collected_at || ""}, date(row.first_collected_at)),
        el("td", {"data-sort-value":row.last_collected_at || ""}, date(row.last_collected_at)),
        el("td", {"data-sort-value":row.collection_count ?? ""}, typeof row.collection_count === "number" ? row.collection_count + "×" : "—"),
        el(
          "td",
          {},
          el(
            "details",
            {},
            el("summary", {}, "Observation data"),
            dataView(row.data, "Provider observation"),
          ),
        ),
      ),
    );
    const artifacts = el("div", { class: "ops-artifacts" });
    for (const item of data.artifacts || []) {
      const url = safeURL(item.url, true);
      const body = el(
        "div",
        {},
        el(
          "p",
          { class: "small muted" },
          text(item.tool) +
            " · " +
            number(item.size) +
            " bytes · " +
            date(item.created_at),
        ),
      );
      if (item.kind === "screenshot") body.append(screenshotQuality(item.metadata || {}));
      // Say which kind of image this is before showing it.
      const badge = window.LeakScopeEvidence?.classBadge(item.capture_class, item.capture_label);
      if (badge) body.prepend(el("p", { class: "ev-headline" }, badge, el("span", { class: "ev-headline-note" }, item.capture_note || "")));
      const render = item.kind === "evidence-render" ? item.metadata?.render : null;
      if (render) body.append(note([render.headline, render.state_label, render.operation].filter(Boolean).join(" · ")));
      const summary = item.metadata?.wire_summary;
      if (summary)
        body.append(
          el(
            "p",
            { class: "ops-wire-summary small mono" },
            "Sent " +
              number(summary.bytes_sent) +
              " bytes · Received " +
              number(summary.bytes_received) +
              " bytes" +
              (summary.transport
                ? " · " + text(summary.transport).toUpperCase()
                : "") +
              (summary.tls ? " · TLS" : ""),
          ),
        );
      if (url)
        body.append(
          el(
            "a",
            {
              href: url,
              target: "_blank",
              rel: "noopener noreferrer",
              class: "btn btn-small",
            },
            "Open artifact ↗",
          ),
        );
      else body.append(note("Artifact link is unavailable."));
      if (
        url &&
        (item.kind === "screenshot" || item.kind === "evidence-render") &&
        item.mime?.startsWith("image/") &&
        new URL(url).origin === location.origin
      )
        body.append(
          el("img", {
            src: url,
            alt: render ? render.description || "Rendered protocol evidence for " + endpoint.address + "; not a screenshot" : "Captured screenshot for " + endpoint.address,
            loading: "lazy",
          }),
        );
      if (
        url &&
        ["wire", "wire-transcript", "http-trace"].includes(item.kind) &&
        new URL(url).origin === location.origin
      ) {
        const transcript = el("div", {
          class: "ops-transcript",
          hidden: true,
          tabindex: "0",
        });
        const inspect = btn(
          "Inspect transcript",
          () =>
            action(inspect, transcript, async () => {
              transcript.hidden = false;
              transcript.textContent = "Loading transcript…";
              const response = await window.fetch(url, {
                credentials: "same-origin",
              });
              if (!response.ok) throw new Error("Unable to load transcript.");
              const limit = 262144;
              if (Number(response.headers?.get("content-length")) > limit)
                throw new Error(
                  "Transcript exceeds the 256 KiB inline limit. Open the artifact to inspect it.",
                );
              let value;
              if (response.body?.getReader) {
                const reader = response.body.getReader(),
                  chunks = [];
                let length = 0;
                try {
                  while (true) {
                    const { done, value: chunk } = await reader.read();
                    if (done) break;
                    length += chunk.byteLength;
                    if (length > limit) {
                      await reader.cancel();
                      throw new Error(
                        "Transcript exceeds the 256 KiB inline limit. Open the artifact to inspect it.",
                      );
                    }
                    chunks.push(chunk);
                  }
                } finally {
                  reader.releaseLock();
                }
                const bytes = new Uint8Array(length);
                let offset = 0;
                for (const chunk of chunks) {
                  bytes.set(chunk, offset);
                  offset += chunk.byteLength;
                }
                value = new TextDecoder().decode(bytes);
              } else {
                value = await response.text();
                if (new Blob([value]).size > limit)
                  throw new Error(
                    "Transcript exceeds the 256 KiB inline limit. Open the artifact to inspect it.",
                  );
              }
              try {
                const parsed = JSON.parse(value);
                if (parsed && typeof parsed === "object") {
                  transcript.replaceChildren(dataView(value, "Request / response transcript"));
                } else transcript.replaceChildren(el("pre", {class:"code-box"}, value));
              } catch (_) {
                transcript.replaceChildren(el("pre", {class:"code-box"}, value));
              }
            }),
          "btn btn-small",
        );
        body.append(inspect, transcript);
      }
      if (item.metadata)
        body.append(
          el(
            "details",
            {},
            el("summary", {}, "Capture metadata"),
            dataView(item.metadata, "Capture metadata"),
          ),
        );
      artifacts.append(
        panel(
          {
            "wire-transcript": "Request / response transcript",
            wire: "Request / response transcript",
            "wire-request": "Request bytes",
            "wire-response": "Response bytes",
            "http-trace": "HTTP request / response trace",
            screenshot: item.capture_label || "Screenshot",
            "evidence-render": "Rendered protocol evidence",
          }[item.kind] ||
            item.kind ||
            "Artifact",
          body,
        ),
      );
    }
    const captures = (data.artifacts || [])
      .filter((item) => {
        const url = safeURL(item.url, true);
        return (
          item.kind === "screenshot" &&
          item.mime === "image/png" &&
          url &&
          new URL(url).origin === location.origin &&
          Number.isFinite(Date.parse(item.created_at))
        );
      })
      .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
      .slice(-2);
    const comparison =
      captures.length === 2
        ? panel(
            "Compare recent screenshots",
            el(
              "div",
              { class: "ops-screenshot-comparison" },
              ...captures.map((item, index) =>
                el(
                  "figure",
                  {},
                  el(
                    "figcaption",
                    { class: "small muted" },
                    (index ? "Latest" : "Previous") +
                      " · " +
                      date(item.created_at),
                  ),
                  el(
                    "a",
                    {
                      href: safeURL(item.url, true),
                      target: "_blank",
                      rel: "noopener noreferrer",
                    },
                    el("img", {
                      src: safeURL(item.url, true),
                      alt:
                        (index ? "Latest" : "Previous") +
                        " captured screenshot for " +
                        endpoint.address,
                      loading: "lazy",
                    }),
                  ),
                ),
              ),
            ),
          )
        : null;
    root.replaceChildren(
      ...[
        heading(
          endpoint.title || endpoint.address,
          endpoint.address +
            ":" +
            endpoint.port +
            " · " +
            text(endpoint.transport) +
            " · " +
            text(endpoint.service_type),
        ),
        link("← Observations", "/assets/", "btn"),
        endpoint.resource_path
          ? el(
              "p",
              { class: "small mono" },
              "Resource path: " + endpoint.resource_path,
            )
          : null,
        panel(
          "Evidence actions",
          el(
            "div",
            {},
            endpoint.native_identity?.available && endpoint.native_identity.operation
              ? note("Protocol identification: " + endpoint.native_identity.operation + (endpoint.native_identity.limitation ? ". " + endpoint.native_identity.limitation : ""))
              : endpoint.native_identity?.limitation ? note(endpoint.native_identity.limitation) : null,
            actions,
            wireEditor,
            browserEditor,
            buttons,
            output,
            el(
              "p",
              { class: "small muted" },
              "Fetches create timestamped evidence. Protocol identity is available only for the adapters listed in the catalog.",
            ),
          ),
        ),
        window.LeakScopeEvidence && data.attribution ? panel("Attribution", window.LeakScopeEvidence.ladder(data.attribution)) : null,
        window.LeakScopeEvidence && data.capture_status ? panel("Capture status", window.LeakScopeEvidence.capturePanel(data.capture_status)) : null,
        panel(
          "Result tags",
          tagsEditor("endpoint", id, endpoint.tags || [], undefined, endpoint),
        ),
        panel(
          "Observation history",
          history.length
            ? table(
                [
                  "Source",
                  "Source observed",
                  "First collected",
                  "Last collected",
                  "Times captured",
                  "Evidence",
                ],
                history,
                {scope:"Up to 100 loaded observation records"},
              )
            : note("No observation history is available."),
        ),
        panel(
          "Artifacts",
          artifacts.children.length
            ? artifacts
            : note("No capture artifacts yet."),
        ),
        comparison,
      ].filter(Boolean),
    );
  }
  async function monitoringPage(root, signal) {
    const catalog = await request("/catalog", undefined, signal);
    const stream = await request("/stream", undefined, signal);
    const matchSorting = {key:null,direction:"asc"};
    const health = el("div", { class: "ops-stream-health" });
    // Passive instrument: it redraws from the readings this page already polls for.
    const feed = window.LeakScopeScope?.stream ? window.LeakScopeScope.stream() : null;
    feed?.classList.add("scope-stream-page");
    const streamInsights = el("div", { class: "ls-viz-monitor-insights" });
    const matches = el("div", {
      class: "ops-stream-matches",
      "aria-live": "polite",
    });
    const mode = select(
      "mode",
      [
        ["builtin", "Built-in local Certstream"],
        ["external", "External Certstream URL"],
      ],
      stream.mode || "builtin",
    );
    const external = input("external_url", "", {
      placeholder: "wss://your-certstream.example/",
      maxlength: "2000",
    });
    let externalChanged = false;
    let privateExternalSaved = false;
    const externalHint = el("p", { class: "small muted" });
    function updateExternal(data) {
      privateExternalSaved = !!data.external_url_has_credentials;
      external.value = privateExternalSaved ? "" : data.external_url || "";
      external.placeholder = privateExternalSaved
        ? "Authenticated URL saved · leave unchanged to keep"
        : "wss://your-certstream.example/";
      externalHint.textContent = privateExternalSaved
        ? "An authenticated external URL is saved" +
          (data.external_url ? " for " + data.external_url : "") +
          ". Leave this field unchanged to keep its private parameters. Enter a complete replacement URL to change it."
        : "Use the exact WebSocket URL supplied by your feed.";
      externalChanged = false;
    }
    updateExternal(stream);
    external.addEventListener("input", () => {
      externalChanged = true;
    });
    const externalField = field("External WebSocket URL", external);
    externalField.append(externalHint);
    const streamEnabled = check(
      "Enable stream ingestion",
      "stream_enabled",
      stream.enabled !== false,
    );
    const streamOutput = el("div");
    const streamSubmit = el(
      "button",
      { type: "submit", class: "btn" },
      "Save stream settings",
    );
    function toggleExternal() {
      externalField.hidden = mode.value !== "external";
    }
    mode.addEventListener("change", toggleExternal);
    toggleExternal();
    const streamForm = el(
      "form",
      { class: "ops-form" },
      field("Stream source", mode),
      externalField,
      streamEnabled,
      el("div", { class: "ops-wide" }, streamSubmit),
      el("div", { class: "ops-wide" }, streamOutput),
    );
    function drawHealth(data) {
      feed?.update(data);
      health.replaceChildren(...[
        el(
          "div",
          { class: "row-actions" },
          badge(data.state || "unknown"),
          el(
            "span",
            { class: "small mono" },
            data.mode === "external"
              ? "External configured feed"
              : data.builtin_url || "Built-in local service",
          ),
        ),
        el(
          "div",
          { class: "ops-small-metrics" },
          ...[
            ["Received", data.received],
            ["Matched", data.matched],
            ["Dropped", data.dropped],
            ["Lag (seconds)", data.lag_seconds],
          ].map(([label, value]) =>
            el(
              "div",
              {},
              el("strong", {}, number(value)),
              el("span", { class: "small muted" }, label),
            ),
          ),
        ),
        el(
          "p",
          { class: "small muted" },
          "Last event: " +
            date(data.last_event_at) +
            " · Heartbeat: " +
            date(data.heartbeat_at),
        ),
        el("p", {class:"small muted"}, "Received, matched and dropped are persisted cumulative counters, not event rates. They are retained across ingestor restarts."),
        data.last_error ? note(data.last_error, true) : null,
      ].filter(child => child != null));
      if (window.LeakScopeViz && !streamInsights.contains(document.activeElement)) {
        const recent = (Array.isArray(data.recent_matches) ? data.recent_matches : []).slice(0, 20);
        const dates = new Map(); let undated = 0;
        for (const item of recent) {
          const stamp = typeof item.collected_at === "string" ? new Date(item.collected_at) : null;
          if (!stamp || !Number.isFinite(stamp.getTime())) { undated++; continue; }
          const day = stamp.toISOString().slice(0, 10);
          dates.set(day, (dates.get(day) || 0) + 1);
        }
        streamInsights.replaceChildren(window.LeakScopeViz.disclosure({
          id: "monitoring-matches", title: "Saved certificate activity", open: visualOpen(),
          summary: recent.length + " latest persisted watch-match records",
          children: window.LeakScopeViz.timeline({
            title: "Saved match updates by collection date", unit: "watch-match records",
            rows: [...dates].map(([date, count]) => ({date, count})),
            coverageLabel: "Latest " + recent.length + " persisted watch-match records, grouped by their last collected UTC date. This is a bounded sample, not feed throughput."
              + (undated ? " " + undated + " records without a valid date are omitted from the chart." : ""),
            emptyText: "No dated saved certificate matches are available yet.",
          }),
        }));
      }
      const rows = (data.recent_matches || [])
        .slice(0, 20)
        .map((item) =>
          el(
            "tr",
            {},
            el(
              "td",
              {},
              link(
                (item.names || []).join(", ") || "Certificate match",
                "/certificates/?id=" + encodeURIComponent(item.certificate_id),
                "cell-title",
              ),
            ),
            el("td", {}, item.watch_name || "Saved scope"),
            el("td", {"data-sort-value":item.collected_at || ""}, date(item.collected_at)),
          ),
        );
      matches.replaceChildren(
        rows.length
          ? table(["Matched certificate names", "Watch", "Collected"], rows, {state:matchSorting,scope:"Up to 20 latest saved matches"})
          : note(
              "No persisted certificate matches yet. Matches appear when an enabled scope rule finds an event.",
            ),
      );
    }
    drawHealth(stream);
    streamForm.addEventListener("submit", (event) => {
      event.preventDefault();
      action(streamSubmit, streamOutput, async () => {
        if (
          mode.value === "external" &&
          !(privateExternalSaved && !externalChanged)
        ) {
          let valid = false;
          try {
            const url = new URL(external.value.trim());
            valid =
              ["ws:", "wss:"].includes(url.protocol) &&
              !!url.hostname &&
              !url.username &&
              !url.password;
          } catch (_) {}
          if (!valid)
            throw new Error(
              "Enter a ws:// or wss:// URL without embedded credentials.",
            );
        }
        const payload = {
          mode: mode.value,
          enabled: streamEnabled.querySelector("input").checked,
        };
        if (mode.value === "external" && externalChanged)
          payload.external_url = external.value.trim();
        await request("/stream", payload);
        streamOutput.replaceChildren(
          note(
            "Stream settings saved. Health reflects the ingestor's actual connection state.",
          ),
        );
        const current = await request("/stream");
        drawHealth(current);
        updateExternal(current);
      });
    });
    const watches = el("div");
    const watchForm = el("form", { class: "ops-form" });
    const watchName = input("watch_name", "", {
      required: true,
      maxlength: "200",
      placeholder: "Name this scope",
    });
    const domains = el("textarea", {
      name: "domains",
      class: "input",
      rows: "3",
      maxlength: "5000",
      placeholder: "example.com\nsub.example.com",
    });
    const keywords = input("keywords", "", {
      placeholder: "Optional comma-separated keywords",
    });
    const ports = input("ports", "443", { placeholder: "443, 8443" });
    const cooldown = input("watch_cooldown", 3600, {
      type: "number",
      min: "60",
      max: "31536000",
    });
    const maxFetches = input("max_daily_fetches", 100, {
      type: "number",
      min: "1",
      max: "100000",
    });
    const tags = input("watch_tags", "", {
      placeholder: "certificate, production",
    });
    const inheritedTags = el("div", { class: "row-actions" });
    const auto = check(
      "Enable follow-up fetching for matching hostnames",
      "watch_auto_fetch",
      false,
    );
    const enabled = check("Enable this watch", "watch_enabled", true);
    const actions = actionsControl(catalog.tools);
    const unavailableActions = new Map(
      [...actions.querySelectorAll("input")].map(box => [box, box.disabled]),
    );
    const followup = el(
      "div", { class: "ops-wide ops-form", "data-watch-followup": true },
      field("Ports to fetch", ports),
      field("Fetch cooldown in seconds", cooldown),
      field("Daily fetch limit", maxFetches),
      el("div", { class: "ops-wide" }, el("h3", {}, "Fetch profile"), actions),
    );
    function updateFollowup() {
      const enabled = auto.querySelector("input").checked;
      followup.hidden = !enabled;
      for (const control of [ports, cooldown, maxFetches]) control.disabled = !enabled;
      for (const [box, unavailable] of unavailableActions)
        box.disabled = !enabled || unavailable;
    }
    auto.querySelector("input").addEventListener("change", updateFollowup);
    updateFollowup();
    const output = el("div", { class: "ops-wide" });
    const submit = el(
      "button",
      { type: "submit", class: "btn btn-primary" },
      "Save watch",
    );
    let editingWatchId = null;
    const cancelWatchEdit = btn(
      "Cancel edit",
      () => seedWatch(null),
      "btn btn-quiet",
    );
    cancelWatchEdit.hidden = true;
    function seedWatch(item) {
      editingWatchId = item?.id || null;
      watchName.value = item?.name || "";
      domains.value = (item?.domains || []).join("\n");
      keywords.value = (item?.keywords || []).join(", ");
      ports.value = (item?.ports || [443]).join(", ");
      tags.value = (item?.manual_tags || []).join(", ");
      inheritedTags.replaceChildren(
        ...(item?.inherited_tags?.length
          ? [
              el(
                "span",
                { class: "small muted" },
                "Inherited tags · read-only",
              ),
              ...item.inherited_tags.map((tag) => badge(tag)),
            ]
          : []),
      );
      cooldown.value = item?.cooldown_seconds || 3600;
      maxFetches.value = item?.max_daily_fetches || 100;
      auto.querySelector("input").checked = item?.auto_fetch ?? false;
      enabled.querySelector("input").checked = item?.enabled ?? true;
      for (const checkbox of actions.querySelectorAll("input"))
        checkbox.checked =
          !unavailableActions.get(checkbox) &&
          (item?.actions || ["http"]).includes(checkbox.value);
      updateFollowup();
      submit.textContent = item ? "Save watch changes" : "Save watch";
      cancelWatchEdit.hidden = !item;
      output.replaceChildren();
      if (item)
        watchForm.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }
    async function loadWatches() {
      const data = await request("/watches");
      watches.replaceChildren(
        ...((data.results || []).length
          ? data.results.map((item) => {
              const message = el("div");
              const toggle = btn(
                item.enabled ? "Pause" : "Enable",
                () =>
                  action(toggle, message, async () => {
                    await request(
                      "/watches/" + encodeURIComponent(item.id) + "/toggle",
                      { enabled: !item.enabled },
                    );
                    await loadWatches();
                  }),
                "btn btn-small",
              );
              const remove = btn(
                "Delete",
                () =>
                  action(remove, message, async () => {
                    await request(
                      "/watches/" + encodeURIComponent(item.id) + "/delete",
                      {},
                    );
                    await loadWatches();
                  }),
                "btn btn-small btn-quiet",
              );
              return panel(
                item.name,
                el(
                  "div",
                  {},
                  el(
                    "div",
                    { class: "row-actions" },
                    badge(item.enabled ? "enabled" : "paused"),
                    badge(item.auto_fetch ? "automatic fetch" : "observe only"),
                  ),
                  el(
                    "p",
                    { class: "mono small" },
                    (item.domains || []).join(", ") || "No domain restriction",
                  ),
                  el(
                    "p",
                    { class: "small muted" },
                    "Keywords: " +
                      ((item.keywords || []).join(", ") || "—") +
                      (item.auto_fetch
                        ? " · Ports: " + (item.ports || []).join(", ") + " · Actions: " + (item.actions || []).join(", ")
                        : " · Certificate matches only; no follow-up fetches."),
                  ),
                  tagsEditor(
                    "watch",
                    item.id,
                    item.tags || [],
                    undefined,
                    item,
                  ),
                  el(
                    "div",
                    { class: "row-actions" },
                    btn("Edit", () => seedWatch(item), "btn btn-small"),
                    toggle,
                    remove,
                  ),
                  message,
                ),
              );
            })
          : [
              note(
                "No certificate watches yet. Add a domain or keyword scope to start matching incoming events.",
              ),
            ]),
      );
    }
    watchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      action(submit, output, async () => {
        const autoFetch = auto.querySelector("input").checked;
        const parsedPorts = autoFetch ? list(ports.value).map((value) => Number(value)) : [];
        if (
          parsedPorts.some(
            (value) => !Number.isInteger(value) || value < 1 || value > 65535,
          )
        )
          throw new Error("Ports must be integers between 1 and 65535.");
        await request(
          editingWatchId
            ? "/watches/" + encodeURIComponent(editingWatchId) + "/save"
            : "/watches",
          {
            name: watchName.value.trim(),
            domains: list(domains.value),
            keywords: list(keywords.value),
            ports: parsedPorts,
            actions: autoFetch ? selectedActions(actions) : [],
            auto_fetch: autoFetch,
            enabled: enabled.querySelector("input").checked,
            cooldown_seconds: Number(cooldown.value),
            max_daily_fetches: Number(maxFetches.value),
            tags: list(tags.value),
          },
        );
        output.replaceChildren(
          note(
            autoFetch
              ? "Watch saved. Matching hostnames can trigger the configured follow-up fetches."
              : "Watch saved. Matching certificates will be saved for this scope.",
          ),
        );
        await loadWatches();
      });
    });
    watchForm.append(
      field("Watch name", watchName),
      field("Domain scopes", domains, "ops-wide"),
      field("Keyword rules", keywords),
      field("Result tags", tags),
      inheritedTags,
      el("div", { class: "ops-wide ops-actions" }, enabled, auto),
      followup,
      el("div", { class: "ops-wide row-actions" }, submit, cancelWatchEdit),
      output,
    );
    root.replaceChildren(
      heading(
        "Real-time monitoring",
        "Save matching certificate events for your scopes. Follow-up fetching is optional.",
      ),
      ...(feed ? [feed] : []),
      panel(
        "Certstream connection",
        el("div", {}, health, streamForm),
        "The built-in instance is installed with this stack. Select an external WebSocket URL when you want a different feed.",
      ),
      streamInsights,
      panel("Create watch", watchForm),
      panel(
        "Recent matches",
        matches,
        "Latest 20 persisted matches for your scope watches. Updates every five seconds while this page is visible.",
      ),
      el(
        "div",
        { class: "ops-section-heading" },
        el("h2", {}, "Scope watches"),
        link("Manage scheduled lookups →", "/lookups/", "btn btn-small"),
      ),
      watches,
    );
    await loadWatches();
    let timer;
    let polling = false;
    function schedulePoll() {
      if (!signal.aborted && document.visibilityState === "visible")
        timer = setTimeout(poll, 5000);
    }
    async function poll() {
      if (signal.aborted || polling || document.visibilityState !== "visible")
        return;
      polling = true;
      try {
        drawHealth(await request("/stream", undefined, signal));
      } catch (error) {
        if (!signal.aborted)
          health.replaceChildren(
            note("Stream health unavailable: " + error.message, true),
          );
      } finally {
        polling = false;
        schedulePoll();
      }
    }
    function visibility() {
      clearTimeout(timer);
      if (document.visibilityState === "visible") poll();
    }
    document.addEventListener("visibilitychange", visibility, { signal });
    signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
    schedulePoll();
  }
  function bars(title, rows) {
    if (window.LeakScopeViz) {
      const unit = title === "Sources" ? "observations" : "endpoints";
      const coverageLabel = {
        Sources: "Visible saved observation records, grouped by provider.",
        Technologies: "Visible saved endpoints, grouped by recorded service identity.",
        Categories: "Visible saved endpoints, grouped by catalog category.",
        Tags: "Endpoints per tag. An endpoint may have more than one tag.",
      }[title];
      const readout = el("p", {class:"ls-viz-readout", "aria-live":"polite"}, "Select a breakdown to inspect its count.");
      const chart = window.LeakScopeViz.bars({ rows, unit, coverageLabel,
        onSelect: row => { readout.textContent = text(row.label) + ": " + number(row.count) + " " + unit + "."; } });
      return window.LeakScopeViz.disclosure({
        id: "analysis-" + title.toLowerCase(), title, open: visualOpen(),
        summary: "Saved " + unit,
        children: el("div", {}, chart, chart.querySelector(".ls-viz-bar-row") ? readout : null),
      });
    }
    const values = (rows || []).filter(
      (row) =>
        typeof row.count === "number" &&
        Number.isFinite(row.count) &&
        row.count >= 0,
    );
    if (!values.length)
      return panel(title, note("No observations in this breakdown yet."));
    const max = Math.max(1, ...values.map((row) => row.count));
    const body = el("div", { class: "ops-bars" });
    for (const row of values) {
      const fill = el("span", { class: "ops-bar-fill" });
      fill.style.width =
        Math.max(0, Math.min(100, (row.count / max) * 100)) + "%";
      body.append(
        el(
          "div",
          { class: "ops-bar-row" },
          el("span", {}, text(row.label)),
          el("div", { class: "ops-bar-track", "aria-hidden": "true" }, fill),
          el("strong", {}, number(row.count)),
        ),
      );
    }
    return panel(title, body);
  }
  function timeline(rows) {
    if (window.LeakScopeViz) return window.LeakScopeViz.disclosure({
      id: "analysis-timeline", title: "Observation timeline", open: visualOpen(),
      summary: "Recorded collection dates · last 30 days",
      children: window.LeakScopeViz.timeline({ rows, title: "Recorded collection dates",
        coverageLabel: "Saved observations first collected in the last 30 days. These are collection dates, not provider scan dates; missing dates are not assumed to be zero.",
      }),
    });
    if (!(rows || []).length)
      return panel(
        "Observation timeline",
        note("No observation timeline yet."),
      );
    const values = rows.filter(
      (row) =>
        typeof row.count === "number" &&
        Number.isFinite(row.count) &&
        row.count >= 0,
    );
    if (!values.length)
      return panel(
        "Observation timeline",
        note("No valid timeline points are available."),
      );
    const max = Math.max(1, ...values.map((row) => row.count));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 720 180");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Collected observations over time");
    const title = document.createElementNS(svg.namespaceURI, "title");
    title.textContent = "Observation counts by collection date";
    svg.append(title);
    const step = 680 / values.length;
    values.forEach((row, i) => {
      const rect = document.createElementNS(svg.namespaceURI, "rect");
      rect.setAttribute("x", String(20 + i * step));
      rect.setAttribute("y", String(155 - (row.count / max) * 130));
      rect.setAttribute("width", String(Math.max(1, step - 3)));
      rect.setAttribute("height", String((row.count / max) * 130));
      rect.setAttribute("fill", "#178363");
      const label = document.createElementNS(svg.namespaceURI, "title");
      label.textContent = text(row.date) + ": " + row.count;
      rect.append(label);
      svg.append(rect);
    });
    return panel(
      "Observation timeline",
      el(
        "figure",
        { class: "ops-timeline" },
        svg,
        el(
          "figcaption",
          { class: "small muted" },
          values[0].date + " → " + values[values.length - 1].date,
        ),
        el(
          "details",
          {},
          el("summary", {}, "Timeline data"),
          table(
            ["Collection date", "Observations"],
            values.map((row) =>
              el(
                "tr",
                {},
                el("td", {}, row.date),
                el("td", {"data-sort-value":row.count}, number(row.count)),
              ),
            ),
          ),
        ),
      ),
    );
  }
  async function analysisPage(root) {
    const data = await request("/analysis");
    const counts = el(
      "div",
      { class: "ops-metrics" },
      ...[
        "endpoints",
        "observations",
        "artifacts",
        "lookups",
        "watches",
        "changes",
      ].map((key) =>
        el(
          "div",
          { class: "panel ops-metric" },
          el("span", { class: "small muted" }, key),
          el("strong", {}, number(data.counts?.[key])),
        ),
      ),
    );
    const changes = (data.recent_changes || []).map((item) =>
      el(
        "tr",
        {},
        el(
          "td",
          {},
          link(
            item.address || item.endpoint_id,
            "/assets/?endpoint=" + encodeURIComponent(item.endpoint_id),
            "cell-title",
          ),
        ),
        el("td", {}, item.service_type),
        el("td", {}, item.provider),
        el("td", {"data-sort-value":item.collected_at || ""}, date(item.collected_at)),
      ),
    );
    const relations = (data.relations || []).map((item) =>
      el(
        "tr",
        {},
        el("td", {}, item.kind),
        el("td", { class: "mono small" }, item.value),
        el("td", {"data-sort-value":item.count}, number(item.count)),
        el(
          "td",
          {},
          (item.endpoints || []).map((endpoint) =>
            link(
              endpoint.address + ":" + endpoint.port,
              "/assets/?endpoint=" + encodeURIComponent(endpoint.id),
              "ops-endpoint-link",
            ),
          ),
        ),
      ),
    );
    root.replaceChildren(
      heading(
        "Data analysis",
        "Explore collected observations, changes and relationships with source evidence.",
      ),
      counts,
      timeline(data.timeline),
      el(
        "div",
        { class: "grid-two" },
        bars("Sources", data.by_provider),
        bars("Technologies", data.by_service),
        bars("Categories", data.by_category),
        bars("Tags", data.by_tag),
      ),
      panel(
        "Recent changes",
        changes.length
          ? table(["Endpoint", "Technology", "Source", "Collected"], changes, {scope:"Loaded recent changes"})
          : note("No observed changes yet."),
      ),
      panel(
        "Observed relationships",
        el(
          "div",
          {},
          note(
            "Shared certificates or fingerprints describe observed relationships. They do not by themselves prove common ownership or a vulnerability.",
          ),
          relations.length
            ? table(
                [
                  "Evidence kind",
                  "Shared value",
                  "Endpoints",
                  "Evidence links",
                ],
                relations,
              )
            : note("No relationship evidence is available yet."),
        ),
      ),
    );
  }
  async function render(root, page) {
    disposePage();
    const controller = new AbortController();
    disposePage = () => controller.abort();
    window.addEventListener("pagehide", disposePage, {
      once: true,
      signal: controller.signal,
    });
    root.setAttribute("aria-busy", "true");
    root.replaceChildren(note("Loading workspace…"));
    try {
      const renderer = {
        catalog: catalogPage,
        lookups: lookupsPage,
        assets: assetsPage,
        monitoring: monitoringPage,
        analysis: analysisPage,
      }[page];
      if (!renderer) throw new Error("This workspace page is unavailable.");
      await renderer(root, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted)
        root.replaceChildren(
          heading(
            "Workspace unavailable",
            "The requested data could not be loaded.",
          ),
          note(error.message, true),
          btn("Try again", () => render(root, page)),
        );
    } finally {
      if (!controller.signal.aborted) root.setAttribute("aria-busy", "false");
    }
  }
  window.LeakScopeOperations = { render, openTags, openLookup };
})();
