(function () {
  "use strict";
  const KEY="leakscope.activityLogs", LIMIT=1000, instances=new Map(), controls=new Set();
  let enabled=false, sequence=0, disposed=false;
  try{enabled=localStorage.getItem(KEY)==="true";}catch(_){}
  const terminal=state=>["succeeded","success","failed","failure","cancelled","canceled","lost","skipped","paused","expired"].includes(String(state||"").toLowerCase());
  const text=value=>value==null ? "—" : String(value);
  function el(tag,attrs={},...children){const node=document.createElement(tag);for(const [key,value] of Object.entries(attrs)){if(value==null||value===false)continue;if(key==="class")node.className=value;else if(key.startsWith("on"))node.addEventListener(key.slice(2),value);else if(["checked","disabled","hidden"].includes(key))node[key]=Boolean(value);else node.setAttribute(key,text(value));}children.flat(Infinity).forEach(value=>{if(value!=null&&value!==false)node.append(value instanceof Node?value:document.createTextNode(text(value)));});return node;}
  const button=(name,fn)=>el("button",{type:"button",class:"btn btn-small",onclick:fn},name);
  function clean(value){if(Array.isArray(value))return value.map(clean);if(value&&typeof value==="object")return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,/secret|password|token|credential|api.?key|authorization|cookie/i.test(key)?"[sensitive value hidden]":clean(item)]));return value;}
  function dataView(value,label){const safe=clean(value);if(window.LeakScopeData?.viewer)return window.LeakScopeData.viewer(safe,{label});if(safe&&typeof safe==="object"){const list=el("dl",{class:"run-log-fields"});for(const [key,item] of Object.entries(safe))list.append(el("dt",{},key.replaceAll("_"," ")),el("dd",{},item&&typeof item==="object"?dataView(item,key):text(item)));return list;}return el("p",{},text(safe));}
  function displayKey(node, container) {
    const section=node.closest("[data-log-disclosure],[data-event-details]");
    const context=section ? section.dataset.logDisclosure || "event:"+section.dataset.eventDetails : "output";
    const viewer=node.closest(".data-viewer");
    const label=viewer?.getAttribute("aria-label") || "";
    const field=node.closest(".data-field");
    const fieldPath=field?.querySelector(":scope > dt")?.title;
    if(node.matches("[data-log-disclosure],[data-event-details]"))return context;
    if(fieldPath)return context+":"+label+":"+fieldPath;
    const scope=viewer || section || container;
    return context+":"+label+":"+node.tagName+":"+[...scope.querySelectorAll(node.tagName)].indexOf(node);
  }
  function rememberDisplay(container) {
    return {details:new Map([...container.querySelectorAll("details")].map(node=>[displayKey(node,container),node.open])),
      modes:new Map([...container.querySelectorAll(".data-viewer")].map(node=>[node.getAttribute("aria-label"),node.querySelector('[data-viewer-mode][aria-pressed="true"]')?.dataset.viewerMode || "formatted"]))};
  }
  function restoreDisplay(container, saved) {
    // Opening a nested formatter creates its children lazily; restore each level.
    for(let depth=0;depth<20;depth++) {
      let opened=0;
      for(const node of container.querySelectorAll("details")) {
        if(saved.details.get(displayKey(node,container)) && !node.open) {
          node.open=true;node.dispatchEvent(new Event("toggle"));opened++;
        }
      }
      if(!opened)break;
    }
    for(const node of container.querySelectorAll(".data-viewer")) {
      if(saved.modes.get(node.getAttribute("aria-label"))==="raw")node.querySelector('[data-viewer-mode="raw"]')?.click();
    }
  }
  function updateControls(){for(const control of controls){if(control.type==="checkbox")control.checked=enabled;else{control.setAttribute("aria-pressed",String(enabled));control.textContent="Activity logs "+(enabled?"on":"off");control.title="Show or hide expandable logs and saved output across jobs";}}}
  function setEnabled(value){enabled=Boolean(value);try{localStorage.setItem(KEY,String(enabled));}catch(_){}updateControls();for(const entry of instances.values())entry.preferenceChanged();window.dispatchEvent(new CustomEvent("leakscope:activity-logs-change",{detail:{enabled}}));}
  function preference(){const checkbox=el("input",{type:"checkbox",name:"activity_logs",checked:enabled,onchange:event=>setEnabled(event.target.checked)});controls.add(checkbox);return el("label",{class:"check-row run-logs-preference"},checkbox,el("span",{},el("strong",{},"Activity logs and saved output"),el("small",{},"Show expandable job logs across the workspace. Logs load only when opened; this does not start collection.")));}
  function panel(initial,options={}){
    const id=String(initial?.id||initial?.run_id||"");if(!id)return el("span",{hidden:true});
    const key=String(options.key||"viewer-"+(++sequence))+":"+id;
    if(instances.has(key)){const item=instances.get(key);item.update(initial);return item.node;}
    let run=initial,cursor=0,events=[],hasMore=false,following=true,timer=null,requestController=null,version=0,snapshot={},snapshotSignature="",dropped=false,wasEligible=false,staleResponses=0;
    const node=el("details",{class:"run-log-viewer","data-run-log-id":id,hidden:!enabled});
    const summary=el("summary",{},"Logs & output"),stateLabel=el("span",{class:"run-log-state"}),status=el("p",{class:"small muted run-log-status",role:"status","aria-live":"polite"});
    const lines=el("div",{class:"run-log-lines",role:"log","aria-live":"off","aria-label":"Recorded run events",tabindex:"0"});
    const output=el("div",{class:"run-log-output"}),error=el("div",{class:"run-log-error",role:"status"}),retention=el("p",{class:"small muted"});
    const follow=el("input",{type:"checkbox",checked:true,onchange:()=>{following=follow.checked;stop();if(following)refresh();}});
    const levels=el("select",{class:"select",name:"log_level","aria-label":"Filter loaded log events",onchange:drawEvents},["all","info","warning","error"].map(value=>el("option",{value},value==="all"?"All levels":value)));
    const next=button("Load next page",()=>refresh()),earlier=button("Browse from start",()=>{following=false;follow.checked=false;stop();cursor=0;events=[];dropped=false;drawEvents();refresh();});
    const refreshButton=button("Refresh logs",()=>refresh());
    summary.append(stateLabel);
    node.append(summary,el("div",{class:"run-log-body"},el("div",{class:"run-log-toolbar"},refreshButton,earlier,next,el("label",{class:"check-row"},follow,"Follow live"),levels,window.LeakScopeExport?window.LeakScopeExport.bar(()=>({headers:["Time","Level","Message","Details"],rows:events.map(item=>[item.created_at||"",String(item.level||"info"),String(item.message||item.event||""),item.data&&Object.keys(item.data).length?JSON.stringify(clean(item.data)):""])}),"Run log"):null),status,error,retention,lines,output));
    function eligible(){return !disposed&&enabled&&node.open&&node.isConnected&&!document.hidden&&!node.closest('[hidden],[aria-hidden="true"],dialog:not([open]),details:not([open])');}
    function stop(){clearTimeout(timer);timer=null;version++;requestController?.abort();requestController=null;}
    function paintState(){stateLabel.textContent=String(run.state||run.status||"queued").replaceAll("_"," ");next.hidden=!hasMore;retention.textContent=dropped?"Showing the latest 1,000 loaded events; earlier events remain saved. Browse from start to read older pages.":events.length+" recorded event"+(events.length===1?"":"s")+" loaded";}
    function paintOutput(value) {
      const signature=JSON.stringify(clean(value));if(signature===snapshotSignature)return;
      snapshotSignature=signature;const display=rememberDisplay(output);
      output.replaceChildren(el("h4",{},"Saved output"));
      let result=value.result ?? run.result;
      if(typeof result==="string"){try{result=JSON.parse(result);}catch(_){}}
      if(result && typeof result==="object" && Object.keys(result).length || typeof result==="string" && result)output.append(dataView(result,"Run output"));
      else output.append(el("p",{class:"small muted"},"Saved output appears here as the job records results."));
      if(value.error || run.error)output.append(el("p",{class:"notice notice-error"},value.error || run.error));
      const exp=value.expansion || run.expansion || run.activity?.expansion;
      if(exp) {
        const facts=el("div",{class:"run-log-facts"});
        for(const [label,val] of [["Candidates saved",exp.saved_candidates],["Current source",exp.current_source],["Current seed",exp.current_seed],["Pending source queries",exp.pending_queries],["Seeds processed",exp.processed]]) {
          if(val!=null && val!=="")facts.append(el("span",{},label+": "+val));
        }
        output.append(facts);
        if(exp.id)output.append(el("a",{class:"btn btn-small",href:"/domain-expansion/?id="+encodeURIComponent(exp.id)},"Open expansion results"));
        if(exp.coverage?.length)output.append(el("details",{"data-log-disclosure":"coverage"},el("summary",{},"Saved source coverage"),dataView(exp.coverage,"Saved source coverage")));
        if(exp.notes?.length)output.append(el("details",{"data-log-disclosure":"notes"},el("summary",{},"Saved collection notes"),exp.notes.map(note=>el("p",{class:"small"},typeof note==="string"?note:note.message || "Collection note"))));
      }
      restoreDisplay(output,display);
    }
    function drawEvents(){const display=rememberDisplay(lines),before=lines.scrollTop,atBottom=before+lines.clientHeight>=lines.scrollHeight-40;lines.replaceChildren();const filter=levels.value||"all",visible=events.filter(item=>filter==="all"||String(item.level).toLowerCase()===filter);if(!events.length)lines.append(el("p",{class:"small muted"},"No recorded events are available for this run. Older runs can still have saved output and source coverage below."));else if(!visible.length)lines.append(el("p",{class:"small muted"},"No loaded events match this level."));for(const event of visible){const level=String(event.level||"info").toLowerCase(),date=event.created_at&&!Number.isNaN(Date.parse(event.created_at))?new Date(event.created_at).toLocaleTimeString():"Time unavailable";const row=el("article",{class:"run-log-line is-"+level,"data-log-event":event.id},el("time",{datetime:event.created_at||""},date),el("span",{class:"run-log-level"},level),el("span",{class:"run-log-message"},event.message||event.event||"Run event"));if(event.data&&Object.keys(event.data).length){const details=el("details",{"data-event-details":String(event.id)},el("summary",{},"Details"),dataView(event.data,"Event details"));row.append(details);}lines.append(row);}restoreDisplay(lines,display);lines.scrollTop=following&&atBottom?lines.scrollHeight:before;paintState();}
    function update(latest) {
      const previous=Date.parse(run.updated_at),current=Date.parse(latest.updated_at);
      if(Number.isFinite(current) && Number.isFinite(previous) && current<previous)return false;
      if(terminal(run.state) && !terminal(latest.state) && (!Number.isFinite(current) || Number.isFinite(previous) && current<=previous))return false;
      run={...run,...latest};paintState();
      const expansion=run.expansion || run.activity?.expansion || (run.kind==="domain_expansion" ? run.activity : null);
      paintOutput({...snapshot,result:run.result ?? snapshot.result,error:run.error ?? snapshot.error,
        expansion:snapshot.expansion || expansion ? {...(snapshot.expansion || {}),...(expansion || {})}:undefined});
      return true;
    }
    async function refresh(){if(!eligible()||requestController)return;clearTimeout(timer);timer=null;const mine=++version;requestController=new AbortController();refreshButton.disabled=true;error.replaceChildren();try{const response=await fetch("/api/runs/"+encodeURIComponent(id)+"/events?"+new URLSearchParams({after:String(cursor),limit:"100"}),{credentials:"same-origin",headers:{Accept:"application/json"},signal:requestController.signal});if(!response.ok)throw Error("Logs request failed ("+response.status+")");const data=await response.json();if(mine!==version||!eligible())return;const known=new Set(events.map(item=>String(item.id)));for(const event of data.events||[])if(event&&event.id!=null&&!known.has(String(event.id))){events.push(event);known.add(String(event.id));}if(events.length>LIMIT){events=events.slice(-LIMIT);dropped=true;}const incoming=Number(data.next_cursor);if(Number.isFinite(incoming)&&incoming>=cursor)cursor=incoming;hasMore=Boolean(data.has_more);
      if(data.run && update(data.run)===false){
        drawEvents();status.textContent="A newer run status and saved output were retained; an older response was ignored.";
        if(eligible() && following && ++staleResponses<=2)timer=setTimeout(refresh,250);
        return;
      }
      staleResponses=0;snapshot=data.snapshot||{};paintOutput(snapshot);drawEvents();status.textContent="Last checked "+new Date().toLocaleTimeString()+(hasMore?" · more saved events available":"")+(terminal(run.state)?" · collection "+run.state:"");if(eligible()&&following&&(hasMore||!terminal(run.state)))timer=setTimeout(refresh,hasMore?250:2000);}catch(err){if(mine===version&&err.name!=="AbortError"&&eligible()){error.append(el("p",{class:"notice notice-error"},"Could not refresh logs. "+err.message));if(following&&!terminal(run.state))timer=setTimeout(refresh,5000);}}finally{if(mine===version){requestController=null;refreshButton.disabled=false;}}}
    function reconcile() {
      const now=eligible();if(now===wasEligible)return;
      wasEligible=now;if(now)refresh();else stop();
    }
    function preferenceChanged(){node.hidden=!enabled;reconcile();}
    node.addEventListener("toggle",reconcile);
    const item={node,update,preferenceChanged,stop,refresh,reconcile};instances.set(key,item);
    paintState();paintOutput({result:initial.result,error:initial.error,expansion:initial.expansion});drawEvents();
    if(instances.size>200)for(const [old,entry] of instances){if(!entry.node.isConnected&&old!==key){entry.stop();instances.delete(old);if(instances.size<=200)break;}}
    return node;
  }
  const toggle=document.getElementById("activity-logs-toggle");if(toggle){controls.add(toggle);toggle.addEventListener("click",()=>setEnabled(!enabled));}updateControls();
  const observer=new MutationObserver(()=>{for(const entry of instances.values())entry.reconcile();});observer.observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:["open","hidden","aria-hidden"]});
  document.addEventListener("visibilitychange",()=>{for(const entry of instances.values())entry.reconcile();});
  window.addEventListener("storage",event=>{if(event.key===KEY)setEnabled(event.newValue==="true");});
  window.addEventListener("pagehide",()=>{disposed=true;observer.disconnect();for(const entry of instances.values())entry.stop();instances.clear();});
  window.LeakScopeRunLogs={panel,preference,setEnabled,isEnabled:()=>enabled,dispose:()=>{disposed=true;observer.disconnect();for(const entry of instances.values())entry.stop();instances.clear();}};
})();
