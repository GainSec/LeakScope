// Local fixture only: no production API, provider queries or target requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const out = path.resolve(process.env.UI_EVIDENCE_DIR || path.join(root, '.runtime/preview-ui'));
fs.mkdirSync(out, {recursive:true});
const endpoint = {id:'11111111-1111-4111-8111-111111111111',address:'192.0.2.7',port:5432,transport:'tcp',service_type:'postgres',fetch_actions:['check','tcp','identity'],default_actions:['identity']};
const services = [{key:'postgres',label:'PostgreSQL',category:'database',legacy:false},{key:'modbus',label:'Modbus',category:'ot',legacy:false},{key:'dns',label:'DNS',category:'protocol',legacy:false},...Array.from({length:139},(_,i)=>({key:'fixture-service-'+i,label:'Fixture service '+i,category:'protocol',legacy:false}))];
const scripts = [...fs.readFileSync(path.join(root,'leakscope_app/ui_bundles.py'),'utf8').matchAll(/'(js\/leakscope\/[^']+\.js)'/g)].map(m=>m[1]).filter(s=>!s.endsWith('bundle.js'));
const bundle = scripts.map(name=>fs.readFileSync(path.join(root,'leakscope_app/static',name),'utf8')).join('\n;\n');
const css = [...fs.readFileSync(path.join(root,'leakscope_app/ui_bundles.py'),'utf8').split("'js/leakscope/workspace_bundle.js'")[0].matchAll(/'([^']+\.css)'/g)].map(m=>m[1]).filter(s=>!s.endsWith('bundle.css'));
const calls = [];
let runReads = 0;
function html(bundled = false) {
 return fs.readFileSync(path.join(root,'leakscope_app/templates/workbench/app.html'),'utf8')
  .replace(/\{% workspace_assets 'css'[\s\S]*?\{% endfor %\}/,'')
  .replace(/\{% workspace_assets 'js'[\s\S]*?\{% endfor %\}/,'')
  .replace(/\{% if page == 'geography' %\}[\s\S]*?\{% endif %\}/,'')
  .replace(/\{% static '([^']+)' %\}/g,'/static/$1')
  .replace(/\{%[^%]*%\}/g,'')
  .replace("{{ page|default:'overview' }}",'preview').replace("{{ type|default:'' }}",'').replace("{{ target_id|default:'' }}",'').replace('{{ csrf_token }}','local-fixture')
  .replace('</head>',css.map(name=>'<link rel="stylesheet" href="/static/'+name+'">').join('')+(bundled ? '<script defer src="/workspace-fixture.js"></script>' : scripts.map(name=>'<script defer src="/static/'+name+'"></script>').join(''))+'</head>');
}
const server = http.createServer(async(req,res)=>{
 const url = new URL(req.url,'http://localhost');
 if(url.pathname.startsWith('/static/')){
  const file=path.resolve(root,'leakscope_app/static',url.pathname.slice(8));
  if(!file.startsWith(path.join(root,'leakscope_app/static')+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  const ext=path.extname(file);res.setHeader('Content-Type',ext==='.js'?'application/javascript':ext==='.css'?'text/css':ext==='.svg'?'image/svg+xml':'image/png');return res.end(fs.readFileSync(file));
 }
 if(url.pathname==='/workspace-fixture.js'){res.setHeader('Content-Type','application/javascript');return res.end(bundle);}
 if(url.pathname==='/preview/'){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(html(url.searchParams.has('bundle')));}
 let body='';for await(const chunk of req)body+=chunk;
 calls.push({path:url.pathname,method:req.method,body:body?JSON.parse(body):null});
 res.setHeader('Content-Type','application/json');
 const run={id:'22222222-2222-4222-8222-222222222222',kind:'capture',state:'succeeded',progress:100,result:{summary:'Local fixture: PostgreSQL protocol identity retained',identity_verified:true}};
 const data=url.pathname==='/api/operations/catalog'?{results:services,tools:{curl:true,httpx:true,playwright:true}}:url.pathname==='/api/operations/endpoints'?{results:[endpoint],page:1,pages:1,count:1}:url.pathname.endsWith('/fetch')?{runs:[{...run,state:'queued',result:null}]}:url.pathname.startsWith('/api/runs/')?(runReads++,run):{results:[]};
 res.end(JSON.stringify(data));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
  const results=[];
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],external=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{if(new URL(route.request().url()).hostname!=='127.0.0.1'){external.push(route.request().url());return route.abort();}return route.continue();});
   await page.goto('http://127.0.0.1:'+server.address().port+'/preview/'+(width===390?'?bundle=1':'' ));
   await page.locator('[name=endpoint_id] option[value="'+endpoint.id+'"]').waitFor({state:'attached'});
   assert.equal(calls.filter(c=>c.method==='POST').length,results.length,'browsing causes no writes');
   assert.equal(await page.locator('[name=technology] option').count(),143);
   await page.locator('[name=technology]').selectOption('postgres');
   await page.locator('[name=endpoint_id]').selectOption(endpoint.id);
   assert.equal(await page.locator('[name=actions]:enabled').count(),3);
   await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});
   await page.screenshot({path:path.join(out,'preview-'+width+'.png'),fullPage:true});
   await page.locator('[name=active_probe]').check();
   await page.getByRole('button',{name:'Capture selected evidence',exact:true}).click();
   await page.locator('.run-card > p.small').filter({hasText:'Local fixture: PostgreSQL protocol identity retained'}).waitFor();
   await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});
   await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});
   await page.screenshot({path:path.join(out,'preview-result-'+width+'.png'),fullPage:true});
   const bounds=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,form:document.querySelector('[data-preview-form]').getBoundingClientRect().width}));
   assert.ok(bounds.document<=width+1,JSON.stringify(bounds));assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
   results.push({width,assets:width===390?'assembled workspace bundle':'individual workspace modules',bounds,page_errors:errors,external_requests:external.length});await page.close();
  }
  const writes=calls.filter(c=>c.method==='POST');assert.equal(writes.length,2);assert.ok(writes.every(c=>c.path==='/api/operations/endpoints/'+endpoint.id+'/fetch'));assert.ok(runReads>=2);
  fs.writeFileSync(path.join(out,'render-evidence.json'),JSON.stringify({results,writes,run_reads:runReads,fixture:'local synthetic catalog and saved endpoint',production_api_calls:0,provider_calls:0,target_requests:0},null,2));
  console.log(JSON.stringify({passed:true,out,results,run_reads:runReads}));
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
