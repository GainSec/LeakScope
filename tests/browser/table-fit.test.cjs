const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
const base = path.resolve(__dirname, '../../leakscope_app/static/js/leakscope');
function setup({observer = false} = {}) {
  const dom = new JSDOM('<main id="page-root"></main>', {url: 'http://localhost/certificates/', runScripts: 'outside-only'});
  const w = dom.window, watched = [];
  if (observer) w.ResizeObserver = class { constructor(callback) { this.callback = callback; } observe(node) { watched.push({node, fire: width => this.callback([{target: node, contentRect: {width}}])}); } disconnect() {} };
  w.eval(fs.readFileSync(path.join(base, 'tables.js'), 'utf8'));
  return {dom, w, d: w.document, T: w.LeakScopeTables, root: w.document.querySelector('main'), watched};
}
function table(s, headers = ['Select', 'Subject', 'Names', 'Issuer', 'Watches', 'First seen', 'Last seen', 'Actions'], rows = 2) {
  const box = s.d.createElement('div'); box.className = 'table-scroll';
  const t = s.d.createElement('table');
  t.innerHTML = '<thead><tr>' + headers.map(h => '<th>' + h + '</th>').join('') + '</tr></thead><tbody>' + Array.from({length: rows}, () => '<tr>' + headers.map(h => '<td>' + h.toLowerCase() + ' value</td>').join('') + '</tr>').join('') + '</tbody>';
  box.append(t); s.root.append(box); return t;
}

test('fit marks a table, names every body cell after its column and is safe to repeat', () => {
  const s = setup(), t = table(s);
  s.T.enhance(t);
  assert.equal(s.T.fit(t), true);
  assert.ok(t.classList.contains('ls-fit'));
  assert.deepEqual([...t.tBodies[0].rows[1].cells].map(c => c.dataset.label), ['Select', 'Subject', 'Names', 'Issuer', 'Watches', 'First seen', 'Last seen', 'Actions']);
  const row = t.tBodies[0].insertRow(); for (let i = 0; i < 8; i++) row.insertCell();
  s.T.fit(t);
  assert.equal(row.cells[3].dataset.label, 'Issuer');
  s.dom.window.close();
});

test('matrices and opted-out tables are left alone', () => {
  const s = setup(), matrix = table(s), opted = table(s), headless = s.d.createElement('table');
  matrix.dataset.sortable = 'false'; opted.dataset.fit = 'false'; headless.innerHTML = '<tbody><tr><td>x</td></tr></tbody>'; s.root.append(headless);
  for (const t of [matrix, opted, headless]) { assert.equal(s.T.fit(t), false); assert.equal(t.classList.contains('ls-fit'), false); }
  s.dom.window.close();
});

test('a table stacks when its space is too narrow for its columns and unstacks when there is room', () => {
  assert.equal(setup().T.shouldStack(1140, 7), false);
  const s = setup({observer: true}), t = table(s);
  assert.equal(s.T.shouldStack(800, 7), true);
  assert.equal(s.T.shouldStack(980, 7), false);
  assert.equal(s.T.shouldStack(500, 3), false);
  assert.equal(s.T.shouldStack(340, 3), true);
  assert.equal(s.T.shouldStack(0, 5), false, 'an unmeasured table is not stacked');
  s.T.fit(t); s.T.fit(t);
  assert.equal(s.watched.length, 1, 'observed once');
  s.watched[0].fire(700); assert.equal(t.classList.contains('is-stacked'), true);
  s.watched[0].fire(1200); assert.equal(t.classList.contains('is-stacked'), false);
  s.dom.window.close();
});

test('the shell fits every table it enhances, including ones added later', async () => {
  const s = setup();
  s.w.matchMedia = () => ({matches: false, addEventListener() {}});
  s.w.eval(fs.readFileSync(path.join(base, 'workspace.js'), 'utf8'));
  const t = table(s);
  await new Promise(r => setTimeout(r, 10));
  assert.ok(t.classList.contains('ls-fit'));
  const row = t.tBodies[0].insertRow(); for (let i = 0; i < 8; i++) row.insertCell();
  await new Promise(r => setTimeout(r, 10));
  assert.equal(row.cells[1].dataset.label, 'Subject');
  s.dom.window.close();
});

test('a table whose content still overflows is tightened, then stacked, by measurement', () => {
  const s = setup(), t = table(s, ['Kind', 'Shared value', 'Endpoints'], 1), box = t.parentElement;
  let wide = () => t.classList.contains('is-tight') ? 500 : 700;
  Object.defineProperty(box, 'clientWidth', {get: () => 500});
  Object.defineProperty(box, 'scrollWidth', {get: () => wide()});
  s.T.fit(t);
  assert.equal(t.classList.contains('is-tight'), true);
  assert.equal(t.classList.contains('is-stacked'), false);
  wide = () => t.classList.contains('is-stacked') ? 500 : 640;
  s.T.fit(t);
  assert.equal(t.classList.contains('is-tight'), true);
  assert.equal(t.classList.contains('is-stacked'), true);
  wide = () => 500;
  s.T.fit(t);
  assert.equal(t.classList.contains('is-tight'), false);
  assert.equal(t.classList.contains('is-stacked'), false);
  s.dom.window.close();
});
