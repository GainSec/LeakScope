const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../leakscope_app/static/js/leakscope/build.js'), 'utf8');
const wait = (ms = 15) => new Promise(resolve => setTimeout(resolve, ms));
const open = [];
test.afterEach(() => open.splice(0).forEach(s => { s.w.LeakScopeBuild.stop(); s.dom.window.close(); }));

const field = (key, label, type, extra = {}) => ({key, label, type, required: false, default: null, choices: [], minimum: null, maximum: null, help: '', ...extra});
const BLOCKS = [
  {key: 'run_by_hand', says: 'Starts with nothing.', name: 'Run by hand', group: 'trigger', reach: 'local', start: true, takes: null, gives: 'value', fields: [], output_fields: []},
  {key: 'saved_results', says: 'Starts with observations LeakScope already holds.', name: 'Saved results that match', group: 'trigger', reach: 'local', start: true, takes: null, gives: 'endpoint', output_fields: [],
    fields: [field('technology', 'Technology', 'text'), field('limit', 'At most', 'number', {default: 100, minimum: 1, maximum: 5000})]},
  {key: 'search', says: 'Runs one provider query.', name: 'Search a provider', group: 'find', reach: 'credits', start: false, takes: 'any', gives: 'endpoint', output_fields: [],
    fields: [field('provider', 'Provider', 'choice', {required: true, choices: [['shodan', 'Shodan'], ['zoomeye', 'ZoomEye']]}), field('query', 'Query', 'text', {required: true})]},
  {key: 'filter', says: 'Keeps results whose fields match.', name: 'Filter', group: 'logic', reach: 'local', start: false, takes: 'endpoint', gives: 'endpoint', output_fields: [], fields: [field('conditions', 'Keep when', 'conditions', {required: true})]},
  {key: 'capture', says: 'One web request to each result.', name: 'Capture', group: 'collect', reach: 'target', start: false, takes: 'endpoint', gives: 'endpoint', output_fields: [],
    fields: [field('action', 'What to collect', 'choice', {required: true, choices: [['httpx', 'HTTP fingerprint'], ['screenshot', 'Screenshot']]})]},
  {key: 'tag', says: 'Adds tags.', name: 'Tag', group: 'organize', reach: 'local', start: false, takes: 'endpoint', gives: 'endpoint', output_fields: [], fields: [field('tags', 'Tags', 'tags', {required: true})]},
];
const CATALOG = {blocks: BLOCKS, limits: {defaults: {pages: 5, contacts: 25, items: 500}, max: {pages: 200, contacts: 1000, items: 5000}},
  conditions: {fields: ['address', 'port', 'technology', 'tags'], ops: [['is', 'is'], ['one_of', 'is one of'], ['empty', 'is empty']]}};
const STEPS = [{id: 's1', block: 'saved_results', settings: {technology: 'jenkins', limit: 50}},
  {id: 's2', block: 'capture', settings: {action: 'httpx'}}, {id: 's3', block: 'tag', settings: {tags: ['built']}}];
const ESTIMATE = {pages: 0, contacts: 25, items: 50, limits: CATALOG.limits.defaults, steps: [], token: 'tok-1'};
const detail = (extra = {}) => ({id: 'w1', name: 'Jenkins sweep', definition: {steps: STEPS}, problems: '', estimate: ESTIMATE, runs: [], ...extra});
const runView = (state, extra = {}) => ({run: {id: 'r1', state, can_cancel: ['queued', 'running'].includes(state), error: ''}, spent: {pages: 0, contacts: 2}, limits: CATALOG.limits.defaults,
  partial: false, stopped_at: null, workflow: {id: 'w1', name: 'Jenkins sweep'},
  steps: [{id: 's1', name: 'Saved results that match', state: 'done', items_in: 0, items_out: 3, pages: 0, contacts: 0, note: '3 saved results'},
    {id: 's2', name: 'Capture', state: state === 'running' ? 'waiting' : state === 'cancelled' ? 'cancelled' : 'done', items_in: 3, items_out: 2, passed_by: 1, pages: 0, contacts: 2, note: '2 captured · 1 not queued'}], ...extra});

function setup(routes = {}, url = 'http://localhost/build/w1/') {
  const dom = new JSDOM('<main id="root"></main>', {url, runScripts: 'outside-only', pretendToBeVisual: true});
  const w = dom.window, calls = [];
  const table = {'GET /api/build/blocks': CATALOG, 'GET /api/build/workflows/w1': detail(), 'GET /api/build/workflows': {results: []}, ...routes};
  w.fetch = async (path, options = {}) => {
    const method = options.method || 'GET', key = method + ' ' + path;
    calls.push({key, body: options.body ? JSON.parse(options.body) : undefined});
    let value = table[key];
    if (typeof value === 'function') value = await value(calls[calls.length - 1]);
    if (value === undefined) throw Error('Unexpected request: ' + key);
    const status = value && value.__status || 200;
    return {ok: status < 400, status, json: async () => value};
  };
  w.eval(source);
  const s = {dom, w, d: w.document, calls, root: w.document.querySelector('#root')};
  open.push(s);
  return s;
}
const click = (s, text, scope) => { const node = [...(scope || s.root).querySelectorAll('button')].find(b => b.textContent.trim() === text); assert.ok(node, 'button ' + text); node.click(); return node; };
const names = s => [...s.root.querySelectorAll('.build-step-title strong')].map(node => node.textContent);
const posts = (s, suffix) => s.calls.filter(call => call.key.startsWith('POST') && call.key.endsWith(suffix));

test('the list shows workflows with reach as words, and an empty state', async () => {
  const s = setup({'GET /api/build/workflows': {results: [{id: 'w1', name: 'Jenkins sweep', steps: 3, reaches: ['local', 'target'], groups: ['trigger', 'collect', 'organize'], last_run: {state: 'succeeded', partial: true}}]}}, 'http://localhost/build/');
  await s.w.LeakScopeBuild.render(s.root, '');
  const row = s.root.querySelector('.build-row');
  assert.equal(row.getAttribute('href'), '/build/w1/');
  assert.match(row.textContent, /Jenkins sweep/); assert.match(row.textContent, /3 steps/); assert.match(row.textContent, /Contacts target/); assert.match(row.textContent, /Finished · stopped early/);
  assert.equal(row.querySelectorAll('.build-mark').length, 3);
  const empty = setup({}, 'http://localhost/build/');
  await empty.w.LeakScopeBuild.render(empty.root, '');
  assert.match(empty.root.textContent, /No workflows yet/);
});

test('the editor shows one card per step with its reach and settings form', async () => {
  const s = setup();
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.deepEqual(names(s), ['Saved results that match', 'Capture', 'Tag']);
  assert.deepEqual([...s.root.querySelectorAll('.build-step .build-reach')].map(n => n.textContent), ['Local', 'Contacts target', 'Local']);
  assert.match(s.root.querySelector('.build-summary').textContent, /Technology: jenkins · At most: 50/);
  assert.equal(s.root.querySelector('.build-name').value, 'Jenkins sweep');
  click(s, 'Edit', s.root.querySelector('[data-step=s1]'));
  const body = s.root.querySelector('[data-step=s1] .build-step-body');
  assert.equal(body.hidden, false);
  assert.equal(body.querySelector('[name=technology]').value, 'jenkins');
  const limit = body.querySelector('[name=limit]');
  assert.equal(limit.type, 'number'); assert.equal(limit.getAttribute('max'), '5000');
  limit.value = '7'; limit.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  assert.match(s.root.querySelector('.build-summary').textContent, /At most: 7/);
  assert.match(s.root.querySelector('[role=status]').textContent, /Unsaved changes/);
});

const off = s => [...s.root.querySelectorAll('.build-library .build-choice.is-off')].map(n => n.dataset.block);
const pick = (s, key) => s.root.querySelector('.build-library [data-block=' + key + ']').click();

test('a new workflow shows every block, and says how to begin', async () => {
  const s = setup({'GET /api/build/workflows/w1': detail({name: 'New workflow', definition: {steps: []}, problems: 'A workflow needs at least one step', estimate: null})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.equal(s.root.querySelectorAll('.build-library .build-choice').length, BLOCKS.length);
  assert.match(s.root.querySelector('.build-library h3').textContent, /Blocks\s*6/);
  assert.deepEqual(off(s), ['search', 'filter', 'capture', 'tag']);
  assert.match(s.root.querySelector('[data-block=tag] .build-why').textContent, /Pick how the workflow starts first/);
  assert.match(s.root.querySelector('.build-empty').textContent, /Pick how this workflow starts/);
  assert.equal(s.root.querySelector('.build-problems').hidden, true);
  pick(s, 'tag');
  assert.deepEqual(names(s), [], 'a greyed-out block does nothing');
  pick(s, 'saved_results');
  assert.deepEqual(names(s), ['Saved results that match']);
  assert.equal(s.root.querySelector('.build-empty'), null);
  assert.deepEqual(off(s), []);
});

test('the library is searchable by name, description, group and reach', async () => {
  const s = setup();
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  const find = s.root.querySelector('[aria-label="Search blocks"]');
  const shown = text => { find.value = text; find.dispatchEvent(new s.w.Event('input', {bubbles: true})); return [...s.root.querySelectorAll('.build-library .build-choice')].map(n => n.dataset.block); };
  assert.deepEqual(shown('tag'), ['tag']);
  assert.deepEqual(shown('contacts target'), ['capture']);
  assert.deepEqual(shown('starts'), ['run_by_hand', 'saved_results']);
  assert.deepEqual(shown('provider query'), ['search']);
  assert.deepEqual(shown('zzz'), []);
  assert.match(s.root.querySelector('.build-shelf').textContent, /No block matches/);
  assert.equal(shown('').length, BLOCKS.length);
});

test('blocks that cannot go next say why, a start block replaces step 1, and Save posts the edited order', async () => {
  const s = setup({'GET /api/build/workflows/w1': detail({definition: {steps: [{id: 's1', block: 'run_by_hand', settings: {}}]}}),
    'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.deepEqual(off(s), ['filter', 'capture', 'tag']);
  assert.match(s.root.querySelector('[data-block=capture] .build-why').textContent, /Needs results to work on/);
  assert.match(s.root.querySelector('[data-block=saved_results] .build-why').textContent, /Replaces step 1/);
  pick(s, 'search');
  assert.deepEqual(off(s), []);
  pick(s, 'tag'); pick(s, 'capture');
  assert.deepEqual(names(s), ['Run by hand', 'Search a provider', 'Tag', 'Capture']);
  s.root.querySelector('[data-step=s4] [aria-label="Move up"]').click();
  assert.deepEqual(names(s), ['Run by hand', 'Search a provider', 'Capture', 'Tag']);
  assert.equal(s.root.querySelector('[data-step=s2] [aria-label="Move up"]'), null, 'nothing moves above the start');
  click(s, 'Remove', s.root.querySelector('[data-step=s3]'));
  pick(s, 'saved_results');
  assert.deepEqual(names(s), ['Saved results that match', 'Search a provider', 'Capture']);
  click(s, 'Save'); await wait();
  const saved = posts(s, '/save')[0].body;
  assert.equal(saved.name, 'Jenkins sweep');
  assert.deepEqual(saved.definition.steps.map(step => [step.id, step.block]), [['s1', 'saved_results'], ['s2', 'search'], ['s4', 'capture']]);
  assert.deepEqual(saved.definition.limits, {pages: 5, contacts: 25, items: 500});
  assert.match(s.root.querySelector('[role=status]').textContent, /^Saved$/);
});

test('conditions are picked from lists and saved as rules', async () => {
  const s = setup({'GET /api/build/workflows/w1': detail({definition: {steps: [STEPS[0], {id: 's2', block: 'filter', settings: {}}]}}), 'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Edit', s.root.querySelector('[data-step=s2]'));
  const rule = s.root.querySelector('.build-rule');
  assert.deepEqual([...rule.querySelector('[aria-label=Field]').options].map(o => o.value), ['address', 'port', 'technology', 'tags']);
  const value = rule.querySelector('[aria-label=Value]');
  value.value = 'jenkins'; value.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  click(s, 'Add condition');
  assert.equal(s.root.querySelectorAll('.build-rule').length, 2);
  click(s, 'Save'); await wait();
  assert.deepEqual(posts(s, '/save')[0].body.definition.steps[1].settings.conditions,
    {match: 'all', rules: [{field: 'technology', op: 'is', value: 'jenkins'}, {field: 'technology', op: 'is', value: ''}]});
});

test('Run shows what it can spend, and starts only after Run is confirmed', async () => {
  let estimates = 0;
  const s = setup({'POST /api/build/workflows/w1/estimate': () => { estimates += 1; return ESTIMATE; },
    'POST /api/build/workflows/w1/run': call => call.body.token === 'tok-2' ? {run: {id: 'r1'}} : {__status: 409, error: 'Confirm', estimate: {...ESTIMATE, contacts: 9, token: 'tok-2'}},
    'GET /api/build/runs/r1': runView('succeeded', {results_url: '/assets/?run=r1'})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  const dialog = s.root.querySelector('.build-confirm');
  assert.equal(dialog.hidden, true);
  click(s, 'Run'); await wait();
  assert.equal(dialog.hidden, false);
  assert.match(dialog.textContent, /Contacts target\s*up to 25 target contacts/);
  assert.match(dialog.textContent, /up to 50 at any step/);
  assert.doesNotMatch(dialog.textContent, /provider page/);
  assert.equal(posts(s, '/run').length, 0, 'nothing runs before confirmation');
  click(s, 'Run', dialog); await wait();
  assert.equal(dialog.hidden, false, 'a changed workflow asks again');
  assert.match(dialog.textContent, /up to 9 target contacts/);
  assert.match(s.root.querySelector('[role=status]').textContent, /changed/);
  click(s, 'Run', dialog); await wait();
  assert.deepEqual(posts(s, '/run').map(call => call.body.token), ['tok-1', 'tok-2']);
  assert.equal(dialog.hidden, true);
  assert.match(s.root.querySelector('.build-run').textContent, /Run · Finished/);
  assert.equal(s.root.querySelector('.build-run a.btn-primary').getAttribute('href'), '/assets/?run=r1');
  click(s, 'Run'); await wait(); click(s, 'Cancel', dialog);
  assert.equal(dialog.hidden, true); assert.equal(posts(s, '/run').length, 2);
});

test('an invalid workflow shows its problems and does not ask to run', async () => {
  const s = setup({'POST /api/build/workflows/w1/estimate': {__status: 400, error: 'Step 2: Tags is required\nStep 3: unknown block "x"'}});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Run'); await wait();
  const box = s.root.querySelector('.build-problems');
  assert.equal(box.hidden, false);
  assert.deepEqual([...box.querySelectorAll('p')].map(p => p.textContent), ['Step 2: Tags is required', 'Step 3: unknown block "x"']);
  assert.equal(s.root.querySelector('.build-confirm').hidden, true);
});

test('the run view shows counts, where a partial run stopped, and Stop', async () => {
  let state = 'running';
  const s = setup({'GET /api/build/workflows/w1': detail({runs: [{id: 'r1', state: 'running', partial: false, created_at: '2026-11-14T10:00:00Z'}]}),
    'GET /api/build/runs/r1': () => runView(state, state === 'running' ? {} : {partial: true, stopped_at: {step: 's2', name: 'Capture', reason: '1 left out at a ceiling'}}),
    'POST /api/runs/r1/cancel': () => { state = 'cancelled'; return {}; }});
  await s.w.LeakScopeBuild.render(s.root, 'w1'); await wait();
  const view = s.root.querySelector('.build-run');
  assert.match(view.textContent, /Run · Running/);
  assert.match(view.textContent, /3 in → 2 out · 1 passed by · 2 contacts/);
  assert.match(view.textContent, /Waiting/);
  assert.match(view.textContent, /2 target contacts of 25/);
  assert.equal(view.querySelector('a[href^="/assets/"]'), null);
  click(s, 'Stop', view); await wait();
  assert.equal(posts(s, '/cancel').length, 1);
  assert.match(view.textContent, /Run · Stopped/);
  assert.match(view.querySelector('.build-partial').textContent, /Stopped early at Capture: 1 left out at a ceiling\./);
  assert.equal([...view.querySelectorAll('button')].some(b => b.textContent === 'Stop'), false);
});

test('polling stops when the run ends and when the page is left', async () => {
  let reads = 0, state = 'running';
  const s = setup({'GET /api/build/workflows/w1': detail({runs: [{id: 'r1', state: 'running', partial: false, created_at: '2026-11-14T10:00:00Z'}]}),
    'GET /api/build/runs/r1': () => { reads += 1; return runView(state); }});
  const timers = [];
  s.w.setTimeout = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
  s.w.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].fn = null; };
  s.w.eval(source);
  await s.w.LeakScopeBuild.render(s.root, 'w1'); await wait();
  assert.equal(reads, 1); assert.equal(timers.filter(t => t.fn).length, 1); assert.equal(timers[0].ms, 3000);
  state = 'succeeded';
  await timers[0].fn(); await wait();
  assert.equal(reads, 2); assert.equal(timers.length, 1, 'a finished run schedules nothing');
  state = 'running';
  await s.w.LeakScopeBuild.render(s.root, 'w1'); await wait();
  const pending = timers.filter(t => t.fn).length;
  assert.equal(pending, 1);
  s.w.dispatchEvent(new s.w.Event('pagehide'));
  assert.equal(timers.filter(t => t.fn).length, 0);
});

test('a step whose block no longer exists is shown as unavailable and can be removed', async () => {
  const s = setup({'GET /api/build/workflows/w1': detail({definition: {steps: [STEPS[0], {id: 's2', block: 'retired', settings: {}}]}, problems: 'Step 2: unknown block "retired"'})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.deepEqual(names(s), ['Saved results that match', 'Unavailable block']);
  assert.match(s.root.querySelector('.is-unavailable').textContent, /retired/);
  assert.equal(s.root.querySelector('.build-problems').hidden, false);
  click(s, 'Remove', s.root.querySelector('.is-unavailable'));
  assert.deepEqual(names(s), ['Saved results that match']);
});

test('names and notes from the server are placed as text', async () => {
  const evil = '<img src=x onerror="window.pwned=1">';
  const s = setup({'GET /api/build/workflows/w1': detail({name: evil, problems: evil, runs: [{id: 'r1', state: 'failed', partial: false, created_at: '2026-11-14T10:00:00Z'}]}),
    'GET /api/build/runs/r1': runView('failed', {run: {id: 'r1', state: 'failed', can_cancel: false, error: evil}, steps: [{id: 's1', name: evil, state: 'failed', items_in: 0, items_out: 0, pages: 0, contacts: 0, note: evil}]})});
  await s.w.LeakScopeBuild.render(s.root, 'w1'); await wait();
  assert.equal(s.root.querySelector('img'), null);
  assert.equal(s.w.pwned, undefined);
  assert.equal(s.root.querySelector('.build-name').value, evil);
  assert.match(s.root.querySelector('.build-run').textContent, /onerror/);
});

test('a block key that names a built-in property is unavailable, not a crash', async () => {
  const s = setup({'GET /api/build/workflows/w1': detail({definition: {steps: [STEPS[0], {id: 's2', block: 'constructor', settings: {}}, {id: 's3', block: '__proto__', settings: {}}]}})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.deepEqual(names(s), ['Saved results that match', 'Unavailable block', 'Unavailable block']);
});

test('the confirming Run button cannot be pressed twice', async () => {
  let release;
  const s = setup({'POST /api/build/workflows/w1/estimate': ESTIMATE, 'POST /api/build/workflows/w1/run': () => new Promise(resolve => { release = () => resolve({run: {id: 'r1'}}); }),
    'GET /api/build/runs/r1': runView('succeeded')});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Run'); await wait();
  const go = click(s, 'Run', s.root.querySelector('.build-confirm'));
  assert.equal(go.disabled, true);
  go.click(); await wait();
  assert.equal(posts(s, '/run').length, 1);
  release(); await wait();
});

test('choices drawn from saved data, suggestions and optional conditions', async () => {
  const rich = {key: 'rich', says: 'Rich start.', name: 'Rich start', group: 'trigger', reach: 'local', start: true, takes: null, gives: 'endpoint', output_fields: [], fields: [
    field('technology', 'Technology', 'text', {suggest: 'technologies'}), field('organization', 'Organization', 'choice', {options: 'organizations'}),
    field('conditions', 'Only when', 'conditions')]};
  const s = setup({'GET /api/build/blocks': {...CATALOG, blocks: [rich, ...BLOCKS], options: {organizations: [['o1', 'Acme'], ['o2', 'Globex']], technologies: ['jenkins', 'grafana'], tags: []}},
    'GET /api/build/workflows/w1': detail({definition: {steps: [{id: 's1', block: 'rich', settings: {organization: 'gone'}}]}}), 'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Edit', s.root.querySelector('[data-step=s1]'));
  const body = s.root.querySelector('[data-step=s1] .build-step-body');
  const pickOrg = body.querySelector('[name=organization]');
  assert.deepEqual([...pickOrg.options].map(o => [o.value, o.textContent]), [['', 'Any'], ['o1', 'Acme'], ['o2', 'Globex'], ['gone', 'No longer available']]);
  pickOrg.value = 'o2'; pickOrg.dispatchEvent(new s.w.Event('change', {bubbles: true}));
  assert.match(s.root.querySelector('.build-summary').textContent, /Organization: Globex/);
  const list = s.d.getElementById(body.querySelector('[name=technology]').getAttribute('list'));
  assert.deepEqual([...list.querySelectorAll('option')].map(o => o.value), ['jenkins', 'grafana']);
  assert.equal(body.querySelectorAll('.build-rule').length, 0, 'an optional condition starts empty');
  click(s, 'Save'); await wait();
  assert.equal(posts(s, '/save')[0].body.definition.steps[0].settings.conditions, undefined);
  click(s, 'Add condition');
  const value = s.root.querySelector('.build-rule [aria-label=Value]'); value.value = '10.'; value.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  click(s, 'Save'); await wait();
  assert.deepEqual(posts(s, '/save')[1].body.definition.steps[0].settings.conditions, {match: 'all', rules: [{field: 'technology', op: 'is', value: '10.'}]});
  click(s, 'Remove', s.root.querySelector('.build-rule'));
  click(s, 'Save'); await wait();
  assert.equal(posts(s, '/save')[2].body.definition.steps[0].settings.conditions, null);
});

test('values: a named value can be inserted into a later step, and a step can be limited to some results', async () => {
  const take = {key: 'take_value', says: 'Names a field.', name: 'Take a value', group: 'data', reach: 'local', start: false, takes: 'endpoint', gives: 'endpoint', names_setting: 'name', output_fields: [],
    fields: [field('name', 'Name', 'text', {required: true}), field('source', 'Take from', 'text', {required: true, suggest: 'fields'})]};
  const blocks = [take, ...BLOCKS.map(block => block.key === 'tag' ? {...block, fields: [field('tags', 'Tags', 'tags', {required: true, template: true})]} : block)];
  const s = setup({'GET /api/build/blocks': {...CATALOG, blocks}, 'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition}),
    'GET /api/build/workflows/w1': detail({definition: {steps: [STEPS[0], {id: 's2', block: 'take_value', settings: {name: 'host', source: 'address'}}, {id: 's3', block: 'tag', settings: {tags: ['seen']}}]}})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Edit', s.root.querySelector('[data-step=s2]'));
  assert.deepEqual([...s.root.querySelector('[data-step=s2] [name=source]').options].map(o => o.value), ['', 'address', 'port', 'technology', 'tags']);
  click(s, 'Edit', s.root.querySelector('[data-step=s3]'));
  const body = s.root.querySelector('[data-step=s3] .build-step-body');
  const pick = body.querySelector('[aria-label="Insert a value"]');
  assert.deepEqual([...pick.options].map(o => o.textContent), ['Insert a value…', '{{address}}', '{{port}}', '{{technology}}', '{{tags}}', '{{host}}']);
  const tags = body.querySelector('[name=tags]');
  tags.value = 'seen, h-'; tags.setSelectionRange(8, 8);
  pick.value = 'host'; pick.dispatchEvent(new s.w.Event('change', {bubbles: true}));
  assert.equal(tags.value, 'seen, h-{{host}}');
  const when = body.querySelector('.build-when');
  click(s, 'Add condition', when);
  const rule = when.querySelector('.build-rule');
  assert.ok([...rule.querySelector('[aria-label=Field]').options].some(o => o.value === 'host'), 'a named value can be used in a condition');
  const value = rule.querySelector('[aria-label=Value]'); value.value = '443';
  const name = rule.querySelector('[aria-label=Field]'); name.value = 'port'; name.dispatchEvent(new s.w.Event('change', {bubbles: true}));
  value.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  assert.match(s.root.querySelector('[data-step=s3] .build-summary').textContent, /only when port is 443/);
  click(s, 'Save'); await wait();
  const saved = posts(s, '/save')[0].body.definition.steps;
  assert.deepEqual(saved[2], {id: 's3', block: 'tag', settings: {tags: ['seen', 'h-{{host}}']}, when: {match: 'all', rules: [{field: 'port', op: 'is', value: '443'}]}});
  assert.equal('when' in saved[1], false);
});

test('domain blocks fit after domains, result blocks after results, and each says what it needs', async () => {
  const domainStart = {key: 'saved_domains', says: 'Starts with domains.', name: 'Saved domains', group: 'trigger', reach: 'local', start: true, takes: null, gives: 'domain', output_fields: [], fields: []};
  const watch = {key: 'create_watch', says: 'Watches domains.', name: 'Watch', group: 'organize', reach: 'local', start: false, takes: ['domain'], gives: 'same', output_fields: [], fields: []};
  const both = {key: 'limit', says: 'First few.', name: 'Limit', group: 'logic', reach: 'local', start: false, takes: ['endpoint', 'domain'], gives: 'same', output_fields: [], fields: []};
  const back = {key: 'results_for_domains', says: 'Domains to results.', name: 'Results for domains', group: 'data', reach: 'local', start: false, takes: ['domain'], gives: 'endpoint', output_fields: [], fields: []};
  const s = setup({'GET /api/build/blocks': {...CATALOG, blocks: [domainStart, watch, both, back, ...BLOCKS], fields: {endpoint: ['address', 'port'], domain: ['domain', 'root'], value: []}},
    'GET /api/build/workflows/w1': detail({definition: {steps: [{id: 's1', block: 'saved_domains', settings: {}}]}})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.deepEqual(off(s).sort(), ['capture', 'filter', 'tag']);
  assert.match(s.root.querySelector('[data-block=tag] .build-why').textContent, /this workflow holds domains here/);
  pick(s, 'limit'); pick(s, 'create_watch');
  click(s, 'Edit', s.root.querySelector('[data-step=s2]'));
  click(s, 'Add condition', s.root.querySelector('[data-step=s2] .build-when'));
  assert.deepEqual([...s.root.querySelector('[data-step=s2] .build-when [aria-label=Field]').options].map(o => o.value), ['domain', 'root']);
  pick(s, 'results_for_domains');
  assert.deepEqual(off(s).sort(), ['create_watch', 'results_for_domains']);
  assert.match(s.root.querySelector('[data-block=create_watch] .build-why').textContent, /Needs domains/);
  pick(s, 'tag');
  assert.deepEqual(names(s), ['Saved domains', 'Limit', 'Watch', 'Results for domains', 'Tag']);
});

test('a checkbox setting is saved as true or false, and values a block always adds can be picked later', async () => {
  const hook = {key: 'web_request', says: 'Sends a request.', name: 'Web request', group: 'send', reach: 'send', start: false, takes: ['endpoint'], gives: 'same', adds_fixed: ['response_status'], output_fields: [],
    fields: [field('url', 'Address', 'text', {required: true, template: true}), field('allow_internal', 'Allow internal addresses', 'flag', {default: false})]};
  const blocks = [hook, ...BLOCKS.map(block => block.key === 'tag' ? {...block, fields: [field('tags', 'Tags', 'tags', {required: true, template: true})]} : block)];
  const s = setup({'GET /api/build/blocks': {...CATALOG, blocks}, 'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition}),
    'GET /api/build/workflows/w1': detail({definition: {steps: [STEPS[0], {id: 's2', block: 'web_request', settings: {url: 'https://hooks.example.com/'}}, {id: 's3', block: 'tag', settings: {tags: ['x']}}]}})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.match(s.root.querySelector('[data-step=s2] .build-reach').textContent, /Sends out/);
  click(s, 'Edit', s.root.querySelector('[data-step=s2]'));
  const box = s.root.querySelector('[data-step=s2] [name=allow_internal]');
  assert.equal(box.type, 'checkbox'); assert.equal(box.checked, false);
  box.checked = true; box.dispatchEvent(new s.w.Event('change', {bubbles: true}));
  assert.match(s.root.querySelector('[data-step=s2] .build-summary').textContent, /Allow internal addresses/);
  click(s, 'Edit', s.root.querySelector('[data-step=s3]'));
  assert.ok([...s.root.querySelector('[data-step=s3] [aria-label="Insert a value"]').options].some(o => o.value === 'response_status'));
  click(s, 'Save'); await wait();
  assert.equal(posts(s, '/save')[0].body.definition.steps[1].settings.allow_internal, true);
});

test('a workflow is switched to run on a schedule with one confirmation, and can be switched off', async () => {
  const on = {enabled: true, event: false, schedule_minutes: 30, next_run_at: '2026-11-14T12:00:00Z', note: ''};
  const s = setup({'GET /api/build/workflows/w1': detail({automatic: {enabled: false, event: false, schedule_minutes: null, next_run_at: null, note: 'Turned off: it was changed after it was confirmed.'}}),
    'POST /api/build/workflows/w1/estimate': {...ESTIMATE, limits: {...ESTIMATE.limits, runs_per_day: 24}},
    'POST /api/build/workflows/w1/enable': call => call.body.token === 'tok-1' ? detail({automatic: on}) : {__status: 409, error: 'Confirm'},
    'POST /api/build/workflows/w1/disable': detail({automatic: {enabled: false, event: false, schedule_minutes: null, next_run_at: null, note: ''}})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  const auto = s.root.querySelector('.build-auto');
  assert.match(auto.textContent, /Runs only when you press Run/); assert.match(auto.textContent, /Turned off: it was changed/);
  click(s, 'Run on a schedule…', auto); await wait();
  const dialog = s.root.querySelector('.build-confirm');
  assert.equal(dialog.hidden, false);
  assert.match(dialog.textContent, /each run: up to 25 target contacts/); assert.match(dialog.textContent, /at most 24 runs a day/);
  assert.equal(posts(s, '/enable').length, 0);
  dialog.querySelector('[name=schedule_minutes]').value = '30';
  click(s, 'Turn on', dialog); await wait();
  assert.deepEqual(posts(s, '/enable')[0].body, {token: 'tok-1', schedule_minutes: 30});
  assert.equal(dialog.hidden, true);
  assert.match(auto.textContent, /Runs automatically/); assert.match(auto.textContent, /every 30 minutes · next/);
  click(s, 'Turn off', auto); await wait();
  assert.equal(posts(s, '/disable').length, 1);
  assert.match(auto.textContent, /Runs only when you press Run/);
});

test('a workflow that starts on new records is turned on without a schedule, and the list marks automatic workflows', async () => {
  const event = {key: 'new_observations', says: 'Starts by itself.', name: 'When new results are saved', group: 'trigger', reach: 'local', start: true, event: true, takes: null, gives: 'endpoint', output_fields: [], fields: []};
  const s = setup({'GET /api/build/blocks': {...CATALOG, blocks: [event, ...BLOCKS]}, 'POST /api/build/workflows/w1/estimate': {...ESTIMATE, contacts: 0, limits: {...ESTIMATE.limits, runs_per_day: 24}},
    'GET /api/build/workflows/w1': detail({definition: {steps: [{id: 's1', block: 'new_observations', settings: {}}, STEPS[2]]}, automatic: {enabled: false, event: true}}),
    'POST /api/build/workflows/w1/enable': detail({automatic: {enabled: true, event: true, schedule_minutes: null, next_run_at: null, note: ''}})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Run automatically…', s.root.querySelector('.build-auto')); await wait();
  const dialog = s.root.querySelector('.build-confirm');
  assert.match(dialog.textContent, /whenever new records appear/); assert.equal(dialog.querySelector('[name=schedule_minutes]'), null);
  assert.match(dialog.textContent, /reads and changes saved data only/);
  click(s, 'Turn on', dialog); await wait();
  assert.deepEqual(posts(s, '/enable')[0].body, {token: 'tok-1'});
  assert.match(s.root.querySelector('.build-auto').textContent, /starts by itself when new records appear/);
  const list = setup({'GET /api/build/workflows': {results: [{id: 'w1', name: 'Auto', steps: 2, reaches: ['local'], groups: ['trigger', 'organize'], last_run: null, automatic: {enabled: true}}]}}, 'http://localhost/build/');
  await list.w.LeakScopeBuild.render(list.root, '');
  assert.match(list.root.querySelector('.build-auto-chip').textContent, /Automatic/);
});

test('the page says so when the workflow cannot be loaded', async () => {
  const s = setup({'GET /api/build/workflows/w1': {__status: 404, error: 'Record not found'}});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.match(s.root.textContent, /Record not found/);
  assert.equal(s.root.querySelector('a').getAttribute('href'), '/build/');
});

// ---------------------------------------------------------------- the map and branches
const IF = {key: 'if', says: 'Splits results in two.', name: 'If', group: 'logic', reach: 'local', start: false, takes: ['endpoint', 'domain'], gives: 'same', ports: ['yes', 'no'], output_fields: [],
  fields: [field('conditions', 'If', 'conditions', {required: true})]};
const NAMES = {key: 'names_of_results', says: 'Names.', name: 'Names of each result', group: 'data', reach: 'local', start: false, takes: 'endpoint', gives: 'domain', output_fields: [], fields: []};
const mapSetup = steps => setup({'GET /api/build/blocks': {...CATALOG, blocks: [...BLOCKS, IF, NAMES], fields: {endpoint: CATALOG.conditions.fields, domain: ['domain']}},
  'GET /api/build/workflows/w1': detail({definition: {steps}}), 'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition})});
const nodes = s => [...s.root.querySelectorAll('.build-node')].map(node => node.getAttribute('data-node'));
const lines = s => [...s.root.querySelectorAll('.build-line-hit')].map(node => node.getAttribute('data-line'));
const port = (s, ref) => s.root.querySelector('[data-port="' + ref + '"]');
const savedSteps = async s => { click(s, 'Save'); await wait(); return posts(s, '/save').pop().body.definition.steps; };

test('the map draws one box per step and a line per connection, and a box opens its step', async () => {
  const s = mapSetup(STEPS);
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.deepEqual(nodes(s), ['s1', 's2', 's3']);
  assert.deepEqual(lines(s), ['s1>s2', 's2>s3']);
  assert.match(s.root.querySelector('[data-node=s1]').textContent, /Saved results that match.*Technology: jenkins/);
  s.root.querySelector('[data-node=s2] .build-node-main').click();
  assert.equal(s.root.querySelector('[data-step=s2] .build-step-body').hidden, false);
  assert.equal(s.root.querySelector('[data-node=s2] .build-node-main').getAttribute('aria-pressed'), 'true');
  assert.match(s.root.querySelector('[data-step=s2] .build-after').textContent, /Comes after.*Saved results that match/);
  const empty = setup({'GET /api/build/workflows/w1': detail({definition: {steps: []}})});
  await empty.w.LeakScopeBuild.render(empty.root, 'w1');
  assert.equal(empty.root.querySelector('.build-canvas').hidden, true);
});

test('an If has a Yes and a No dot; blocks are added to the chosen side and saved with their connections', async () => {
  const s = mapSetup([STEPS[0]]);
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  pick(s, 'if');
  assert.deepEqual([...s.root.querySelectorAll('[data-node=s2] .build-port')].map(node => node.textContent), ['Yes', 'No']);
  pick(s, 'tag');
  assert.deepEqual(lines(s), ['s1>s2', 's2:yes>s3'], 'the first block after an If goes to its free Yes side');
  port(s, 's2:no').click();
  assert.match(s.root.querySelector('.build-map-bar').textContent, /Now press the step that comes after If · No/);
  pick(s, 'capture');
  assert.deepEqual(lines(s), ['s1>s2', 's2:yes>s3', 's2:no>s4']);
  assert.equal(s.root.querySelector('[aria-label="Move up"]'), null, 'a branched workflow is rearranged on the map');
  // Join: the dot under Tag, then the Capture box... then both feed a last step.
  pick(s, 'tag');
  port(s, 's3').click(); s.root.querySelector('[data-node=s5] .build-node-main').click();
  const steps = await savedSteps(s);
  assert.deepEqual(steps.map(step => [step.id, step.block, step.after]), [['s1', 'saved_results', undefined], ['s2', 'if', ['s1']], ['s3', 'tag', ['s2:yes']], ['s4', 'capture', ['s2:no']], ['s5', 'tag', ['s4', 's3']]]);
  assert.match(s.root.querySelector('[data-step=s5] .build-after').textContent, /Capture.*Tag.*arrive together/);
});

test('connections that make a loop or the wrong kind are refused with the reason', async () => {
  const s = mapSetup([STEPS[0], {id: 's2', block: 'names_of_results', settings: {}}, {id: 's3', block: 'tag', settings: {tags: ['x']}, after: ['s1']}]);
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  port(s, 's2').click(); s.root.querySelector('[data-node=s3] .build-node-main').click();
  assert.match(s.root.querySelector('[role=status]').textContent, /Tag needs endpoint items, but Names of each result gives domain/);
  port(s, 's3').click(); s.root.querySelector('[data-node=s1] .build-node-main').click();
  assert.match(s.root.querySelector('[role=status]').textContent, /start block cannot come after/);
  assert.deepEqual(lines(s), ['s1>s2', 's1>s3']);
  const loop = mapSetup([STEPS[0], STEPS[1], STEPS[2]]);
  await loop.w.LeakScopeBuild.render(loop.root, 'w1');
  port(loop, 's3').click(); loop.root.querySelector('[data-node=s2] .build-node-main').click();
  assert.match(loop.root.querySelector('[role=status]').textContent, /would make a loop/);
});

test('a line can be removed, a removed step hands its followers to what fed it, and a plain chain is saved without connections', async () => {
  const s = mapSetup([STEPS[0], {id: 's2', block: 'if', settings: {}}, {id: 's3', block: 'tag', settings: {tags: ['a']}, after: ['s2:yes']}, {id: 's4', block: 'tag', settings: {tags: ['b']}, after: ['s2:no', 's3']}]);
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  s.root.querySelector('[data-line="s2:no>s4"]').dispatchEvent(new s.w.Event('click', {bubbles: true}));
  click(s, 'Remove connection');
  assert.deepEqual(lines(s), ['s1>s2', 's2:yes>s3', 's3>s4']);
  click(s, 'Remove', s.root.querySelector('[data-step=s2]'));
  assert.deepEqual(lines(s), ['s1>s3', 's3>s4']);
  assert.deepEqual((await savedSteps(s)).map(step => [step.id, step.after]), [['s1', undefined], ['s3', undefined], ['s4', undefined]]);
  assert.ok(s.root.querySelector('[aria-label="Move up"]'), 'a plain chain can be reordered with the arrows again');
});

test('the same connections can be made from the step card, and an unconnected step says so', async () => {
  const s = mapSetup([STEPS[0], STEPS[1], {id: 's3', block: 'tag', settings: {tags: ['x']}, after: []}]);
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  assert.match(s.root.querySelector('[data-node=s3]').textContent, /Not connected/);
  click(s, 'Edit', s.root.querySelector('[data-step=s3]'));
  const choose = s.root.querySelector('[data-step=s3] [name=after]');
  assert.deepEqual([...choose.options].map(option => option.textContent), ['Choose a step…', 'Saved results that match', 'Capture']);
  choose.value = 's1'; choose.dispatchEvent(new s.w.Event('change'));
  assert.deepEqual(lines(s), ['s1>s2', 's1>s3']);
  s.root.querySelector('[data-step=s3] [aria-label="Disconnect from Saved results that match"]').click();
  assert.match(s.root.querySelector('[data-node=s3]').textContent, /Not connected/);
});

test('the last run is drawn on the map until the workflow is edited', async () => {
  const view = runView('succeeded');
  const s = setup({'GET /api/build/workflows/w1': detail({runs: [{id: 'r1', state: 'succeeded', created_at: '2026-11-14T10:00:00Z'}]}), 'GET /api/build/runs/r1': view});
  await s.w.LeakScopeBuild.render(s.root, 'w1'); await wait();
  assert.match(s.root.querySelector('[data-node=s2]').textContent, /Done · 3 → 2/);
  assert.ok(s.root.querySelector('[data-node=s1]').classList.contains('is-done'));
  assert.equal(s.root.querySelectorAll('.build-line.is-live').length, 2);
  assert.match(s.root.querySelector('.build-map-bar').textContent, /Showing the last run/);
  click(s, 'Edit', s.root.querySelector('[data-step=s1]'));
  const input = s.root.querySelector('[data-step=s1] [name=technology]'); input.value = 'grafana'; input.dispatchEvent(new s.w.Event('input'));
  pick(s, 'tag');
  assert.match(s.root.querySelector('[data-node=s1]').textContent, /Technology: grafana/);
  assert.equal(s.root.querySelectorAll('.build-line.is-live').length, 0);
});

test('targets are chosen by typing them or ticking saved ones, and saved as a list', async () => {
  const CHOSEN = {key: 'chosen_results', says: 'Starts with chosen results.', name: 'Chosen saved results', group: 'trigger', reach: 'local', start: true, takes: null, gives: 'endpoint', output_fields: [],
    fields: [field('addresses', 'Addresses', 'list', {required: true, suggest: 'address'})]};
  const s = setup({'GET /api/build/blocks': {...CATALOG, blocks: [...BLOCKS, CHOSEN]}, 'GET /api/build/workflows/w1': detail({definition: {steps: [{id: 's1', block: 'chosen_results', settings: {addresses: ['192.0.2.1']}}]}}),
    'GET /api/build/pick?kind=address&q=exam': {results: [{value: 'app.example.com', note: '2 saved results'}, {value: 'shop.example.com', note: '1 saved result'}]},
    'GET /api/build/pick?kind=address&q=zz': {results: []},
    'POST /api/build/workflows/w1/save': call => detail({definition: call.body.definition})});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  click(s, 'Edit', s.root.querySelector('[data-step=s1]'));
  const body = s.root.querySelector('[data-step=s1] .build-step-body'), area = body.querySelector('textarea[name=addresses]');
  assert.equal(area.value, '192.0.2.1');
  assert.match(body.textContent, /1 entry chosen/);
  area.value = '192.0.2.1\n 198.51.100.7 ,192.0.2.1'; area.dispatchEvent(new s.w.Event('input'));
  assert.match(body.textContent, /2 entries chosen/);
  const find = body.querySelector('[name=addresses-find]');
  find.value = 'exam'; find.dispatchEvent(new s.w.Event('input')); await wait();
  const rows = [...body.querySelectorAll('.build-pick-row')];
  assert.deepEqual(rows.map(row => row.textContent), ['app.example.com2 saved results', 'shop.example.com1 saved result']);
  rows[0].querySelector('input').click();
  assert.match(area.value, /app\.example\.com$/);
  click(s, 'Add all 2', body);
  rows[0].querySelector('input').click();
  click(s, 'Save'); await wait();
  assert.deepEqual(posts(s, '/save')[0].body.definition.steps[0].settings.addresses, ['192.0.2.1', '198.51.100.7', 'shop.example.com']);
  find.value = 'zz'; find.dispatchEvent(new s.w.Event('input')); await wait();
  assert.match(body.textContent, /Nothing saved matches “zz”/);
});

test('the library can be narrowed to one group, and Filter and Sort sit under a group named for them', async () => {
  const s = setup();
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  const chips = () => [...s.root.querySelectorAll('.build-kinds button')], listed = () => [...s.root.querySelectorAll('.build-library .build-choice')].map(n => n.dataset.block);
  const chip = chips().find(n => n.textContent.includes('Filter, sort and flow'));
  assert.ok(chip, 'a chip for the group');
  assert.equal(chips().length, new Set(BLOCKS.map(b => b.group)).size);
  chip.click();
  assert.deepEqual(listed(), ['filter']);
  assert.equal(chips().find(n => n.textContent.includes('Filter, sort and flow')).getAttribute('aria-pressed'), 'true');
  const find = s.root.querySelector('[aria-label="Search blocks"]');
  find.value = 'tag'; find.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  assert.deepEqual(listed(), []);
  assert.match(s.root.querySelector('.build-shelf').textContent, /No block in Filter, sort and flow matches “tag”/);
  find.value = ''; find.dispatchEvent(new s.w.Event('input', {bubbles: true}));
  chips().find(n => n.textContent.includes('Filter, sort and flow')).click();
  assert.equal(listed().length, BLOCKS.length);
});

test('the confirmation names public lookups and sending, and says local only when every step is local', async () => {
  const reaching = {...ESTIMATE, contacts: 0, reaches: ['public', 'send', 'local']};
  const s = setup({'POST /api/build/workflows/w1/estimate': () => reaching});
  await s.w.LeakScopeBuild.render(s.root, 'w1');
  const dialog = s.root.querySelector('.build-confirm');
  click(s, 'Run'); await wait();
  assert.match(dialog.textContent, /Public lookup\s*asks public sources such as DNS and registries/);
  assert.match(dialog.textContent, /Sends out\s*sends data to the addresses set in its steps/);
  assert.doesNotMatch(dialog.textContent, /saved data only/);
  const local = setup({'POST /api/build/workflows/w1/estimate': () => ({...ESTIMATE, contacts: 0, reaches: ['local']})});
  await local.w.LeakScopeBuild.render(local.root, 'w1');
  click(local, 'Run'); await wait();
  assert.match(local.root.querySelector('.build-confirm').textContent, /reads and changes saved data only/);
});

test('workflows export to a file of names and steps, and import as new switched-off copies', async () => {
  const listed = {results: [{id: 'w1', name: 'Jenkins sweep', steps: 3, reaches: ['local'], groups: ['trigger'], last_run: null, automatic: {enabled: true}}]};
  const made = [];
  const s = setup({'GET /api/build/workflows': listed, 'GET /api/build/workflows/w1': detail({automatic: {enabled: true}}),
    'POST /api/build/workflows': call => { if (call.body.name === 'Broken') return {__status: 400, error: 'Name must be 1–200 characters'}; made.push(call.body); return {id: 'n' + made.length, name: call.body.name, definition: call.body.definition, problems: made.length === 2 ? 'Step 1 has no block' : ''}; }}, 'http://localhost/build/');
  let saved = null;
  s.w.LeakScopeExport = {slug: v => String(v).toLowerCase(), today: () => '2026-11-16', save: (name, content, type, mark) => { saved = {name, content, type, mark}; }};
  await s.w.LeakScopeBuild.render(s.root, '');
  click(s, 'Export all');
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(saved.name, 'workflows-2026-11-16.json'); assert.equal(saved.mark, false);
  const file = JSON.parse(saved.content);
  assert.equal(file.format, 'leakscope-workflows'); assert.equal(file.version, 1);
  assert.deepEqual(file.workflows, [{name: 'Jenkins sweep', definition: {steps: STEPS}}], 'no ids, runs or automatic state leave with the file');
  const picker = s.root.querySelector('input[type=file]');
  const give = async text => { Object.defineProperty(picker, 'files', {configurable: true, value: [{size: text.length, text: async () => text}]}); picker.dispatchEvent(new s.w.Event('change')); await new Promise(resolve => setTimeout(resolve, 30)); };
  await give('not json');
  assert.match(s.root.querySelector('[role=status]').textContent, /not valid JSON/);
  await give(JSON.stringify({format: 'something-else', workflows: []}));
  assert.match(s.root.querySelector('[role=status]').textContent, /not a LeakScope workflow file/);
  assert.equal(made.length, 0);
  await give(JSON.stringify({format: 'leakscope-workflows', version: 1, workflows: [{name: 'One', definition: {steps: STEPS}, enabled: true, id: 'x'}, {name: 'Two', definition: {steps: []}}, {name: 'Broken', definition: {steps: []}}]}));
  assert.deepEqual(made, [{name: 'One', definition: {steps: STEPS}}, {name: 'Two', definition: {steps: []}}], 'only the name and steps are sent');
  const text = s.root.querySelector('[role=status]').textContent;
  assert.match(text, /Imported 2 workflows/); assert.match(text, /switched off/); assert.match(text, /1 needs fixing/); assert.match(text, /Not imported: Broken/);
  await give(JSON.stringify({name: 'Single', definition: {steps: []}}));
  assert.equal(made.length, 3, 'a single exported workflow imports too');
});
