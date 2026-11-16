const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM, VirtualConsole } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const template = fs.readFileSync(
  path.join(root, "leakscope_app/templates/workbench/app.html"),
  "utf8",
);
const scripts = ["utils.js", "data_viewer.js", "visualizations.js", "workspace.js", "app.js"].map((name) =>
  fs.readFileSync(
    path.join(root, "leakscope_app/static/js/leakscope", name),
    "utf8",
  ),
);
const types = [
  {
    key: "elastic",
    label: "Elasticsearch",
    discovery: true,
    preview: true,
    export: true,
  },
  {
    key: "mongo",
    label: "MongoDB",
    discovery: true,
    preview: true,
    export: true,
  },
];
const finding = {
  id: 1,
  type: "elastic",
  label: "192.0.2.1:9200",
  ip: "192.0.2.1",
  port: "9200",
  provider: "shodan",
  status: "new",
  created_at: "2026-11-07T10:00:00Z",
  summary: "<script>window.injected=true</script>",
};
const fixtures = {
  "/api/summary": {
    counts: { total: 1, confirmed: 0, for_later: 0, hidden: 0 },
    by_type: [{ type: "elastic", label: "Elasticsearch", count: 1 }],
    by_provider: [{ provider: "shodan", count: 1 }],
    providers: { shodan: { configured: true }, zoomeye: { configured: true } },
    recent_runs: [],
  },
  "/api/types": { results: types },
  "/api/findings": { results: [finding], count: 1, page: 1, pages: 1 },
  "/api/findings/elastic/1": {
    finding,
    fields: { name: "test", secret: "sensitive-fixture" },
    capabilities: { preview: true, export: true },
    search: { keyword: "test" },
  },
  "/api/runs": { results: [] },
  "/api/queries": { results: [] },
  "/api/settings": {
    providers: { shodan: { configured: true }, zoomeye: { configured: false } },
    blacklist: [],
    health: { database: true, redis: true, worker: true },
    version: { python: "3.13", django: "5.2", celery: "5.7" },
    storage: { exports: "/data/artifacts" },
  },
  "/api/query-preview": {
    queries: [{ type: "elastic", query: 'product:"Elastic" port:9200' }],
  },
};
async function render(page, options = {}) {
  const failures = [],
    calls = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => failures.push(error.message));
  let html = template
    .replaceAll("{% load static %}", "")
    .replace(/\{% static '([^']+)' %\}/g, "/static/$1")
    .replace("{{ csrf_token }}", "fixture-csrf")
    .replace("{{ page|default:'overview' }}", page)
    .replace("{{ type|default:'' }}", options.type ?? "elastic")
    .replace("{{ target_id|default:'' }}", "1");
  const dom = new JSDOM(html, {
    url: "http://localhost/" + (page === "overview" ? "" : page + "/") + (options.query || ""),
    runScripts: "outside-only",
    virtualConsole,
  });
  const optionsHook = options.preview;
  const injectedFixtures=options.fixtures || {};
  const keyPath = (url) => new URL(url, "http://localhost").pathname;
  dom.window.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (keyPath(url) === "/api/query-preview" && optionsHook) return optionsHook(url, options);
    const key = new URL(url, "http://localhost").pathname;
    const result =
      injectedFixtures[key] || fixtures[key] ||
      (key.endsWith("/run")
        ? { run: { id: "fixture-finished", state: "succeeded" } }
        : { results: [] });
    return { ok: true, json: async () => result };
  };
  if (options.assets) dom.window.LeakScopeAssets=options.assets;
  dom.window.confirm = () => false;
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  scripts.forEach((script) => dom.window.eval(script));
  await new Promise((resolve) => setTimeout(resolve, 30));
  return { dom, failures, calls };
}
test("legacy finding row and detail keep their saved references when adding to organizations",async()=>{
  for (const page of ["findings","detail"]) {
    const opened=[];const {dom}=await render(page,{assets:{addToOrganization:ref=>opened.push(ref)}});
    const add=[...dom.window.document.querySelectorAll("button")].find(b=>b.textContent==="Add to organization");
    assert.ok(add);add.click();assert.equal(opened[0].type,"elastic");assert.equal(String(opened[0].id),"1");dom.window.close();
  }
});
for (const page of [
  "overview",
  "discover",
  "findings",
  "detail",
  "runs",
  "queries",
  "preview",
  "explore",
  "exports",
  "settings",
]) {
  test(page + " renders fixture data without script execution", async () => {
    const { dom, failures } = await render(page);
    const doc = dom.window.document;
    assert.equal(failures.length, 0, failures.join("\n"));
    assert.equal(
      doc.querySelector("#page-root").getAttribute("aria-busy"),
      "false",
    );
    assert.ok(doc.querySelector("h1"));
    assert.equal(dom.window.injected, undefined);
    assert.ok(
      !doc
        .querySelector("#page-root")
        .textContent.includes("could not load its data"),
    );
    if (page === "detail")
      assert.ok(
        !doc.querySelector("dd").textContent.includes("sensitive-fixture"),
      );
    dom.window.close();
  });
}

test("finding sensitive reveal fetches an explicit reveal request", async () => {
  const { dom, calls } = await render("detail");
  const doc = dom.window.document;
  const reveal = [...doc.querySelectorAll("button")].find(
    (b) => b.textContent === "Reveal",
  );
  assert.ok(reveal);
  assert.equal(
    doc
      .querySelector(".sensitive-value pre")
      .textContent.includes("sensitive-fixture"),
    false,
  );
  reveal.click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.ok(calls.some((c) => c.url === "/api/findings/elastic/1?reveal=1"));
  assert.equal(
    doc.querySelector(".sensitive-value pre").textContent,
    "sensitive-fixture",
  );
  reveal.click();
  assert.equal(
    doc
      .querySelector(".sensitive-value pre")
      .textContent.includes("sensitive-fixture"),
    false,
  );
  dom.window.close();
});
test("custom-query run preserves editable parameters", async () => {
  fixtures["/api/queries"] = {
    results: [
      {
        id: 3,
        name: "Scoped query",
        query_template: "{keyword} net:{network}",
        provider: "shodan",
        default_type: "elastic",
        active_probe_default: false,
      },
    ],
  };
  const { dom, calls } = await render("queries");
  const doc = dom.window.document;
  [...doc.querySelectorAll("button")]
    .find((b) => b.textContent === "Run")
    .click();
  const form = doc.querySelector("#run-dialog-body form");
  assert.ok(form);
  form.querySelector("[name=keyword]").value = "one & two";
  form.querySelector("[name=network]").value = "192.0.2.0/24";
  form.querySelector("[name=page_count]").value = "2";
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
  await new Promise((resolve) => setTimeout(resolve, 10));
  const submitted = calls.find((c) => c.url === "/api/queries/3/run");
  assert.ok(submitted);
  const body = JSON.parse(submitted.options.body);
  assert.equal(body.keyword, "one & two");
  assert.equal(body.network, "192.0.2.0/24");
  assert.equal(body.page_count, "2");
  assert.equal(body.active_probe, false);
  dom.window.close();
  fixtures["/api/queries"] = { results: [] };
});

test("standalone detail does not render a null placeholder section", async () => {
  const key = "/api/findings/elastic/1";
  const original = fixtures[key];
  fixtures[key] = { ...original, search: null };
  const { dom } = await render("detail");
  assert.notEqual(
    dom.window.document.querySelector("#page-root").lastChild.textContent,
    "null",
  );
  dom.window.close();
  fixtures[key] = original;
});

test("partial completed runs are labelled clearly and stop at the saved result", async () => {
  const original = fixtures["/api/runs"];
  fixtures["/api/runs"] = {
    results: [
      {
        id: "partial-fixture",
        kind: "discovery",
        type: "elastic",
        provider: "shodan",
        state: "succeeded",
        result: { partial: true, errors: ["Page 2 failed"], total: 2 },
      },
    ],
  };
  const { dom, calls } = await render("runs");
  const doc = dom.window.document;
  [...doc.querySelectorAll("button")]
    .find((button) => button.textContent === "View →")
    .click();
  const dialog = doc.querySelector("#run-dialog-body");
  assert.equal(dialog.querySelector(".badge").textContent, "partial");
  assert.ok(
    dialog.textContent.includes(
      "Partial results saved; some pages or results failed",
    ),
  );
  assert.equal(
    calls.some((call) => call.url === "/api/runs/partial-fixture"),
    false,
  );
  dom.window.close();
  fixtures["/api/runs"] = original;
});

test("Discover exposes categorized OT and IoT profiles with explicit discovery strategies", async () => {
  const old = fixtures["/api/types"];
  fixtures["/api/types"] = {
    results: [
      ...types,
      {
        key: "modbus",
        label: "Modbus",
        legacy: false,
        discovery: true,
        category: "ot",
        category_label: "ICS / SCADA / OT",
        categories: ["ot"],
        discovery_modes: {
          fingerprint: ["shodan"],
          nonstandard: ["shodan"],
          ports: ["shodan"],
        },
      },
      {
        key: "tasmota",
        label: "Tasmota",
        legacy: false,
        discovery: true,
        category: "iot",
        categories: ["iot"],
        discovery_modes: {
          fingerprint: ["shodan"],
          nonstandard: [],
          ports: ["shodan"],
        },
      },
    ],
  };
  try {
    const { dom, calls } = await render("discover"),
      d = dom.window.document;
    assert.match(
      d.querySelector("#page-root").textContent,
      /ICS \/ SCADA \/ OT/,
    );
    assert.match(d.querySelector("#page-root").textContent, /IoT/);
    const mode = d.querySelector("[name=query_mode]");
    assert.equal(mode.value, "fingerprint");
    assert.ok(d.querySelector("[name=types][value=modbus]"));
    const search = d.querySelector("[name=service_search]");
    search.value = "tasmota";
    search.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    assert.equal(
      d.querySelector("[name=types][value=modbus]").closest("label").hidden,
      true,
    );
    mode.value = "nonstandard";
    mode.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    assert.equal(d.querySelector("[name=types][value=tasmota]").disabled, true);
    await new Promise((r) => setTimeout(r, 350));
    assert.equal(
      JSON.parse(
        calls.filter((x) => x.url === "/api/query-preview").at(-1).options.body,
      ).query_mode,
      "nonstandard",
    );
    dom.window.close();
  } finally {
    fixtures["/api/types"] = old;
  }
});

test("catalog-only profiles never enter legacy findings, custom queries or inspection selectors", async () => {
  const old = fixtures["/api/types"];
  fixtures["/api/types"] = {
    results: [
      ...types,
      { key: "modbus", label: "Modbus", legacy: false, discovery: true },
    ],
  };
  try {
    for (const page of [
      "findings",
      "queries",
      "preview",
      "explore",
      "exports",
    ]) {
      const { dom } = await render(page);
      assert.equal(
        dom.window.document.querySelector("select option[value=modbus]"),
        null,
        page,
      );
      dom.window.close();
    }
  } finally {
    fixtures["/api/types"] = old;
  }
});


test("Discover starts unselected and category changes cannot retain an implicit Elasticsearch query", async () => {
  const old=fixtures["/api/types"];
  fixtures["/api/types"]={results:[...types,{key:"s7",label:"Siemens S7",discovery:true,categories:["ot"],discovery_modes:{fingerprint:["shodan"]}}]};
  const {dom,calls}=await render("discover",{type:""}),d=dom.window.document;
  try {
    assert.equal(d.querySelectorAll("[name=types]:checked").length,0);
    const category=d.querySelector("[name=service_category]");category.value="ot";category.dispatchEvent(new dom.window.Event("change",{bubbles:true}));
    const s7=d.querySelector("[name=types][value=s7]");s7.checked=true;s7.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    await new Promise(r=>setTimeout(r,350));
    assert.deepEqual(JSON.parse(calls.filter(x=>x.url==="/api/query-preview").at(-1).options.body).types,["s7"]);
  } finally {dom.window.close();fixtures["/api/types"]=old;}
});

test("Discover exposes selections outside the current filter and removes them explicitly", async () => {
  const {dom}=await render("discover",{type:""}),d=dom.window.document;
  try {
    const elastic=d.querySelector("[name=types][value=elastic]");elastic.checked=true;elastic.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    const search=d.querySelector("[name=service_search]");search.value="MongoDB";search.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    const remove=d.querySelector('[aria-label="Remove Elasticsearch from selection"]');assert.ok(remove);
    remove.click();assert.equal(elastic.checked,false);
  } finally {dom.window.close();}
});

test("Discover ignores an older preview response after the selection has changed", async () => {
  const pending=[];let defer=false;
  const {dom}=await render("discover",{type:"",preview:()=>defer?new Promise(resolve=>pending.push(resolve)):Promise.resolve({ok:true,json:async()=>({queries:[]})})}),d=dom.window.document;defer=true;
  const response=(type,query)=>({ok:true,json:async()=>({queries:[{type,query,supported:true,reason:"Response evidence"}]})});
  try {
    const elastic=d.querySelector("[name=types][value=elastic]"),mongo=d.querySelector("[name=types][value=mongo]");
    elastic.checked=true;elastic.dispatchEvent(new dom.window.Event("input",{bubbles:true}));await new Promise(r=>setTimeout(r,350));
    elastic.checked=false;mongo.checked=true;mongo.dispatchEvent(new dom.window.Event("input",{bubbles:true}));await new Promise(r=>setTimeout(r,350));
    assert.equal(pending.length,2);pending[1](response("mongo","CURRENT MONGO QUERY"));await new Promise(r=>setTimeout(r,10));pending[0](response("elastic","STALE ELASTIC QUERY"));await new Promise(r=>setTimeout(r,10));
    assert.match(d.querySelector(".code-box").textContent,/CURRENT MONGO QUERY/);assert.doesNotMatch(d.querySelector(".code-box").textContent,/STALE ELASTIC QUERY/);
  } finally {dom.window.close();}
});

test("Discover explicitly linked Netlas profile chooses its supported provider and explains all-port search", async () => {
  const old=fixtures["/api/types"];
  fixtures["/api/types"]={results:types.map(t=>t.key==="mongo"?{...t,discovery_modes:{fingerprint:["netlas"]}}:t)};
  const {dom}=await render("discover",{type:"",query:"?service_type=mongo",preview:async()=>({ok:true,json:async()=>({queries:[{type:"mongo",query:"mongodb.build_info.version:*",supported:true,reason:"Decoded MongoDB handshake"}]})})}),d=dom.window.document;
  try {
    assert.equal(d.querySelector('[name=provider]:checked').value,"netlas");
    assert.deepEqual([...d.querySelectorAll("[name=types]:checked")].map(x=>x.value),["mongo"]);
    assert.match(d.querySelector(".code-box").textContent,/All indexed ports/);assert.match(d.querySelector(".code-box").textContent,/Decoded MongoDB handshake/);
  } finally {dom.window.close();fixtures["/api/types"]=old;}
});

test("provider switch retains unsupported selection and blocks submission instead of silently dropping it", async()=>{
  const old=fixtures["/api/types"];
  fixtures["/api/types"]={results:types.map(t=>t.key==="mongo"?{...t,discovery_modes:{fingerprint:["netlas"]}}:t)};
  const {dom,calls}=await render("discover",{type:"",query:"?service_type=mongo&provider=netlas"}),d=dom.window.document;
  try {
    const radio=d.querySelector('[name=provider][value=shodan]');radio.checked=true;radio.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    const mongo=d.querySelector('[name=types][value=mongo]');assert.equal(mongo.checked,true);assert.equal(mongo.disabled,true);
    assert.match(d.querySelector('.selected-services').textContent,/unavailable/i);
    d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
    await new Promise(r=>setTimeout(r,10));assert.equal(calls.some(c=>c.url==='/api/discover'),false);
    assert.match(d.querySelector('#page-root').textContent,/selected services are unavailable/i);
  } finally {dom.window.close();fixtures["/api/types"]=old;}
});

test('Discover separates passive-default collection from provider query strategy', async () => {
  const old=fixtures['/api/types'];
  fixtures['/api/types']={results:types.map(t=>({...t,fetch_actions:['tcp','http','httpx','screenshot']}))};
  const {dom,calls}=await render('discover',{query:'?query_mode=legacy'}),d=dom.window.document;
  try {
    const modes=[...d.querySelectorAll('[name=collection_mode]')];
    assert.deepEqual(modes.map(x=>x.value),['passive','demo','semi_passive','screenshot','full']);
    assert.equal(d.querySelector('[name=collection_mode]:checked').value,'passive');
    assert.equal(d.querySelector('[name=query_mode]').value,'legacy');
    assert.equal(d.querySelector('form').querySelector('[name=active_probe]'),null);
    assert.match(d.querySelector('.service-capture').textContent,/Available captures/);
    assert.match(d.querySelector('.collection-mode-hint').textContent,/no direct target requests/i);
    assert.equal(d.querySelector('[name=page]'),null);
    assert.equal(d.querySelector('[name=start]').value,'continue');assert.equal(d.querySelector('[name=start_value]').closest('.field').hidden,true);
    d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
    await new Promise(r=>setTimeout(r,20));
    const body=JSON.parse(calls.find(c=>c.url==='/api/discover').options.body);
    assert.equal(body.collection_mode,'passive');assert.equal(body.query_mode,'legacy');assert.equal(body.page,1);assert.equal('active_probe' in body,false);
    assert.equal(body.start,'continue');assert.equal('start_value' in body,false);
    const start=d.querySelector('[name=start]'),value=d.querySelector('[name=start_value]');
    start.value='after';start.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
    assert.equal(value.closest('.field').hidden,false);assert.equal(value.value,'100');assert.match(value.closest('.field').textContent,/Skip this many results/);
    calls.length=0;d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
    await new Promise(r=>setTimeout(r,20));
    const next=JSON.parse(calls.find(c=>c.url==='/api/discover').options.body);
    assert.equal(next.start,'after');assert.equal(next.start_value,100);
  } finally {dom.window.close();fixtures['/api/types']=old;}
});

test('Discover collection choice stays independent and reaches the preview and discovery payload', async () => {
  const {dom,calls}=await render('discover'),d=dom.window.document;
  try {
    const full=d.querySelector('[name=collection_mode][value=full]');assert.ok(full);full.checked=true;full.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
    assert.equal(d.querySelector('[name=query_mode]').value,'fingerprint');
    assert.match(d.querySelector('.collection-mode-hint').textContent,/direct target requests/i);
    await new Promise(r=>setTimeout(r,350));
    assert.equal(JSON.parse(calls.filter(c=>c.url==='/api/query-preview').at(-1).options.body).collection_mode,'full');
    d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,20));
    assert.equal(JSON.parse(calls.find(c=>c.url==='/api/discover').options.body).collection_mode,'full');
  } finally {dom.window.close();}
});

test('run progress identifies the saved collection mode before result details', async () => {
  const old=fixtures['/api/runs'];fixtures['/api/runs']={results:[{id:'mode-run',kind:'lookup',state:'succeeded',provider:'shodan',parameters:{},result:{collection:{mode:'full'},stored:2}}]};
  const {dom}=await render('runs'),d=dom.window.document;
  try {
    [...d.querySelectorAll('button')].find(b=>b.textContent==='View →').click();
    assert.equal(d.querySelector('#run-dialog-body .run-collection-mode').textContent,'Collection: Full');
  } finally {dom.window.close();fixtures['/api/runs']=old;}
});

test('Discover Demo makes its real minimum request limits explicit without changing passive default',async()=>{
 const {dom,calls}=await render('discover'),d=dom.window.document;
 try{
  assert.equal(d.querySelector('[name=collection_mode]:checked').value,'passive');
  const demo=d.querySelector('[name=collection_mode][value=demo]');assert.ok(demo,'Demo choice is available');
  d.querySelector('[name=page_count]').value='12';demo.click();
  assert.match(d.querySelector('.collection-mode-hint').textContent,/real|provider/i);
  assert.match(d.querySelector('.collection-mode-hint').textContent,/per.*query|each.*query/i);
  d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,30));
  const body=JSON.parse(calls.find(c=>c.url==='/api/discover').options.body);assert.equal(body.collection_mode,'demo');assert.equal(body.page_count,1);
 }finally{dom.window.close();}
});


test('saved query Demo clamps only the submitted execution and retains normal form limits',async()=>{
 const old=fixtures['/api/queries'];fixtures['/api/queries']={results:[{id:3,name:'Fixture',query_template:'{keyword}',provider:'shodan',default_type:'elastic',active_probe_default:true}]};
 const {dom,calls}=await render('queries'),d=dom.window.document;try{
  [...d.querySelectorAll('button')].find(x=>x.textContent==='Run').click();const form=d.querySelector('#run-dialog-body form');form.querySelector('[name=page_count]').value='12';form.querySelector('[name=demo]').click();
  form.dispatchEvent(new dom.window.Event('submit',{cancelable:true}));await new Promise(r=>setTimeout(r,20));const body=JSON.parse(calls.find(c=>c.url==='/api/queries/3/run').options.body);
  assert.equal(body.demo,true);assert.equal(body.page_count,1);assert.equal(body.active_probe,false);
 }finally{dom.window.close();fixtures['/api/queries']=old;}
});

test('Discover keyword-only search previews and submits one passive generic query without services',async()=>{
 const {dom,calls}=await render('discover',{type:'',preview:async()=>({ok:true,json:async()=>({queries:[{type:'generic',supported:true,query:'"acme"',reason:'Provider keyword search'}]})})}),d=dom.window.document;
 try{
  assert.equal(d.querySelectorAll('[name=types]:checked').length,0);
  const keyword=d.querySelector('[name=keyword]');keyword.value='acme';keyword.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  await new Promise(r=>setTimeout(r,350));
  const previews=calls.filter(c=>c.url==='/api/query-preview');assert.equal(previews.length,1);
  const body=JSON.parse(previews[0].options.body);assert.deepEqual(body.types,[]);assert.equal(body.keyword,'acme');assert.equal(body.collection_mode,'passive');
  assert.match(d.querySelector('.code-box').textContent,/Keyword search/);assert.match(d.querySelector('.code-box').textContent,/no port restrictions/i);
  assert.equal(d.querySelector('[data-service-filters]').open,false);
  assert.ok(keyword.compareDocumentPosition(d.querySelector('[name=types]')) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,20));
  const submitted=JSON.parse(calls.find(c=>c.url==='/api/discover').options.body);assert.deepEqual(submitted.types,[]);assert.equal(submitted.keyword,'acme');assert.equal(submitted.collection_mode,'passive');
 }finally{dom.window.close();}
});

test('Discover keyword-only Demo estimates one query and keeps minimum limits',async()=>{
 const {dom,calls}=await render('discover',{type:'',preview:async()=>({ok:true,json:async()=>({queries:[{type:'generic',query:'"acme"'}]})})}),d=dom.window.document;
 try{
  const keyword=d.querySelector('[name=keyword]');keyword.value='acme';keyword.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  d.querySelector('[name=page_count]').value='15';d.querySelector('[name=collection_mode][value=demo]').click();
  await new Promise(r=>setTimeout(r,350));assert.match(d.querySelector('.code-box').textContent,/Demo: 1 selected quer/);
  d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,20));
  const submitted=JSON.parse(calls.find(c=>c.url==='/api/discover').options.body);assert.equal(submitted.collection_mode,'demo');assert.equal(submitted.page_count,1);assert.equal(submitted.zoomeye_pagesize,1);assert.deepEqual(submitted.types,[]);
 }finally{dom.window.close();}
});

test('Discover requires a keyword or a service and returns to generic search after removing a service',async()=>{
 const {dom,calls}=await render('discover',{type:'',preview:async()=>({ok:true,json:async()=>({queries:[{type:'generic',query:'"acme"'}]})})}),d=dom.window.document;
 try{
  d.querySelector('[name=keyword]').value='   ';
  d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,10));
  assert.equal(calls.some(c=>c.url==='/api/discover'),false);assert.match(d.querySelector('#page-root').textContent,/Enter a keyword or select/i);
  const keyword=d.querySelector('[name=keyword]');keyword.value='acme';
  const service=d.querySelector('[name=types][value=elastic]');service.checked=true;service.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  [...d.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Remove Elasticsearch from selection').click();
  await new Promise(r=>setTimeout(r,350));assert.deepEqual(JSON.parse(calls.filter(c=>c.url==='/api/query-preview').at(-1).options.body).types,[]);
  assert.match(d.querySelector('.code-box').textContent,/Keyword search/);
 }finally{dom.window.close();}
});

test('lookup run exposes zero matches and exact query without opening Result details',async()=>{
 const old=fixtures['/api/runs'];fixtures['/api/runs']={results:[{id:'empty-run',kind:'lookup',provider:'shodan',state:'succeeded',result:{stored:0,total:0,query:'"flock admin"',endpoint_ids:[]}}]};
 const {dom}=await render('runs'),d=dom.window.document;
 try{[...d.querySelectorAll('button')].find(b=>b.textContent==='View →').click();const card=d.querySelector('#run-dialog-body .run-card');assert.match(card.querySelector('.run-result-summary').textContent,/0 results saved/);assert.match(card.textContent,/No matches returned/);assert.equal(card.querySelector('.run-provider-query').textContent,'"flock admin"');assert.equal(card.querySelector('a.run-observations-link').getAttribute('href'),'/assets/?run=empty-run');assert.equal(card.querySelector('details').open,false);}finally{dom.window.close();fixtures['/api/runs']=old;}
});

test('Discover keeps the completed output on the page after progress dialog closes',async()=>{
 const discover=fixtures['/api/discover'];fixtures['/api/discover']={runs:[{id:'saved-run',kind:'lookup',provider:'shodan',state:'succeeded',result:{stored:2,total:2,query:'"acme"',endpoint_ids:['one','two']}}],results_url:'/assets/'};
 const {dom}=await render('discover'),d=dom.window.document;
 try{d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,20));d.getElementById('close-run-dialog').click();const output=d.querySelector('[data-discovery-results]');assert.ok(output);assert.match(output.textContent,/2 results saved/);assert.equal(output.querySelector('a.run-observations-link').getAttribute('href'),'/assets/?run=saved-run');assert.equal(d.getElementById('run-dialog').open,false);}finally{dom.window.close();if(discover)fixtures['/api/discover']=discover;else delete fixtures['/api/discover'];}
});

test('older discovery completion cannot replace the latest search output',async()=>{
 const old=fixtures['/api/discover'],oldRun=fixtures['/api/runs/older-run'];
 fixtures['/api/discover']={runs:[{id:'older-run',kind:'lookup',provider:'shodan',state:'queued'}]};
 fixtures['/api/runs/older-run']={id:'older-run',kind:'lookup',provider:'shodan',state:'succeeded',result:{stored:0,total:0,query:'"older"',endpoint_ids:[]}};
 const {dom}=await render('discover'),d=dom.window.document;
 try{d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,20));fixtures['/api/discover']={runs:[{id:'newer-run',kind:'lookup',provider:'shodan',state:'succeeded',result:{stored:1,total:1,query:'"newer"',endpoint_ids:['one']}}]};d.getElementById('close-run-dialog').click();d.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,800));const output=d.querySelector('[data-discovery-results]');assert.match(output.textContent,/newer/);assert.doesNotMatch(output.textContent,/older/);}finally{dom.window.close();if(old)fixtures['/api/discover']=old;else delete fixtures['/api/discover'];if(oldRun)fixtures['/api/runs/older-run']=oldRun;else delete fixtures['/api/runs/older-run'];}
});

test("approved shell loads dark theme, command search and active grouped navigation",async()=>{const {dom,failures}=await render("detail");const d=dom.window.document;try{assert.deepEqual(failures,[]);assert.equal(d.documentElement.dataset.theme,"dark");assert.ok(d.querySelector("#command-toggle"));assert.ok(d.querySelectorAll("[data-nav-group]").length>=4);assert.ok(d.querySelector("[data-nav-group=evidence]").open);assert.equal(d.querySelector("[data-nav=overview]").getAttribute("href"),"/");}finally{dom.window.close()}});

test("finding structured fields use formatted view and unchanged raw JSON",async()=>{const fixture=fixtures["/api/findings/elastic/1"],original=fixture.fields;fixture.fields={payload:{zero:0,flag:false,nested:{field:"<img src=x onerror=window.injected=true>"}}};const {dom}=await render("detail");try{const viewer=dom.window.document.querySelector(".data-viewer");assert.ok(viewer);assert.equal(viewer.querySelector("[data-viewer-mode=formatted]").getAttribute("aria-pressed"),"true");assert.equal(viewer.querySelector(".data-raw"),null);viewer.querySelector("[data-viewer-mode=raw]").click();assert.deepEqual(JSON.parse(viewer.querySelector(".data-raw").textContent),fixture.fields.payload);assert.equal(dom.window.injected,undefined);}finally{dom.window.close();fixture.fields=original}});
test('run history paginates loaded jobs without starting any operations',async()=>{const old=fixtures['/api/runs'];fixtures['/api/runs']={results:Array.from({length:61},(_,i)=>({id:'job-'+i,kind:'lookup',provider:'shodan',state:'succeeded',created_at:'2026-11-09T10:00:00Z'}))};const {dom,calls}=await render('runs');try{const d=dom.window.document;assert.equal(d.querySelectorAll('.job-history tbody tr').length,25);assert.match(d.querySelector('.table-pager').textContent,/1–25 of 61 loaded jobs/);[...d.querySelectorAll('.table-pager button')].find(b=>b.textContent==='Next').click();assert.match(d.querySelector('.table-pager').textContent,/26–50 of 61 loaded jobs/);assert.equal(calls.filter(c=>c.options.method==='POST').length,0);}finally{dom.window.close();fixtures['/api/runs']=old}});
test('Preview omits optional null children instead of rendering literal null',async()=>{const {dom}=await render('preview');try{const walker=dom.window.document.createTreeWalker(dom.window.document.querySelector('main'),dom.window.NodeFilter.SHOW_TEXT);const nulls=[];while(walker.nextNode())if(walker.currentNode.textContent.trim()==='null')nulls.push(walker.currentNode);assert.equal(nulls.length,0);}finally{dom.window.close()}});
test('Discover opens saved run endpoints and formatted evidence without rerunning or fetching targets',async()=>{const oldRuns=fixtures['/api/runs'],oldEndpoints=fixtures['/api/operations/endpoints'],oldDetail=fixtures['/api/operations/endpoints/endpoint-1'];const endpoint={id:'endpoint-1',address:'192.0.2.10',port:1502,service_type:'modbus',transport:'tcp',title:'Saved controller',last_seen:'2026-11-09T08:00:00Z'};fixtures['/api/runs']={results:[{id:'saved-job',kind:'lookup',provider:'shodan',state:'succeeded',result:{stored:1,total:1,query:'"fixture"',endpoint_ids:[endpoint.id]}}]};fixtures['/api/operations/endpoints']={results:[endpoint],count:1,page:1};fixtures['/api/operations/endpoints/endpoint-1']={endpoint,observations:[{provider:'shodan',data:{product:'<img src=x onerror=window.injected=true>',live:false}}],artifacts:[]};const {dom,calls}=await render('discover');try{const d=dom.window.document;[...d.querySelectorAll('button')].find(b=>b.textContent==='View output').click();await new Promise(r=>setTimeout(r,30));assert.equal(d.querySelectorAll('[data-discovery-results] tbody tr').length,1);[...d.querySelectorAll('[data-discovery-results] button')].find(b=>b.textContent==='Inspect').click();await new Promise(r=>setTimeout(r,30));assert.ok(d.querySelector('.discovery-inspector[open]'));[...d.querySelectorAll('.inspector-tab')].find(b=>b.textContent==='Evidence').click();assert.ok(d.querySelector('.discovery-inspector .data-viewer'));assert.equal(d.querySelector('.discovery-inspector .data-raw'),null);assert.equal(dom.window.injected,undefined);assert.equal(calls.filter(c=>c.options.method==='POST'&&!c.url.startsWith('/api/query-preview')).length,0);}finally{dom.window.close();fixtures['/api/runs']=oldRuns;if(oldEndpoints)fixtures['/api/operations/endpoints']=oldEndpoints;else delete fixtures['/api/operations/endpoints'];if(oldDetail)fixtures['/api/operations/endpoints/endpoint-1']=oldDetail;else delete fixtures['/api/operations/endpoints/endpoint-1'];}});
test('run pagination retains keyboard focus and picks the available control at the final page',async()=>{const old=fixtures['/api/runs'];fixtures['/api/runs']={results:Array.from({length:61},(_,i)=>({id:'job-'+i,kind:'lookup',provider:'shodan',state:'succeeded'}))};const {dom}=await render('runs');try{const d=dom.window.document,next=[...d.querySelectorAll('.table-pager button')].find(b=>b.textContent==='Next');next.focus();next.click();assert.equal(d.activeElement,next);next.click();assert.equal(d.activeElement.textContent,'Previous');assert.equal(next.disabled,true);}finally{dom.window.close();fixtures['/api/runs']=old}});
test('delayed linked saved run cannot replace a newer saved-output selection',async()=>{const oldRuns=fixtures['/api/runs'],oldDetail=fixtures['/api/runs/stale-run'];const current={id:'current-run',kind:'lookup',provider:'zoomeye',state:'succeeded',result:{stored:0,total:0,query:'CURRENT-SAVED-QUERY'}};fixtures['/api/runs']={results:[current]};let resolveOld;const oldFetchHook=fixtures['/api/runs/stale-run'];fixtures['/api/runs/stale-run']=new Promise(resolve=>{resolveOld=resolve});const {dom}=await render('discover',{query:'?run=stale-run'});try{const d=dom.window.document;[...d.querySelectorAll('button')].find(b=>b.textContent==='View output').click();assert.match(d.querySelector('[data-discovery-results]').textContent,/CURRENT-SAVED-QUERY/);resolveOld({id:'stale-run',kind:'lookup',provider:'shodan',state:'succeeded',result:{stored:0,total:0,query:'STALE-SAVED-QUERY'}});await new Promise(r=>setTimeout(r,30));assert.match(d.querySelector('[data-discovery-results]').textContent,/CURRENT-SAVED-QUERY/);assert.doesNotMatch(d.querySelector('[data-discovery-results]').textContent,/STALE-SAVED-QUERY/);}finally{dom.window.close();fixtures['/api/runs']=oldRuns;if(oldDetail)fixtures['/api/runs/stale-run']=oldDetail;else delete fixtures['/api/runs/stale-run'];}});

test("Overview shows saved observations even when legacy findings are empty", async () => {
  const summary={counts:{total:0,confirmed:0,for_later:0,hidden:0},by_type:[],by_provider:[],providers:{},recent_runs:[],
    observations:{endpoints:1244,observations:1571,artifacts:632,lookups:145,watches:5,
      by_provider:[{provider:"zoomeye",count:1000}],by_type:[{service_type:"grpc",count:12},{service_type:"",count:3}]}};
  const {dom,failures,calls}=await render("overview",{fixtures:{"/api/summary":summary}});
  const d=dom.window.document;
  assert.equal(d.querySelector(".summary-grid .metric .label").textContent,"Saved endpoints");
  assert.match(d.querySelector(".summary-grid .metric .number").textContent,/1.?244/);
  assert.ok(d.querySelector('a[href="/assets/?type=grpc"]'));
  assert.equal(d.querySelector('a[href="/assets/?type="]'),null);
  const disclosure=[...d.querySelectorAll("details")].find(x=>x.querySelector("summary")?.textContent.includes("Legacy finding review"));
  assert.ok(disclosure);assert.equal(disclosure.open,false);
  assert.equal(calls.filter(x=>x.options.method && x.options.method!=="GET").length,0);
  assert.deepEqual(failures,[]);dom.window.close();
});

test('Discover preview says where the search starts and why',async()=>{
 const starts={continue:{type:'generic',mode:'continue',page:4,page_size:50,first_result:151,repeated:0,known:true,total:900},after:{type:'generic',mode:'after',page:2,page_size:20,first_result:21,repeated:10}};
 const {dom}=await render('discover',{type:'',preview:async(url,options)=>({ok:true,json:async()=>({queries:[{type:'generic',query:'"acme"'}],start:[starts[JSON.parse(options.body).start]]})})}),d=dom.window.document;
 try{
  const keyword=d.querySelector('[name=keyword]');keyword.value='acme';keyword.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  await new Promise(r=>setTimeout(r,350));
  assert.match(d.querySelector('.code-box').textContent,/Start: page 4 \(result 151 onwards, 50 per page\) · earlier runs already fetched the pages before it · provider reported 900 in total/);
  const start=d.querySelector('[name=start]');start.value='after';start.dispatchEvent(new dom.window.Event('change',{bubbles:true}));start.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  await new Promise(r=>setTimeout(r,350));
  assert.match(d.querySelector('.code-box').textContent,/Start: page 2 .*the 10 result\(s\) before your start on that page are fetched again/);
 }finally{dom.window.close();}
});
