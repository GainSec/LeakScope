const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert/strict');
const base=process.env.LEAKSCOPE_BASE_URL||'http://127.0.0.1:2630';
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));fs.mkdirSync('.runtime/grpc-ui',{recursive:true});try{
 const types=(await (await page.request.get(base+'/api/types')).json()).results;assert.equal(types.length,130);
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});
  for(const [key,provider,needle] of [['grpc','shodan','application/grpc'],['plc','zoomeye','device="plc"'],['lantronix-config','shodan','lantronix-udp'],['smux','zoomeye','service="smux"']]){
   await page.goto(base+'/discover/?service_type='+key+'&provider='+provider);await page.waitForSelector('#page-root[aria-busy=false]');await page.waitForFunction(needle=>document.querySelector('.code-box').textContent.includes(needle),needle);
   assert.equal(await page.locator('[name=types]:checked').count(),1);assert.equal(await page.locator('[name=types]:checked').inputValue(),key);const preview=await page.locator('.code-box').textContent();assert.ok(!/\bport\s*[:=]/.test(preview));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:'.runtime/grpc-ui/'+key+'-'+width+'.png',fullPage:true});
  }
  await page.goto(base+'/discover/?service_type=plc&provider=netlas');await page.waitForSelector('#page-root[aria-busy=false]');await page.waitForFunction(()=>document.querySelector('.code-box').textContent.includes('device-class'));assert.ok((await page.locator('.code-box').textContent()).includes('No verified'));
 }
 assert.deepEqual(errors,[]);fs.writeFileSync('.runtime/grpc-browser.json',JSON.stringify({base,types:types.length,viewports:[1440,390],checks:['gRPCallports','PLCdeviceZoomEye','NetlasPLCexplicitgap','LantronixUDP','SMUXclassifier','noportpredicate','singlevisibleselection','nooverflow'],errors},null,2));console.log('gRPC/device/OT browser paths passed')
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
