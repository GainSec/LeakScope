// Owned local UI fixture: no production database, provider or public target requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const project = path.resolve(__dirname, '../..');
const output = path.join(project, '.runtime/organization-ui');
fs.mkdirSync(output, {recursive:true});
const source = {id:'aaf5c715-826b-4539-a6a8-52bc0f7de0ae',kind:'organization',value:'Research Lab',label:'Research Lab',attributes:{aliases:['Research laboratory']},status:'active'};
const destination = {id:'127baf58-7594-4894-b780-d5c1ce336044',kind:'organization',value:'Research Group',label:'Research Group',attributes:{aliases:[]},status:'active'};
const counts = {domains:2,members:1,evidence:4,incoming:1,outgoing:3};
let sourceStatus = 'active';
const calls = [];
const detail = org => ({organization:org,domains:[{name:'example.test',node_id:'domain-fixture',source:'user_scope'}],members:[{kind:'service',value:'https://example.test:443/',label:'Owned fixture endpoint',url:'/assets/?endpoint=fixture'}]});
const assets = {'/relationships.js':['text/javascript','leakscope_app/static/js/leakscope/relationships.js'],'/relationships.css':['text/css','leakscope_app/static/css/relationships.css'],'/app.css':['text/css','leakscope_app/static/css/leakscope.css']};
const server = http.createServer(async(req,res)=>{
 const url = new URL(req.url,'http://fixture');
 if(assets[url.pathname]){const [type,file]=assets[url.pathname];res.setHeader('Content-Type',type);res.end(fs.readFileSync(path.join(project,file)));return;}
 if(url.pathname==='/reset'){sourceStatus='active';res.end('reset');return;}
 if(!url.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="owned-local-fixture"><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/relationships.css"><style>body{padding:24px;background:#f4f6f5}main{max-width:1100px;margin:auto;min-width:0}h1{font-size:30px}.fixture-label{font:12px system-ui;color:#597365;margin:0 0 12px}@media(max-width:600px){body{padding:12px}}</style></head><body><main><p class="fixture-label">OWNED LOCAL UI FIXTURE · no public requests</p><div id="root" class="asset-page"></div></main><script src="/relationships.js"></script><script>LeakScopeAssets.render(document.querySelector("#root"),"organizations")</script></body></html>');return;}
 let text='';for await(const chunk of req)text+=chunk;
 const body=text?JSON.parse(text):undefined;calls.push({url:url.pathname+url.search,method:req.method,body});res.setHeader('Content-Type','application/json');
 const current={...source,status:sourceStatus};let value;
 const base='/api/assets/organizations/';
 if(url.pathname==='/api/assets/organizations') value={results:[current,destination].filter(org=>url.searchParams.get('status')==='all'||org.status===(url.searchParams.get('status')||'active'))};
 else if(url.pathname===base+source.id+'/manage') value={preview:{organization:current,counts,...(url.searchParams.get('target_id')?{target:destination,target_counts:{...counts,domains:1}}:{})}};
 else if(url.pathname===base+source.id+'/archive'){sourceStatus='archived';value={organization:{...source,status:sourceStatus}};}
 else if(url.pathname===base+source.id+'/restore'){sourceStatus='active';value={organization:{...source,status:sourceStatus}};}
 else if(url.pathname===base+source.id+'/merge'){assert.deepEqual(body,{target_id:destination.id,confirmation:source.value});sourceStatus='merged';value={organization:destination,merged_from:source.id};}
 else if(url.pathname===base+source.id+'/delete'){assert.deepEqual(body,{confirmation:source.value});sourceStatus='deleted';value={organization:{...source,status:sourceStatus}};}
 else if(url.pathname===base+source.id)value=detail(current);
 else if(url.pathname===base+destination.id)value=detail(destination);
 else value={};res.end(JSON.stringify(value));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const results=[];
 try{
  for(const width of[1440,390]){
   const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));await page.request.get(base+'/reset');await page.goto(base+'/organizations/?id='+source.id);await page.getByRole('heading',{name:source.value,exact:true}).waitFor();
   await page.getByRole('button',{name:'Merge organization',exact:true}).click();const merge=page.getByRole('dialog',{name:'Merge organization',exact:true});await merge.locator('[name=merge_target]').selectOption(destination.id);await merge.locator('.asset-merge-direction strong').filter({hasText:destination.value}).waitFor();await merge.locator('[name=organization_confirmation]').fill(source.value);await page.screenshot({path:path.join(output,'merge-'+width+'.png'),fullPage:true});
   let bounds=await page.evaluate(()=>({document:document.documentElement.scrollWidth,viewport:innerWidth,dialog:document.querySelector('dialog').scrollWidth,dialogClient:document.querySelector('dialog').clientWidth}));assert.ok(bounds.document<=width+1,JSON.stringify(bounds));assert.ok(bounds.dialog<=bounds.dialogClient+1,JSON.stringify(bounds));await merge.getByRole('button',{name:'Cancel',exact:true}).click();
   await page.getByRole('button',{name:'Ignore / Archive',exact:true}).click();await page.getByRole('button',{name:'Restore organization',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Add domains',exact:true}).count(),0);await page.screenshot({path:path.join(output,'archived-'+width+'.png'),fullPage:true});await page.getByRole('button',{name:'Restore organization',exact:true}).click();await page.getByRole('button',{name:'Add domains',exact:true}).waitFor();
   await page.getByRole('button',{name:'Delete organization',exact:true}).click();const deletion=page.getByRole('dialog',{name:'Delete organization',exact:true});await deletion.getByText('Organization deletion impact',{exact:true}).waitFor();await page.screenshot({path:path.join(output,'delete-'+width+'.png'),fullPage:true});await deletion.locator('[name=organization_confirmation]').fill(source.value);await deletion.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'Organizations',exact:true}).waitFor();assert.equal(new URL(page.url()).search,'');assert.equal(await page.locator('[data-organization-status=deleted]').count(),0);
   await page.request.get(base+'/reset');await page.goto(base+'/organizations/?id='+source.id);await page.getByRole('button',{name:'Merge organization',exact:true}).click();const confirmed=page.getByRole('dialog',{name:'Merge organization',exact:true});await confirmed.locator('[name=merge_target]').selectOption(destination.id);await confirmed.locator('.asset-merge-direction strong').filter({hasText:destination.value}).waitFor();await confirmed.locator('[name=organization_confirmation]').fill(source.value);await confirmed.getByRole('button',{name:'Confirm merge',exact:true}).click();await page.getByRole('heading',{name:destination.value,exact:true}).waitFor();assert.equal(new URL(page.url()).searchParams.get('id'),destination.id);assert.deepEqual(errors,[]);results.push({width,bounds,page_errors:errors,merge:true,archive_restore:true,delete:true,fixture:'owned local UI fixture'});await page.close();
  }
  const report={results,public_target_requests:0,production_database_writes:0,mutations:calls.filter(c=>c.method==='POST')};fs.writeFileSync(path.join(output,'render-evidence.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
