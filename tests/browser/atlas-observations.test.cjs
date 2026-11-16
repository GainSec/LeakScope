const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),windows=[];
test.afterEach(()=>windows.splice(0).forEach(dom=>dom.window.close()));
async function setup(query){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/assets/?'+query,runScripts:'outside-only'});windows.push(dom);
 const calls=[];dom.window.fetch=async(url,opts={})=>{calls.push({url:new URL(url,dom.window.location),opts});return{ok:true,json:async()=>url.includes('/catalog')?{results:[{key:'http',label:'HTTP'}],tools:{}}:{results:[],count:0,page:1,pages:1}}};
 dom.window.eval(fs.readFileSync(require('node:path').resolve(__dirname,'../../leakscope_app/static/js/leakscope/list_toolbar.js'),'utf8'));dom.window.eval(fs.readFileSync(path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/operations.js'),'utf8'));
 const root=dom.window.document.querySelector('#root');await dom.window.LeakScopeOperations.render(root,'assets');return{dom,root,calls};
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
test('Observations retains classifier and geographic organization time context across AJAX filter changes',async()=>{
 const s=await setup('technology=jenkins&category=administration&organization_id=11111111-1111-4111-8111-111111111111&country=US&continent=NA&since=2026-01-01T00%3A00%3A00Z&until=2026-12-01T00%3A00%3A00Z&provider=direct&run=run1');
 const keys=['technology','category','organization_id','country','continent','since','until'];
 const first=s.calls.find(call=>call.url.pathname.endsWith('/endpoints')).url.searchParams;
 for(const key of keys)assert.ok(first.get(key),'Missing incoming '+key);
 assert.equal(first.get('provider'),'direct');assert.equal(first.get('run'),'run1');
 const context=s.root.querySelector('.ls-chips');assert.equal(context.querySelectorAll('.ls-chip').length,8);assert.ok(context);assert.match(context.textContent,/jenkins/i);assert.match(context.textContent,/US/);
 s.root.querySelector('[name=q]').value='build';s.root.querySelector('form').dispatchEvent(new s.dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();
 const latest=s.calls.filter(call=>call.url.pathname.endsWith('/endpoints')).at(-1).url.searchParams;for(const key of keys)assert.equal(latest.get(key),first.get(key));assert.equal(latest.get('q'),'build');
 assert.equal(s.calls.some(call=>call.opts.method==='POST'),false);
});
test('Observations explicitly clears incoming Atlas context while preserving ordinary filters and run',async()=>{
 const s=await setup('technology=jenkins&organization_id=11111111-1111-4111-8111-111111111111&provider=shodan&type=http&tag=review&run=run1');
 const scoped=()=>[...s.root.querySelectorAll('.ls-chip')].filter(node=>node.textContent.startsWith('Scope · '));assert.equal(scoped().length,2);while(scoped().length){scoped()[0].click();await tick();}
 const latest=s.calls.filter(call=>call.url.pathname.endsWith('/endpoints')).at(-1).url.searchParams;
 assert.equal(latest.has('technology'),false);assert.equal(latest.has('organization_id'),false);assert.equal(latest.get('provider'),'shodan');assert.equal(latest.get('type'),'http');assert.equal(latest.get('tag'),'review');assert.equal(latest.get('run'),'run1');assert.equal(scoped().length,0);assert.equal(s.root.querySelectorAll('.ls-chip').length,3);
});
