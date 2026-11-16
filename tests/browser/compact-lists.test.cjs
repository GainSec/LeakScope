const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const base = path.resolve(__dirname, "../../leakscope_app/static/js/leakscope");
const lookup = i => ({ id: "l" + i, name: "Rule " + i, provider: "netlas", query: 'body:"query ' + i + '"', service_type: "modbus", enabled: true,
  interval_seconds: 3600, collection_mode: "passive", last_state: "succeeded", last_run_at: "2026-11-09T00:00:00Z", manual_tags: ["review"], inherited_tags: [], tags: ["review"] });
const organization = i => ({ id: "o" + i, kind: "organization", value: "Example " + i, label: "Example " + i, status: "active", attributes: { aliases: ["Alias " + i] } });
function setup(routes = {}, url = "http://localhost/lookups/") {
  const dom = new JSDOM('<meta name="csrf-token" content="fixture"><div id="root"></div>', { url, runScripts: "outside-only" });
  const calls = [];
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new dom.window.Event("close")); };
  const defaults = {
    "/api/operations/catalog": { results: [{ key: "modbus", label: "Modbus TCP", transport: "tcp", ports: [502] }], tools: {}, providers: { netlas: { configured: true } } },
    "/api/operations/lookups": { results: Array.from({ length: 45 }, (_, i) => lookup(i)) },
    "/api/assets/organizations": { results: Array.from({ length: 39 }, (_, i) => organization(i)) },
    "/api/operations/tags": { manual_tags: [], inherited_tags: [], tags: [] },
  };
  dom.window.fetch = async (value, opts = {}) => {
    const parsed = new URL(value, "http://localhost"); calls.push({ url: parsed, opts, body: opts.body && JSON.parse(opts.body) });
    let result = routes[parsed.pathname] ?? defaults[parsed.pathname] ?? {};
    if (typeof result === "function") result = await result(parsed, opts);
    return { ok: true, json: async () => result, text: async () => JSON.stringify(result), headers: { get: () => null } };
  };
  for (const name of ["utils.js", "data_viewer.js", "visualizations.js", "operations.js", "relationship_map.js", "relationships.js"])
    dom.window.eval(fs.readFileSync(path.join(base, name), "utf8"));
  return { dom, calls, root: dom.window.document.querySelector("#root"), ops: dom.window.LeakScopeOperations, assets: dom.window.LeakScopeAssets };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const button = (root, label) => [...root.querySelectorAll("button")].find(node => node.textContent === label);

test("lookup lists render twelve compact summaries with lazy configuration and honest loaded counts", async () => {
  const { dom, root, ops, calls } = setup(); await ops.render(root, "lookups");
  const rows = root.querySelectorAll(".ops-lookup-row"); assert.equal(rows.length, 12);
  assert.equal(root.querySelector(".ops-lookup-editor").open, false);
  assert.match(rows[0].querySelector(".ops-lookup-summary").textContent, /netlas.*modbus.*succeeded/i);
  assert.match(rows[0].textContent, /Every 3600 seconds/);
  assert.equal(rows[0].querySelector(".ops-lookup-details").open, false);
  assert.equal(rows[0].querySelector('input[name="tags"]'), null);
  assert.match(root.textContent, /45 loaded lookups/);
  const count = calls.length; button(root, "Next lookups").click();
  assert.equal(root.querySelector(".ops-lookup-row").dataset.lookupId, "l12"); assert.equal(calls.length, count);
  const filter = root.querySelector('input[name="lookup_filter"]'); filter.value = "Rule 44";
  filter.dispatchEvent(new dom.window.Event("input"));
  assert.equal(root.querySelectorAll(".ops-lookup-row").length, 1);
  assert.match(root.textContent, /1 matching.*45 loaded lookups/);
  dom.window.close();
});

test("lookup configuration remains available, preserves drafts across paging and editor opens on edit or validation", async () => {
  const { dom, root, ops } = setup(); await ops.render(root, "lookups");
  const row = root.querySelector(".ops-lookup-row"), details = row.querySelector(".ops-lookup-details");
  details.open = true; await settle();
  assert.match(details.textContent, /body:"query 0"/);
  const tags = details.querySelector('input[name="tags"]'); assert.ok(tags); tags.value = "draft";
  button(root, "Next lookups").click(); button(root, "Previous lookups").click();
  assert.equal(root.querySelector('.ops-lookup-row input[name="tags"]').value, "draft");
  button(row, "Edit").click(); assert.equal(root.querySelector(".ops-lookup-editor").open, true);
  assert.equal(root.querySelector('input[name="name"]').value, "Rule 0");
  root.querySelector(".ops-lookup-editor").open = false;
  root.querySelector('input[name="name"]').dispatchEvent(new dom.window.Event("invalid", { cancelable: true }));
  assert.equal(root.querySelector(".ops-lookup-editor").open, true);
  dom.window.close();
});

test("compact lookup controls retain exact Demo, pause and delete payloads", async () => {
  const { dom, root, ops, calls } = setup(); await ops.render(root, "lookups");
  let row = root.querySelector(".ops-lookup-row"); button(row, "Run Demo").click(); await settle();
  assert.deepEqual(calls.find(call => call.url.pathname === "/api/operations/lookups/l0/run").body, { demo: true });
  button(row, "Pause").click(); await settle();
  assert.deepEqual(calls.find(call => call.url.pathname.endsWith("/l0/toggle")).body, { enabled: false });
  row = root.querySelector(".ops-lookup-row"); button(row, "Delete").click(); await settle();
  assert.deepEqual(calls.find(call => call.url.pathname.endsWith("/l0/delete")).body, {});
  dom.window.close();
});

test("organization rows paginate locally and retain primary view plus discovery controls", async () => {
  const { dom, root, assets, calls } = setup({}, "http://localhost/organizations/"); await assets.render(root, "organizations");
  assert.equal(root.querySelectorAll(".asset-organization-row").length, 12);
  const first = root.querySelector(".asset-organization-row");
  assert.equal(first.querySelector(".asset-organization-details").open, false);
  assert.ok(first.querySelector('a[href="/organizations/?id=o0"]'));
  assert.match(first.textContent, /1 alias/);
  const count = calls.length; button(root, "Next organizations").click(); assert.equal(calls.length, count);
  const filter = root.querySelector('input[name="organization_filter"]'); filter.value = "Example 38"; filter.dispatchEvent(new dom.window.Event("input"));
  assert.equal(root.querySelectorAll(".asset-organization-row").length, 1);
  assert.ok(button(root, "Discover candidates")); assert.ok(button(root, "Research services with Firecrawl"));
  assert.equal(calls.some(call => call.body), false);
  dom.window.close();
});

test("failed organization status changes show visible list errors and clear stale rows until an explicit retry", async () => {
  let fail = true;
  const { dom, root, assets, calls } = setup({"/api/assets/organizations":url=>{
    if(url.searchParams.get("status")==="archived") {
      if(fail) throw new Error("Archived list unavailable");
      return {results:[{...organization(0),status:"archived"}]};
    }
    return {results:[organization(0)]};
  }},"http://localhost/organizations/");
  await assets.render(root,"organizations");assert.equal(root.querySelectorAll(".asset-organization-row").length,1);
  const create=root.querySelector(".asset-organization-create");assert.equal(create.open,false);
  const status=root.querySelector('select[name="organization_status"]');status.value="archived";status.dispatchEvent(new dom.window.Event("change"));await settle();
  const alert=root.querySelector('.asset-organization-list-status [role="alert"]');assert.ok(alert);assert.match(alert.textContent,/Archived list unavailable/);
  assert.equal(alert.closest("details"),null);assert.equal(create.open,false);
  assert.equal(root.querySelectorAll(".asset-organization-row").length,0);
  assert.equal(root.querySelector('input[name="organization_filter"]').disabled,true);
  assert.equal(status.value,"archived");assert.equal(new URL(dom.window.location.href).searchParams.get("status"),"archived");
  fail=false;button(root,"Retry organization list").click();await settle();
  assert.equal(root.querySelector('.asset-organization-list-status [role="alert"]'),null);
  assert.equal(root.querySelectorAll('[data-organization-status="archived"]').length,1);
  assert.equal(root.querySelector('input[name="organization_filter"]').disabled,false);
  assert.equal(calls.some(call=>call.body),false);
  dom.window.close();
});

test("certificate list shows brief names and issuer text with lazy full evidence while preserving server pagination", async () => {
  const certificate = { id: "c1", fingerprint: "abc", issuer: { CN: "<img src=x> Issuer", O: "Fixture CA" }, domains: Array.from({ length: 40 }, (_, i) => "d" + i + ".example"), watch_matches: [] };
  const { dom, root, assets, calls } = setup({ "/api/assets/certificates": { results: [certificate], count: 51, page: 1, pages: 3, stats: {}, watches: [] } }, "http://localhost/certificates/");
  await assets.render(root, "certificates");
  const names = root.querySelector(".asset-certificate-names"); assert.ok(names);
  assert.equal(names.querySelectorAll("li").length, 3);
  const extra = names.querySelector(".asset-certificate-more"); assert.equal(extra.open, false); extra.open = true; await settle();
  assert.ok(names.querySelectorAll("li").length > 3);
  const issuer = root.querySelector(".asset-certificate-issuer"); assert.equal(issuer.open, false);
  assert.equal(issuer.querySelector(".data-viewer"), null); issuer.open = true; await settle();
  assert.ok(issuer.querySelector(".data-viewer")); assert.equal(root.querySelector("img"), null);
  button(root, "Next").click(); await settle();
  assert.equal(calls.at(-1).url.searchParams.get("page"), "2"); assert.equal(calls.at(-1).url.searchParams.get("page_size"), "25");
  dom.window.close();
});

test("large certificate detail name lists preserve all actions behind local pagination", async () => {
  const certificate = { id: "c1", fingerprint: "abc", issuer: "Fixture CA", domains: Array.from({ length: 80 }, (_, i) => "d" + i + ".example"), watch_matches: [] };
  const { dom, root, assets, calls } = setup({ "/api/assets/certificates/c1": { certificate, data: {} } }, "http://localhost/certificates/?id=c1");
  await assets.render(root, "certificates");
  const list = root.querySelector('[data-list-id="certificate-names"]'); assert.ok(list); assert.equal(list.open, false);
  assert.equal(list.querySelectorAll("tbody tr").length, 25);
  const count = calls.length; button(list, "Next names").click(); assert.equal(calls.length, count);
  assert.match(list.textContent, /d25.example/); assert.ok(button(list, "Monitor")); assert.ok(button(list, "Asset tools"));
  dom.window.close();
});

test("technology catalog bounds initial rows and retains every profile through local search and pagination", async () => {
  const profiles = Array.from({ length: 61 }, (_, i) => ({ key: "p" + i, label: "Profile " + i, category: "ot", ports: [502], transport: "tcp", queries: { shodan: 'body:"p' + i + '"' } }));
  const { dom, root, ops, calls } = setup({ "/api/operations/catalog": { results: profiles, tools: {} } }, "http://localhost/technologies/"); await ops.render(root, "catalog");
  assert.equal(root.querySelectorAll(".service-card").length, 25);
  assert.equal(root.querySelector(".service-card-front").getAttribute("aria-expanded"), "false");
  const count = calls.length; button(root, "Next technologies").click(); assert.equal(calls.length, count);
  assert.match(root.querySelector(".service-card").textContent, /Profile 25/);
  const filter = root.querySelector('input[name="catalog_search"]'); filter.value = "Profile 60"; filter.dispatchEvent(new dom.window.Event("input"));
  assert.equal(root.querySelectorAll(".service-card").length, 1);
  assert.ok(root.querySelector('a[href^="/lookups/?"]')); assert.ok(root.querySelector('a[href^="/discover/?"]'));
  dom.window.close();
});

test("graph tables collapse and page locally while preserving graph selection and original evidence proof", async () => {
  const nodes = Array.from({ length: 60 }, (_, i) => ({ id: "n" + i, kind: "ip", value: "192.0.2." + i, label: "IP " + i, attributes: {} }));
  const edges = Array.from({ length: 59 }, (_, i) => ({ id: "e" + i, source: "n0", target: "n" + (i + 1), relation: "observed_at", confidence: 60, evidence_source: "provider", evidence_id: "proof" + i }));
  const { dom, root, assets, calls } = setup({ "/api/assets/graph": { nodes, edges, caveats: [] }, "/api/assets/evidence/proof0": { evidence: { source: "provider", data: { fixture: true } } } }, "http://localhost/relationships/");
  await assets.render(root, "relationships"); assert.equal(root.querySelectorAll("[data-graph-node]").length, 60);
  const nodeList = root.querySelector('[data-list-id="graph-nodes"]'), edgeList = root.querySelector('[data-list-id="graph-edges"]');
  assert.ok(nodeList); assert.equal(nodeList.open, false); assert.equal(edgeList.open, false);
  assert.equal(nodeList.querySelectorAll("tbody tr").length, 25); assert.equal(edgeList.querySelectorAll("tbody tr").length, 25);
  root.querySelector('[data-graph-node="n59"]').dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  button(nodeList, "Next nodes").click(); assert.match(root.querySelector(".asset-selection").textContent, /IP 59/);
  button(edgeList, "View proof").click(); await settle();
  assert.ok(calls.some(call => call.url.pathname === "/api/assets/evidence/proof0"));
  assert.equal(calls.some(call => call.body), false);
  dom.window.close();
});

test("local lookup, organization, catalog and graph pagers preserve keyboard focus on middle and boundary pages", async () => {
  function check(root, dom, noun) {
    let next = button(root, "Next " + noun); next.focus(); next.click();
    assert.equal(dom.window.document.activeElement.textContent, "Next " + noun, noun + " middle page");
    while (!button(root, "Next " + noun).disabled) {
      next = button(root, "Next " + noun); next.focus(); next.click();
    }
    assert.equal(dom.window.document.activeElement.textContent, "Previous " + noun, noun + " last page");
    while (!button(root, "Previous " + noun).disabled) {
      const previous = button(root, "Previous " + noun); previous.focus(); previous.click();
    }
    assert.equal(dom.window.document.activeElement.textContent, "Next " + noun, noun + " first page");
  }
  const lookups=setup();await lookups.ops.render(lookups.root,"lookups");check(lookups.root,lookups.dom,"lookups");lookups.dom.window.close();
  const orgs=setup({},"http://localhost/organizations/");await orgs.assets.render(orgs.root,"organizations");check(orgs.root,orgs.dom,"organizations");orgs.dom.window.close();
  const profiles=Array.from({length:61},(_,i)=>({key:"p"+i,label:"Profile "+i,category:"ot",ports:[],queries:{}}));
  const catalog=setup({"/api/operations/catalog":{results:profiles,tools:{}}});await catalog.ops.render(catalog.root,"catalog");check(catalog.root,catalog.dom,"technologies");catalog.dom.window.close();
  const nodes=Array.from({length:60},(_,i)=>({id:"n"+i,kind:"ip",value:"192.0.2."+i,attributes:{}}));
  const graph=setup({"/api/assets/graph":{nodes,edges:[],caveats:[]}},"http://localhost/relationships/");await graph.assets.render(graph.root,"relationships");
  const table=graph.root.querySelector('[data-list-id="graph-nodes"]');table.open=true;check(table,graph.dom,"nodes");graph.dom.window.close();
});
