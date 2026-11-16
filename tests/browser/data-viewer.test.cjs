const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const filename = path.resolve(__dirname, '../../leakscope_app/static/js/leakscope/data_viewer.js');
function setup(options = {}) {
  const dom = new JSDOM('<main></main>', {runScripts:'outside-only',url:'http://localhost/'});
  if (options.withoutSourceContext) {
    const nativeParse = dom.window.JSON.parse;
    dom.window.JSON.parse = (source, reviver) => nativeParse(source,
      typeof reviver === "function" ? function (key, value) {return reviver.call(this,key,value);} : reviver);
  }
  if (fs.existsSync(filename)) dom.window.eval(fs.readFileSync(filename,'utf8'));
  assert.ok(dom.window.LeakScopeData?.viewer, 'shared viewer is exported');
  return {dom, view:(value,options)=>{const node=dom.window.LeakScopeData.viewer(value,options);dom.window.document.querySelector('main').append(node);return node;}};
}
const mode = (node, value) => node.querySelector('[data-viewer-mode="'+value+'"]').click();
async function until(predicate) {
  const deadline = Date.now() + 2000;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for the native details toggle to render its content");
    await new Promise(resolve=>setTimeout(resolve,5));
  }
}
test('formatted is default and raw roundtrips all original JSON without mutating it',()=>{
  const {dom,view}=setup();const input={host:'api.example',port:9443,enabled:false,count:0,unknown_key:null,empty:'',items:[],object:{}};const snapshot=JSON.stringify(input);const node=view(input,{label:'Provider observation'});
  assert.equal(node.querySelector('[data-viewer-mode=formatted]').getAttribute('aria-pressed'),'true');assert.equal(node.querySelector('.data-raw'),null);assert.match(node.textContent,/Hostname/);for(const value of ['False','0','No value','Empty text','Empty list','Empty object'])assert.match(node.textContent,new RegExp(value));
  mode(node,'raw');assert.equal(node.querySelector('[data-viewer-mode=raw]').getAttribute('aria-pressed'),'true');assert.equal(node.querySelector('.data-raw').textContent,JSON.stringify(input,null,2));assert.deepEqual(JSON.parse(node.querySelector('.data-raw').textContent),input);mode(node,'formatted');assert.equal(JSON.stringify(input),snapshot);assert.match(node.textContent,/Unknown key/);dom.window.close();
});
test('hostile keys, strings and multiline evidence render as text in both modes',()=>{
  const {dom,view}=setup();const node=view({'<img src=x onerror=alert(1)>':'<script>injected=true</script>',response:'HTTP/1.1 200 OK\r\n\r\n<html>literal</html>'});assert.equal(node.querySelector('img,script,html'),null);assert.match(node.textContent,/<img src=x/);assert.match(node.textContent,/<script>/);assert.match(node.querySelector('.data-text').textContent,/\r\n\r\n/);mode(node,'raw');assert.equal(node.querySelector('img,script'),null);assert.match(node.querySelector('.data-raw').textContent,/<script>/);dom.window.close();
});
test('nested arrays and objects expand lazily and retain ordered values',async()=>{
  const {dom,view}=setup();const node=view({records:[{name:'first',zero:0},{name:'second',valid:false}]});assert.equal(node.querySelector('.data-items'),null);let details=node.querySelector('.data-nested');details.open=true;await until(()=>node.querySelector('.data-items'));assert.equal(node.querySelectorAll('.data-items > li').length,2);let inner=node.querySelector('.data-items .data-nested');inner.open=true;await until(()=>inner.querySelector('.data-fields'));assert.match(inner.textContent,/first/);assert.match(inner.textContent,/0/);mode(node,'raw');assert.equal(JSON.parse(node.querySelector('.data-raw').textContent).records[1].name,'second');dom.window.close();
});
test('large root objects and lists render bounded pages with explicit access to all fields',async()=>{
  const {dom,view}=setup();const input=Object.fromEntries(Array.from({length:1000},(_,i)=>['key_'+i,{value:i}]));const node=view(input);assert.equal(node.querySelectorAll('.data-field').length,32);assert.equal(node.querySelectorAll('.data-nested .data-field').length,0);node.querySelector('.data-more').click();assert.equal(node.querySelectorAll('.data-field').length,64);mode(node,'raw');assert.equal(Object.keys(JSON.parse(node.querySelector('.data-raw').textContent)).length,1000);
  const array=view(Array.from({length:80},(_,i)=>i));assert.equal(array.querySelectorAll('.data-items > li').length,32);array.querySelector('.data-more').click();assert.equal(array.querySelectorAll('.data-items > li').length,64);array.querySelector('.data-more').click();assert.equal(array.querySelectorAll('.data-items > li').length,80);assert.equal(array.querySelector('.data-more'),null);dom.window.close();
});
test('JSON-encoded strings format their structure but preserve the original string in Raw JSON',()=>{
  const {dom,view}=setup();const original=' { "status": false, "count": 0 }\n';const node=view(original);assert.match(node.textContent,/Status/);assert.match(node.textContent,/False/);mode(node,'raw');assert.equal(JSON.parse(node.querySelector('.data-raw').textContent),original);dom.window.close();
});
test('viewer modes are independent, keyboard native, and readable dates keep original values',()=>{
  const {dom,view}=setup();const first=view({observed_at:'2026-11-09T08:30:00Z',fingerprint:'a'.repeat(64)});const second=view({status:'saved'});assert.equal(first.querySelectorAll('button[type=button]').length,2);mode(first,'raw');assert.equal(second.querySelector('[data-viewer-mode=formatted]').getAttribute('aria-pressed'),'true');mode(first,'formatted');assert.equal(first.querySelector('time').getAttribute('datetime'),'2026-11-09T08:30:00Z');assert.equal(first.querySelector('[title="'+'a'.repeat(64)+'"]').title,'a'.repeat(64));dom.window.close();
});
test('large string bodies stay collapsed until requested and preserve literal wire text',async()=>{
  const {dom,view}=setup();const wire='HTTP/1.1 200 OK\r\n\r\n'+('<html>literal</html>\n'.repeat(500));const node=view({body:wire});assert.equal(node.querySelector('.data-text'),null);const details=node.querySelector('.data-long-text');assert.ok(details);details.open=true;await until(()=>details.querySelector('.data-text'));assert.equal(details.querySelector('.data-text').textContent,wire);assert.equal(details.querySelector('html'),null);mode(node,'raw');assert.equal(JSON.parse(node.querySelector('.data-raw').textContent).body,wire);dom.window.close();
});
test('certificate module uses the shared viewer for issuer and original evidence without extra lookups',async()=>{
  const {dom}=setup();const document=dom.window.document,calls=[];const fixture={certificate:{id:'c1',fingerprint:'abc',issuer:{cn:'Example CA',organization:'<img src=x>'},domains:['example.com']},data:{issuer:{cn:'Example CA'},verified:false,unknown_key:0}};
  dom.window.history.replaceState({},'', '/certificates/?id=c1');dom.window.fetch=async url=>{calls.push(url);return{ok:true,json:async()=>fixture}};
  const source=path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/relationships.js');dom.window.eval(fs.readFileSync(source,'utf8'));await dom.window.LeakScopeAssets.render(document.querySelector('main'),'certificates');
  assert.equal(calls.length,1);assert.ok(document.querySelectorAll('.data-viewer').length>=2);const viewers=[...document.querySelectorAll('.data-viewer')];const proof=viewers.find(n=>n.querySelector('.data-viewer-title').textContent==='Certificate evidence');assert.ok(proof);assert.equal(proof.querySelector('[data-viewer-mode=formatted]').getAttribute('aria-pressed'),'true');assert.match(proof.textContent,/Unknown key/);mode(proof,'raw');assert.deepEqual(JSON.parse(proof.querySelector('.data-raw').textContent),fixture.data);assert.equal(document.querySelector('img'),null);dom.window.close();
});

const exactNumberTokens = ["9007199254740993", "-9007199254740993", "0.12345678901234567890123456789", "1.234567890123456789e+40", "1e-400", "-0"];
test('encoded JSON numbers preserve their source tokens in formatted nested objects and arrays',async()=>{
  const {dom,view}=setup();const original=' {"positive":'+exactNumberTokens[0]+',"negative":'+exactNumberTokens[1]+',"decimal":'+exactNumberTokens[2]+',"exponent":'+exactNumberTokens[3]+',"nested":[{"tiny":'+exactNumberTokens[4]+',"zero":'+exactNumberTokens[5]+'}],"numeric_string":"9007199254740993"} \n';
  const node=view(original);let pending;
  while((pending=[...node.querySelectorAll('.data-nested')].filter(n=>!n.open)).length){
    pending.forEach(n=>n.open=true);await until(()=>pending.every(n=>n.querySelector('.data-collection')));
  }
  assert.deepEqual([...node.querySelectorAll('.data-number')].map(n=>n.textContent),exactNumberTokens);
  assert.ok([...node.querySelectorAll('.data-string')].some(n=>n.textContent==='9007199254740993'));
  mode(node,'raw');assert.equal(JSON.parse(node.querySelector('.data-raw').textContent),original);dom.window.close();
});
test('source-context fallback retains literal JSON for unsafe integers decimals and exponent numbers',()=>{
  const {dom,view}=setup({withoutSourceContext:true});
  for(const token of exactNumberTokens){const original=' {"nested":[{"value":'+token+'}]} \n';const node=view(original);
    assert.equal(node.querySelector('.data-number'),null);assert.match(node.textContent,/original JSON text.*precision/i);
    assert.ok([...node.querySelectorAll('.data-text,.data-string')].some(n=>n.textContent===original));
    mode(node,'raw');assert.equal(JSON.parse(node.querySelector('.data-raw').textContent),original);
  }
  dom.window.close();
});
test('fallback ignores numeric-looking JSON strings including escaped quotes and still formats safe integers',()=>{
  const {dom,view}=setup({withoutSourceContext:true});const original=JSON.stringify({text:'9007199254740993 0.12345678901234567890 1e-400 \"and quotes\"',numeric_string:'-9007199254740993',safe_integer:42});
  const node=view(original);assert.deepEqual([...node.querySelectorAll('.data-number')].map(n=>n.textContent),['42']);assert.ok([...node.querySelectorAll('.data-string')].some(n=>n.textContent==='-9007199254740993'));assert.doesNotMatch(node.textContent,/original JSON text.*precision/i);mode(node,'raw');assert.equal(JSON.parse(node.querySelector('.data-raw').textContent),original);dom.window.close();
});
test('already parsed numeric values display exactly as supplied without inventing original lexemes',()=>{
  const {dom,view}=setup();const input={provided:9007199254740992,decimal:0.1};const node=view(input);assert.deepEqual([...node.querySelectorAll('.data-number')].map(n=>n.textContent),[String(input.provided),String(input.decimal)]);mode(node,'raw');assert.deepEqual(JSON.parse(node.querySelector('.data-raw').textContent),input);dom.window.close();
});
test('X.509 issuer and subject abbreviations receive friendly labels without changing raw evidence',async()=>{
  const {dom,view}=setup();const input={issuer:{C:'US',l:'Austin',O:'Example CA',ou:'Trust services',ST:'Texas'},subject:{c:'US',L:'Boston',o:'Example Research',OU:'Platform',st:'Massachusetts'},C:'ordinary field'};const node=view(input);
  const details=[...node.querySelectorAll('.data-nested')];details.forEach(n=>n.open=true);await until(()=>details.every(n=>n.querySelector('.data-fields')));
  for(const name of ['Country','Locality','Organization','Organizational unit','State / province'])assert.equal([...node.querySelectorAll('.data-key')].filter(n=>n.textContent===name).length,2);
  assert.ok([...node.querySelectorAll('.data-key')].some(n=>n.textContent==='C'));
  mode(node,'raw');assert.deepEqual(JSON.parse(node.querySelector('.data-raw').textContent),input);
  const issuer=view({C:'US',O:'Example CA'},{label:'Certificate issuer'});assert.match(issuer.textContent,/Country/);assert.match(issuer.textContent,/Organization/);dom.window.close();
});
