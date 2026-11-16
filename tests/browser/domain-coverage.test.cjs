const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const source=fs.readFileSync(path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/domain_coverage.js'),'utf8');
test('domain coverage keeps its scoped matrix and filters without any retired analytics or mutation',async()=>{
 const dom=new JSDOM('<main id="root"></main>',{url:'http://localhost/domain-coverage/?organization_id=org-1',runScripts:'outside-only'}),w=dom.window,calls=[];
 const data={scope:{id:'org-1',label:'Saved organization'},generated_at:'2026-11-11T12:00:00Z',record_types:['A','TXT','CNAME','SVCB'],coverage:{limited:false},domains:[{name:'example.test',records:{TXT:2,CNAME:1},sources:['dns'],freshness:{current:3},reference:{type:'asset',id:'domain-1'}}]};
 w.fetch=async(url,options={})=>{calls.push({url:new URL(url,w.location.origin),options});return{ok:true,json:async()=>String(url).includes('/organizations')?{results:[{id:'org-1',label:'Saved organization'}]}:data}};
 try{
  w.eval(source);const root=w.document.querySelector('#root');await w.LeakScopeDomainCoverage.render(root);
  assert.equal(root.getAttribute('aria-busy'),'false');assert.equal(root.querySelector('details').open,false);
  assert.match(root.textContent,/Saved organization/);assert.match(root.textContent,/CNAME/);assert.match(root.textContent,/SVCB/);
  assert.ok(calls.some(row=>row.url.pathname==='/api/assets/domain-coverage'&&row.url.searchParams.get('organization_id')==='org-1'));
  assert.ok(calls.every(row=>!row.options.method||row.options.method==='GET'));
  assert.ok(calls.every(row=>!row.url.pathname.includes('/intelligence/')));
  const filter=root.querySelector('[aria-label="Filter saved domain evidence"]');filter.value='not-saved';filter.dispatchEvent(new w.Event('input'));
  assert.match(root.querySelector('[data-domain-matrix]').textContent,/No saved domains match/);
  filter.value='';filter.dispatchEvent(new w.Event('input'));assert.equal(root.querySelectorAll('tbody tr').length,1);
  const button=label=>[...root.querySelectorAll('button')].find(row=>row.textContent===label);
  button('Expand all').click();assert.equal(root.querySelector('details').open,true);
  button('Collapse all').click();assert.equal(root.querySelector('details').open,false);
 }finally{w.LeakScopeDomainCoverage?.dispose();w.close();}
});
