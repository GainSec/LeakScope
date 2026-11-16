(function(){
  'use strict';
  const root=window;let active=null,sequence=0;
  const text=value=>value==null?'Unknown':typeof value==='object'?JSON.stringify(value):String(value);
  function el(tag,attrs={},...children){const n=document.createElement(tag);for(const [key,value]of Object.entries(attrs)){if(value==null||value===false)continue;if(key==='class')n.className=value;else if(key.startsWith('on'))n.addEventListener(key.slice(2),value);else n.setAttribute(key,text(value));}for(const child of children.flat(Infinity)){if(child!=null&&child!==false)n.append(child instanceof Node?child:document.createTextNode(text(child)));}return n;}
  const button=(label,onclick,attrs={})=>el('button',{type:'button',class:'btn btn-small',onclick,...attrs},label);
  const date=value=>value&&!Number.isNaN(Date.parse(value))?new Date(value).toLocaleString():'Unknown';
  const dataView=(value,label)=>root.LeakScopeData?.viewer(value,{label})||el('pre',{class:'code-box'},text(value));
  const empty=message=>el('p',{class:'inspector-empty'},message);
  const section=(label,...body)=>el('section',{class:'inspector-section'},el('h3',{},label),...body);
  function link(label,href){return el('a',{class:'btn btn-small',href},label);}
  // A stored placeholder such as a provider's "auto" is not a technology; show what a saved provider banner reports instead, and say so.
  function technology(summary){const hint=summary.protocol_hint,stored=summary.service_type;if(hint&&summary.effective_service_type&&summary.effective_service_type!==stored)return summary.effective_service_type.toUpperCase()+' (provider reported from saved response text; stored type: '+(stored||'none')+')';return stored||'Unclassified';}
  function diffValue(value,label){return value!==null&&typeof value==='object'?dataView(value,label):el('span',{class:'inspector-diff-value'},value==null?'No value':value===''?'Empty text':typeof value==='boolean'?(value?'True':'False'):text(value));}
  function fields(value){const dl=el('dl',{class:'inspector-facts'});for(const [key,v]of Object.entries(value)){if(v==null||v==='')continue;dl.append(el('div',{},el('dt',{},key),el('dd',{},text(v))));}return dl;}
  function provenance(row={}){
    const p=row.provenance||{};const source=p.label||(['shodan','zoomeye','netlas'].includes(row.provider)?'Provider reported':row.provider?'Recorded source':'Saved evidence');
    const observed=p.observed_at??row.observed_at;const collected=p.collected_at??row.collected_at??row.last_collected_at??row.first_collected_at;
    const n=el('div',{class:'evidence-provenance'},el('span',{class:'provenance-badge'},source),el('span',{class:'provenance-source'},row.provider||row.source||''),
      el('span',{},'Source observed ',el('time',{datetime:observed},date(observed))),el('span',{},'Collected ',el('time',{datetime:collected},date(collected))));
    const latest=row.last_observed_at;if(latest&&latest!==observed)n.append(el('span',{},'Latest source report ',el('time',{datetime:latest},date(latest))));
    const ageTime=latest||observed;const parsed=Date.parse(ageTime);const age=ageTime&&Number.isFinite(parsed)?Math.floor((Date.now()-parsed)/86400000):null;
    n.append(el('span',{class:'provenance-age'},'Source age: '+(age==null?'Unknown':age<0?'Future source timestamp':age===0?'Under 24 hours':age===1?'1 day':age+' days')));
    if(p.basis)n.append(el('small',{class:'muted'},p.basis));
    return n;
  }
  function artifactURL(value){try{const url=new URL(value,location.href);return url.origin===location.origin&&/^\/api\/operations\/artifacts\/[\w-]+\/?$/.test(url.pathname)&&!url.search&&!url.hash?url.pathname:null;}catch(_){return null;}}
  function normalise(ref,data){
    if(ref.type==='endpoint')return {title:data.endpoint?.title||data.endpoint?.address||'Observation',subtitle:[data.endpoint?.address,data.endpoint?.port].filter(x=>x!=null).join(':'),summary:data.endpoint||{},observations:data.observations||[],artifacts:data.artifacts||[],attribution:data.attribution||null,captureStatus:data.capture_status||null,href:'/assets/?endpoint='+encodeURIComponent(ref.id)};
    if(ref.type==='evidence') {
      const proof=data.evidence||{};
      const source=text(proof.source||'Unknown source').replaceAll('_',' ').replaceAll(':',' / ');
      return {
        title:'Saved evidence', subtitle:source.charAt(0).toUpperCase()+source.slice(1),
        summary:{Source:source,Observed:date(proof.observed_at),'Last collected':date(proof.last_collected_at),'Evidence expires':date(proof.expires_at)},
        evidence:proof.data||{},observations:[],artifacts:[],
        href:proof.subject?'/relationships/?root='+encodeURIComponent(proof.subject):'/relationships/'
      };
    }
    if(ref.type==='certificate'){const c=data.certificate||{};return {title:'Certificate',subtitle:c.fingerprint||ref.id,summary:{'Certificate names':(c.domains||[]).join(', '),'Issuer':c.issuer,'Valid from':date(c.not_before),'Valid until':date(c.not_after),'Observed':date(c.observed_at),'First collected':date(c.first_seen),'Last collected':date(c.last_seen)},evidence:data.data||c.data||{},observations:[],artifacts:[],href:'/certificates/?id='+encodeURIComponent(ref.id)};}
    if(ref.type==='candidate'){const c=ref.record||{};return {title:c.domain||'Domain candidate',subtitle:'Saved discovery lead · ownership requires review',summary:{'Domain':c.domain,'Relationship':c.relation,'Confidence':c.confidence,'Review state':c.status,'DNS state':c.dns_status},evidence:c.evidence||[],observations:[],artifacts:[],href:'/domain-expansion/?id='+encodeURIComponent(ref.expansion_id||'')+'&view=results'};}
    if(ref.type==='cloud'){const c=ref.record||{};return {title:c.name||c.url||'Cloud resource',subtitle:c.provider||'Saved cloud discovery',summary:{'Provider':c.provider,'Kind':c.kind,'Region':c.region,'State':c.state,'Last checked':date(c.last_checked)},evidence:c.evidence||{},observations:[],artifacts:[],href:'/cloud/'};}
    const finding=data.finding||data;return {title:finding.label||finding.title||'Saved finding',subtitle:[finding.type||ref.type,finding.provider].filter(Boolean).join(' · '),summary:{'Technology':finding.type||ref.type,'Provider':finding.provider||'Unknown','Review state':finding.status,'Saved':date(finding.created_at),'Endpoint':[finding.ip,finding.port].filter(Boolean).join(':')},evidence:data.fields||{},observations:[],artifacts:[],href:'/findings/'+encodeURIComponent(ref.type)+'/'+encodeURIComponent(ref.id)+'/'};
  }
  function dispose(){active?.close();}
  async function open(reference,options={}){
    dispose();const ref={...reference},id=++sequence,previous=document.activeElement,controller=new AbortController();
    if(!ref.type||ref.id==null)throw new Error('A saved result reference is required');
    const dialog=el('dialog',{class:'investigation-inspector','aria-labelledby':'investigation-title-'+id}),body=el('div',{class:'inspector-content'},empty('Loading saved evidence…'));
    const heading=el('h2',{id:'investigation-title-'+id},'Saved result'),subtitle=el('p',{class:'small muted inspector-subtitle'}),tabs=el('div',{class:'investigation-tabs',role:'tablist','aria-label':'Investigation views'});
    function close(){if(controller.signal.aborted)return;controller.abort();if(dialog.open)dialog.close();dialog.remove();if(active?.dialog===dialog)active=null;if(previous?.isConnected)previous.focus();}
    active={dialog,close};dialog.append(el('header',{class:'investigation-heading'},el('div',{},el('span',{class:'eyebrow'},'SAVED EVIDENCE'),heading,subtitle),button('Close inspector',close,{class:'icon-button inspector-close','aria-label':'Close inspector'})),tabs,body);
    document.body.append(dialog);dialog.addEventListener('cancel',event=>{event.preventDefault();close()});dialog.addEventListener('close',close);const leave=()=>close();addEventListener('pagehide',leave,{once:true});controller.signal.addEventListener('abort',()=>removeEventListener('pagehide',leave),{once:true});dialog.showModal();
    async function get(url){const response=await fetch(url,{method:'GET',credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}});const result=await response.json();if(!response.ok)throw new Error(result.error||'Saved evidence could not be loaded');return result;}
    try{
      let data={};if(ref.type==='endpoint')data=await get('/api/operations/endpoints/'+encodeURIComponent(ref.id));else if(ref.type==='evidence')data=await get('/api/assets/evidence/'+encodeURIComponent(ref.id));else if(ref.type==='certificate')data=await get('/api/assets/certificates/'+encodeURIComponent(ref.id));else if(!['cloud','candidate'].includes(ref.type))data=await get('/api/findings/'+encodeURIComponent(ref.type)+'/'+encodeURIComponent(ref.id));
      if(controller.signal.aborted)return;
      const view=normalise(ref,data);heading.textContent=view.title;subtitle.textContent=view.subtitle;
      const panes=new Map(),tabNodes=new Map();let comparisonVersion=0;
      // This one saved result as a file: everything loaded for it (JSON), or its facts as readable text or a two-column CSV.
      const Export=root.LeakScopeExport;
      if(Export?.save){
        const flat=value=>value==null?'':typeof value==='object'?JSON.stringify(value):String(value);
        const facts=()=>Object.entries(ref.type==='endpoint'?{Address:view.summary.address,Port:view.summary.port,Transport:view.summary.transport,Technology:technology(view.summary),Title:view.summary.title,'First collected':view.summary.first_seen,'Last collected':view.summary.last_seen}:view.summary).filter(([,value])=>value!=null&&value!=='').map(([key,value])=>[key,flat(value)]);
        const name=extension=>Export.slug((view.title||ref.type)+' '+(view.subtitle||ref.id))+'-'+Export.today()+'.'+extension;
        const report=()=>{
          const lines=[view.title,view.subtitle||'',''],rows=facts(),width=Math.max(0,...rows.map(row=>row[0].length));
          for(const [key,value]of rows)lines.push(key.padEnd(width)+'  '+value);
          if(view.evidence&&Object.keys(view.evidence).length)lines.push('','Source evidence','---------------',JSON.stringify(view.evidence,null,2));
          for(const row of view.observations)lines.push('','Source record · '+(row.provider||'unknown')+' · observed '+(row.observed_at||'unknown')+' · collected '+(row.last_collected_at||row.collected_at||row.first_collected_at||'unknown'),JSON.stringify(row.data||{},null,2));
          if(view.artifacts.length){lines.push('','Saved captures','--------------');for(const item of view.artifacts)lines.push([item.capture_label||item.kind,item.created_at,item.size!=null?item.size+' bytes':'',item.sha256,artifactURL(item.url)?location.origin+artifactURL(item.url):''].filter(Boolean).join(' · '));}
          return lines.join('\n')+'\n';
        };
        const tools=el('div',{class:'ls-export inspector-export','data-export':'false'},el('span',{class:'ls-export-label'},'Export this result'),
          Export.action('TXT',()=>Export.save(name('txt'),report(),'text/plain')),
          Export.action('CSV',()=>Export.save(name('csv'),Export.csv({headers:['Field','Value'],rows:facts()}),'text/csv')),
          Export.action('JSON',()=>Export.save(name('json'),JSON.stringify(ref.record?{reference:{type:ref.type,id:ref.id},record:ref.record}:data,null,2),'application/json',false),'Everything loaded for this result'));
        tabs.before(tools);
      }
      function renderSummary(){
        const facts=ref.type==='endpoint'?{'Address':view.summary.address,'Port':view.summary.port,'Transport':view.summary.transport,'Technology':technology(view.summary),'First collected':date(view.summary.first_seen),'Last collected':date(view.summary.last_seen)}:view.summary;
        const panel=section('At a glance',fields(facts));
        const counts=el('div',{class:'inspector-metrics'},el('div',{},el('strong',{},view.observations.length),el('span',{},'Loaded source records')),el('div',{},el('strong',{},view.artifacts.length),el('span',{},'Loaded captures')));panel.append(counts);
        // What the saved evidence supports, and why a live capture is or is not there.
        if(root.LeakScopeEvidence&&view.attribution)panel.append(section('Attribution',root.LeakScopeEvidence.ladder(view.attribution)));
        if(root.LeakScopeEvidence&&view.captureStatus)panel.append(section('Capture status',root.LeakScopeEvidence.capturePanel(view.captureStatus)));
        if(view.observations.length)panel.append(section('Latest collected record',provenance(view.observations[0])));
        if(ref.type==='endpoint'){const network=root.LeakScopeNetworkContext?.panel(view.summary.address,{signal:controller.signal});if(network)panel.append(network);}
        panel.append(empty('Saved evidence describes an observation at a point in time. Collection time does not confirm current accessibility.'),link('Open full detail →',view.href));return panel;
      }
      function renderEvidence(){const panel=section('Source evidence');if(view.observations.length){panel.append(empty('Showing up to 100 retained records. Each source keeps its own observation and collection dates.'));for(const row of view.observations){const d=el('details',{class:'inspector-record'},el('summary',{},row.provider||'Unknown source',' · ',date(row.observed_at)),provenance(row));d.append(dataView(row.data||{},'Provider observation'));panel.append(d);}}else{panel.append(empty(ref.type==='candidate'?'A discovery lead is an inference to review, not a claim of ownership.':'The original saved record is shown below; missing source dates remain unknown.'));if(ref.type==='candidate')for(const proof of view.evidence)panel.append(el('article',{class:'inspector-record'},el('strong',{},proof.source||'Saved source'),el('p',{},proof.excerpt||proof.signal||'Saved relationship evidence'),el('p',{class:'small muted'},'Source observed '+date(proof.observed_at))));panel.append(dataView(view.evidence||{},'Saved source evidence'));}return panel;}
      function renderHistory(){
        const panel=section('Saved history');if(!view.observations.length){panel.append(empty('No comparable observation history was retained for this result.'));return panel;}
        panel.append(empty('Compare retained records from the same provider. Repeated identical observations may share a single record; this is not a complete scan-by-scan archive.'));
        const timeline=el('ol',{class:'inspector-timeline'});for(const row of view.observations)timeline.append(el('li',{},provenance(row)));panel.append(timeline);
        const groups=new Map();for(const row of view.observations){const key=row.provider||'unknown';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
        const eligible=[...groups].filter(([,rows])=>rows.length>=2);if(!eligible.length){panel.append(empty('At least two distinct retained records from one provider are needed for comparison.'));return panel;}
        const provider=el('select',{name:'compare_provider',class:'select','aria-label':'Comparison provider'}),before=el('select',{name:'compare_before',class:'select','aria-label':'Before record'}),after=el('select',{name:'compare_after',class:'select','aria-label':'After record'}),output=el('div',{class:'inspector-comparison','aria-live':'polite'});
        for(const [name]of eligible)provider.append(el('option',{value:name},name));
        const compare=button('Compare saved records',async()=>{const version=++comparisonVersion;compare.disabled=true;output.replaceChildren(empty('Comparing saved evidence…'));try{const value=await get('/api/investigation/endpoints/'+encodeURIComponent(ref.id)+'/compare?'+new URLSearchParams({before:before.value,after:after.value}));if(controller.signal.aborted||version!==comparisonVersion)return;output.replaceChildren(el('p',{class:'small muted'},value.basis||'Comparison of saved provider evidence'),el('p',{},text(value.count)+' differing fields'+(value.truncated?' · bounded display':'')));if(!value.changes?.length)output.append(empty('No differences in the retained, masked evidence.'));else{const table=el('table',{class:'inspector-diff-table'},el('caption',{},'Before and after saved evidence'),el('thead',{},el('tr',{},['Field / change','Before','After'].map(x=>el('th',{scope:'col'},x))))),tbody=el('tbody');for(const change of value.changes)tbody.append(el('tr',{'data-change-kind':change.kind},el('th',{scope:'row'},el('code',{},change.path),el('small',{},change.kind)),el('td',{},change.kind==='added'?el('span',{class:'muted'},'Not present'):diffValue(change.before,'Before')),el('td',{},change.kind==='removed'?el('span',{class:'muted'},'Not present'):diffValue(change.after,'After'))));table.append(tbody);output.append(el('div',{class:'inspector-diff-wrap'},table));}}catch(error){if(!controller.signal.aborted&&version===comparisonVersion)output.replaceChildren(empty(error.message));}finally{if(!controller.signal.aborted&&version===comparisonVersion)compare.disabled=before.value===after.value;} });
        function update(){comparisonVersion++;output.replaceChildren();compare.disabled=before.value===after.value;}
        function populate(){const rows=groups.get(provider.value);before.replaceChildren();after.replaceChildren();for(const row of rows){const label=date(row.observed_at)+' · collected '+date(row.last_collected_at||row.first_collected_at)+' · '+row.id.slice(0,8);before.append(el('option',{value:row.id},label));after.append(el('option',{value:row.id},label));}before.value=rows[1].id;after.value=rows[0].id;update();}
        provider.addEventListener('change',populate);before.addEventListener('change',update);after.addEventListener('change',update);populate();
        panel.append(el('div',{class:'inspector-compare-controls'},el('label',{},'Provider',provider),el('label',{},'Before',before),el('label',{},'After',after),compare),output);return panel;
      }
      function renderCaptures(){
        const panel=section('Saved captures'),Evidence=root.LeakScopeEvidence;
        if(Evidence&&view.captureStatus)panel.append(Evidence.capturePanel(view.captureStatus));
        if(!view.artifacts.length){panel.append(empty('No saved captures for this result. Use Actions to open the explicit capture controls.'));return panel;}
        const isImage=x=>x.kind==='screenshot'||x.kind==='evidence-render';
        const images=view.artifacts.filter(x=>x.kind==='screenshot'&&artifactURL(x.url));
        if(images.length>=2){const comparison=el('details',{class:'inspector-screenshot-comparison'},el('summary',{},'Compare saved screenshots'));
          comparison.addEventListener('toggle',()=>{if(!comparison.open||comparison.querySelector('figure'))return;const grid=el('div',{class:'inspector-image-pair'});for(const [i,label]of [[1,'Earlier capture'],[0,'Later capture']]){const item=images[i];grid.append(el('figure',{},el('figcaption',{},label+' · '+date(item.created_at)),el('img',{src:artifactURL(item.url),alt:label+' for '+view.title,loading:'lazy'})));}comparison.append(grid);});panel.append(comparison);}
        for(const item of view.artifacts){const url=artifactURL(item.url);const render=item.kind==='evidence-render'?item.metadata?.render||{}:null;
          const group=el('details',{class:'inspector-artifact','data-capture-class':item.capture_class||null},el('summary',{},(item.capture_label||item.kind||'Artifact')+' · '+date(item.created_at)),
            item.capture_class&&Evidence?el('p',{class:'ev-headline'},Evidence.classBadge(item.capture_class,item.capture_label),el('span',{class:'ev-headline-note'},item.capture_note||'')):null,
            fields(render?{'Shows':render.headline,'Outcome':render.state_label,'Operation':render.operation,'Seen by source':render.source_observed_at?date(render.source_observed_at):null,'Evidence saved':render.source_collected_at?date(render.source_collected_at):null,'Image drawn':date(render.rendered_at||item.created_at),'Bytes':item.size,'SHA-256':item.sha256}:{'Tool':item.tool||'Unknown','Captured':date(item.created_at),'Bytes':item.size,'SHA-256':item.sha256}),dataView(item.metadata||{},'Capture metadata'));
          if(url){group.append(link(render?'Open evidence image ↗':'Open saved artifact ↗',url));group.addEventListener('toggle',()=>{if(isImage(item)&&group.open&&!group.querySelector('img'))group.append(el('img',{class:render?'inspector-evidence-image':null,src:url,alt:render?(render.description||'Rendered protocol evidence for '+view.title+'; not a screenshot'):'Saved screenshot for '+view.title,loading:'lazy'}));});}else group.append(empty('This artifact has no valid local download link.'));panel.append(group);}return panel;
      }
      function renderActions(){const panel=section('Explicit actions',empty('These actions open existing tools. Viewing this drawer does not start provider searches, DNS lookups or captures.'),link('Open full detail and collection controls →',view.href)),actions=el('div',{class:'inspector-actions'});
        if(ref.type!=='evidence') {
        const invoke=(label,callback)=>actions.append(button(label,()=>{close();callback()}));
        if(root.LeakScopeAssets?.addToOrganization)invoke('Add to organization',()=>root.LeakScopeAssets.addToOrganization(ref.type==='candidate'?ref.record?.domain:ref));
        if(root.LeakScopeAssets?.openTools)invoke('Names, DNS & netblock',()=>root.LeakScopeAssets.openTools(ref.type==='candidate'?ref.record?.domain:ref));
        if(!['candidate','cloud'].includes(ref.type)){if(root.LeakScopeIntelligence?.openSimilar)invoke('Find similar',()=>root.LeakScopeIntelligence.openSimilar([ref]));if(root.LeakScopeIntelligence?.openAsk)invoke('Ask',()=>root.LeakScopeIntelligence.openAsk([ref]));if(root.LeakScopePresets?.openCreate)invoke('Create discovery preset',()=>root.LeakScopePresets.openCreate([ref]));}
        }
        panel.append(actions);return panel;
      }
      const builders={Summary:renderSummary,Evidence:renderEvidence,History:renderHistory,Captures:renderCaptures,Actions:renderActions};
      function show(name,focus=false){if(controller.signal.aborted)return;for(const [key,node]of tabNodes){const selected=key===name;node.setAttribute('aria-selected',String(selected));node.tabIndex=selected?0:-1;}if(!panes.has(name)){const panel=el('div',{role:'tabpanel',id:'inspector-panel-'+id+'-'+name,'aria-labelledby':'inspector-tab-'+id+'-'+name},builders[name]());panes.set(name,panel);}body.replaceChildren(panes.get(name));if(focus)tabNodes.get(name).focus();}
      for(const name of Object.keys(builders)){const node=button(name,()=>show(name),{role:'tab',id:'inspector-tab-'+id+'-'+name,'aria-controls':'inspector-panel-'+id+'-'+name,class:'inspector-tab'});node.addEventListener('keydown',event=>{const names=[...tabNodes.keys()],index=names.indexOf(name);let next;if(event.key==='ArrowRight')next=names[(index+1)%names.length];if(event.key==='ArrowLeft')next=names[(index+names.length-1)%names.length];if(event.key==='Home')next=names[0];if(event.key==='End')next=names.at(-1);if(next){event.preventDefault();show(next,true);}});tabNodes.set(name,node);tabs.append(node);}show(Object.hasOwn(builders,options.tab)?options.tab:'Summary');
    }catch(error){if(!controller.signal.aborted)body.replaceChildren(empty(error.message||'Saved evidence could not be loaded'));}
  }
  root.LeakScopeInspector={open,dispose,provenance};
})();
