const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
function setup(markup='') {
  const dom = new JSDOM('<body><main id="page-root">'+markup+'</main></body>', {url:'http://localhost/correlations/',runScripts:'outside-only'});
  dom.window.matchMedia=()=>({matches:false,addEventListener(){}});
  dom.window.fetch=()=>{throw Error('Enhancement must not make requests')};
  dom.window.eval(fs.readFileSync('leakscope_app/static/js/leakscope/workspace.js','utf8'));
  return dom;
}
test('shared enhancement gives submit and leading header actions primary emphasis while preserving explicit tones and handlers',()=>{
  const dom=setup('<div class="page-heading"><h1>Page</h1><div class="heading-actions"><a id="lead" class="btn" href="/assets/">Open</a><a id="other" class="btn" href="/runs/">History</a></div></div><form><button id="submit" class="btn" type="submit">Apply</button><button id="danger" class="btn btn-danger" type="submit">Remove</button><button id="quiet" class="btn btn-quiet" type="submit">Quiet</button><button id="secondary" class="btn" data-action-tone="secondary" type="submit">Secondary</button></form>');
  const d=dom.window.document;
  try {
    for(const id of ['lead','submit'])assert.ok(d.getElementById(id).classList.contains('btn-primary'),id);
    for(const id of ['other','danger','quiet','secondary'])assert.equal(d.getElementById(id).classList.contains('btn-primary'),false,id);
    let clicked=0;const lead=d.getElementById('lead');lead.addEventListener('click',e=>{e.preventDefault();clicked++});dom.window.LeakScopeShell.enhance(d.querySelector('main'));lead.click();assert.equal(clicked,1);assert.equal(d.getElementById('lead'),lead);
  }finally{dom.window.close()}
});
test('late inserted actions are enhanced and holographic surfaces remain on cards',async()=>{
  const dom=setup();const d=dom.window.document;
  try {
    const block=d.createElement('section');block.innerHTML='<article class="workspace-correlation-group"></article><article class="workspace-change-card"></article><section class="workspace-saved-views"></section><form class="workspace-filters"><button type="submit" class="btn">Apply</button></form><h2>Heading</h2>';d.querySelector('main').append(block);
    await new Promise(r=>setTimeout(r,0));
    assert.equal(block.querySelectorAll('.holo-surface').length,3);
    assert.ok(block.querySelector('button').classList.contains('btn-primary'));
    assert.equal(block.classList.contains('holo-surface'),false);assert.equal(block.querySelector('h2').classList.contains('holo-surface'),false);
  }finally{dom.window.close()}
});
