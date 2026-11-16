const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const endpoint = {
  id: "11111111-1111-4111-8111-111111111111",
  address: "192.0.2.7",
  port: 443,
  transport: "tcp",
  scheme: "https",
  service_type: "postgres",
  title: "<img src=x onerror=alert(1)>",
  tags: ["review"],
  observation_count: 2,
};
const catalog = {
  results: [
    {
      key: "postgres",
      label: "PostgreSQL",
      category: "database",
      ports: [5432],
      transport: "tcp",
      queries: {
        shodan: "product:PostgreSQL",
        zoomeye: 'app="PostgreSQL"',
        netlas: "protocol:postgres",
      },
      identity: false,
      inspection: false,
      export: false,
      detection_confidence: "Provider evidence only",
    },
  ],
  providers: {
    shodan: { configured: true },
    zoomeye: { configured: false },
    netlas: { configured: true },
  },
  tools: { curl: true, httpx: false, gowitness: false },
};
const lookup = {
  id: "lookup-1",
  name: "Database watch",
  provider: "netlas",
  query: "protocol:postgres",
  service_type: "postgres",
  enabled: true,
  auto_fetch: true,
  tags: ["database"],
  interval_seconds: 3600,
  actions: ["http"],
  last_state: "succeeded",
};
const stream = {
  mode: "builtin",
  enabled: true,
  state: "connected",
  received: 200,
  matched: 3,
  dropped: 0,
  lag_seconds: 1,
  builtin_url: "ws://certstream:8080/",
};
function setup(overrides = {}, url = "http://localhost/") {
  const dom = new JSDOM(
    '<meta name="csrf-token" content="fixture"><div id="page-root"></div>',
    { url, runScripts: "outside-only" },
  );
  const calls = [];
  const fake = {
    "/catalog": catalog,
    "/tags": { tags: ["review"], manual_tags: ["review"], inherited_tags: [] },
    "/lookups": { results: [lookup] },
    "/endpoints": { results: [endpoint], count: 1, page: 1, pages: 1 },
    ["/endpoints/" + endpoint.id]: {
      endpoint,
      observations: [
        {
          id: "obs",
          provider: "netlas",
          data: { title: "Stored evidence" },
          first_collected_at: "2026-11-07T10:00:00Z",
        },
      ],
      artifacts: [
        {
          id: "artifact",
          kind: "screenshot",
          url: "javascript:alert(1)",
          tool: "gowitness",
          size: 100,
        },
      ],
    },
    "/stream": stream,
    "/watches": { results: [] },
    "/analysis": {
      counts: {
        endpoints: 1,
        observations: 2,
        artifacts: 0,
        lookups: 1,
        watches: 0,
        changes: 0,
      },
      by_provider: [{ label: "netlas", count: 2 }],
      by_service: [],
      by_category: [],
      by_tag: [],
      timeline: [],
      recent_changes: [],
      relations: [],
      tool_health: { curl: true },
    },
  };
  dom.window.fetch = async (url, opts = {}) => {
    calls.push({ url, body: opts.body ? JSON.parse(opts.body) : null, opts });
    const route = new URL(url, "http://localhost").pathname.replace(
      "/api/operations",
      "",
    );
    const data = overrides[route] ??
      fake[route] ?? {
        lookup,
        run: { id: "run-1", state: "queued" },
        tags: ["review"],
      };
    return {
      ok: true,
      json: async () => data,
      text: async () =>
        typeof data === "string" ? data : JSON.stringify(data),
      headers: { get: () => null },
    };
  };
  dom.window.LeakScopeIntelligence = {
    openAsk: (refs) => calls.push({ action: "ask", refs }),
    openSimilar: (refs) => calls.push({ action: "similar", refs }),
    openSettings: (onSaved) => calls.push({ action: "settings", onSaved }),
  };
  dom.window.eval(fs.readFileSync(require('node:path').resolve(__dirname,'../../leakscope_app/static/js/leakscope/list_toolbar.js'),'utf8'));dom.window.eval(
    fs.readFileSync(
      path.join(root, "leakscope_app/static/js/leakscope/operations.js"),
      "utf8",
    ),
  );
  return { dom, calls, api: dom.window.LeakScopeOperations };
}
const settle = () => new Promise((r) => setTimeout(r, 30));
const button = (d, label) =>
  [...d.querySelectorAll("button")].find((b) => b.textContent === label);
test("endpoint names DNS and netblock tools keep the saved endpoint identity", async () => {
  const { dom, calls, api } = setup({}, "http://localhost/assets/?endpoint=" + endpoint.id);
  const opened = [];
  dom.window.LeakScopeAssets = {openTools: reference => opened.push(reference)};
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  button(d, "Names, DNS & netblock").click();
  assert.equal(opened.length, 1);
  assert.equal(opened[0].type, "endpoint");
  assert.equal(opened[0].id, endpoint.id);
  assert.equal(calls.some(call => /\/(dns|resolve|netblock)$/.test(call.url)), false);
  delete dom.window.LeakScopeAssets;
  assert.doesNotThrow(() => button(d, "Names, DNS & netblock").click());
  dom.window.close();
});
test("every endpoint row and detail has an explicit add-to-organization action", async () => {
  for (const url of ["http://localhost/assets/", "http://localhost/assets/?endpoint=" + endpoint.id]) {
    const {dom,api}=setup({},url);const opened=[];
    dom.window.LeakScopeAssets={addToOrganization:ref=>opened.push(ref)};
    await api.render(dom.window.document.querySelector("#page-root"),"assets");
    const add=button(dom.window.document,"Add to organization");assert.ok(add);add.click();
    assert.equal(opened[0].type,"endpoint");assert.equal(opened[0].id,endpoint.id);dom.window.close();
  }
});
test("Netlas enrichment waits for collection selection and submits one explicit job", async () => {
  const { dom, calls, api } = setup(
    {},
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  const watched = [];
  dom.window.LeakScopeWorkbench = {
    watchRun: (run, holder, complete, options) =>
      watched.push({ run, holder, complete, options }),
  };
  await api.render(d.querySelector("#page-root"), "assets");
  button(d, "Enrich with Netlas").click();
  assert.equal(
    calls.some((c) => c.url.endsWith("/enrich")),
    false,
  );
  const dialog = d.querySelector("dialog.ops-enrichment-dialog");
  assert.ok(dialog);
  assert.match(dialog.textContent, /Up to 2 Netlas requests/);
  const host = dialog.querySelector('input[value="host"]');
  host.click();
  button(dialog, "Run enrichment").click();
  await settle();
  const call = calls.find((c) => c.url.endsWith("/enrich"));
  assert.deepEqual(call.body, { collections: ["dns"] });
  assert.equal(call.opts.headers["X-CSRFToken"], "fixture");
  assert.equal(watched.length, 1);
  button(dialog, "×").click();
  assert.equal(watched[0].options.signal.aborted, true);
  assert.equal(d.querySelector("dialog.ops-enrichment-dialog"), null);
  dom.window.close();
});
test("missing Netlas configuration directs enrichment to integration settings", async () => {
  const { dom, calls, api } = setup(
    {
      "/catalog": {
        ...catalog,
        providers: { ...catalog.providers, netlas: { configured: false } },
      },
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  await api.render(dom.window.document.querySelector("#page-root"), "assets");
  button(dom.window.document, "Enrich with Netlas").click();
  assert.ok(calls.some((call) => call.action === "settings"));
  assert.equal(
    calls.some((call) => call.url?.endsWith("/enrich")),
    false,
  );
  const saved = calls.find((call) => call.action === "settings");
  assert.equal(typeof saved.onSaved, "function");
  saved.onSaved({ netlas: { configured: true } });
  button(dom.window.document, "Enrich with Netlas").click();
  assert.ok(dom.window.document.querySelector("dialog.ops-enrichment-dialog"));
  assert.equal(calls.filter((call) => call.action === "settings").length, 1);
  dom.window.close();
});
test("endpoint evidence shows resource path and compares only the latest two safe PNG captures", async () => {
  const artifacts = [1, 3, 2].map((i) => ({
    id: String(i),
    kind: "screenshot",
    mime: "image/png",
    url: `/api/operations/artifacts/${i}`,
    created_at: `2026-11-0${i}T10:00:00Z`,
  }));
  artifacts.push({
    id: "unsafe",
    kind: "screenshot",
    mime: "image/png",
    url: "https://remote.example/image.png",
    created_at: "2026-11-06T10:00:00Z",
  });
  const { dom, api } = setup(
    {
      ["/endpoints/" + endpoint.id]: {
        endpoint: { ...endpoint, resource_path: "/admin/<script>" },
        observations: [],
        artifacts,
      },
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  assert.match(d.body.textContent, /Resource path: \/admin\/<script>/);
  assert.equal(d.querySelector("script"), null);
  const comparison = d.querySelector(".ops-screenshot-comparison");
  assert.ok(comparison);
  assert.deepEqual(
    [...comparison.querySelectorAll("img")].map((i) => new URL(i.src).pathname),
    ["/api/operations/artifacts/2", "/api/operations/artifacts/3"],
  );
  assert.equal(d.querySelector('img[src^="https://remote"]'), null);
  dom.window.close();
});
test("monitoring matches refresh with health and stop polling when the page is disposed", async () => {
  const match = {
    id: "match-1",
    certificate_id: endpoint.id,
    names: ["<img src=x>"],
    watch_name: "Fixture scope",
    collected_at: "2026-11-07T10:00:00Z",
  };
  const { dom, calls, api } = setup({
    "/stream": { ...stream, recent_matches: [match] },
  });
  Object.defineProperty(dom.window.document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  const timers = new Map();
  let next = 0;
  dom.window.setTimeout = (callback, delay) => {
    const id = ++next;
    timers.set(id, { callback, delay });
    return id;
  };
  dom.window.clearTimeout = (id) => timers.delete(id);
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "monitoring");
  const matches = d.querySelector(".ops-stream-matches");
  assert.match(matches.textContent, /<img src=x>/);
  assert.equal(matches.querySelector("img"), null);
  assert.equal(
    matches.querySelector("a").getAttribute("href"),
    "/certificates/?id=" + endpoint.id,
  );
  assert.equal([...timers.values()][0].delay, 5000);
  await [...timers.values()][0].callback();
  assert.equal(
    calls.filter((call) => call.url === "/api/operations/stream").length,
    2,
  );
  dom.window.dispatchEvent(new dom.window.Event("pagehide"));
  const count = calls.length;
  for (const timer of timers.values()) await timer.callback();
  assert.equal(calls.length, count);
  dom.window.close();
});
test("shared run watcher displays safe terminal JSON and cancels polling on modal close", async () => {
  const html = fs
    .readFileSync(
      path.join(root, "leakscope_app/templates/workbench/app.html"),
      "utf8",
    )
    .replace("{{ page|default:'overview' }}", "overview");
  const dom = new JSDOM(html, {
    url: "http://localhost/",
    runScripts: "outside-only",
  });
  const calls = [];
  dom.window.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      json: async () =>
        url.startsWith("/api/runs/")
          ? {
              id: "job-1",
              state: "succeeded",
              result: { host: "<img src=x onerror=alert(1)>" },
            }
          : { counts: {}, by_type: [], by_provider: [], recent_runs: [] },
    };
  };
  for (const file of ["utils.js", "app.js"])
    dom.window.eval(
      fs.readFileSync(
        path.join(root, "leakscope_app/static/js/leakscope", file),
        "utf8",
      ),
    );
  await settle();
  assert.equal(typeof dom.window.LeakScopeWorkbench?.watchRun, "function");
  const timers = new Map();
  let next = 0;
  dom.window.setTimeout = (callback) => {
    const id = ++next;
    timers.set(id, callback);
    return id;
  };
  dom.window.clearTimeout = (id) => timers.delete(id);
  const holder = dom.window.document.createElement("div");
  const controller = new dom.window.AbortController();
  dom.window.LeakScopeWorkbench.watchRun(
    { id: "cancelled-job", state: "queued" },
    holder,
    null,
    { signal: controller.signal },
  );
  assert.equal(timers.size, 1);
  controller.abort();
  assert.equal(timers.size, 0);
  assert.equal(
    calls.some((call) => call.url.includes("cancelled-job")),
    false,
  );
  const active = new dom.window.AbortController();
  let completed = 0;
  dom.window.LeakScopeWorkbench.watchRun(
    { id: "job-1", state: "queued" },
    holder,
    () => completed++,
    { signal: active.signal },
  );
  await [...timers.values()][0]();
  assert.equal(completed, 1);
  assert.match(holder.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(holder.querySelector("img"), null);
  assert.equal(
    calls.find((call) => call.url === "/api/runs/job-1").options.signal,
    active.signal,
  );
  dom.window.close();
});
test("catalog omits unsupported capabilities and seeds lookups without HTML execution", async () => {
  const { dom, api } = setup();
  await api.render(dom.window.document.querySelector("#page-root"), "catalog");
  const d = dom.window.document;
  assert.match(d.body.textContent, /PostgreSQL/);
  assert.doesNotMatch(d.body.textContent, /Not available|Inspection|Export/);
  assert.equal(d.querySelector("img"), null);
  const link = [...d.querySelectorAll("a")].find(
    (a) => a.textContent === "Create lookup",
  );
  assert.equal(new URL(link.href).searchParams.get("service_type"), "postgres");
  assert.equal(
    d.querySelector("#page-root").getAttribute("aria-busy"),
    "false",
  );
  dom.window.close();
});
test("lookup form preserves provider query scope actions and scheduling fields", async () => {
  const { dom, calls, api } = setup(
    {},
    "http://localhost/lookups/?service_type=postgres&provider=netlas&query=protocol%3Apostgres",
  );
  await api.render(dom.window.document.querySelector("#page-root"), "lookups");
  const d = dom.window.document;
  d.querySelector("input[name=name]").value = "Stored scope";
  d.querySelector("input[name=network]").value = "192.0.2.0/24";
  d.querySelector("input[name=tags]").value = "database, important";
  button(d, "Save lookup").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/lookups" && c.body);
  assert.equal(call.body.provider, "netlas");
  assert.equal(call.body.query, "protocol:postgres");
  assert.equal(call.body.scope.network, "192.0.2.0/24");
  assert.deepEqual(call.body.tags, ["database", "important"]);
  assert.equal(call.opts.headers["X-CSRFToken"], "fixture");
  dom.window.close();
});
test("assets preserve endpoint UUID references for Ask and fetch actions", async () => {
  const { dom, calls, api } = setup(
    {},
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  await api.render(dom.window.document.querySelector("#page-root"), "assets");
  const d = dom.window.document;
  button(d, "Ask").click();
  assert.deepEqual(
    JSON.parse(JSON.stringify(calls.find((c) => c.action === "ask").refs)),
    [{ type: "endpoint", id: endpoint.id }],
  );
  button(d, "Fetch selected actions").click();
  await settle();
  const call = calls.find((c) =>
    c.url?.endsWith("/endpoints/" + endpoint.id + "/fetch"),
  );
  assert.ok(call);
  assert.equal(d.querySelector('a[href^="javascript:"]'), null);
  assert.equal(d.querySelector("img"), null);
  dom.window.close();
});
test("bulk fetch selection obeys the API limit of ten endpoint IDs", async () => {
  const endpoints = Array.from({ length: 11 }, (_, index) => ({
    ...endpoint,
    id: `endpoint-${index}`,
    address: `192.0.2.${index + 1}`,
  }));
  const { dom, calls, api } = setup({
    "/endpoints": { results: endpoints, count: 11, page: 1, pages: 1 },
  });
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  const boxes = [...d.querySelectorAll('input[aria-label^="Select "]')];
  boxes.forEach((box) => box.click());
  assert.equal(boxes.filter((box) => box.checked).length, 10);
  assert.equal(boxes[10].checked, false);
  assert.match(d.body.textContent, /Select at most 10 endpoints/);
  button(d, "Fetch selected actions").click();
  await settle();
  const call = calls.find(
    (item) => item.url === "/api/operations/fetch" && item.body,
  );
  assert.deepEqual(
    call.body.ids,
    endpoints.slice(0, 10).map((item) => item.id),
  );
  dom.window.close();
});
test("monitoring defaults to built-in service and rejects malformed external URL", async () => {
  const { dom, calls, api } = setup();
  await api.render(
    dom.window.document.querySelector("#page-root"),
    "monitoring",
  );
  const d = dom.window.document;
  assert.equal(d.querySelector("select[name=mode]").value, "builtin");
  const mode = d.querySelector("select[name=mode]");
  mode.value = "external";
  mode.dispatchEvent(new dom.window.Event("change"));
  d.querySelector("input[name=external_url]").value = "javascript:alert(1)";
  button(d, "Save stream settings").click();
  await settle();
  assert.equal(
    calls.some((c) => c.url === "/api/operations/stream" && c.body),
    false,
  );
  assert.match(d.body.textContent, /ws:\/\//);
  dom.window.close();
});
test("certificate watch defaults to observe-only without ports or profiles", async () => {
  const { dom, calls, api } = setup();
  await api.render(
    dom.window.document.querySelector("#page-root"),
    "monitoring",
  );
  const d = dom.window.document;
  d.querySelector("input[name=watch_name]").value = "Owned domains";
  d.querySelector("textarea[name=domains]").value =
    "example.com\nsub.example.com";
  button(d, "Save watch").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/watches" && c.body);
  assert.deepEqual(call.body.domains, ["example.com", "sub.example.com"]);
  assert.equal(call.body.auto_fetch, false);
  assert.deepEqual(call.body.ports, []);
  assert.deepEqual(call.body.actions, []);
  dom.window.close();
});
test("watch follow-up fields are hidden and disabled until explicitly enabled", async () => {
  const { dom, calls, api } = setup();
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "monitoring");
  const auto = d.querySelector("input[name=watch_auto_fetch]");
  const followup = d.querySelector("[data-watch-followup]");
  assert.equal(auto.checked, false);
  assert.equal(followup.hidden, true);
  assert.equal(d.querySelector("input[name=ports]").disabled, true);
  assert.equal(d.querySelector("input[name=watch_cooldown]").disabled, true);
  auto.checked = true;
  auto.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(followup.hidden, false);
  assert.equal(d.querySelector("input[name=ports]").disabled, false);
  assert.equal(followup.querySelector('input[value="httpx"]').disabled, true);
  d.querySelector("input[name=watch_name]").value = "Explicit fetch";
  d.querySelector("textarea[name=domains]").value = "example.com";
  d.querySelector("input[name=ports]").value = "8443";
  button(d, "Save watch").click();
  await settle();
  const payload = calls.find(c => c.url === "/api/operations/watches" && c.body).body;
  assert.equal(payload.auto_fetch, true);
  assert.deepEqual(payload.ports, [8443]);
  assert.deepEqual(payload.actions, ["http"]);
  dom.window.close();
});
test("recent monitor matches link saved wildcard certificates without an assumed port", async () => {
  const { dom, api } = setup({"/stream": {...stream, recent_matches: [{
    id: "match-1", certificate_id: "certificate-1", names: ["*.example.com"],
    watch_name: "Scope <script>alert(1)</script>", collected_at: "2026-11-08T10:00:00Z"
  }]}});
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "monitoring");
  const link = d.querySelector('a[href="/certificates/?id=certificate-1"]');
  assert.ok(link);
  assert.equal(link.textContent, "*.example.com");
  assert.doesNotMatch(link.textContent, /:443/);
  assert.match(d.body.textContent, /Scope <script>/);
  assert.equal(d.querySelectorAll("script").length, 0);
  dom.window.close();
});
test("analysis uses supplied metrics and labels observed relationships", async () => {
  const { dom, api } = setup();
  await api.render(dom.window.document.querySelector("#page-root"), "analysis");
  const d = dom.window.document;
  assert.match(d.body.textContent, /Observed relationships/);
  assert.match(d.body.textContent, /netlas/);
  assert.match(d.body.textContent, /No relationship evidence/);
  assert.equal(
    d.querySelector("#page-root").getAttribute("aria-busy"),
    "false",
  );
  dom.window.close();
});
test("editing a lookup preserves its ID and current schedule", async () => {
  const { dom, calls, api } = setup({
    "/lookups": {
      results: [
        {
          ...lookup,
          tags: ["database", "inherited"],
          manual_tags: ["database"],
          inherited_tags: ["inherited"],
        },
      ],
    },
  });
  await api.render(dom.window.document.querySelector("#page-root"), "lookups");
  const d = dom.window.document;
  button(d, "Edit").click();
  assert.equal(d.querySelector("input[name=name]").value, lookup.name);
  assert.equal(d.querySelector(".ops-form input[name=tags]").value, "database");
  d.querySelector("input[name=interval_seconds]").value = "7200";
  button(d, "Save changes").click();
  await settle();
  const call = calls.find(
    (c) => c.url === "/api/operations/lookups/lookup-1/save",
  );
  assert.ok(call);
  assert.equal(call.body.interval_seconds, 7200);
  assert.equal(call.body.provider, "netlas");
  assert.deepEqual(call.body.tags, ["database"]);
  dom.window.close();
});
test("tag dialog sends the actual finding key and safely closes", async () => {
  const { dom, calls, api } = setup();
  api.openTags("finding", "elastic:1", ["review"]);
  await settle();
  const d = dom.window.document;
  d.querySelector("input[name=tags]").value = "review, database";
  button(d, "Save tags").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/tags" && c.body);
  assert.equal(call.body.target_kind, "finding");
  assert.equal(call.body.target_key, "elastic:1");
  button(d, "×").click();
  assert.equal(d.querySelector("dialog"), null);
  dom.window.close();
});
test("native endpoints never default to HTTP when no HTTP capability is declared", async () => {
  const native = {
    ...endpoint,
    scheme: "",
    fetch_actions: [],
    default_actions: [],
  };
  const { dom, api } = setup(
    {
      ["/endpoints/" + endpoint.id]: {
        endpoint: native,
        observations: [],
        artifacts: [],
      },
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  await api.render(dom.window.document.querySelector("#page-root"), "assets");
  const d = dom.window.document;
  assert.equal(d.querySelector('input[value="http"]').checked, false);
  assert.equal(d.querySelector('input[value="http"]').disabled, true);
  assert.equal(button(d, "Fetch selected actions").disabled, true);
  dom.window.close();
});
test("catalog-backed lookup profiles default to the supported identity action", async () => {
  const caps = {
    ...catalog,
    results: [
      {
        ...catalog.results[0],
        identity: true,
        fetch_actions: ["identity"],
        default_actions: ["identity"],
      },
    ],
  };
  const { dom, api } = setup(
    { "/catalog": caps },
    "http://localhost/lookups/?service_type=postgres",
  );
  await api.render(dom.window.document.querySelector("#page-root"), "lookups");
  const d = dom.window.document;
  assert.equal(d.querySelector('input[value="identity"]').checked, true);
  assert.equal(d.querySelector('input[value="http"]').disabled, true);
  dom.window.close();
});
test("editing a watch preserves its identity and disabled automatic mode", async () => {
  const watch = {
    id: "watch-1",
    name: "Certificate scope",
    domains: ["example.com"],
    keywords: ["storage"],
    ports: [443],
    actions: ["http"],
    auto_fetch: false,
    enabled: true,
    cooldown_seconds: 7200,
    max_daily_fetches: 12,
    tags: ["review", "inherited"],
    manual_tags: ["review"],
    inherited_tags: ["inherited"],
  };
  const { dom, calls, api } = setup({ "/watches": { results: [watch] } });
  await api.render(
    dom.window.document.querySelector("#page-root"),
    "monitoring",
  );
  const d = dom.window.document;
  button(d, "Edit").click();
  assert.equal(d.querySelector("textarea[name=domains]").value, "example.com");
  assert.equal(d.querySelector("input[name=watch_auto_fetch]").checked, false);
  assert.equal(d.querySelector("input[name=watch_tags]").value, "review");
  d.querySelector("input[name=watch_cooldown]").value = "10800";
  button(d, "Save watch changes").click();
  await settle();
  const call = calls.find(
    (c) => c.url === "/api/operations/watches/watch-1/save",
  );
  assert.ok(call);
  assert.equal(call.body.cooldown_seconds, 10800);
  assert.equal(call.body.auto_fetch, false);
  assert.deepEqual(call.body.tags, ["review"]);
  dom.window.close();
});
test("editing an active certificate watch preserves its follow-up settings", async () => {
  const watch = {id: "watch-active", name: "Existing active watch", domains: ["example.com"],
    keywords: [], ports: [8443], actions: ["identity"], auto_fetch: true, enabled: false,
    cooldown_seconds: 7200, max_daily_fetches: 12};
  const { dom, calls, api } = setup({"/watches": {results: [watch]}});
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "monitoring");
  button(d, "Edit").click();
  assert.equal(d.querySelector("[data-watch-followup]").hidden, false);
  assert.equal(d.querySelector("input[name=watch_auto_fetch]").checked, true);
  assert.equal(d.querySelector("input[name=watch_enabled]").checked, false);
  button(d, "Save watch changes").click();
  await settle();
  const payload = calls.find(c => c.url === "/api/operations/watches/watch-active/save").body;
  assert.deepEqual(payload.ports, [8443]);
  assert.deepEqual(payload.actions, ["identity"]);
  assert.equal(payload.auto_fetch, true);
  assert.equal(payload.enabled, false);
  assert.equal(payload.cooldown_seconds, 7200);
  assert.equal(payload.max_daily_fetches, 12);
  dom.window.close();
});
test("seeded lookup editor preserves scope and starts as a disabled manual draft", async () => {
  const { dom, calls, api } = setup();
  api.openLookup({
    name: "Similar storage",
    provider: "netlas",
    query: 'http.title:"Storage"',
    scope: { network: "192.0.2.0/24", country: "US", exclude: "staging" },
    service_type: "",
  });
  await settle();
  const d = dom.window.document;
  assert.equal(d.querySelector("input[name=name]").value, "Similar storage");
  assert.equal(d.querySelector("input[name=network]").value, "192.0.2.0/24");
  assert.equal(d.querySelector("select[name=schedule]").value, "manual");
  assert.equal(d.querySelector("input[name=enabled]").checked, false);
  button(d, "Save lookup").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/lookups" && c.body);
  assert.equal(call.body.scope.country, "US");
  assert.equal(call.body.interval_seconds, null);
  assert.equal(call.body.enabled, false);
  dom.window.close();
});
test("scheduled web research uses topic mode and never carries target fetch actions", async () => {
  const { dom, calls, api } = setup();
  await api.render(dom.window.document.querySelector("#page-root"), "lookups");
  const d = dom.window.document;
  const provider = d.querySelector("select[name=provider]");
  provider.value = "research";
  provider.dispatchEvent(new dom.window.Event("change"));
  d.querySelector("input[name=name]").value = "New database releases";
  d.querySelector("textarea[name=query]").value =
    "New database products and release documentation";
  button(d, "Save lookup").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/lookups" && c.body);
  assert.equal(call.body.provider, "research");
  assert.equal(call.body.auto_fetch, false);
  assert.deepEqual(call.body.actions, []);
  assert.deepEqual(call.body.scope, {});
  dom.window.close();
});
test("saving stream settings preserves a masked authenticated URL unless edited", async () => {
  const { dom, calls, api } = setup({
    "/stream": {
      ...stream,
      mode: "external",
      external_url: "wss://feed.example.test/path",
      external_url_has_credentials: true,
    },
  });
  await api.render(
    dom.window.document.querySelector("#page-root"),
    "monitoring",
  );
  const d = dom.window.document;
  assert.equal(d.querySelector("input[name=external_url]").value, "");
  button(d, "Save stream settings").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/stream" && c.body);
  assert.ok(call);
  assert.equal(Object.hasOwn(call.body, "external_url"), false);
  dom.window.close();
});
test("tag editing keeps inherited tags read-only and posts manual assignments only", async () => {
  const dto = {
    tags: ["from-rule", "review"],
    manual_tags: ["review"],
    inherited_tags: ["from-rule"],
  };
  const { dom, calls, api } = setup({ "/tags": dto });
  api.openTags("endpoint", endpoint.id, dto.tags);
  await settle();
  const d = dom.window.document;
  assert.equal(d.querySelector("input[name=tags]").value, "review");
  assert.match(d.querySelector("dialog").textContent, /from-rule/);
  d.querySelector("input[name=tags]").value = "review, audit";
  button(d, "Save tags").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/operations/tags" && c.body);
  assert.deepEqual(call.body.tags, ["review", "audit"]);
  dom.window.close();
});

test("TCP evidence editor submits explicit bounded manual options", async () => {
  const record = {
    ...endpoint,
    scheme: "",
    service_type: "modbus",
    fetch_actions: ["tcp"],
    default_actions: ["tcp"],
  };
  const { dom, calls, api } = setup(
    {
      ["/endpoints/" + endpoint.id]: {
        endpoint: record,
        observations: [],
        artifacts: [],
      },
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  assert.equal(
    [...d.querySelector("#page-root").childNodes].some(
      (n) => n.nodeType === 3 && n.textContent.includes("null"),
    ),
    false,
    "Missing optional sections must not render literal null text",
  );
  assert.match(d.body.textContent, /TCP request \/ response/);
  const payload = d.querySelector("[name=tcp_payload]");
  assert.ok(payload);
  assert.equal(payload.value, "");
  payload.value = "hello";
  d.querySelector("[name=tcp_encoding]").value = "text";
  d.querySelector("[name=tcp_timeout]").value = "3";
  button(d, "Fetch selected actions").click();
  await settle();
  const body = calls.find((x) => x.url.endsWith("/fetch")).body;
  assert.deepEqual(body.actions, ["tcp"]);
  assert.deepEqual(body.options, {
    tcp: { payload: "hello", encoding: "text", tls: false, timeout_seconds: 3 },
  });
  dom.window.close();
});

test("wire artifact inspection displays transcript as safe text", async () => {
  const { dom, api } = setup(
    {
      ["/endpoints/" + endpoint.id]: {
        endpoint,
        observations: [],
        artifacts: [
          {
            kind: "wire",
            url: "/artifacts/wire",
            metadata: { wire_summary: { bytes_sent: 0, bytes_received: 12 } },
          },
        ],
      },
      "/artifacts/wire": JSON.stringify({
        events: [
          {
            direction: "received",
            hex: "3c",
            text: "<script>window.injected=true</script>",
            bytes: 1,
          },
        ],
      }),
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  const inspect = button(d, "Inspect transcript");
  assert.ok(inspect);
  inspect.click();
  await settle();
  assert.match(d.body.textContent, /<script>window.injected=true<\/script>/);
  assert.equal(dom.window.injected, undefined);
  assert.equal(d.querySelector(".ops-transcript script"), null);
  dom.window.close();
});

test("catalog query strategies show fingerprints and explicit unsupported reasons", async () => {
  const variants = {
    fingerprint: {
      shodan: {
        query: '"Modbus"',
        supported: true,
        basis: "Indexed response banner",
      },
      netlas: { supported: false, reason: "No verified signature" },
    },
    nonstandard: {
      shodan: {
        query: '"Modbus" -port:502',
        supported: true,
        basis: "Banner excluding usual ports",
      },
    },
    ports: {
      shodan: {
        query: "port:502",
        supported: true,
        basis: "Port-only candidate",
      },
    },
  };
  const { dom, api } = setup({
    "/catalog": {
      ...catalog,
      results: [
        {
          key: "modbus",
          label: "Modbus",
          category: "ot",
          categories: ["ot"],
          category_label: "ICS / SCADA / OT",
          ports: [502],
          transport: "tcp",
          query_variants: variants,
        },
      ],
    },
  });
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "catalog");
  const mode = d.querySelector("[name=catalog_query_mode]");
  assert.ok(mode);
  assert.equal(mode.value, "fingerprint");
  assert.match(d.body.textContent, /No verified signature/);
  assert.match(d.body.textContent, /Indexed response banner/);
  assert.ok(
    [...d.querySelectorAll("pre")].some((p) => p.textContent === '"Modbus"'),
  );
  assert.equal(
    [...d.querySelectorAll("a")].filter(
      (a) => a.textContent === "Create lookup",
    ).length,
    1,
  );
  mode.value = "nonstandard";
  mode.dispatchEvent(new dom.window.Event("change"));
  assert.ok(
    [...d.querySelectorAll("pre")].some(
      (p) => p.textContent === '"Modbus" -port:502',
    ),
  );
  dom.window.close();
});

test("UDP request evidence requires an explicit payload and never sends TLS options", async () => {
  const record = {
    ...endpoint,
    transport: "udp",
    scheme: "",
    service_type: "coap",
    fetch_actions: ["udp"],
    default_actions: [],
  };
  const { dom, calls, api } = setup(
    { ["/endpoints/" + endpoint.id]: { endpoint: record } },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  const udp = d.querySelector("[value=udp]");
  udp.checked = true;
  udp.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  button(d, "Fetch selected actions").click();
  await settle();
  assert.equal(
    calls.some((c) => c.url.endsWith("/fetch")),
    false,
  );
  assert.match(d.body.textContent, /UDP capture requires/);
  d.querySelector("[name=udp_payload]").value = "40010001";
  button(d, "Fetch selected actions").click();
  await settle();
  assert.deepEqual(calls.find((c) => c.url.endsWith("/fetch")).body.options, {
    udp: { payload: "40010001", encoding: "hex", timeout_seconds: 3 },
  });
  dom.window.close();
});

test("TCP editor rejects malformed hex, oversized payloads and timeout bounds before queuing", async () => {
  const record = {
    ...endpoint,
    scheme: "",
    fetch_actions: ["tcp"],
    default_actions: ["tcp"],
  };
  const { dom, calls, api } = setup(
    { ["/endpoints/" + endpoint.id]: { endpoint: record } },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  for (const [payload, timeout, pattern] of [
    ["abc", "3", /complete byte pairs/],
    ["aa".repeat(4097), "3", /exceeds 4 KiB/],
    ["", "11", /1 to 10 seconds/],
  ]) {
    d.querySelector("[name=tcp_payload]").value = payload;
    d.querySelector("[name=tcp_timeout]").value = timeout;
    button(d, "Fetch selected actions").click();
    await settle();
    assert.match(d.body.textContent, pattern);
  }
  assert.equal(
    calls.some((c) => c.url.endsWith("/fetch")),
    false,
  );
  dom.window.close();
});

test("wire transcripts enforce their inline byte limit before downloading", async () => {
  const { dom, api } = setup(
    {
      ["/endpoints/" + endpoint.id]: {
        endpoint,
        artifacts: [{ kind: "wire-transcript", url: "/artifacts/wire" }],
      },
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  let read = false;
  const oldFetch = dom.window.fetch;
  dom.window.fetch = async (url, opts) =>
    url.endsWith("/artifacts/wire")
      ? {
          ok: true,
          headers: { get: () => String(262145) },
          text: async () => {
            read = true;
            return "oversized";
          },
        }
      : oldFetch(url, opts);
  button(d, "Inspect transcript").click();
  await settle();
  assert.equal(read, false);
  assert.match(d.body.textContent, /256 KiB inline limit/);
  dom.window.close();
});

test("HTTP interfaces on arbitrary ports expose screenshots while binary TCP profiles do not", async () => {
  for (const http of [true, false]) {
    const record = {
      ...endpoint,
      scheme: http ? "http" : "",
      port: 32349,
      fetch_actions: http ? ["http", "screenshot", "tcp"] : ["tcp"],
      default_actions: [],
    };
    const { dom, api } = setup(
      {
        "/catalog": {
          ...catalog,
          tools: { curl: true, httpx: true, gowitness: true },
        },
        ["/endpoints/" + endpoint.id]: { endpoint: record },
      },
      "http://localhost/assets/?endpoint=" + endpoint.id,
    );
    const d = dom.window.document;
    await api.render(d.querySelector("#page-root"), "assets");
    assert.equal(
      d.querySelector("[name=actions][value=screenshot]").disabled,
      !http,
    );
    assert.equal(d.querySelector("[name=actions][value=tcp]").disabled, false);
    dom.window.close();
  }
});

test("native VNC screenshot is available without gowitness", async () => {
  const record = {...endpoint, scheme: "", service_type: "vnc", port: 33390,
    fetch_actions: ["screenshot", "tcp"], default_actions: ["screenshot"]};
  const {dom, api} = setup({["/endpoints/" + endpoint.id]: {endpoint: record}},
    "http://localhost/assets/?endpoint=" + endpoint.id);
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  const screenshot = d.querySelector("[name=actions][value=screenshot]");
  assert.equal(screenshot.disabled, false);
  assert.equal(screenshot.checked, true);
  assert.match(screenshot.parentElement.textContent, /VNC/);
  dom.window.close();
});

test("automatic forms never expose manual payloads or allow generic UDP requests", async () => {
  for (const page of ["lookups", "monitoring", "assets"]) {
    const { dom, api } = setup();
    const d = dom.window.document;
    await api.render(d.querySelector("#page-root"), page);
    assert.equal(
      d.querySelector("[name=tcp_payload], [name=udp_payload]"),
      null,
      page,
    );
    for (const control of d.querySelectorAll("[name=actions][value=udp]"))
      assert.equal(control.disabled, true, page);
    dom.window.close();
  }
});

test("wire transcript reader cancels an oversized streamed response", async () => {
  const { dom, api } = setup(
    {
      ["/endpoints/" + endpoint.id]: {
        endpoint,
        artifacts: [{ kind: "wire-transcript", url: "/artifacts/wire" }],
      },
    },
    "http://localhost/assets/?endpoint=" + endpoint.id,
  );
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "assets");
  let reads = 0,
    canceled = false,
    released = false;
  dom.window.fetch = async () => ({
    ok: true,
    headers: { get: () => null },
    body: {
      getReader: () => ({
        read: async () => {
          reads++;
          return { done: false, value: new Uint8Array(150000) };
        },
        cancel: async () => {
          canceled = true;
        },
        releaseLock: () => {
          released = true;
        },
      }),
    },
  });
  button(d, "Inspect transcript").click();
  await settle();
  assert.equal(reads, 2);
  assert.equal(canceled, true);
  assert.equal(released, true);
  assert.match(d.body.textContent, /256 KiB inline limit/);
  dom.window.close();
});

test("ZoomEye scheduled lookups preserve their explicit page size when edited", async () => {
  const saved = { ...lookup, provider: "zoomeye", page_size: 17 };
  const { dom, calls, api } = setup({ "/lookups": { results: [saved] } });
  const d = dom.window.document;
  await api.render(d.querySelector("#page-root"), "lookups");
  button(d, "Edit").click();
  const pageSize = d.querySelector("[name=page_size]");
  assert.ok(pageSize);
  assert.equal(pageSize.value, "17");
  assert.equal(pageSize.disabled, false);
  assert.equal(pageSize.closest("label").hidden, false);
  pageSize.value = "23";
  button(d, "Save changes").click();
  await settle();
  assert.equal(calls.find((c) => c.url.endsWith("/save")).body.page_size, 23);
  const provider = d.querySelector("[name=provider]");
  provider.value = "netlas";
  provider.dispatchEvent(new dom.window.Event("change"));
  assert.equal(pageSize.disabled, true);
  assert.equal(pageSize.closest("label").hidden, true);
  dom.window.close();
});

test("catalog makes reference ports and optional restrictions explicit", async () => {
  const { dom, api } = setup();
  await api.render(dom.window.document.querySelector("#page-root"), "catalog");
  try {
    const d=dom.window.document;
    assert.match(d.querySelector('[name=catalog_query_mode] option[value=fingerprint]').textContent,/All indexed ports/);
    assert.match(d.querySelector('#page-root').textContent,/Reference ports/);
    assert.match(d.querySelector('[name=catalog_query_mode] option[value=nonstandard]').textContent,/optional/i);
  } finally {dom.window.close();}
});

test('saved lookup catalog button uses all-port candidates and preserves query on unsupported provider', async () => {
  const grpc={key:'grpc',label:'gRPC',ports:[],transport:'tcp',queries:{},query_variants:{fingerprint:{shodan:{supported:true,query:'"application/grpc"'},netlas:{supported:false,query:'',reason:'No verified mapping'}}}};
  const {dom,api}=setup({'/catalog':{...catalog,results:[grpc]}},'http://localhost/lookups/?service_type=grpc&provider=shodan');
  const d=dom.window.document;await api.render(d.querySelector('#page-root'),'lookups');
  button(d,'Use catalog query').click();assert.equal(d.querySelector('[name=query]').value,'"application/grpc"');
  d.querySelector('[name=provider]').value='netlas';button(d,'Use catalog query').click();
  assert.equal(d.querySelector('[name=query]').value,'"application/grpc"');assert.match(d.body.textContent,/No verified mapping/);dom.window.close();
});

test('lookup collection modes preserve custom actions and save an adaptive preset explicitly', async () => {
  const {dom,calls,api}=setup({},'http://localhost/lookups/?service_type=postgres&query=fixture'),d=dom.window.document;
  try {
    await api.render(d.querySelector('#page-root'),'lookups');
    const mode=d.querySelector('[name=collection_mode]');assert.ok(mode);assert.equal(mode.value,'');
    assert.equal(d.querySelector('[name=auto_fetch]').disabled,false);
    mode.value='full';mode.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
    assert.equal(d.querySelector('[name=auto_fetch]').disabled,true);
    assert.ok([...d.querySelectorAll('[name=actions]')].every(x=>x.disabled));
    d.querySelector('[name=name]').value='Full collection';button(d,'Save lookup').click();await settle();
    const payload=calls.find(c=>c.url==='/api/operations/lookups'&&c.body).body;
    assert.equal(payload.collection_mode,'full');assert.equal(payload.auto_fetch,true);assert.deepEqual(payload.actions,[]);
  } finally {dom.window.close();}
});

test('editing a passive discovery lookup keeps its mode and cannot enable automatic target contact', async () => {
  const {dom,calls,api}=setup({'/lookups':{results:[{...lookup,collection_mode:'passive',auto_fetch:true}]}}),d=dom.window.document;
  try {
    await api.render(d.querySelector('#page-root'),'lookups');button(d,'Edit').click();
    assert.equal(d.querySelector('[name=collection_mode]').value,'passive');
    assert.equal(d.querySelector('[name=auto_fetch]').checked,false);assert.equal(d.querySelector('[name=auto_fetch]').disabled,true);
    button(d,'Save changes').click();await settle();
    const payload=calls.find(c=>c.url==='/api/operations/lookups/lookup-1/save').body;
    assert.equal(payload.collection_mode,'passive');assert.equal(payload.auto_fetch,false);assert.deepEqual(payload.actions,[]);
  } finally {dom.window.close();}
});

test('research lookup cannot retain a target collection preset', async () => {
  const {dom,calls,api}=setup(),d=dom.window.document;
  try {
    await api.render(d.querySelector('#page-root'),'lookups');
    const mode=d.querySelector('[name=collection_mode]');assert.ok(mode);mode.value='full';mode.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
    const provider=d.querySelector('[name=provider]');provider.value='research';provider.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
    assert.ok(['','passive'].includes(mode.value));assert.equal(mode.disabled,false);assert.equal([...mode.options].find(x=>x.value==='full').disabled,true);
    d.querySelector('[name=name]').value='Research';d.querySelector('[name=query]').value='Fixture product';button(d,'Save lookup').click();await settle();
    const payload=calls.find(c=>c.url==='/api/operations/lookups'&&c.body).body;assert.equal(payload.auto_fetch,false);assert.deepEqual(payload.actions,[]);assert.ok(['','passive'].includes(payload.collection_mode));
  } finally {dom.window.close();}
});

test('result explains native identification and limits before sending requests',async()=>{
 const record={...endpoint,service_type:'coap',transport:'udp',fetch_actions:['identity','udp'],default_actions:['identity'],native_identity:{available:true,operation:'GET /.well-known/core',limitation:'One discovery response; no resource reads'}};
 const {dom,api,calls}=setup({['/endpoints/'+endpoint.id]:{endpoint:record}},'http://localhost/assets/?endpoint='+endpoint.id);
 await api.render(dom.window.document.querySelector('#page-root'),'assets');
 assert.match(dom.window.document.body.textContent,/Protocol identification: GET \/\.well-known\/core/);
 assert.match(dom.window.document.body.textContent,/One discovery response; no resource reads/);
 assert.equal(calls.some(c=>c.url.endsWith('/fetch')),false);dom.window.close();
});

test("screenshot artifact quality is visible without hiding actual image evidence", async () => {
  const samples = [
    {metadata: {status:401,partial:true,capture_quality:{state:"http_error",reasons:[]}}, expected:/Authentication required page captured/},
    {metadata: {backend:"playwright",tls:{certificate_errors_ignored:true,certificate_validity:"unverified"}},expected:/TLS certificate validity unverified/},
    {metadata: {partial:true,title:"Checking your browser...",status:403,capture_quality:{state:"challenge",reasons:["Browser challenge title"],visual_completeness:"unassessed"}}, expected:/Browser challenge or interstitial captured/, status:/HTTP 403/},
    {metadata: {blocked_external_requests:11,partial:false}, expected:/Partial screenshot.*page resources were blocked/},
    {metadata: {partial:true,status:502,capture_quality:{state:"http_error",reasons:["HTTP response error"],visual_completeness:"unassessed"}}, expected:/HTTP error page captured/, status:/HTTP 502/},
    {metadata: {capture_quality:{state:"unassessed",reasons:[],visual_completeness:"unassessed"}}, expected:/Visual completeness unverified/},
  ];
  for (const sample of samples) {
    const {dom,api}=setup({["/endpoints/"+endpoint.id]:{endpoint,observations:[],artifacts:[{id:"image",kind:"screenshot",mime:"image/png",url:"/api/operations/artifacts/image",tool:"gowitness",size:123,metadata:sample.metadata}]}},"http://localhost/assets/?endpoint="+endpoint.id);
    const d=dom.window.document;
    await api.render(d.querySelector("#page-root"),"assets");
    assert.match(d.body.textContent,sample.expected);
    if(sample.status)assert.match(d.body.textContent,sample.status);
    assert.ok(d.querySelector('img[src$="/api/operations/artifacts/image"]'));
    assert.equal(d.querySelectorAll('img').length,1);
    assert.ok(d.querySelector('a[href$="/api/operations/artifacts/image"]'));
    assert.match(d.body.textContent,/Visual completeness unverified/);
    dom.window.close();
  }
});

test('web screenshots expose Playwright profiles and submit reviewed options only when selected', async () => {
  const record={...endpoint,address:'www.example.com',service_type:'http',fetch_actions:['http','screenshot','tcp'],default_actions:['http']};
  const {dom,calls,api}=setup({'/catalog':{...catalog,tools:{curl:true,playwright:true,gowitness:true}},['/endpoints/'+endpoint.id]:{endpoint:record}},'http://localhost/assets/?endpoint='+endpoint.id);const d=dom.window.document;
  await api.render(d.querySelector('#page-root'),'assets');
  const editor=d.querySelector('.ops-browser-editor');assert.ok(editor);assert.equal(editor.hidden,true);
  const shot=d.querySelector('[name=actions][value=screenshot]');assert.equal(shot.disabled,false);shot.click();
  assert.equal(editor.hidden,false);assert.equal(d.querySelector('[name=screenshot_backend]').value,'playwright');assert.equal(d.querySelector('[name=screenshot_profile]').value,'web_page');
  d.querySelector('[name=screenshot_wait]').value='4';d.querySelector('[name=screenshot_full_page]').checked=true;
  button(d,'Fetch selected actions').click();await settle();const post=calls.find(c=>c.url.endsWith('/fetch'));
  assert.deepEqual(post.body.options.screenshot,{backend:'playwright',profile:'web_page',wait_seconds:4,timeout_seconds:35,full_page:true});assert.equal(post.opts.headers['X-CSRFToken'],'fixture');dom.window.close();
});
test('gowitness fallback uses strict capture without implying Playwright rendering controls',async()=>{
  const record={...endpoint,service_type:'http',fetch_actions:['screenshot'],default_actions:['screenshot']};
  const {dom,calls,api}=setup({'/catalog':{...catalog,tools:{playwright:true,gowitness:true}},['/endpoints/'+endpoint.id]:{endpoint:record}},'http://localhost/assets/?endpoint='+endpoint.id);const d=dom.window.document;await api.render(d.querySelector('#page-root'),'assets');
  const backend=d.querySelector('[name=screenshot_backend]');assert.ok(backend);backend.value='gowitness';backend.dispatchEvent(new dom.window.Event('change'));
  assert.equal(d.querySelector('[name=screenshot_profile]').value,'strict');assert.equal(d.querySelector('[name=screenshot_profile]').disabled,true);assert.equal(d.querySelector('[name=screenshot_wait]').disabled,true);
  button(d,'Fetch selected actions').click();await settle();assert.deepEqual(calls.find(c=>c.url.endsWith('/fetch')).body.options.screenshot,{backend:'gowitness',profile:'strict'});dom.window.close();
});
test('manual verification is explicit and remains unavailable for native VNC',async()=>{
  for(const native of [false,true]){const record={...endpoint,service_type:native?'vnc':'http',fetch_actions:['screenshot'],default_actions:['screenshot']};const {dom,api}=setup({'/catalog':{...catalog,tools:{playwright:true,gowitness:false}},['/endpoints/'+endpoint.id]:{endpoint:record}},'http://localhost/assets/?endpoint='+endpoint.id);const d=dom.window.document;const opened=[];dom.window.LeakScopeBrowser={open:(...args)=>opened.push(args)};await api.render(d.querySelector('#page-root'),'assets');const manual=button(d,'Open verification browser');if(native){assert.equal(manual,undefined);assert.equal(d.querySelector('.ops-browser-editor'),null);}else{assert.ok(manual);assert.equal(opened.length,0);manual.click();assert.equal(opened[0][0].id,endpoint.id);assert.equal(opened[0][1].backend,'playwright');}dom.window.close();}
});

test('Playwright screenshot diagnostics display actual response status and rendering evidence',async()=>{
 const metadata={backend:'playwright',profile:'web_page',status_code:200,title:'Fixture public page',partial:true,blocked_requests:[],capture_quality:{state:'restricted',reasons:['2 JavaScript errors'],visual_completeness:'unassessed'},render:{ready_state:'complete',fonts_status:'loaded',incomplete_images:1,webgl:true},network:{bytes:12000,requests:7},errors:[{type:'TypeError'},{type:'Error'}]};const {dom,api}=setup({['/endpoints/'+endpoint.id]:{endpoint,artifacts:[{kind:'screenshot',mime:'image/png',url:'/artifact.png',metadata}]}},'http://localhost/assets/?endpoint='+endpoint.id);const d=dom.window.document;await api.render(d.querySelector('#page-root'),'assets');const quality=d.querySelector('.ops-capture-quality');assert.match(quality.textContent,/HTTP 200/);assert.match(quality.textContent,/Web page/);assert.match(quality.textContent,/Fonts: loaded/);assert.match(quality.textContent,/1 incomplete image/);assert.doesNotMatch(quality.textContent,/page resources were blocked/);dom.window.close();
});

test('lookup Run Demo is a per-run override and never saves or toggles the rule',async()=>{
 const {dom,calls,api}=setup(),d=dom.window.document;try{
  await api.render(d.querySelector('#page-root'),'lookups');const run=button(d,'Run Demo');assert.ok(run);run.click();await settle();
  const call=calls.find(c=>c.url==='/api/operations/lookups/lookup-1/run');assert.deepEqual(call.body,{demo:true});assert.equal(call.opts.headers['X-CSRFToken'],'fixture');
  assert.equal(calls.some(c=>/\/(save|toggle)$/.test(c.url)),false);
 }finally{dom.window.close();}
});
test('Netlas Demo forwards selected collections explicitly and explains the one-collection sample',async()=>{
 const {dom,calls,api}=setup({},"http://localhost/assets/?endpoint="+endpoint.id),d=dom.window.document;try{
  await api.render(d.querySelector('#page-root'),'assets');button(d,'Enrich with Netlas').click();const dialog=d.querySelector('.ops-enrichment-dialog');
  const demo=dialog.querySelector('[name=demo]');assert.ok(demo);demo.click();assert.match(dialog.textContent,/one|1/);button(dialog,'Run enrichment').click();await settle();
  const call=calls.find(c=>c.url.endsWith('/enrich'));assert.equal(call.body.demo,true);assert.deepEqual(call.body.collections,['host','dns']);
 }finally{dom.window.close();}
});


test('research schedules accept Demo while keeping target collection disabled and saved limits unchanged',async()=>{
 const {dom,calls,api}=setup(),d=dom.window.document;try{
  await api.render(d.querySelector('#page-root'),'lookups');const provider=d.querySelector('[name=provider]');provider.value='research';provider.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  const mode=d.querySelector('[name=collection_mode]');mode.value='demo';mode.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.querySelector('[name=name]').value='Demo research';d.querySelector('[name=query]').value='Fixture product';button(d,'Save lookup').click();await settle();const body=calls.find(c=>c.url==='/api/operations/lookups'&&c.body).body;
  assert.equal(body.collection_mode,'demo');assert.equal(body.auto_fetch,false);assert.deepEqual(body.actions,[]);
 }finally{dom.window.close();}
});

test('observations preserve a search run filter and explain its zero-result output',async()=>{
 const record={id:'empty-run',provider:'shodan',state:'succeeded',query:'"flock admin"',stored:0,total:0,partial:false,error:''};
 const {dom,calls,api}=setup({'/endpoints':{results:[],count:0,page:1,pages:1,run:record}},'http://localhost/assets/?run=empty-run');const d=dom.window.document;
 try{await api.render(d.querySelector('#page-root'),'assets');const requested=new URL(calls.find(c=>c.url.includes('/endpoints?')).url,'http://localhost');assert.equal(requested.searchParams.get('run'),'empty-run');assert.match(d.querySelector('[data-run-results]').textContent,/flock admin/);assert.doesNotMatch(d.querySelector('[data-run-results]').textContent,/null/);assert.match(d.querySelector('#page-root').textContent,/This search returned no matches/);assert.equal(d.querySelector('a[href="/runs/?id=empty-run"]').textContent,'Run details');d.querySelector('[name=q]').value='test';d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await settle();assert.equal(new URL(calls.at(-1).url,'http://localhost').searchParams.get('run'),'empty-run');assert.equal(new URL(dom.window.location.href).searchParams.get('run'),'empty-run');}finally{dom.window.close()}
});
test('observation inventory opens shared inspector without leaving filters or collecting',async()=>{const {dom,calls,api}=setup({},'http://localhost/assets/?q=fixture');let selected;dom.window.LeakScopeInspector={open:ref=>selected=ref};await api.render(dom.window.document.querySelector('#page-root'),'assets');const inspect=[...dom.window.document.querySelectorAll('button')].find(x=>x.textContent==='Inspect');assert.ok(inspect);inspect.click();assert.equal(selected.type,'endpoint');assert.equal(selected.id,endpoint.id);assert.equal(new URL(dom.window.location.href).searchParams.get('q'),'fixture');assert.equal(calls.filter(c=>c.body).length,0);dom.window.close()});

// ---- Observations on the shared list toolbar -------------------------------------------------
async function observations({ url = "http://localhost/assets/", rows = [endpoint], handler = null } = {}) {
  const data = { results: rows, count: rows.length, page: 1, pages: 1 };
  const { dom, calls, api } = setup({ "/endpoints": data }, url);
  const requests = [];
  const original = dom.window.fetch;
  dom.window.fetch = async (target, opts = {}) => {
    const parsed = new URL(target, "http://localhost");
    if (parsed.pathname === "/api/operations/endpoints") {
      requests.push(parsed);
      if (handler) { calls.push({ url: target, opts }); return { ok: true, json: async () => handler(parsed, requests.length) }; }
    }
    return original(target, opts);
  };
  const d = dom.window.document, root = d.querySelector("#page-root");
  await api.render(root, "assets");
  return { dom, d, root, calls, requests, w: dom.window };
}
const many = (n) => Array.from({ length: n }, (_, i) => ({ ...endpoint, id: "id-" + i, address: "192.0.2." + i, title: "row-" + i }));
const boxes = (root) => [...root.querySelectorAll("tbody input[type=checkbox]")];
const tickBox = (s, box) => { box.checked = true; box.dispatchEvent(new s.w.Event("change", { bubbles: true })); };

test("observations shows one toolbar, no Apply button and no always-on bulk panel", async () => {
  const s = await observations();
  try {
    assert.equal(s.root.querySelectorAll(".ls-listbar").length, 1);
    assert.equal(s.root.querySelector("button[type=submit]"), null);
    assert.doesNotMatch(s.root.textContent, /Selected endpoint actions/);
    assert.equal(s.root.querySelector(".ls-selection").hidden, true);
    assert.ok(s.root.querySelector(".ls-listbar").compareDocumentPosition(s.root.querySelector("table")) & 4);
    const row = s.root.querySelector(".ls-listbar-row");
    assert.deepEqual([...row.querySelectorAll("input,select")].map((n) => n.name), ["q", "type", "provider"]);
    assert.ok(s.root.querySelector(".ls-filters-panel").querySelector("[name=tag]"));
    assert.match(s.root.querySelector(".ls-count").textContent, /^1 matching endpoint/);
    assert.ok(s.root.querySelector(".ls-pager").textContent.includes("Page 1 of 1"));
  } finally { s.dom.window.close(); }
});

test("selecting rows reveals the bar and fetch actions sit in its popover", async () => {
  const s = await observations({ rows: many(3) });
  try {
    tickBox(s, boxes(s.root)[0]); tickBox(s, boxes(s.root)[1]);
    assert.equal(s.root.querySelector(".ls-selection").hidden, false);
    assert.equal(s.root.querySelector(".ls-selection-count").textContent, "2 selected · up to 10 per batch");
    const pop = s.root.querySelector(".ls-selection details.ls-popover");
    assert.match(pop.querySelector("summary").textContent, /Fetch evidence/);
    assert.ok(pop.querySelectorAll("input[type=checkbox]").length >= 1);
    assert.ok([...pop.querySelectorAll("button")].some((b) => b.textContent === "Fetch selected actions"));
    const labels = [...s.root.querySelectorAll(".ls-selection > button")].map((b) => b.textContent);
    assert.deepEqual(labels, ["Find similar", "Ask", "Clear"]);
    [...s.root.querySelectorAll(".ls-selection > button")].pop().click();
    assert.equal(s.root.querySelector(".ls-selection").hidden, true);
    assert.equal(boxes(s.root).filter((b) => b.checked).length, 0);
    assert.equal(s.calls.filter((c) => c.opts?.method === "POST").length, 0);
  } finally { s.dom.window.close(); }
});

test("an eleventh row is refused and the bar still reads ten", async () => {
  const s = await observations({ rows: many(11) });
  try {
    const all = boxes(s.root);
    all.forEach((box) => tickBox(s, box));
    assert.equal(all[10].checked, false);
    assert.match(s.root.querySelector(".ls-selection-count").textContent, /^10 selected/);
    assert.match(s.root.textContent, /Select at most 10 endpoints/);
  } finally { s.dom.window.close(); }
});

test("changing a filter clears the selection and reloads once", async () => {
  const s = await observations({ rows: many(2) });
  try {
    tickBox(s, boxes(s.root)[0]);
    const type = s.root.querySelector("[name=type]");
    type.value = "postgres"; type.dispatchEvent(new s.w.Event("change", { bubbles: true }));
    await settle();
    assert.equal(s.root.querySelector(".ls-selection").hidden, true);
    assert.equal(s.requests.length, 2);
    assert.equal(s.requests[1].searchParams.get("type"), "postgres");
    assert.equal(s.requests[1].searchParams.get("page"), "1");
    assert.deepEqual([...s.root.querySelectorAll(".ls-chip")].map((c) => c.textContent), ["Technology: PostgreSQL"]);
  } finally { s.dom.window.close(); }
});

test("fast typing sends one request, a stale response is dropped and focus stays in search", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const reply = (title) => ({ results: [{ ...endpoint, id: title, title }], count: 1, page: 1, pages: 1 });
  const s = await observations({ handler: (u, n) => n === 1 ? reply("first") : n === 2 ? slow.then(() => reply("stale-row")) : reply("redis-row") });
  try {
    const q = s.root.querySelector("[name=q]");
    q.focus();
    q.value = "r"; q.dispatchEvent(new s.w.Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 340));
    assert.equal(s.requests.length, 2);
    for (const value of ["re", "red", "redis"]) { q.value = value; q.dispatchEvent(new s.w.Event("input", { bubbles: true })); await new Promise((r) => setTimeout(r, 40)); }
    await new Promise((r) => setTimeout(r, 340));
    assert.equal(s.requests.length, 3);
    assert.equal(s.requests[2].searchParams.get("q"), "redis");
    release(); await settle();
    assert.match(s.root.querySelector("table").textContent, /redis-row/);
    assert.doesNotMatch(s.root.textContent, /stale-row/);
    assert.equal(s.d.activeElement, q);
  } finally { s.dom.window.close(); }
});

test("filters arriving in the URL without an inline control become removable chips", async () => {
  const s = await observations({ url: "http://localhost/assets/?organization_id=7&tag=prod&country=DE" });
  try {
    assert.equal(s.requests[0].searchParams.get("organization_id"), "7");
    const chips = () => [...s.root.querySelectorAll(".ls-chip")].map((c) => c.textContent);
    assert.deepEqual(chips(), ["Tag: prod", "Scope · Organization: 7", "Scope · Country: DE"]);
    assert.equal(s.root.querySelector(".ls-filters-toggle").textContent, "Filters · 1");
    [...s.root.querySelectorAll(".ls-chip")][1].click();
    await settle();
    assert.equal(s.requests.at(-1).searchParams.get("organization_id"), null);
    assert.equal(s.requests.at(-1).searchParams.get("country"), "DE");
    assert.deepEqual(chips(), ["Tag: prod", "Scope · Country: DE"]);
    assert.equal(new URL(s.w.location.href).searchParams.get("organization_id"), null);
  } finally { s.dom.window.close(); }
});

test("each row has Inspect inline and the rest in a menu", async () => {
  const s = await observations();
  try {
    s.w.LeakScopeInspector = { open() {} };
    s.root.querySelector("[name=provider]").dispatchEvent(new s.w.Event("change", { bubbles: true }));
    await settle();
    const cell = s.root.querySelector("tbody tr td:last-child .ls-rowactions");
    assert.deepEqual([...cell.children].map((n) => n.matches("details") ? "menu" : n.textContent), ["Inspect", "menu"]);
    const menu = cell.querySelector("details.ls-rowmenu");
    assert.equal(menu.querySelector("summary").getAttribute("aria-label"), "More actions");
    assert.deepEqual([...menu.querySelectorAll(".ls-menuitem")].map((n) => n.textContent), ["Add to organization", "Create discovery preset", "Open full detail →"]);
    assert.equal(menu.querySelector("a.ls-menuitem").getAttribute("href"), "/assets/?endpoint=" + endpoint.id);
  } finally { s.dom.window.close(); }
});

test("the observations table is the page's main content and is never folded away", async () => {
  const s = await observations({ rows: many(25) });
  try {
    const grid = s.root.querySelector("table");
    assert.equal(grid.dataset.collapsible, "false");
    assert.equal(grid.closest("details"), null);
  } finally { s.dom.window.close(); }
});

test("the pager cannot be pressed twice during a load and never leaves the last page", async () => {
  let release; const slow = new Promise((resolve) => { release = resolve; });
  const reply = (page) => ({ results: [{ ...endpoint, id: "p" + page, title: "page-" + page }], count: 2, page, pages: 2 });
  const s = await observations({ handler: (u, n) => n === 1 ? reply(1) : slow.then(() => reply(2)) });
  try {
    const next = () => [...s.root.querySelectorAll(".ls-pager button")].find((b) => b.textContent.startsWith("Next"));
    next().click();
    assert.equal(next().disabled, true);
    next().click();
    release(); await settle();
    assert.equal(s.requests.length, 2);
    assert.match(s.root.querySelector("table").textContent, /page-2/);
    assert.ok(s.root.querySelector(".ls-pager").textContent.includes("Page 2 of 2"));
  } finally { s.dom.window.close(); }
});

test("a row menu positions itself outside the table's scroll box", async () => {
  const s = await observations();
  try {
    const menu = s.root.querySelector("details.ls-rowmenu");
    menu.open = true; menu.dispatchEvent(new s.w.Event("toggle"));
    assert.equal(menu.querySelector(".ls-popover-body").style.position, "fixed");
  } finally { s.dom.window.close(); }
});

test("the observations table is marked to fit its width and every cell names its column", async () => {
  const s = await observations();
  try {
    const grid = s.root.querySelector("table");
    assert.ok(grid.classList.contains("ls-fit"));
    assert.deepEqual([...grid.querySelectorAll("tbody tr:first-child td")].map((cell) => cell.dataset.label), ["Select", "Endpoint", "Technology", "Tags", "Observations", "Last seen", "Actions"]);
  } finally { s.dom.window.close(); }
});
