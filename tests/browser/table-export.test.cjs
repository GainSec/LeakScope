const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
const base = path.resolve(__dirname, '../../leakscope_app/static/js/leakscope');
function setup() {
  const dom = new JSDOM('<main id="page-root"><h1>Saved results</h1></main>', {url: 'http://localhost/assets/', runScripts: 'outside-only'});
  const w = dom.window, saved = [];
  w.URL.createObjectURL = blob => { saved.push(blob); return 'blob:x'; }; w.URL.revokeObjectURL = () => {};
  w.HTMLAnchorElement.prototype.click = function () { saved[saved.length - 1].name = this.download; };
  for (const file of ['tables.js', 'table_export.js']) w.eval(fs.readFileSync(path.join(base, file), 'utf8'));
  return {dom, w, d: w.document, X: w.LeakScopeExport, T: w.LeakScopeTables, root: w.document.querySelector('main'), saved};
}
function table(s, headers, rows) {
  const box = s.d.createElement('div'); box.className = 'table-scroll';
  const t = s.d.createElement('table');
  t.innerHTML = '<thead><tr>' + headers.map(h => '<th>' + h + '</th>').join('') + '</tr></thead><tbody>' + rows.map(r => '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</tbody>';
  box.append(t); s.root.append(box); return t;
}

test('data keeps named, visible columns and readable cell text', () => {
  const s = setup();
  const t = table(s, ['Select', 'Address', 'Port', '', 'Seen', 'Actions'], [
    ['<input type="checkbox">', '<button>45.33.32.156</button>', ' 80\n ', '<button>Open</button>', '<time datetime="2026-11-15">today</time><span hidden>secret</span>', '<button>Delete</button>'],
    ['<input type="checkbox">', 'scanme.nmap.org', '—', '', 'x', ''],
  ]);
  t.tHead.rows[0].cells[4].hidden = true;
  assert.deepEqual(JSON.parse(JSON.stringify(s.X.data(t))), {headers: ['Address', 'Port'], rows: [['45.33.32.156', '80'], ['scanme.nmap.org', '']]});
  s.dom.window.close();
});

test('stacked parts of a cell are separate words; search highlights stay joined', () => {
  const s = setup(), t = table(s, ['Operation', 'Address'], [['<span class="cell-title">Discover</span><span class="cell-sub">discovery</span>', '<mark>45.33</mark>.32.156']]);
  assert.deepEqual(JSON.parse(JSON.stringify(s.X.data(t).rows)), [['Discover discovery', '45.33.32.156']]);
  s.dom.window.close();
});

test('the placeholder row of an empty table is not exported', () => {
  const s = setup(), t = table(s, ['Name'], []);
  t.tBodies[0].innerHTML = '<tr data-empty="true"><td colspan="1">No saved rows</td></tr><tr><td colspan="3">Nothing here</td></tr>';
  assert.equal(s.X.data(t).rows.length, 0);
  s.dom.window.close();
});

test('CSV quotes what needs quoting and stores formulas as text; TXT is one tab-separated line per row', () => {
  const s = setup(), source = {headers: ['Name', 'Note'], rows: [['a,b', 'said "hi"'], ['=SUM(A1)', 'two\nlines'], ['-1', '@x']]};
  assert.equal(s.X.csv(source), 'Name,Note\r\n"a,b","said ""hi"""\r\n\'=SUM(A1),two lines\r\n\'-1,\'@x\r\n');
  assert.equal(s.X.text(source), 'Name\tNote\na,b\tsaid "hi"\n=SUM(A1)\ttwo lines\n-1\t@x\n');
  s.dom.window.close();
});

test('every table with rows gets one export bar; empty and opted-out tables do not show one', () => {
  const s = setup();
  const full = table(s, ['Name'], [['a'], ['b']]), empty = table(s, ['Name'], []), opted = table(s, ['Name'], [['a']]);
  opted.dataset.export = 'false';
  s.X.sweep(); s.X.sweep();
  assert.equal(s.root.querySelectorAll('.ls-export').length, 2);
  const bar = full.parentElement.previousElementSibling;
  assert.ok(bar.matches('.ls-export') && !bar.hidden);
  assert.deepEqual([...bar.querySelectorAll('button')].map(b => b.textContent), ['Export CSV', 'Export TXT']);
  assert.equal(empty.parentElement.previousElementSibling.hidden, true);
  assert.equal(opted.parentElement.previousElementSibling.matches('.ls-export'), false);
  empty.tBodies[0].innerHTML = '<tr><td>now</td></tr>'; s.X.sweep();
  assert.equal(empty.parentElement.previousElementSibling.hidden, false);
  full.parentElement.remove(); s.X.sweep();
  assert.equal(s.root.querySelectorAll('.ls-export').length, 1, 'the bar leaves with its table');
  s.dom.window.close();
});

test('the buttons save a file named after the table and the date', () => {
  const s = setup(), t = table(s, ['Name'], [['a']]);
  t.createCaption().textContent = 'Open ports · sorted';
  s.X.sweep();
  const [csv, txt] = t.parentElement.previousElementSibling.querySelectorAll('button');
  csv.click(); txt.click();
  assert.match(s.saved[0].name, /^open-ports-\d{4}-\d\d-\d\d\.csv$/);
  assert.match(s.saved[1].name, /^open-ports-\d{4}-\d\d-\d\d\.txt$/);
  assert.equal(s.saved[0].type, 'text/csv;charset=utf-8');
  s.dom.window.close();
});

test('a table without a caption is named after its panel heading, then the page', () => {
  const s = setup(), t = table(s, ['Name'], [['a']]);
  assert.equal(s.X.title(t), 'Saved results');
  s.T.enhance(t, {scope: 'Sort all 1 loaded jobs'});
  assert.equal(s.X.title(t), 'Saved results', 'the sort note is not the name');
  const panel = s.d.createElement('section'); panel.innerHTML = '<h3>DNS records</h3>'; panel.append(t.parentElement); s.root.append(panel);
  assert.equal(s.X.title(t), 'DNS records');
  s.dom.window.close();
});

test('a paged table exports every row matching its search, not only the page shown', () => {
  const s = setup(), t = table(s, ['Name'], Array.from({length: 60}, (_, i) => ['row ' + i]));
  const shell = s.T.browse(t, {state: {query: '', sort: {key: null, direction: 'asc'}, page: 1, pageSize: 25, filters: {}}});
  s.root.append(shell);
  assert.equal(t.tBodies[0].rows.length, 25);
  assert.equal(s.X.data(t).rows.length, 60);
  const search = shell.querySelector('input[type=search]'); search.value = 'row 5'; search.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  assert.equal(s.X.data(t).rows.length, 11);
  s.X.sweep();
  assert.ok(shell.previousElementSibling.matches('.ls-export'), 'the bar sits above the whole browser');
  s.dom.window.close();
});

test('a folded-away table keeps its export bar outside the fold', () => {
  const s = setup(), t = table(s, ['Name'], Array.from({length: 30}, (_, i) => ['row ' + i]));
  s.X.sweep();
  const section = s.T.collapse(t, {title: 'Job history'});
  assert.ok(section, 'long tables fold');
  s.X.sweep();
  assert.equal(s.root.querySelectorAll('.ls-export').length, 1);
  assert.ok(t.closest('.ls-results-section').previousElementSibling.matches('.ls-export'));
  assert.equal(s.X.title(t), 'Saved results', 'the fold is not the name');
  s.dom.window.close();
});

test('bar() exports rows that are not a table', () => {
  const s = setup(), bar = s.X.bar(() => ({headers: ['Step'], rows: [['one']]}), 'Run steps');
  s.root.append(bar); bar.querySelector('[data-export=txt]').click();
  assert.match(s.saved[0].name, /^run-steps-.*\.txt$/);
  s.dom.window.close();
});

test('card lists export one row per card: its name and its details, without buttons', () => {
  const s = setup();
  s.root.insertAdjacentHTML('beforeend', '<div class="ops-lookup-items">' + ['Daily elastic', 'Weekly mongo'].map(n => '<section class="panel"><h2>' + n + '</h2><p>Shodan</p><span>every day</span><button>Run now</button><table><thead><tr><th>x</th></tr></thead><tbody><tr><td>inner</td></tr></tbody></table></section>').join('') + '</div>');
  s.X.sweep(); s.X.sweep();
  const list = s.root.querySelector('.ops-lookup-items'), bar = list.previousElementSibling;
  assert.ok(bar.matches('.ls-export[data-cards]'));
  assert.equal(s.root.querySelectorAll('.ls-export[data-cards]').length, 1);
  bar.querySelector('[data-export=csv]').click();
  assert.match(s.saved[0].name, /^scheduled-lookups-/);
  assert.deepEqual(JSON.parse(JSON.stringify(s.X.cards([...list.children]))), {headers: ['Name', 'Details'], rows: [['Daily elastic', 'Shodan · every day'], ['Weekly mongo', 'Shodan · every day']]});
  list.remove(); s.X.sweep();
  assert.equal(s.root.querySelectorAll('.ls-export[data-cards]').length, 0);
  s.dom.window.close();
});

test('a table with a server export asks the server for every match instead of the loaded rows', () => {
  const s = setup(), t = table(s, ['Certificate'], [['a'], ['b']]), asked = [];
  t.dataset.exportUrl = '/export/certificates/?q=example.com';
  s.w.HTMLAnchorElement.prototype.click = function () { asked.push(this.getAttribute('href')); };
  const bar = s.X.attach(t);
  bar.querySelector('[data-export=txt]').click();
  assert.deepEqual(asked, ['/export/certificates/?q=example.com&format=txt']);
  assert.equal(s.saved.length, 0, 'nothing is built in the browser');
  assert.match(bar.title, /Every matching row/);
  s.dom.window.close();
});

test('server download links take one format each and refuse another site', () => {
  const s = setup(), asked = [];
  s.w.HTMLAnchorElement.prototype.click = function () { asked.push(this.getAttribute('href')); };
  const bar = s.X.links('/export/organizations/abc/', [['HTML report', 'html'], ['CSV', 'csv']], 'Export this organization');
  assert.equal(bar.dataset.export, 'false');
  assert.deepEqual([...bar.querySelectorAll('button')].map(b => b.textContent), ['HTML report', 'CSV']);
  bar.querySelectorAll('button')[1].click();
  s.X.fetchFile('//evil.example/x'); s.X.fetchFile('https://evil.example/x'); s.X.fetchFile('javascript:alert(1)');
  assert.deepEqual(asked, ['/export/organizations/abc/?format=csv']);
  s.dom.window.close();
});

test('a table inside an element marked with a server export uses it; chosen items are posted with the CSRF token', () => {
  const s = setup(), t = table(s, ['Address'], [['a']]), asked = [];
  t.parentElement.parentElement.dataset.exportUrl = '/export/table/observations/?q=x';
  s.w.HTMLAnchorElement.prototype.click = function () { asked.push(this.getAttribute('href')); };
  s.X.attach(t).querySelector('[data-export=csv]').click();
  assert.deepEqual(asked, ['/export/table/observations/?q=x&format=csv']);
  const meta = s.d.createElement('meta'); meta.name = 'csrf-token'; meta.content = 'tok'; s.d.head.append(meta);
  let sent = null;
  s.w.HTMLFormElement.prototype.submit = function () { sent = {action: this.getAttribute('action'), method: this.method, fields: Object.fromEntries([...this.elements].map(i => [i.name, i.value]))}; };
  s.X.postFile('/export/screenshots/', {ids: 'a,b', q: 'x'});
  assert.deepEqual(sent, {action: '/export/screenshots/', method: 'post', fields: {csrfmiddlewaretoken: 'tok', ids: 'a,b', q: 'x'}});
  sent = null; s.X.postFile('https://evil.example/', {ids: 'a'}); assert.equal(sent, null);
  s.dom.window.close();
});
