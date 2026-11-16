(function () {
  "use strict";
  const API = "/api/ics";
  let disposePage = () => {};
  const text = value => value == null ? "—" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, text(value));
  }
  function displayValue(value, label) {
    return value && typeof value === "object" ? dataView(value, label) : text(value);
  }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (name === "class") node.className = value;
      else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
      else if (["disabled", "checked", "selected"].includes(name)) node[name] = !!value;
      else node.setAttribute(name, value === true ? "" : text(value));
    }
    children.flat(Infinity).forEach(child => {
      if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(text(child)));
    });
    return node;
  }
  const button = (label, action, attrs = {}) => el("button", {type:"button", class:"btn btn-small", onclick:action, ...attrs}, label);
  const notice = (message, error = false) => el("div", {class:"notice" + (error ? " notice-error" : ""), role:error ? "alert" : "status"}, message);
  const field = (label, control) => el("label", {class:"field"}, el("span", {class:"field-label"}, label), control);
  const input = (name, value, attrs = {}) => el("input", {name, value, class:"input", ...attrs});
  const number = (name, value, min, max) => input(name, value, {type:"number", min, max, step:1});
  const select = (name, choices, value) => el("select", {name, class:"select"}, choices.map(([key,label]) => el("option", {value:key, selected:String(key) === String(value)}, label)));
  const panel = (title, ...children) => el("section", {class:"panel ics-panel"}, el("div", {class:"panel-body"}, el("h2", {}, title), ...children));
  const date = value => value ? (Number.isNaN(new Date(value).valueOf()) ? text(value) : new Date(value).toLocaleString()) : "—";
  const raw = (value, label = "Saved evidence") => el("details", {}, el("summary", {}, label), dataView(value, label));
  const endpointURL = endpoint => "/assets/?endpoint=" + encodeURIComponent(endpoint.id);
  function table(headers, rows, caption, options = {}) {
    const grid = el("table", {}, el("caption", {}, caption),
      el("thead", {}, el("tr", {}, headers.map(label => el("th", {scope:"col"}, label)))),
      el("tbody", {}, rows.map(cells => el("tr", {}, cells.map(cell => el("td", {}, cell))))));
    window.LeakScopeTables?.enhance(grid, options);
    return el("div", {class:"table-scroll"}, grid);
  }
  function localURL(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const url = new URL(value, location.origin);
      return ["http:", "https:"].includes(url.protocol) && url.origin === location.origin && !url.username && !url.password ? url.pathname + url.search : null;
    } catch (_) { return null; }
  }
  async function request(path, data, signal) {
    const response = await fetch(API + path, {
      method:data === undefined ? "GET" : "POST", credentials:"same-origin", signal,
      headers:{Accept:"application/json", ...(data === undefined ? {} : {"Content-Type":"application/json", "X-CSRFToken":document.querySelector('meta[name="csrf-token"]')?.content || ""})},
      ...(data === undefined ? {} : {body:JSON.stringify(data)})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(text(result.error || result.detail || "Request failed"));
    return result;
  }
  function modal(title) {
    const controller = new AbortController();
    const dialog = el("dialog", {class:"run-dialog ics-dialog", "aria-label":title});
    const body = el("div", {class:"ics-dialog-body"});
    dialog.append(el("div", {class:"ics-modal-heading"}, el("h2", {}, title), button("Close", () => dialog.close())), body);
    dialog.addEventListener("cancel", event => {event.preventDefault(); dialog.close();});
    dialog.addEventListener("close", () => {controller.abort(); dialog.remove();}, {once:true});
    document.body.append(dialog);
    dialog.showModal();
    return {body, signal:controller.signal};
  }
  function endpointId(reference) {
    if (!reference || reference.type !== "endpoint" || !reference.id) throw new Error("Choose a saved endpoint.");
    return String(reference.id);
  }
  function protocol(endpoint) {
    const service = String(endpoint.service_type || "").toLowerCase();
    return service.startsWith("modbus") ? "modbus" : service.startsWith("bacnet") ? "bacnet" : null;
  }
  function identity(endpoint) {
    const address = String(endpoint.address || "");
    return (address.includes(":") ? "[" + address + "]" : address) + ":" + text(endpoint.port);
  }
  function endpointActions(endpoint, includeInspect = true, probe) {
    const reference = {type:"endpoint", id:endpoint.id};
    return el("div", {class:"ics-actions"}, el("a", {class:"btn btn-small", href:endpointURL(endpoint)}, "Open endpoint"),
      button("Add to organization", () => window.LeakScopeAssets?.addToOrganization(reference)),
      button("Create discovery preset", () => window.LeakScopePresets?.openCreate?.([reference])),
      button("Related services", () => openRelated(reference)),
      includeInspect && protocol(endpoint) ? button("Inspect points", () => openInspection(reference)) : null,
      probe ? webProbeActions(endpoint,probe.output,probe.signal,probe.refresh) : null);
  }
  function webProbeActions(endpoint,output,signal,refresh) {
    if (endpoint.transport!=="tcp" || !["","unknown","unclassified","http","https"].includes(String(endpoint.service_type || "").toLowerCase())) return [];
    return [false,true].map(tls=>{
      const check=button(tls ? "Check HTTPS" : "Check HTTP",()=>perform(check,output,signal,async()=>{
        const response=await request("/web-probe",{endpoint_id:endpoint.id,tls},signal);
        if(!signal.aborted) followRun(response,output,refresh,signal);
      }));
      return check;
    });
  }
  async function perform(control, output, signal, action) {
    if (control.disabled) return;
    control.disabled = true;
    try {await action();} catch (error) {if (!signal.aborted) output.append(notice(error.message, true));}
    finally {if (!signal.aborted) control.disabled = false;}
  }
  function followRun(response, output, refresh, signal, resume) {
    output.replaceChildren();
    if (response.cached) output.append(notice("Saved result reused. No new collection was needed."));
    if (response.run) {
      const progress = el("div"); output.append(progress);
      if (window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(response.run, progress, latest => {
        if (signal.aborted) return;
        completion(output, latest?.result, resume);
        refresh();
      }, {signal});
      else {
        output.append(notice("Job " + text(response.run.id) + " queued."), el("a", {href:"/runs/"}, "Open Runs"), button("Refresh saved context", refresh));
        if(window.LeakScopeRunLogs)progress.append(window.LeakScopeRunLogs.panel(response.run,{key:"ics-"+response.run.id}));
      }
    } else {
      output.append(notice("Request completed."));
      completion(output, response.result, resume);
      refresh();
    }
  }
  function completion(output, result, resume) {
    if (typeof result === "string") {try {result=JSON.parse(result);} catch (_) {return;}}
    if (!result || typeof result !== "object") return;
    if (result.metadata?.http_verified === true && result.endpoint_id) output.append(notice("HTTP response verified. Open the saved web endpoint to choose capture actions."),
      el("a",{href:endpointURL({id:result.endpoint_id})},"Open verified web endpoint"));
    else if (result.metadata?.http_verified === false) output.append(notice("HTTP response was not verified. The TCP service retains its original identity; review the run evidence."));
    if (result.partial) output.append(notice("Partial result. Review budgets, errors and any remaining ports."));
    if (result.remaining_ports) {
      const remaining=el("textarea",{class:"input ics-code",name:"remaining_ports",readonly:true,rows:3},result.remaining_ports);
      output.append(field("Remaining TCP ports (exact resume range)",remaining),el("p",{class:"small muted"},"This expression preserves unfinished gaps. Review it and approve a new scan to continue."));
      if(resume) output.append(button("Use remaining ports",()=>resume(result.remaining_ports)));
    }
    if (result.next_port != null) output.append(el("p",{class:"small muted"},"First remaining port hint: " + text(result.next_port) + ". Use the exact remaining range above when available."));
    if (result.timeouts_do_not_establish_closed_ports) output.append(el("p",{class:"small muted"},"Timeouts do not establish closed ports."));
    (result.errors || []).forEach(error => output.append(error && typeof error === "object" ? dataView(error, "Error details") : notice(text(error))));
    if (Array.isArray(result.open_ports)) output.append(el("p", {}, "Observed open TCP ports: " + result.open_ports.map(text).join(", ")));
  }
  function optionsFor(values) {
    if (Array.isArray(values)) return values.map(item => typeof item === "string" ? [item,item] : [item.key || item.value || item.service_type || item.provider, item.label || item.name || item.key || item.value || item.service_type || item.provider]).filter(item => item[0]);
    if (values && typeof values === "object") return Object.entries(values).map(([key,value]) => [key, typeof value === "string" ? value : value.label || key]);
    return [];
  }
  async function render(root) {
    disposePage();
    const controller = new AbortController();
    disposePage = () => controller.abort();
    const signal = controller.signal;
    const params = new URLSearchParams(location.search);
    const query = input("q", params.get("q") || "", {type:"search", placeholder:"Host, title or service"});
    const service = select("service_type", [["", "All services"]], "");
    const provider = select("provider", [["", "All sources"]], "");
    const search = el("button", {type:"submit", class:"btn btn-primary"}, "Search screenshots");
    const form = el("form", {class:"ics-filters"}, field("Search",query), field("Service",service), field("Source",provider), search);
    const output = el("div", {"aria-live":"polite"});
    root.classList.add("ics-page");
    root.replaceChildren(el("div", {class:"page-heading"}, el("div", {}, el("h1", {}, "HMI screenshot gallery"), el("p", {}, "Review saved HMI and industrial web screenshots with their observed host and service identity."))),
      notice("This gallery reads saved screenshots. Collection, indexed host searches and protocol inspection start only when requested."), form, output);
    let version = 0;
    async function load(page = 1) {
      const current = ++version;
      root.setAttribute("aria-busy", "true");
      const selectedService = service.value || params.get("service_type") || "";
      const selectedProvider = provider.value || params.get("provider") || "";
      try {
        const data = await request("/gallery?" + new URLSearchParams({q:query.value, service_type:selectedService, provider:selectedProvider, page:String(page), page_size:"24"}), undefined, signal);
        if (signal.aborted || current !== version) return;
        const filters = data.filters || {};
        const serviceChoices = optionsFor(filters.service_types || filters.service_type);
        const providerChoices = optionsFor(filters.providers || filters.provider);
        if (selectedService && !serviceChoices.some(([key]) => key === selectedService)) serviceChoices.push([selectedService,selectedService]);
        if (selectedProvider && !providerChoices.some(([key]) => key === selectedProvider)) providerChoices.push([selectedProvider,selectedProvider]);
        service.replaceChildren(...select("service_type", [["", "All services"],...serviceChoices], selectedService).children);
        provider.replaceChildren(...select("provider", [["", "All sources"],...providerChoices], selectedProvider).children);
        service.value=selectedService;
        provider.value=selectedProvider;
        params.delete("service_type"); params.delete("provider");
        output.replaceChildren(notice(`${data.count || 0} saved screenshots`));
        const cards = el("div", {class:"ics-gallery"});
        for (const item of data.results || []) {
          const endpoint = item.endpoint || {};
          const imageURL = localURL(item.image_url);
          const image = imageURL ? el("img", {src:imageURL, alt:"Saved screenshot of " + identity(endpoint), loading:"lazy", decoding:"async"}) : null;
          const imageBox = el("div", {class:"ics-image"}, image ? el("a",{href:imageURL,target:"_blank",rel:"noopener noreferrer","aria-label":"Open full saved screenshot of "+identity(endpoint)},image) : el("span", {}, "Saved image unavailable"));
          if (image) image.addEventListener("error", () => imageBox.replaceChildren(el("span", {}, "Saved image unavailable")), {once:true});
          const source = item.metadata?.provider || endpoint.provider || item.metadata?.source || item.tool || item.kind || "Saved screenshot";
          cards.append(el("article", {class:"panel ics-card"}, imageBox, el("div", {class:"panel-body"},
            el("h2", {}, endpoint.title || endpoint.service_type || "Saved HMI"), el("p", {class:"ics-host"}, identity(endpoint)),
            el("p", {class:"small muted"}, text(endpoint.service_type) + " · Source: " + text(source)), el("p", {class:"small muted"}, "Captured: " + date(item.created_at)), screenshotQuality(item.metadata || {}),
            el("div", {class:"ics-tags"}, (endpoint.tags || []).map(tag => el("span", {class:"badge"}, typeof tag === "object" ? tag.name || tag.label || tag.key : tag))), endpointActions(endpoint))));
        }
        if (!cards.children.length) output.append(panel("No saved screenshots", el("p", {}, "Screenshots captured from observed industrial web interfaces will appear here. Adjust filters or capture a known endpoint explicitly.")));
        else output.append(cards);
        const currentPage = Number(data.page || page), pages = Number(data.pages || 1);
        output.append(el("div", {class:"ics-actions ics-pagination"}, button("Previous", () => load(currentPage - 1), {disabled:currentPage <= 1}), el("span", {}, `Page ${currentPage} of ${pages}`), button("Next", () => load(currentPage + 1), {disabled:currentPage >= pages})));
      } catch (error) {if (!signal.aborted && current === version) output.replaceChildren(notice(error.message, true));}
      finally {if (!signal.aborted && current === version) root.setAttribute("aria-busy", "false");}
    }
    form.addEventListener("submit", event => {event.preventDefault(); params.delete("service_type"); params.delete("provider"); load(1);});
    await load(Number(params.get("page") || 1));
    return disposePage;
  }
  function screenshotQuality(metadata = {}) {
    const quality = metadata.capture_quality || {};
    const blocked = Number(metadata.blocked_external_requests) > 0 ? Number(metadata.blocked_external_requests) : 0;
    const state = ["challenge", "http_error", "restricted"].includes(quality.state) ? quality.state : blocked ? "restricted" : "unassessed";
    const label = state === "challenge" ? "Browser challenge or interstitial captured" : state === "http_error" ? "HTTP error page captured" : state === "restricted" ? "Partial screenshot — page resources were blocked" : metadata.partial === true ? "Partial screenshot captured" : "Saved screenshot";
    const output = el("div", {class:"notice ics-capture-quality", role:"status", "data-capture-quality":state}, el("strong", {}, label));
    const details = [];
    if (/^[1-5][0-9]{2}$/.test(String(metadata.status))) details.push("HTTP " + metadata.status);
    if (typeof metadata.title === "string" && metadata.title.trim()) details.push("Page title: " + metadata.title.slice(0,500));
    if (blocked) details.push(blocked + " external page requests blocked by capture policy");
    if (details.length) output.append(el("p", {class:"small"}, details.join(" · ")));
    const reasons = Array.isArray(quality.reasons) ? [...new Set(quality.reasons.filter(item => typeof item === "string" && item.trim()).map(item => item.slice(0,500)))].slice(0,5) : [];
    if (reasons.length) output.append(el("p", {class:"small"}, reasons.join(" · ")));
    output.append(el("p", {class:"small"}, "Visual completeness unverified. Review the saved image and capture evidence."));
    return output;
  }
  function openRelated(reference) {
    const {body,signal} = modal("Related services on this host");
    let id;
    try {id = endpointId(reference);} catch (error) {body.append(notice(error.message,true)); return;}
    const saved = el("div", {}, notice("Loading saved host context…"));
    const output = el("div", {"aria-live":"polite"});
    const provider = select("provider", [["netlas","Netlas"],["shodan","Shodan"],["zoomeye","ZoomEye"]], "netlas");
    const mode = select("collection_mode", [["passive","Passive · indexed evidence only"],["demo","Demo · minimum real provider request"],["semi_passive","Semi-passive · identity follow-up"],["screenshot","Screenshots · web follow-up"],["full","Full · configured follow-up"]], "passive");
    const maximum = number("max_results", 25, 1, 100);
    const search = el("button", {type:"submit",class:"btn btn-primary",disabled:true}, "Search indexed host");
    const hint=notice("Search indexed services on the same host across all ports. Passive mode uses provider evidence. Demo makes one minimum real provider request, saves at most one result and starts no automatic capture; minimum billing may still apply. Other modes can contact returned services.");
    mode.addEventListener("change",()=>{maximum.disabled=mode.value==='demo';});
    const form = el("form", {class:"ics-form"}, hint, field("Search provider", provider), field("Collection mode",mode), field("Maximum results",maximum), search);
    const active = activeInventory(id,signal,load);
    body.append(saved, panel("Indexed host search",form), output, active.panel);
    async function load() {
      try {
        const data = await request("/context?"+new URLSearchParams({endpoint:id}), undefined, signal);
        if (signal.aborted) return;
        saved.replaceChildren(el("div",{},el("p", {}, "Observed endpoint: ", el("strong", {}, identity(data.endpoint))), el("p", {class:"muted"}, "Related services share the same host address. This does not establish common ownership."),
          el("div",{class:"ics-actions"},webProbeActions(data.endpoint,output,signal,load)),
          panel("Saved related services", (data.related || []).length ? table(["Host / port", "Service", "Title", "Actions"], data.related.map(endpoint => [identity(endpoint), endpoint.service_type || "Unclassified", endpoint.title, endpointActions(endpoint,true,{output,signal,refresh:load})]), "Saved services on the same host") : el("p", {class:"muted"}, "No other saved services on this host.")),
          el("p",{class:"small muted"},"Check HTTP or HTTPS sends one explicit web request to that saved TCP service. It does not start a screenshot capture.")));
        search.disabled = false;
        active.setHost(data.host_ip);
      } catch (error) {if (!signal.aborted) saved.replaceChildren(notice(error.message,true));}
    }
    form.addEventListener("submit", event => {event.preventDefault(); perform(search,output,signal,async () => {
      const maxResults = mode.value==='demo' ? 1 : integer(maximum.value,1,100,"Maximum results");
      const response = await request("/related", {endpoint_id:id, provider:provider.value, collection_mode:mode.value, max_results:maxResults}, signal);
      if (!signal.aborted) followRun(response,output,load,signal);
    });});
    load();
  }
  function portRange(value) {
    const expression=String(value).trim();
    if (!expression || expression.length>1000) throw new Error("Enter a TCP port range of at most 1000 characters.");
    const selected=new Set();
    for (const part of expression.split(",")) {
      const match=/^(\d+)(?:\s*-\s*(\d+))?$/.exec(part.trim());
      if(!match) throw new Error("Use TCP ports or ranges, such as 22,80,443,1000-2000.");
      const start=integer(match[1],1,65535,"TCP port"),end=integer(match[2] || match[1],1,65535,"TCP port");
      if(end<start) throw new Error("TCP port range ends before it starts.");
      for(let port=start;port<=end;port++) selected.add(port);
    }
    if(selected.size>65535) throw new Error("Select at most 65,535 TCP ports.");
    return expression;
  }
  function activeInventory(id,signal,refresh) {
    let hostIP;
    const host=notice("A saved host IP is required. This action does not resolve hostnames.");
    const ports=input("ports","1-65535",{maxlength:1000});
    const rate=number("rate_per_second",20,1,100),deadline=number("max_seconds",120,15,900);
    const consent=el("input",{type:"checkbox",name:"allow_active_scan",required:true});
    const output=el("div",{"aria-live":"polite"});
    const run=el("button",{type:"submit",class:"btn btn-primary",disabled:true},"Scan TCP ports");
    const enabled=()=>{run.disabled=!hostIP || !consent.checked;};
    consent.addEventListener("change",enabled);
    const form=el("form",{class:"ics-form"},host,
      el("p",{class:"muted small"},"Connect-only TCP inventory contacts the selected saved IP. It records successful connections without application requests, protocol inspection or automatic screenshots. A deadline can leave a partial port range."),
      field("TCP ports or ranges",ports),el("div",{class:"ics-form-grid"},field("Connections per second",rate),field("Maximum seconds",deadline)),
      el("label",{class:"check-row"},consent,"I approve active TCP connections to this saved IP and the selected ports."),run,output);
    form.addEventListener("submit",event=>{event.preventDefault();perform(run,output,signal,async()=>{
      if(!hostIP || !consent.checked) throw new Error("Approve active connections to the saved host IP first.");
      const payload={endpoint_id:id,ports:portRange(ports.value),rate_per_second:integer(rate.value,1,100,"Rate"),max_seconds:integer(deadline.value,15,900,"Maximum seconds"),allow_active_scan:true};
      const response=await request("/scan",payload,signal);
      if(!signal.aborted) followRun(response,output,refresh,signal,remaining=>{ports.value=remaining;consent.checked=false;enabled();ports.focus();});
    }).then(()=>{if(!signal.aborted)enabled();});});
    return {panel:panel("Active TCP inventory",form),setHost(value){hostIP=typeof value==="string" && value ? value : null;host.replaceChildren(hostIP ? el("span",{},"Selected saved IP: ",el("strong",{},hostIP)) : "A saved host IP is required. This action does not resolve hostnames.");enabled();}};
  }
  function integer(value, min, max, label) {
    const parsed = Number(value);
    if (String(value).trim() === "" || !Number.isInteger(parsed) || parsed < min || parsed > max) throw new Error(`${label} must be an integer from ${min} to ${max}.`);
    return parsed;
  }
  function jsonArray(control, maximum, label) {
    let value;
    try {value = JSON.parse(control.value);} catch (_) {throw new Error(label + " must be a valid JSON array.");}
    if (!Array.isArray(value) || value.length > maximum || value.some(item => !item || typeof item !== "object" || Array.isArray(item))) throw new Error(label + " must contain at most " + maximum + " objects.");
    return value;
  }
  function pointView(inspection) {
    if (!inspection) return el("p", {class:"muted"}, "No saved point inspection. Preview the exact read options before starting one.");
    const metadata = inspection.metadata || {};
    const data = metadata.inspection || metadata;
    const output = el("div", {}, el("p", {class:"small muted"}, "Observed: " + date(inspection.created_at)),
      el("p", {class:"muted small"}, "Raw register values are unmapped unless a device-specific mapping supplies labels, datatypes, units or scaling. BACnet labels and units are shown only when returned by the device."));
    if (data.partial) output.append(notice("Partial inspection. Some points or properties could not be read."));
    (data.errors || []).forEach(error => output.append(error && typeof error === "object" ? dataView(error, "Error details") : notice(text(error))));
    const points = data.points || [];
    if (points.length) output.append(table(["Point / register", "Label", "Raw value", "Value", "Units", "Datatype", "Status"], points.map(point => [
      displayValue(point.object_identifier || point.object || (point.object_type != null && point.object_instance != null ? point.object_type + ":" + point.object_instance : point.type != null && point.instance != null ? point.type + ":" + point.instance : point.function != null ? "FC" + point.function + " @ " + point.address : point.address), "Point identifier"),
      point.label || point.name || "Unmapped", displayValue(point.raw_value, "Raw point value"), displayValue(point.value, "Point value"), point.units ?? (point.units_code != null ? "Units code " + point.units_code : "—"), point.datatype || "—", displayValue(point.error || point.status || "Saved", "Point status")
    ]), "Saved inspection points", {data:points,scope:"All loaded inspection points",columns:[
      {key:"point",value:p=>text(p.object_identifier || p.object || (p.object_type != null && p.object_instance != null ? p.object_type+":"+p.object_instance : p.type != null && p.instance != null ? p.type+":"+p.instance : p.function != null ? "FC"+p.function+" @ "+p.address : p.address))},
      {key:"label",value:p=>p.label || p.name || "Unmapped"},
      {key:"raw_value",type:points.every(p=>p.raw_value == null || typeof p.raw_value === "number")?"number":"text",value:p=>p.raw_value == null ? null : text(p.raw_value)},
      {key:"value",type:points.every(p=>p.value == null || typeof p.value === "number")?"number":"text",value:p=>p.value == null ? null : text(p.value)},
      {key:"units",value:p=>p.units ?? p.units_code}, {key:"datatype",value:p=>p.datatype}, {key:"status",value:p=>text(p.error || p.status || "Saved")}
    ]}));
    else output.append(el("p", {class:"muted"}, "No point values in this saved inspection."));
    output.append(raw(metadata));
    return output;
  }
  function inspectionEditor(endpoint, saved, output, signal, refresh) {
    const kind = protocol(endpoint);
    const timeout = number("timeout_seconds",5,1,20);
    const fields = el("div", {class:"ics-form-grid"});
    const advanced = el("div");
    let build;
    if (kind === "modbus") {
      const unit = number("unit_id",1,1,255), enabled = el("input", {type:"checkbox",name:"read_block"});
      const includeIdentity = el("input",{type:"checkbox",name:"include_device_identity",checked:true});
      const fn = select("function", [[3,"03 · Holding registers"],[4,"04 · Input registers"],[1,"01 · Coils"],[2,"02 · Discrete inputs"]],3);
      const address = number("address",0,0,65535), count = number("count",1,1,125);
      const mappings = el("textarea", {class:"input ics-code", name:"mappings", rows:6, maxlength:32000}, "[]");
      fields.append(field("Unit ID",unit),field("Function",fn),field("Starting address (zero-based)",address),field("Count",count));
      const toggle = el("label", {class:"check-row"},enabled,"Read the configured block (leave unchecked for device identification only)");
      fn.addEventListener("change",()=>{count.max=Number(fn.value)<=2 ? "256" : "125";});
      advanced.append(el("label",{class:"check-row"},includeIdentity,"Read device identification"),toggle,field("Device-specific mappings (JSON array)",mappings),el("p",{class:"small muted"},'Optional mapping example: [{"function":3,"address":0,"label":"Supply temperature","units":"°C","datatype":"uint16","byte_order":"big","word_order":"big","scale":0.1,"offset":0}]. Use bool for coils/discrete inputs. Register meanings vary by device; no standard engineering meaning is assumed.'));
      build = () => {
        const functionCode = integer(fn.value,1,4,"Read function");
        const blocks = enabled.checked ? [{function:functionCode,address:integer(address.value,0,65535,"Starting address"),count:integer(count.value,1,functionCode<=2 ? 256 : 125,"Count")}] : [];
        if (!includeIdentity.checked && !blocks.length) throw new Error("Select device identification or an explicit read block.");
        if (blocks.some(block => block.address + block.count > 65536)) throw new Error("Read block exceeds the register address range.");
        const mapping = jsonArray(mappings,64,"Mappings");
        for (const item of mapping) {
          integer(item.function,1,4,"Mapping read function"); integer(item.address,0,65535,"Mapping address");
          if (item.datatype != null && !(item.function<=2 ? ["bool"] : ["uint16","int16","uint32","int32","float32"]).includes(item.datatype)) throw new Error("Unsupported mapping datatype for this read function.");
          for (const key of ["byte_order","word_order"]) if (item[key] != null && !["big","little"].includes(item[key])) throw new Error(key + " must be big or little.");
          for (const key of ["scale","offset"]) if (item[key] != null && (typeof item[key] !== "number" || !Number.isFinite(item[key]))) throw new Error(key + " must be a finite number.");
          for (const key of ["label","units"]) if (item[key] != null && (typeof item[key] !== "string" || item[key].length>(key==="units" ? 80 : 200))) throw new Error(key + " must be a short string.");
          const width=["uint32","int32","float32"].includes(item.datatype) ? 2 : 1;
          if (!blocks.some(block=>block.function===item.function && item.address>=block.address && item.address+width<=block.address+block.count)) throw new Error("Each mapping must fit inside the selected explicit read block.");
        }
        return {unit_id:integer(unit.value,1,255,"Unit ID"),include_device_identity:includeIdentity.checked,blocks,mappings:mapping,timeout_seconds:integer(timeout.value,1,20,"Timeout")};
      };
    } else {
      const device = number("device_instance","",0,4194303), maximum = number("max_objects",4,0,8);
      const objects = el("textarea", {class:"input ics-code",name:"objects",rows:5,maxlength:16000}, "[]");
      fields.append(field("Device instance (optional)",device),field("Maximum objects",maximum));
      advanced.append(field("Requested objects (JSON array)",objects),el("p",{class:"small muted"},'Example: [{"type":0,"instance":1}]. An empty array requests a capped device object list. Reads collect object identity, present value and evidenced units; no write-property request is sent.'));
      build = () => {
        const requested = jsonArray(objects,8,"Objects");
        requested.forEach(item => {integer(item.type,0,1023,"Object type");integer(item.instance,0,4194303,"Object instance");});
        const maxObjects = integer(maximum.value,0,8,"Maximum objects");
        if (requested.length > maxObjects) throw new Error("Requested objects exceed Maximum objects.");
        return {device_instance:device.value.trim() ? integer(device.value,0,4194303,"Device instance") : null,max_objects:maxObjects,objects:requested,timeout_seconds:integer(timeout.value,1,20,"Timeout")};
      };
    }
    fields.append(field("Timeout (seconds)",timeout));
    const preview = el("div", {"aria-live":"polite"});
    const errors = el("div", {"aria-live":"polite"});
    let approved;
    const run = button("Run read-only inspection", async () => {
      if (!approved || run.disabled) return;
      run.disabled = true;
      try {
        const options = build();
        if (JSON.stringify(options) !== approved) throw new Error("Options changed. Preview the reads again.");
        const response = await request("/inspect",{endpoint_id:endpoint.id,options:JSON.parse(approved)},signal);
        if (!signal.aborted) followRun(response,output,refresh,signal);
      } catch (error) {if(!signal.aborted) errors.replaceChildren(notice(error.message,true));}
      finally {approved=null; if(!signal.aborted)run.disabled=true;}
    }, {disabled:true,class:"btn btn-primary"});
    const previewButton = button("Preview reads", () => {
      approved=null;run.disabled=true;errors.replaceChildren();
      try {
        const options = build(); approved=JSON.stringify(options);
        preview.replaceChildren(el("div",{},notice("Preview only · no request sent to the device."),el("p",{},"Target: ",el("strong",{},identity(endpoint))),
          kind === "modbus" && !options.blocks.length ? el("p",{},"Device identification only. No coils or registers will be read.") : null,
          kind === "bacnet" && !options.objects.length ? el("p",{},"Request a capped object list, then read at most "+options.max_objects+" objects.") : null,
          raw(options,"Exact read options")));
        preview.querySelector("details").open=true;run.disabled=false;
      } catch(error) {errors.replaceChildren(notice(error.message,true));}
    });
    const form = el("form", {class:"ics-form"}, fields,advanced,el("div",{class:"ics-actions"},previewButton,run),errors,preview);
    form.addEventListener("submit",event=>event.preventDefault());
    const invalidate = () => {approved=null;run.disabled=true;preview.replaceChildren(notice("Options changed. Preview the reads again."));};
    form.addEventListener("input",invalidate,true);form.addEventListener("change",invalidate,true);
    return form;
  }
  function openInspection(reference) {
    const {body,signal} = modal("Read-only point inspection");
    let id;
    try {id=endpointId(reference);} catch(error){body.append(notice(error.message,true));return;}
    const content=el("div",{},notice("Loading saved inspection context…"));
    const output=el("div",{"aria-live":"polite"});
    const saved=el("div");body.append(content,output,saved);
    async function refresh() {
      try {
        const context=await request("/context?"+new URLSearchParams({endpoint:id}),undefined,signal);
        if(signal.aborted)return;
        saved.replaceChildren(panel("Latest saved inspection",pointView(context.latest_inspection)));
        return context;
      } catch(error){if(!signal.aborted)output.append(notice(error.message,true));return null;}
    }
    async function load() {
      const context=await refresh();if(!context || signal.aborted)return;
      const endpoint=context.endpoint;
      content.replaceChildren(el("p",{},"Observed endpoint: ",el("strong",{},identity(endpoint))),
        notice("This action sends read-only protocol requests to the selected endpoint. It does not write control values. Review the unit, addresses and device mapping before running."));
      if(!context.supported_inspection || !protocol(endpoint)) {content.append(notice("Read-only point inspection is not supported for this saved protocol."));return;}
      content.append(inspectionEditor(endpoint,saved,output,signal,refresh));
    }
    load();
  }
  window.LeakScopeICS={render,openRelated,openInspection};
})();
