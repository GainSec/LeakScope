const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const source = path.resolve(__dirname, "../../leakscope_app/static/js/leakscope/visualizations.js");

function setup() {
  const dom = new JSDOM("<!doctype html><main></main>", {
    url: "http://localhost/assets/", runScripts: "outside-only",
  });
  // The first run intentionally verifies that the approved component is not yet implemented.
  if (fs.existsSync(source)) dom.window.eval(fs.readFileSync(source, "utf8"));
  return { dom, api: dom.window.LeakScopeViz, root: dom.window.document.querySelector("main") };
}
const flush = () => new Promise(resolve => setTimeout(resolve, 10));

test("shared visualization API is available without external dependencies", () => {
  const { dom, api } = setup();
  assert.ok(api, "LeakScopeViz must be installed");
  for (const name of ["disclosure", "bars", "timeline", "endpointInsights", "organizationMap"])
    assert.equal(typeof api[name], "function", name);
  dom.window.close();
});

test("disclosures preserve explicit collapse choices by stable ID", async () => {
  const { dom, api, root } = setup();
  const first = api.disclosure({ id: "assets-insights", title: "Insights", summary: "2 records", open: true });
  root.append(first);
  assert.equal(first.open, true);
  await flush(); first.open = false; await flush();
  const replacement = api.disclosure({ id: "assets-insights", title: "Insights", open: true });
  assert.equal(replacement.open, false);
  const unrelated = api.disclosure({ id: "different", title: "Other", open: true });
  assert.equal(unrelated.open, true);
  dom.window.close();
});

test("unavailable storage does not prevent accessible disclosures", () => {
  const { dom, api } = setup();
  Object.defineProperty(dom.window, "localStorage", { get() { throw new Error("Blocked"); } });
  const disclosure = api.disclosure({ id: "blocked", title: "Evidence", open: false });
  assert.equal(disclosure.open, false);
  assert.equal(disclosure.querySelector("summary").textContent, "Evidence");
  dom.window.close();
});

test("bars retain exact counts including zero, use native controls and invoke callbacks", () => {
  const { dom, api, root } = setup(); let selected;
  const values = [{ key: "grpc", label: "gRPC", count: 0 }, { key: "bacnet", label: "BACnet/IP", count: 1250 }];
  root.append(api.bars({ title: "Technologies", rows: values, unit: "endpoints", onSelect: row => selected = row.key }));
  const controls = root.querySelectorAll("button.ls-viz-bar-row");
  assert.equal(controls.length, 2);
  assert.match(controls[0].getAttribute("aria-label"), /0 endpoints/);
  assert.match(controls[1].textContent, /1,250/);
  controls[1].click(); assert.equal(selected, "bacnet");
  assert.equal(controls[1].getAttribute("aria-pressed"), "true");
  assert.deepEqual(values.map(row => row.count), [0, 1250]);
  dom.window.close();
});

test("bar labels are text and unsafe links are not created", () => {
  const { dom, api, root } = setup();
  root.append(api.bars({ title: "<script>alert(1)</script>", rows: [
    { label: "<img src=x onerror=alert(1)>", count: 2, href: "javascript:alert(1)" },
    { label: "Saved source", count: 1, href: "/assets/?provider=shodan" },
  ] }));
  assert.equal(root.querySelector("img,script"), null);
  assert.equal(root.querySelector('a[href^="javascript:"]'), null);
  assert.ok(root.querySelector('a[href="/assets/?provider=shodan"]'));
  assert.match(root.textContent, /<img src=x/);
  dom.window.close();
});

test("large bar series use bounded initial DOM with explicit more controls", () => {
  const { dom, api, root } = setup();
  root.append(api.bars({ rows: Array.from({ length: 200 }, (_, i) => ({ key: i, label: "Type " + i, count: i })) }));
  assert.ok(root.querySelectorAll(".ls-viz-bar-row").length <= 30);
  const more = root.querySelector("[data-viz-more]"); assert.ok(more);
  more.click(); assert.ok(root.querySelectorAll(".ls-viz-bar-row").length > 25);
  dom.window.close();
});

test("empty and invalid chart rows do not fabricate observations", () => {
  const { dom, api, root } = setup();
  root.append(api.bars({ rows: [{ label: "Unknown", count: null }, { label: "Invalid", count: -2 }] }));
  assert.equal(root.querySelectorAll(".ls-viz-bar-row").length, 0);
  assert.match(root.textContent, /No saved data/);
  root.append(api.timeline({ rows: [] }));
  assert.equal(root.querySelectorAll(".ls-viz-time-point").length, 0);
  dom.window.close();
});

test("timelines preserve actual dates and zero counts without inventing missing dates", () => {
  const { dom, api, root } = setup(); let chosen;
  const rows = [{ date: "2026-11-03", count: 5 }, { date: "2026-11-01", count: 0 }, { date: "2026-02-30", count: 99 }];
  root.append(api.timeline({ title: "Collection history", rows, coverageLabel: "Recorded collection dates", onSelect: row => chosen = row.date }));
  const controls = root.querySelectorAll(".ls-viz-time-point");
  assert.equal(controls.length, 2);
  assert.deepEqual([...controls].map(node => node.dataset.date), ["2026-11-01", "2026-11-03"]);
  assert.equal(root.querySelector('[data-date="2026-11-02"]'), null);
  controls[0].click(); assert.equal(chosen, "2026-11-01");
  assert.match(root.querySelector(".ls-viz-readout").textContent, /0 observations/);
  assert.equal(rows[0].date, "2026-11-03");
  dom.window.close();
});

test("endpoint insights label current page and route actual types and endpoints through callbacks", () => {
  const { dom, api, root } = setup(); let type, endpoint;
  const rows = [
    { id: "ep-1", address: "192.0.2.1", port: 502, service_type: "modbus", last_seen: "2026-11-09T11:00:00Z" },
    { id: "ep-2", address: "192.0.2.2", port: 47808, service_type: "bacnet", last_seen: "2026-11-01T10:00:00Z" },
  ];
  root.append(api.endpointInsights({ id: "page", rows, total: 230, page: 2,
    now: Date.parse("2026-11-09T12:00:00Z"), catalog: [{ key: "modbus", label: "Modbus TCP" }],
    onType: key => type = key, onEndpoint: row => endpoint = row.id,
  }));
  assert.match(root.textContent, /Page 2/); assert.match(root.textContent, /2 of 230 matching endpoints/);
  root.querySelector('[data-viz-key="modbus"]').click(); assert.equal(type, "modbus");
  root.querySelector('[data-viz-key="last-day"]').click();
  root.querySelector('[data-viz-endpoint="ep-1"]').click(); assert.equal(endpoint, "ep-1");
  assert.match(root.textContent, /Last seen/); assert.doesNotMatch(root.textContent, /live verified/i);
  dom.window.close();
});

test("unknown and future last-seen values remain separate from current observations", () => {
  const { dom, api, root } = setup();
  root.append(api.endpointInsights({ rows: [
    { id: "ep-1", service_type: "", last_seen: "invalid" },
    { id: "ep-2", service_type: "", last_seen: "2027-01-01T00:00:00Z" },
  ], total: 2, now: Date.parse("2026-11-09T12:00:00Z") }));
  const unknown = root.querySelector('[data-viz-key="unknown-age"]');
  assert.match(unknown.getAttribute("aria-label"), /2 endpoints/);
  assert.match(root.querySelector('[data-viz-key="last-day"]').getAttribute("aria-label"), /0 endpoints/);
  dom.window.close();
});

test("organization map shows only direct saved associations and retains provenance on selection", () => {
  const { dom, api, root } = setup(); let selected;
  root.append(api.organizationMap({ organization: { id: "org", label: "Example Research" },
    domains: [{ name: "example.com", node_id: "d1", source: "user_scope" }],
    members: [{ id: "m1", kind: "domain", value: "vendor.example", label: "Vendor", relationship: "uses_service" }],
    onSelect: item => selected = item,
  }));
  assert.equal(root.querySelectorAll(".ls-viz-map-node").length, 3);
  assert.match(root.textContent, /do not establish corporate ownership/);
  root.querySelector('[data-viz-node="d1"]').click();
  assert.equal(selected.value, "example.com"); assert.equal(selected.relation, "user_scope");
  assert.match(root.querySelector(".ls-viz-readout").textContent, /User-supplied scope/);
  root.querySelector('[data-viz-node="m1"]').click();
  assert.equal(selected.relation, "uses_service");
  assert.match(root.querySelector(".ls-viz-readout").textContent, /uses service/i);
  dom.window.close();
});

test("organization maps keep untrusted names inert and bound initial associations", () => {
  const { dom, api, root } = setup();
  root.append(api.organizationMap({ organization: { id: "org", label: "<svg onload=alert(1)>" },
    domains: Array.from({ length: 80 }, (_, i) => ({ name: "d" + i + ".example", node_id: "d" + i })),
    members: [{ id: "bad", label: "<img src=x>", url: "javascript:alert(1)" }],
  }));
  assert.equal(root.querySelector("svg,img"), null);
  assert.ok(root.querySelectorAll(".ls-viz-map-node").length <= 15);
  assert.match(root.textContent, /81 direct associations/);
  assert.ok(root.querySelector("[data-viz-more]"));
  dom.window.close();
});

test("empty/loading endpoint summaries make no API requests and show explicit states", () => {
  const { dom, api, root } = setup(); let requests = 0; dom.window.fetch = () => requests++;
  root.append(api.endpointInsights({ rows: [], total: 0 }), api.endpointInsights({ loading: true }));
  assert.match(root.textContent, /No saved endpoints on this page/);
  assert.match(root.textContent, /Loading saved observations/);
  assert.equal(requests, 0);
  dom.window.close();
});

test("missing endpoint totals stay explicitly unavailable rather than implying complete coverage", () => {
  const { dom, api, root } = setup();
  root.append(api.endpointInsights({ rows: [{ id: "saved", service_type: "https" }], page: 3 }));
  assert.match(root.textContent, /total unavailable/i);
  assert.doesNotMatch(root.textContent, /1 of 1 matching endpoints/);
  dom.window.close();
});
