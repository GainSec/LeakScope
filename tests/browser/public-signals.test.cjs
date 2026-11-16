const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path");
const { JSDOM } = require("jsdom");
const BASE = path.resolve(__dirname, "../../leakscope_app/static/js/leakscope");
const windows = [];
const tick = () => new Promise((r) => setTimeout(r, 30));
test.afterEach(() => windows.splice(0).forEach((d) => d.window.close()));
function setup(
  routes = {},
  url = "http://localhost/correlations/?public=1&organization_id=org1&q=tenant",
) {
  const dom = new JSDOM(
    '<meta name="csrf-token" content="fixture"><div id="root"></div>',
    { url, runScripts: "outside-only", pretendToBeVisual: true },
  );
  windows.push(dom);
  const calls = [],
    inspected = [],
    w = dom.window;
  w.fetch = async (value, options = {}) => {
    const u = new URL(value, w.location.href);
    calls.push({
      url: u,
      options,
      body: options.body ? JSON.parse(options.body) : undefined,
    });
    let dto = routes[u.pathname] || { results: [] };
    if (typeof dto === "function") dto = await dto(u, options);
    return {
      ok: !dto.error_status,
      status: dto.error_status || 200,
      json: async () => dto,
    };
  };
  w.LeakScopeInspector = { open: (ref) => inspected.push(ref) };
  for (const file of ["public_signals.js", "workspace_workflows.js"])
    if (fs.existsSync(path.join(BASE, file)))
      w.eval(fs.readFileSync(path.join(BASE, file), "utf8"));
  return { w, root: w.document.getElementById("root"), calls, inspected };
}
const button = (host, label) => {
  const n = [...host.querySelectorAll("button")].find(
    (n) => n.textContent === label,
  );
  assert.ok(n, "Missing " + label);
  return n;
};
const row = {
  kind: "entra.tenant",
  value: "<tenant-uuid>",
  endpoint_count: 0,
  domain_count: 1,
  last_seen: "2026-11-11T12:00:00Z",
  reference: { type: "evidence", id: "proof1" },
  proof_refs: [{ type: "evidence", id: "proof1" }],
  preview_payload: { evidence_id: "proof1" },
  basis: "Publisher declaration; review before inferring ownership.",
};
test("public identifier mode reads single-observation signals, scope filters and page boundaries without collection", async () => {
  const s = setup({
    "/api/workspace/public-signals": (u) => ({
      results: [row],
      total: 2,
      page: Number(u.searchParams.get("page") || 1),
      pages: 2,
      page_size: 1,
      kinds: ["entra.tenant"],
      network_contact: false,
      basis: "Saved public identifiers only",
    }),
  });
  await s.w.LeakScopeWorkspace.render(s.root, "correlations");
  const call = s.calls.find(
    (c) => c.url.pathname === "/api/workspace/public-signals",
  );
  assert.ok(call);
  assert.equal(call.url.searchParams.get("organization_id"), "org1");
  assert.equal(call.url.searchParams.get("q"), "tenant");
  const card = s.root.querySelector(".public-signal-card");
  assert.ok(card);
  assert.equal(card.open, false);
  assert.equal(card.querySelector("tenant-uuid"), null);
  assert.match(card.textContent, /1 domain/);
  button(s.root, "Load more groups").click();
  await tick();
  assert.ok(
    s.calls.some(
      (c) =>
        c.url.pathname.endsWith("/public-signals") &&
        c.url.searchParams.get("page") === "2",
    ),
  );
  assert.equal(s.calls.filter((c) => c.options.method === "POST").length, 0);
});
test("proof and safe literal previews are explicit; saving a template never runs a query", async () => {
  const query = 'http.html:"<tenant-uuid>"';
  const s = setup({
    "/api/intelligence/pivot-queries": {
      queries: {
        shodan: {
          supported: true,
          query,
          url: "https://www.shodan.io/search?query=term",
        },
        zoomeye: { supported: true, query: 'http.body="tenant"' },
        netlas: { supported: true, query: 'http.body:"tenant"' },
      },
      basis: "Indexed body literal leads",
      network_contact: false,
    },
    "/api/queries": { query: { id: 42 } },
  });
  const controller = new s.w.AbortController();
  assert.ok(s.w.LeakScopePublicSignals, "Module exported");
  const card = s.w.LeakScopePublicSignals.card(row, {
    signal: controller.signal,
  });
  s.root.append(card);
  button(card, "Inspect saved proof").click();
  assert.equal(s.inspected[0].id, "proof1");
  assert.equal(s.calls.length, 0);
  button(card, "Preview provider queries").click();
  await tick();
  assert.deepEqual(s.calls[0].body, { evidence_id: "proof1" });
  assert.equal(s.calls[0].options.headers["X-CSRFToken"], "fixture");
  assert.equal(card.querySelector("textarea").value, query);
  assert.equal(card.querySelector("textarea").readOnly, true);
  assert.equal(card.querySelector("tenant-uuid"), null);
  button(card, "Save Shodan template").click();
  await tick();
  const saved = s.calls.find((c) => c.url.pathname === "/api/queries");
  assert.equal(saved.body.query_template, query);
  assert.equal(saved.body.provider, "shodan");
  assert.equal(saved.body.active_probe_default, false);
  assert.match(saved.body.description, /proof1/);
  assert.ok(card.querySelector('a[href="/custom-queries/?id=42&demo=1"]'));
  assert.equal(
    s.calls.some((c) => c.url.pathname.endsWith("/run")),
    false,
  );
});
test("unsupported provider previews expose reason and never offer misleading save actions", async () => {
  const s = setup({
    "/api/intelligence/pivot-queries": {
      queries: {
        shodan: { supported: false, reason: "Unsupported declaration" },
      },
      basis: "No provider request",
    },
  });
  const card = s.w.LeakScopePublicSignals.card(row, {});
  s.root.append(card);
  button(card, "Preview provider queries").click();
  await tick();
  assert.match(card.textContent, /Unsupported declaration/);
  assert.equal(
    [...card.querySelectorAll("button")].some((n) =>
      /Save Shodan/.test(n.textContent),
    ),
    false,
  );
});
test("artifact-backed public signals expose the exact retained capture without fetching it automatically", () => {
  const s = setup();
  const saved = {
    ...row,
    reference: { type: "endpoint", id: "endpoint1" },
    proof_refs: [
      {
        type: "endpoint",
        id: "endpoint1",
        artifact_id: "artifact1",
        observation_id: "observation1",
      },
    ],
  };
  const card = s.w.LeakScopePublicSignals.card(saved, {});
  s.root.append(card);
  assert.ok(
    card.querySelector('a[href="/api/operations/artifacts/artifact1"]'),
  );
  assert.match(card.textContent, /observation1/);
  assert.equal(s.calls.length, 0);
});
test("switching exact hash correlations to public identifiers clears incompatible kind filters", async () => {
  const s = setup(
    {
      "/api/workspace/correlations": {
        results: [],
        kinds: ["artifact.sha256"],
        has_more: false,
      },
      "/api/workspace/public-signals": {
        results: [row],
        total: 1,
        page: 1,
        pages: 1,
        kinds: ["entra.tenant"],
      },
    },
    "http://localhost/correlations/?kind=artifact.sha256&q=tenant",
  );
  await s.w.LeakScopeWorkspace.render(s.root, "correlations");
  const mode = s.root.querySelector("[name=correlation_mode]");
  assert.equal(s.root.querySelector("[name=kind]").value, "artifact.sha256");
  mode.value = "public";
  mode.dispatchEvent(new s.w.Event("change", { bubbles: true }));
  s.root
    .querySelector("form")
    .dispatchEvent(
      new s.w.Event("submit", { bubbles: true, cancelable: true }),
    );
  await tick();
  const call = s.calls.find(
    (c) => c.url.pathname === "/api/workspace/public-signals",
  );
  assert.ok(call);
  assert.equal(call.url.searchParams.get("kind"), null);
  assert.equal(call.url.searchParams.get("q"), "tenant");
  assert.equal(s.root.querySelector("[name=kind]").value, "");
  assert.ok(s.root.querySelector(".public-signal-card"));
});
