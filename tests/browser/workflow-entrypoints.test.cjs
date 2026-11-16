const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');const root=path.resolve(__dirname,'../..');
async function render(page,search="",fixtures={}){let html=fs.readFileSync(path.join(root,'leakscope_app/templates/workbench/app.html'),'utf8').replaceAll("{{ page|default:'overview' }}",page).replaceAll("{{ type|default:'' }}",'').replaceAll("{{ target_id|default:'' }}",'');const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://localhost/'+page+'/'+search}),calls=[];dom.window.fetch=async(url,opts={})=>{calls.push({url:String(url),opts});let data={results:[],counts:{},window:{since:'2026-11-08T00:00:00Z',until:'2026-11-09T00:00:00Z'}};if(String(url).includes('/activity'))data={results:[],counts:{},basis:'Saved jobs'};if(String(url).includes('/correlations'))data={results:[],count:0,kinds:[],basis:'Saved indexed evidence'};data=fixtures[String(url).split('?')[0]]||data;return{ok:true,json:async()=>data}};dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false};for(const f of ['utils.js','tables.js','data_viewer.js','run_logs.js','public_signals.js','workspace_workflows.js','workspace.js','app.js']){const p=path.join(root,'leakscope_app/static/js/leakscope',f);if(fs.existsSync(p))dom.window.eval(fs.readFileSync(p,'utf8'));}await new Promise(r=>setTimeout(r,60));return{dom,calls,d:dom.window.document}}
for(const page of ['changes','correlations'])test(page+' has navigation, its own read-only page and the persistent activity control',async()=>{const {dom,calls,d}=await render(page);assert.ok(d.querySelector('a[href="/'+page+'/"]'));assert.ok(calls.some(c=>c.url.startsWith('/api/workspace/'+page)));assert.ok(d.querySelector('#workspace-activity'));assert.equal(calls.filter(c=>c.opts.method&&c.opts.method!=='GET').length,0);assert.doesNotMatch(d.querySelector('#page-root').textContent,/page could not load/i);dom.window.LeakScopeWorkspace?.dispose();dom.window.close()});

test('public signal template link opens a Demo review dialog without executing a provider search',async()=>{
 const {dom,calls,d}=await render('queries','?id=saved-public-id&demo=1',{'/api/queries':{results:[{id:'saved-public-id',name:'Saved public signal',provider:'shodan',query_template:'http.html:"project-123"',active_probe_default:true}]}});
 try {
  assert.equal(d.querySelector('#run-dialog').open,true);
  assert.equal(d.querySelector('#run-dialog [name=demo]').checked,true);
  assert.equal(d.querySelector('#run-dialog [name=page_count]').disabled,true);
  assert.equal(d.querySelector('#run-dialog [name=active_probe]').checked,false);
  assert.equal(d.querySelector('#run-dialog [name=active_probe]').disabled,true);
  assert.equal(calls.filter(c=>c.opts.method&&c.opts.method!=='GET').length,0);
 } finally {dom.window.LeakScopeWorkspace?.dispose();dom.window.close();}
});
