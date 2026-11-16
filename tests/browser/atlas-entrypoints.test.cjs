const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../..');
for(const [page,href,api] of [['technologies','/technology-dashboard/','/api/atlas/technologies'],['screenshots','/screenshots/','/api/atlas/screenshots'],['geography','/geography/','/api/atlas/geography']])test(page+' uses its real app entrypoint, navigation and saved views without collection',async()=>{
  const html=fs.readFileSync(path.join(root,'leakscope_app/templates/workbench/app.html'),'utf8').replaceAll("{{ page|default:'overview' }}",page).replaceAll("{{ type|default:'' }}",'').replaceAll("{{ target_id|default:'' }}",'');
  const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://localhost'+href}),calls=[];
  dom.window.fetch=async(url,options={})=>{calls.push({url:String(url),method:options.method||'GET'});const data=String(url).includes('/maps/status')?{installed:true,status:'ready'}:{results:[],groups:[],continents:[],countries:[],counts:{},summary:{},coverage:{},collections:[],database:{status:'missing'},features:[]};return {ok:true,json:async()=>data}};
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false};
  for(const file of ['utils.js','tables.js','workspace_workflows.js','workspace.js','atlas.js','app.js'])dom.window.eval(fs.readFileSync(path.join(root,'leakscope_app/static/js/leakscope',file),'utf8'));
  await new Promise(resolve=>setTimeout(resolve,100));
  assert.ok(dom.window.document.querySelector('a[href="'+href+'"]'));
  assert.ok(calls.some(call=>call.url.startsWith(api)));
  assert.ok(calls.some(call=>call.url.includes('/api/workspace/views')));
  assert.ok(calls.every(call=>call.method==='GET'));
  assert.doesNotMatch(dom.window.document.querySelector('#page-root').textContent,/page could not load/i);
  dom.window.LeakScopeAtlas.dispose();dom.window.LeakScopeWorkspace.dispose();dom.window.close();
});
