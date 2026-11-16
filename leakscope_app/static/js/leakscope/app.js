(function () {
  "use strict";
  const U = window.LeakScopeUI;
  const root = document.getElementById("page-root");
  const page = document.body.dataset.page || "overview";
  const initialType =
    document.body.dataset.type ||
    new URLSearchParams(location.search).get("type") ||
    "";
  const initialTarget =
    document.body.dataset.targetId ||
    new URLSearchParams(location.search).get("target_id") ||
    "";
  const pageNames = {
    overview: "Home",
    "domain-coverage": "Domain coverage",
    discover: "Discover",
    cloud: "Cloud discovery",
    findings: "Findings",
    detail: "Finding detail",
    runs: "Runs",
    settings: "Settings",
    queries: "Custom queries",
    research: "Research",
    assets: "Observations",
    lookups: "Scheduled lookups",
    build: "Build",
    monitoring: "Monitoring",
    ics: "ICS / HMI gallery",
    certificates: "Certificates",
    organizations: "Organizations",
    "domain-expansion": "Domain expansion",
    relationships: "Relationships",
    catalog: "Service catalog",
    analysis: "Analysis",
    changes: "What changed",
    correlations: "Correlations",
    technologies: "Technologies",
    screenshots: "Screenshots",
    geography: "Geography",
    preview: "Preview",
    explore: "Explore",
    exports: "Export & import",
  };
  let typeCache;
  let previewTimer;
  const watchTimers = new Map();
  function node(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.startsWith("on") && typeof value === "function")
        el.addEventListener(key.slice(2), value);
      else if (key === "checked" || key === "disabled" || key === "selected")
        el[key] = !!value;
      else el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false)
        el.append(
          child instanceof Node
            ? child
            : document.createTextNode(String(child)),
        );
    }
    return el;
  }
  const txt = (value, cls) => node("span", { class: cls, text: U.text(value) });
  const button = (label, action, cls = "btn") =>
    node("button", { type: "button", class: cls, onclick: action }, label);
  const link = (label, href, cls = "btn") =>
    node("a", { href, class: cls }, label);
  function tagsAction(kind, key, values) {
    if (!window.LeakScopeOperations) return null;
    const tags = Array.isArray(values) ? values : [];
    const action = button(
      "Tags (" + tags.length + ")",
      () =>
        window.LeakScopeOperations.openTags(kind, key, tags, (updated) => {
          action.textContent = "Tags (" + updated.length + ")";
        }),
      "btn btn-small",
    );
    return action;
  }
  function badge(state) {
    const normalized = U.state(state);
    return node("span", {
      class: "badge badge-" + normalized,
      text: normalized.replaceAll("_", " "),
    });
  }
  function errorBox(error) {
    return node(
      "div",
      { class: "notice notice-error", role: "alert" },
      U.text(error.message || error),
    );
  }
  function loading() {
    return node(
      "div",
      { class: "loading-inline", role: "status" },
      node("span", { class: "spinner" }),
      "Loading…",
    );
  }
  function empty(title, detail, action) {
    return node(
      "div",
      { class: "empty-state" },
      node("span", { class: "empty-icon", "aria-hidden": "true" }, "⌕"),
      node("h3", {}, title),
      node("p", {}, detail),
      action,
    );
  }
  function toast(message, isError = false) {
    const el = node(
      "div",
      { class: "toast" + (isError ? " error" : "") },
      message,
    );
    document.getElementById("toast-root").append(el);
    setTimeout(() => el.remove(), 6500);
  }
  function heading(title, description, actions = []) {
    return node(
      "div",
      { class: "page-heading" },
      node("div", {}, node("h1", {}, title), node("p", {}, description)),
      node("div", { class: "heading-actions" }, actions),
    );
  }
  function panel(title, description, body, actions = []) {
    if (["Exact query preview", "A useful starting point", "Direct tools", "Blacklist", "Runtime details"].includes(title)) {
      return node("details", {class:"workspace-disclosure", "data-disclosure-id":page+":"+title.toLowerCase().replace(/[^a-z0-9]+/g,"-"), open:title==="Exact query preview"},
        node("summary", {}, node("div", {},node("h2", {},title),description?node("p", {},description):null)),body);
    }
    return node(
      "section",
      { class: "panel" },
      node(
        "div",
        { class: "panel-heading" },
        node(
          "div",
          {},
          node("h2", {}, title),
          description ? node("p", {}, description) : null,
        ),
        node("div", { class: "row-actions" }, actions),
      ),
      body,
    );
  }
  function content(...children) {
    root.replaceChildren(
      ...children.flat().filter((child) => child != null && child !== false),
    );
    root.setAttribute("aria-busy", "false");
  }
  function field(label, input, hint) {
    const id = input.id || "field-" + Math.random().toString(36).slice(2);
    input.id = id;
    return node(
      "label",
      { class: "field", for: id },
      node("span", {}, label),
      input,
      hint ? node("small", {}, hint) : null,
    );
  }
  function input(name, placeholder = "", value = "", type = "text") {
    return node("input", { name, type, placeholder, value });
  }
  function select(name, options, value = "") {
    const el = node("select", { name });
    for (const option of options) {
      const [v, label] = Array.isArray(option)
        ? option
        : [
            option.key || option.value,
            option.label || option.key || option.value,
          ];
      el.append(
        node(
          "option",
          { value: v, selected: String(value) === String(v) },
          label,
        ),
      );
    }
    return el;
  }
  function checkbox(label, name, checked = false, description) {
    const el = node("input", { type: "checkbox", name, checked });
    return node(
      "label",
      { class: "check-row" },
      el,
      node(
        "span",
        {},
        node("strong", {}, label),
        description ? node("small", {}, description) : null,
      ),
    );
  }
  function formValues(form) {
    const data = {};
    for (const [key, value] of new FormData(form)) {
      if (Object.hasOwn(data, key)) {
        data[key] = [].concat(data[key], value);
      } else data[key] = value;
    }
    for (const box of form.querySelectorAll("input[type=checkbox]")) {
      if (box.name && box.name !== "types") data[box.name] = box.checked;
    }
    return data;
  }
  function csrf() {
    return (
      document.querySelector("meta[name=csrf-token]")?.content ||
      document.cookie
        .split("; ")
        .find((x) => x.startsWith("csrftoken="))
        ?.slice(10) ||
      ""
    );
  }
  async function request(path, data, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      method: data === undefined ? "GET" : "POST",
      headers: {
        Accept: "application/json",
        ...(data !== undefined
          ? { "Content-Type": "application/json", "X-CSRFToken": csrf() }
          : {}),
        ...options.headers,
      },
      ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
    });
    let payload;
    try {
      payload = await response.json();
    } catch (_) {
      throw new Error(
        "The server returned an unexpected response. Refresh or try again.",
      );
    }
    if (!response.ok)
      throw new Error(
        U.text(
          payload.error ||
            payload.detail ||
            "Request failed (" + response.status + ").",
        ),
      );
    return payload;
  }
  async function types() {
    if (!typeCache) {
      const data = await request("/api/types");
      typeCache = data.results || data.types || [];
    }
    return typeCache;
  }
  function typeLabel(type) {
    return (
      typeCache?.find((x) => (x.key || x.value) === type)?.label ||
      type ||
      "Unknown"
    );
  }
  function table(headers, rows, options = {}) {
    const t = node(
      "table",
      {},
      node(
        "thead",
        {},
        node(
          "tr",
          {},
          headers.map((h) => node("th", { scope: "col" }, h)),
        ),
      ),
    );
    t.append(node("tbody", {}, rows));
    window.LeakScopeTables?.enhance(t, options);
    return node("div", { class: "table-scroll" }, t);
  }
  function formatCount(n) {
    return U.number(n);
  }
  function structuredValue(value) {
    return value !== null && (typeof value === "object" || (typeof value === "string" && /^[\s]*[\[{]/.test(value) && (() => {try { const parsed=JSON.parse(value); return parsed !== null && typeof parsed === "object"; } catch (_) {return false;} })()));
  }
  function prettyData(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || node("pre", {class:"code-box", text:U.text(value)});
  }
  function evidence(value, sensitive = false, revealLoader = null) {
    const pre = node("pre", { text: U.text(value) });
    if (!sensitive) return structuredValue(value) ? prettyData(value, "Evidence fields") : pre;
    const hidden = node("pre", { text: "••••••••  Sensitive value hidden" }), revealed = node("div");
    let shown = false,
      revealedValue = value,
      loaded = !revealLoader;
    const toggle = button(
      "Reveal",
      async () => {
        if (!shown && !loaded) {
          toggle.disabled = true;
          toggle.textContent = "Loading…";
          try {
            revealedValue = await revealLoader();
            loaded = true;
          } catch (error) {
            toast(error.message, true);
            return;
          } finally {
            toggle.disabled = false;
            toggle.textContent = shown ? "Hide" : "Reveal";
          }
        }
        shown = !shown;
        hidden.textContent = shown
          ? U.text(revealedValue)
          : "••••••••  Sensitive value hidden";
        if (shown && structuredValue(revealedValue) && window.LeakScopeData) {hidden.hidden=true;hidden.textContent="";revealed.replaceChildren(prettyData(revealedValue,"Revealed evidence"));}
        else {hidden.hidden=false;revealed.replaceChildren();}
        toggle.textContent = shown ? "Hide" : "Reveal";
        toggle.setAttribute("aria-pressed", String(shown));
      },
      "btn btn-small",
    );
    toggle.setAttribute("aria-pressed", "false");
    return node("div", { class: "sensitive-value" }, hidden, revealed, toggle);
  }
  function renderFields(fields, revealLoader = null) {
    const dl = node("dl", { class: "detail-grid" });
    for (const [name, value] of Object.entries(fields || {})) {
      dl.append(
        node("dt", {}, name.replaceAll("_", " ")),
        node(
          "dd",
          {},
          evidence(
            value,
            U.isSensitive(name),
            revealLoader ? () => revealLoader(name) : null,
          ),
        ),
      );
    }
    return dl;
  }
  function safeResult(value) {
    if (Array.isArray(value)) return value.map(safeResult);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value).map(([k, v]) => [
          k,
          U.isSensitive(k) ? "[sensitive value hidden]" : safeResult(v),
        ]),
      );
    return value;
  }
  function runResult(run) {
    let result = run.result;
    if (typeof result === "string") {
      try {
        result = JSON.parse(result);
      } catch (_) {}
    }
    return result;
  }
  function runCard(run, viewerKey, onStopped) {
    const state = run.state || run.status || "queued",
      pct = U.progress(run),
      result = runResult(run);
    const card = node(
      "div",
      { class: "run-card" },
      node(
        "div",
        { class: "run-card-heading" },
        node(
          "span",
          { class: "run-card-title" },
          run.type ? typeLabel(run.type) : (run.kind || "Background job").replaceAll("_"," ").replace(/^./,value=>value.toUpperCase()),
        ),
        badge(result?.partial && state === "succeeded" ? "partial" : state),
      ),
      node(
        "div",
        { class: "run-meta" },
        [run.kind, run.provider, U.date(run.created_at)]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    let parameters = run.parameters;
    if (typeof parameters === "string") {
      try { parameters = JSON.parse(parameters); } catch (_) { parameters = null; }
    }
    const collectionMode = result?.collection?.mode || result?.collection_mode || parameters?.collection_mode;
    const collectionLabel = {
      demo: "Demo", passive: "Passive", semi_passive: "Semi-passive", screenshot: "Screenshot", full: "Full",
    }[collectionMode];
    if (collectionLabel)
      card.append(node("p", { class: "small run-collection-mode" }, "Collection: " + collectionLabel));
    if (result?.demo || parameters?.demo || collectionMode === "demo") card.append(node("p", {class:"notice small run-demo-notice"}, "Demo · real minimal API usage. This is a limited sample; saved-result limits do not change a provider’s minimum billable page."));
    if (run.kind === "lookup") {
      if (typeof result?.stored === "number" && Number.isFinite(result.stored)) {
        card.append(node("p", { class: "run-result-summary" }, result.stored + (result.stored === 1 ? " result saved" : " results saved")));
        if (typeof result.new_results === "number" && typeof result.repeat_results === "number" && result.stored > 0)
          card.append(node("p", { class: "small run-repeat-summary" }, result.new_results + " new · " + result.repeat_results + " captured before" +
            (typeof result.start_page === "number" && typeof result.next_page === "number" && result.next_page > result.start_page
              ? " · pages " + result.start_page + (result.next_page - 1 > result.start_page ? "–" + (result.next_page - 1) : "") : "")));
        if (state === "succeeded" && !result.partial && result.total === 0 && result.stored === 0)
          card.append(node("p", { class: "notice small" }, "No matches returned by " + (run.provider || "the provider") + "."));
      }
      if (typeof result?.query === "string" && result.query)
        card.append(node("div", { class: "mt-small" }, node("span", { class: "small muted" }, "Exact provider query"),
          node("pre", { class: "code-box run-provider-query", text: result.query })));
      if (run.id)
        card.append(link("View results →", "/assets/?run=" + encodeURIComponent(run.id), "btn btn-primary btn-small run-observations-link"));
    }
    if (!U.isTerminal(state))
      card.append(
        node(
          "div",
          {
            class: "progress-track"+(pct === null ? " is-indeterminate" : ""),
            role: "progressbar",
            "aria-label": "Run progress",
            "aria-valuemin": 0,
            "aria-valuemax": 100,
            ...(pct !== null ? { "aria-valuenow": pct } : {}),
          },
          node("div", {
            class: "progress-fill",
            style: "width:" + (pct === null ? 8 : pct) + "%",
          }),
        ),
        node(
          "span",
          { class: "small muted" },
          pct === null ? (run.kind === "domain_expansion" ? "Collecting evidence · the discovery queue can grow" : "Waiting for progress…") : pct + "% complete",
        ),
      );
    if (run.error)
      card.append(
        node(
          "div",
          { class: "notice notice-error mt-small" },
          U.text(run.error),
        ),
      );
    if (result?.partial)
      card.append(
        node(
          "div",
          { class: "notice mt-small" },
          "Partial results saved; some pages or results failed",
        ),
      );
    if (result && U.isTerminal(state)) {
      if (result.error && !run.error)
        card.append(
          node(
            "div",
            { class: "notice notice-error mt-small" },
            U.text(result.error),
          ),
        );
      const token = result.download_token;
      if (token && /^[a-zA-Z0-9_-]+$/.test(token))
        card.append(
          link(
            "Download export ↓",
            "/export/download/" + encodeURIComponent(token),
            "btn btn-primary btn-small run-result-link",
          ),
        );
      if (result.summary)
        card.append(node("p", { class: "small" }, result.summary));
      card.append(
        node(
          "details",
          { class: "mt-small" },
          node("summary", {}, "Result details"),
          prettyData(safeResult(result), "Run result"),
        ),
      );
    }
    const expansion=run.expansion || run.activity?.expansion || (run.kind==="domain_expansion" ? run.activity : null);
    if(expansion) {
      const facts=[];
      if(expansion.saved_candidates!=null)facts.push(expansion.saved_candidates+" candidates saved");
      if(expansion.processed!=null)facts.push(expansion.processed+" seeds processed");
      if(expansion.current_source)facts.push("Source: "+expansion.current_source);
      if(expansion.current_seed)facts.push("Seed: "+expansion.current_seed);
      if(expansion.current_action)facts.push(expansion.current_action);
      if(expansion.source_queries_completed!=null)facts.push(expansion.source_queries_completed+" source queries completed");
      if(expansion.pending_queries!=null)facts.push(expansion.pending_queries+" source queries pending");
      if(facts.length)card.append(node("p",{class:"run-live-facts"},facts.join(" · ")));
      const expansionId=expansion.id || expansion.expansion_id;
      if(expansionId)card.append(link("Open saved expansion results →","/domain-expansion/?id="+encodeURIComponent(expansionId),"btn btn-small"));
    }
    if(run.updated_at)card.append(node("p",{class:"small muted"},"Last activity: "+U.date(run.updated_at)));
    if(run.cancellation?.message)card.append(node("p",{class:"notice small"},run.cancellation.message));
    if(run.id && !U.isTerminal(state) && (run.can_cancel === true || run.kind === "domain_expansion")) {
      const stopOutput=node("div",{role:"status"});
      const stop=button("Stop run",()=>perform(stop,async()=>{
        const stopped=await request("/api/runs/"+encodeURIComponent(run.id)+"/cancel",{});
        const latest={...run,...(stopped.run || stopped),cancellation:stopped.cancellation};
        if(latest.state || latest.status) {
          if(onStopped)onStopped(latest);else card.replaceWith(runCard(latest,viewerKey));
        }
      },stopOutput),"btn btn-small");
      card.append(stop,stopOutput);
    }
    if(run.id && window.LeakScopeRunLogs)card.append(window.LeakScopeRunLogs.panel(run,{key:viewerKey}));
    if (run.id)card.append(node("div", { class: "run-meta mono" }, String(run.id)));
    return card;
  }
  let runViewerSequence=0;
  const runViewerKeys=new WeakMap();
  async function watchRun(run, container, onComplete, options = {}) {
    if(!runViewerKeys.has(container))runViewerKeys.set(container,"run-card-"+(++runViewerSequence));
    const viewerKey=runViewerKeys.get(container);
    const id = run.id || run.run_id || run.task_id;
    const signal = options.signal;
    if (!id || signal?.aborted) return;
    let timer, watching=true;
    const stop = () => {
      watching=false;
      clearTimeout(timer);
      watchTimers.delete(id);
      signal?.removeEventListener("abort", stop);
    };
    signal?.addEventListener("abort", stop, { once: true });
    let attempts = 0,
      errors = 0;
    const stopped=latest=>{stop();container.replaceChildren(runCard(latest,viewerKey));if(onComplete)onComplete(latest);};
    async function poll() {
      if (signal?.aborted || !watching) return;
      try {
        const latest = await request(
          "/api/runs/" + encodeURIComponent(id),
          undefined,
          { signal },
        );
        if (signal?.aborted || !watching) return;
        errors = 0;
        container.replaceChildren(runCard(latest,viewerKey,stopped));
        if (U.isTerminal(latest.state || latest.status)) {
          stop();
          if (onComplete) onComplete(latest);
          return;
        }
      } catch (error) {
        if (signal?.aborted) return;
        errors++;
        if (errors >= 3) {
          container.append(
            errorBox(
              new Error(
                "Progress updates paused. Open Runs to check this job. " +
                  error.message,
              ),
            ),
          );
          stop();
          return;
        }
      }
      attempts++;
      if (attempts >= 180) {
        container.append(
          node(
            "div",
            { class: "notice mt-small" },
            "This job is still running. Follow its progress from the Runs page.",
          ),
        );
        stop();
        return;
      }
      timer = setTimeout(
        poll,
        document.hidden ? 10000 : Math.min(1500 + attempts * 100, 5000),
      );
      watchTimers.set(id, timer);
    }
    container.replaceChildren(runCard(run,viewerKey,stopped));
    if (!U.isTerminal(run.state || run.status)) {
      timer = setTimeout(poll, 700);
      watchTimers.set(id, timer);
    } else {
      stop();
      if (onComplete) onComplete(run);
    }
  }
  window.LeakScopeWorkbench = { watchRun };
  function openRuns(runs, onComplete) {
    const dialog = document.getElementById("run-dialog"),
      body = document.getElementById("run-dialog-body");
    document.getElementById("run-dialog-title").textContent = "Run progress";
    body.replaceChildren();
    if(window.LeakScopeRunLogs)body.append(window.LeakScopeRunLogs.preference());
    for (const run of runs) {
      const holder = node("div");
      body.append(holder);
      watchRun(run, holder, onComplete);
    }
    body.append(link("View all runs →", "/runs/", "btn btn-quiet mt"));
    if (!dialog.open) dialog.showModal();
  }
  async function perform(buttonEl, action, errorTarget) {
    const old = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = "Working…";
    try {
      await action();
    } catch (error) {
      if (errorTarget) errorTarget.replaceChildren(errorBox(error));
      else toast(error.message, true);
    } finally {
      buttonEl.disabled = false;
      buttonEl.textContent = old;
    }
  }
  function runsTable(runs) {
    if (!runs.length) return empty("No runs yet","Start a discovery search or inspect a finding. Your job history will appear here.",link("Start discovery →","/discover/","btn btn-primary"));
    const wrapper=node("div",{class:"job-history"}),body=node("div"),pager=node("nav",{class:"table-pager","aria-label":"Job history pages"});
    let current=0;
    const sorting={key:null,direction:"asc"};
    const columns=[{key:"operation",value:r=>(r.type?typeLabel(r.type):r.kind)||"Run"},{key:"provider",value:r=>r.provider||"direct"},{key:"status",value:r=>r.state||r.status},{key:"started",type:"date",value:r=>r.created_at},null];
    const position=node("span",{class:"small muted","aria-live":"polite"}),prev=button("Previous",()=>{current--;draw()},"btn btn-small"),next=button("Next",()=>{current++;draw()},"btn btn-small");
    if(runs.length>25)pager.append(position,prev,next);
    function draw() {
      const focused=document.activeElement;
      const sorted=window.LeakScopeTables?.sort(runs,sorting,columns)||runs;
      const start=current*25,visible=sorted.slice(start,start+25);
      const rowFor=r=>node("tr",{},
        node("td",{},node("span",{class:"cell-title"},(r.type?typeLabel(r.type):r.kind)||"Run"),node("span",{class:"cell-sub"},r.kind||"discovery")),
        node("td",{},node("span",{class:"provider-badge"},r.provider||"direct")),node("td",{},badge(r.state||r.status)),node("td",{class:"small muted","data-sort-value":r.created_at||""},U.date(r.created_at)),
        node("td",{},button("View →",()=>openRuns([r]),"btn btn-small btn-quiet")));
      const shown=table(["Operation / service","Provider","Status","Started",""],visible.map(rowFor),{columns,state:sorting,onSort:()=>{current=0;draw();},scope:"Sort all "+runs.length+" loaded jobs"});
      // Export takes every loaded job, not only the 25 on this page.
      shown.querySelector("table").lsExportRows=()=>sorted.map(rowFor);
      body.replaceChildren(shown);
      if(runs.length>25) {
        position.textContent=(start+1)+"–"+Math.min(start+25,runs.length)+" of "+runs.length+" loaded jobs";
        prev.disabled=current===0;next.disabled=start+25>=runs.length;
        if(focused===next&&next.disabled)prev.focus();else if(focused===prev&&prev.disabled)next.focus();
      }
    }
    wrapper.append(body,pager);draw();return wrapper;
  }
  async function overview() {
    const [data] = await Promise.all([request("/api/summary"), types()]);
    const counts = data.counts || {};
    const greeting = node(
      "div",
      { class: "welcome" },
      node(
        "div",
        {},
        node("h2", {}, "A clearer view of your exposure"),
        node(
          "p",
          {},
          "Discover public exposure signals, inspect deliberately, and keep the source attached.",
        ),
      ),
      node(
        "div",
        { class: "welcome-graphic", "aria-hidden": "true" },
        Array.from({ length: 14 }, () => node("i")),
      ),
      link("New discovery +", "/discover/", "btn btn-primary"),
    );
    const legacyMetrics = node(
      "div",
      { class: "summary-grid" },
      [
        [
          "Legacy findings",
          counts.total ?? counts.findings,
          "Older service-specific result tables",
          "▤",
        ],
        ["Confirmed", counts.confirmed, "Legacy findings reviewed and confirmed", "✓"],
        ["For review", counts.for_later, "Legacy findings saved to revisit", "◷"],
        ["Hidden", counts.hidden, "Legacy findings excluded from their default view", "◌"],
      ].map(([label, value, meta, icon]) =>
        node(
          "div",
          { class: "metric" },
          node("span", { class: "label" }, label),
          node("span", { class: "metric-icon", "aria-hidden": "true" }, icon),
          node("strong", { class: "number" }, formatCount(value)),
          node("span", { class: "meta" }, meta),
        ),
      ),
    );
    const observed = data.observations || {};
    const metrics = node("div", {class:"summary-grid"}, [
      ["Saved endpoints", observed.endpoints, "Visible addresses and services", "/assets/", "▤"],
      ["Observations", observed.observations, "Visible source and fetch history", "/assets/", "◉"],
      ["Evidence files", observed.artifacts, "Screenshots, responses and protocol captures", "/assets/", "▧"],
      ["Saved lookups", observed.lookups, "Reusable searches and schedules", "/lookups/", "◷"],
    ].map(([label,value,meta,href,icon]) => node("a", {class:"metric",href},
      node("span",{class:"label"},label), node("span",{class:"metric-icon","aria-hidden":"true"},icon),
      node("strong",{class:"number"},formatCount(value)), node("span",{class:"meta"},meta))));
    const legacyReview = node("details",{class:"panel"},
      node("summary",{class:"panel-heading"}, "Legacy finding review · " + formatCount(counts.total ?? counts.findings)),
      node("div",{class:"panel-body"}, node("p",{class:"small muted"},
        "These review counters apply to the older service-specific Findings tables. New discovery results, source history and captures are saved in Observations."),
        legacyMetrics, link("Browse legacy findings →","/findings/","btn btn-small")));
    const byType = (observed.by_type || []).map(item => ({
          key:item.service_type || "", label:item.service_type || "Unclassified", count:item.count,
        }))
        .filter((t) => t.count > 0)
        .sort((a, b) => b.count - a.count)
        .slice(0, 7),
      max = Math.max(1, ...byType.map((t) => t.count));
    const bars = byType.length
      ? node(
          "div",
          { class: "panel-body data-bars" },
          byType.map((t) =>
            node(
              "div",
              {},
              node(
                "div",
                { class: "data-bar-top" },
                node(
                  "strong",
                  {},
                  t.key ? link(t.label, "/assets/?type=" + encodeURIComponent(t.key), "") : txt(t.label),
                ),
                txt(formatCount(t.count)),
              ),
              node(
                "div",
                { class: "data-track" },
                node("div", {
                  class: "data-fill",
                  style: "width:" + (t.count / max) * 100 + "%",
                }),
              ),
            ),
          ),
        )
      : data.observations?.endpoints > 0
        ? empty(
            "No service-specific findings",
            "Your provider and capture evidence is available in Observations.",
            link("Open observations →", "/assets/", "btn btn-small"),
          )
        : empty(
            "Your collection starts here",
            "Service distribution appears as discovery results are stored.",
          );
    let observedPanel = null;
    if (data.observations) {
      const observed = data.observations;
      const observedSources = node(
        "div",
        { class: "row-actions" },
        ...(observed.by_provider || []).map((item) =>
          node(
            "span",
            { class: "provider-badge" },
            item.provider + " · " + formatCount(item.count),
          ),
        ),
      );
      observedPanel = panel(
        "Observed assets",
        "Endpoints and evidence collected by lookups, streams and fetches.",
        node(
          "div",
          { class: "panel-body ops-observation-summary" },
          node(
            "p",
            { class: "small muted" },
            formatCount(observed.lookups) +
              " saved lookups · " +
              formatCount(observed.watches) +
              " scope watches",
          ),
          observedSources,
          node(
            "div",
            { class: "row-actions mt" },
            link("Observations →", "/assets/", "btn btn-primary btn-small"),
            link("Monitoring →", "/monitoring/", "btn btn-small"),
            link("Analysis →", "/analysis/", "btn btn-small"),
          ),
        ),
      );
    }
    const providers = data.providers || {},
      byProvider = observed.by_provider || [];
    const providerBody = node(
      "div",
      { class: "panel-body" },
      ["shodan", "zoomeye", "netlas"].map((provider) => {
        const record = providers[provider] || {},
          count = byProvider.find((x) => x.provider === provider)?.count || 0;
        return node(
          "div",
          { class: "provider-row" },
          node(
            "div",
            { class: "provider-icon" },
            { shodan: "SH", zoomeye: "ZE", netlas: "NL" }[provider] || provider,
          ),
          node(
            "div",
            {},
            node(
              "strong",
              {},
              { shodan: "Shodan", zoomeye: "ZoomEye", netlas: "Netlas" }[
                provider
              ] || provider,
            ),
            node(
              "small",
              {},
              record.configured ? "Configured" : "Check configuration",
            ),
          ),
          node("div", { class: "provider-count" }, formatCount(count)),
        );
      }),
      node(
        "p",
        { class: "small muted mt" },
        "Counts reflect saved endpoints for each provider; a shared endpoint can appear under several sources. Account balances update only when requested.",
      ),
      link("Provider settings →", "/settings/", "btn btn-small btn-quiet"),
    );
    const V = window.LeakScopeViz;
    const recentStates = new Map();
    for (const run of data.recent_runs || []) {const state=U.state(run.state || run.status);recentStates.set(state,(recentStates.get(state)||0)+1);}
    const insight = V ? V.disclosure({id:"overview-collection-insights",title:"Collection insights",summary:"Saved data · latest job sample",open:innerWidth>800,children:node("div",{class:"ls-viz-grid"},
      V.bars({title:"Observed endpoints by provider",unit:"endpoints",coverageLabel:"Distinct saved endpoints for each source. A shared endpoint can appear under multiple providers.",rows:(data.observations?.by_provider||[]).map(r=>({key:r.provider,label:r.provider,count:r.count,href:"/assets/?provider="+encodeURIComponent(r.provider)}))}),
      V.bars({title:"Recent job states",unit:"jobs",coverageLabel:"Latest "+(data.recent_runs||[]).length+" saved jobs · sample, not all history",rows:[...recentStates].map(([key,count])=>({key,label:key.replaceAll("_"," "),count,href:"/runs/"}))})
    )}) : null;
    const technologyPanel = panel("Saved endpoints by technology", "Visible saved endpoints, grouped by technology", bars);
    const providerPanel = panel("Provider coverage", "Distinct saved endpoints for each source", providerBody);
    const runsPanel = panel(
      "Recent runs",
      "Durable job history across discovery and inspection",
      runsTable((data.recent_runs || []).slice(0, 6)),
      [link("All runs →", "/runs/", "btn btn-small btn-quiet")],
    );
    const actions = [link("New discovery", "/discover/", "btn btn-primary"), link("Browse observations", "/assets/", "btn")];
    const instruments = window.LeakScopeScope ? window.LeakScopeScope.overview({ summary: data, metrics }) : null;
    if (!instruments) {
      // Without the instrument layer the page keeps its tabular layout.
      content(
        heading("Overview", "Your collection, recent activity, and the next useful step.", actions),
        greeting, metrics, insight, observedPanel, legacyReview,
        node("div", { class: "grid-two" }, technologyPanel, providerPanel),
        node("div", { class: "mt" }, runsPanel),
      );
      return;
    }
    // The tables stay one click away for exact figures; the instruments lead.
    const ledger = node("details", { class: "scope-ledger", "data-disclosure-id": "overview:ledger" },
      node("summary", {}, node("h2", {}, "Collection ledger"), node("span", {}, "Providers, technologies and recent jobs as tables")),
      node("div", { class: "scope-ledger-body" },
        greeting, insight, observedPanel,
        node("div", { class: "grid-two" }, technologyPanel, providerPanel),
        node("div", { class: "mt" }, runsPanel),
        legacyReview));
    content(
      heading("Overview", "Your collection, recent activity, and the next useful step.", actions),
      instruments,
      ledger,
    );
  }
  async function openEndpointInspector(record) {
    if(window.LeakScopeInspector)return window.LeakScopeInspector.open({type:"endpoint",id:record.id});
    const controller = new AbortController(), previousFocus=document.activeElement;
    const dialog=node("dialog",{class:"discovery-inspector", "aria-labelledby":"discovery-inspector-title"});
    const close=()=>{controller.abort();if(dialog.open)dialog.close();dialog.remove();if(previousFocus?.isConnected)previousFocus.focus();};
    const pane=node("div",{class:"discovery-inspector-body"},loading());
    const header=node("div",{class:"dialog-heading"},node("div",{},node("span",{class:"eyebrow"},"SAVED SERVICE EVIDENCE"),node("h2",{id:"discovery-inspector-title"},record.title||record.address||"Observation"),node("p",{class:"mono small muted"},record.address+":"+record.port)),button("×",close,"icon-button"));
    header.querySelector("button").setAttribute("aria-label","Close service inspector");
    dialog.append(header,pane);document.body.append(dialog);
    dialog.addEventListener("cancel",e=>{e.preventDefault();close();});
    dialog.addEventListener("close",()=>{controller.abort();dialog.remove();},{once:true});
    const leave=()=>{controller.abort();dialog.remove();};addEventListener("pagehide",leave,{once:true});
    controller.signal.addEventListener("abort",()=>removeEventListener("pagehide",leave),{once:true});
    dialog.showModal();
    try {
      const data=await request("/api/operations/endpoints/"+encodeURIComponent(record.id),undefined,{signal:controller.signal});
      if(controller.signal.aborted)return;
      const endpoint=data.endpoint||record,body=node("div"),tabs=node("div",{class:"inspector-tabs",role:"group","aria-label":"Evidence view"});
      function show(name){
        tabs.querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",String(b.textContent===name)));
        if(name==="Summary")body.replaceChildren(renderFields({address:endpoint.address,port:endpoint.port,transport:endpoint.transport,service:typeLabel(endpoint.service_type),title:endpoint.title,first_seen:endpoint.first_seen,last_seen:endpoint.last_seen,observations:endpoint.observation_count}),node("p",{class:"notice small"},"Saved observations retain their source. Current availability requires an explicit inspection."),
          node("div",{class:"row-actions"},link("Open full observation →","/assets/?endpoint="+encodeURIComponent(endpoint.id),"btn btn-primary"),
          window.LeakScopeAssets?button("Add to organization",()=>{close();window.LeakScopeAssets.addToOrganization({type:"endpoint",id:endpoint.id})},"btn btn-small"):null));
        else if(name==="Evidence")body.replaceChildren(node("p",{class:"small muted"},(data.observations||[]).length+" saved provider records loaded. Sensitive fields retain server masking."),prettyData(data.observations||[],"Provider observations"));
        else {
          const artifacts=data.artifacts||[];
          body.replaceChildren(artifacts.length?node("p",{class:"small muted"},artifacts.length+" saved artifacts loaded. Opening this view starts no capture."):empty("No saved captures","Open the full observation to explicitly choose applicable collection actions."));
          for(const artifact of artifacts){
            const group=node("details",{class:"inspector-artifact"},node("summary",{},(artifact.kind||"Artifact")+" · "+(artifact.tool||"saved")+" · "+U.date(artifact.created_at)),prettyData(artifact.metadata||{},"Capture metadata"));
            try{const url=new URL(artifact.url,location.href);if(url.origin===location.origin&&url.pathname.startsWith("/api/operations/artifacts/")){group.append(link("Open saved artifact ↗",url.href,"btn btn-small"));if(artifact.kind==="screenshot")group.addEventListener("toggle",()=>{if(group.open&&!group.querySelector("img"))group.append(node("img",{src:url.href,alt:"Saved screenshot for "+endpoint.address,loading:"lazy"}))});}}catch(_){}
            body.append(group);
          }
        }
      }
      for(const name of ["Summary","Evidence","Captures"])tabs.append(button(name,()=>show(name),"inspector-tab"));
      pane.replaceChildren(tabs,body);show("Summary");
    }catch(error){if(!controller.signal.aborted)pane.replaceChildren(errorBox(error));}
  }

  async function discover() {
    const allTypes = await types(),
      available = allTypes.filter((t) => t.discovery !== false);
    const discoveryParams = new URLSearchParams(location.search);
    const selected = discoveryParams.get("service_type") || initialType || "";
    const initialMode = ["fingerprint", "nonstandard", "ports", "legacy"].includes(discoveryParams.get("query_mode"))
      ? discoveryParams.get("query_mode") : "fingerprint";
    const selectedProfile = available.find((item) => (item.key || item.value) === selected);
    const providersForProfile = selectedProfile?.discovery_modes?.[initialMode]
      || selectedProfile?.discovery_providers || [];
    const requestedProvider = discoveryParams.get("provider");
    const initialProvider = ["shodan", "zoomeye", "netlas"].includes(requestedProvider)
      ? requestedProvider : providersForProfile[0] || "shodan";
    const form = node("form", { class: "form-stack" }),
      errors = node("div"),
      latestResults = node("section", { class: "mt", "data-discovery-results": "", "aria-live": "polite" }),
      queryBox = node("pre", {
        class: "code-box",
        text: "Enter a keyword or select a service type to preview the provider query.",
      });
    const provider = node(
      "div",
      { class: "provider-choice" },
      ["shodan", "zoomeye", "netlas"].map((p) =>
        node(
          "label",
          { class: "radio-card" },
          node("input", {
            type: "radio",
            name: "provider",
            value: p,
            checked: p === initialProvider,
          }),
          { shodan: "Shodan", zoomeye: "ZoomEye", netlas: "Netlas" }[p],
        ),
      ),
    );
    const collectionModes = [
      ["passive", "Passive", "Search the provider and save its results. No direct target requests."],
      ["demo", "Demo", "Real provider requests: one minimum supported page and at most one saved result per selected query. No automatic target collection."],
      ["semi_passive", "Semi-passive", "Fingerprint the service or check that its TCP connection is reachable."],
      ["screenshot", "Screenshot", "Capture a browser screenshot of matching web interfaces."],
      ["full", "Full", "Collect all applicable fingerprints, responses, wire evidence and screenshots."],
    ];
    const requestedCollection = discoveryParams.get("collection_mode");
    const initialCollection = collectionModes.some(([value]) => value === requestedCollection)
      ? requestedCollection : "passive";
    const collectionChoice = node("div", {
      class: "collection-choice", role: "radiogroup", "aria-label": "Collection mode",
    }, collectionModes.map(([value, label, detail]) => node("label", { class: "radio-card collection-card" },
      node("input", { type: "radio", name: "collection_mode", value, checked: value === initialCollection }),
      node("span", {}, node("strong", {}, label + (value === "passive" ? " · default" : "")), node("small", {}, detail)),
    )));
    const collectionHint = node("p", { class: "collection-mode-hint notice small", "aria-live": "polite" });
    function collectionDescription() {
      const value = form.querySelector("[name=collection_mode]:checked")?.value || "passive";
      const selected = collectionModes.find(([key]) => key === value);
      return { value, label: selected[1], detail: selected[2] };
    }
    function updateCollectionHint() {
      const selected = collectionDescription();
      pages.disabled = selected.value === "demo";
      pages.closest(".field").hidden = selected.value === "demo";
      if (selected.value === "demo") {pageSize.disabled = true;pageSizeField.hidden = true;}
      startField.hidden = selected.value === "demo";
      if (selected.value === "demo") startValueField.hidden = true; else startControls();
      collectionHint.textContent = selected.value === "demo"
        ? "Demo performs real requests: one minimum supported provider page per selected query and at most one saved result each. Fixed-page credits may still apply; no automatic follow-up collection. Your normal page settings are retained."
        : selected.value === "passive"
        ? "Passive collection makes no direct target requests. The capability labels describe available captures, not work already running."
        : selected.value === "screenshot"
          ? "Screenshot collection makes direct target requests to applicable web interfaces. Non-web targets and unavailable browser capture are reported as skipped."
          : selected.value === "semi_passive"
            ? "Semi-passive collection makes bounded direct target requests: an HTTP fingerprint, supported native handshake or TCP reachability check. A connection alone does not prove the protocol."
            : "Full collection makes direct target requests for every applicable capture. Native protocols retain wire evidence; screenshots require a web interface. Unsupported operations are reported as skipped.";
    }
    const categoryNames = {
      database: "Databases",
      storage: "Data storage",
      ot: "ICS / SCADA / OT",
      iot: "IoT / embedded",
      messaging: "Messaging",
      web: "Web / applications",
      other: "Other services",
    };
    const categoriesFor = (item) =>
      item.categories?.length ? item.categories : [item.category || "other"];
    const mode = select(
      "query_mode",
      [
        ["fingerprint", "All indexed ports · response fingerprints"],
        ["nonstandard", "Exclude reference ports · optional"],
        ["ports", "Only reference ports · optional candidates"],
        ["legacy", "Legacy provider presets · review filters"],
      ],
      initialMode,
    );
    const modeHint = node("p", { class: "hint" });
    const serviceSearch = input(
      "service_search",
      "Search service names, protocols or categories",
    );
    serviceSearch.setAttribute("aria-label", "Search service types");
    const categoryKeys = [...new Set(available.flatMap(categoriesFor))];
    const categoryFilter = select("service_category", [
      ["", "All categories"],
      ...categoryKeys.map((key) => [key, categoryNames[key] || key]),
    ]);
    const count = node("p", { class: "small muted", "aria-live": "polite" });
    const selection = node("div", { class: "selected-services", "aria-label": "Selected services" });
    const choices = node("div", { class: "service-catalog-groups" });
    const chips = node("div", {
      class: "service-category-chips",
      role: "group",
      "aria-label": "Service categories",
    });
    for (const key of categoryKeys) {
      const chip = node(
        "button",
        { type: "button", class: "btn btn-small", "data-category": key },
        (categoryNames[key] || key) +
          " (" +
          available.filter((item) => categoriesFor(item).includes(key)).length +
          ")",
      );
      chip.addEventListener("click", () => {
        categoryFilter.value = categoryFilter.value === key ? "" : key;
        filterChoices();
      });
      chips.append(chip);
      const group = node(
        "section",
        { class: "service-type-group", "data-category": key },
        node("h4", {}, categoryNames[key] || key),
      );
      const grid = node("div", { class: "type-grid" });
      for (const t of available.filter(
        (item) => categoriesFor(item)[0] === key,
      )) {
        const capture = t.fetch_actions || t.capture?.actions || [];
        grid.append(
          node(
            "label",
            { class: "type-check", "data-service": t.key || t.value },
            node("input", {
              type: "checkbox",
              name: "types",
              value: t.key || t.value,
              checked: (t.key || t.value) === selected,
            }),
            node(
              "span",
              {},
              node("strong", {}, t.label),
              node(
                "small",
                { class: "service-capture" },
                capture.length
                  ? "Available captures · " + capture
                      .map(
                        (action) =>
                          ({
                            check: "Connection reachability",
                            tcp: "TCP evidence",
                            udp: "UDP evidence",
                            httpx: "HTTP fingerprint",
                            screenshot: "Screenshot",
                            identity: "Protocol identity",
                            http: "HTTP response",
                          })[action] || action,
                      )
                      .join(" · ")
                  : "Available captures depend on the observed endpoint",
              ),
            ),
            node("small", { class: "service-availability" }),
          ),
        );
      }
      if (grid.children.length) {
        group.append(grid);
        choices.append(group);
      }
    }
    function filterChoices() {
      const q = serviceSearch.value.trim().toLowerCase();
      let visible = 0;
      for (const label of choices.querySelectorAll("label[data-service]")) {
        const item = available.find(
          (item) => (item.key || item.value) === label.dataset.service,
        );
        label.hidden = !!(
          (categoryFilter.value &&
            !categoriesFor(item).includes(categoryFilter.value)) ||
          ![
            item.key,
            item.label,
            ...categoriesFor(item).flatMap((key) => [key, categoryNames[key]]),
          ]
            .join(" ")
            .toLowerCase()
            .includes(q)
        );
        if (!label.hidden) visible++;
      }
      for (const section of choices.children)
        section.hidden = ![...section.querySelectorAll("label")].some(
          (label) => !label.hidden,
        );
      for (const chip of chips.children)
        chip.setAttribute(
          "aria-pressed",
          String(chip.dataset.category === categoryFilter.value),
        );
      count.textContent =
        visible +
        " services shown · " +
        choices.querySelectorAll("input:checked").length +
        " selected";
      const checked = [...choices.querySelectorAll("input:checked")];
      selection.replaceChildren(
        node("span", { class: "small muted" }, checked.length ? "Selected services:" : "No service filter · search by keyword."),
        ...checked.map((checkbox) => {
          const label = typeLabel(checkbox.value);
          const remove = node("button", {
            type: "button", class: "btn btn-small",
            "aria-label": "Remove " + label + " from selection",
          }, label + (checkbox.disabled ? " · unavailable" : "") + " ×");
          remove.addEventListener("click", () => {
            checkbox.checked = false;
            filterChoices();
            schedulePreview();
          });
          return remove;
        }),
      );
    }
    serviceSearch.addEventListener("input", filterChoices);
    categoryFilter.addEventListener("change", filterChoices);
    const keyword = input("keyword", "Company, domain, or phrase"),
      network = input("network", "IP address or CIDR"),
      country = input("country", "Country code, e.g. US"),
      exclude = input("exclude", "Comma-separated terms");
    const pages = input("page_count", "", 1, "number");
    pages.min = 1;
    pages.max = 20;
    // Where the provider's result list is entered. "continue" resumes after the pages earlier runs of the
    // same search fetched, so credits are not spent on results that are already saved.
    const start = select("start", [
        ["continue", "Where the last run stopped (new results only)"],
        ["beginning", "The beginning (repeat results is OK)"],
        ["page", "A page number"],
        ["after", "After a result number"],
      ], "continue"),
      startValue = input("start_value", "", "", "number");
    startValue.min = 0;
    const startField = field("Start at", start, "Provider ordering shifts as their index updates, so a few results can still repeat or be missed."),
      startValueField = field("Page number", startValue);
    function startControls() {
      const mode = start.value, numbered = mode === "page" || mode === "after";
      startValueField.hidden = !numbered;
      startValue.disabled = !numbered;
      startValue.min = mode === "page" ? 1 : 0;
      startValueField.querySelector("span").textContent = mode === "after" ? "Skip this many results" : "Page number";
      if (numbered && startValue.value === "") startValue.value = mode === "page" ? 2 : 100;
    }
    start.addEventListener("change", startControls);
    startControls();
    function startLine(item) {
      if (!item || typeof item.page !== "number") return "";
      const at = "page " + item.page + " (result " + item.first_result + " onwards, " + item.page_size + " per page)";
      if (item.mode === "continue") {
        if (item.exhausted) return "\nStart: earlier runs already fetched every result for this search" + (item.total != null ? " (" + item.total + ")" : "") + ". Choose the beginning to fetch them again.";
        return item.known
          ? "\nStart: " + at + " · earlier runs already fetched the pages before it" + (item.total != null ? " · provider reported " + item.total + " in total" : "") + "."
          : "\nStart: the first page · no earlier run of this exact search and page size is recorded.";
      }
      if (item.mode === "beginning") return "\nStart: the first page · results saved by earlier runs are fetched again.";
      return "\nStart: " + at + "." + (item.repeated ? " Pages are fixed blocks, so the " + item.repeated + " result(s) before your start on that page are fetched again." : "");
    }
    const pageSize = input("zoomeye_pagesize", "", 1, "number");
    pageSize.min = 1;
    pageSize.max = 100;
    pageSize.disabled = true;
    const pageSizeField = field(
      "ZoomEye results per page",
      pageSize,
      "A provider result limit; it can affect credit use.",
    );
    pageSizeField.hidden = true;
    const providerNote = node(
      "div",
      { class: "notice small", hidden: true },
      "Results appear in Observations, with source evidence and endpoint capture controls. Each result keeps a count of how many times it has been captured.",
      link("Open observations →", "/assets/", "btn btn-small mt-small"),
    );
    const submit = node(
      "button",
      { type: "submit", class: "btn btn-primary" },
      "Run discovery →",
    );
    form.append(
      node(
        "section",
        { class: "form-section" },
        node("h3", { class: "form-section-title" }, "Provider"),
        provider,
        providerNote,
      ),
      node(
        "section",
        { class: "form-section" },
        node("h3", { class: "form-section-title" }, "Keyword"),
        field("Keyword or phrase", keyword, "Search across indexed fields. Service filters are optional."),
        node("details", {class:"discover-scopes", "data-disclosure-id":"discover:scope"},
          node("summary", {},"Network, country & exclusions"),
          node("div", {class:"field-grid"},field("Network",network,"IP address or CIDR"),field("Country",country),field("Exclude",exclude))),
      ),
      node(
        "details",
        { class: "form-section", "data-service-filters": "", "data-disclosure-id":"discover:services", open: !!selected },
        node("summary", { class: "form-section-title" }, "Service filters · optional"),
        node("p", { class: "hint" }, "Leave services unselected to search your keyword across the provider’s indexed fields."),
        button("Saved presets", () => window.LeakScopePresets?.openManager?.(), "btn btn-small"),
        field("Discovery strategy", mode),
        modeHint,
        chips,
        node(
          "div",
          { class: "field-grid" },
          field("Find a service", serviceSearch),
          field("Category", categoryFilter),
        ),
        count,
        selection,
        choices,
      ),
      node(
        "section",
        { class: "form-section" },
        node("h3", { class: "form-section-title" }, "Collection"),
        collectionChoice,
        collectionHint,
      ),
      node("details", {class:"form-section discover-scopes", "data-disclosure-id":"discover:limits"},
        node("summary", {},"Request limits"),
        node("div",{class:"field-grid keep-two"},startField,startValueField,field("Pages to fetch",pages,"Keep this small while refining a search.")),pageSizeField),
      errors,
      node(
        "div",
        { class: "form-footer" },
        node(
          "span",
          { class: "hint" },
          "Provider searches may consume account credits.",
        ),
        submit,
      ),
    );
    function payload() {
      const d = formValues(form);
      delete d.service_search;
      delete d.service_category;
      d.types = [
        ...form.querySelectorAll("[name=types]:checked"),
      ].map((x) => x.value);
      d.page = 1;
      if (d.collection_mode === "demo") d.start = "beginning";
      if (d.start === "page" || d.start === "after") d.start_value = Number(d.start_value);
      else delete d.start_value;
      d.page_count = d.collection_mode === "demo" ? 1 : Number(d.page_count);
      if (d.collection_mode === "demo") d.zoomeye_pagesize = 1;
      if (d.zoomeye_pagesize) d.zoomeye_pagesize = Number(d.zoomeye_pagesize);
      return d;
    }
    let previewGeneration = 0;
    let latestSearchGeneration = 0;
    const inlineResults=node("div",{class:"discovery-inline-results"});
    let outputController=null,outputKey="";
    const outputSorting={key:"last_seen",direction:"desc"};
    async function loadSavedOutput(run,generation){
      const result=runResult(run);
      if(!run.id||run.kind!=="lookup"||!U.isTerminal(run.state||run.status)||!result||typeof result.stored!=="number")return;
      const key=String(run.id)+":"+result.stored+":"+outputSorting.key+":"+outputSorting.direction;
      if(outputKey===key){if(!inlineResults.isConnected)latestResults.append(inlineResults);return;}
      outputKey="";outputController?.abort();outputController=new AbortController();
      const signal=outputController.signal;
      latestResults.append(inlineResults);inlineResults.replaceChildren(loading());
      if(result.stored===0){outputKey=key;inlineResults.replaceChildren();return;}
      try{
        const data=await request("/api/operations/endpoints?"+new URLSearchParams({run:run.id,page_size:10,sort:outputSorting.key,direction:outputSorting.direction}),undefined,{signal});
        if(signal.aborted||generation!==latestSearchGeneration)return;
        const endpoints=data.results||[];outputKey=key;
        inlineResults.replaceChildren(node("p",{class:"small muted"},(run.provider||"Provider")+" · "+(run.type?typeLabel(run.type):"Keyword search")+" · "+endpoints.length+" of "+(data.count??endpoints.length)+" saved endpoints · first page of this job. Each job keeps its own complete results link."));
        if(!endpoints.length){inlineResults.append(node("p",{class:"small muted"},"No visible endpoint records are linked to this run."));return;}
        if(window.LeakScopeViz)inlineResults.append(window.LeakScopeViz.endpointInsights({id:"discover-output-insights",rows:endpoints,total:data.count,page:1,catalog:allTypes,onEndpoint:openEndpointInspector,open:innerWidth>800}));
        inlineResults.append(table(["Endpoint","Technology","Last seen",""],endpoints.map(endpoint=>node("tr",{},node("td",{},node("span",{class:"cell-title"},endpoint.title||endpoint.address),node("span",{class:"cell-sub mono"},endpoint.address+":"+endpoint.port)),node("td",{},typeLabel(endpoint.service_type)),node("td",{class:"small muted"},U.date(endpoint.last_seen)),node("td",{},button("Inspect",()=>openEndpointInspector(endpoint),"btn btn-small")))),{columns:[{key:"endpoint"},{key:"technology"},{key:"last_seen",type:"date"},null],state:outputSorting,onSort:()=>loadSavedOutput(run,generation),scope:"Sort all saved endpoints in this job; first 10 displayed"}));
        inlineResults.append(link("All results from this run →","/assets/?run="+encodeURIComponent(run.id),"btn btn-small mt"));
      }catch(error){if(!signal.aborted&&generation===latestSearchGeneration)inlineResults.replaceChildren(errorBox(error));}
    }
    function savedOutput(run){
      const generation=++latestSearchGeneration;
      latestResults.replaceChildren(node("div",{class:"discovery-output-heading"},node("h2",{},"Saved search output"),link("All observations →","/assets/","btn btn-small")),node("div",{class:"panel-body"},runCard(run)));
      loadSavedOutput(run,generation);
    }

    function schedulePreview() {
      const generation = ++previewGeneration;
      clearTimeout(previewTimer);
      const d = payload();
      queryBox.textContent = d.types.length || d.keyword.trim()
        ? "Updating query preview…"
        : "Enter a keyword or select at least one service type.";
      previewTimer = setTimeout(() => preview(generation), 300);
    }
    async function preview(generation = ++previewGeneration) {
      const d = payload();
      if (!d.types.length && !d.keyword.trim()) {
        queryBox.textContent = "Enter a keyword or select at least one service type.";
        return;
      }
      try {
        const r = await request("/api/query-preview", d);
        if (generation !== previewGeneration) return;
        const scope = !d.types.length
          ? "All indexed ports · no port restrictions"
          : {
          fingerprint: "All indexed ports · no port restrictions",
          nonstandard: "Reference ports excluded by your selected strategy",
          ports: "Restricted to reference ports by your selected strategy",
          legacy: "Legacy preset · review the query for port/product filters",
        }[d.query_mode];
        const collection = collectionDescription();
        const demoEstimate = d.collection_mode === "demo" ? "Demo: " + (r.queries || []).length + " selected queries · at most one minimum provider request/page per query. Real provider charges may apply.\n\n" : "";
        queryBox.textContent = demoEstimate + "Collection: " + collection.label + " · " + collection.detail + "\n\n" +
          (r.queries || [])
            .map(
              (x, index) =>
                (x.type === "generic" ? "Keyword search" : typeLabel(x.type)) +
                "\n" +
                scope + "\n" +
                (x.supported !== false && x.reason ? "Matches: " + x.reason + "\n\n" : "") +
                (x.supported === false
                  ? "Unavailable: " +
                    (x.reason || "No verified query for this strategy.")
                  : x.query || "No query available.") +
                (x.basis?.length ? "\nBasis: " + x.basis : "") +
                (x.supported === false || d.collection_mode === "demo" ? "" : startLine(r.start?.[index])),
            )
            .join("\n\n") ||
          r.query ||
          "No provider query is available for this selection.";
      } catch (error) {
        if (generation !== previewGeneration) return;
        queryBox.textContent = "Query preview unavailable: " + error.message;
      }
    }
    function providerControls() {
      const selectedProvider = form.querySelector(
        "[name=provider]:checked",
      ).value;
      const isZoomEye = selectedProvider === "zoomeye";
      const isNetlas = selectedProvider === "netlas";
      pageSizeField.hidden = !isZoomEye;
      pageSize.disabled = !isZoomEye;
      providerNote.hidden = false;
      updateCollectionHint();
      modeHint.textContent = !choices.querySelector("input:checked")
        ? "Service strategies apply when a service is selected. Keyword-only searches add no service, product or port filters."
        : {
        fingerprint:
          "Search response evidence across all indexed ports, including unusual ports. No port filter is added.",
        nonstandard:
          "Optional: exclude the profile’s reference ports. This narrows the search and can return zero even when the all-port query matches.",
        ports:
          "Optional: restrict candidates to the profile’s reference ports. Choose All indexed ports to find services regardless of port.",
        legacy:
          "Older provider query syntax. These presets can include product or port filters; collection still follows the selected mode.",
      }[mode.value];
      for (const checkbox of choices.querySelectorAll("input[name=types]")) {
        const item = available.find(
          (item) => (item.key || item.value) === checkbox.value,
        );
        const providers = item?.discovery_modes
          ? item.discovery_modes[mode.value] || []
          : item?.discovery_providers || ["shodan", "zoomeye"];
        const allowed = providers.includes(selectedProvider);
        checkbox.disabled = !allowed;
        checkbox.closest("label").classList.toggle("is-unavailable", !allowed);
        const query = item?.query_variants?.[mode.value]?.[selectedProvider];
        const alternatives = providers.map((value) => ({shodan: "Shodan", zoomeye: "ZoomEye", netlas: "Netlas"})[value] || value);
        checkbox
          .closest("label")
          .querySelector(".service-availability").textContent = allowed
          ? ""
          : (query?.reason || "No query preset is implemented for this provider and strategy.")
            + (alternatives.length ? " Available presets: " + alternatives.join(", ") + "." : "");
      }
      filterChoices();
    }
    provider.addEventListener("change", providerControls);
    providerControls();
    form.addEventListener("input", () => {
      providerControls();
      schedulePreview();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      errors.replaceChildren();
      const d = payload();
      if (!d.types.length && !d.keyword.trim()) {
        errors.append(errorBox("Enter a keyword or select at least one service type."));
        return;
      }
      if (choices.querySelector("input:checked:disabled")) {
        errors.append(errorBox("Some selected services are unavailable for this provider and strategy. Switch provider or strategy, or remove them from the selection."));
        return;
      }
      perform(
        submit,
        async () => {
          const r = await request("/api/discover", payload());
          const generation = ++latestSearchGeneration;
          const started = U.startedRuns(r);
          const latest = new Map(started.map((run) => [run.id, run]));
          function showLatest(run) {
            if (generation !== latestSearchGeneration) return;
            if (run) latest.set(run.id, run);
            latestResults.replaceChildren(node("div", {class:"discovery-output-heading"},node("h2", {},"Latest search output"),link("All observations →","/assets/","btn btn-small")),
              node("div",{class:"panel-body"},...[...latest.values()].map(runCard)));
            const completed=[...latest.values()].find(item=>item.kind==="lookup"&&U.isTerminal(item.state||item.status));
            if(completed)loadSavedOutput(completed,generation);
          }
          showLatest();
          openRuns(started, showLatest);
          document.getElementById("run-dialog-body").append(link("All observations →", "/assets/", "btn btn-quiet mt"));
        },
        errors,
      );
    });
    const helper = node(
      "div",
      { class: "panel-body" },
      node(
        "ul",
        { class: "helper-list" },
        [
          [
            "Start with a tight scope",
            "Use a network or a specific term, then inspect the exact query before running.",
          ],
          [
            "Provider data is a signal",
            "A stored result does not establish a current exposure. Inspect the source and timestamps.",
          ],
          [
            "Follow the run",
            "Progress and errors stay attached to the run. Refreshing a page does not launch it again.",
          ],
        ].map(([h, p], i) =>
          node(
            "li",
            {},
            node(
              "span",
              { class: "helper-number" },
              String(i + 1).padStart(2, "0"),
            ),
            node("div", {}, node("strong", {}, h), node("p", {}, p)),
          ),
        ),
      ),
    );
    const savedJobs = node("div", {class:"panel-body discovery-recent-runs"},loading());
    latestResults.className="panel";
    latestResults.append(node("div",{class:"discovery-output-heading"},node("h2",{},"Search results"),link("Saved observations →","/assets/","btn btn-small")),
      node("div",{class:"discovery-empty"},node("strong",{},"Your next signal starts here"),node("p",{},"Choose a provider and enter a keyword, or select service profiles. The saved output stays here after the progress window closes.")));
    content(
      heading("Discover", "Search the signal. Keep provider evidence and collection choices in view.", [link("Saved queries","/custom-queries/","btn")]),
      node("div", {class:"discover-workspace"},
        node("div",{class:"discover-composer"},panel("Search setup","Provider collection",node("div",{class:"panel-body"},form))),
        node("div",{class:"discover-output"},latestResults,
          panel("Exact query preview","Composed by the backend · no provider search",node("div",{class:"panel-body"},queryBox)),
          panel("Recent discovery jobs","Existing saved results · opening does not rerun a search",savedJobs),
          panel("A useful starting point",null,helper))),
      node("div",{class:"mt"},directTools()));
    request("/api/runs").then(data=>{
      const runs=(data.results||[]).filter(r=>["lookup","discover","discovery"].includes(r.kind)).slice(0,6);
      savedJobs.replaceChildren(...(runs.length?runs.map(run=>node("div",{class:"discovery-recent-run"},node("div",{},node("strong",{},(run.provider||"Provider")+" · "+(run.type?typeLabel(run.type):"Keyword search")),node("small",{},U.date(run.created_at)+" · "+U.state(run.state||run.status))),button("View output",()=>savedOutput(run),"btn btn-small"))):[node("p",{class:"small muted"},"No saved discovery jobs yet. Your next search will appear here and in Runs.")]));
    }).catch(error=>savedJobs.replaceChildren(errorBox(error)));
    const savedRunId=discoveryParams.get("run");
    const savedRequestGeneration=latestSearchGeneration,savedRunController=new AbortController();
    if(savedRunId)request("/api/runs/"+encodeURIComponent(savedRunId),undefined,{signal:savedRunController.signal})
      .then(run=>{if(!savedRunController.signal.aborted&&latestSearchGeneration===savedRequestGeneration)savedOutput(run);})
      .catch(error=>{if(!savedRunController.signal.aborted&&latestSearchGeneration===savedRequestGeneration)latestResults.append(errorBox(error));});
    addEventListener("pagehide",()=>{outputController?.abort();savedRunController.abort();},{once:true});
    preview();
  }
  function directTools() {
    const form = node("form", { class: "form-stack" }),
      tool = select("tool", [
        ["javascript", "JavaScript file"],
        ["github", "Git repository"],
        ["buckets", "Bucket names"],
      ]),
      target = input("keyword", "https://example.test/app.js"),
      errors = node("div"),
      submit = node("button", { type: "submit", class: "btn" }, "Start tool →");
    form.append(
      node(
        "div",
        { class: "field-grid" },
        field("Tool", tool),
        field("URL or bucket keyword", target),
      ),
      checkbox(
        "Allow this direct operation",
        "active_probe",
        false,
        "These tools fetch a file, clone a repository, or test candidate bucket names.",
      ),
      errors,
      submit,
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const d = formValues(form);
      if (!d.active_probe) {
        errors.replaceChildren(
          errorBox("Enable the direct-operation checkbox to start this tool."),
        );
        return;
      }
      perform(
        submit,
        async () => {
          const r = await request("/api/tools/" + d.tool, {
            keyword: d.keyword,
            active_probe: true,
          });
          openRuns(U.startedRuns(r));
        },
        errors,
      );
    });
    return panel(
      "Direct tools",
      "Existing JavaScript, repository and bucket workflows",
      node("div", { class: "panel-body" }, form),
    );
  }
  async function findings() {
    const all = (await types()).filter((item) => item.legacy !== false),
      params = new URLSearchParams(location.search),
      form = node("form", { class: "toolbar" }),
      body = node("div"),
      count = node("span", { class: "results-count" });
    const intelligence = window.LeakScopeIntelligence;
    const sorting=window.LeakScopeTables?.fromParams(params,["target","type","provider","status","created_at"],"created_at") || {key:"created_at",direction:"desc"};
    let loadVersion=0;
    const selected = new Map();
    const selectionCount = node(
      "span",
      { class: "small muted" },
      "0 selected · up to 10",
    );
    const similarAction = button("Find similar", () =>
      intelligence.openSimilar([...selected.values()]),
    );
    const askAction = button("Ask", () =>
      intelligence.openAsk([...selected.values()]),
    );
    const clearSelection = button(
      "Clear selection",
      () => {
        selected.clear();
        body.querySelectorAll("input[data-finding-select]").forEach((box) => {
          box.checked = false;
        });
        updateSelection();
      },
      "btn btn-small btn-quiet",
    );
    function updateSelection() {
      selectionCount.textContent = selected.size + " selected · up to 10";
      similarAction.disabled =
        askAction.disabled =
        clearSelection.disabled =
          selected.size === 0;
    }
    updateSelection();
    const type = select(
        "type",
        [
          ["", "All service types"],
          ...all.map((x) => [x.key || x.value, x.label]),
        ],
        params.get("type") || "",
      ),
      provider = select(
        "provider",
        [
          ["", "All providers"],
          ["shodan", "Shodan"],
          ["zoomeye", "ZoomEye"],
          ["unknown", "Unknown"],
        ],
        params.get("provider") || "",
      ),
      status = select(
        "status",
        [
          ["", "Visible findings"],
          ["new", "New"],
          ["confirmed", "Confirmed"],
          ["for_later", "For review"],
          ["hidden", "Hidden"],
        ],
        params.get("status") || "",
      ),
      search = input(
        "q",
        "Search targets, titles, and evidence",
        params.get("q") || "",
      );
    form.append(
      field("Search", search),
      field("Service", type),
      field("Provider", provider),
      field("Review state", status),
      node("button", { type: "submit", class: "btn" }, "Apply filters"),
    );
    let current = Number(params.get("page") || 1);
    async function load() {
      const version=++loadVersion;
      selected.clear();
      updateSelection();
      body.replaceChildren(loading());
      try {
        const q = new URLSearchParams(new FormData(form));
        q.set("page", current);
        q.set("sort",sorting.key);q.set("direction",sorting.direction);
        const data = await request("/api/findings?" + q);
        if(version!==loadVersion)return;
        { const all=new URLSearchParams(q);all.delete("page");all.delete("page_size"); body.dataset.exportUrl = "/export/table/findings/?" + all; }
        count.textContent = formatCount(data.count) + " findings";
        history.replaceState(null, "", "?" + q);
        if (!(data.results || []).length) {
          body.replaceChildren(
            empty(
              "No legacy findings match",
              "New discovery results, source history and captures are saved in Observations. Change a filter to check older service-specific records.",
              link("Open observations →", "/assets/", "btn btn-primary"),
            ),
          );
          return;
        }
        const rows = data.results.map((f) =>
          node(
            "tr",
            {},
            intelligence
              ? node(
                  "td",
                  { class: "intel-select-cell" },
                  node("input", {
                    type: "checkbox",
                    "data-finding-select": f.type + ":" + f.id,
                    "aria-label": "Select " + (f.label || f.type + " " + f.id),
                    onchange: (event) => {
                      const key = f.type + ":" + f.id;
                      if (event.target.checked && selected.size >= 10) {
                        event.target.checked = false;
                        toast(
                          "Select up to 10 findings for a focused query or question.",
                          true,
                        );
                      } else if (event.target.checked)
                        selected.set(key, { type: f.type, id: f.id });
                      else selected.delete(key);
                      updateSelection();
                    },
                  }),
                )
              : null,
            node(
              "td",
              {},
              link(
                f.label || [f.ip, f.port].filter(Boolean).join(":"),
                "/findings/" +
                  encodeURIComponent(f.type) +
                  "/" +
                  encodeURIComponent(f.id) +
                  "/",
                "cell-title mono",
              ),
              node(
                "span",
                { class: "cell-sub" },
                ["keys", "github", "javascript"].includes(f.type)
                  ? "Sensitive evidence · open to review"
                  : String(f.summary || "").slice(0, 110),
              ),
            ),
            node("td", {}, typeLabel(f.type)),
            node(
              "td",
              {},
              node(
                "span",
                { class: "provider-badge" },
                f.provider || "unknown",
              ),
            ),
            node("td", {}, badge(f.status || "new")),
            node("td", { class: "small muted" }, U.date(f.created_at)),
            node(
              "td",
              {},
              window.LeakScopeInspector ? button("Inspect →",()=>window.LeakScopeInspector.open({type:f.type,id:f.id}),"btn btn-small btn-quiet") : link(
                "Inspect →",
                "/findings/" +
                  encodeURIComponent(f.type) +
                  "/" +
                  encodeURIComponent(f.id) +
                  "/",
                "btn btn-small btn-quiet",
              ),
              button("Add to organization", () => window.LeakScopeAssets?.addToOrganization?.({type:f.type,id:f.id}), "btn btn-small"),
              button("Create discovery preset", () => window.LeakScopePresets?.openCreate?.([{type:f.type,id:f.id}]), "btn btn-small"),
            ),
          ),
        );
        const pages = Number(data.pages || 1),
          previous = button(
            "← Previous",
            () => {
              current--;
              load();
            },
            "btn btn-small",
          ),
          next = button(
            "Next →",
            () => {
              current++;
              load();
            },
            "btn btn-small",
          );
        previous.disabled = current <= 1;
        next.disabled = current >= pages;
        body.replaceChildren(
          table(
            [
              ...(intelligence ? ["Select"] : []),
              "Target / evidence",
              "Service",
              "Source",
              "Review state",
              "Observed",
              "",
            ],
            rows,
            {columns:[...(intelligence?[null]:[]),{key:"target"},{key:"type"},{key:"provider"},{key:"status"},{key:"created_at",type:"date"},null],state:sorting,onSort:()=>{current=1;load();},scope:"Sort all matching saved findings"},
          ),
          node(
            "div",
            { class: "pager" },
            node("span", {}, "Page " + current + " of " + pages),
            node("div", { class: "pager-actions" }, previous, next),
          ),
        );
      } catch (error) {
        if(version!==loadVersion)return;
        body.replaceChildren(errorBox(error));
      }
    }
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      current = 1;
      load();
    });
    content(
      heading(
        "Findings",
        "Review older service-specific findings. New discovery results are saved in Observations.",
        [link("New discovery +", "/discover/", "btn btn-primary")],
      ),
      form,
      intelligence
        ? node(
            "div",
            { class: "intel-bulk toolbar" },
            selectionCount,
            similarAction,
            askAction,
            clearSelection,
          )
        : null,
      panel("Legacy findings", "Older service-specific records; new discovery results are saved in Observations.", body, [count]),
    );
    load();
  }
  async function detail() {
    const type = initialType,
      id = initialTarget;
    if (!type || !id)
      throw new Error("Choose a finding from the Findings page.");
    const data = await request(
        "/api/findings/" +
          encodeURIComponent(type) +
          "/" +
          encodeURIComponent(id),
      ),
      f = data.finding || data,
      fields = data.fields || {},
      caps = data.capabilities || {},
      actions = node("div", { class: "row-actions" }),
      errors = node("div");
    async function review(status) {
      try {
        await request(
          "/api/findings/" +
            encodeURIComponent(type) +
            "/" +
            encodeURIComponent(id) +
            "/review",
          { status },
        );
        toast("Review state updated.");
        await detail();
      } catch (error) {
        errors.replaceChildren(errorBox(error));
      }
    }
    const reviewSelect = select(
      "status",
      [
        ["new", "New"],
        ["confirmed", "Confirmed"],
        ["for_later", "For review"],
        ["hidden", "Hidden"],
      ],
      f.status || "new",
    );
    reviewSelect.addEventListener("change", () => review(reviewSelect.value));
    if (caps.preview !== false)
      actions.append(
        link(
          "Preview target →",
          "/preview/?source=legacy&type=" +
            encodeURIComponent(type) +
            "&target_id=" +
            encodeURIComponent(id),
          "btn btn-primary",
        ),
      );
    if (caps.export !== false)
      actions.append(
        link(
          "Explore / export",
          "/explore/?type=" +
            encodeURIComponent(type) +
            "&target_id=" +
            encodeURIComponent(id),
          "btn",
        ),
      );
    if (window.LeakScopeAssets) actions.append(button("Names, DNS & netblock", () => window.LeakScopeAssets.openTools({type,id})));
    if (window.LeakScopeAssets) actions.append(button("Add to organization", () => window.LeakScopeAssets.addToOrganization({type,id})));
    if (window.LeakScopeExpansion) actions.append(button("Expand domains", () => window.LeakScopeExpansion.open({reference:{type,id}})));
    actions.append(button("Create discovery preset", () => window.LeakScopePresets?.openCreate?.([{type,id}])));
    if (window.LeakScopeIntelligence) {
      const reference = { type, id };
      actions.append(
        button("Find similar", () =>
          window.LeakScopeIntelligence.openSimilar([reference]),
        ),
        button("Ask", () => window.LeakScopeIntelligence.openAsk([reference])),
        link(
          "Research technology",
          "/research/?topic=" + encodeURIComponent(typeLabel(type)),
          "btn",
        ),
      );
    }
    const tagButton = tagsAction("finding", type + ":" + id, f.tags);
    if (tagButton) actions.append(tagButton);
    const metadata = {
      service: typeLabel(type),
      provider: f.provider || "unknown",
      target: f.ip || f.label,
      port: f.port,
      observed: f.created_at,
      search_id: f.search_id,
    };
    const summary = node(
      "div",
      { class: "panel-body" },
      renderFields(metadata),
      node("div", { class: "mt" }, field("Review state", reviewSelect)),
      errors,
    );
    const values = node(
      "div",
      { class: "panel-body" },
      renderFields(fields, async (name) => {
        const revealed = await request(
          "/api/findings/" +
            encodeURIComponent(type) +
            "/" +
            encodeURIComponent(id) +
            "?reveal=1",
        );
        return revealed.fields?.[name];
      }),
    );
    content(
      heading(
        f.label || [f.ip, f.port].filter(Boolean).join(":"),
        "Stored evidence with its original service and collection source.",
        [link("← Findings", "/findings/", "btn")],
      ),
      node(
        "div",
        { class: "toolbar" },
        badge(f.status || "new"),
        ...actions.children,
      ),
      node(
        "div",
        { class: "grid-two" },
        panel(
          "Evidence",
          "Collected values; sensitive fields are masked by default",
          values,
        ),
        panel(
          "Provenance & review",
          "Review decisions are separate from exposure observations",
          summary,
        ),
      ),
      data.search
        ? node(
            "div",
            { class: "mt" },
            panel(
              "Originating search",
              null,
              node("div", { class: "panel-body" }, renderFields(data.search)),
            ),
          )
        : null,
    );
  }
  async function runs() {
    const requestedRun = new URLSearchParams(location.search).get("id");
    let openedRequestedRun = false;
    const body = node("div"),
      refresh = button("Refresh", load, "btn");
    async function load() {
      body.replaceChildren(loading());
      try {
        const d = await request("/api/runs");
        body.dataset.exportUrl = "/export/runs/";
        body.replaceChildren(...(window.LeakScopeScope ? [window.LeakScopeScope.timeline(d.results || [])] : []), runsTable(d.results || []));
        if (requestedRun && !openedRequestedRun) {
          const run = (d.results || []).find((item) => item.id === requestedRun)
            || await request("/api/runs/" + encodeURIComponent(requestedRun));
          openedRequestedRun = true;
          openRuns([run]);
        }
      } catch (error) {
        body.replaceChildren(errorBox(error));
      }
    }
    content(
      heading(
        "Runs",
        "Inspect progress, results, and errors without repeating an operation.",
        [refresh],
      ),
      panel("Job history", "Provider collection and direct operations", body),
    );
    load();
  }
  async function queries() {
    const requested = new URLSearchParams(location.search);
    let openedRequested = false;
    const all = (await types()).filter((item) => item.legacy !== false),
      list = node("div"),
      form = node("form", { class: "form-stack" }),
      errors = node("div"),
      name = input("name", "A useful name"),
      description = node("textarea", {
        name: "description",
        placeholder: "What does this query find?",
        rows: 2,
      }),
      template = node("textarea", {
        name: "query_template",
        placeholder:
          "Provider query syntax with {keyword}, {country}, {network}, {exclude}",
        rows: 5,
        class: "mono",
      }),
      provider = select("provider", [
        ["shodan", "Shodan"],
        ["zoomeye", "ZoomEye"],
        ["netlas", "Netlas"],
      ]),
      type = select("default_type", [
        ["", "Generic provider match"],
        ...all
          .filter((x) => x.discovery !== false)
          .map((x) => [x.key || x.value, x.label]),
      ]),
      active = checkbox(
        "Active inspection by default",
        "active_probe_default",
        false,
      ),
      submit = node(
        "button",
        { type: "submit", class: "btn btn-primary" },
        "Save query",
      );
    let editing = null;
    form.append(
      field("Name", name),
      field("Description", description),
      node(
        "div",
        { class: "field-grid" },
        field("Provider", provider),
        field("Result type", type),
      ),
      field(
        "Query template",
        template,
        "Supported parameters: {keyword}, {country}, {network}, {exclude}.",
      ),
      active,
      errors,
      node(
        "div",
        { class: "row-actions" },
        submit,
        button(
          "Clear",
          () => {
            editing = null;
            form.reset();
            submit.textContent = "Save query";
          },
          "btn btn-quiet",
        ),
      ),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      perform(
        submit,
        async () => {
          const d = formValues(form);
          if (editing) d.id = editing;
          await request("/api/queries", d);
          toast("Query saved.");
          editing = null;
          form.reset();
          submit.textContent = "Save query";
          await load();
        },
        errors,
      );
    });
    async function load() {
      list.replaceChildren(loading());
      try {
        const d = await request("/api/queries");
        if (!d.results?.length) {
          list.replaceChildren(
            empty(
              "No custom queries",
              "Save a provider query here to reuse its syntax and defaults.",
            ),
          );
          return;
        }
        list.replaceChildren(
          node(
            "div",
            { class: "panel-body query-list" },
            d.results.map((q) =>
              node(
                "article",
                { class: "query-card" },
                node("h3", {}, q.name),
                node("p", {}, q.description || "No description"),
                node("pre", { class: "code-box", text: q.query_template }),
                node(
                  "div",
                  { class: "query-card-footer" },
                  node("span", { class: "provider-badge" }, q.provider),
                  tagsAction("query", q.id, q.tags),
                  button(
                    "Edit",
                    () => {
                      editing = q.id;
                      name.value = q.name;
                      description.value = q.description || "";
                      template.value = q.query_template;
                      provider.value = q.provider;
                      type.value = q.default_type || "";
                      form.querySelector(
                        "[name=active_probe_default]",
                      ).checked = !!q.active_probe_default;
                      submit.textContent = "Update query";
                      name.focus();
                    },
                    "btn btn-small",
                  ),
                  button(
                    "Run",
                    () => queryRunDialog(q),
                    "btn btn-primary btn-small",
                  ),
                  button(
                    "Delete",
                    async () => {
                      if (!confirm("Delete the saved query “" + q.name + "”?"))
                        return;
                      try {
                        await request("/api/queries/" + q.id + "/delete", {});
                        await load();
                      } catch (error) {
                        toast(error.message, true);
                      }
                    },
                    "btn btn-small btn-quiet btn-danger",
                  ),
                ),
              ),
            ),
          ),
        );
        if (!openedRequested && requested.get("id")) {
          const saved = d.results.find((row) => String(row.id) === requested.get("id"));
          if (saved) {
            openedRequested = true;
            queryRunDialog(saved, { demo: requested.get("demo") === "1" });
          }
        }
      } catch (error) {
        list.replaceChildren(errorBox(error));
      }
    }
    content(
      heading(
        "Custom queries",
        "Keep provider-specific query syntax and reuse the parameters you need.",
      ),
      node(
        "div",
        { class: "grid-equal" },
        panel("Saved queries", "Reusable collection recipes", list),
        panel(
          "Query editor",
          "Write and review the exact provider syntax",
          node("div", { class: "panel-body" }, form),
        ),
      ),
    );
    load();
  }
  function queryRunDialog(query, options = {}) {
    const dialog = document.getElementById("run-dialog"),
      body = document.getElementById("run-dialog-body");
    document.getElementById("run-dialog-title").textContent = "Run saved query";
    const form = node("form", { class: "form-stack" }),
      errors = node("div");
    const start = input("page", "", 1, "number"),
      pages = input("page_count", "", 1, "number");
    start.min = pages.min = 1;
    pages.max = 20;
    const demo = checkbox("Demo · minimal API use", "demo", options.demo === true, "Real provider requests: one minimum page and one saved result; no automatic inspection. Normal settings are retained.");
    const submit = node(
      "button",
      { type: "submit", class: "btn btn-primary" },
      "Run query →",
    );
    form.append(
      node("p", { class: "hint" }, query.name + " · " + query.provider),
      node("pre", { class: "code-box", text: query.query_template }),
      node(
        "div",
        { class: "field-grid" },
        field("Keyword", input("keyword", "Optional phrase")),
        field("Network", input("network", "IP or CIDR")),
        field("Country", input("country", "Country code")),
        field("Exclude", input("exclude", "Comma-separated terms")),
      ),
      node(
        "div",
        { class: "field-grid keep-two" },
        field("Start page", start),
        field("Pages to fetch", pages),
      ),
      checkbox(
        "Allow active inspection",
        "active_probe",
        !!query.active_probe_default,
        "Use the saved default only when the intended target scope is correct.",
      ),
      demo,
      errors,
      submit,
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      perform(
        submit,
        async () => {
          const values = formValues(form);
          if (values.demo) {values.page = 1; values.page_count = 1; values.active_probe = false;} else delete values.demo;
          const response = await request(
            "/api/queries/" + query.id + "/run",
            values,
          );
          openRuns(U.startedRuns(response));
          if (response.results_url === "/assets/")
            document
              .getElementById("run-dialog-body")
              .append(
                link("Open observations →", "/assets/", "btn btn-primary mt"),
              );
        },
        errors,
      );
    });
    const demoBox = demo.querySelector("input");
    demoBox.addEventListener("change", () => {pages.disabled = demoBox.checked; start.disabled = demoBox.checked || query.provider === "netlas"; const probe = form.querySelector("input[name=active_probe]"); if (query.provider !== "netlas") probe.disabled = demoBox.checked;});
    body.replaceChildren(form);
    if (options.demo === true) {
      form.querySelector("input[name=active_probe]").checked = false;
      demoBox.dispatchEvent(new Event("change"));
    }
    if (query.provider === "netlas") {
      start.disabled = true;
      start.value = 1;
      const inspection = form.querySelector("input[name=active_probe]");
      inspection.checked = false;
      inspection.disabled = true;
      inspection.closest("label").hidden = true;
      form.prepend(
        node(
          "div",
          { class: "notice" },
          "Netlas starts at the first page and stores results in Observations. Fetch targets from there after collection.",
        ),
      );
    }
    if (!dialog.open) dialog.showModal();
  }
  async function inspection(mode) {
    const all = (await types()).filter((item) => item.legacy !== false),
      filtered = all.filter((t) =>
        mode === "preview" ? t.preview !== false : true,
      ),
      form = node("form", { class: "form-stack" }),
      type = select(
        "type",
        [
          ["", "Choose a service"],
          ...filtered.map((x) => [x.key || x.value, x.label]),
        ],
        initialType,
      ),
      target = select("target_id", [["", "Choose a stored target"]]),
      errors = node("div"),
      results = node("div"),
      selection = node("textarea", {
        name: "selection",
        rows: 3,
        placeholder: '{"index":"my-index"}',
        class: "mono",
      }),
      active = checkbox(
        "Allow direct target requests",
        "active_probe",
        false,
        "This operation can connect to the selected service. Select a stored target and enable it deliberately.",
      ),
      submit = node(
        "button",
        { type: "submit", class: "btn btn-primary" },
        mode === "preview" ? "Run preview →" : "Explore target →",
      );
    let targets = [],
      captured = null;
    async function loadTargets() {
      target.replaceChildren(node("option", { value: "" }, "Loading targets…"));
      try {
        const response = await request(
          "/api/findings?" +
            new URLSearchParams({ type: type.value, page: 1, page_size: 200 }),
        );
        targets = response.results || [];
        if (
          initialTarget &&
          type.value === initialType &&
          !targets.some((t) => String(t.id) === String(initialTarget))
        ) {
          const detail = await request(
            "/api/findings/" +
              encodeURIComponent(initialType) +
              "/" +
              encodeURIComponent(initialTarget),
          );
          targets.push(detail.finding || detail);
        }
        target.replaceChildren(
          node("option", { value: "" }, "Choose a stored target"),
          ...targets.map((t) =>
            node(
              "option",
              {
                value: t.id,
                selected:
                  type.value === initialType &&
                  String(t.id) === String(initialTarget),
              },
              t.label || [t.ip, t.port].filter(Boolean).join(":") || "#" + t.id,
            ),
          ),
        );
      } catch (error) {
        errors.replaceChildren(errorBox(error));
        target.replaceChildren(
          node("option", { value: "" }, "Unable to load targets"),
        );
      }
    }
    type.addEventListener("change", () => {
      results.replaceChildren();
      loadTargets();
    });
    form.append(...[
      node(
        "div",
        { class: "tool-targets" },
        field("Service type", type),
        field("Stored target", target),
      ),
      mode === "explore"
        ? field(
            "Selection (JSON)",
            selection,
            "Optional: index, db, collection, repo, bucket, keyspace, table or filter.",
          )
        : null,
      active,
      errors,
      submit,
    ].filter(child=>child!=null));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      errors.replaceChildren();
      const values = formValues(form);
      if (!values.type || !values.target_id) {
        errors.append(errorBox("Choose a service and a stored target."));
        return;
      }
      if (!values.active_probe) {
        errors.append(
          errorBox("Enable direct requests to inspect this target."),
        );
        return;
      }
      const context = {
        type: values.type,
        target_id: values.target_id,
        active_probe: true,
      };
      captured = context;
      perform(
        submit,
        async () => {
          if (mode === "preview") {
            const response = await request("/api/preview", context);
            openRuns(U.startedRuns(response), (run) => {
              results.replaceChildren(runCard(run));
              if (U.state(run.state || run.status) === "succeeded")
                results.append(exportForm(context));
            });
            return;
          }
          let selection = {};
          if (values.selection) {
            try {
              selection = JSON.parse(values.selection);
            } catch (_) {
              throw new Error("Selection must be a JSON object.");
            }
          }
          if (
            !selection ||
            Array.isArray(selection) ||
            typeof selection !== "object"
          )
            throw new Error("Selection must be a JSON object.");
          const response = await request("/api/explore", {
            ...context,
            selection,
          });
          const resultBody = node(
            "div",
            { class: "tool-result" },
            response.export_options
              ? renderFields(response.export_options)
              : null,
            prettyData(safeResult(response.items || []), "Inspection result"),
          );
          results.replaceChildren(
            resultBody,
            exportForm({ ...context, selection }),
          );
        },
        errors,
      );
    });
    content(
      heading(
        mode === "preview" ? "Preview" : "Explore",
        mode === "preview"
          ? "Inspect a stored endpoint and keep the target fixed while the job runs."
          : "Browse the available selections on a stored target, then export deliberately.",
      ),
      node(
        "div",
        { class: "grid-two" },
        panel(
          "Select a target",
          "Direct inspection is always explicit",
          node("div", { class: "panel-body" }, form),
        ),
        panel(
          "Inspection results",
          "Returned evidence is displayed as text",
          node("div", { class: "panel-body" }, results),
        ),
      ),
    );
    if (initialType) loadTargets();
    else
      results.append(
        empty(
          "Choose a target",
          "Select a stored finding to begin a preview or exploration.",
        ),
      );
  }
  function exportForm(context) {
    const form = node("form", { class: "form-stack mt" }),
      max = input("max_items", "", 1000, "number"),
      fmt = select("format", [
        ["json", "JSON"],
        ["csv", "CSV"],
      ]),
      selection = node("textarea", {
        name: "selection",
        rows: 3,
        class: "mono",
        value: "",
      }),
      errors = node("div"),
      submit = node(
        "button",
        { type: "submit", class: "btn" },
        "Export selected target ↓",
      );
    selection.value = JSON.stringify(context.selection || {}, null, 2);
    max.min = 1;
    max.max = 5000;
    form.append(
      node("div", { class: "separator" }),
      node("h3", { class: "form-section-title" }, "Export this target"),
      node(
        "p",
        { class: "hint" },
        typeLabel(context.type) + " · target #" + context.target_id,
      ),
      field("Selection (JSON)", selection),
      node(
        "div",
        { class: "field-grid keep-two" },
        field("Maximum items", max),
        field("Format", fmt),
      ),
      errors,
      submit,
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      perform(
        submit,
        async () => {
          const d = formValues(form);
          let sel;
          try {
            sel = JSON.parse(d.selection || "{}");
          } catch (_) {
            throw new Error("Selection must be valid JSON.");
          }
          const r = await request("/api/export", {
            ...context,
            selection: sel,
            max_items: Number(d.max_items),
            format: d.format,
            active_probe: true,
          });
          openRuns(U.startedRuns(r));
        },
        errors,
      );
    });
    return form;
  }
  async function exports() {
    const all = (await types()).filter((item) => item.legacy !== false),
      form = node("form", { class: "form-stack" }),
      type = select(
        "type",
        all.map((x) => [x.key || x.value, x.label]),
        initialType,
      ),
      fmt = select("format", [
        ["csv", "CSV"],
        ["json", "JSON"],
      ]),
      download = node(
        "a",
        { class: "btn btn-primary" },
        "Download older findings ↓",
      );
    function update() {
      download.href =
        "/export/?" +
        new URLSearchParams({ type: type.value, format: fmt.value });
    }
    type.addEventListener("change", update);
    fmt.addEventListener("change", update);
    form.append(
      field("Service type", type),
      field("Format", fmt),
      node(
        "div",
        { class: "notice" },
        "These are the per-service records the earlier search pages stored. Results saved by current lookups are in the saved results download. It does not probe targets.",
      ),
      download,
    );
    update();
    // What current lookups, captures and workflows save. The older download above only holds per-service findings.
    let technologies = [];
    try {
      technologies = ((await request("/api/build/blocks")).options || {}).technologies || [];
    } catch (error) {
      technologies = [];
    }
    const savedForm = node("form", { class: "form-stack" }),
      savedType = select("technology", [["", "All technologies"], ...technologies.map((name) => [name, name])], ""),
      savedFormat = select("format", [
        ["csv", "CSV"],
        ["json", "JSON"],
      ]),
      savedDownload = node("a", { class: "btn btn-primary" }, "Download saved results ↓");
    function updateSaved() {
      const query = new URLSearchParams({ format: savedFormat.value });
      if (savedType.value) query.set("technology", savedType.value);
      savedDownload.href = "/export/results/?" + query;
    }
    savedType.addEventListener("change", updateSaved);
    savedFormat.addEventListener("change", updateSaved);
    savedForm.append(
      field("Technology", savedType),
      field("Format", savedFormat),
      node("div", { class: "notice" }, "The newest 5,000 saved results with their address, port, technology, names, sources and tags. It does not probe targets."),
      savedDownload,
    );
    updateSaved();
    const importForm = node("form", { class: "form-stack" }),
      entries = node("textarea", {
        name: "entries",
        rows: 7,
        placeholder: "One IP address or hostname per line",
      }),
      errors = node("div"),
      submit = node(
        "button",
        { type: "submit", class: "btn" },
        "Import blacklist",
      );
    importForm.append(
      field(
        "Blacklist entries",
        entries,
        "Imported values are included in discovery blacklist checks.",
      ),
      errors,
      submit,
    );
    importForm.addEventListener("submit", (e) => {
      e.preventDefault();
      perform(
        submit,
        async () => {
          const list = entries.value
            .split("\n")
            .map((x) => x.trim())
            .filter(Boolean);
          if (!list.length)
            throw new Error("Add at least one blacklist entry.");
          const r = await request("/api/blacklist", { entries: list });
          toast(
            "Blacklist updated" +
              (r.created != null ? " · " + r.created + " new entries" : ""),
          );
          entries.value = "";
        },
        errors,
      );
    });
    content(
      heading(
        "Export & import",
        "Move stored findings out of the workbench and maintain your exclusions.",
      ),
      node(
        "div",
        { class: "grid-equal" },
        panel(
          "Export saved results",
          "What lookups, captures and workflows saved",
          node("div", { class: "panel-body" }, savedForm),
        ),
        panel(
          "Export older findings",
          "Per-service records from the earlier search pages",
          node("div", { class: "panel-body" }, form),
        ),
        panel(
          "Import blacklist",
          "Keep excluded hosts out of collection",
          node("div", { class: "panel-body" }, importForm),
        ),
      ),
    );
  }
  async function settings() {
    const data = await request("/api/settings"),
      providerBody = node("div", { class: "panel-body" }),
      feedback = node("div");
    let balance = null;
    function providerRows() {
      providerBody.replaceChildren(
        ...["shodan", "zoomeye", "netlas"].map((key) => {
          const cfg = data.providers?.[key] || {},
            check = balance?.[key];
          return node(
            "div",
            { class: "provider-row" },
            node(
              "span",
              { class: "provider-icon" },
              { shodan: "SH", zoomeye: "ZE", netlas: "NL" }[key] || key,
            ),
            node(
              "div",
              {},
              node(
                "strong",
                {},
                { shodan: "Shodan", zoomeye: "ZoomEye", netlas: "Netlas" }[
                  key
                ] || key,
              ),
              node(
                "small",
                {},
                check
                  ? check.ok
                    ? "Account check passed"
                    : check.error || "Account check failed"
                  : cfg.configured
                    ? "Private key configured"
                    : "Key not configured",
              ),
            ),
            check?.credits != null
              ? node(
                  "div",
                  { class: "provider-count" },
                  formatCount(check.credits),
                  node(
                    "small",
                    {},
                    key === "shodan" ? " query credits" : " points",
                  ),
                )
              : badge(cfg.configured ? "configured" : "missing"),
          );
        }),
        node(
          "p",
          { class: "small muted mt" },
          "Keys are loaded privately by the server and are never sent to this page.",
        ),
        checkButton,
        feedback,
      );
    }
    const checkButton = button(
      "Check provider accounts",
      () =>
        perform(
          checkButton,
          async () => {
            const r = await request("/api/providers/check", {});
            balance = r.providers || {};
            providerRows();
            toast("Account checks completed.");
          },
          feedback,
        ),
      "btn",
    );
    providerRows();
    const health = node(
      "div",
      { class: "panel-body health-list" },
      Object.entries(data.health || {}).map(([key, value]) =>
        node(
          "div",
          { class: "health-row" },
          txt(key.charAt(0).toUpperCase() + key.slice(1)),
          typeof value === "boolean"
            ? badge(value ? "healthy" : "unavailable")
            : node("span", { class: "health-value" }, U.text(value)),
        ),
      ),
    );
    const versions = node(
      "div",
      { class: "panel-body health-list" },
      Object.entries(data.version || data.runtime || {}).map(([key, value]) =>
        node(
          "div",
          { class: "health-row" },
          txt(key),
          node("span", { class: "health-value" }, U.text(value)),
        ),
      ),
    );
    const blacklist = Array.isArray(data.blacklist) ? data.blacklist : [],
      storage = node(
        "div",
        { class: "panel-body" },
        renderFields(data.storage || {}),
      );
    // Database: one file out, one file in. Import replaces everything and runs as a background job.
    const dbState = node("div", { class: "database-state", role: "status", "aria-live": "polite" }),
      dbFile = node("input", { type: "file", name: "file", accept: ".gz,application/gzip" }),
      dbWord = node("input", { type: "text", name: "confirm", autocomplete: "off", placeholder: "REPLACE", "aria-label": "Type REPLACE to confirm" }),
      dbFeedback = node("div");
    let dbTimer = null;
    function paintDatabase(view) {
      const state = view.import || {}, lines = [];
      if (state.state === "queued") lines.push(node("div", { class: "notice" }, "Import of " + U.text(state.file || "the file") + " is waiting to start."));
      else if (state.state === "running") lines.push(node("div", { class: "notice" }, node("span", { class: "spinner" }), " " + U.text(state.step || "Importing") + "…"));
      else if (state.state === "done") lines.push(node("div", { class: "notice notice-success" }, "Imported " + formatCount(state.rows || 0) + " rows from " + U.text(state.file || "the file") + " · " + U.date(state.updated) + ". The database from before is kept on the server as " + U.text(state.safety_copy || "a safety copy") + "."));
      else if (state.state === "failed") lines.push(node("div", { class: "notice notice-error" }, U.text(state.error || "The import failed. Nothing was changed.")));
      if (view.busy_jobs) lines.push(node("p", { class: "small muted" }, formatCount(view.busy_jobs) + (view.busy_jobs === 1 ? " job is" : " jobs are") + " queued or running. Import is available when none are."));
      dbState.replaceChildren(...lines);
      importButton.disabled = !!view.importing || !!view.busy_jobs;
      clearTimeout(dbTimer);
      if (view.importing && dbState.isConnected) dbTimer = setTimeout(refreshDatabase, 3000);
    }
    async function refreshDatabase() {
      try { paintDatabase(await request("/api/settings/database")); } catch (_) { if (dbState.isConnected) dbTimer = setTimeout(refreshDatabase, 5000); }
    }
    const importButton = button("Import database", () => perform(importButton, async () => {
      dbFeedback.replaceChildren();
      if (!dbFile.files?.[0]) throw new Error("Choose a LeakScope database file first.");
      if (dbWord.value.trim() !== "REPLACE") throw new Error("Type REPLACE to confirm. Importing replaces everything saved here.");
      const body = new FormData();
      body.append("file", dbFile.files[0]); body.append("confirm", "REPLACE");
      const response = await fetch("/api/settings/database/import", { method: "POST", credentials: "same-origin", headers: { Accept: "application/json", "X-CSRFToken": csrf() }, body });
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(U.text(payload.error || "The import could not start (" + response.status + ")."));
      dbFile.value = ""; dbWord.value = "";
      toast("Import started.");
      paintDatabase(payload);
    }, dbFeedback), "btn btn-danger");
    const database = node("div", { class: "panel-body database-transfer" },
      node("div", { class: "database-transfer-part" },
        node("a", { class: "btn", href: "/export/database/", download: "" }, "Export database ↓"),
        node("p", { class: "small muted" }, "One file with everything saved here. Captured files such as screenshots are stored separately and are not included.")),
      node("div", { class: "database-transfer-part" },
        field("Database file", dbFile),
        field("Type REPLACE to confirm", dbWord, "Importing replaces everything saved here with the file's contents. A copy of the current database is saved on the server first."),
        importButton, dbFeedback),
      dbState);
    refreshDatabase();
    content(
      heading(
        "Settings",
        "Provider configuration, local runtime, and storage health.",
        window.LeakScopeIntelligence
          ? [
              button("Configure integrations", () =>
                window.LeakScopeIntelligence.openSettings(() => settings()),
              ),
            ]
          : [],
      ),
      node(
        "div",
        { class: "grid-equal" },
        node(
          "div",
          { class: "stack" },
          panel(
            "Provider accounts",
            "Read-only checks; no discovery search is run",
            providerBody,
          ),
          panel(
            "Runtime versions",
            "Versions reported by this deployment",
            versions,
          ),
        ),
        node(
          "div",
          { class: "stack" },
          panel("System health", "Local service status", health),
          panel("Storage", "Current data and artifact configuration", storage),
          panel("Database", "Download everything, or replace it from a download", database),
          window.LeakScopeRunLogs ? panel("Display preferences", "Saved in this browser; collection behavior is unchanged",node("div",{class:"panel-body"},window.LeakScopeRunLogs.preference())) : null,
        ),
      ),
      node(
        "div",
        { class: "mt" },
        panel(
          "Blacklist",
          formatCount(blacklist.length) + " configured entries",
          node(
            "div",
            { class: "panel-body" },
            blacklist.length
              ? node("pre", { class: "code-box", text: blacklist.join("\n") })
              : node(
                  "p",
                  { class: "small muted" },
                  "No exclusions have been configured.",
                ),
            link(
              "Import entries →",
              "/export-import/",
              "btn btn-small mt-small",
            ),
          ),
        ),
      ),
    );
  }
  document.getElementById("breadcrumb-page").textContent =
    pageNames[page] || "Workbench";
  document.title = "LeakScope · " + (pageNames[page] || "Workbench");
  for (const a of document.querySelectorAll("[data-nav]")) {
    if (a.dataset.nav === (window.LeakScopeShell?.navKey ? window.LeakScopeShell.navKey(page) : page === "detail" ? "findings" : page)) {
      a.classList.add("active");
      a.setAttribute("aria-current", "page");
    }
  }
  document.getElementById("menu-toggle").addEventListener("click", () => {
    const menu = document.getElementById("sidebar"),
      open = menu.classList.toggle("is-open");
    document
      .getElementById("menu-toggle")
      .setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (event) => {
    if (
      innerWidth <= 800 &&
      !event.target.closest("#sidebar") &&
      !event.target.closest("#menu-toggle")
    ) {
      document.getElementById("sidebar").classList.remove("is-open");
      document
        .getElementById("menu-toggle")
        .setAttribute("aria-expanded", "false");
    }
  });
  document
    .getElementById("close-run-dialog")
    .addEventListener("click", () =>
      document.getElementById("run-dialog").close(),
    );
  const renderers = {
    ics: () => window.LeakScopeICS.render(root),
    cloud: () => window.LeakScopeCloud.render(root),
    overview,
    discover,
    findings,
    detail,
    runs,
    queries,
    research: () => window.LeakScopeIntelligence.renderResearch(root),
    assets: () => window.LeakScopeOperations.render(root, "assets"),
    lookups: () => window.LeakScopeOperations.render(root, "lookups"),
    build: () => window.LeakScopeBuild.render(root, initialTarget),
    monitoring: () => window.LeakScopeOperations.render(root, "monitoring"),
    certificates: () => window.LeakScopeAssets.render(root, "certificates"),
    organizations: () => window.LeakScopeAssets.render(root, "organizations"),
    "domain-expansion": () => window.LeakScopeExpansion.render(root),
    relationships: () => window.LeakScopeAssets.render(root, "relationships"),
    catalog: () => window.LeakScopeOperations.render(root, "catalog"),
    analysis: () => window.LeakScopeOperations.render(root, "analysis"),
    changes: () => window.LeakScopeWorkspace.render(root,"changes"),
    correlations: () => window.LeakScopeWorkspace.render(root,"correlations"),
    technologies: () => window.LeakScopeAtlas.render(root,"technologies"),
    screenshots: () => window.LeakScopeAtlas.render(root,"screenshots"),
    geography: () => window.LeakScopeAtlas.render(root,"geography"),
    "domain-coverage": () => window.LeakScopeDomainCoverage.render(root),
    preview: () => window.LeakScopePreview ? window.LeakScopePreview.render(root, {legacy: () => inspection("preview")}) : inspection("preview"),
    explore: () => inspection("explore"),
    exports,
    settings,
  };
  if(window.LeakScopeWorkspace && document.getElementById("workspace-activity"))window.LeakScopeWorkspace.mountActivity(document.getElementById("workspace-activity"));
  (async () => {
    try {
      await (renderers[page] || overview)();
      // Observations mounts its own view menu inside the shared list toolbar.
      if(window.LeakScopeWorkspace && (page==="assets" ? new URLSearchParams(location.search).has("endpoint") : ["findings","organizations","cloud","certificates","relationships","domain-expansion","changes","correlations","technologies","screenshots","geography"].includes(page)))root.prepend(window.LeakScopeWorkspace.savedViews({page,root}));
    } catch (error) {
      content(
        heading(
          pageNames[page] || "Workbench",
          "The page could not load its data.",
        ),
        node(
          "div",
          { class: "panel page-error" },
          node(
            "div",
            { class: "panel-body mt" },
            errorBox(error),
            button("Try again", () => location.reload(), "btn mt"),
          ),
        ),
      );
    }
  })();
})();
