const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../..');
const profile = {
  key: 'postgres', label: 'PostgreSQL', category: 'database', ports: [5432], transport: 'tcp',
  identity: 'Protocol identity', inspection: false, export: null,
  detection_confidence: 'Provider evidence only', fetch_actions: ['tcp'],
  presentation: {icon_url: '/static/img/service-icons/postgresql.svg', icon_kind: 'brand', documentation_url: 'https://www.postgresql.org/docs/', download_url: 'https://www.postgresql.org/download/'},
  query_variants: {
    fingerprint: {shodan: {query: 'product:PostgreSQL', supported: true, basis: 'Response signature'}, netlas: {supported: false, reason: 'No verified signature'}},
    nonstandard: {shodan: {query: 'product:PostgreSQL -port:5432', supported: true}},
    ports: {shodan: {query: 'port:5432', supported: true}},
  },
};
async function setup(results = [profile]) {
  const dom = new JSDOM('<div id="page-root"></div>', {url:'http://localhost/catalog/', runScripts:'outside-only'});
  const calls = [];
  dom.window.fetch = async url => {calls.push(url); return {ok:true, json:async()=>({results}), headers:{get:()=>null}};};
  dom.window.eval(fs.readFileSync(path.join(root,'leakscope_app/static/js/leakscope/operations.js'),'utf8'));
  await dom.window.LeakScopeOperations.render(dom.window.document.querySelector('#page-root'), 'catalog');
  return {dom, d:dom.window.document, calls};
}
const button = (d, label) => [...d.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === label || b.textContent === label);

test('portrait card flips through native controls, hides inactive face and restores focus on Escape', async () => {
  const {dom,d} = await setup();
  try {
    const front = button(d, 'Show PostgreSQL details');
    assert.ok(front, 'a native front button opens card details');
    const back = d.getElementById(front.getAttribute('aria-controls'));
    assert.equal(back.getAttribute('aria-hidden'),'true'); assert.ok(back.hasAttribute('inert'));
    front.focus(); front.click();
    assert.equal(front.getAttribute('aria-expanded'),'true'); assert.ok(front.hasAttribute('inert'));
    assert.equal(front.getAttribute('aria-hidden'),'true'); assert.equal(back.getAttribute('aria-hidden'),'false'); assert.ok(!back.hasAttribute('inert'));
    const close = button(d, 'Show PostgreSQL front'); assert.equal(d.activeElement,close);
    close.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(front.getAttribute('aria-expanded'),'false'); assert.equal(d.activeElement,front);
    front.click(); close.click(); assert.equal(d.activeElement,front);
  } finally {dom.window.close();}
});

test('card backs omit unsupported capabilities and empty capture metadata', async () => {
  const {dom,d} = await setup([profile, {...profile,key:'bare',label:'Bare',identity:false,fetch_actions:[],detection_confidence:null,ports:[],presentation:{}}]);
  try {
    const cards = d.querySelectorAll('.service-card'); assert.equal(cards.length,2);
    assert.deepEqual([...cards[0].querySelectorAll('dt')].map(e=>e.textContent), ['Detection','Identity']);
    assert.doesNotMatch(cards[0].textContent,/Not available|Inspection|Export/);
    assert.doesNotMatch(cards[1].textContent,/Evidence capture:|No universal reference port|Not available/);
    assert.match(cards[0].textContent,/Reference ports/);
    assert.match(d.body.textContent,/All indexed ports/);
  } finally {dom.window.close();}
});

test('local logos and official links are safe and links leave the card open without requests', async () => {
  const {dom,d,calls} = await setup([profile,{...profile,key:'untrusted',label:'<img src=x onerror=alert(1)>',presentation:{icon_url:'https://tracking.invalid/logo.svg',documentation_url:'javascript:alert(1)',download_url:null}}]);
  try {
    assert.deepEqual([...d.querySelectorAll('img')].map(i=>i.getAttribute('src')), ['/static/img/service-icons/postgresql.svg']);
    assert.equal(d.querySelectorAll('img[onerror]').length,0);
    const front = button(d,'Show PostgreSQL details'); front.click();
    const docs = [...d.querySelectorAll('a')].find(a=>a.textContent === 'Documentation');
    assert.equal(docs.href,'https://www.postgresql.org/docs/'); assert.match(docs.rel,/noopener/);
    assert.equal(d.querySelectorAll('a[href^="javascript:"]').length,0);
    docs.addEventListener('click',event=>event.preventDefault()); docs.click();
    assert.equal(front.getAttribute('aria-expanded'),'true');
    assert.deepEqual(calls,['/api/operations/catalog']);
  } finally {dom.window.close();}
});

test('official Memcached JPEG renders while unapproved image paths stay blocked', async () => {
  const memcached = {...profile, key:'memcached', label:'Memcached', presentation:{
    icon_url:'/static/img/service-icons/memcached.jpg', icon_kind:'brand',
    documentation_url:'https://docs.memcached.org/', download_url:'https://memcached.org/downloads',
  }};
  const rows = [memcached, ...['/static/img/service-icons/unapproved.html', '/static/img/service-icons/../private.jpg', 'https://tracking.invalid/memcached.jpg'].map((icon_url,index)=>({...profile,key:'unsafe'+index,presentation:{icon_url}}))];
  const {dom,d} = await setup(rows);
  try {
    assert.deepEqual([...d.querySelectorAll('img')].map(i=>i.getAttribute('src')), ['/static/img/service-icons/memcached.jpg']);
    assert.match(d.querySelector('.service-card').textContent,/Technology profile/);
  } finally {dom.window.close();}
});

test('query presets, unsupported reasons and explicit Discover mode survive flip cards', async () => {
  const {dom,d} = await setup();
  try {
    assert.match(d.body.textContent,/No verified signature/); assert.match(d.body.textContent,/Response signature/);
    let lookup = [...d.querySelectorAll('a')].find(a=>a.textContent === 'Create lookup');
    assert.equal(new URL(lookup.href).searchParams.get('query'),'product:PostgreSQL');
    const mode = d.querySelector('[name=catalog_query_mode]'); mode.value='nonstandard'; mode.dispatchEvent(new dom.window.Event('change'));
    lookup = [...d.querySelectorAll('a')].find(a=>a.textContent === 'Create lookup');
    assert.equal(new URL(lookup.href).searchParams.get('query'),'product:PostgreSQL -port:5432');
    const discover = [...d.querySelectorAll('a')].find(a=>a.textContent === 'Open in Discover');
    assert.equal(new URL(discover.href).searchParams.get('query_mode'),'nonstandard');
  } finally {dom.window.close();}
});

test('search categories and paging stay local and reset to the first card page', async () => {
  const rows = Array.from({length:28},(_,i)=>({...profile,key:'service'+i,label:'Service '+i,categories:i===27?['ot','iot']:['database']}));
  const {dom,d,calls} = await setup(rows);
  try {
    assert.equal(d.querySelectorAll('.service-card').length,25);
    button(d,'Next technologies').click(); assert.equal(d.querySelectorAll('.service-card').length,3);
    const category=d.querySelector('[name=category]'); category.value='iot'; category.dispatchEvent(new dom.window.Event('change'));
    assert.equal(d.querySelectorAll('.service-card').length,1); assert.match(d.body.textContent,/Service 27/);
    const search=d.querySelector('[name=catalog_search]'); search.value='does not exist'; search.dispatchEvent(new dom.window.Event('input'));
    assert.equal(d.querySelectorAll('.service-card').length,0); assert.match(d.body.textContent,/No technologies match/);
    assert.deepEqual(calls,['/api/operations/catalog']);
  } finally {dom.window.close();}
});
