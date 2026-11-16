(function () {
  'use strict';
  const API='/api/assets/network-ranges';
  const FEEDS=[['aws','Amazon Web Services'],['gcp','Google Cloud'],['cloudflare','Cloudflare'],['github','GitHub']];
  const text=value=>value==null?'Unknown':String(value);
  function el(tag,attrs={},...children){const node=document.createElement(tag);for(const [key,value]of Object.entries(attrs)){if(value==null||value===false)continue;if(key==='class')node.className=value;else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);else node.setAttribute(key,text(value));}for(const child of children.flat(Infinity))if(child!=null&&child!==false)node.append(child instanceof Node?child:document.createTextNode(text(child)));return node;}
  function isIP(value){
    if(typeof value!=='string'||!value||value.length>64)return false;
    if(/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value))return value.split('.').every(part=>String(Number(part))===part&&Number(part)<=255);
    if(!value.includes(':')||!/^[a-f0-9:.]+$/i.test(value))return false;
    try{return !!new URL('http://['+value+']/').hostname;}catch(_){return false;}
  }
  const date=value=>value&&!Number.isNaN(Date.parse(value))?new Date(value).toLocaleString():'Unknown';
  function source(url){try{const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)return null;return el('a',{href:parsed.href,target:'_blank',rel:'noopener noreferrer'},'Official source ↗');}catch(_){return null;}}
  function panel(ip,{signal}={}){
    if(!isIP(ip))return null;
    const details=el('details',{class:'panel asset-panel network-context'},el('summary',{},'Network context'));
    const result=el('div',{class:'network-context-results'}),status=el('p',{class:'small muted','aria-live':'polite'}),feedback=el('p',{class:'small','aria-live':'polite'}),datasets=el('div',{class:'network-context-datasets'});
    const selected=new Map();
    const choices=el('fieldset',{class:'network-context-feeds'},el('legend',{},'Official network feeds'));
    for(const [provider,label]of FEEDS){const checkbox=el('input',{type:'checkbox',name:'network_feed',value:provider});checkbox.checked=provider==='aws';selected.set(provider,checkbox);choices.append(el('label',{},checkbox,label));}
    let loaded=false,busy=false;
    async function request(method,payload){const response=await fetch(API+(method==='GET'?'?'+new URLSearchParams({ip}):''),{method,credentials:'same-origin',signal,headers:{Accept:'application/json',...(method==='POST'?{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content||''}:{})},...(method==='POST'?{body:JSON.stringify(payload)}:{})});const data=await response.json();if(!response.ok)throw Error(data.error||'Network context unavailable');return data;}
    async function load(){
      status.textContent='Reading local network snapshots…';
      const data=await request('GET');if(signal?.aborted||!details.isConnected)return;
      result.replaceChildren(el('p',{class:'small muted'},data.basis||'Vendor-published network context, not ownership or physical location.'));
      if(!data.matches?.length)result.append(el('p',{},'No matching range in the installed snapshots. Missing coverage is unknown.'));
      for(const match of data.matches||[])result.append(el('article',{class:'network-context-match'},el('strong',{},match.provider_label||match.provider),el('code',{},match.prefix),
        el('p',{},'Published services: ',(match.services||[]).join(', ')||'Unspecified'),el('p',{},'Published region: ',(match.regions||[]).join(', ')||'Unspecified'),
        el('p',{class:'small muted'},'Snapshot collected: ',date(match.updated_at)),source(match.source_url)));
      datasets.replaceChildren(...(data.datasets||[]).map(row=>el('p',{class:'small muted'},el('strong',{},row.label||row.provider),' · ',row.status==='ready'?text(row.prefixes)+' prefixes':'Not installed',
        ' · Snapshot collected: ',date(row.updated_at),' · Feed published: ',row.published_at||'Unknown',' ',source(row.source_url))));
      loaded=true;status.textContent='Cached lookup only · no network requests.';
    }
    const refresh=el('button',{type:'button',class:'btn btn-small',onclick:async()=>{
      if(busy)return;const providers=[...selected].filter(([,checkbox])=>checkbox.checked).map(([provider])=>provider);
      if(!providers.length){feedback.textContent='Select at least one official feed.';return;}
      busy=true;refresh.disabled=true;feedback.textContent='Refreshing selected official feeds…';
      try{const data=await request('POST',{providers});if(signal?.aborted||!details.isConnected)return;
        const outcomes=(data.results||[]).map(row=>(FEEDS.find(([key])=>key===row.provider)?.[1]||row.provider)+': '+(row.status==='updated'?'updated':'refresh failed; previous snapshot retained'));
        feedback.textContent=outcomes.join(' · ')+'. '+text(data.requests)+' public feed request'+(data.requests===1?'':'s')+'.';await load();
      }catch(error){if(!signal?.aborted)feedback.textContent=error.message;}finally{busy=false;refresh.disabled=false;}
    }},'Refresh selected feeds');
    const retry=el('button',{type:'button',class:'btn btn-small',onclick:()=>open(true)},'Read cached context');
    async function open(force=false){if((loaded&&!force)||busy||signal?.aborted)return;busy=true;retry.disabled=true;try{await load();}catch(error){if(!signal?.aborted)status.textContent=error.message;}finally{busy=false;retry.disabled=false;}}
    details.addEventListener('toggle',()=>{if(details.open)open();});
    details.append(el('div',{class:'panel-body'},el('p',{class:'small muted'},'IP: '+ip+'. Published network ranges identify vendor/service context. They do not establish customer ownership, a running service or physical location.'),status,result,
      el('details',{class:'network-context-snapshots'},el('summary',{},'Snapshot coverage and dates'),datasets),choices,
      el('p',{class:'small muted'},'Refresh makes one public request per selected provider, up to four. It does not contact this IP or use paid search APIs.'),el('div',{class:'asset-actions'},refresh,retry),feedback));
    return details;
  }
  window.LeakScopeNetworkContext={panel,isIP};
})();
