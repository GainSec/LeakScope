const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const script = path.resolve(__dirname, '../../leakscope_app/static/js/leakscope/relationship_map.js');

function setup(options = {}) {
  const dom = new JSDOM('<!doctype html><main></main>', { runScripts: 'outside-only', url: 'http://localhost/relationships/' });
  if (fs.existsSync(script)) dom.window.eval(fs.readFileSync(script, 'utf8'));
  assert.equal(typeof dom.window.LeakScopeRelationshipMap?.create, 'function', 'relationship map API exists');
  const map = dom.window.LeakScopeRelationshipMap.create(options);
  dom.window.document.querySelector('main').append(map.element);
  return { dom, map, root: map.element };
}
const nodes = [
  { id: 'org', kind: 'organization', value: 'Example Org' },
  { id: 'domain', kind: 'domain', value: 'example.test' },
  { id: 'ip', kind: 'ip', value: '192.0.2.1' },
  { id: 'alone', kind: 'service', value: 'isolated.example:42' },
];
const edges = [
  { id: 'scope', source: 'org', target: 'domain', relation: 'user_scope', evidence_source: 'user', confidence: 100 },
  { id: 'dns-old', source: 'domain', target: 'ip', relation: 'resolves_to', evidence_source: 'dns', confidence: 90, expired: true, historical: true },
  { id: 'dns-new', source: 'domain', target: 'ip', relation: 'resolves_to', evidence_source: 'dns', confidence: 90, expired: false, historical: true },
];

test('public identifiers have a readable typed lane', () => {
  const {dom,root}=setup({nodes:[...nodes,{id:'identifier',kind:'identifier',value:'public-app:fixture'}],edges});
  const lane=root.querySelector('.ls-rel-lane[data-kind="identifier"]');
  assert.ok(lane);assert.match(lane.textContent,/Public identifiers/);
  assert.equal(root.querySelector('[data-rel-node="identifier"]').dataset.kind,'identifier');
  dom.window.close();
});

test('typed lanes position actual asset kinds deterministically and select exact IDs', () => {
  let selected;
  const { dom, map, root } = setup({ nodes, edges, onNode: node => selected = node.id });
  const lanes = [...root.querySelectorAll('.ls-rel-lane')].map(n => n.dataset.kind);
  assert.deepEqual(lanes, ['organization', 'domain', 'ip', 'service']);
  assert.equal(root.querySelector('[data-rel-node="domain"]').dataset.kind, 'domain');
  root.querySelector('[data-rel-node="domain"]').click();
  assert.equal(selected, 'domain');
  assert.equal(root.querySelectorAll('.ls-rel-edge.is-adjacent').length, 3);
  assert.match(root.querySelector('.ls-rel-selection').textContent, /example.test/);
  map.select('ip');
  assert.equal(selected, 'domain', 'external select must not invoke callbacks');
  assert.equal(root.querySelector('[data-rel-node="ip"]').getAttribute('aria-pressed'), 'true');
  dom.window.close();
});

test('focused paths expose relation direction and exact edge callbacks by keyboard', () => {
  let selected;
  const { dom, root, map } = setup({ nodes, edges, onEdge: edge => selected = edge.id });
  map.select('domain');
  const edge = root.querySelector('[data-rel-edge="dns-new"]');
  assert.match(edge.getAttribute('aria-label'), /example.test.*resolves to.*192.0.2.1/);
  assert.ok(edge.querySelector('[marker-end]'));
  edge.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  assert.equal(selected, 'dns-new');
  assert.match(root.querySelector('.ls-rel-selection').textContent, /Saved historical evidence/);
  assert.doesNotMatch(root.querySelector('.ls-rel-selection').textContent, /expired/i);
  dom.window.close();
});

test('matrix preserves direction and each independent evidence record', () => {
  const chosen = [];
  const { dom, root } = setup({ nodes, edges, onEdge: edge => chosen.push(edge.id) });
  root.querySelector('[data-rel-view="matrix"]').click();
  const forward = root.querySelector('[data-rel-source="domain"][data-rel-target="ip"]');
  assert.match(forward.getAttribute('aria-label'), /2 saved relationships/);
  assert.equal(root.querySelector('[data-rel-source="ip"][data-rel-target="domain"]'), null);
  forward.click();
  const records = root.querySelectorAll('.ls-rel-evidence-option');
  assert.equal(records.length, 2);
  assert.match(records[0].textContent, /Expired/);
  assert.doesNotMatch(records[1].textContent, /Expired/);
  records[1].click();
  assert.deepEqual(chosen, ['dns-new']);
  dom.window.close();
});

test('search preserves saved direct neighbors and isolates never imply ownership', () => {
  const { dom, root } = setup({ nodes, edges });
  const search = root.querySelector('input[type="search"]');
  search.value = 'example.test';
  search.dispatchEvent(new dom.window.Event('input'));
  assert.equal(root.querySelectorAll('[data-rel-node]').length, 3);
  assert.match(root.querySelector('.ls-rel-counts').textContent, /1 match.*2 direct neighbors/);
  search.value = 'isolated'; search.dispatchEvent(new dom.window.Event('input'));
  assert.equal(root.querySelectorAll('[data-rel-node]').length, 1);
  root.querySelector('[data-rel-node="alone"]').click();
  assert.match(root.querySelector('.ls-rel-selection').textContent, /No saved relationships/);
  assert.match(root.textContent, /do not establish ownership/);
  dom.window.close();
});

test('malicious labels stay text and empty subset stays usable', () => {
  const payload = '<img src=x onerror=alert(1)>';
  const { dom, root } = setup({ nodes: [{ id: 'evil', kind: 'domain', value: payload }], edges: [] });
  assert.equal(root.querySelector('img'), null);
  assert.match(root.textContent, /<img src=x/);
  const search = root.querySelector('input'); search.value = 'no matches'; search.dispatchEvent(new dom.window.Event('input'));
  assert.match(root.textContent, /No saved assets match/);
  root.querySelector('[data-rel-clear]').click();
  assert.equal(root.querySelectorAll('[data-rel-node]').length, 1);
  dom.window.close();
});

test('explicit graph and matrix bounds prevent silently presenting complete coverage', () => {
  const many = Array.from({ length: 180 }, (_, i) => ({ id: 'n' + i, kind: 'domain', value: 'n' + String(i).padStart(3, '0') + '.example' }));
  const links = Array.from({ length: 550 }, (_, i) => ({ id: 'e' + i, source: 'n0', target: 'n1', relation: 'related', evidence_source: 'test' }));
  const { dom, root } = setup({ nodes: many, edges: links });
  assert.equal(root.querySelectorAll('[data-rel-node]').length, 150);
  assert.equal(root.querySelectorAll('[data-rel-edge]').length, 500);
  assert.match(root.textContent, /150 of 180 supplied assets/);
  assert.match(root.textContent, /500 of 550 supplied relationships/);
  root.querySelector('[data-rel-view="matrix"]').click();
  assert.match(root.textContent, /60 of 150 assets/);
  assert.equal(root.querySelectorAll('.ls-rel-matrix thead th[scope="col"]').length, 60);
  dom.window.close();
});

test('filtering a late alphabetic match compacts lanes without changing source data', () => {
  const values = Array.from({ length: 40 }, (_, i) => ({ id: 'd' + i, kind: 'domain', value: 'name' + String(i).padStart(2, '0') + '.example' }));
  const snapshot = JSON.stringify(values);
  const { dom, root } = setup({ nodes: values, edges: [] });
  const firstPosition = root.querySelector('[data-rel-node="d0"]').style.top;
  const search = root.querySelector('input'); search.value = 'name39'; search.dispatchEvent(new dom.window.Event('input'));
  assert.equal(root.querySelector('[data-rel-node="d39"]').style.top, firstPosition, 'filtered matches must remain immediately visible');
  assert.equal(JSON.stringify(values), snapshot);
  dom.window.close();
});

test('bounded matrix retains later-kind shared IP hubs instead of an empty domain-only square', () => {
  const values = Array.from({ length: 75 }, (_, i) => ({ id: 'domain' + i, kind: 'domain', value: 'd' + i + '.example' }));
  values.push({ id: 'ip-hub', kind: 'ip', value: '192.0.2.80' });
  const links = values.slice(0, 75).map((node, i) => ({ id: 'edge' + i, source: node.id, target: 'ip-hub', relation: 'resolves_to' }));
  const { dom, map, root } = setup({ nodes: values, edges: links });
  root.querySelector('[data-rel-view="matrix"]').click();
  assert.ok(root.querySelector('[data-rel-target="ip-hub"]'), 'a connected IP hub must remain visible beyond the first60 domains');
  assert.equal(root.querySelectorAll('.ls-rel-matrix-cell').length, 59);
  map.select('domain74');
  assert.ok(root.querySelector('[data-rel-source="domain74"][data-rel-target="ip-hub"]'), 'external selection can bring an out-of-bounds asset into the matrix');
  dom.window.close();
});

test('activating a focused evidence option retains logical keyboard focus after replacement', () => {
  let chosen;
  const { dom, map, root } = setup({ nodes, edges, onEdge: edge => chosen = edge.id });
  map.select('domain');
  const option = root.querySelector('[data-rel-evidence="dns-new"]');
  option.focus();
  assert.equal(dom.window.document.activeElement, option);
  option.click();
  const title = root.querySelector('.ls-rel-selection-title');
  assert.equal(chosen, 'dns-new');
  assert.equal(dom.window.document.activeElement, title, 'focus should move to the selected evidence heading rather than the page body');
  assert.equal(title.tabIndex, -1);
  assert.match(title.textContent, /example.test.*resolves to.*192.0.2.1/);
  dom.window.close();
});

test('external selection and filtering never steal focus from search or outside controls', () => {
  const { dom, map, root } = setup({ nodes, edges });
  const search = root.querySelector('input[type="search"]');
  search.focus();
  map.select('domain');
  assert.equal(dom.window.document.activeElement, search);
  search.value = 'example.test'; search.dispatchEvent(new dom.window.Event('input'));
  assert.equal(dom.window.document.activeElement, search);
  const toggle = root.querySelector('[data-rel-view="matrix"]'); toggle.focus();
  map.select('dns-new');
  assert.equal(dom.window.document.activeElement, toggle);
  dom.window.close();
});

test('overlap matrix remains visible when the workspace collapses large saved-result tables', () => {
  const values = Array.from({ length: 60 }, (_, i) => ({ id: 'n' + i, kind: 'domain', value: 'n' + i + '.example' }));
  const { dom, root } = setup({ nodes: values, edges: [{ id: 'saved', source: 'n0', target: 'n1', relation: 'related' }] });
  dom.window.eval(fs.readFileSync(path.join(path.dirname(script), 'tables.js'), 'utf8'));
  root.querySelector('[data-rel-view="matrix"]').click();
  const matrix = root.querySelector('.ls-rel-matrix');
  dom.window.LeakScopeTables.collapse(matrix);
  assert.equal(matrix.closest('details:not([open])'), null, 'a matrix is a visible visualization, not a collapsible results table');
  assert.equal(root.querySelectorAll('.ls-rel-matrix tbody tr').length, 60);
  assert.ok(root.querySelector('[data-rel-source="n0"][data-rel-target="n1"]'));
  dom.window.close();
});
