const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');const {JSDOM}=require('jsdom');
const base=path.resolve(__dirname,'../../leakscope_app/static/js/leakscope');
const data={endpoint:{id:'ep',address:'192.0.2.1',port:9443,transport:'tcp',service_type:'https',title:'Fixture'},observations:[{id:'b',provider:'shodan',observed_at:'2025-01-02T00:00:00Z',last_collected_at:'2026-11-09T00:00:00Z',data:{http:{title:'New'}}},{id:'a',provider:'shodan',observed_at:'2025-01-01T00:00:00Z',last_collected_at:'2026-11-08T00:00:00Z',data:{http:{title:'Old'}}},{id:'c',provider:'zoomeye',observed_at:null,last_collected_at:'2026-11-09T00:00:00Z',data:{}}],artifacts:[{id:'img1',kind:'screenshot',created_at:'2026-11-08T00:00:00Z',url:'/api/operations/artifacts/img1'},{id:'img2',kind:'screenshot',created_at:'2026-11-09T00:00:00Z',url:'javascript:alert(1)'}]};
function setup(routes={}){const dom=new JSDOM('<main><button id="origin">Inspect</button><input value="filter stays"></main>',{url:'http://localhost/assets/?q=fixture',runScripts:'outside-only'}),calls=[];dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new dom.window.Event('close'))};dom.window.fetch=async(url,opts={})=>{calls.push({url,opts});let value=Object.entries(routes).find(([key])=>url.startsWith(key))?.[1]??data;if(typeof value==='function')value=value();return {ok:true,json:async()=>await value}};for(const name of ['data_viewer.js','inspector.js']){const f=path.join(base,name);if(fs.existsSync(f))dom.window.eval(fs.readFileSync(f,'utf8'))}assert.ok(dom.window.LeakScopeInspector?.open,'shared inspector exports open');return {dom,calls,api:dom.window.LeakScopeInspector,doc:dom.window.document}}
const settle=()=>new Promise(r=>setTimeout(r,20));const tab=(d,name)=>[...d.querySelectorAll('[role=tab]')].find(n=>n.textContent===name);const button=(d,name)=>[...d.querySelectorAll('button')].find(n=>n.textContent===name);
test('opening inspector preserves list state and makes only a saved-data GET',async()=>{const {dom,api,doc,calls}=setup();doc.querySelector('#origin').focus();await api.open({type:'endpoint',id:'ep'});assert.equal(doc.querySelector('input').value,'filter stays');assert.equal(dom.window.location.search,'?q=fixture');assert.deepEqual(calls.map(c=>c.url),['/api/operations/endpoints/ep']);assert.ok(calls.every(c=>!c.opts.method||c.opts.method==='GET'));assert.equal(doc.querySelectorAll('[role=tab]').length,5);button(doc,'Close inspector').click();assert.equal(doc.querySelector('dialog'),null);assert.equal(doc.activeElement.id,'origin');dom.window.close()});
test('unknown scan time never borrows recent collection date',async()=>{const {dom,api,doc}=setup();await api.open({type:'endpoint',id:'ep'});tab(doc,'Evidence').click();assert.match(doc.body.textContent,/Provider reported/);assert.match(doc.body.textContent,/Source observed.*Unknown/s);assert.match(doc.body.textContent,/Collected/);assert.match(doc.body.textContent,/2025/);assert.equal(doc.querySelectorAll('img').length,0);dom.window.close()});
test('history compares two same-provider saved records only on explicit click',async()=>{const {dom,api,doc,calls}=setup({'/api/investigation/':{before:data.observations[1],after:data.observations[0],changes:[{path:'http.title',kind:'changed',before:'Old',after:'New'}],count:1,truncated:false,basis:'Saved provider records'}});await api.open({type:'endpoint',id:'ep'});tab(doc,'History').click();assert.equal(calls.length,1);const before=doc.querySelector('[name=compare_before]'),after=doc.querySelector('[name=compare_after]');assert.ok(before&&after);assert.deepEqual([...before.options].map(x=>x.value).sort(),['a','b']);button(doc,'Compare saved records').click();await settle();assert.ok(calls[1].url.startsWith('/api/investigation/endpoints/ep/compare?'));assert.match(doc.querySelector('.inspector-comparison').textContent,/http.title/);assert.match(doc.querySelector('.inspector-comparison').textContent,/Old/);assert.match(doc.querySelector('.inspector-comparison').textContent,/New/);dom.window.close()});
test('captures load images only in opened sections and refuse unsafe artifact URLs',async()=>{const {dom,api,doc}=setup();await api.open({type:'endpoint',id:'ep'});tab(doc,'Captures').click();assert.equal(doc.querySelectorAll('img').length,0);const details=doc.querySelector('.inspector-artifact');details.open=true;details.dispatchEvent(new dom.window.Event('toggle'));assert.equal(doc.querySelectorAll('img').length,1);assert.equal(doc.querySelector('img').getAttribute('src'),'/api/operations/artifacts/img1');assert.equal(doc.querySelector('a[href^="javascript:"]'),null);dom.window.close()});
test('closing before a saved-data response arrives ignores it and aborts request',async()=>{let resolve;const promise=new Promise(r=>resolve=r);const {dom,api,doc,calls}=setup({'/api/operations/':()=>promise});const opening=api.open({type:'endpoint',id:'ep'});await settle();button(doc,'Close inspector').click();assert.equal(calls[0].opts.signal.aborted,true);resolve(data);await opening;assert.equal(doc.querySelector('dialog'),null);dom.window.close()});
test('malicious fields are literal text and keyboard tabs move with selection',async()=>{const {dom,api,doc}=setup({'/api/findings/':{finding:{type:'jenkins',id:42,label:'<img src=x>',provider:'shodan'},fields:{title:'<script>bad()</script>'}}});await api.open({type:'jenkins',id:42});assert.equal(doc.querySelector('img,script'),null);const first=tab(doc,'Summary');first.focus();first.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.equal(tab(doc,'Evidence').getAttribute('aria-selected'),'true');assert.equal(doc.activeElement,tab(doc,'Evidence'));assert.match(doc.body.textContent,/<script>/);tab(doc,'History').click();assert.match(doc.body.textContent,/No.*history|not.*retained/i);dom.window.close()});
test('local candidate adapter opens saved evidence with no network or invented snapshots',async()=>{const {dom,api,doc,calls}=setup();await api.open({type:'candidate',id:'c1',expansion_id:'e1',record:{domain:'api.example.com',confidence:60,relation:'related',evidence:[{source:'crtsh',excerpt:'Certificate name'}]}});assert.equal(calls.length,0);assert.match(doc.body.textContent,/api.example.com/);tab(doc,'Evidence').click();assert.match(doc.body.textContent,/Certificate name/);tab(doc,'History').click();assert.match(doc.body.textContent,/No.*history|not.*retained/i);dom.window.close()});
test('actions remain explicit and open does not invoke enrichment or collection',async()=>{const {dom,api,doc}=setup();let used=0;dom.window.LeakScopeAssets={openTools:()=>used++};await api.open({type:'endpoint',id:'ep'});tab(doc,'Actions').click();assert.equal(used,0);button(doc,'Names, DNS & netblock').click();assert.equal(used,1);dom.window.close()});

test('freshness separates unknown source time from top-level collection and labels age explicitly',()=>{const {dom,api,doc}=setup();const node=api.provenance({source:'dns',observed_at:null,collected_at:'2026-11-09T00:00:00Z'});doc.body.append(node);assert.match(node.textContent,/Collected/);assert.equal(node.querySelectorAll('time[datetime="2026-11-09T00:00:00Z"]').length,1);assert.match(node.textContent,/Source age: Unknown/);dom.window.close()});
test('latest deduplicated source report remains distinct from initial source observed time',()=>{const {dom,api,doc}=setup();const node=api.provenance({provider:'shodan',observed_at:'2025-01-01T00:00:00Z',last_observed_at:'2026-11-08T00:00:00Z',last_collected_at:'2026-11-09T00:00:00Z'});doc.body.append(node);assert.match(node.textContent,/Latest source report/);assert.equal(node.querySelectorAll('time').length,3);dom.window.close()});

test('saved evidence reference fetches only its retained proof and links to its subject', async () => {
 const subject='11111111-2222-4333-8444-555555555555';
 const {dom,api,doc,calls}=setup({'/api/assets/evidence/proof-1':{evidence:{id:'proof-1',subject,source:'public_identity:android',observed_at:'2026-11-01T00:00:00Z',last_collected_at:'2026-11-02T00:00:00Z',expires_at:'2026-11-03T00:00:00Z',data:{namespace:'android.app',value:'com.example.mobile'}}}});
 try {
  await api.open({type:'evidence',id:'proof-1'});
  assert.deepEqual(calls.map(call=>call.url),['/api/assets/evidence/proof-1']);
  assert.ok(calls.every(call=>call.opts.method==='GET'));
  assert.equal(doc.querySelector('.investigation-heading h2').textContent,'Saved evidence');
  assert.match(doc.querySelector('.inspector-subtitle').textContent,/public identity.*android/i);
  const facts=doc.querySelector('.inspector-facts');
  for(const label of ['Source','Observed','Last collected','Evidence expires'])assert.ok([...facts.querySelectorAll('dt')].some(node=>node.textContent===label));
  assert.equal(doc.querySelector('.inspector-section a').getAttribute('href'),'/relationships/?root='+subject);
 } finally {dom.window.close();}
});

test('saved evidence reference uses the formatted and raw viewer with literal untrusted data', async () => {
 const payload={namespace:'android.app',value:'<img src=x onerror="window.bad=1">',nested:{html:'<script>window.bad=1</script>'}};
 const {dom,api,doc}=setup({'/api/assets/evidence/proof-2':{evidence:{id:'proof-2',subject:'subject-2',source:'dns',data:payload}}});
 try {
  await api.open({type:'evidence',id:'proof-2'});
  tab(doc,'Evidence').click();
  const viewer=doc.querySelector('.data-viewer');
  assert.ok(viewer);
  assert.equal(viewer.querySelector('[data-viewer-mode=formatted]').getAttribute('aria-pressed'),'true');
  assert.match(viewer.textContent,/<img/);
  assert.equal(doc.querySelector('img,script'),null);
  viewer.querySelector('[data-viewer-mode=raw]').click();
  assert.equal(JSON.parse(viewer.querySelector('.data-raw').textContent).value,payload.value);
  assert.equal(viewer.querySelector('[data-viewer-mode=raw]').getAttribute('aria-pressed'),'true');
  assert.equal(doc.querySelector('img,script'),null);
 } finally {dom.window.close();}
});

test('saved evidence reference omits actions unsupported by evidence APIs', async () => {
 const {dom,api,doc,calls}=setup({'/api/assets/evidence/proof-3':{evidence:{id:'proof-3',subject:'subject-3',source:'dns',data:{type:'A'}}}});
 let invoked=0;
 dom.window.LeakScopeAssets={addToOrganization:()=>invoked++,openTools:()=>invoked++};
 dom.window.LeakScopeIntelligence={openSimilar:()=>invoked++,openAsk:()=>invoked++};
 dom.window.LeakScopePresets={openCreate:()=>invoked++};
 try {
  await api.open({type:'evidence',id:'proof-3'});
  tab(doc,'Actions').click();
  for(const label of ['Add to organization','Names, DNS & netblock','Find similar','Ask','Create discovery preset'])assert.equal(button(doc,label),undefined);
  assert.equal(invoked,0);
  assert.equal(calls.length,1);
  assert.equal(doc.querySelector('.inspector-section a').getAttribute('href'),'/relationships/?root=subject-3');
 } finally {dom.window.close();}
});
