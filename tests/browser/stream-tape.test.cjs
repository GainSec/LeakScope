const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../leakscope_app/static/js/leakscope/scope.js'), 'utf8');
const open = [];
test.afterEach(() => open.splice(0).forEach(dom => dom.window.close()));

function setup(stored) {
  const dom = new JSDOM('<body><main id="root"></main></body>', {url: 'http://localhost/', runScripts: 'outside-only'});
  open.push(dom);
  const w = dom.window;
  if (stored) w.sessionStorage.setItem('leakscope.stream.samples', JSON.stringify(stored));
  w.fetch = () => { throw Error('The tape must not make requests in these tests'); };
  w.eval(source);
  return {dom, w, scope: w.LeakScopeScope};
}
const sample = (at, received) => ({at, received});
const plain = value => JSON.parse(JSON.stringify(value));

test('each bar is the certificates counted between two readings that changed', () => {
  const {scope} = setup();
  const tape = scope.streamTape([sample(0, 1000), sample(8000, 1000), sample(16000, 5000), sample(24000, 9000)], [], 24000);
  assert.deepEqual(plain(tape.bars), [
    {from: 0, to: 16000, count: 4000, rate: 250},
    {from: 16000, to: 24000, count: 4000, rate: 500},
  ]);
  assert.equal(tape.peak, 500);
});

test('a counter reset, a long gap and readings older than the window draw no bar', () => {
  const {scope} = setup();
  const reset = scope.streamTape([sample(0, 9000), sample(8000, 100), sample(16000, 900)], [], 16000);
  assert.deepEqual(plain(reset.bars.map(bar => bar.count)), [800]);
  const gap = scope.streamTape([sample(0, 100), sample(120000, 90000), sample(128000, 91600)], [], 128000);
  assert.deepEqual(plain(gap.bars.map(bar => [bar.from, bar.count])), [[120000, 1600]]);
  const old = scope.streamTape([sample(0, 0), sample(8000, 800), sample(400000, 900), sample(408000, 1700)], [], 408000);
  assert.deepEqual(plain(old.bars.map(bar => bar.from)), [400000]);
  assert.equal(scope.streamTape([], [], 5).bars.length, 0);
  assert.equal(scope.streamTape([], [], 5).peak, 0);
});

test('only saved matches inside the window become markers, at their own time', () => {
  const {scope} = setup();
  const now = Date.parse('2026-11-13T12:00:00Z');
  const tape = scope.streamTape([], [
    {id: 'a', names: ['api.example.com'], watch_name: 'acme', collected_at: '2026-11-13T11:59:00Z'},
    {id: 'b', names: ['old.example.com'], watch_name: 'acme', collected_at: '2026-11-13T09:00:00Z'},
    {id: 'c', names: ['x'], watch_name: 'acme', collected_at: 'not a date'},
  ], now);
  assert.deepEqual(plain(tape.marks.map(mark => [mark.id, now - mark.at, mark.names, mark.watch])), [['a', 60000, 'api.example.com', 'acme']]);
});

test('the panel describes the tape in words, keeps readings for the next page and has no decorative gate', () => {
  const s = setup();
  let now = Date.parse('2026-11-13T12:00:00Z');
  s.w.Date.now = () => now;
  const panel = s.scope.stream({poll: false});
  s.w.document.querySelector('#root').append(panel);
  const data = received => ({state: 'connected', enabled: true, received, matched: 3, lag_seconds: 0, recent_matches: []});
  panel.update(data(1000));
  assert.match(panel.querySelector('.scope-stream-flow').getAttribute('aria-label'), /waiting for a second reading/i);
  now += 8000; panel.update(data(5000));
  const label = panel.querySelector('.scope-stream-flow').getAttribute('aria-label');
  assert.match(label, /500 per second/);
  assert.match(panel.querySelector('.scope-stream-scale').textContent, /500/);
  assert.equal(panel.querySelector('.scope-stream-gate'), null);
  assert.doesNotMatch(panel.textContent, /watches$/m);
  assert.deepEqual(JSON.parse(s.w.sessionStorage.getItem('leakscope.stream.samples')), [[now - 8000, 1000], [now, 5000]]);
  panel.stop();

  const again = setup([[now - 8000, 1000], [now, 5000], ['bad', 'row'], [now - 900000, 5]]);
  again.w.Date.now = () => now + 8000;
  const next = again.scope.stream({poll: false});
  next.update(data(9000));
  assert.deepEqual(plain(next.tape().bars.map(bar => bar.count)), [4000, 4000]);
  next.stop();
});

test('blocked storage does not break the panel', () => {
  const s = setup();
  const fail = () => { throw Error('blocked'); };
  s.w.Storage.prototype.getItem = fail; s.w.Storage.prototype.setItem = fail;
  const panel = s.scope.stream({poll: false});
  panel.update({state: 'connected', received: 10, matched: 0, recent_matches: []});
  assert.equal(panel.tape().bars.length, 0);
  panel.stop();
});
