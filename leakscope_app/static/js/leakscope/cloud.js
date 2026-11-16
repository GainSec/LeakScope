(function () {
  "use strict";
  const API = "/api/operations/cloud";
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, typeof value === "string" ? value : JSON.stringify(value, null, 2));
  }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (["checked", "disabled", "selected"].includes(key)) node[key] = !!value;
      else node.setAttribute(key, String(value));
    }
    children.flat(Infinity).forEach(child => {
      if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }
  const btn = (label, action, attrs = {}) => el("button", {type: "button", class: "btn", onclick: action, ...attrs}, label);
  const notice = (message, error = false) => el("div", {class: "notice" + (error ? " notice-error" : ""), role: error ? "alert" : "status"}, message);
  const field = (label, control) => el("label", {class: "field"}, el("span", {class: "field-label"}, label), control);
  const check = (label, name, checked) => el("label", {class: "check-row"}, el("input", {type: "checkbox", name, checked}), label);
  const panel = (title, ...body) => el("section", {class: "panel"}, el("div", {class: "panel-body"}, el("h2", {}, title), ...body));
  function table(headers, rows, options = {}) {
    const grid = el("table", {}, el("thead", {}, el("tr", {}, headers.map(h => el("th", {scope:"col"}, h)))), el("tbody", {}, rows));
    window.LeakScopeTables?.enhance(grid, options);
    return el("div", {class:"table-scroll"}, grid);
  }
  const cell = value => el("td", {}, value || "—");
  function option(value, label) { return el("option", {value}, label); }
  function outcomeTable(rows) {
    return table(["Resource", "Provider / region", "Assessment", "Evidence"], rows.map(row => el("tr", {},
      cell(row.name), cell(row.provider + (row.region ? " / " + row.region : "")),
      cell((row.state || "").replaceAll("_", " ")),
      cell(row.reason || row.evidence?.reason || ""))));
  }
  async function render(root) {
    const controller = new AbortController();
    const params=new URLSearchParams(location.search);
    const sorting=window.LeakScopeTables?.fromParams(params,["name","provider","region","kind","state","last_checked"],"last_checked") || {key:"last_checked",direction:"desc"};
    let timer, selectedRun, page = Math.max(1,Number(params.get("page")) || 1), inventoryVersion=0;
    window.addEventListener("pagehide", () => { controller.abort(); clearTimeout(timer); }, {once: true});
    async function api(path, body) {
      const response = await fetch(API + path, {method: body === undefined ? "GET" : "POST", credentials: "same-origin",
        signal: controller.signal, headers: {Accept: "application/json", "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || ""},
        ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Cloud discovery request failed");
      return data;
    }
    root.setAttribute("aria-busy", "true");
    try {
      const catalog = await api("/catalog");
      const names = el("textarea", {name: "names", class: "input", rows: 3, maxlength: 650,
        placeholder: "acme\nacme-assets\nacme-backups", required: true});
      const providers = el("div", {class: "cloud-provider-grid"});
      const providerControls = new Map();
      for (const provider of catalog.providers) {
        const box = check(provider.label, "provider-" + provider.key, true);
        const region = el("select", {name: "region-" + provider.key, class: "select", multiple: true,
          "aria-label": provider.label + " regions", size: Math.min(4, provider.regions.length || 1)},
          provider.regions.map(value => el("option", {value, selected: true}, value)));
        const group = el("div", {class: "cloud-provider"}, box,
          provider.regions.length ? el("details", {}, el("summary", {}, "Regions · all selected by default"), region) : el("small", {class: "muted"}, "Global endpoint patterns"));
        providers.append(group);
        providerControls.set(provider.key, {box: box.querySelector("input"), region, provider});
      }
      const permutations = check("Generate related names (dev, test, staging, prod, backup, assets)", "permutations", false);
      const storage = check("Storage and databases", "storage", true);
      const applications = check("Hosted applications", "applications", false);
      const verify = check("Verify anonymous listing access (bounded listing request)", "verify", true);
      const active = check("Allow direct requests to the selected cloud endpoints", "active", false);
      const limit = el("input", {name: "max_requests", type: "number", class: "input", min: 1, max: 500, value: 100});
      const rate = el("input", {name: "rate_per_second", type: "number", class: "input", min: 0.1, max: 2, step: 0.1, value: 1});
      const preview = el("div", {"data-cloud-preview": ""});
      const progress = el("div", {"data-cloud-progress": "", "aria-live": "polite"});
      const inventory = el("div");
      const history = el("div");
      const providerFilter = el("select", {class: "select", "aria-label": "Filter cloud provider"},
        option("", "All providers"), catalog.providers.map(p => option(p.key, p.label)));
      const stateFilter = el("select", {class: "select", "aria-label": "Filter assessment"}, option("", "All assessments"),
        ["public", "access_denied", "reachable", "service_response", "not_found", "error", "unverified", "redirect", "rate_limited"].map(s => option(s, s.replaceAll("_", " "))));
      const search = el("input", {class: "input", placeholder: "Resource name or URL", "aria-label": "Search cloud resources", maxlength: 200});
      providerFilter.value=params.get("provider") || "";stateFilter.value=params.get("state") || "";search.value=params.get("q") || "";
      function spec() {
        const selected = [...providerControls].filter(([, c]) => c.box.checked);
        return {names: names.value.split(/[\s,]+/).filter(Boolean), providers: selected.map(([key]) => key),
          regions: Object.fromEntries(selected.filter(([, c]) => c.provider.regions.length).map(([key, c]) => [key, [...c.region.selectedOptions].map(o => o.value)])),
          kinds: [storage.querySelector("input").checked ? "storage" : null, applications.querySelector("input").checked ? "applications" : null].filter(Boolean),
          permutations: permutations.querySelector("input").checked, verify: verify.querySelector("input").checked,
          max_requests: Number(limit.value), rate_per_second: Number(rate.value)};
      }
      async function guarded(button, target, action) {
        button.disabled = true;
        try { await action(); } catch (error) { if (!controller.signal.aborted) target.replaceChildren(notice(error.message, true)); }
        finally { button.disabled = false; }
      }
      async function loadInventory() {
        const version=++inventoryVersion;
        const query=new URLSearchParams({provider:providerFilter.value || "",state:stateFilter.value || "",q:search.value,page,page_size:30,sort:sorting.key,direction:sorting.direction});
        const data = await api("/resources?" + query);
        if(controller.signal.aborted || version!==inventoryVersion)return;
        window.history.replaceState(null,"","?"+query);
        inventory.replaceChildren(el("p", {class: "small muted"}, data.count + " observed cloud resources"));
        { const all=new URLSearchParams(query);all.delete("page");all.delete("page_size"); inventory.dataset.exportUrl = "/export/table/cloud/?" + all; }
        if (!data.results.length) { inventory.append(notice("No resources match. Preview candidates, then start an explicit check to collect evidence.")); return; }
        inventory.append(table(["Resource", "Provider / kind", "Assessment", "Last checked", "Evidence / inspection"], data.results.map(row => {
          const details = el("details", {}, el("summary", {}, "Stored evidence"), dataView(row.evidence, "Stored evidence"));
          return el("tr", {}, cell(el("div", {}, el("strong", {}, row.name), el("div", {class: "small muted"}, row.url))),
            cell(row.provider + " / " + row.kind + (row.region ? " / " + row.region : "")),
            cell(el("div", {}, row.state.replaceAll("_", " "), el("p", {class: "small muted"}, row.evidence?.reason))),
            cell(el("time", {class: "small muted", datetime: row.last_checked}, row.last_checked ? new Date(row.last_checked).toLocaleString() : "—")),
            cell(el("div", {}, row.endpoint_id ? el("div", {class: "row-actions"},
              el("a", {class: "btn btn-small", href: "/assets/?endpoint=" + encodeURIComponent(row.endpoint_id)}, "Endpoint observations / tags"),
              ...[["Capture HTTP", "http"], ["Screenshot", "screenshot"]].map(([label, action]) => {
                const button = btn(label, () => guarded(button, progress, async () => {
                  const data = await api("/resources/" + row.id + "/fetch", {actions: [action]});
                  if (window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(data.run, progress);
                  else progress.replaceChildren(notice("Capture queued. View its progress in Runs."));
                }), {class: "btn btn-small"});
                return button;
              })) : null, window.LeakScopeInspector ? btn("Inspect",()=>window.LeakScopeInspector.open({type:"cloud",id:row.id,record:row}),{class:"btn btn-small"}) : null, btn("Add to organization", () => window.LeakScopeAssets?.addToOrganization?.({type:"cloud",id:row.id}), {class:"btn btn-small"}), details)));
        }),{columns:[{key:"name"},{key:"provider",description:"Cloud provider"},{key:"state"},{key:"last_checked",type:"date"},null],state:sorting,onSort:()=>{page=1;refreshInventory();},scope:"Sort all matching saved cloud resources"}));
        inventory.append(el("div", {class: "row-actions"}, btn("Previous", () => {page--; refreshInventory();}, {disabled: page <= 1}),
          el("span", {}, "Page " + page), btn("Next", () => {page++; refreshInventory();}, {disabled: page * 30 >= data.count})));
      }
      function refreshInventory() { const version=inventoryVersion+1;loadInventory().catch(e => {if(!controller.signal.aborted && version===inventoryVersion)inventory.replaceChildren(notice(e.message,true));}); }
      async function loadHistory() {
        const data = await api("/runs");
        history.replaceChildren(...(data.results.length ? data.results.map(run => btn(
          new Date(run.created_at).toLocaleString() + " · " + run.state,
          () => observe(run), {class: "btn btn-small"})) : [notice("No cloud discovery runs yet.")]));
      }
      function loadSpec(value) {
        names.value = value.names.join("\n");
        for (const [key, controls] of providerControls) {
          controls.box.checked = value.providers.includes(key);
          for (const o of controls.region.options) o.selected = !value.regions?.[key] || value.regions[key].includes(o.value);
        }
        for (const [box, key] of [[permutations, "permutations"], [verify, "verify"]]) box.querySelector("input").checked = value[key];
        storage.querySelector("input").checked = value.kinds.includes("storage");
        applications.querySelector("input").checked = value.kinds.includes("applications");
        limit.value = value.max_requests; rate.value = value.rate_per_second;
        active.querySelector("input").checked = false;
        preview.replaceChildren(notice("Previous settings loaded. Preview is available; starting requires direct-request consent."));
        names.focus();
      }
      function drawRun(run, value, frozen) {
        const terminal = ["succeeded", "failed", "skipped", "cancelled", "lost"].includes(run.state);
        progress.replaceChildren(el("h3", {}, "Run · " + run.state),
          notice(value?.summary || run.error || (run.progress_current + " / " + run.progress_total + " candidates processed")));
        if (value?.partial || value?.limited) progress.append(notice("This run has partial or limited coverage. Review individual outcomes and skipped names."));
        if (value?.outcomes?.length) progress.append(outcomeTable(value.outcomes));
        if (value?.skipped_names?.length) progress.append(notice("Names skipped by provider naming rules: " + value.skipped_names.join(", ")));
        if(window.LeakScopeRunLogs)progress.append(window.LeakScopeRunLogs.panel(run,{key:"cloud-"+run.id}));
        if (terminal && frozen?.names) progress.append(btn("Load these settings", () => loadSpec(frozen)));
        if (!terminal) progress.append(btn("Cancel remaining checks", async () => {
          await api("/runs/" + run.id + "/cancel", {}); await observe(run);
        }));
        return terminal;
      }
      async function observe(run) {
        clearTimeout(timer); selectedRun = run.id;
        try {
          const data = run.state === "succeeded" && run.result ? {run, result: run.result, spec: run.parameters} : await api("/runs/" + run.id);
          if (selectedRun !== run.id || controller.signal.aborted) return;
          const done = drawRun(data.run, data.result, data.spec);
          if (done) await Promise.all([loadInventory(), loadHistory()]);
          else timer = setTimeout(() => observe({id: run.id}), 1800);
        } catch (error) { if (!controller.signal.aborted) progress.replaceChildren(notice(error.message, true)); }
      }
      const previewButton = btn("Preview candidates", () => guarded(previewButton, preview, async () => {
        const data = await api("/preview", spec());
        preview.replaceChildren(notice(data.generated + " candidates generated · " + data.candidates.length + " within this run limit · no target requests made"));
        if (data.limited) preview.append(notice("The request limit truncates this plan. Increase it or narrow providers, regions or names."));
        if (data.skipped_names.length) preview.append(notice("Skipped names: " + data.skipped_names.join(", ")));
        preview.append(table(["Name", "Provider", "Region / kind", "Candidate URL"], data.candidates.map(c => el("tr", {}, cell(c.name), cell(c.provider), cell((c.region || "global") + " / " + c.kind), cell(c.url)))));
      }), {"data-action": "preview"});
      const startButton = btn("Start cloud discovery", () => guarded(startButton, progress, async () => {
        if (!active.querySelector("input").checked) throw new Error("Allow direct requests before starting. Preview makes no target requests.");
        const data = await api("/start", {...spec(), active: true});
        await observe(data.run);
      }), {class: "btn btn-primary", "data-action": "start"});
      root.replaceChildren(el("div", {class: "page-heading"}, el("div", {}, el("h1", {}, "Cloud discovery"),
        el("p", {}, "Find cloud storage, databases and hosted applications from resource names."))),
        notice("Preview generates names locally. Starting a run contacts selected cloud endpoints. No API keys are required."),
        panel("Discovery scope", field("Resource names · up to 10, separated by commas or lines", names),
          el("p", {class: "small muted"}, "Names are tried exactly by default. Related-name generation also derives a first-label stem from domain names."),
          el("div", {class: "row-actions"}, btn("Select all providers", () => providerControls.forEach(c => c.box.checked = true)), btn("Clear providers", () => providerControls.forEach(c => c.box.checked = false))),
          providers, el("div", {class: "row-actions"}, storage, applications), permutations,
          el("div", {class: "grid-two"}, field("Maximum candidate checks (1–500)", limit), field("Maximum requests per second (0.1–2)", rate)), verify,
          el("p", {class: "small muted"}, "Verification requests a small listing or shallow database keys. File contents are not downloaded. Redirects are recorded without following them."),
          active, el("div", {class: "row-actions"}, previewButton, startButton)),
        panel("Candidate preview", preview), panel("Run outcomes", progress),
        panel("Cloud resources", el("div", {class: "row-actions"}, search, providerFilter, stateFilter,
          btn("Filter", () => {page = 1; refreshInventory();})), inventory), panel("Recent runs", history));
      await Promise.all([loadInventory(), loadHistory()]);
    } catch (error) { root.replaceChildren(notice(error.message, true)); }
    finally { root.setAttribute("aria-busy", "false"); }
  }
  window.LeakScopeCloud = {render};
})();
