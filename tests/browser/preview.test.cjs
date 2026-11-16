const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../..');
const id = '11111111-1111-4111-8111-111111111111';
const otherId = '22222222-2222-4222-8222-222222222222';
const endpoint = {id,address:'192.0.2.7',port:5432,transport:'tcp',service_type:'postgres',fetch_actions:['check','tcp','identity'],default_actions:['identity']};
const catalog = {results:[{key:'postgres',label:'PostgreSQL',legacy:false,category:'database'},{key:'modbus',label:'Modbus',legacy:false,category:'ot'},{key:'dns',label:'DNS',legacy:false,category:'protocol'},{key:'elastic',label:'Elasticsearch',legacy:true}],tools:{curl:true,httpx:false,playwright:true}};
const tick = () => new Promise(r=>setTimeout(r,0));
async function setup({url='http://localhost/preview/',handler,app=false}={}) {
 const dom=new JSDOM('<meta name="csrf-token" content="fixture"><body data-page="preview"><main id="page-root"></main><div id="toast-root"></div><span id="breadcrumb-page"></span><button id="menu-toggle"></button><aside id="sidebar"></aside><button id="close-run-dialog"></button>',{url,runScripts:'outside-only'});
 const calls=[],panels=[],legacy=[];
 dom.window.fetch=async (url,opts={})=>{
  calls.push({url,opts,body:opts.body?JSON.parse(opts.body):null});
  const route=new URL(url,'http://localhost').pathname;
  if(handler){const result=await handler(url,opts);if(result!==undefined)return result;}
  const data=route==='/api/types'?{results:catalog.results}:route==='/api/operations/catalog'?catalog:route.endsWith('/fetch')?{runs:[{id:'real-run-42',state:'queued'}]}:route==='/api/operations/endpoints'?{results:[endpoint],page:1,pages:1,count:1}:route==='/api/operations/endpoints/'+otherId?{endpoint:{...endpoint,id:otherId,address:'192.0.2.99'}}:{results:[]};
  return {ok:true,json:async()=>data};
 };
 dom.window.LeakScopeRunLogs={panel:run=>{panels.push(run);const node=dom.window.document.createElement('div');node.className='run-log-fixture';node.textContent=run.id;return node;}};
 for(const name of app?['utils','preview','app']:['preview']){const file=path.join(root,'leakscope_app/static/js/leakscope/'+name+'.js');if(fs.existsSync(file))dom.window.eval(fs.readFileSync(file,'utf8'));}
 assert.ok(dom.window.LeakScopePreview,'modern Preview renderer is registered');
 if(!app)await dom.window.LeakScopePreview.render(dom.window.document.getElementById('page-root'),{legacy:async()=>{legacy.push(true);dom.window.document.getElementById('page-root').textContent='Legacy inspection fixture';}});
 await tick();return {dom,d:dom.window.document,calls,panels,legacy};
}
function change(dom,node,value){node.value=value;node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));}
const button=(d,text)=>[...d.querySelectorAll('button')].find(x=>x.textContent===text);
function selectTarget(env,value=id){change(env.dom,env.d.querySelector('[name=endpoint_id]'),value);}
function consent(env){env.d.querySelector('[name=active_probe]').checked=true;env.d.querySelector('[name=active_probe]').dispatchEvent(new env.dom.window.Event('change',{bubbles:true}));}
function submit(env){env.d.querySelector('[data-preview-form]').dispatchEvent(new env.dom.window.Event('submit',{bubbles:true,cancelable:true}));}

test('modern Preview includes every catalog service and browses saved UUID records without writes',async()=>{
 const e=await setup();try{
  assert.deepEqual([...e.d.querySelector('[name=technology]').options].map(x=>x.value),['','postgres','modbus','dns','elastic']);
  change(e.dom,e.d.querySelector('[name=technology]'),'modbus');await tick();
  assert.equal(new URL(e.calls.at(-1).url,'http://localhost').searchParams.get('technology'),'modbus');
  assert.match(e.d.querySelector('[name=endpoint_id]').textContent,/192.0.2.7:5432/);
  assert.ok(e.calls.every(x=>!x.opts.method||x.opts.method==='GET'));
  assert.match(e.d.body.textContent,/fingerprint|transcript/i);
 }finally{e.dom.window.close();}
});
test('UUID handoff outside the first page resolves its saved detail and remains passive',async()=>{
 const e=await setup({url:'http://localhost/preview/?technology=postgres&endpoint='+otherId});try{
  assert.equal(e.d.querySelector('[name=technology]').value,'postgres');
  assert.equal(e.d.querySelector('[name=endpoint_id]').value,otherId);
  assert.ok(e.calls.some(x=>x.url==='/api/operations/endpoints/'+otherId));
  assert.ok(e.calls.every(x=>x.opts.method==='GET'));
 }finally{e.dom.window.close();}
});
test('endpoint actions, consent and explicit submit produce one valid UUID capture and real run proof',async()=>{
 const e=await setup();try{
  selectTarget(e);
  const actions=[...e.d.querySelectorAll('[name=actions]')];
  assert.deepEqual(actions.filter(x=>!x.disabled).map(x=>x.value),['check','tcp','identity']);
  assert.equal(e.d.querySelector('[name=active_probe]').checked,false);
  submit(e);await tick();assert.equal(e.calls.filter(x=>x.opts.method==='POST').length,0);
  consent(e);submit(e);await tick();
  const writes=e.calls.filter(x=>x.opts.method==='POST');assert.equal(writes.length,1);
  assert.equal(writes[0].url,'/api/operations/endpoints/'+id+'/fetch');assert.deepEqual(writes[0].body,{actions:['identity']});
  assert.equal(writes[0].opts.headers['X-CSRFToken'],'fixture');assert.match(e.d.body.textContent,/real-run-42/);assert.equal(e.panels[0].id,'real-run-42');
  assert.equal(e.d.querySelector('[name=active_probe]').checked,false);
 }finally{e.dom.window.close();}
});
test('UDP remains available when advertised; tool absence and missing capabilities disable unsupported actions',async()=>{
 const e=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?{ok:true,json:async()=>({results:[{...endpoint,transport:'udp',fetch_actions:['udp','httpx'],default_actions:['udp']}],count:1,page:1,pages:1})}:undefined});try{
  selectTarget(e);assert.equal(e.d.querySelector('[name=actions][value=udp]').disabled,false);assert.equal(e.d.querySelector('[name=actions][value=httpx]').disabled,true);
  assert.equal(e.d.querySelector('[name=actions][value=tcp]').disabled,true);
 }finally{e.dom.window.close();}
});
test('switching target or technology clears consent and stale action selection',async()=>{
 const e=await setup();try{selectTarget(e);consent(e);change(e.dom,e.d.querySelector('[name=technology]'),'dns');await tick();assert.equal(e.d.querySelector('[name=active_probe]').checked,false);assert.ok(button(e.d,'Capture selected evidence').disabled);}finally{e.dom.window.close();}
});
test('overlapping technology requests abort the old request and reject stale results',async()=>{
 const pending=[];let defer=false;
 const e=await setup({handler:async(url,opts)=>defer&&new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?new Promise(resolve=>pending.push({resolve,opts})):undefined});try{
  defer=true;change(e.dom,e.d.querySelector('[name=technology]'),'postgres');change(e.dom,e.d.querySelector('[name=technology]'),'modbus');await tick();
  assert.equal(pending.length,2);assert.equal(pending[0].opts.signal.aborted,true);
  pending[1].resolve({ok:true,json:async()=>({results:[{...endpoint,address:'new-result'}],count:1,page:1,pages:1})});await tick();
  pending[0].resolve({ok:true,json:async()=>({results:[{...endpoint,address:'stale-result'}],count:1,page:1,pages:1})});await tick();
  assert.match(e.d.querySelector('[name=endpoint_id]').textContent,/new-result/);assert.doesNotMatch(e.d.body.textContent,/stale-result/);
 }finally{e.dom.window.close();}
});
test('empty and failed saved endpoint loads keep capture disabled and allow retry',async()=>{
 let fail=false;
 const e=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?{ok:!fail,json:async()=>fail?{error:'Fixture offline'}:{results:[],count:0,page:1,pages:1}}:undefined});try{
  assert.match(e.d.body.textContent,/No saved endpoints/);assert.ok(button(e.d,'Capture selected evidence').disabled);
  fail=true;change(e.dom,e.d.querySelector('[name=technology]'),'dns');await tick();assert.match(e.d.body.textContent,/Fixture offline/);assert.ok(button(e.d,'Retry saved endpoints'));
 }finally{e.dom.window.close();}
});
test('service search is local and saved endpoint paging carries technology and search',async()=>{
 const e=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?{ok:true,json:async()=>({results:[endpoint],count:40,page:Number(new URL(url,'http://localhost').searchParams.get('page'))||1,pages:2})}:undefined});try{
  const before=e.calls.length;const search=e.d.querySelector('[name=service_search]');search.value='modbus';search.dispatchEvent(new e.dom.window.Event('input'));assert.equal(e.calls.length,before);assert.match(e.d.querySelector('[name=technology]').textContent,/Modbus/);assert.doesNotMatch(e.d.querySelector('[name=technology]').textContent,/PostgreSQL/);
  change(e.dom,e.d.querySelector('[name=technology]'),'modbus');await tick();const q=e.d.querySelector('[name=q]');q.value='saved host';q.closest('form').dispatchEvent(new e.dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();button(e.d,'Next endpoints').click();await tick();const p=new URL(e.calls.at(-1).url,'http://localhost').searchParams;assert.equal(p.get('page'),'2');assert.equal(p.get('technology'),'modbus');assert.equal(p.get('q'),'saved host');
 }finally{e.dom.window.close();}
});
test('legacy source is explicit and switching back restores modern saved endpoint browsing',async()=>{
 const e=await setup();try{change(e.dom,e.d.querySelector('[name=preview_source]'),'legacy');await tick();assert.equal(e.legacy.length,1);assert.match(e.d.body.textContent,/Legacy inspection fixture/);change(e.dom,e.d.querySelector('[name=preview_source]'),'observations');await tick();assert.ok(e.d.querySelector('[name=endpoint_id]'));}finally{e.dom.window.close();}
});
test('app routes Preview into the modern adapter and preserves explicit legacy initialization',async()=>{
 for(const query of ['', '?source=legacy&type=elastic&target_id=1']){const e=await setup({app:true,url:'http://localhost/preview/'+query});try{assert.ok(e.d.querySelector('[name=preview_source]'));assert.ok(e.d.querySelector(query?'[name=target_id]':'[name=endpoint_id]'));assert.equal(e.calls.some(x=>x.url.startsWith('/api/findings')),!!query);}finally{e.dom.window.close();}}
});

test('UDP capture requires a reviewed bounded datagram and sends only its explicit options',async()=>{
 const e=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?{ok:true,json:async()=>({results:[{...endpoint,transport:'udp',service_type:'dns',fetch_actions:['udp'],default_actions:[]}],count:1,page:1,pages:1})}:undefined});try{
  selectTarget(e);const udp=e.d.querySelector('[name=actions][value=udp]');udp.checked=true;udp.dispatchEvent(new e.dom.window.Event('change',{bubbles:true}));consent(e);submit(e);await tick();assert.equal(e.calls.filter(x=>x.opts.method==='POST').length,0);assert.match(e.d.body.textContent,/nonempty.*payload/i);
  const payload=e.d.querySelector('[name=udp_payload]');assert.ok(payload);payload.value='hello';consent(e);submit(e);await tick();const write=e.calls.find(x=>x.opts.method==='POST');assert.deepEqual(write.body,{actions:['udp'],options:{udp:{payload:'hello',encoding:'text',timeout_seconds:3}}});
 }finally{e.dom.window.close();}
});
test('saved technology presets filter their real base service and untrusted labels remain text',async()=>{
 const e=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/catalog'?{ok:true,json:async()=>({...catalog,results:[...catalog.results,{key:'saved-profile',label:'<img src=x onerror=alert(1)>',base_type:'postgres'}]})}:undefined});try{
  change(e.dom,e.d.querySelector('[name=technology]'),'saved-profile');await tick();assert.equal(new URL(e.calls.at(-1).url,'http://localhost').searchParams.get('technology'),'postgres');assert.equal(e.d.querySelectorAll('img[onerror]').length,0);
 }finally{e.dom.window.close();}
});
test('Preview delegates real accepted runs to the shared progress watcher and aborts on source switch',async()=>{
 const e=await setup();try{
  const watched=[];e.dom.window.LeakScopeWorkbench={watchRun:(run,holder,done,options)=>watched.push({run,holder,options})};selectTarget(e);consent(e);submit(e);await tick();assert.equal(watched.length,1);assert.equal(watched[0].run.id,'real-run-42');assert.ok(watched[0].holder.isConnected);assert.equal(watched[0].options.signal.aborted,false);change(e.dom,e.d.querySelector('[name=preview_source]'),'legacy');await tick();assert.equal(watched[0].options.signal.aborted,true);
 }finally{e.dom.window.close();}
});
test('older numeric finding links preserve legacy Preview while UUID target_id handoffs use Observations',async()=>{
 const legacy=await setup({app:true,url:'http://localhost/preview/?type=elastic&target_id=1'});try{assert.equal(legacy.d.querySelector('[name=preview_source]').value,'legacy');assert.ok(legacy.d.querySelector('[name=target_id]'));}finally{legacy.dom.window.close();}
 const modern=await setup({url:'http://localhost/preview/?type=postgres&target_id='+otherId});try{assert.equal(modern.d.querySelector('[name=preview_source]').value,'observations');assert.equal(modern.d.querySelector('[name=endpoint_id]').value,otherId);}finally{modern.dom.window.close();}
});
test('missing endpoint capabilities stay unavailable and failed submissions cannot claim a queued run',async()=>{
 const e=await setup({handler:async url=>url.endsWith('/fetch')?{ok:false,json:async()=>({error:'Fixture capture rejected'})}:undefined});try{
  selectTarget(e);consent(e);submit(e);await tick();assert.match(e.d.body.textContent,/Fixture capture rejected/);assert.equal(e.d.querySelectorAll('[data-preview-run]').length,0);assert.equal(e.d.querySelector('[name=active_probe]').checked,false);
 }finally{e.dom.window.close();}
 const noCapabilities=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?{ok:true,json:async()=>({results:[{...endpoint,fetch_actions:undefined}],count:1,page:1,pages:1})}:undefined});try{selectTarget(noCapabilities);assert.equal(noCapabilities.d.querySelectorAll('[name=actions]:enabled').length,0);}finally{noCapabilities.dom.window.close();}
});
test('UDP hex payloads submit the validated bytes and reject malformed or oversized payloads locally',async()=>{
 const e=await setup({handler:async url=>new URL(url,'http://localhost').pathname==='/api/operations/endpoints'?{ok:true,json:async()=>({results:[{...endpoint,transport:'udp',service_type:'dns',fetch_actions:['udp'],default_actions:['udp']}],count:1,page:1,pages:1})}:undefined});try{
  selectTarget(e);const payload=e.d.querySelector('[name=udp_payload]');change(e.dom,e.d.querySelector('[name=udp_encoding]'),'hex');
  for(const invalid of ['f','zz','ff'.repeat(4097)]){payload.value=invalid;consent(e);submit(e);await tick();assert.equal(e.calls.filter(x=>x.opts.method==='POST').length,0);}
  payload.value='f f';consent(e);submit(e);await tick();assert.equal(e.calls.find(x=>x.opts.method==='POST').body.options.udp.payload,'ff');
 }finally{e.dom.window.close();}
});
