const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const file=path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/expansion.js');
const org={id:'org-1',value:'Fixture Holdings',status:'active',attributes:{aliases:['Fixture']}};
const candidate={id:'c1',domain:'different-brand.net',root:'different-brand.net',relation:'acquired_brand',confidence:70,confidence_label:'medium',status:'pending',evidence:[{source:'web',url:'https://fixture.example/legal',excerpt:'Acquired <img src=x onerror=alert(1)>',signal:'legal statement',observed_at:'2026-11-08T00:00:00Z'}],paths:[{seed:'fixture.example',source:'web'}],tags:[],dns_status:'unchecked'};
const expansion={id:'e1',organization_id:'org-1',organization_name:org.value,seeds:[{kind:'domain',value:'fixture.example',depth:0}],sources:['saved','certificates'],state:'succeeded',usage:{web:0},budgets:{web:4},notes:[],partial:false,created_at:'2026-11-08T00:00:00Z'};
function setup(routes={},url='http://localhost/domain-expansion/') {
 const dom=new JSDOM('<meta name="csrf-token" content="fixture"><main id="root"></main>',{url,runScripts:'outside-only'}),calls=[];
 dom.window.fetch=async(url,opts={})=>{const u=new URL(url,'http://localhost');const call={url:u,opts,body:opts.body?JSON.parse(opts.body):undefined};calls.push(call);let v=routes[u.pathname]??({'/api/assets/organizations':{results:[org]},'/api/assets/organizations/org-1':{organization:org,domains:[{name:'fixture.example'}]},'/api/assets/expansions':{results:[]},'/api/assets/expansions/e1':{expansion,candidates:[candidate],count:1}}[u.pathname]||{});if(typeof v==='function')v=await v(call);else v=await v;return {ok:!v.error,status:v._status || (v.error?400:200),json:async()=>v};};
 if(fs.existsSync(file))dom.window.eval(fs.readFileSync(file,'utf8'));
 return {dom,calls,api:dom.window.LeakScopeExpansion,root:dom.window.document.querySelector('#root')};
}
const settle=()=>new Promise(r=>setTimeout(r,20));
const button=(root,label)=>[...root.querySelectorAll('button')].find(n=>n.textContent===label);
function value(root,name,v){const n=root.querySelector(`[name="${name}"]`);assert.ok(n,name);n.value=v;n.dispatchEvent(new root.ownerDocument.defaultView.Event('input'));return n;}
function selected(root){const box=root.querySelector('input[name=candidate_id]');assert.ok(box);box.checked=true;box.dispatchEvent(new root.ownerDocument.defaultView.Event('change'));}
test('expansion starts from multiple input types without spending on page load',async()=>{
 const {dom,root,api,calls}=setup();assert.ok(api,'expansion module exported');await api.render(root);assert.ok(root.querySelector('[name=companies]'));assert.ok(root.querySelector('[name=domains]'));assert.match(root.textContent,/retained local/i);assert.match(root.textContent,/Netlas/);assert.equal(root.querySelector('[name=source_netlas]').checked,false);assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});
test('organization seeds and aliases are prefilled with a results link and selected scope',async()=>{
 const {dom,root,api,calls}=setup({},'http://localhost/domain-expansion/?organization_id=org-1');assert.ok(api);await api.render(root);assert.equal(root.querySelector('[name=organization_id]').value,'org-1');assert.match(root.querySelector('[name=companies]').value,/Fixture Holdings/);assert.match(root.querySelector('[name=companies]').value,/Fixture/);assert.equal(root.querySelector('[name=domains]').value,'fixture.example');assert.ok(root.querySelector('a[href="/organizations/?id=org-1"]'));assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});
test('explicit submit sends sources budgets context and minimal real Demo limits',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});assert.ok(api);await api.render(root);value(root,'companies','Fixture Holdings\nOther Brand');value(root,'domains','api.fixture.example\nfixture.example');value(root,'country','US');value(root,'industry','Manufacturing');root.querySelector('[name=demo]').checked=true;button(root,'Start expansion').click();await settle();const post=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions');assert.ok(post);assert.deepEqual(post.body.companies,['Fixture Holdings','Other Brand']);assert.equal(post.body.demo,true);assert.equal(post.body.max_depth,0);assert.equal(post.body.max_candidates,1);assert.equal(post.body.budgets.netlas,0);assert.ok(post.body.budgets.web<=1);assert.deepEqual(post.body.context,{country:'US',industry:'Manufacturing'});assert.equal(post.opts.headers['X-CSRFToken'],'fixture');dom.window.close();
});
test('candidate detail shows evidence and safe source links without inventing ownership',async()=>{
 const {dom,root,api}=setup({},'http://localhost/domain-expansion/?id=e1');assert.ok(api);await api.render(root);assert.match(root.textContent,/different-brand.net/);assert.match(root.textContent,/acquired.brand/i);assert.match(root.textContent,/legal statement/);assert.match(root.textContent,/Acquired <img/);assert.equal(root.querySelector('img'),null);assert.ok(root.querySelector('a[href="https://fixture.example/legal"]'));assert.match(root.textContent,/unchecked/);assert.ok(button(root,'Add to organization'));dom.window.close();
});
test('bulk accept reject and tags are explicit requests on selected candidate ids',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1/review':{expansion,candidates:[candidate]}},'http://localhost/domain-expansion/?id=e1');assert.ok(api);await api.render(root);assert.equal(button(root,'Add to organization').disabled,true);selected(root);button(root,'Add to organization').click();await settle();let review=calls.filter(c=>c.url.pathname.endsWith('/review'));assert.deepEqual(review[0].body,{candidate_ids:['c1'],action:'accept',organization_id:'org-1'});selected(root);value(root,'candidate_tags','priority, brand');button(root,'Apply tags').click();await settle();review=calls.filter(c=>c.url.pathname.endsWith('/review'));assert.deepEqual(review[1].body.tags,['priority','brand']);selected(root);button(root,'Reject selected').click();await settle();assert.equal(calls.filter(c=>c.url.pathname.endsWith('/review'))[2].body.action,'reject');dom.window.close();
});
test('standalone candidate acceptance requires a chosen organization',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,organization_id:null,organization_name:''},candidates:[candidate]}},'http://localhost/domain-expansion/?id=e1');assert.ok(api);await api.render(root);selected(root);assert.equal(button(root,'Add to organization').disabled,true);const dest=value(root,'review_organization_id','org-1');dest.dispatchEvent(new dom.window.Event('change'));assert.equal(button(root,'Add to organization').disabled,false);assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});
test('expand selected makes a child request and monitor delegates only after click',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1/expand':{expansion:{...expansion,id:'e2'},candidates:[]}},'http://localhost/domain-expansion/?id=e1');assert.ok(api);let watched;dom.window.LeakScopeAssets={monitor:v=>watched=v};await api.render(root);assert.equal(watched,undefined);button(root,'Monitor').click();assert.equal(watched,'different-brand.net');selected(root);button(root,'Expand selected').click();await settle();const c=calls.find(c=>c.url.pathname.endsWith('/expand'));assert.deepEqual(c.body.candidate_ids,['c1']);assert.equal(calls.some(c=>/\/fetch/.test(c.url.pathname)),false);dom.window.close();
});
test('filters and select visible preserve distinctions for lookalikes and wildcard evidence',async()=>{
 const wild={...candidate,id:'c2',domain:'*.fixture.example',relation:'subdomain'},look={...candidate,id:'c3',domain:'fixture.examp1e',relation:'lookalike'};
 const {dom,root,api}=setup({'/api/assets/expansions/e1':{expansion,candidates:[candidate,wild,look]}},'http://localhost/domain-expansion/?id=e1');assert.ok(api);await api.render(root);const f=value(root,'candidate_relation','lookalike');f.dispatchEvent(new dom.window.Event('change'));assert.equal(root.querySelectorAll('input[name=candidate_id]').length,1);assert.match(root.querySelector('[data-candidate-list]').textContent,/fixture.examp1e/);button(root,'Select visible').click();assert.equal(root.querySelector('input[name=candidate_id]').checked,true);dom.window.close();
});
test('cancellation is explicit and partial notes and request usage remain visible',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,state:'running',partial:true,notes:['DNS budget reached'],usage:{web:1,dns:8}},candidates:[candidate]},'/api/assets/expansions/e1/cancel':{expansion:{...expansion,state:'cancelled'},candidates:[candidate]}},'http://localhost/domain-expansion/?id=e1');assert.ok(api);await api.render(root);assert.match(root.textContent,/DNS budget reached/);assert.match(root.textContent,/Partial/);assert.equal(calls.filter(c=>c.body).length,0);button(root,'Cancel expansion').click();await settle();assert.equal(calls.filter(c=>c.url.pathname.endsWith('/cancel')).length,1);assert.match(root.textContent,/cancelled/);dom.window.close();
});
test('dispose aborts pending reads and stops them from replacing another page',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r);const {dom,root,api}=setup({'/api/assets/expansions/e1':pending},'http://localhost/domain-expansion/?id=e1');assert.ok(api);const rendering=api.render(root);await settle();api.dispose();root.replaceChildren(dom.window.document.createTextNode('Next page'));resolve({expansion,candidates:[candidate]});await rendering;assert.equal(root.textContent,'Next page');dom.window.close();
});
test('URL handoffs retain organization or result references without starting jobs',()=>{
 const {dom,api,calls}=setup();assert.ok(api);assert.equal(api.url({organization_id:'org-1'}),'/domain-expansion/?organization_id=org-1');const url=new URL(api.url({reference:{type:'endpoint',id:'ep-1'},domain:'api.fixture.example'}),'http://localhost');assert.equal(url.searchParams.get('reference_type'),'endpoint');assert.equal(url.searchParams.get('reference_id'),'ep-1');assert.equal(url.searchParams.get('domain'),'api.fixture.example');assert.equal(calls.length,0);dom.window.close();
});

test('organization page exposes expansion and saved results entry points without spending',async()=>{
 const {dom,root,calls}=setup({},'http://localhost/organizations/?id=org-1');dom.window.eval(fs.readFileSync(path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/relationships.js'),'utf8'));await dom.window.LeakScopeAssets.render(root,'organizations');const expand=[...root.querySelectorAll('a')].find(a=>a.textContent==='Expand domains');assert.ok(expand);assert.match(expand.getAttribute('href'),/organization_id=org-1/);assert.ok([...root.querySelectorAll('a')].find(a=>a.textContent==='Expansion results'));assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});
test('saved finding asset tools hand off the original reference to domain expansion',async()=>{
 const {dom,calls}=setup({'/api/assets/context':{target:'192.0.2.5',names:[{name:'api.fixture.example'}],ips:['192.0.2.5'],actions:{}}});dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.eval(fs.readFileSync(path.resolve(__dirname,'../../leakscope_app/static/js/leakscope/relationships.js'),'utf8'));dom.window.LeakScopeAssets.openTools({type:'endpoint',id:'ep-1'});await settle();const a=[...dom.window.document.querySelectorAll('a')].find(a=>a.textContent==='Expand domains');assert.ok(a);const u=new URL(a.href);assert.equal(u.searchParams.get('reference_type'),'endpoint');assert.equal(u.searchParams.get('reference_id'),'ep-1');assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});
test('a single candidate can expand directly without changing the multi-select review',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1/expand':{expansion:{...expansion,id:'e2'},candidates:[]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);const child=button(root,'Expand from here');assert.ok(child);child.click();await settle();assert.deepEqual(calls.find(c=>c.url.pathname.endsWith('/expand')).body.candidate_ids,['c1']);dom.window.close();
});
test('source failures preserve reviewed selections and allow retry with an explicit error',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1/review':{error:'Organization was archived during review'}},'http://localhost/domain-expansion/?id=e1');await api.render(root);selected(root);button(root,'Add to organization').click();await settle();assert.match(root.textContent,/archived during review/);assert.equal(root.querySelector('input[name=candidate_id]').checked,true);assert.equal(button(root,'Add to organization').disabled,false);assert.equal(calls.filter(c=>c.url.pathname.endsWith('/review')).length,1);dom.window.close();
});
test('a stale pending refresh cannot overwrite a completed review decision',async()=>{
 let resolve,count=0;const pending=new Promise(r=>resolve=r);const accepted={...candidate,status:'accepted'};
 const {dom,root,api}=setup({'/api/assets/expansions/e1':()=>++count===1?{expansion,candidates:[candidate]}:pending,'/api/assets/expansions/e1/review':{expansion,candidates:[accepted]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);button(root,'Refresh results').click();await settle();selected(root);button(root,'Add to organization').click();await settle();assert.match(root.querySelector('[data-candidate-list]').textContent,/accepted/);resolve({expansion,candidates:[candidate]});await settle();assert.match(root.querySelector('[data-candidate-list]').textContent,/accepted/);dom.window.close();
});
test('company legal names preserve embedded commas as one identity',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);value(root,'companies','Example Holdings, Inc.\nAnother Brand');button(root,'Start expansion').click();await settle();assert.deepEqual(calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions').body.companies,['Example Holdings, Inc.','Another Brand']);dom.window.close();
});
test('opening saved results puts research first while keeping new expansion controls available',async()=>{
 const {dom,root,api}=setup({},'http://localhost/domain-expansion/?id=e1');await api.render(root);const disclosure=root.querySelector('details[data-expansion-setup]');assert.ok(disclosure);assert.equal(disclosure.open,false);assert.ok(disclosure.querySelector('button[type=submit]'));assert.match(root.querySelector('[data-expansion-detail]').textContent,/Research results/);dom.window.close();
});
test('refresh retains review destination, typed tags and selected candidates',async()=>{
 const {dom,root,api}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,organization_id:null},candidates:[candidate]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);selected(root);const destination=value(root,'review_organization_id','org-1');destination.dispatchEvent(new dom.window.Event('change'));value(root,'candidate_tags','important, investigation');button(root,'Refresh results').click();await settle();assert.equal(root.querySelector('[name=review_organization_id]').value,'org-1');assert.equal(root.querySelector('[name=candidate_tags]').value,'important, investigation');assert.equal(root.querySelector('input[name=candidate_id]').checked,true);assert.equal(button(root,'Apply tags').disabled,false);dom.window.close();
});
test('changing organization replaces old automatic seeds but keeps manually added names and domains',async()=>{
 const second={...org,id:'org-2',value:'Second Holdings',attributes:{aliases:['Second Brand']}};
 const {dom,root,api,calls}=setup({'/api/assets/organizations':{results:[org,second]},'/api/assets/organizations/org-2':{organization:second,domains:[{name:'second.example'}]},'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}},'http://localhost/domain-expansion/?organization_id=org-1');await api.render(root);value(root,'companies',root.querySelector('[name=companies]').value+'\nManual Brand');value(root,'domains',root.querySelector('[name=domains]').value+'\nmanual.example');const organization=value(root,'organization_id','org-2');organization.dispatchEvent(new dom.window.Event('change'));await settle();assert.equal(root.querySelector('[name=companies]').value.includes('Fixture'),false);assert.equal(root.querySelector('[name=domains]').value.includes('fixture.example'),false);assert.match(root.querySelector('[name=companies]').value,/Manual Brand/);assert.match(root.querySelector('[name=companies]').value,/Second Brand/);assert.match(root.querySelector('[name=domains]').value,/manual.example/);assert.match(root.querySelector('[name=domains]').value,/second.example/);button(root,'Start expansion').click();await settle();const posted=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions');assert.deepEqual(posted.body.companies,['Manual Brand']);assert.equal(posted.body.domains.includes('fixture.example'),false);dom.window.close();
});
test('organization prefills only the bounded aliases and never submits them as manual overrides',async()=>{
 const many={...org,attributes:{aliases:Array.from({length:40},(_,i)=>'Alias '+i)}};
 const {dom,root,api,calls}=setup({'/api/assets/organizations/org-1':{organization:many,domains:[{name:'fixture.example'}]},'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}},'http://localhost/domain-expansion/?organization_id=org-1');await api.render(root);assert.equal(root.querySelector('[name=companies]').value.split('\n').length,9);assert.match(root.textContent,/first 8 aliases/i);button(root,'Start expansion').click();await settle();assert.deepEqual(calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions').body.companies,[]);dom.window.close();
});
test('same-session refresh restores focused text field caret and open candidate evidence',async()=>{
 const {dom,root,api}=setup({},'http://localhost/domain-expansion/?id=e1');await api.render(root);const tags=value(root,'candidate_tags','investigate, important');tags.focus();tags.setSelectionRange(2,8,'backward');const evidence=root.querySelector('[data-candidate-id] details.expansion-evidence');evidence.open=true;evidence.querySelector('details').open=true;button(root,'Refresh results').click();await settle();const restored=root.querySelector('[name=candidate_tags]');assert.equal(dom.window.document.activeElement,restored);assert.equal(restored.selectionStart,2);assert.equal(restored.selectionEnd,8);assert.equal(restored.selectionDirection,'backward');assert.equal(root.querySelector('[data-candidate-id] details.expansion-evidence').open,true);assert.equal(root.querySelector('[data-candidate-id] details.expansion-evidence details').open,true);dom.window.close();
});
test('same-session refresh restores focused select while a child session resets evidence and focus',async()=>{
 const {dom,root,api}=setup({'/api/assets/expansions/e1/expand':{expansion:{...expansion,id:'e2'},candidates:[{...candidate,id:'child-candidate'}]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);const control=value(root,'candidate_status','pending');control.dispatchEvent(new dom.window.Event('change'));control.focus();root.querySelector('[data-candidate-id] details.expansion-evidence').open=true;button(root,'Refresh results').click();await settle();assert.equal(dom.window.document.activeElement,root.querySelector('[name=candidate_status]'));assert.equal(root.querySelector('[name=candidate_status]').value,'pending');button(root,'Expand from here').click();await settle();assert.equal(root.querySelector('[data-candidate-id] details.expansion-evidence').open,false);assert.notEqual(dom.window.document.activeElement,root.querySelector('[name=candidate_status]'));dom.window.close();
});

test('domain discovery separates first-party names from website dependencies and external references',async()=>{
 const domains=Array.from({length:5},(_,i)=>({...candidate,id:'domain-'+i,domain:'host'+i+'.fixture.example',relation:'subdomain',category:'domains'}));
 const services=Array.from({length:32},(_,i)=>({...candidate,id:'service-'+i,domain:'cdn'+i+'.service.example',relation:'service_provider',category:'services',evidence:[{source:'web',signal:'service_dependency'}]}));
 const reference={...candidate,id:'ref',domain:'publisher.example',relation:'related',category:'references'};
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion,candidates:[...domains,...services,reference],counts:{domains:5,related:0,services:32,references:1,lookalikes:0,total:38}}},'http://localhost/domain-expansion/?id=e1');
 await api.render(root);
 assert.equal(root.querySelector('[name=candidate_category]').value,'discovery');
 assert.equal(root.querySelectorAll('[data-candidate-id]').length,5);
 assert.match(root.querySelector('[data-discovery-counts]').textContent,/5 domain names/);
 assert.match(root.querySelector('[data-discovery-counts]').textContent,/32 third-party services/);
 assert.equal(root.querySelector('[data-candidate-list]').textContent.includes('cdn0.service.example'),false);
 const category=value(root,'candidate_category','services');category.dispatchEvent(new dom.window.Event('change'));
 assert.equal(root.querySelectorAll('[data-candidate-id]').length,32);
 assert.equal(button(root,'Expand from here'),undefined);
 selected(root);assert.equal(button(root,'Expand selected').disabled,true);
 button(root,'Expand selected').click();assert.equal(calls.some(c=>c.url.pathname.endsWith('/expand')),false);
 value(root,'candidate_category','references').dispatchEvent(new dom.window.Event('change'));
 assert.match(root.querySelector('[data-candidate-list]').textContent,/publisher.example/);
 assert.equal(button(root,'Expand from here'),undefined);
 dom.window.close();
});

test('passive source defaults and grouped request budgets keep web and paid discovery opt-in',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);
 for(const source of ['saved','certificates','crtsh','certspotter','hackertarget','urlscan','gleif','passive_dns'])assert.equal(root.querySelector('[name=source_'+source+']')?.checked,true,source);
 for(const source of ['web','netlas','dns','brute','alterations','infrastructure'])assert.equal(root.querySelector('[name=source_'+source+']')?.checked,false,source);
 for(const [bucket,limit] of [['public_ct','20'],['domain_api','20'],['org_api','10']])assert.equal(root.querySelector('[name=budget_'+bucket+']')?.value,limit);
 value(root,'domains','fixture.example');root.querySelector('[name=demo]').checked=true;button(root,'Start expansion').click();await settle();
 const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions').body;
 assert.deepEqual(sent.sources,['saved','certificates','crtsh','certspotter','hackertarget','urlscan','gleif','passive_dns']);
 assert.equal(sent.budgets.web,0);assert.equal(sent.budgets.netlas,0);
 assert.equal(sent.budgets.public_ct,1);assert.equal(sent.budgets.domain_api,1);assert.equal(sent.budgets.org_api,1);
 assert.match(root.textContent,/unexpired/i);assert.match(root.textContent,/existing.*scans/i);
 dom.window.close();
});

test('category choice and hidden candidate selections survive refresh without enabling service expansion',async()=>{
 const service={...candidate,id:'service',domain:'cdn.service.example',category:'services',relation:'service_provider'};
 const {dom,root,api}=setup({'/api/assets/expansions/e1':{expansion,candidates:[candidate,service]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 const filter=value(root,'candidate_category','services');filter.dispatchEvent(new dom.window.Event('change'));selected(root);button(root,'Refresh results').click();await settle();
 assert.equal(root.querySelector('[name=candidate_category]').value,'services');assert.equal(root.querySelector('input[name=candidate_id]').checked,true);assert.equal(button(root,'Expand selected').disabled,true);
 dom.window.close();
});

test('legal entity leads stay separate from domain candidates and require an explicit search handoff',async()=>{
 const lead={kind:'company',value:'Different Legal Holdings',relation:'parent',ambiguous:true,evidence:[{source:'gleif',source_record_id:'LEI-FIXTURE',url:'https://api.gleif.org/api/v1/lei-records/LEI-FIXTURE',signal:'reported_parent',excerpt:'Reported parent relationship; no website field.'}]};
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,leads:[lead]},candidates:[]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 const panel=root.querySelector('[data-entity-leads]');assert.ok(panel);assert.match(panel.textContent,/Different Legal Holdings/);assert.match(panel.textContent,/parent/);assert.match(panel.textContent,/ambiguous/i);assert.match(panel.textContent,/LEI-FIXTURE/);
 const handoff=panel.querySelector('a[data-company-expansion]');assert.ok(handoff);const target=new URL(handoff.href);assert.equal(target.searchParams.get('company'),lead.value);assert.equal(target.searchParams.get('organization_id'),'org-1');assert.equal(target.searchParams.get('focused'),'1');
 assert.equal(root.querySelectorAll('[data-candidate-id]').length,0);assert.equal(calls.some(c=>c.body),false);dom.window.close();
});

test('company-only bootstrap enables bounded web lookup until the user makes an explicit source choice',async()=>{
 const {dom,root,api,calls}=setup();await api.render(root);const web=root.querySelector('[name=source_web]');assert.equal(web.checked,false);
 value(root,'companies','Only A Company');assert.equal(web.checked,true);assert.match(root.querySelector('[data-company-bootstrap]').textContent,/Company-only searches use.*bounded web identity lookup/i);
 value(root,'domains','fixture.example');assert.equal(web.checked,false);
 value(root,'domains','');assert.equal(web.checked,true);
 web.checked=false;web.dispatchEvent(new dom.window.Event('input',{bubbles:true}));web.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(web.checked,false);
 assert.match(root.querySelector('[data-company-bootstrap]').textContent,/entity-only.*unresolved/i);
 value(root,'companies','A Different Company');assert.equal(web.checked,false);
 value(root,'domains','fixture.example');web.checked=true;web.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 value(root,'companies','');assert.equal(web.checked,true);assert.equal(calls.some(c=>c.body),false);dom.window.close();
});

test('organization context bootstraps web only when its company names have no domain roots',async()=>{
 const rootless={...org,id:'org-2',value:'Rootless Holdings'};
 const {dom,root,api}=setup({'/api/assets/organizations':{results:[org,rootless]},'/api/assets/organizations/org-2':{organization:rootless,domains:[]}},'http://localhost/domain-expansion/?organization_id=org-1');await api.render(root);
 const web=root.querySelector('[name=source_web]');assert.equal(web.checked,false);
 value(root,'domains','');assert.equal(web.checked,false,'stored organization roots remain known even when the displayed seed field is cleared');
 value(root,'organization_id','org-2').dispatchEvent(new dom.window.Event('change'));await settle();assert.equal(root.querySelector('[name=domains]').value,'');assert.equal(web.checked,true);
 assert.match(root.querySelector('[data-company-bootstrap]').textContent,/passive datasets require a domain seed/i);
 value(root,'organization_id','org-1').dispatchEvent(new dom.window.Event('change'));await settle();assert.equal(web.checked,false);assert.equal(root.querySelector('[data-company-bootstrap]').textContent,'');dom.window.close();
});

test('focused company research keeps organization association without importing its original roots or aliases',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}},'http://localhost/domain-expansion/?organization_id=org-1&company=Different%20Legal%20Parent&focused=1');await api.render(root);
 assert.equal(root.querySelector('[name=companies]').value,'Different Legal Parent');assert.equal(root.querySelector('[name=domains]').value,'');assert.equal(root.querySelector('[name=organization_id]').value,'org-1');
 assert.equal(root.querySelector('[name=source_web]').checked,true);assert.match(root.textContent,/focused research/i);
 button(root,'Start expansion').click();await settle();const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions').body;
 assert.equal(sent.focused,true);assert.equal(sent.organization_id,'org-1');assert.deepEqual(sent.companies,['Different Legal Parent']);assert.deepEqual(sent.domains,[]);dom.window.close();
});

test('accepting mixed discovery and service entries describes relationships rather than organization-owned domains',async()=>{
 const service={...candidate,id:'service',domain:'cdn.service.example',category:'services',relation:'service_provider'};
 const response={expansion,candidates:[{...candidate,status:'accepted'},{...service,status:'accepted'}]};
 const {dom,root,api}=setup({'/api/assets/expansions/e1':{expansion,candidates:[candidate,service]},'/api/assets/expansions/e1/review':response},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 value(root,'candidate_category','all').dispatchEvent(new dom.window.Event('change'));button(root,'Select visible').click();button(root,'Add to organization').click();await settle();
 assert.match(root.textContent,/Selected relationships saved under the organization/);assert.equal(root.textContent.includes('Selected domains added to the organization'),false);dom.window.close();
});

function expansionTimers(dom){const w=dom.window,pending=new Map();let key=9000;const nativeSet=w.setTimeout.bind(w),nativeClear=w.clearTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>ms===2500?(pending.set(++key,()=>fn(...args)),key):nativeSet(fn,ms,...args);w.clearTimeout=id=>{if(!pending.delete(id))nativeClear(id)};return {pending,async tick(){assert.equal(pending.size,1,'one status poll');const [id,fn]=pending.entries().next().value;pending.delete(id);await fn();await settle();}};}

test('starting an expansion is acknowledged immediately outside setup and duplicate submits are blocked',async()=>{
 let finish;const response=new Promise(r=>{finish=r});
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?response:{results:[]}});await api.render(root);value(root,'domains','fixture.example');
 const scrolls=[];dom.window.HTMLElement.prototype.scrollIntoView=function(){scrolls.push(this)};
 const form=root.querySelector('form');button(root,'Start expansion').click();form.dispatchEvent(new dom.window.Event('submit',{cancelable:true,bubbles:true}));await settle();
 const status=root.querySelector('[data-expansion-activity]');assert.ok(status,'prominent activity panel');assert.equal(status.hidden,false);assert.equal(status.closest('[data-expansion-setup]'),null);assert.match(status.textContent,/Starting expansion/i);assert.equal(button(root,'Starting expansion…').disabled,true);assert.equal(calls.filter(c=>c.body).length,1);
 finish({expansion:{...expansion,state:'queued',run:{id:'run-e1',state:'queued',progress_current:0,progress_total:0}},candidates:[]});await settle();assert.match(status.textContent,/Expansion queued/);assert.match(status.textContent,/worker/i);assert.equal(root.querySelector('[data-expansion-setup]').open,false);assert.ok(scrolls.includes(status));assert.equal(new URL(dom.window.location.href).searchParams.get('id'),'e1');dom.window.close();
});

test('saved queued and running progress becomes a completed result without invented percentages',async()=>{
 let step=0;const states=[{...expansion,state:'queued',run:{id:'r1',state:'queued',progress_current:0,progress_total:0}},{...expansion,state:'running',usage:{web:2},run:{id:'r1',state:'running',progress_current:1,progress_total:4,started_at:'2026-11-09T10:00:00Z'}},{...expansion,state:'succeeded',run:{id:'r1',state:'succeeded',progress_current:4,progress_total:4,finished_at:'2026-11-09T10:01:00Z'}}];
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':()=>({expansion:states[Math.min(step++,2)],candidates:step>1?[candidate]:[]})},'http://localhost/domain-expansion/?id=e1');const timers=expansionTimers(dom);await api.render(root);
 const status=root.querySelector('[data-expansion-activity]');assert.ok(status);assert.match(status.textContent,/Expansion queued/);assert.ok(status.querySelector('a[href="/runs/?id=r1"]'));
 await timers.tick();assert.match(status.textContent,/Expansion running/);assert.match(status.textContent,/1 seed processed/);assert.match(status.textContent,/2 requests used/);assert.ok(status.querySelector('progress'));assert.equal(status.querySelector('progress').hasAttribute('value'),false);
 await timers.tick();assert.match(status.textContent,/Expansion completed/);assert.match(status.textContent,/1 candidate/);assert.equal(status.querySelector('progress'),null);assert.equal(timers.pending.size,0);assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});

test('a lost progress refresh retries and preserves the last known state and saved candidates',async()=>{
 let reads=0;const running={...expansion,state:'running',run:{id:'r1',state:'running',progress_current:1,progress_total:2}};
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':()=>{if(++reads===2)throw new Error('network offline');return {expansion:reads>2?expansion:running,candidates:[candidate]}}},'http://localhost/domain-expansion/?id=e1');const timers=expansionTimers(dom);await api.render(root);await timers.tick();
 const status=root.querySelector('[data-expansion-activity]');assert.match(status.textContent,/retrying/i);assert.match(status.textContent,/running/i);assert.match(root.querySelector('[data-candidate-list]').textContent,/different-brand.net/);assert.equal(timers.pending.size,1);
 await timers.tick();assert.match(status.textContent,/Expansion completed/);assert.doesNotMatch(status.textContent,/retrying/i);assert.equal(calls.filter(c=>c.body).length,0);api.dispose();assert.equal(timers.pending.size,0);dom.window.close();
});

test('child expansion and terminal failure have visible safe status feedback',async()=>{
 let finish;const deferred=new Promise(r=>{finish=r});const failed={...expansion,id:'e2',state:'failed',run:{id:'r2',state:'failed',error:'Source error <img src=x>'}};
 const {dom,root,api}=setup({'/api/assets/expansions/e1/expand':()=>deferred},'http://localhost/domain-expansion/?id=e1');await api.render(root);button(root,'Expand from here').click();await settle();assert.match(root.querySelector('[data-expansion-activity]').textContent,/Starting expansion/);
 finish({expansion:failed,candidates:[]});await settle();const status=root.querySelector('[data-expansion-activity]');assert.match(status.textContent,/Expansion failed/);assert.match(status.textContent,/Source error <img src=x>/);assert.equal(status.querySelector('img'),null);assert.equal(status.querySelector('progress'),null);dom.window.close();
});

test('a previous job refresh cannot replace a new expansion submission',async()=>{
 let reads=0,finishOld,finishChild;const old=new Promise(r=>{finishOld=r}),child=new Promise(r=>{finishChild=r});
 const {dom,root,api}=setup({'/api/assets/expansions/e1':()=>++reads===1?{expansion:{...expansion,state:'running'},candidates:[candidate]}:old,'/api/assets/expansions/e1/expand':()=>child},'http://localhost/domain-expansion/?id=e1');const timers=expansionTimers(dom);await api.render(root);const [id,fn]=timers.pending.entries().next().value;timers.pending.delete(id);const polling=fn();await settle();button(root,'Expand from here').click();await settle();finishOld({expansion,candidates:[candidate]});await polling;await settle();assert.match(root.querySelector('[data-expansion-activity]').textContent,/Starting expansion/);finishChild({expansion:{...expansion,id:'e2',state:'queued'},candidates:[]});await settle();assert.equal(new URL(dom.window.location.href).searchParams.get('id'),'e2');dom.window.close();
});

test('an accepted expansion keeps its permanent ID when the first status read fails',async()=>{
 let reads=0;const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{id:'e2'}:{results:[]},'/api/assets/expansions/e2':()=>{if(++reads===1)throw Error('status unavailable');return {expansion:{...expansion,id:'e2',state:'queued'},candidates:[]}}});const timers=expansionTimers(dom);await api.render(root);value(root,'domains','fixture.example');button(root,'Start expansion').click();await settle();assert.equal(new URL(dom.window.location.href).searchParams.get('id'),'e2');assert.match(root.querySelector('[data-expansion-activity]').textContent,/saved|submitted/i);assert.doesNotMatch(root.querySelector('[data-expansion-activity]').textContent,/could not start/i);await timers.tick();assert.match(root.querySelector('[data-expansion-activity]').textContent,/Expansion queued/);assert.equal(calls.filter(c=>c.body).length,1);dom.window.close();
});

test('failed submission is visible and never retried as a new research request',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{error:'Unable to queue research'}:{results:[]}});const timers=expansionTimers(dom);await api.render(root);value(root,'domains','fixture.example');button(root,'Start expansion').click();await settle();const status=root.querySelector('[data-expansion-activity]');assert.match(status.textContent,/Expansion could not start/);assert.match(status.textContent,/Unable to queue research/);assert.equal(status.querySelector('progress'),null);assert.equal(button(root,'Start expansion').disabled,false);assert.equal(timers.pending.size,0);assert.equal(calls.filter(c=>c.body).length,1);dom.window.close();
});

test('late same-expansion refresh errors cannot overwrite a newer completed snapshot',async()=>{
 let reads=0,rejectOld;const pending=new Promise((_,reject)=>{rejectOld=reject});
 const {dom,root,api}=setup({'/api/assets/expansions/e1':()=>{reads++;if(reads===2)return pending;return {expansion:reads===1?{...expansion,state:'running'}:expansion,candidates:[candidate]}}},'http://localhost/domain-expansion/?id=e1');const timers=expansionTimers(dom);await api.render(root);const [id,fn]=timers.pending.entries().next().value;timers.pending.delete(id);const polling=fn();await settle();button(root,'Refresh results').click();await settle();assert.match(root.querySelector('[data-expansion-activity]').textContent,/Expansion completed/);rejectOld(Error('stale error'));await polling;await settle();assert.doesNotMatch(root.querySelector('[data-expansion-activity]').textContent,/Retrying/i);assert.equal(timers.pending.size,0);dom.window.close();
});

test('manual refresh failure keeps automatic status polling alive',async()=>{
 let reads=0;const {dom,root,api}=setup({'/api/assets/expansions/e1':()=>{if(++reads===2)throw Error('offline');return {expansion:reads>2?expansion:{...expansion,state:'running'},candidates:[candidate]}}},'http://localhost/domain-expansion/?id=e1');const timers=expansionTimers(dom);await api.render(root);button(root,'Refresh results').click();await settle();assert.match(root.querySelector('[data-expansion-activity]').textContent,/Retrying automatically/i);assert.equal(timers.pending.size,1);await timers.tick();assert.match(root.querySelector('[data-expansion-activity]').textContent,/Expansion completed/);dom.window.close();
});

test('lost submission response is unconfirmed and checking history never resubmits research',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>{if(c.body)throw new TypeError('Connection lost after submission');return {results:[]}}});const timers=expansionTimers(dom);await api.render(root);value(root,'domains','fixture.example');button(root,'Start expansion').click();await settle();const status=root.querySelector('[data-expansion-activity]');assert.match(status.textContent,/unconfirmed/i);assert.doesNotMatch(status.textContent,/could not start/i);assert.match(status.textContent,/may have.*accepted/i);assert.ok(button(status,'Check saved expansions'));button(status,'Check saved expansions').click();await settle();assert.equal(calls.filter(c=>c.body).length,1);assert.equal(timers.pending.size,0);dom.window.close();
});

test('local validation is visibly rejected without claiming an unconfirmed server start',async()=>{
 const {dom,root,api,calls}=setup();await api.render(root);button(root,'Start expansion').click();await settle();const status=root.querySelector('[data-expansion-activity]');assert.match(status.textContent,/Expansion could not start/);assert.match(status.textContent,/Enter a company, domain or infrastructure seed, or choose an organization/);assert.doesNotMatch(status.textContent,/unconfirmed|may have accepted/i);assert.equal(calls.filter(c=>c.body).length,0);assert.equal(button(status,'Check saved expansions'),undefined);dom.window.close();
});

test('expansion separates saved-result quota, DNS activity and provider budgets and accepts network seeds',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);
 for(const source of ['reverse_dns','passive_dns','infrastructure','brute','alterations'])assert.ok(root.querySelector('[name=source_'+source+']'),source);
 assert.equal(root.querySelector('[name=max_candidates]').value,'0');assert.equal(root.querySelector('[name=max_depth]').max,'10');
 assert.equal(root.querySelector('[name=budget_dns]').value,'5000');assert.equal(root.querySelector('[name=budget_netlas]').value,'0');
 value(root,'ips','192.0.2.5\n2001:db8::5');value(root,'networks','192.0.2.0/24');value(root,'asns','AS64500');
 value(root,'runtime_seconds','3600');value(root,'dns_rate','12');value(root,'wordlist','portal\nvpn');
 root.querySelector('[name=source_reverse_dns]').checked=true;root.querySelector('[name=source_brute]').checked=true;
 button(root,'Start expansion').click();await settle();
 const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions')?.body;assert.ok(sent,'IP-only seed submission');
 assert.deepEqual(sent.ips,['192.0.2.5','2001:db8::5']);assert.deepEqual(sent.networks,['192.0.2.0/24']);assert.deepEqual(sent.asns,['AS64500']);
 assert.equal(sent.max_candidates,0);assert.equal(sent.runtime_seconds,3600);assert.equal(sent.dns_rate,12);assert.deepEqual(sent.wordlist,['portal','vpn']);
 assert.equal(sent.budgets.dns,5000);assert.equal(sent.budgets.netlas,0);assert.match(root.textContent,/storage quota/i);dom.window.close();
});

test('server pagination uses database totals, remote filters and page-scoped selection',async()=>{
 const make=c=>{const page=Number(c.url.searchParams.get('page')||1),service=c.url.searchParams.get('category')==='services';return {expansion,candidates:[{...candidate,id:'page-'+page,domain:(service?'cdn':'host')+page+'.fixture.example',category:service?'services':'domains',relation:service?'service_provider':'subdomain'}],count:service?500:1200,counts:{domains:1200,related:0,services:500,references:0,lookalikes:0,total:1700},page,page_size:100,pages:service?5:12}};
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':make},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 assert.match(root.querySelector('[data-expansion-activity]').textContent,/1700 candidates saved/);assert.match(root.querySelector('[data-discovery-counts]').textContent,/1200 domain names/);
 selected(root);button(root,'Next page').click();await settle();assert.match(root.querySelector('[data-candidate-list]').textContent,/host2.fixture.example/);assert.equal(root.querySelector('input[name=candidate_id]').checked,false);
 assert.equal(calls.at(-1).url.searchParams.get('page'),'2');assert.match(root.querySelector('[data-candidate-pagination]').textContent,/Page 2 of 12/);
 value(root,'candidate_category','services').dispatchEvent(new dom.window.Event('change'));await settle();
 const filtered=calls.filter(c=>c.url.pathname==='/api/assets/expansions/e1').at(-1);assert.equal(filtered.url.searchParams.get('page'),'1');assert.equal(filtered.url.searchParams.get('category'),'services');
 assert.match(root.querySelector('[data-candidate-list]').textContent,/cdn1.fixture.example/);assert.equal(button(root,'Expand from here'),undefined);
 value(root,'candidate_query','brand');await new Promise(r=>setTimeout(r,300));assert.equal(calls.filter(c=>c.url.pathname==='/api/assets/expansions/e1').at(-1).url.searchParams.get('q'),'brand');dom.window.close();
});

test('paused expansion shows coverage and resumes only with explicit cumulative limits',async()=>{
 const paused={...expansion,state:'paused',resumable:true,max_candidates:100,runtime_seconds:1800,dns_rate:20,usage:{public_ct:20,dns:80},budgets:{public_ct:20,dns:5000,netlas:0},checkpoint_summary:{processed:3,pending_seeds:25,current_source:'certspotter',reason:'public_ct request budget reached'},coverage:[{source:'certspotter',kind:'domain',seed:'fixture.example',state:'paused',candidates:200,requests:20,pages:20,notes:['Public CT budget reached <img src=x onerror=alert(1)>']} ]};
 let resumed=false;const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':()=>({expansion:resumed?{...paused,state:'queued',resumable:false}:paused,candidates:[candidate],count:1,counts:{domains:0,related:1,services:0,references:0,lookalikes:0,total:1},page:1,page_size:100,pages:1}),'/api/assets/expansions/e1/resume':()=>{resumed=true;return {expansion:{...paused,state:'queued',resumable:false},candidates:[candidate],count:1,page:1,page_size:100,pages:1}}},'http://localhost/domain-expansion/?id=e1');const timers=expansionTimers(dom);await api.render(root);
 assert.match(root.querySelector('[data-expansion-activity]').textContent,/Expansion paused/);assert.match(root.querySelector('[data-expansion-activity]').textContent,/public_ct request budget reached/);assert.equal(timers.pending.size,0);
 const coverage=root.querySelector('[data-expansion-coverage]');assert.ok(coverage);assert.match(coverage.textContent,/certspotter/);assert.match(coverage.textContent,/200/);assert.equal(coverage.querySelector('img'),null);
 assert.equal(calls.filter(c=>c.body).length,0);assert.equal(root.querySelector('[name=resume_budget_public_ct]').value,'20');
 value(root,'resume_budget_public_ct','40');value(root,'resume_max_candidates','0');value(root,'resume_runtime_seconds','3600');button(root,'Resume expansion').click();await settle();
 const resume=calls.find(c=>c.url.pathname.endsWith('/resume'));assert.ok(resume);assert.equal(resume.body.budgets.public_ct,40);assert.equal(resume.body.budgets.dns,5000);assert.equal(resume.body.budgets.netlas,0);assert.equal(resume.body.max_candidates,0);assert.equal(resume.body.runtime_seconds,3600);
 assert.match(root.querySelector('[data-expansion-activity]').textContent,/Expansion queued/);assert.equal(timers.pending.size,1);dom.window.close();
});

test('a resumed request with uncertain delivery never repeats automatically',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,state:'paused',resumable:true,budgets:{netlas:0},usage:{netlas:0}},candidates:[]},'/api/assets/expansions/e1/resume':()=>{throw new Error('transport interrupted')}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 button(root,'Resume expansion').click();await settle();assert.equal(calls.filter(c=>c.url.pathname.endsWith('/resume')).length,1);assert.match(root.textContent,/could not confirm|uncertain|check.*status/i);assert.equal(calls.filter(c=>c.body).length,1);dom.window.close();
});

test('network-only seeds enable matching default techniques while explicit source choices persist',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);
 value(root,'asns','AS64500');assert.equal(root.querySelector('[name=source_infrastructure]').checked,true);assert.equal(root.querySelector('[name=source_reverse_dns]').checked,true);
 const reverse=root.querySelector('[name=source_reverse_dns]');reverse.checked=false;reverse.dispatchEvent(new dom.window.Event('change',{bubbles:true}));value(root,'networks','192.0.2.0/24');assert.equal(reverse.checked,false);
 button(root,'Start expansion').click();await settle();const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions')?.body;assert.ok(sent);assert.ok(sent.sources.includes('infrastructure'));assert.equal(sent.sources.includes('netlas'),false);dom.window.close();
});

test('paginated mutation results are reloaded using the selected server filters',async()=>{
 let cancelled=false;const domain={...candidate,category:'domains',domain:'api.fixture.example',relation:'subdomain'}, service={...candidate,id:'service',domain:'thirdparty.example',category:'services',relation:'service_provider'};
 const snapshot=()=>({...expansion,state:cancelled?'cancelled':'running'}), counts={domains:1,related:0,services:1,references:0,lookalikes:0,total:2};
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':()=>({expansion:snapshot(),candidates:[domain],count:1,counts,page:1,page_size:100,pages:1}),'/api/assets/expansions/e1/cancel':()=>{cancelled=true;return {expansion:snapshot(),candidates:[domain,service],count:2,counts,page:1,page_size:100,pages:1}}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 button(root,'Cancel expansion').click();await settle();assert.equal(calls.filter(c=>c.url.pathname==='/api/assets/expansions/e1').length,2);assert.equal(root.querySelector('[name=candidate_category]').value,'discovery');assert.equal(root.querySelector('[data-candidate-list]').textContent.includes('thirdparty.example'),false);dom.window.close();
});

test('resume validation failures from the server remain visible without automatic retries',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,state:'paused',resumable:true,runtime_seconds:1800,budgets:{netlas:0},usage:{netlas:0}},candidates:[]},'/api/assets/expansions/e1/resume':{error:'Runtime allowance has already been used; increase runtime_seconds',_status:400}},'http://localhost/domain-expansion/?id=e1');await api.render(root);button(root,'Resume expansion').click();await settle();assert.match(root.querySelector('[data-expansion-resume]').textContent,/Runtime allowance has already been used/);assert.equal(calls.filter(c=>c.url.pathname.endsWith('/resume')).length,1);assert.match(root.querySelector('[data-expansion-activity]').textContent,/Expansion paused/);dom.window.close();
});

test('web research has an explicit result count separate from request count and Demo samples one',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);assert.equal(root.querySelector('[name=web_results]')?.value,'20');assert.equal(root.querySelector('[name=web_results]').max,'100');value(root,'companies','Example Holdings');value(root,'web_results','75');root.querySelector('[name=demo]').checked=true;button(root,'Start expansion').click();await settle();const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions')?.body;assert.equal(sent.web_results,1);dom.window.close();
});

test('the domain seed field accepts the backend 1000-seed allowance instead of the old 60-seed cap',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);value(root,'domains',Array.from({length:100},(_,i)=>'host'+i+'.fixture.example').join('\n'));button(root,'Start expansion').click();await settle();const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions')?.body;assert.ok(sent);assert.equal(sent.domains.length,100);assert.ok(Number(root.querySelector('[name=domains]').maxLength)>=254000);dom.window.close();
});

test('older expansion history pages remain reachable and organization filters are preserved',async()=>{
 const historyPage=call=>{const page=Number(call.url.searchParams.get('page')||1);return {results:[{...expansion,id:page===1?'recent-expansion':'older-expansion',organization_name:page===1?'Recent saved research':'Older saved research'}],count:101,page,page_size:100,pages:2}};
 const {dom,root,api,calls}=setup({'/api/assets/expansions':historyPage},'http://localhost/domain-expansion/?organization_id=org-1');await api.render(root);
 const history=root.querySelector('[data-expansion-history]');assert.match(history.textContent,/101 saved expansions/);assert.match(history.textContent,/Page 1 of 2/);assert.equal(button(history,'Previous history page').disabled,true);
 button(history,'Next history page').click();await settle();assert.match(history.textContent,/Older saved research/);assert.match(history.textContent,/Page 2 of 2/);assert.equal(button(history,'Next history page').disabled,true);
 const second=calls.filter(c=>c.url.pathname==='/api/assets/expansions').at(-1);assert.equal(second.url.searchParams.get('organization_id'),'org-1');assert.equal(second.url.searchParams.get('page'),'2');assert.equal(second.url.searchParams.get('page_size'),'100');
 button(history,'Previous history page').click();await settle();assert.match(history.textContent,/Recent saved research/);assert.equal(calls.filter(c=>c.body).length,0);dom.window.close();
});

test('infrastructure leads have readable evidence and focused address network and ASN handoffs',async()=>{
 const leads=[{kind:'ip',value:'192.0.2.5',relation:'observed_address',evidence:[{source:'dns',signal:'dns_a',excerpt:'Observed A answer <img src=x onerror=alert(1)>',via:'example.com'}]},{kind:'network',value:'192.0.2.0/24',relation:'announced_prefix',evidence:[{source:'ripestat',signal:'routing_record',excerpt:'Public routing record for this prefix',url:'https://stat.ripe.net/'}]},{kind:'asn',value:'64500',relation:'origin_asn',evidence:[{source:'ripestat',signal:'origin_asn',excerpt:'Routing origin, not proof of ownership'}]}];
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,leads},candidates:[]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 const section=root.querySelector('[data-infrastructure-leads]');assert.ok(section);assert.equal(section.open,false);assert.match(section.textContent,/Infrastructure leads/);assert.match(section.textContent,/192\.0\.2\.5/);assert.match(section.textContent,/192\.0\.2\.0\/24/);assert.match(section.textContent,/64500/);assert.match(section.textContent,/Observed A answer <img/);assert.equal(section.querySelector('img'),null);
 for(const lead of leads){const handoff=section.querySelector('[data-infrastructure-expansion="'+lead.kind+'"]');assert.ok(handoff);const target=new URL(handoff.href);assert.equal(target.searchParams.get(lead.kind),lead.value);assert.equal(target.searchParams.get('organization_id'),'org-1');assert.equal(target.searchParams.get('focused'),'1');const next=setup({},target.href);await next.api.render(next.root);assert.equal(next.root.querySelector('[name='+({ip:'ips',network:'networks',asn:'asns'}[lead.kind])+']').value,lead.value);assert.equal(next.root.querySelector('[name=domains]').value,'');assert.equal(next.calls.some(c=>c.body),false);next.dom.window.close();}
 assert.equal(calls.some(c=>c.body),false);dom.window.close();
});

test('all infrastructure leads remain reachable through local pages without collection requests',async()=>{
 const leads=Array.from({length:205},(_,i)=>({kind:'ip',value:'192.0.2.'+i,relation:'passive_dns_address',evidence:[{source:'index',signal:'observed_address',excerpt:'Indexed address '+i}]}));
 const {dom,root,api,calls}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,leads},candidates:[]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);const section=root.querySelector('[data-infrastructure-leads]');assert.ok(section);assert.equal(section.querySelectorAll('tbody tr').length,100);assert.match(section.textContent,/205 infrastructure leads/);
 button(section,'Next leads page').click();assert.match(section.textContent,/Page 2 of 3/);assert.match(section.textContent,/192\.0\.2\.100/);button(section,'Next leads page').click();assert.equal(section.querySelectorAll('tbody tr').length,5);assert.match(section.textContent,/192\.0\.2\.204/);assert.equal(button(section,'Next leads page').disabled,true);assert.equal(calls.some(c=>c.body),false);dom.window.close();
});
test('candidate inspector uses saved evidence without expanding or accepting it',async()=>{const {dom,root,api,calls}=setup({},'http://localhost/domain-expansion/?id=e1');let ref;dom.window.LeakScopeInspector={open:r=>ref=r};await api.render(root);const inspect=button(root,'Inspect');assert.ok(inspect);inspect.click();assert.equal(ref.type,'candidate');assert.equal(ref.expansion_id,'e1');assert.equal(ref.record.domain,candidate.domain);assert.equal(calls.filter(c=>c.body).length,0);dom.window.close()});
test('Common Crawl is opt-in and shows an archive and request preview without collecting',async()=>{const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});await api.render(root);const source=root.querySelector('[name=source_commoncrawl]');assert.ok(source);assert.equal(source.checked,false);source.checked=true;source.dispatchEvent(new dom.window.Event('change',{bubbles:true}));value(root,'domains','example.com');value(root,'commoncrawl_index','CC-MAIN-2026-39');assert.match(root.textContent,/metadata.*page|page.*metadata/i);assert.equal(calls.filter(c=>c.body).length,0);button(root,'Start expansion').click();await settle();const sent=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions').body;assert.ok(sent.sources.includes('commoncrawl'));assert.equal(sent.commoncrawl_index,'CC-MAIN-2026-39');assert.ok(sent.budgets.domain_api>0);dom.window.close()});

test('Wayback is opt-in and retains its shared public-index budget when selected alone', async () => {
 const {dom, root, api, calls} = setup({'/api/assets/expansions': call => call.body ? {expansion, candidates: []} : {results: []}});
 await api.render(root);
 const source = root.querySelector('[name=source_wayback]');
 assert.ok(source);
 assert.equal(source.checked, false);
 assert.equal(calls.filter(call => call.body).length, 0);
 for (const box of root.querySelectorAll('input[name^=source_]')) box.checked = false;
 source.checked = true;
 source.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
 value(root, 'domains', 'example.com');
 button(root, 'Start expansion').click();
 await settle();
 const sent = calls.find(call => call.body && call.url.pathname === '/api/assets/expansions').body;
 assert.deepEqual(sent.sources, ['wayback']);
 assert.ok(sent.budgets.domain_api > 0);
 dom.window.close();
});

test('Netlas DNS history is explicit, submits snapshot dates and preserves its shared budget',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions':c=>c.body?{expansion,candidates:[]}:{results:[]}});
 await api.render(root);
 assert.equal(calls.some(c=>c.url.pathname.endsWith('/netlas-indices')),false);
 const source=root.querySelector('[name=source_netlas_dns]');assert.ok(source);source.checked=true;source.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.equal(root.querySelector('[data-netlas-dns-options]').hidden,false);
 value(root,'domains','fixture.example');value(root,'netlas_indices','2024-01-01\n2023-01-01');value(root,'budget_netlas','2');
 button(root,'Start expansion').click();await settle();
 const body=calls.find(c=>c.body&&c.url.pathname==='/api/assets/expansions').body;
 assert.ok(body.sources.includes('netlas_dns'));assert.deepEqual(body.netlas_indices,['2024-01-01','2023-01-01']);assert.equal(body.budgets.netlas,2);
 dom.window.close();
});
test('Netlas snapshot metadata loads only on click and selection fills dates',async()=>{
 const {dom,root,api,calls}=setup({'/api/assets/expansions/netlas-indices':{indices:[{label:'2024-01-01',state:'ready'}],requests:1}});
 await api.render(root);button(root,'Load available DNS snapshots').click();await settle();
 assert.equal(calls.filter(c=>c.url.pathname.endsWith('/netlas-indices')&&c.body).length,1);
 const choice=root.querySelector('[name=netlas_snapshot]');assert.ok(choice);choice.checked=true;choice.dispatchEvent(new dom.window.Event('change'));
 assert.equal(root.querySelector('[name=netlas_indices]').value,'2024-01-01');dom.window.close();
});
test('Netlas dated DNS records are formatted in candidate evidence without interpreting markup',async()=>{
 const record={...candidate,relation:'historical',evidence:[{source:'netlas_dns',signal:'historical_dns_record',index_label:'2024-01-01',observed_at:null,dns_records:{A:['192.0.2.2'],TXT:['<img src=x onerror=alert(1)>']},historical:true}]};
 const {dom,root,api}=setup({'/api/assets/expansions/e1':{expansion:{...expansion,netlas_indices:['2024-01-01']},candidates:[record]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 assert.match(root.textContent,/Snapshot: 2024-01-01/);assert.match(root.textContent,/192\.0\.2\.2/);assert.match(root.textContent,/Indexed time unknown/);assert.equal(root.querySelector('img'),null);dom.window.close();
});
test('Netlas snapshots without returned DNS values disclose that limit',async()=>{
 const empty={...candidate,evidence:[{source:'netlas_dns',index_label:'2024-01-01',dns_records:{},historical:true}]};
 const {dom,root,api}=setup({'/api/assets/expansions/e1':{expansion,candidates:[empty]}},'http://localhost/domain-expansion/?id=e1');await api.render(root);
 assert.match(root.textContent,/No DNS record values were returned/);dom.window.close();
});
