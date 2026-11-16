(function () {
  "use strict";
  function element(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (["hidden", "disabled", "checked", "open"].includes(key))
        node[key] = !!value;
      else if (key.startsWith("on") && typeof value === "function")
        node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== undefined && child !== null && child !== false)
        node.append(
          child instanceof Node
            ? child
            : document.createTextNode(String(child)),
        );
    }
    return node;
  }

  const number = (value) =>
    Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const count = (value) =>
    number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  const when = (value) =>
    value && Number.isFinite(Date.parse(value))
      ? new Date(value).toLocaleString()
      : "Unknown";
  const button = (label, action, attributes = {}) =>
    element(
      "button",
      {
        type: "button",
        class: "btn btn-small",
        onclick: action,
        ...attributes,
      },
      label,
    );
  const message = (text, error = false) =>
    element(
      "p",
      {
        class: "coverage-message" + (error ? " is-error" : ""),
        role: error ? "alert" : "status",
      },
      text,
    );
  const field = (label, control) =>
    element(
      "label",
      { class: "coverage-field" },
      element("span", {}, label),
      control,
    );


  function evidenceAction(reference, label = "Inspect evidence") {
    if (!reference?.id) return null;
    if (reference.type === "asset")
      return element(
        "a",
        { href: "/relationships/?root=" + encodeURIComponent(reference.id) },
        label,
      );
    if (window.LeakScopeInspector?.open)
      return button(label, () => window.LeakScopeInspector.open(reference));
    if (reference.type === "endpoint")
      return element(
        "a",
        { href: "/assets/?endpoint=" + encodeURIComponent(reference.id) },
        label,
      );
    return null;
  }

  function panel(title, description, children, options = {}) {
    const content = element(
      "div",
      { class: "coverage-panel-content" },
      description
        ? element("p", { class: "coverage-description" }, description)
        : null,
      children,
    );
    return element(
        "details",
        {
          class: "coverage-panel " + (options.className || ""),
        },
        element(
          "summary",
          {},
          element("span", {}, title),
          options.count != null
            ? element(
                "span",
                { class: "coverage-small-count" },
                count(options.count),
              )
            : null,
        ),
        content,
      );
  }


  function table(headers, rows, caption) {
    const body = element(
      "tbody",
      {},
      rows.map((cells) =>
        element(
          "tr",
          {},
          cells.map((cell) => element("td", {}, cell)),
        ),
      ),
    );
    return element(
      "div",
      {
        class: "coverage-table-scroll",
        tabindex: "0",
        role: "region",
        "aria-label": caption,
      },
      element(
        "table",
        {},
        element("caption", { class: "coverage-visually-hidden" }, caption),
        element(
          "thead",
          {},
          element(
            "tr",
            {},
            headers.map((label) => element("th", { scope: "col" }, label)),
          ),
        ),
        body,
      ),
    );
  }


  function domainCoverage(data) {
    const domains = data.domains || [];
    const recordTypes = data.record_types || ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "CAA", "HTTPS", "SVCB", "SRV"];
    const search = element("input", {
      class: "input",
      type: "search",
      placeholder: "Filter saved domains",
      "aria-label": "Filter saved domain evidence",
    });
    const sort = element(
      "select",
      { class: "select", "aria-label": "Sort domain evidence" },
      element("option", { value: "coverage" }, "Most record types"),
      element("option", { value: "name" }, "Domain name"),
      element("option", { value: "recent" }, "Recently collected"),
    );
    const holder = element("div", { "data-domain-matrix": "" }),
      status = element("p", {
        class: "coverage-description",
        "aria-live": "polite",
      });
    let shown = 15;
    const more = button("Show more domains", () => {
      shown += 30;
      draw();
    });
    const coverage = (row) =>
      recordTypes.filter((type) => number(row.records?.[type]) > 0).length;
    function draw() {
      const filtered = domains.filter((row) =>
        String(row.name || "")
          .toLowerCase()
          .includes(search.value.trim().toLowerCase()),
      );
      filtered.sort((a, b) =>
        sort.value === "name"
          ? String(a.name).localeCompare(String(b.name))
          : sort.value === "recent"
            ? (Date.parse(b.last_collected_at) || 0) -
              (Date.parse(a.last_collected_at) || 0)
            : coverage(b) - coverage(a) ||
              String(a.name).localeCompare(String(b.name)),
      );
      const rows = filtered.slice(0, shown).map((row) => [
        element(
          "div",
          { class: "coverage-domain-label" },
          element("strong", {}, row.name),
          element("small", {}, (row.sources || []).join(" · ")),
          evidenceAction(row.reference, "Evidence"),
        ),
        ...recordTypes.map((type) => {
          const amount = number(row.records?.[type]);
          return element(
            "span",
            {
              class: "coverage-record-cell" + (amount ? " has-records" : ""),
              title: amount
                ? count(amount) + " retained " + type + " records"
                : "No retained " + type + " evidence",
            },
            amount ? count(amount) : "—",
          );
        }),
        element(
          "span",
          { class: "coverage-freshness" },
          count(row.freshness?.current) + " within expiry",
          element(
            "small",
            {},
            count(row.freshness?.expired) +
              " expired · " +
              count(row.freshness?.unknown) +
              " expiry unspecified",
          ),
        ),
        element(
          "span",
          { class: "coverage-date" },
          when(row.last_collected_at),
        ),
      ]);
      holder.replaceChildren(
        rows.length
          ? table(
              ["Domain", ...recordTypes, "Recorded expiry", "Last collected"],
              rows,
              "Retained DNS evidence by domain",
            )
          : message("No saved domains match this filter."),
      );
      status.textContent =
        Math.min(shown, filtered.length) +
        " of " +
        filtered.length +
        " matching saved domains · Blank cells mean evidence is absent from this saved view.";
      more.hidden = shown >= filtered.length;
    }
    search.addEventListener("input", () => {
      shown = 15;
      draw();
    });
    sort.addEventListener("change", () => {
      shown = 15;
      draw();
    });
    draw();
    return panel(
      "Domain evidence coverage",
      "Inspect retained DNS types, recorded expiry and collection dates across the selected domain scope.",
      [
        element("div", { class: "coverage-inline-tools" }, search, sort),
        status,
        holder,
        more,
      ],
      { count: domains.length },
    );
  }


  let active;
  function dispose() { active?.(); }
  async function render(root) {
    dispose();
    const controller = new AbortController();
    let disposed = false, version = 0, reading;
    const selected = new URLSearchParams(location.search).get("organization_id") || "";
    const scope = element("select", {class:"select", name:"organization_id", "aria-label":"Organization scope"}, element("option", {value:""}, "All saved domains"));
    if (selected) scope.append(element("option", {value:selected}, "Selected organization"));
    scope.value = selected;
    const status = element("div", {"aria-live":"polite"});
    const content = element("div", {class:"coverage-content"});
    const scopeName = element("strong", {}, "Loading saved scope…");
    const generated = element("span", {class:"coverage-generated"});
    const refresh = button("Refresh saved evidence", () => load());
    const shell = element("div", {class:"domain-coverage"},
      element("header", {}, element("h1", {}, "Domain evidence coverage"), element("p", {}, "Review saved DNS evidence, record types and collection dates for an organization or all saved domains.")),
      element("div", {class:"coverage-toolbar"}, field("Organization scope", scope), refresh),
      element("div", {class:"coverage-scope-banner"}, element("span", {}, "Evidence scope"), scopeName, generated),
      status,
      element("div", {class:"coverage-controls"}, button("Expand all", () => shell.querySelectorAll("details").forEach(row => {row.open=true})), button("Collapse all", () => shell.querySelectorAll("details").forEach(row => {row.open=false}))),
      content);
    root.replaceChildren(shell);
    async function request(path, signal) {
      const response = await fetch(path, {signal, credentials:"same-origin", headers:{Accept:"application/json"}});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || "Saved DNS evidence could not be loaded.");
      return data;
    }
    async function load() {
      reading?.abort(); reading = new AbortController();
      const mine = ++version;
      refresh.disabled = true; root.setAttribute("aria-busy","true");
      status.replaceChildren(message("Reading saved DNS evidence…"));
      content.replaceChildren();
      try {
        const query = new URLSearchParams(scope.value ? {organization_id:scope.value} : {});
        const data = await request("/api/assets/domain-coverage" + (query.size ? "?"+query : ""), reading.signal);
        if (disposed || mine !== version) return;
        scopeName.textContent = data.scope?.label || "All saved domains";
        generated.textContent = "Prepared " + when(data.generated_at);
        const coverage = data.coverage || {};
        content.replaceChildren(...[
          coverage.limited ? message("Bounded saved view: " + count(coverage.domains_sampled) + " / " + count(coverage.domains_total) + " domains and " + count(coverage.sampled_evidence) + " / " + count(coverage.scope_evidence) + " DNS evidence records. Narrow the organization scope for more detail.") : null,
          domainCoverage(data),
        ].filter(Boolean));
        status.replaceChildren();
      } catch(error) {
        if (!disposed && mine === version && error.name !== "AbortError") status.replaceChildren(message(error.message,true),button("Retry saved evidence",load));
      } finally {
        if (!disposed && mine === version) {refresh.disabled=false;root.setAttribute("aria-busy","false");}
      }
    }
    scope.addEventListener("change", () => {
      const query = new URLSearchParams(location.search);
      scope.value ? query.set("organization_id",scope.value) : query.delete("organization_id");
      history.replaceState(null,"",location.pathname + (query.size ? "?"+query : ""));
      load();
    });
    const cleanup = () => {disposed=true;version++;controller.abort();reading?.abort();observer.disconnect();if(active===cleanup)active=null;};
    const observer = new MutationObserver(() => {if(!root.contains(shell))cleanup();});
    observer.observe(root,{childList:true});active=cleanup;
    const organizations = request("/api/assets/organizations?status=all",controller.signal).then(data => {
      if(disposed)return;
      const current=scope.value;
      scope.replaceChildren(element("option",{value:""},"All saved domains"),...(data.results||[]).map(row=>element("option",{value:row.id},row.label||row.value||"Saved organization")));
      if(current && ![...scope.options].some(row=>row.value===current))scope.append(element("option",{value:current},"Selected organization"));
      scope.value=current;
    }).catch(error=>{if(error.name!=="AbortError")scope.title="Organization names are unavailable; the selected scope is retained.";});
    await Promise.all([load(),organizations]);
  }
  window.addEventListener("pagehide",dispose);
  window.LeakScopeDomainCoverage = {render,dispose};
})();
