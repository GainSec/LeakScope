// Evidence levels, capture states and image classes are shown as what they are.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const base = path.resolve(__dirname, '../../leakscope_app/static/js/leakscope'), windows = [];
const tick = () => new Promise(resolve => setTimeout(resolve, 30));
test.afterEach(() => windows.splice(0).forEach(dom => dom.window.close()));

const E = '33333333-3333-4333-8333-333333333333';
const attribution = {
  level: 'provider_reported', label: 'Provider reported', caution: 'A port, keyword, tag or organization match is not an identity or ownership claim.',
  ladder: [
    {level: 'candidate', label: 'Discovery candidate', meaning: 'Matched a search.', established: true, items: [{kind: 'search', text: 'Returned by Lookup “Port 12798” (shodan: port:12798). Tags applied to every result: cardanonode, identity-candidate.', at: '2026-11-12T00:51:28Z'}]},
    {level: 'provider_reported', label: 'Provider reported', meaning: 'A provider named the service.', established: true, items: [{kind: 'evidence', text: 'SSH: The saved provider response starts with SSH identification text; not confirmed live.', at: '2026-11-06T06:49:56Z'}]},
    {level: 'protocol_confirmed', label: 'Live protocol confirmed', meaning: 'A live exchange completed.', established: false, items: []},
    {level: 'implementation_verified', label: 'Implementation verified', meaning: 'Implementation-specific evidence.', established: false, items: []}
  ],
  tags: [{tag: 'cardanonode', origin: 'lookup:x', origin_label: 'Lookup “Port 12798” (shodan: port:12798)', role: 'grouping', note: 'Applied to every result of this search. A match is not an identity or ownership claim.'}],
  implementation: {state: 'unverified', note: 'Not established. A shared protocol, protocol version, port or banner does not identify the implementation.', collector_notes: []}
};
const captureStatus = {
  state: 'failed', label: 'Capture failed', reason: '1 attempt failed. Last: Wire exchange failed or timed out.', channel: 'protocol',
  screenshot: {state: 'not_applicable', label: 'Not applicable', reason: 'SSH is not a web page or remote display, so there is nothing to screenshot. Saved protocol bytes are drawn as an evidence image instead.'},
  protocol: {state: 'failed', label: 'Capture failed', reason: '1 attempt failed. Last: Wire exchange failed or timed out.', operation: 'SSH identification banner only'},
  images: {browser: 0, framebuffer: 0, rendered: 1}, visual: {class: 'rendered', note: 'Drawn by LeakScope from saved response bytes. It is not a screenshot and not a terminal session.'},
  attempts: {total: 1, failed: 1, retried: false},
  history: [{run_id: 'r1', state: 'failed', at: '2026-11-12T00:51:40Z', collection_mode: 'full', routed_by_hint: false,
    actions: [{action: 'check', label: 'TCP reachability check', state: 'failed', message: 'TCP/TLS reachability check failed or timed out'}, {action: 'tcp', label: 'Bounded TCP read', state: 'failed', message: 'Wire exchange failed or timed out'}]}],
  basis: 'Saved capture jobs and files only.'
};
const rendered = {id: 'a1', endpoint_id: E, reference: {type: 'endpoint', id: E}, address: '114.55.136.135', port: 12798, title: 'OpenSSH', technology: 'ssh', technology_basis: 'provider_reported', provider: 'shodan',
  created_at: '2026-11-12T03:00:00Z', mime: 'image/png', size: 33000, sha256: 'a'.repeat(64), original_url: '/api/operations/artifacts/a1', thumbnail_url: '/api/atlas/screenshots/a1/thumbnail',
  manual_tags: [], inherited_tags: ['cardanonode'], collections: [], pinned: false, group: {key: 'all', label: 'All'},
  capture_class: 'rendered', capture_label: 'Rendered protocol evidence', capture_note: 'Drawn by LeakScope from saved response bytes. It is not a screenshot and not a terminal session.',
  capture: {state: 'failed', label: 'Capture failed · timed out', reason: '1 attempt failed.', channel: 'protocol', screenshot_state: 'not_applicable', protocol_state: 'failed', images: {browser: 0, framebuffer: 0, rendered: 1}},
  evidence: {state: 'reported', state_label: 'Provider reported · not verified live', headline: 'SSH identification saved by Shodan', description: 'Rendered protocol evidence (not a screenshot). Target 114.55.136.135:12798 tcp.',
    operation: 'Provider-saved response text; nothing sent by LeakScope', source_kind: 'provider_banner', source_observed_at: '2026-11-06T06:49:56Z', source_collected_at: '2026-11-12T00:51:28Z',
    raw_files: [{id: 'w1', kind: 'wire-transcript', mime: 'application/json', size: 655, url: '/api/operations/artifacts/w1'}]}};
const browserShot = {...rendered, id: 'b1', title: 'Saved console', capture_class: 'browser', capture_label: 'Browser screenshot', capture_note: 'A browser loaded the page and saved what it drew.', evidence: null, capture: null,
  original_url: '/api/operations/artifacts/b1', thumbnail_url: '/api/atlas/screenshots/b1/thumbnail'};

function load(html, url, files, routes = {}) {
  const dom = new JSDOM(html, {url, runScripts: 'outside-only', pretendToBeVisual: true}); windows.push(dom);
  const w = dom.window, calls = [];
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.fetch = async (value, opts = {}) => {
    const u = new URL(value, w.location.href); calls.push({url: u, opts});
    const data = routes[u.pathname] ?? {results: []};
    return {ok: true, status: 200, json: async () => data};
  };
  for (const name of files) w.eval(fs.readFileSync(path.join(base, name), 'utf8'));
  return {dom, w, d: w.document, calls};
}

test('level and capture strips count endpoints in words and link each level to its filter', () => {
  const {w, d} = load('<main></main>', 'http://localhost/', ['evidence.js']);
  const levels = w.LeakScopeEvidence.levelStrip({candidate: 20, provider_reported: 12, protocol_confirmed: 1, implementation_verified: 0}, {href: level => '/assets/?attribution=' + level});
  d.body.append(levels);
  assert.equal(levels.querySelectorAll('.ev-seg').length, 3, 'a level with no endpoints draws no segment');
  assert.match(levels.querySelector('[role=img]').getAttribute('aria-label'), /20 discovery candidate, 12 provider reported, 1 live protocol confirmed/);
  assert.deepEqual([...levels.querySelectorAll('.ev-key li')].map(node => node.textContent), ['20 candidates', '12 provider reported', '1 live confirmed']);
  assert.equal(levels.querySelector('.ev-key a').getAttribute('href'), '/assets/?attribution=candidate');
  const captures = w.LeakScopeEvidence.captureStrip({succeeded: 3, failed: 2, not_applicable: 9});
  assert.deepEqual([...captures.querySelectorAll('.ev-key li')].map(node => node.textContent), ['3 captured', '2 failed', '9 not applicable']);
  assert.match(w.LeakScopeEvidence.levelStrip({}).textContent, /No identity evidence/);
  const unnamed = w.LeakScopeEvidence.levelStrip({none: 5, candidate: 2}, {href: level => '/assets/?attribution=' + level});
  assert.deepEqual([...unnamed.querySelectorAll('.ev-key li')].map(node => node.textContent), ['5 unidentified', '2 candidates']);
  assert.equal(unnamed.querySelectorAll('.ev-key a').length, 1, 'only real levels link to a filter');
});

test('the three image classes carry different labels and notes', () => {
  const {w} = load('<main></main>', 'http://localhost/', ['evidence.js']);
  const labels = ['browser', 'framebuffer', 'rendered'].map(name => w.LeakScopeEvidence.classBadge(name));
  assert.deepEqual(labels.map(node => node.textContent), ['Browser screenshot', 'Native framebuffer capture', 'Rendered protocol evidence']);
  assert.match(labels[2].getAttribute('title'), /not a screenshot and not a terminal session/);
  assert.equal(new Set(labels.map(node => node.className)).size, 3);
  assert.equal(w.LeakScopeEvidence.classBadge('terminal'), null);
});

test('inspector shows the evidence ladder, the reason no capture exists and every attempt', async () => {
  const data = {endpoint: {id: E, address: '114.55.136.135', port: 12798, transport: 'tcp', service_type: 'auto', effective_service_type: 'ssh', protocol_hint: {service: 'ssh'}, title: 'OpenSSH'},
    observations: [], attribution, capture_status: captureStatus,
    artifacts: [{id: 'a1', kind: 'evidence-render', capture_class: 'rendered', capture_label: 'Rendered protocol evidence', capture_note: rendered.capture_note, created_at: '2026-11-12T03:00:00Z',
      url: '/api/operations/artifacts/a1', metadata: {render: rendered.evidence}}]};
  const {w, d, calls} = load('<main><button id="origin">Inspect</button></main>', 'http://localhost/assets/', ['evidence.js', 'inspector.js'], {['/api/operations/endpoints/' + E]: data});
  await w.LeakScopeInspector.open({type: 'endpoint', id: E}); await tick();
  assert.ok(calls.every(call => !call.opts.method || call.opts.method === 'GET'));
  const text = d.body.textContent;
  assert.match(text, /SSH \(provider reported from saved response text; stored type: auto\)/);
  const rungs = [...d.querySelectorAll('.ev-rung')];
  assert.deepEqual(rungs.map(node => node.classList.contains('is-established')), [true, true, false, false]);
  assert.match(rungs[0].textContent, /Port 12798/);
  assert.match(rungs[2].textContent, /Not established/);
  assert.match(rungs[3].textContent, /does not identify the implementation/);
  assert.match(text, /not an identity or ownership claim/);
  assert.match(text, /Capture failed/);
  assert.doesNotMatch(text, /Capture failed · timed out/, 'an ambiguous saved error is not reported as a timeout');
  assert.match(text, /nothing to screenshot/);
  assert.match(text, /Wire exchange failed or timed out/);
  assert.match(text, /1 saved attempt/);
  [...d.querySelectorAll('[role=tab]')].find(node => node.textContent === 'Captures').click();
  const artifact = d.querySelector('.inspector-artifact');
  assert.equal(artifact.dataset.captureClass, 'rendered');
  assert.match(artifact.querySelector('summary').textContent, /^Rendered protocol evidence/);
  assert.equal(d.querySelectorAll('img').length, 0, 'the image loads only when opened');
  artifact.open = true; artifact.dispatchEvent(new w.Event('toggle'));
  assert.match(d.querySelector('img').getAttribute('alt'), /not a screenshot/);
  assert.match(artifact.textContent, /Open evidence image/);
});

test('gallery labels each image class, filters by it and keeps raw files beside a drawn image', async () => {
  const {w, d, calls} = load('<meta name="csrf-token" content="fixture"><main id="root"></main>', 'http://localhost/screenshots/?capture_class=rendered', ['evidence.js', 'atlas.js'], {
    '/api/atlas/screenshots': {results: [rendered, browserShot], count: 2, total_visible_count: 2, has_more: false, basis: 'Retained visible images only.', class_counts: {browser: 248, framebuffer: 0, rendered: 591}},
    '/api/atlas/collections': {results: []}, '/api/assets/organizations': {results: []}});
  const root = d.querySelector('#root'); await w.LeakScopeAtlas.render(root, 'screenshots'); await tick();
  const request = calls.find(call => call.url.pathname === '/api/atlas/screenshots');
  assert.equal(request.url.searchParams.get('capture_class'), 'rendered');
  assert.deepEqual([...root.querySelector('[name=capture_class]').options].map(option => option.textContent), ['All image types', 'Browser screenshots', 'Native framebuffer captures', 'Rendered protocol evidence']);
  const cards = [...root.querySelectorAll('.atlas-shot-card')];
  assert.deepEqual(cards.map(card => card.dataset.captureClass), ['rendered', 'browser']);
  assert.deepEqual(cards.map(card => card.querySelector('.ev-class').textContent), ['Rendered protocol evidence', 'Browser screenshot']);
  assert.match(cards[0].querySelector('.atlas-shot-open').getAttribute('aria-label'), /^Open evidence image:/);
  assert.match(cards[1].querySelector('.atlas-shot-open').getAttribute('aria-label'), /^Open screenshot:/);
  assert.match(cards[0].textContent, /Provider reported · not verified live/);
  assert.match(cards[0].textContent, /Evidence saved/);
  cards[0].querySelector('.atlas-shot-open').click(); await tick();
  const dialog = d.querySelector('[role=dialog]');
  assert.match(dialog.querySelector('.atlas-viewer-title').textContent, /^Rendered protocol evidence · 114\.55\.136\.135:12798/);
  assert.match(dialog.querySelector('img').getAttribute('alt'), /not a screenshot/);
  assert.match(dialog.textContent, /nothing sent by LeakScope/);
  assert.equal(dialog.querySelector('.ev-raw-files a').getAttribute('href'), '/api/operations/artifacts/w1');
  assert.match(dialog.textContent, /Screenshot: not applicable/);
  const types = [...root.querySelectorAll('.ev-type')];
  assert.deepEqual(types.map(node => node.textContent), ['All images839', 'Browser screenshot248', 'Native framebuffer capture0', 'Rendered protocol evidence591']);
  assert.deepEqual(types.map(node => node.getAttribute('aria-pressed')), ['false', 'false', 'false', 'true']);
  assert.equal(types[2].disabled, true, 'a class with no images cannot be chosen');
  assert.match(root.textContent, /Draw evidence images from saved responses/);
  assert.ok(calls.every(call => !call.opts.method || call.opts.method === 'GET'), 'browsing draws or requests nothing');
  d.querySelector('[role=dialog] .atlas-viewer-close')?.click();
  types[1].click(); await tick();
  assert.equal(calls.filter(call => call.url.pathname === '/api/atlas/screenshots').at(-1).url.searchParams.get('capture_class'), 'browser');
});

test('technology cards separate candidates from confirmed services and explain missing captures', async () => {
  const group = {key: 'ssh', label: 'SSH', category: 'other', endpoints: 33, screenshots: 0, quality: 'mixed', level: 'protocol_confirmed',
    levels: {candidate: 20, provider_reported: 12, protocol_confirmed: 1, implementation_verified: 0},
    captures: {succeeded: 1, partial: 0, failed: 3, pending: 0, not_attempted: 29, unsupported: 0, not_applicable: 0}, images: {browser: 0, framebuffer: 0, rendered: 14},
    capture_note: {state: 'not_attempted', reason: 'No protocol capture has been requested for this endpoint.', endpoints: 29},
    preview: {id: 'a1', capture_class: 'rendered', thumbnail_url: '/api/atlas/screenshots/a1/thumbnail', original_url: '/api/operations/artifacts/a1', title: 'SSH saved capture'}, filters: {technology: 'ssh'}};
  const {w, d, calls} = load('<meta name="csrf-token" content="fixture"><main id="root"></main>', 'http://localhost/technology-dashboard/?attribution=provider_reported', ['evidence.js', 'atlas.js'], {
    '/api/atlas/technologies': {totals: {endpoints: 40, technologies: 1, screenshots: 0, unknown: 2, unverified: 5, levels: {candidate: 22, provider_reported: 12, protocol_confirmed: 1, implementation_verified: 0, none: 5},
      captures: {succeeded: 1, failed: 3, not_attempted: 36}}, groups: [group], categories: [{key: 'other', label: 'Other', endpoints: 33}], basis: 'Visible retained identity evidence only.'},
    '/api/assets/organizations': {results: []}});
  const root = d.querySelector('#root'); await w.LeakScopeAtlas.render(root, 'technologies'); await tick();
  assert.equal(calls.find(call => call.url.pathname === '/api/atlas/technologies').url.searchParams.get('attribution'), 'provider_reported');
  const card = root.querySelector('.atlas-tech-card');
  assert.match(card.textContent, /0 retained captures · 14 rendered evidence images/);
  assert.match(card.textContent, /20 candidates/);
  assert.match(card.textContent, /12 provider reported/);
  assert.match(card.textContent, /1 live confirmed/);
  assert.equal(card.querySelector('.ev-strip-level .ev-key a').getAttribute('href'), '/assets/?attribution=candidate&technology=ssh');
  assert.match(card.textContent, /29 without a live capture: No protocol capture has been requested/);
  assert.equal(card.querySelector('.atlas-tech-preview .ev-class').textContent, 'Rendered protocol evidence');
  assert.match(card.querySelector('.atlas-tech-preview img').getAttribute('alt'), /not a screenshot/);
  assert.match(card.querySelector('.atlas-tech-preview').getAttribute('href'), /capture_class=rendered/);
  const panel = root.querySelector('.ev-levels-panel');
  assert.equal(panel.hidden, false);
  assert.match(panel.textContent, /22 candidates/);
  assert.match(panel.textContent, /36 not attempted/);
});
