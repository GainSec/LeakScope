const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const source = () =>
  fs.readFileSync(
    path.join(root, "leakscope_app/static/js/leakscope/intelligence.js"),
    "utf8",
  );
const settings = {
  llm: {
    configured: true,
    base_url: "http://model.local/v1",
    model: "fixture-model",
  },
  firecrawl: { configured: true },
  providers: ["shodan", "zoomeye", "netlas"],
};
const context = {
  references: [{ type: "elastic", id: 1, label: "Fixture" }],
  fields: [
    {
      id: "elastic:1:title",
      field: "title",
      label: "Title",
      value: '<img src=x onerror="window.injected=true">',
      source: "elastic:1",
    },
    {
      id: "elastic:1:port",
      field: "port",
      label: "Port",
      value: 9200,
      source: "elastic:1",
    },
  ],
  context: {
    findings: [{ reference: "elastic:1", facts: { title: "Fixture" } }],
  },
  warnings: [],
};
const suggestion = {
  id: 1,
  kind: "similar",
  title: "Fixture query",
  status: "draft",
  queries: {
    shodan: {
      query: 'http.title:"Fixture"',
      url: "https://www.shodan.io/search?query=fixture",
      supported: true,
      warnings: [],
    },
    netlas: {
      query: "",
      supported: false,
      warnings: ["Fingerprint format unavailable"],
    },
  },
  sources: [],
  rationale: "<script>window.injected=true</script>",
};
function setup(overrides = {}) {
  const dom = new JSDOM(
    '<meta name="csrf-token" content="fixture"><div id="page-root"></div>',
    { url: "http://localhost/", runScripts: "outside-only" },
  );
  const calls = [];
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new dom.window.Event("close"));
  };
  dom.window.fetch = async (url, opts = {}) => {
    calls.push({
      url,
      opts,
      body: opts.body ? JSON.parse(opts.body) : undefined,
    });
    const route = url.replace("/api/intelligence", "");
    let result =
      overrides[route] ??
      ({
        "/settings": settings,
        "/context": context,
        "/similar": { suggestion, warnings: [] },
        "/ask": {
          answer: '<img src=x onerror="window.injected=true">',
          model: "fixture-model",
          usage: {},
        },
        "/suggestions": { results: [suggestion], count: 1 },
        "/research": {
          sources: [
            {
              id: "S1",
              url: "javascript:alert(1)",
              title: "<b>source</b>",
              excerpt: "Source excerpt",
            },
          ],
          suggestions: [],
          warnings: ["Synthesis unavailable"],
        },
      }[route] || { suggestion: { ...suggestion, status: "adopted" } });
    return { ok: true, json: async () => result };
  };
  dom.window.eval(source());
  return { dom, calls, api: dom.window.LeakScopeIntelligence };
}
const settle = () => new Promise((r) => setTimeout(r, 25));
function button(doc, text) {
  return [...doc.querySelectorAll("button")].find(
    (e) => e.textContent === text,
  );
}
test("similar captures references, selected fields, scope and CSRF; previews unsupported mappings safely", async () => {
  const { dom, calls, api } = setup();
  const refs = [{ type: "elastic", id: 1 }];
  api.openSimilar(refs);
  refs[0].id = 999;
  await settle();
  const d = dom.window.document;
  assert.equal(d.querySelector("dialog img"), null);
  const checks = [...d.querySelectorAll("input[name=signals]")];
  assert.equal(checks.length, 2);
  checks[1].checked = false;
  d.querySelector("input[name=network]").value = "192.0.2.0/24";
  button(d, "Generate queries").click();
  await settle();
  const call = calls.find((c) => c.url.endsWith("/similar"));
  assert.deepEqual(call.body.references, [{ type: "elastic", id: 1 }]);
  assert.deepEqual(call.body.selected_fields, ["elastic:1:title"]);
  assert.equal(call.body.scope.network, "192.0.2.0/24");
  assert.equal(call.opts.headers["X-CSRFToken"], "fixture");
  assert.match(
    d.querySelector("dialog").textContent,
    /Fingerprint format unavailable/,
  );
  assert.equal(dom.window.injected, undefined);
  dom.window.close();
});
test("Ask sends only selected context and displays response as text", async () => {
  const { dom, calls, api } = setup();
  api.openAsk([{ type: "elastic", id: 1 }]);
  await settle();
  const d = dom.window.document;
  d.querySelector("textarea[name=question]").value = "Explain this result";
  button(d, "Ask").click();
  await settle();
  const call = calls.find((c) => c.url.endsWith("/ask"));
  assert.equal(call.body.question, "Explain this result");
  assert.equal(call.body.selected_fields.length, 2);
  assert.match(d.querySelector("dialog").textContent, /<img src=x/);
  assert.equal(d.querySelector("dialog img"), null);
  assert.equal(dom.window.injected, undefined);
  dom.window.close();
});
test("unconfigured Ask is disabled and points to server configuration", async () => {
  const { dom, calls, api } = setup({
    "/settings": { ...settings, llm: { configured: false } },
  });
  api.openAsk([{ type: "elastic", id: 1 }]);
  await settle();
  const d = dom.window.document;
  assert.equal(button(d, "Ask").disabled, true);
  assert.match(d.querySelector("dialog").textContent, /Configure integrations/);
  assert.equal(
    calls.some((c) => c.url.endsWith("/ask")),
    false,
  );
  dom.window.close();
});
test("closed dialog ignores pending responses and does not add another request", async () => {
  const { dom, calls, api } = setup();
  api.openSimilar([{ type: "elastic", id: 1 }]);
  await settle();
  const before = calls.length;
  dom.window.document.querySelector("dialog").close();
  await settle();
  assert.equal(dom.window.document.querySelector("dialog"), null);
  assert.equal(calls.length, before);
  dom.window.close();
});
test("research shows safe source links and renders inbox status actions", async () => {
  const { dom, calls, api } = setup();
  await api.renderResearch(dom.window.document.querySelector("#page-root"));
  const d = dom.window.document;
  d.querySelector("input[name=topic]").value = "Example database";
  button(d, "Discover queries").click();
  await settle();
  assert.equal(d.querySelector('a[href^="javascript:"]'), null);
  assert.match(d.body.textContent, /Synthesis unavailable/);
  assert.equal(d.querySelector("script"), null);
  button(d, "Adopt").click();
  await settle();
  assert.ok(
    calls.some(
      (c) =>
        c.url.endsWith("/suggestions/1/review") && c.body.status === "adopted",
    ),
  );
  dom.window.close();
});
async function renderApp(page, overrides = {}) {
  const template = fs
    .readFileSync(
      path.join(root, "leakscope_app/templates/workbench/app.html"),
      "utf8",
    )
    .replaceAll("{% load static %}", "")
    .replace(/\{% static '([^']+)' %\}/g, "/static/$1")
    .replace("{{ csrf_token }}", "fixture")
    .replace("{{ page|default:'overview' }}", page)
    .replace("{{ type|default:'' }}", "elastic")
    .replace("{{ target_id|default:'' }}", "1");
  const dom = new JSDOM(template, {
    url: "http://localhost/" + page + "/",
    runScripts: "outside-only",
  });
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new dom.window.Event("close"));
  };
  const calls = [];
  const find = {
    id: 1,
    type: "elastic",
    label: "Fixture",
    ip: "192.0.2.1",
    port: "9200",
    summary: "Stored",
    provider: "shodan",
    status: "new",
  };
  dom.window.fetch = async (url, opts = {}) => {
    const pathname = new URL(url, "http://localhost").pathname;
    calls.push({ url, body: opts.body ? JSON.parse(opts.body) : undefined });
    const payload =
      overrides[pathname] ||
      {
        "/api/types": { results: [{ key: "elastic", label: "Elasticsearch" }] },
        "/api/findings": { results: [find], count: 1, pages: 1 },
        "/api/findings/elastic/1": {
          finding: find,
          fields: { title: "Fixture" },
          capabilities: {},
        },
        "/api/intelligence/context": context,
        "/api/intelligence/settings": settings,
        "/api/intelligence/suggestions": { results: [], count: 0 },
      }[pathname] ||
      {};
    return { ok: true, json: async () => payload };
  };
  for (const name of ["utils.js", "intelligence.js", "app.js"])
    dom.window.eval(
      fs.readFileSync(
        path.join(root, "leakscope_app/static/js/leakscope", name),
        "utf8",
      ),
    );
  await settle();
  return { dom, calls };
}
test("finding detail exposes Find similar and Ask actions using its stable reference", async () => {
  const { dom, calls } = await renderApp("detail");
  const d = dom.window.document;
  assert.ok(button(d, "Find similar"));
  button(d, "Find similar").click();
  await settle();
  assert.deepEqual(
    calls.find((c) => c.url.endsWith("/context")).body.references,
    [{ type: "elastic", id: "1" }],
  );
  dom.window.close();
});
test("findings bulk actions keep selected references and enforce selection", async () => {
  const { dom, calls } = await renderApp("findings");
  const d = dom.window.document;
  const action = button(d, "Find similar");
  assert.equal(action.disabled, true);
  const checkbox = d.querySelector("input[data-finding-select]");
  assert.ok(checkbox);
  checkbox.checked = true;
  checkbox.dispatchEvent(new dom.window.Event("change"));
  assert.equal(action.disabled, false);
  action.click();
  await settle();
  assert.deepEqual(
    calls.find((c) => c.url.endsWith("/context")).body.references,
    [{ type: "elastic", id: 1 }],
  );
  dom.window.close();
});
test("research shell uses the dedicated renderer and navigation", async () => {
  const { dom } = await renderApp("research");
  const d = dom.window.document;
  assert.equal(d.querySelector("h1").textContent, "Research");
  assert.equal(
    d.querySelector("[data-nav=research]").getAttribute("aria-current"),
    "page",
  );
  assert.equal(
    d.querySelector("#page-root").getAttribute("aria-busy"),
    "false",
  );
  dom.window.close();
});
test("Ask on a saved suggestion sends its id and shows saved-source context", async () => {
  const { dom, calls, api } = setup({
    "/context": {
      references: [],
      fields: [],
      context: {
        findings: [],
        suggestion: {
          id: "saved-1",
          title: "Vendor fingerprint",
          sources: [{ title: "Vendor docs" }],
        },
      },
      warnings: [],
    },
  });
  api.openAsk([], { suggestion_id: "saved-1" });
  await settle();
  const d = dom.window.document;
  assert.match(d.querySelector(".intel-context").textContent, /Vendor docs/);
  d.querySelector("textarea").value = "Explain the source";
  button(d, "Ask").click();
  await settle();
  assert.equal(
    calls.find((c) => c.url.endsWith("/ask")).body.suggestion_id,
    "saved-1",
  );
  dom.window.close();
});
test("closing before context resolves discards its response", async () => {
  let resolve;
  const pending = new Promise((r) => (resolve = r));
  const { dom, calls, api } = setup({ "/context": pending });
  api.openSimilar([{ type: "elastic", id: 1 }]);
  dom.window.document.querySelector("dialog").close();
  resolve(context);
  await settle();
  assert.equal(dom.window.document.querySelector("dialog"), null);
  assert.equal(calls.length, 1);
  dom.window.close();
});
test("similar leaves target-specific IP signals unselected by default", async () => {
  const { dom, api } = setup({
    "/context": {
      ...context,
      fields: [
        ...context.fields,
        {
          id: "elastic:1:ip",
          field: "ip",
          label: "IP",
          value: "192.0.2.1",
          source: "elastic:1",
        },
      ],
    },
  });
  api.openSimilar([{ type: "elastic", id: 1 }]);
  await settle();
  assert.equal(
    dom.window.document.querySelector('input[value="elastic:1:ip"]').checked,
    false,
  );
  dom.window.close();
});
test("cancel closes a modal without submitting work", async () => {
  const { dom, calls, api } = setup();
  api.openSimilar([{ type: "elastic", id: 1 }]);
  await settle();
  dom.window.document
    .querySelector("dialog")
    .dispatchEvent(new dom.window.Event("cancel", { cancelable: true }));
  assert.equal(dom.window.document.querySelector("dialog"), null);
  assert.equal(calls.length, 1);
  dom.window.close();
});
test("Similar preserves scope and requires an explicit resolution for conflicting scopes", async () => {
  const { dom, calls, api } = setup({
    "/context": {
      ...context,
      scope: { network: "192.0.2.0/24", country: "US", exclude: "staging" },
      scope_conflicts: ["network"],
    },
  });
  api.openSimilar([{ type: "elastic", id: 1 }]);
  await settle();
  const d = dom.window.document;
  assert.equal(d.querySelector("input[name=country]").value, "US");
  assert.equal(d.querySelector("input[name=exclude]").value, "staging");
  assert.equal(button(d, "Generate queries").disabled, true);
  const network = d.querySelector("input[name=network]");
  network.value = "198.51.100.0/24";
  network.dispatchEvent(new dom.window.Event("input"));
  assert.equal(button(d, "Generate queries").disabled, false);
  button(d, "Generate queries").click();
  await settle();
  assert.deepEqual(calls.find((c) => c.url.endsWith("/similar")).body.scope, {
    network: "198.51.100.0/24",
    country: "US",
    exclude: "staging",
  });
  dom.window.close();
});
test("edited provider queries disable stale links until saved and use returned links", async () => {
  const updated = {
    ...suggestion,
    status: "draft",
    queries: {
      ...suggestion.queries,
      shodan: {
        query: 'http.title:"Edited"',
        supported: true,
        url: "https://www.shodan.io/search?query=edited",
        warnings: [],
      },
    },
  };
  const { dom, calls, api } = setup({
    "/suggestions/1/queries": { suggestion: updated },
  });
  api.openSimilar([{ type: "elastic", id: 1 }]);
  await settle();
  const d = dom.window.document;
  button(d, "Generate queries").click();
  await settle();
  const editor = d.querySelector("textarea[data-query-provider=shodan]");
  assert.ok(editor);
  editor.value = 'http.title:"Edited"';
  editor.dispatchEvent(new dom.window.Event("input"));
  assert.equal(
    editor.closest("section").querySelector("a").hasAttribute("href"),
    false,
  );
  button(d, "Save edits").click();
  await settle();
  const call = calls.find((c) => c.url.endsWith("/suggestions/1/queries"));
  assert.deepEqual(call.body, {
    provider: "shodan",
    query: 'http.title:"Edited"',
  });
  assert.equal(
    editor.closest("section").querySelector("a").href,
    updated.queries.shodan.url,
  );
  dom.window.close();
});
test("generated queries can seed a lookup without executing a provider search", async () => {
  const { dom, calls, api } = setup({
    "/similar": {
      suggestion: { ...suggestion, scope: { network: "192.0.2.0/24" } },
    },
  });
  dom.window.LeakScopeOperations = {
    openLookup: (seed) => calls.push({ action: "lookup", seed }),
  };
  api.openSimilar([{ type: "elastic", id: 1 }]);
  await settle();
  const d = dom.window.document;
  button(d, "Generate queries").click();
  await settle();
  button(d, "Create lookup").click();
  const call = calls.find((c) => c.action === "lookup");
  assert.equal(call.seed.provider, "shodan");
  assert.equal(call.seed.query, suggestion.queries.shodan.query);
  assert.equal(call.seed.scope.network, "192.0.2.0/24");
  assert.equal(
    calls.some((c) => c.url && c.url.includes("/run")),
    false,
  );
  dom.window.close();
});
test("integration settings keep existing keys private and send only explicit replacements", async () => {
  const configured = { ...settings, netlas: { configured: true } };
  const { dom, calls, api } = setup({ "/settings": configured });
  api.openSettings();
  await settle();
  const d = dom.window.document;
  const inputs = [...d.querySelectorAll("input[type=password]")];
  assert.equal(inputs.length, 5);
  assert.ok(inputs.every((input) => input.value === ""));
  d.querySelector("input[name=llm_model]").value = "new-model";
  d.querySelector("input[name=llm_key]").value = "replacement-fixture";
  button(d, "Save integrations").click();
  await settle();
  const call = calls.find(
    (c) => c.url === "/api/intelligence/settings" && c.body,
  );
  assert.equal(call.body.llm.api_key, "replacement-fixture");
  assert.equal(call.body.llm.model, "new-model");
  assert.equal(Object.hasOwn(call.body.firecrawl, "api_key"), false);
  assert.equal(Object.hasOwn(call.body.netlas, "api_key"), false);
  assert.ok(inputs.every((input) => input.value === ""));
  assert.equal(dom.window.sessionStorage.length, 0);
  assert.equal(
    d.querySelector("dialog").textContent.includes("replacement-fixture"),
    false,
  );
  dom.window.close();
});
test("integration settings clear a key only on explicit selection", async () => {
  const { dom, calls, api } = setup();
  api.openSettings();
  await settle();
  const d = dom.window.document;
  const clear = d.querySelector("input[name=netlas_clear]");
  clear.checked = true;
  clear.dispatchEvent(new dom.window.Event("change"));
  button(d, "Save integrations").click();
  await settle();
  const call = calls.find(
    (c) => c.url === "/api/intelligence/settings" && c.body,
  );
  assert.equal(call.body.netlas.api_key, "");
  assert.equal(Object.hasOwn(call.body.llm, "api_key"), false);
  dom.window.close();
});
test("overview shows normalized observations separately from empty legacy findings", async () => {
  const { dom } = await renderApp("overview", {
    "/api/summary": {
      counts: { total: 0, confirmed: 0, for_later: 0, hidden: 0 },
      observations: {
        endpoints: 3,
        observations: 12,
        artifacts: 4,
        lookups: 2,
        watches: 1,
        by_provider: [{ provider: "netlas", count: 12 }],
      },
      by_type: [],
      by_provider: [],
      recent_runs: [],
      providers: { netlas: { configured: true } },
    },
  });
  const d = dom.window.document;
  assert.ok(
    [...d.querySelectorAll("h2")].find(
      (node) => node.textContent === "Observed assets",
    ),
  );
  assert.match(d.querySelector(".ops-observation-summary").textContent, /12/);
  assert.equal(
    d.body.textContent.includes("Your collection starts here"),
    false,
  );
  dom.window.close();
});
test("Netlas discovery respects advertised presets and links to observation results", async () => {
  const fixtures = {
    "/api/types": {
      results: [
        {
          key: "elastic",
          label: "Elasticsearch",
          discovery: true,
          discovery_providers: ["shodan", "zoomeye", "netlas"],
        },
        {
          key: "mongo",
          label: "MongoDB",
          discovery: true,
          discovery_providers: ["shodan", "zoomeye"],
        },
      ],
    },
    "/api/discover": { runs: [], results_url: "/assets/" },
  };
  const { dom, calls } = await renderApp("discover", fixtures);
  const d = dom.window.document;
  const netlas = d.querySelector("input[name=provider][value=netlas]");
  assert.ok(netlas);
  netlas.checked = true;
  netlas.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(
    d.querySelector("input[name=types][value=mongo]").disabled,
    true,
  );
  assert.equal(d.querySelector("input[name=page]"), null);
  assert.equal(d.querySelector("select[name=start]").value, "continue");
  button(d, "Run discovery →").click();
  await settle();
  const call = calls.find((c) => c.url === "/api/discover");
  assert.equal(call.body.provider, "netlas");
  assert.equal(call.body.page, 1);
  assert.ok(d.querySelector('#run-dialog-body a[href="/assets/"]'));
  dom.window.close();
});


test("self-hosted Firecrawl does not claim an absent key is stored", async () => {
  const { dom, api } = setup({ "/settings": { ...settings, firecrawl: { configured: true, key_configured: false, base_url: "http://firecrawl:3002/v2" } } });
  api.openSettings();
  await settle();
  const input = dom.window.document.querySelector("input[name=firecrawl_key]");
  assert.equal(input.value, "");
  assert.doesNotMatch(input.placeholder, /leave blank to keep/);
  dom.window.close();
});

test("integration reasoning mode round-trips while omitted keys stay preserved", async () => {
  const state = {...settings, llm:{...settings.llm, thinking_mode:"disabled", key_configured:true}};
  const {dom,calls,api}=setup({"/settings":state});
  await api.openSettings();const d=dom.window.document;
  const mode=d.querySelector('select[name=llm_thinking_mode]');assert.ok(mode);
  assert.equal(mode.value,"disabled");
  assert.match(d.body.textContent,/compatible.*endpoint|endpoint.*compatible/i);
  mode.value="enabled";mode.dispatchEvent(new dom.window.Event("change"));
  button(d,"Save integrations").click();await settle();
  const saved=calls.find(c=>c.body?.llm);
  assert.equal(saved.body.llm.thinking_mode,"enabled");
  assert.equal(saved.body.llm.base_url,state.llm.base_url);
  assert.equal(saved.body.llm.model,state.llm.model);
  assert.equal("api_key" in saved.body.llm,false);
  dom.window.close();
});

test("integration reasoning mode defaults to server default for older settings responses", async () => {
  const {dom,calls,api}=setup();await api.openSettings();const d=dom.window.document;
  const mode=d.querySelector('select[name=llm_thinking_mode]');assert.ok(mode);
  assert.equal(mode.value,"default");button(d,"Save integrations").click();await settle();
  assert.equal(calls.find(c=>c.body?.llm).body.llm.thinking_mode,"default");dom.window.close();
});

test('Ask Demo is explicit and displays its limited context and output notice',async()=>{
 const {dom,calls,api}=setup(),d=dom.window.document;try{
  api.openAsk([{type:'elastic',id:1}]);await settle();const demo=d.querySelector('dialog [name=demo]');assert.ok(demo);demo.click();
  d.querySelector('[name=question]').value='Explain this evidence';button(d,'Ask').click();await settle();const call=calls.find(c=>c.url.endsWith('/ask'));assert.equal(call.body.demo,true);assert.equal(call.body.question,'Explain this evidence');assert.match(d.body.textContent,/256|short|limited/i);
 }finally{dom.window.close();}
});
test('query research Demo requests a minimal real sample only when selected',async()=>{
 const {dom,calls,api}=setup(),d=dom.window.document;try{
  await api.renderResearch(d.querySelector('#page-root'));const demo=d.querySelector('[name=demo]');assert.ok(demo);demo.click();d.querySelector('[name=topic]').value='Example database';button(d,'Discover queries').click();await settle();assert.deepEqual(calls.find(c=>c.url.endsWith('/research')).body,{topic:'Example database',demo:true});
 }finally{dom.window.close();}
});

test('optional passive discovery credentials are explicit and preserved when untouched',async()=>{
 const {dom,calls,api}=setup({'/settings':{...settings,domain_sources:{certspotter:{key_configured:true},urlscan:{key_configured:false}}}});await api.openSettings();const d=dom.window.document;
 const cert=d.querySelector('[name=certspotter_key]'),url=d.querySelector('[name=urlscan_key]');assert.ok(cert);assert.ok(url);assert.match(cert.placeholder,/Configured/);
 url.value='fixture-urlscan';button(d,'Save integrations').click();await settle();const payload=calls.find(c=>c.url==='/api/intelligence/settings'&&c.body).body;
 assert.equal(payload.urlscan.api_key,'fixture-urlscan');assert.equal(payload.certspotter,undefined);dom.window.close();
});
