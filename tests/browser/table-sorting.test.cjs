const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const base=path.resolve(__dirname,'../../leakscope_app/static/js/leakscope');
function setup(url='http://localhost/assets/') {
 const dom=new JSDOM('<meta name="csrf-token" content="fixture"><main id="root"></main>',{url,runScripts:'outside-only'});
 for(const name of ['utils.js','tables.js'])if(fs.existsSync(path.join(base,name)))dom.window.eval(fs.readFileSync(path.join(base,name),'utf8'));
 return {dom,d:dom.window.document,T:dom.window.LeakScopeTables};
}
const settle=()=>new Promise(r=>setTimeout(r,20));
const values=table=>[...table.tBodies[0].rows].map(r=>r.cells[0].textContent);
function grid(d,rows) {
 const table=d.createElement('table');table.innerHTML='<thead><tr><th>Name</th><th>Count</th><th>Seen</th><th>Select</th><th>Actions</th></tr></thead><tbody></tbody>';
 for(const row of rows){const tr=d.createElement('tr');for(const value of row){const td=d.createElement('td');td.textContent=value??'—';tr.append(td);}table.tBodies[0].append(tr);}
 d.querySelector('#root').append(table);return table;
}
test('shared table helper sorts numeric and ISO dates, keeps missing values last both ways and preserves row controls',()=>{
 const {dom,d,T}=setup();assert.ok(T,'shared sorting helper is loaded');
 const table=grid(d,[['two','2','2026-12-01T00:00:00Z','',''],['ten','10','2026-01-01T00:00:00Z','',''],['none','','','','']]);
 const action=d.createElement('button');action.textContent='Inspect';let clicked=0;action.onclick=()=>clicked++;table.tBodies[0].rows[0].cells[4].append(action);
 T.enhance(table,{columns:[{key:'name'},{key:'count',type:'number'},{key:'seen',type:'date'},null,null]});
 assert.equal(table.querySelectorAll('thead button').length,3);
 table.querySelector('[data-sort-key=count]').click();assert.deepEqual(values(table),['two','ten','none']);
 table.querySelector('[data-sort-key=count]').click();assert.deepEqual(values(table),['ten','two','none']);
 table.querySelector('[data-sort-key=seen]').click();assert.deepEqual(values(table),['ten','two','none']);
 table.querySelector('[data-sort-key=seen]').click();assert.deepEqual(values(table),['two','ten','none']);action.click();assert.equal(clicked,1);dom.window.close();
});
test('sort uses explicit source values rather than rendered dates or hidden evidence text',()=>{
 const {dom,d,T}=setup();assert.ok(T);
 const table=grid(d,[['later','1','Jan 1, 01:00','',''],['earlier','1','Dec 31, 23:00','','']]);
 table.tBodies[0].rows[0].cells[2].dataset.sortValue='2026-01-01T01:00:00Z';table.tBodies[0].rows[1].cells[2].dataset.sortValue='2025-12-31T23:00:00Z';
 T.enhance(table,{columns:[{key:'name'},null,{key:'seen',type:'date'},null,null]});table.querySelector('[data-sort-key=seen]').click();assert.deepEqual(values(table),['earlier','later']);dom.window.close();
});
test('sorting a loaded dataset is stable and happens before caller pagination',()=>{
 const {dom,T}=setup();assert.ok(T);const columns=[{key:'count',type:'number',value:r=>r.count}],rows=Array.from({length:31},(_,i)=>({id:i,count:31-i}));
 const sorted=T.sort(rows,{key:'count',direction:'asc'},columns);assert.deepEqual(Array.from(sorted.slice(0,2),r=>r.id),[30,29]);assert.equal(rows[0].id,0);
 const ties=T.sort([{id:1,count:2},{id:2,count:2},{id:3,count:null}],{key:'count',direction:'desc'},columns);assert.deepEqual(Array.from(ties,r=>r.id),[1,2,3]);dom.window.close();
});
test('remote sorting is callback-only and exposes native keyboard button and aria-sort state',()=>{
 const {dom,d,T}=setup();assert.ok(T);const table=grid(d,[['z',2,'','',''],['a',1,'','','']]),state={key:'name',direction:'desc'};let requested;
 T.enhance(table,{columns:[{key:'name'},null,null,null,null],state,onSort:next=>requested={...next},scope:'All matching saved rows'});
 const button=table.querySelector('[data-sort-key=name]');assert.equal(button.tagName,'BUTTON');assert.equal(button.type,'button');assert.equal(button.closest('th').getAttribute('aria-sort'),'descending');button.focus();assert.equal(d.activeElement,button);button.click();assert.equal(requested.key,'name');assert.equal(requested.direction,'asc');assert.deepEqual(values(table),['z','a']);dom.window.close();
});
test('observations sorts server-side, resets page, preserves all filters and restores header focus',async()=>{
 const {dom,d}=setup('http://localhost/assets/?page=2&q=example&type=modbus&tag=review&provider=zoomeye&run=r1');const calls=[];
 dom.window.fetch=async value=>{const url=new URL(value,'http://localhost');calls.push(url);return {ok:true,json:async()=>url.pathname.endsWith('/catalog')?{results:[{key:'modbus',label:'Modbus'}],tools:{}}:{results:[{id:'e1',address:'192.0.2.1',port:502,service_type:'modbus',title:'Device',tags:['review'],observation_count:2,last_seen:'2026-01-01T00:00:00Z'}],count:60,pages:2,page:Number(url.searchParams.get('page'))}};};
 dom.window.eval(fs.readFileSync(require('node:path').resolve(__dirname,'../../leakscope_app/static/js/leakscope/list_toolbar.js'),'utf8'));dom.window.eval(fs.readFileSync(path.join(base,'operations.js'),'utf8'));await dom.window.LeakScopeOperations.render(d.querySelector('#root'),'assets');
 const sort=d.querySelector('[data-sort-key=observations]');assert.ok(sort,'observation count has a sortable header');sort.click();await settle();const query=calls.at(-1).searchParams;
 assert.equal(query.get('sort'),'observations');assert.equal(query.get('direction'),'asc');assert.equal(query.get('page'),'1');for(const [k,v]of Object.entries({q:'example',type:'modbus',tag:'review',provider:'zoomeye',run:'r1'}))assert.equal(query.get(k),v);
 assert.equal(new URL(dom.window.location.href).searchParams.get('sort'),'observations');assert.equal(d.activeElement?.dataset.sortKey,'observations');assert.equal(d.querySelector('th:first-child button'),null);assert.equal(d.querySelector('th:last-child button'),null);dom.window.close();
});
test('graph list sorting orders every loaded node before its 25-row page is selected',async()=>{
 const {dom,d}=setup('http://localhost/relationships/');const nodes=Array.from({length:31},(_,i)=>({id:'n'+i,kind:'domain',value:'host'+(31-i)+'.example',label:'host'+(31-i)+'.example'}));
 dom.window.fetch=async value=>({ok:true,json:async()=>String(value).includes('/graph')?{nodes,edges:[]}:{results:[]}});
 dom.window.eval(fs.readFileSync(path.join(base,'relationship_map.js'),'utf8'));dom.window.eval(fs.readFileSync(path.join(base,'relationships.js'),'utf8'));await dom.window.LeakScopeAssets.render(d.querySelector('#root'),'relationships');
 const list=d.querySelector('[data-list-id=graph-nodes]');const sort=list.querySelector('[data-sort-key=asset]');assert.ok(sort);sort.click();await settle();assert.match(list.querySelector('tbody tr').textContent,/host1\.example/);assert.equal(list.querySelectorAll('tbody tr').length,25);dom.window.close();
});
test('a saved local sort is reapplied when a polling table refreshes',()=>{
 const {dom,d,T}=setup();assert.ok(T);const state={key:'count',direction:'desc'},table=grid(d,[['two',2,'','',''],['ten',10,'','','']]);
 T.enhance(table,{columns:[{key:'name'},{key:'count',type:'number'},null,null,null],state});assert.deepEqual(values(table),['ten','two']);dom.window.close();
});
test('certificate sorts query all locally saved matches and preserve search fields',async()=>{
 const {dom,d}=setup('http://localhost/certificates/?q=example.com&mode=domain&watch=w1&page=3');const calls=[];
 dom.window.fetch=async value=>{const url=new URL(value,'http://localhost');calls.push(url);return {ok:true,json:async()=>({results:[{id:'c1',fingerprint:'abc',domains:['example.com'],issuer:{O:'Example'},watch_matches:[],last_seen:'2026-01-01T00:00:00Z'}],count:60,pages:3,page:Number(url.searchParams.get('page')),watches:[{id:'w1',name:'Example watch'}]})};};
 dom.window.eval(fs.readFileSync(path.join(base,'relationships.js'),'utf8'));await dom.window.LeakScopeAssets.render(d.querySelector('#root'),'certificates');const header=d.querySelector('[data-sort-key=watches]');assert.ok(header,'watch count sortable');header.click();await settle();const query=calls.at(-1).searchParams;
 assert.equal(query.get('page'),'1');assert.equal(query.get('sort'),'watches');assert.equal(query.get('q'),'example.com');assert.equal(query.get('watch'),'w1');assert.equal(d.activeElement.dataset.sortKey,'watches');dom.window.close();
});
test('cloud inventory uses global persisted sorting rather than sorting only its current page',async()=>{
 const {dom,d}=setup('http://localhost/cloud/?page=2&q=example&provider=aws&state=public');const calls=[];
 dom.window.fetch=async value=>{const url=new URL(value,'http://localhost');calls.push(url);const response=url.pathname.endsWith('/catalog')?{providers:[{key:'aws',label:'AWS',regions:[]}]}:url.pathname.endsWith('/resources')?{results:[{id:'c1',name:'Example',url:'https://example.com/',provider:'aws',kind:'storage',state:'public',last_checked:'2026-01-01T00:00:00Z',evidence:{}}],count:60}:{results:[]};return {ok:true,json:async()=>response};};
 dom.window.eval(fs.readFileSync(path.join(base,'cloud.js'),'utf8'));await dom.window.LeakScopeCloud.render(d.querySelector('#root'));const header=d.querySelector('[data-sort-key=last_checked]');assert.ok(header,'last checked has its own sortable column');header.click();await settle();const query=calls.filter(c=>c.pathname.endsWith('/resources')).at(-1).searchParams;
 assert.equal(query.get('page'),'1');assert.equal(query.get('sort'),'last_checked');assert.equal(query.get('q'),'example');assert.equal(query.get('provider'),'aws');assert.equal(query.get('state'),'public');assert.equal(d.activeElement.dataset.sortKey,'last_checked');dom.window.close();
});
test('a static matrix opts out of table enhancement',()=>{
 const {dom,d,T}=setup();const table=grid(d,[['z',2,'','','']]);table.dataset.sortable='false';T.enhance(table);assert.equal(table.querySelector('thead button'),null);dom.window.close();
});
test('refreshing a local sorted table does not steal focus from a different control',async()=>{
 const {dom,d,T}=setup();const state={key:null,direction:'asc'},columns=[{key:'name'},null,null,null,null],table=grid(d,[['z',2,'','',''],['a',1,'','','']]);T.enhance(table,{columns,state});table.querySelector('[data-sort-key=name]').click();
 const input=d.createElement('input');d.body.append(input);input.focus();const refreshed=grid(d,[['z',2,'','',''],['a',1,'','','']]);T.enhance(refreshed,{columns,state});await settle();assert.equal(d.activeElement,input);dom.window.close();
});
test('local formatting can use source-backed values without parsing pretty cell text',()=>{
 const {dom,d,T}=setup();const table=grid(d,[['ten','10 volts','','',''],['two','two volts','','','']]);
 T.enhance(table,{data:[{value:10},{value:2}],columns:[null,{key:'value',type:'number',value:r=>r.value},null,null,null]});table.querySelector('[data-sort-key=value]').click();assert.deepEqual(values(table),['two','ten']);dom.window.close();
});
function appShell(dom,d,page){
 d.querySelector('#root').id='page-root';d.body.dataset.page=page;
 for(const [tag,id]of [['span','breadcrumb-page'],['button','menu-toggle'],['aside','sidebar'],['button','close-run-dialog'],['dialog','run-dialog']]){const el=d.createElement(tag);el.id=id;d.body.append(el);}
 dom.window.eval(fs.readFileSync(path.join(base,'app.js'),'utf8'));
}
test('job history sorting moves rows from later pages into the first loaded page',async()=>{
 const {dom,d}=setup('http://localhost/runs/'),runs=Array.from({length:31},(_,i)=>({id:'r'+i,kind:'Job '+String(31-i).padStart(2,'0'),provider:'shodan',state:'succeeded',created_at:'2026-01-01T00:00:00Z'}));
 let calls=0;dom.window.fetch=async()=>{calls++;return {ok:true,json:async()=>({results:runs})};};appShell(dom,d,'runs');await settle();d.querySelector('[data-sort-key=operation]').click();await settle();assert.match(d.querySelector('tbody tr').textContent,/Job 01/);assert.equal(d.querySelectorAll('tbody tr').length,25);assert.equal(calls,1);assert.equal(d.activeElement.dataset.sortKey,'operation');dom.window.close();
});
test('Findings header sorting preserves server filters and starts from page one',async()=>{
 const {dom,d}=setup('http://localhost/findings/?q=example&type=elastic&provider=shodan&status=confirmed&page=3');const calls=[];
 dom.window.fetch=async value=>{const url=new URL(value,'http://localhost');calls.push(url);return {ok:true,json:async()=>url.pathname==='/api/types'?{results:[{key:'elastic',label:'Elasticsearch'}]}:{results:[{id:1,type:'elastic',label:'192.0.2.1:9200',provider:'shodan',status:'confirmed',created_at:'2026-01-01T00:00:00Z'}],count:90,pages:3}};};
 appShell(dom,d,'findings');await settle();const header=d.querySelector('[data-sort-key=provider]');assert.ok(header);header.click();await settle();const query=calls.at(-1).searchParams;assert.equal(query.get('sort'),'provider');assert.equal(query.get('page'),'1');for(const [k,v]of Object.entries({q:'example',type:'elastic',provider:'shodan',status:'confirmed'}))assert.equal(query.get(k),v);assert.equal(d.activeElement.dataset.sortKey,'provider');dom.window.close();
});
test('ICS points sort actual numeric values while retaining the saved protocol read results',async()=>{
 const {dom,d}=setup('http://localhost/ics/');dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
 const points=[{function:3,address:0,raw_value:10,value:10.2,label:'Higher'},{function:3,address:1,raw_value:2,value:2.15,label:'Lower'}];
 dom.window.fetch=async()=>({ok:true,json:async()=>({endpoint:{id:'e1',address:'192.0.2.1',port:502,service_type:'modbus'},supported_inspection:true,latest_inspection:{created_at:'2026-01-01T00:00:00Z',metadata:{points}}})});
 dom.window.eval(fs.readFileSync(path.join(base,'ics.js'),'utf8'));dom.window.LeakScopeICS.openInspection({type:'endpoint',id:'e1'});await settle();const button=d.querySelector('[data-sort-key=value]');assert.ok(button);button.click();assert.match(d.querySelector('table tbody tr').textContent,/Lower/);assert.equal(d.querySelector('table tbody tr').cells[3].textContent,'2.15');dom.window.close();
});
test('automatic column types recognize date words without misclassifying domain candidates',()=>{
 const {dom,T}=setup();const columns=T.defaults(['Domain candidate','Candidate URL','Source observed','Last collected','TTL']);assert.equal(columns[0].type,'text');assert.equal(columns[1].type,'text');assert.equal(columns[2].type,'date');assert.equal(columns[3].type,'date');assert.equal(columns[4].type,'number');dom.window.close();
});
test('a delayed remote redraw respects focus moved into a filter while it was loading',async()=>{
 const {dom,d,T}=setup(),state={key:null,direction:'asc'},columns=[{key:'name'},null,null,null,null],table=grid(d,[['z',2,'','','']]);T.enhance(table,{columns,state,onSort:()=>table.remove()});table.querySelector('button').click();
 const filter=d.createElement('input');d.body.append(filter);filter.focus();const next=grid(d,[['z',2,'','','']]);T.enhance(next,{columns,state,onSort:()=>{}});await settle();assert.equal(d.activeElement,filter);dom.window.close();
});
