const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const script = () => fs.readFileSync('leakscope_app/static/js/leakscope/cloud.js', 'utf8');
async function setup(resources=[]) {
  const dom = new JSDOM('<meta name="csrf-token" content="fixture"><main id="page-root"></main>', {url:'http://localhost/cloud/',runScripts:'outside-only'});
  const calls = [];
  dom.window.fetch = async (url, opts={}) => {
    calls.push({url:String(url),opts});
    let body = {results:[],count:0};
    if(new URL(String(url),'http://localhost').pathname.endsWith('/resources'))body={results:resources,count:resources.length};
    if(String(url).endsWith('/catalog')) body={providers:[{key:'aws',label:'Amazon Web Services',regions:[],kinds:['bucket']},{key:'scaleway',label:'Scaleway',regions:['fr-par','pl-waw'],kinds:['bucket']}],limitations:[]};
    if(String(url).endsWith('/preview')) body={generated:1,limited:false,skipped_names:[],candidates:[{provider:'aws',name:'acme',kind:'bucket',region:'',url:'https://acme.s3.amazonaws.com/'}]};
    if(String(url).endsWith('/start')) body={run:{id:'run-1',state:'succeeded',result:{attempted:1,stored:0,outcomes:[{name:'<img src=x onerror=alert(1)>',state:'unverified',reason:'HTTP 200 is insufficient'}]}}};
    return {ok:true,json:async()=>body};
  };
  dom.window.eval(script());
  await dom.window.LeakScopeCloud.render(dom.window.document.querySelector('main'));
  return {dom,calls,document:dom.window.document};
}
test('every cloud result can be added to an organization without starting a capture',async()=>{
  const {dom,calls,document:d}=await setup([{id:'cloud-1',name:'fixture',provider:'aws',kind:'bucket',url:'https://fixture.example.com',state:'public',last_checked:'2026-11-08',evidence:{}}]);
  const opened=[];dom.window.LeakScopeAssets={addToOrganization:ref=>opened.push(ref)};
  const add=[...d.querySelectorAll('button')].find(b=>b.textContent==='Add to organization');assert.ok(add);add.click();
  assert.equal(opened[0].type,'cloud');assert.equal(opened[0].id,'cloud-1');assert.equal(calls.some(c=>c.url.endsWith('/fetch')),false);dom.window.close();
});
test('preview uses explicit names without starting discovery', async()=>{
  const {dom,calls,document:d}=await setup();
  d.querySelector('[name=names]').value='acme';
  d.querySelector('[data-action=preview]').click();
  await new Promise(r=>setTimeout(r,20));
  assert.equal(calls.some(c=>c.url.endsWith('/start')),false);
  assert.match(d.querySelector('[data-cloud-preview]').textContent,/acme.s3.amazonaws.com/);
  assert.equal(d.querySelector('[name=permutations]').checked,false);
  dom.window.close();
});
test('active consent is required and result values render as text',async()=>{
  const {dom,calls,document:d}=await setup();
  d.querySelector('[name=names]').value='acme';
  d.querySelector('[data-action=start]').click();
  await new Promise(r=>setTimeout(r,20));
  assert.equal(calls.some(c=>c.url.endsWith('/start')),false);
  d.querySelector('[name=active]').checked=true;
  d.querySelector('[data-action=start]').click();
  await new Promise(r=>setTimeout(r,20));
  const call=calls.find(c=>c.url.endsWith('/start'));
  assert.equal(JSON.parse(call.opts.body).active,true);
  assert.equal(call.opts.headers['X-CSRFToken'],'fixture');
  assert.equal(d.querySelector('[data-cloud-progress] img'),null);
  assert.match(d.querySelector('[data-cloud-progress]').textContent,/HTTP 200 is insufficient/);
  dom.window.close();
});
test('cloud inspector uses the loaded saved resource without starting a new check',async()=>{const record={id:'cloud-1',name:'fixture',provider:'aws',kind:'bucket',url:'https://fixture.example.com',state:'public',evidence:{reason:'Saved check'}};const {dom,calls,document:d}=await setup([record]);let ref;dom.window.LeakScopeInspector={open:r=>ref=r};await dom.window.LeakScopeCloud.render(d.querySelector('main'));const inspect=[...d.querySelectorAll('button')].find(b=>b.textContent==='Inspect');assert.ok(inspect);inspect.click();assert.equal(ref.type,'cloud');assert.equal(ref.record.evidence.reason,'Saved check');assert.equal(calls.some(c=>c.opts.method==='POST'),false);dom.window.close()});
