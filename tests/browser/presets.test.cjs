const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const source=path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/presets.js');
const refs=[{type:'endpoint',id:'ep-1'}];
const fields=[{id:'vendor-id',field:'modbus.vendor',label:'Modbus vendor',value:'Delta Electronics',source:'endpoint:ep-1',provenance:{provider:'fetch:ot',observation_id:'obs-1'}},{id:'model-id',field:'modbus.product',label:'Modbus product',value:'AS228T-A',source:'endpoint:ep-1'},{id:'ip-id',field:'ip',label:'IP',value:'192.0.2.8',source:'endpoint:ep-1'}];
const queries={shodan:{query:'shodan.module:modbus "Delta Electronics" "AS228T-A"',supported:true},zoomeye:{query:'service="modbus" && banner="AS228T-A"',supported:true},netlas:{query:'',supported:false,warnings:['No verified native field mapping']}};
const preview={fields,selected_fields:['vendor-id','model-id'],queries,service_type:'modbus',suggested_name:'Delta AS228T-A',scope:{network:'192.0.2.0/24',country:'US',exclude:'example'},scope_conflicts:[],references:refs,warnings:[]};
const preset={id:'p-1',key:'preset:p-1',name:'Delta AS228T-A',label:'Delta AS228T-A',service_type:'modbus',queries,scope:preview.scope,description:'Reviewed model',references:refs};
function setup(routes={}){
 const dom=new JSDOM('<meta name="csrf-token" content="fixture-csrf"><main id="root"></main>',{url:'http://localhost/assets/',runScripts:'outside-only'});const calls=[];
 dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new dom.window.Event('close'));};
 dom.window.fetch=async(url,opts={})=>{const call={url,opts,body:opts.body?JSON.parse(opts.body):undefined};calls.push(call);let result=routes[url]??(url.endsWith('/preview')?preview:opts.method==='POST'?{preset,created:true}:{results:[preset]});if(typeof result==='function')result=await result(call);return {ok:result?.ok!==false,json:async()=>result?.payload??result};};
 if(fs.existsSync(source))dom.window.eval(fs.readFileSync(source,'utf8'));
 return {dom,calls,api:dom.window.LeakScopePresets,d:dom.window.document};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,15));
const button=(d,text)=>[...d.querySelectorAll('button')].find(b=>b.textContent===text);
function change(dom,selector,value,type='input') {const input=dom.window.document.querySelector(selector);if(input.type==='checkbox')input.checked=value;else input.value=value;input.dispatchEvent(new dom.window.Event(type,{bubbles:true}));return input;}

test('preset creator previews copied references without searching or saving',async()=>{
 const {dom,calls,api,d}=setup();assert.ok(api);const input=[{...refs[0]}];api.openCreate(input);input[0].id='changed';await settle();assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/discovery-presets/preview');assert.deepEqual(calls[0].body,{references:refs});assert.equal(calls[0].opts.headers['X-CSRFToken'],'fixture-csrf');assert.equal(calls[0].opts.credentials,'same-origin');assert.equal(d.querySelector('[name=name]').value,'Delta AS228T-A');assert.equal(d.querySelector('[name=evidence][value=ip-id]').checked,false);assert.equal(d.querySelector('[name=evidence][value=model-id]').checked,true);assert.equal(button(d,'Save discovery preset').disabled,false);dom.window.close();
});
test('preset fields and provider query previews are rendered as text',async()=>{
 const evil='<img src=x onerror=alert(1)>';const {dom,api,d}=setup({'/api/discovery-presets/preview':{...preview,fields:[{...fields[0],value:evil}],selected_fields:['vendor-id'],queries:{shodan:{supported:true,query:evil}}}});assert.ok(api);await api.openCreate(refs);assert.equal(d.querySelector('img,script'),null);assert.match(d.body.textContent,/onerror=alert/);assert.match(d.body.textContent,/Netlas/);dom.window.close();
});
test('saving sends selected server field ids and reviewed scope, not client queries or values',async()=>{
 const {dom,calls,api,d}=setup();assert.ok(api);await api.openCreate(refs);change(dom,'[name=name]','Controller family');change(dom,'[name=description]','Reviewed evidence');button(d,'Save discovery preset').click();await settle();const save=calls.find(c=>c.url==='/api/discovery-presets');assert.deepEqual(save.body,{references:refs,selected_fields:['vendor-id','model-id'],mode:'all',scope:preview.scope,scope_override:false,name:'Controller family',description:'Reviewed evidence'});assert.equal(Object.hasOwn(save.body,'queries'),false);assert.equal(Object.hasOwn(save.body,'signals'),false);assert.equal(calls.length,2);assert.ok(d.querySelector('a[href="/discover/?service_type=preset%3Ap-1&query_mode=fingerprint"]'));assert.match(d.body.textContent,/saved/i);assert.doesNotMatch(d.body.textContent,/null/);dom.window.close();
});
test('changing selected identity invalidates preview until regenerated',async()=>{
 const {dom,calls,api,d}=setup({'/api/discovery-presets/preview':call=>({...preview,selected_fields:call.body.selected_fields??preview.selected_fields})});assert.ok(api);await api.openCreate(refs);change(dom,'[name=evidence][value=model-id]',false,'change');assert.equal(button(d,'Save discovery preset').disabled,true);assert.match(d.body.textContent,/preview.*again/i);button(d,'Preview provider queries').click();await settle();assert.deepEqual(calls.at(-1).body.selected_fields,['vendor-id']);assert.equal(button(d,'Save discovery preset').disabled,false);dom.window.close();
});
test('scope edits are explicit and require a fresh provider preview',async()=>{
 const {dom,calls,api,d}=setup({'/api/discovery-presets/preview':call=>({...preview,scope:call.body.scope??preview.scope})});assert.ok(api);await api.openCreate(refs);change(dom,'[name=network]','');assert.equal(button(d,'Save discovery preset').disabled,true);button(d,'Preview provider queries').click();await settle();assert.equal(calls.at(-1).body.scope_override,true);assert.equal(calls.at(-1).body.scope.network,'');button(d,'Save discovery preset').click();await settle();const save=calls.find(c=>c.url==='/api/discovery-presets');assert.equal(save.body.scope.network,'');assert.equal(save.body.scope.country,'US');assert.equal(save.body.scope_override,true);dom.window.close();
});
test('conflicting originating scopes must be explicitly resolved before saving',async()=>{
 const {dom,calls,api,d}=setup({'/api/discovery-presets/preview':call=>({...preview,scope:{network:'',country:'',exclude:''},scope_conflicts:call.body.scope_override?[]:['network','country']})});assert.ok(api);await api.openCreate(refs);assert.equal(button(d,'Save discovery preset').disabled,true);assert.equal(button(d,'Preview provider queries').disabled,true);change(dom,'[name=network]','');assert.equal(button(d,'Preview provider queries').disabled,true);change(dom,'[name=country]','US');assert.equal(button(d,'Preview provider queries').disabled,false);button(d,'Preview provider queries').click();await settle();assert.equal(button(d,'Save discovery preset').disabled,false);assert.equal(calls.length,2);dom.window.close();
});
test('clearing all selected signals never requests an empty query or enables saving',async()=>{
 const {dom,calls,api,d}=setup();assert.ok(api);await api.openCreate(refs);change(dom,'[name=evidence][value=vendor-id]',false,'change');change(dom,'[name=evidence][value=model-id]',false,'change');assert.equal(button(d,'Save discovery preset').disabled,true);assert.equal(button(d,'Preview provider queries').disabled,true);assert.equal(calls.length,1);dom.window.close();
});
test('unsupported provider mappings stay visible and cannot save when all are unavailable',async()=>{
 const {dom,api,d}=setup({'/api/discovery-presets/preview':{...preview,queries:{shodan:{supported:false,query:'',warnings:['Selected field has no equivalent']}}}});assert.ok(api);await api.openCreate(refs);assert.match(d.body.textContent,/Selected field has no equivalent/);assert.match(d.body.textContent,/unavailable/i);assert.equal(button(d,'Save discovery preset').disabled,true);dom.window.close();
});
test('a preview response cannot enable saving after options changed during its request',async()=>{
 let resolve;let count=0;const {dom,api,d}=setup({'/api/discovery-presets/preview':call=>{if(++count===1)return preview;return new Promise(r=>resolve=()=>r({...preview,selected_fields:call.body.selected_fields}));}});assert.ok(api);await api.openCreate(refs);change(dom,'[name=evidence][value=model-id]',false,'change');button(d,'Preview provider queries').click();await settle();change(dom,'[name=country]','GB');resolve();await settle();assert.equal(button(d,'Save discovery preset').disabled,true);assert.match(d.body.textContent,/preview.*again/i);dom.window.close();
});
test('closing aborts preview requests and ignores late responses',async()=>{
 let resolve;const {dom,calls,api,d}=setup({'/api/discovery-presets/preview':()=>new Promise(r=>resolve=r)});assert.ok(api);api.openCreate(refs);await settle();button(d,'Close').click();assert.equal(calls[0].opts.signal.aborted,true);resolve(preview);await settle();assert.equal(d.querySelector('dialog'),null);dom.window.close();
});
test('save cannot be double submitted and duplicate records get an existing-preset handoff',async()=>{
 let resolve;const {dom,calls,api,d}=setup({'/api/discovery-presets':()=>new Promise(r=>resolve=r)});assert.ok(api);await api.openCreate(refs);const save=button(d,'Save discovery preset');save.click();save.click();await settle();assert.equal(calls.filter(c=>c.url==='/api/discovery-presets').length,1);resolve({preset,created:false});await settle();assert.match(d.body.textContent,/existing.*preset/i);assert.equal(save.disabled,true);dom.window.close();
});
test('save failures remain visible without running discovery',async()=>{
 const {dom,calls,api,d}=setup({'/api/discovery-presets':{ok:false,payload:{error:'Evidence changed; reopen result'}}});assert.ok(api);await api.openCreate(refs);button(d,'Save discovery preset').click();await settle();assert.match(d.body.textContent,/Evidence changed/);assert.equal(button(d,'Save discovery preset').disabled,false);assert.equal(calls.length,2);dom.window.close();
});
test('manager reads saved presets and shows stored scope without starting searches',async()=>{
 const {dom,calls,api,d}=setup();assert.ok(api);await api.openManager();assert.equal(calls.length,1);assert.equal(calls[0].opts.method,'GET');assert.match(d.body.textContent,/192\.0\.2\.0\/24/);assert.match(d.body.textContent,/US/);assert.ok(d.querySelector('a[href="/discover/?service_type=preset%3Ap-1&query_mode=fingerprint"]'));assert.equal(d.querySelector('script,img'),null);dom.window.close();
});
test('manager deletion is an explicit one-preset action and refreshes the saved list',async()=>{
 let removed=false;const {dom,calls,api,d}=setup({'/api/discovery-presets':()=>({results:removed?[]:[preset]}),'/api/discovery-presets/p-1/delete':()=>{removed=true;return {deleted:true};}});assert.ok(api);await api.openManager();button(d,'Delete preset').click();await settle();assert.equal(calls.filter(c=>c.opts.method==='POST').length,1);assert.equal(calls.find(c=>c.opts.method==='POST').url,'/api/discovery-presets/p-1/delete');assert.match(d.body.textContent,/No saved discovery presets/);dom.window.close();
});
test('creator handles initial errors without showing a save form',async()=>{
 const {dom,api,d}=setup({'/api/discovery-presets/preview':{ok:false,payload:{error:'Result hidden or unavailable'}}});assert.ok(api);await api.openCreate(refs);assert.match(d.body.textContent,/Result hidden or unavailable/);assert.equal(button(d,'Save discovery preset'),undefined);dom.window.close();
});

async function integrationPage(page){
 const appRoot=path.resolve(__dirname,'../..');
 let html=fs.readFileSync(path.join(appRoot,'leakscope_app/templates/workbench/app.html'),'utf8').replaceAll('{% load static %}','').replace(/\{% static '([^']+)' %\}/g,'/static/$1').replace('{{ csrf_token }}','fixture').replace("{{ page|default:'overview' }}",page==='endpoint'?'assets':page).replace("{{ type|default:'' }}",'elastic').replace("{{ target_id|default:'' }}",'1');
 const modern=page==='endpoint'||page==='assets';
 const dom=new JSDOM(html,{url:'http://localhost/'+(modern?'assets/':page+'/')+(page==='endpoint'?'?endpoint=ep-1':''),runScripts:'outside-only'});
 const opened=[],calls=[];dom.window.LeakScopePresets={openCreate:refs=>opened.push(JSON.parse(JSON.stringify(refs))),openManager:()=>opened.push('manager')};
 const endpoint={id:'ep-1',address:'192.0.2.8',port:502,transport:'tcp',scheme:'',service_type:'modbus',title:'Controller',tags:[],fetch_actions:['identity'],default_actions:['identity']};
 const finding={id:1,type:'elastic',label:'Fixture finding',ip:'192.0.2.7',port:'9200',provider:'shodan',status:'new'};
 const routes={'/api/types':{results:[{key:'elastic',label:'Elasticsearch',discovery:true,legacy:true}]},'/api/summary':{providers:{shodan:{configured:true},zoomeye:{configured:true},netlas:{configured:true}}},'/api/findings':{results:[finding],count:1,page:1,pages:1},'/api/findings/elastic/1':{finding,fields:{name:'Fixture'},capabilities:{}},'/api/operations/catalog':{results:[{key:'modbus',label:'Modbus',transport:'tcp',fetch_actions:['identity'],default_actions:['identity']}],tools:{},providers:{}},'/api/operations/endpoints':{results:[endpoint],count:1,page:1,pages:1},'/api/operations/endpoints/ep-1':{endpoint,observations:[],artifacts:[]},'/api/query-preview':{queries:[]}};
 dom.window.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>routes[new URL(url,'http://localhost').pathname]||{results:[]}};};
 dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
 dom.window.eval(fs.readFileSync(path.join(appRoot,'leakscope_app/static/js/leakscope/utils.js'),'utf8'));
 if(modern){dom.window.eval(fs.readFileSync(require('node:path').resolve(__dirname,'../../leakscope_app/static/js/leakscope/list_toolbar.js'),'utf8'));dom.window.eval(fs.readFileSync(path.join(appRoot,'leakscope_app/static/js/leakscope/operations.js'),'utf8'));await dom.window.LeakScopeOperations.render(dom.window.document.querySelector('#page-root'),'assets');}
 else {dom.window.eval(fs.readFileSync(path.join(appRoot,'leakscope_app/static/js/leakscope/app.js'),'utf8'));await settle();}
 return {dom,opened,calls,d:dom.window.document};
}

test('endpoint row and detail preset buttons preserve the saved endpoint reference',async()=>{
 for(const page of ['assets','endpoint']){const {dom,opened,calls,d}=await integrationPage(page);const create=button(d,'Create discovery preset');assert.ok(create,page);assert.equal(opened.length,0);create.click();assert.deepEqual(opened,[[{type:'endpoint',id:'ep-1'}]]);assert.ok(calls.every(c=>!c.options.method||c.options.method==='GET'));dom.window.close();}
});
test('legacy result row and detail preset buttons preserve their actual service reference',async()=>{
 for(const page of ['findings','detail']){const {dom,opened,d}=await integrationPage(page);const create=button(d,'Create discovery preset');assert.ok(create,page);assert.equal(opened.length,0);create.click();assert.deepEqual(opened,[[{type:'elastic',id:page==='detail'?'1':1}]]);dom.window.close();}
});
test('Discover opens the saved preset manager only after its explicit button click',async()=>{
 const {dom,opened,d}=await integrationPage('discover');assert.equal(opened.length,0);const manage=button(d,'Saved presets');assert.ok(manage);manage.click();assert.deepEqual(opened,['manager']);dom.window.close();
});
