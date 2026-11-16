// Local UI fixture only. No provider queries or target requests.
const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const http=require('node:http');const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');const out=path.resolve(process.env.UI_EVIDENCE_DIR||path.join(root,'.runtime/browser-ui'));fs.mkdirSync(out,{recursive:true});
const id='11111111-1111-4111-8111-111111111111';const calls=[];let frame;
const files={'/app.css':'leakscope_app/static/css/leakscope.css','/browser.css':'leakscope_app/static/css/browser_sessions.css','/browser.js':'leakscope_app/static/js/leakscope/browser_sessions.js'};
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost');
 if(files[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.css')?'text/css':'application/javascript');return res.end(fs.readFileSync(path.join(root,files[url.pathname])));}
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="local-fixture"><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/browser.css"><main style="padding:32px"><h1>Local UI fixture</h1><button id="open" class="btn">Open verification browser</button></main><script src="/browser.js"></script><script>document.querySelector("#open").onclick=()=>LeakScopeBrowser.open({id:"endpoint-fixture",address:"owned-fixture.test",port:443},{profile:"web_page",timeout_seconds:35,wait_seconds:2,full_page:false})</script>');}
 let body='';for await(const chunk of req)body+=chunk;calls.push({path:url.pathname,body:body?JSON.parse(body):null});res.setHeader('Cache-Control','no-store');
 if(url.pathname.endsWith('/frame')){res.setHeader('Content-Type','image/png');return res.end(frame);}
 res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(url.pathname.endsWith('/capabilities')?{available:true,manual_sessions:true}:url.pathname==='/api/browser/sessions'?{session:{id,viewport:{width:1280,height:800},expires_at:new Date(Date.now()+600000).toISOString()}}:url.pathname.endsWith('/capture')?{run:{id:'fixture-run',state:'queued'}}:{session:{id}}));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
 const fixture=await browser.newPage({viewport:{width:1280,height:800}});await fixture.setContent('<html style="background:#eff4f0;color:#1d3029;font:22px system-ui"><body style="padding:60px"><div style="max-width:600px;background:white;border:1px solid #d5dfd8;padding:40px;border-radius:16px"><small style="color:#178363">OWNED LOCAL FIXTURE</small><h1>Browser verification</h1><p>This is a local render test. No public target was opened.</p><button style="padding:18px">Continue</button></div></body></html>');frame=await fixture.screenshot();await fixture.close();
 const results=[];
 for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#open').click();await page.locator('.browser-session-viewport img').waitFor();await page.waitForFunction(()=>document.querySelector('.browser-session-viewport img')?.naturalWidth>0);
 const bounds=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,dialog:document.querySelector('dialog').getBoundingClientRect().width,dialogScroll:document.querySelector('dialog').scrollWidth,dialogClient:document.querySelector('dialog').clientWidth}));assert.ok(bounds.document<=width+1,JSON.stringify(bounds));assert.ok(bounds.dialogScroll<=bounds.dialogClient+1,JSON.stringify(bounds));assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(out,'verification-browser-'+width+'.png'),fullPage:true});await page.getByRole('button',{name:'Save screenshot',exact:true}).click();await page.getByText('Screenshot queued from this session.',{exact:false}).waitFor();await page.getByRole('button',{name:'Close browser',exact:true}).click();await page.locator('dialog').waitFor({state:'detached'});results.push({width,bounds,page_errors:errors,fixture:'owned local fixture'});await page.close();
 }
 assert.equal(calls.filter(c=>c.path.endsWith('/capture')).length,2);assert.equal(calls.filter(c=>c.path.endsWith('/close')).length,2);fs.writeFileSync(path.join(out,'render-evidence.json'),JSON.stringify({results,captures:2,closed:2,public_target_requests:0},null,2));console.log(JSON.stringify({passed:true,out,results}));
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
