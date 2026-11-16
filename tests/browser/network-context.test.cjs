const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const file=path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/network_context.js');
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
const cached={ip:'8.8.8.8',network_contact:false,basis:'Published network range; not ownership or physical location.',matches:[{provider:'aws',provider_label:'Amazon Web Services',prefix:'8.8.8.0/24',services:['EC2'],regions:['fixture-region'],updated_at:'2026-11-11T01:00:00Z',source_url:'https://ip-ranges.amazonaws.com/ip-ranges.json'}],datasets:[{provider:'aws',label:'Amazon Web Services',status:'ready',prefixes:2,updated_at:'2026-11-11T01:00:00Z',published_at:'2026-11-10',source_url:'https://ip-ranges.amazonaws.com/ip-ranges.json'}]};
function setup(){
 const dom=new JSDOM('<meta name="csrf-token" content="fixture"><main></main>',{url:'http://localhost/assets/',runScripts:'outside-only'}),calls=[];
 dom.window.fetch=async(url,opts={})=>{calls.push({url,opts,body:opts.body?JSON.parse(opts.body):null});return {ok:true,json:async()=>opts.method==='POST'?{requests:1,results:[{provider:'aws',status:'refresh_failed'}]}:cached};};
 if(fs.existsSync(file))dom.window.eval(fs.readFileSync(file,'utf8'));
 assert.ok(dom.window.LeakScopeNetworkContext?.panel,'Shared network panel exists');
 return {dom,calls,api:dom.window.LeakScopeNetworkContext,doc:dom.window.document};
}
function expand(dom,panel){panel.open=true;panel.dispatchEvent(new dom.window.Event('toggle'));}
test('network context stays collapsed with no request until expanded and reads cached data once',async()=>{
 const {dom,calls,api,doc}=setup();try{
  const panel=api.panel('8.8.8.8');doc.querySelector('main').append(panel);
  assert.equal(panel.tagName,'DETAILS');assert.equal(panel.open,false);assert.equal(calls.length,0);
  expand(dom,panel);await tick();assert.equal(calls.length,1);assert.equal(calls[0].opts.method,'GET');assert.match(calls[0].url,/ip=8.8.8.8/);
  assert.match(panel.textContent,/8\.8\.8\.0\/24/);assert.match(panel.textContent,/EC2/);assert.match(panel.textContent,/Published region/);assert.match(panel.textContent,/fixture-region/);assert.match(panel.textContent,/Snapshot collected/);assert.match(panel.textContent,/not ownership/);
  panel.open=false;expand(dom,panel);await tick();assert.equal(calls.length,1);
 }finally{dom.window.close();}
});
test('refresh is an explicit CSRF-protected selected-feed request and keeps failures visible after reload',async()=>{
 const {dom,calls,api,doc}=setup();try{
  const panel=api.panel('8.8.8.8');doc.querySelector('main').append(panel);expand(dom,panel);await tick();
  [...panel.querySelectorAll('button')].find(button=>button.textContent==='Refresh selected feeds').click();await tick();
  assert.equal(calls.length,3);assert.equal(calls[1].opts.method,'POST');assert.deepEqual(calls[1].body,{providers:['aws']});assert.equal(calls[1].opts.headers['X-CSRFToken'],'fixture');
  assert.equal(calls[2].opts.method,'GET');assert.match(panel.textContent,/refresh failed/i);assert.match(panel.textContent,/1 public feed request/);
 }finally{dom.window.close();}
});
test('read cached context can deliberately reload installed metadata after an initial successful lookup',async()=>{
 const {dom,calls,api,doc}=setup();try{
  const panel=api.panel('8.8.8.8');doc.querySelector('main').append(panel);expand(dom,panel);await tick();
  [...panel.querySelectorAll('button')].find(button=>button.textContent==='Read cached context').click();await tick();
  assert.equal(calls.length,2);assert.ok(calls.every(call=>call.opts.method==='GET'));
 }finally{dom.window.close();}
});
test('hostnames and scoped IPv6 never create a network panel or resolve a name',()=>{
 const {dom,calls,api}=setup();try{assert.equal(api.panel('example.com'),null);assert.equal(api.panel('fe80::1%en0'),null);assert.equal(calls.length,0);assert.ok(api.panel('2606:4700::1111'));}finally{dom.window.close();}
});
test('native endpoint inspector includes collapsed literal-IP context with no extra lookup on open',async()=>{
 const {dom,calls,doc}=setup();try{
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
  dom.window.fetch=async(url,opts={})=>{calls.push({url,opts});return {ok:true,json:async()=>({endpoint:{address:'8.8.8.8',port:443},observations:[],artifacts:[]})};};
  dom.window.eval(fs.readFileSync(path.join(path.dirname(file),'inspector.js'),'utf8'));
  await dom.window.LeakScopeInspector.open({type:'endpoint',id:'fixture'});
  const panel=doc.querySelector('.network-context');assert.ok(panel);assert.equal(panel.open,false);assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/operations/endpoints/fixture');
 }finally{dom.window.close();}
});
test('native asset tools use retained IP choices while hostname-only context never starts lookup',async()=>{
 const {dom,calls,doc}=setup();try{
  let target='example.com',ips=['8.8.8.8'];
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new dom.window.Event('close'));};
  dom.window.fetch=async(url,opts={})=>{calls.push({url,opts});return {ok:true,json:async()=>({target,ips,names:[],evidence:[],actions:{}})};};
  dom.window.eval(fs.readFileSync(path.join(path.dirname(file),'relationships.js'),'utf8'));
  dom.window.LeakScopeAssets.openTools('example.com');await tick();
  let panel=doc.querySelector('.network-context');assert.ok(panel);assert.equal(panel.open,false);assert.equal(calls.length,1);assert.match(panel.textContent,/8\.8\.8\.8/);
  doc.querySelector('dialog').close();ips=[];dom.window.LeakScopeAssets.openTools(target);await tick();
  assert.equal(doc.querySelector('.network-context'),null);assert.equal(calls.length,2);
 }finally{dom.window.close();}
});
