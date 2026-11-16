const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const base = path.resolve(__dirname, "../../leakscope_app/static/js/leakscope");
const catalog = { results: [{ key: "modbus", label: "Modbus TCP", transport: "tcp", ports: [502] }], tools: {}, providers: {} };
const endpoint = { id: "ep-1", address: "192.0.2.5", port: 1502, transport: "tcp", scheme: "", service_type: "modbus", title: "PLC", tags: [], observation_count: 1, fetch_actions: [] };
const org = { id: "org-1", kind: "organization", value: "Example Research", label: "Example Research", attributes: {}, status: "active" };
const detail = { organization: org, domains: [{ name: "example.com", node_id: "domain-1", source: "user_scope" }],
  members: [{ id: "member-1", kind: "result", value: "http:42", label: "Saved finding", reference: { type: "http", id: 42 }, url: "/findings/http/42/" }] };
function setup(routes = {}, url = "http://localhost/assets/", width = 1440) {
  const dom = new JSDOM('<meta name="csrf-token" content="fixture"><div id="root"></div>', { url, runScripts: "outside-only" });
  Object.defineProperty(dom.window, "innerWidth", { value: width });
  Object.defineProperty(dom.window.document, "visibilityState", { value: "visible", configurable: true });
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new dom.window.Event("close")); };
  const calls = [], timers = [];
  dom.window.setTimeout = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
  dom.window.clearTimeout = () => {};
  const defaults = {
    "/api/operations/catalog": catalog,
    "/api/operations/endpoints": { results: [endpoint], count: 230, page: 1, pages: 8 },
    "/api/operations/endpoints/ep-1": { endpoint, observations: [], artifacts: [] },
    "/api/operations/watches": { results: [] },
    "/api/operations/stream": { mode: "builtin", enabled: true, state: "connected", received: 200, matched: 3, dropped: 0, lag_seconds: 1,
      recent_matches: [
        { id: "m1", certificate_id: "c1", names: ["a.example.com"], watch_name: "Org", collected_at: "2026-11-09T11:00:00Z" },
        { id: "m2", certificate_id: "c2", names: ["b.example.com"], watch_name: "Org", collected_at: "2026-11-09T10:00:00Z" },
        { id: "m3", certificate_id: "c3", names: ["old.example.com"], watch_name: "Org", collected_at: null },
      ] },
    "/api/operations/analysis": { counts: { endpoints: 1, observations: 2 }, by_provider: [{ label: "netlas", count: 2 }],
      by_service: [{ label: "Modbus TCP", count: 1 }], by_category: [{ label: "ot", count: 1 }], by_tag: [{ label: "review", count: 1 }],
      timeline: [{ date: "2026-11-01", count: 1 }, { date: "2026-11-03", count: 1 }], recent_changes: [], relations: [] },
    "/api/assets/organizations/org-1": detail,
  };
  dom.window.fetch = async (url, opts = {}) => {
    const parsed = new URL(url, "http://localhost"); calls.push({ url, opts, body: opts.body && JSON.parse(opts.body) });
    let value = routes[parsed.pathname] ?? defaults[parsed.pathname] ?? {};
    if (typeof value === "function") value = await value(parsed, opts);
    return { ok: true, json: async () => value, text: async () => typeof value === "string" ? value : JSON.stringify(value), headers: { get: () => null } };
  };
  for (const filename of ["utils.js", "data_viewer.js", "visualizations.js", "list_toolbar.js", "operations.js", "relationship_map.js", "relationships.js"])
    dom.window.eval(fs.readFileSync(path.join(base, filename), "utf8"));
  return { dom, calls, timers, root: dom.window.document.querySelector("#root"), ops: dom.window.LeakScopeOperations, assets: dom.window.LeakScopeAssets };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const button = (root, text) => [...root.querySelectorAll("button")].find(item => item.textContent === text);

test("observation insights filter saved endpoints through existing local API without provider spending", async () => {
  const { dom, calls, root, ops } = setup({}, "http://localhost/assets/?run=lookup-1&tag=review");
  await ops.render(root, "assets");
  assert.ok(root.querySelector('[data-viz-id="assets-insights"]'));
  assert.match(root.textContent, /1 of 230 matching endpoints/);
  root.querySelector('[data-viz-key="modbus"]').click(); await settle();
  const query = new URL(calls.at(-1).url, "http://localhost").searchParams;
  assert.equal(query.get("type"), "modbus"); assert.equal(query.get("tag"), "review"); assert.equal(query.get("run"), "lookup-1");
  assert.equal(root.querySelector('select[name=type]').value, "modbus");
  assert.equal(calls.some(call => call.body), false);
  dom.window.close();
});

test("empty observation charts remain accurate and mobile insights begin collapsed", async () => {
  const { dom, root, ops } = setup({ "/api/operations/endpoints": { results: [], count: 0, page: 1, pages: 1 } }, undefined, 390);
  await ops.render(root, "assets");
  const chart = root.querySelector('[data-viz-id="assets-insights"]'); assert.ok(chart);
  assert.equal(chart.open, false); assert.match(chart.textContent, /0 of 0 matching endpoints/);
  assert.equal(root.querySelectorAll(".ls-viz-bar-row").length, 0);
  assert.match(root.textContent, /No observations match/);
  dom.window.close();
});

test("unclassified endpoints are inspected locally instead of silently clearing the technology filter", async () => {
  const { dom, calls, root, ops } = setup({ "/api/operations/endpoints": { results: [{ ...endpoint, service_type: "" }], count: 1, page: 1, pages: 1 } });
  await ops.render(root, "assets"); const before = calls.length;
  root.querySelector('[data-viz-key=""]').click(); await settle();
  assert.equal(calls.length, before);
  assert.ok(root.querySelector('a[href="/assets/?endpoint=ep-1"]'));
  dom.window.close();
});

test("monitoring charts use latest saved match records separately from persisted cumulative counters", async () => {
  const { dom, calls, root, ops } = setup({}, "http://localhost/monitoring/");
  await ops.render(root, "monitoring");
  const chart = root.querySelector('[data-viz-id="monitoring-matches"]'); assert.ok(chart);
  assert.match(chart.textContent, /3 latest persisted watch-match records/);
  assert.match(chart.textContent, /1.*without.*date/i);
  const points = chart.querySelectorAll(".ls-viz-time-point"); assert.equal(points.length, 1);
  assert.match(points[0].getAttribute("aria-label"), /2 watch-match records/);
  assert.match(root.textContent, /persisted cumulative counters/i);
  assert.doesNotMatch(root.textContent, /reset on.*restart/i);
  assert.equal([...root.querySelector(".ops-stream-health").childNodes].some(node => node.nodeType === 3 && node.textContent.trim() === "null"), false);
  assert.doesNotMatch(root.textContent, /simulated|demo streaming/i);
  assert.equal(calls.some(call => call.body), false);
  dom.window.close();
});

test("monitoring retains five-second visible polling and stops polling when page is disposed", async () => {
  let version = 0;
  const { dom, calls, timers, root, ops } = setup({ "/api/operations/stream": () => ({ state: "connected", received: ++version, matched: 0, recent_matches: [] }) }, "http://localhost/monitoring/");
  await ops.render(root, "monitoring");
  const poll = timers.find(timer => timer.delay === 5000); assert.ok(poll);
  await poll.callback();
  assert.equal(calls.filter(call => new URL(call.url, "http://localhost").pathname === "/api/operations/stream").length, 2);
  assert.equal(root.querySelectorAll(".ls-viz-time-point").length, 0);
  await ops.render(root, "assets");
  const count = calls.length; await poll.callback(); assert.equal(calls.length, count);
  dom.window.close();
});

test("analysis uses real dated series and interactive breakdowns without inventing filters", async () => {
  const { dom, calls, root, ops } = setup({}, "http://localhost/analysis/");
  await ops.render(root, "analysis");
  assert.equal(root.querySelectorAll(".ls-viz-time-point").length, 2);
  assert.equal(root.querySelector('[data-date="2026-11-02"]'), null);
  const source = root.querySelector('[data-viz-key="netlas"]'); assert.ok(source); source.click();
  assert.match(root.textContent, /2 observations/);
  assert.match(root.textContent, /collection dates/i);
  assert.equal(calls.length, 1); assert.equal(calls.some(call => call.body), false);
  dom.window.close();
});

test("organization details show direct map alongside existing scope, saved results and lifecycle actions", async () => {
  const { dom, calls, root, assets } = setup({}, "http://localhost/organizations/?id=org-1", 390);
  await assets.render(root, "organizations");
  const map = root.querySelector('[data-viz-id="organization:org-1:connections"]'); assert.ok(map); assert.equal(map.open, false);
  assert.equal(map.querySelectorAll(".ls-viz-map-node").length, 3);
  map.querySelector('[data-viz-node="domain-1"]').click();
  assert.match(map.querySelector(".ls-viz-readout").textContent, /User-supplied scope/);
  assert.ok(root.querySelector('textarea[name=add_domains]'));
  for (const label of ["Merge organization", "Ignore / Archive", "Delete organization"]) assert.ok(button(root, label));
  assert.ok(root.querySelector(".data-viewer"));
  assert.equal(calls.length, 1); assert.equal(calls.some(call => call.body), false);
  dom.window.close();
});

test("transcript formatting preserves original JSON text including whitespace and large integers", async () => {
  const original = ' {\n  "counter" : 9007199254740993, "response": { "ok": true }\n }  \n';
  const { dom, root, ops } = setup({
    "/api/operations/endpoints/ep-1": { endpoint, observations: [], artifacts: [{ id: "wire", kind: "wire-transcript", url: "/api/operations/artifacts/wire", metadata: {} }] },
    "/api/operations/artifacts/wire": original,
  }, "http://localhost/assets/?endpoint=ep-1");
  await ops.render(root, "assets"); button(root, "Inspect transcript").click(); await settle();
  const transcript = root.querySelector(".ops-transcript .data-viewer"); assert.ok(transcript);
  transcript.querySelector('[data-viewer-mode="raw"]').click();
  const value = JSON.parse(transcript.querySelector(".data-raw").textContent);
  assert.equal(value, original); assert.match(value, /9007199254740993/);
  dom.window.close();
});

test("plain wire text stays a literal code transcript", async () => {
  const original = "<= Recv 5 bytes\nHELLO";
  const { dom, root, ops } = setup({
    "/api/operations/endpoints/ep-1": { endpoint, observations: [], artifacts: [{ id: "wire", kind: "wire-transcript", url: "/api/operations/artifacts/wire" }] },
    "/api/operations/artifacts/wire": original,
  }, "http://localhost/assets/?endpoint=ep-1");
  await ops.render(root, "assets"); button(root, "Inspect transcript").click(); await settle();
  assert.equal(root.querySelector(".ops-transcript pre").textContent, original);
  assert.equal(root.querySelector(".ops-transcript .data-viewer"), null);
  dom.window.close();
});
