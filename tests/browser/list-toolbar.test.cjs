const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../leakscope_app/static/js/leakscope/list_toolbar.js'), 'utf8');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const open = [];
test.afterEach(() => open.splice(0).forEach(dom => dom.window.close()));

function setup({stored = {}, blockStorage = false, withSelection = true, withInsights = true} = {}) {
  const dom = new JSDOM('<main id="root"></main>', {url: 'http://localhost/assets/', runScripts: 'outside-only'});
  open.push(dom);
  const w = dom.window, d = w.document;
  for (const [key, value] of Object.entries(stored)) w.localStorage.setItem(key, value);
  if (blockStorage) { const fail = () => { throw Error('blocked'); }; w.Storage.prototype.getItem = fail; w.Storage.prototype.setItem = fail; }
  w.fetch = () => { throw Error('The toolbar must not make requests'); };
  w.eval(source);
  const make = (tag, attrs = {}, html = '') => { const node = d.createElement(tag); Object.assign(node, attrs); if (html) node.innerHTML = html; return node; };
  const search = make('input', {type: 'search', name: 'q', placeholder: 'Address, title or technology'});
  const technology = make('select', {name: 'technology'}, '<option value="">All technologies</option><option value="redis">redis</option>');
  const source_ = make('select', {name: 'provider'}, '<option value="">All sources</option><option value="shodan">Shodan</option>');
  const tag = make('input', {type: 'text', name: 'tag'});
  const hidden = make('input', {type: 'checkbox', name: 'hidden'});
  const state = {changes: 0, removedExtra: 0};
  const action = make('button', {type: 'button', textContent: 'Find similar'});
  const bar = w.LeakScopeListBar.create({
    page: 'assets', search,
    quick: [{label: 'Technology', control: technology}, {label: 'Source', control: source_}],
    more: [{label: 'Tag', control: tag}, {label: 'Include hidden', control: hidden}],
    extraChips: [{text: 'Organization scope: 7', remove: () => state.removedExtra++}],
    onChange: () => state.changes++,
    insights: withInsights ? make('div', {textContent: 'charts'}) : null,
    about: make('p', {textContent: 'Saved records only.'}),
    selection: withSelection ? {limit: 10, actions: [action]} : null,
  });
  d.querySelector('#root').append(bar.element, bar.footer);
  return {dom, w, d, bar, search, technology, source: source_, tag, hidden, state};
}
const chips = s => [...s.bar.element.querySelectorAll('.ls-chip')].map(node => node.textContent);
const fire = (s, node, type) => node.dispatchEvent(new s.w.Event(type, {bubbles: true}));

test('toolbar order, filter count and chips follow the controls', () => {
  const s = setup();
  const row = s.bar.element.querySelector('.ls-listbar-row');
  const order = [...row.querySelectorAll('input,select,button')].map(node => node.name || node.className.split(' ').pop());
  assert.deepEqual(order, ['q', 'technology', 'provider', 'ls-filters-toggle', 'ls-insights-toggle']);
  s.technology.value = 'redis'; s.tag.value = 'prod';
  s.bar.refreshChips();
  assert.deepEqual(chips(s), ['Technology: redis', 'Tag: prod', 'Organization scope: 7']);
  assert.equal(s.bar.element.querySelector('.ls-filters-toggle').textContent, 'Filters · 1');
  assert.equal(s.bar.element.querySelector('.ls-filters-panel').hidden, true);
  assert.equal(s.bar.element.querySelector('.ls-chip').getAttribute('aria-label'), 'Remove filter: Technology: redis');
  s.bar.element.querySelector('.ls-filters-toggle').click();
  assert.equal(s.bar.element.querySelector('.ls-filters-panel').hidden, false);
  assert.ok(s.bar.element.querySelector('.ls-filters-panel').contains(s.tag));
});

test('removing a chip clears its control and reports one change', () => {
  const s = setup();
  s.technology.value = 'redis'; s.hidden.checked = true; s.bar.refreshChips();
  assert.deepEqual(chips(s), ['Technology: redis', 'Include hidden', 'Organization scope: 7']);
  s.bar.element.querySelector('.ls-chip').click();
  assert.equal(s.technology.value, ''); assert.equal(s.state.changes, 1);
  assert.deepEqual(chips(s), ['Include hidden', 'Organization scope: 7']);
  [...s.bar.element.querySelectorAll('.ls-chip')].pop().click();
  assert.equal(s.state.removedExtra, 1); assert.equal(s.state.changes, 2);
  assert.deepEqual(chips(s), ['Include hidden']);
});

test('typing is debounced and select changes report at once', async () => {
  const s = setup();
  for (const value of ['r', 're', 'red']) { s.search.value = value; fire(s, s.search, 'input'); await wait(40); }
  assert.equal(s.state.changes, 0);
  await wait(350);
  assert.equal(s.state.changes, 1);
  s.source.value = 'shodan'; fire(s, s.source, 'change');
  assert.equal(s.state.changes, 2);
  s.search.value = 'redis'; fire(s, s.search, 'input');
  s.source.value = ''; fire(s, s.source, 'change');
  await wait(350);
  assert.equal(s.state.changes, 3, 'a pending typed change is folded into the immediate one');
});

test('clear all resets controls, removes extra chips and reports once', () => {
  const s = setup();
  s.search.value = 'acme'; s.technology.value = 'redis'; s.tag.value = 'prod'; s.hidden.checked = true; s.bar.refreshChips();
  [...s.bar.element.querySelectorAll('button')].find(node => node.textContent === 'Clear all').click();
  assert.equal(s.search.value, ''); assert.equal(s.technology.value, ''); assert.equal(s.tag.value, ''); assert.equal(s.hidden.checked, false);
  assert.equal(s.state.removedExtra, 1); assert.equal(s.state.changes, 1);
  assert.equal(s.bar.element.querySelectorAll('.ls-chip').length, 0);
  assert.equal(s.bar.element.querySelector('.ls-chips').hidden, true);
  assert.equal(s.bar.element.querySelector('.ls-filters-toggle').textContent, 'Filters');
});

test('selection bar appears only while rows are selected and names the limit', () => {
  const s = setup(), sel = s.bar.element.querySelector('.ls-selection');
  assert.equal(sel.hidden, true);
  s.bar.setSelected(3);
  assert.equal(sel.hidden, false);
  assert.equal(sel.querySelector('.ls-selection-count').textContent, '3 selected · up to 10 per batch');
  assert.ok([...sel.querySelectorAll('button')].some(node => node.textContent === 'Find similar'));
  let cleared = 0; s.bar.element.addEventListener('ls:clear-selection', () => cleared++);
  [...sel.querySelectorAll('button')].find(node => node.textContent === 'Clear').click();
  assert.equal(cleared, 1);
  s.bar.setSelected(0);
  assert.equal(sel.hidden, true);
});

test('insights toggle remembers its state and survives blocked storage', () => {
  let s = setup();
  const region = s.bar.element.querySelector('.ls-insights'), toggle = s.bar.element.querySelector('.ls-insights-toggle');
  assert.equal(region.hidden, true); assert.equal(toggle.getAttribute('aria-pressed'), 'false');
  toggle.click();
  assert.equal(region.hidden, false); assert.equal(toggle.getAttribute('aria-pressed'), 'true');
  assert.equal(s.w.localStorage.getItem('leakscope.listbar.insights.assets'), 'true');
  s = setup({stored: {'leakscope.listbar.insights.assets': 'true'}});
  assert.equal(s.bar.element.querySelector('.ls-insights').hidden, false);
  s = setup({blockStorage: true});
  assert.equal(s.bar.element.querySelector('.ls-insights').hidden, true);
  s.bar.element.querySelector('.ls-insights-toggle').click();
  assert.equal(s.bar.element.querySelector('.ls-insights').hidden, false);
});

test('count, about and pager share the footer; disposal stops pending reports', async () => {
  const s = setup();
  s.bar.setCount('1,796 matching endpoints');
  assert.equal(s.bar.footer.querySelector('.ls-count').textContent, '1,796 matching endpoints');
  const about = s.bar.footer.querySelector('.ls-about');
  assert.equal(about.hidden, true);
  s.bar.footer.querySelector('.ls-about-toggle').click();
  assert.equal(about.hidden, false); assert.match(about.textContent, /Saved records only/);
  assert.ok(s.bar.pager.classList.contains('ls-pager'));
  s.search.value = 'x'; fire(s, s.search, 'input');
  s.bar.dispose();
  await wait(350);
  assert.equal(s.state.changes, 0);
});
