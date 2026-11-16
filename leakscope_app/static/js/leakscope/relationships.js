(function () {
  "use strict";
  const API = "/api/assets";
  let disposePage = () => {};
  const text = (v) => v == null ? "—" : typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, text(value));
  }
  function displayValue(value, label) {
    return value && typeof value === "object" ? dataView(value, label) : text(value);
  }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (["disabled", "checked", "selected"].includes(key)) node[key] = !!value;
      else node.setAttribute(key, value === true ? "" : text(value));
    }
    children.flat(Infinity).forEach(child => {
      if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(text(child)));
    });
    return node;
  }
  const btn = (label, action, attrs = {}) => el("button", {type: "button", class: "btn btn-small", onclick: action, ...attrs}, label);
  const link = (label, href) => el("a", {href}, label);
  const notice = (message, error = false) => el("div", {class: "notice" + (error ? " notice-error" : ""), role: error ? "alert" : "status"}, message);
  const field = (label, control) => el("label", {class: "field"}, el("span", {class: "field-label"}, label), control);
  const input = (name, value = "", type = "text") => el("input", {class: "input", name, value, type, maxlength: 1000});
  const select = (name, choices, value) => el("select", {class: "select", name}, choices.map(([key,label]) => el("option", {value:key, selected:String(key) === String(value)}, label)));
  const panel = (title, ...body) => el("section", {class: "panel asset-panel"}, el("div", {class:"panel-body"}, el("h2", {}, title), ...body));
  const date = (value) => value ? (Number.isNaN(new Date(value).valueOf()) ? text(value) : new Date(value).toLocaleString()) : "—";
  const list = (value) => [...new Set(String(value || "").split(/[,\n]/).map(v => v.trim()).filter(Boolean))];
  function table(headers, rows, caption, options = {}) {
    const grid = el("table", {}, caption ? el("caption", {class:"asset-caption"}, caption) : null,
      el("thead", {}, el("tr", {}, headers.map(h => el("th", {scope:"col"}, h)))), el("tbody", {}, rows));
    window.LeakScopeTables?.enhance(grid, options);
    return el("div", {class:"table-scroll"}, grid);
  }
  const row = (...cells) => el("tr", {}, cells.map(c => el("td", {}, c)));
  function safeSource(url) {
    if (typeof url !== "string" || !url.trim()) return null;
    try { const u = new URL(url, location.origin); return ["http:","https:"].includes(u.protocol) && !u.username && !u.password ? u.href : null; } catch (_) { return null; }
  }
  function sourceLink(url) {
    const safe = safeSource(url);
    return safe ? el("a", {href:safe, target:"_blank", rel:"noopener noreferrer"}, "Source ↗") : null;
  }
  async function request(path, data, signal) {
    const response = await fetch(path.startsWith("/api/") ? path : API + path, {
      method:data === undefined ? "GET" : "POST", credentials:"same-origin", signal,
      headers:{Accept:"application/json", ...(data === undefined ? {} : {"Content-Type":"application/json", "X-CSRFToken":document.querySelector('meta[name="csrf-token"]')?.content || ""})},
      ...(data === undefined ? {} : {body:JSON.stringify(data)})
    });
    const value = await response.json();
    if (!response.ok) throw new Error(text(value.error || value.detail || "Request failed"));
    return value;
  }
  function raw(value, label = "Saved data") {
    return el("details", {}, el("summary", {}, label), dataView(value, label));
  }
  function structured(data) {
    if (!data || typeof data !== "object") return [];
    const result = [];
    const records = data.records || (data.type && data.name && data.value != null ? [data] : []);
    const types = [...new Set(records.map(record => record.type || "Other"))];
    types.forEach(type => result.push(table(["Name", "Value", "TTL", "Observed", "Expires"], records.filter(record => (record.type || "Other") === type).map(record => row(record.name, displayValue(record.value, "DNS value"), record.ttl, el("time",{"data-sort-value":record.observed_at || ""},date(record.observed_at)), el("time",{"data-sort-value":record.expires_at || ""},date(record.expires_at)))), "DNS " + type + " records")));
    const services = data.services || (data.provider && data.category ? [data] : []);
    if (services.length) result.push(table(["Provider", "Category", "Relationship", "Confidence", "Cited sources"], services.map(service => row(service.provider, service.category, service.relation || "uses_service", el("span",{"data-sort-value":service.confidence ?? ""},text(service.confidence) + " / 100"), serviceSources(service))), "Third-party service signals"));
    const networks = data.networks || (data.cidr || data.start_address ? [data] : []);
    if (networks.length) result.push(table(["Range", "Registered holder", "Name", "Country", "Evidence source"], networks.map(network => row(network.cidr || [network.start_address, network.end_address].filter(Boolean).join(" – "), displayValue(network.organization, "Registered holder"), network.name, network.country, sourceLink(network.source_url) || network.source || "—")), "Public allocation registration"));
    const asns = data.asns || (data.asn ? [data] : []);
    if (asns.length) result.push(table(["ASN", "Relationship", "Evidence source"], asns.map(value => {const item = typeof value === "object" ? value : {asn:value}; return row(item.asn || item.number, item.relation || "reported_asn", sourceLink(item.source_url) || item.source || "—");}), "Evidenced ASNs"));
    if (data.domains?.length) result.push(table(["Domain candidate", "Organization label", "Relationship", "Source"], data.domains.map(item => row(item.name, displayValue(item.organization, "Organization label"), item.relation || "candidate", sourceLink(item.source_url) || item.source || "—")), "Organization domain candidates"));
    if (data.registration && !data.registration.domain) {
      result.push(panel("Domain registration", el("p", {}, "Requested: " + text(data.target || data.domain)),
        data.domain && data.domain !== data.target ? el("p", {class:"small muted"}, "Registrable domain: " + data.domain) : null,
        notice(data.reason || (data.status === "not_found" ? "No public registration record was found." : "This lookup returned no public registration record. Review the lookup errors and source coverage."))));
    } else if (data.registration) {
      const registration = data.registration;
      result.push(panel("Authoritative domain registration", el("p", {}, registration.domain || data.domain || data.target),
        el("p", {class:"small muted"}, "The registrar manages the registration; it is not the registrant. Public registrant data may be redacted. This record does not prove organization ownership."),
        registration.redacted ? notice("Registrant data is redacted or unavailable in this public record.") : null,
        table(["Handle", "Status", "Nameservers", "Source"], [row(registration.handle, displayValue(registration.status, "Registration status"), displayValue(registration.nameservers, "Nameservers"), sourceLink(registration.source_url))], "Domain registration record"),
        (registration.registrars || []).length ? raw(registration.registrars,"Registrar details") : null,
        (registration.registrants || []).length ? raw(registration.registrants,"Public registrant details") : null,
        (registration.events || []).length ? raw(registration.events,"Registration events") : null));
    }
    if (data.entities?.length) result.push(table(["Registered identity", "Handle", "Roles", "Registry", "Source"], data.entities.map(item => row(displayValue(item.name || item.organization || item.label, "Registered identity"), item.handle, displayValue(item.roles || item.role, "Registry roles"), item.registry || item.source, sourceLink(item.source_url))), "Registry entity candidates"));
    if (data.coverage) result.push(el("div", {class:"small muted"}, "Lookup coverage: ", displayValue(data.coverage, "Lookup coverage")));
    return result;
  }
  function serviceSources(service) {
    const sources = [service.source_url, ...(Array.isArray(service.evidence) ? service.evidence : []), ...(Array.isArray(service.sources) ? service.sources : [])];
    const links = new Map();
    for (const source of sources.slice(0, 30)) {
      const url = safeSource(typeof source === "string" ? source : source?.url || source?.source_url);
      if (url && !links.has(url)) links.set(url, el("a", {href:url, target:"_blank", rel:"noopener noreferrer"}, typeof source === "object" && source.title ? source.title : url));
    }
    return links.size ? el("ul", {class:"asset-names"}, [...links.values()].map(item => el("li", {}, item))) : "No cited URL saved";
  }
  function evidence(items = []) {
    if (!items.length) return el("p", {class:"muted"}, "No saved enrichment evidence yet.");
    return el("div", {}, items.map(item => panel(item.source || "Evidence",
      el("p", {class:"small muted"}, "Observed: " + date(item.observed_at) + " · Expires: " + date(item.expires_at)),
      sourceLink(item.source_url), structured(item.data), raw(item.data ?? item, "Evidence data"))));
  }
  function modal(title) {
    const controller = new AbortController();
    const dialog = el("dialog", {class:"run-dialog asset-dialog", "aria-label":title});
    const body = el("div", {class:"asset-dialog-body"});
    const close = () => dialog.close();
    dialog.append(el("div", {class:"asset-modal-heading"}, el("h2", {}, title), btn("Close", close)), body);
    dialog.addEventListener("cancel", event => {event.preventDefault(); close();});
    dialog.addEventListener("close", () => {controller.abort(); dialog.remove();}, {once:true});
    document.body.append(dialog); dialog.showModal();
    return {body, signal:controller.signal, close};
  }
  async function action(button, output, fn, signal) {
    if (button.disabled) return;
    button.disabled = true;
    try {await fn();} catch (error) {if (!signal.aborted) output.append(notice(error.message, true));}
    finally {if (!signal.aborted) button.disabled = false;}
  }
  function showResult(output, result, refresh, signal) {
    output.replaceChildren();
    if (result.demo || result.result?.demo) output.append(notice("Demo · real minimum API usage. This is a limited sample; omitted results or follow-ups are not treated as complete research."));
    if (result.cached) output.append(notice("Cached result · no new lookup was needed."));
    if (result.run) {
      const progress = el("div"); output.append(progress);
      if (window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(result.run, progress, refresh, {signal});
      else {
        progress.append(notice("Job " + text(result.run.id) + " queued. Follow progress in Runs."), link("Open Runs", "/runs/"), btn("Refresh saved evidence", refresh));
        if(window.LeakScopeRunLogs)progress.append(window.LeakScopeRunLogs.panel(result.run,{key:"relationship-"+result.run.id}));
      }
    }
    if (result.result) {
      const data = result.result;
      if (data.partial) output.append(notice("Partial result. Review errors and saved evidence."));
      (data.errors || []).forEach(error => output.append(error && typeof error === "object" ? dataView(error, "Error details") : notice(text(error))));
      output.append(...structured(data), raw(data, "Lookup result"));
      refresh();
    }
  }
  function isIP(target) {return typeof target === "string" && (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(target) || (target.includes(":") && /^[a-f0-9:.]+$/i.test(target)));}
  function concreteName(value) {return typeof value === "string" && !value.includes("*") && !isIP(value) && /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z0-9-]+\.?$/i.test(value);}
  function nameGroup(item) {
    const source = String(item.source || "").toLowerCase();
    if (/ptr|reverse/.test(source)) return "PTR names";
    if (/cert|tls|san/.test(source)) return "Certificate names";
    if (/passive.?dns|indexed.?dns/.test(source)) return "Indexed passive DNS names";
    if (/observed|hostname/.test(source)) return "Observed hostnames";
    return "Provider names";
  }
  function expansionLink(options, title = "Expand domains") {
    const params = new URLSearchParams();
    if(options.organization_id)params.set("organization_id",options.organization_id);
    if(options.view)params.set("view",options.view);
    if(options.domain)params.set("domain",options.domain);
    if(options.reference){params.set("reference_type",options.reference.type);params.set("reference_id",options.reference.id);}
    return link(title,"/domain-expansion/?"+params);
  }
  function openTools(referenceOrTarget) {
    const value = typeof referenceOrTarget === "object" ? {...referenceOrTarget} : {target:String(referenceOrTarget || "")};
    const payload = value.type && value.id != null ? {reference:{type:value.type,id:value.id}} : {target:value.target || value.value || ""};
    const query = payload.reference ? new URLSearchParams(payload.reference) : new URLSearchParams({target:payload.target});
    const {body,signal} = modal("Asset tools");
    const content = el("div"), output = el("div", {"aria-live":"polite"}); body.append(content, output);
    async function load() {
      try {
        const context = await request("/context?" + query, undefined, signal); if (signal.aborted) return;
        const names = context.names || [], ips = (context.ips || []).map(ip => typeof ip === "string" ? ip : ip.address || ip.ip || ip.value).filter(Boolean);
        const domains = [...new Set([context.target,...names.map(n => n.name)].filter(concreteName))];
        const controls = el("div", {class:"asset-tools-grid"});
        const status = el("div", {}, el("p", {}, "Target: ", el("strong", {}, context.target)), el("p", {class:"muted small"}, "Hostnames are separate evidence; the observed target stays unchanged."));
        if (context.node_id) status.append(link("Explore relationships →", "/relationships/?root=" + encodeURIComponent(context.node_id)));
        const organizationTarget = payload.reference || (context.node_id && isIP(context.target) ? {type:"asset",id:context.node_id} : context.target);
        status.append(el("div", {class:"asset-actions"}, btn("Add to organization", () => addToOrganization(organizationTarget)), expansionLink(payload.reference ? {reference:payload.reference} : {domain:context.target})));
        content.replaceChildren(status);
        const groups = new Map(); names.forEach(n => {const group = nameGroup(n);if (!groups.has(group)) groups.set(group, []);groups.get(group).push(n);});
        const namePanels = el("div", {class:"asset-name-panels"});
        for (const [label, items] of groups) namePanels.append(panel(label, el("ul", {class:"asset-names"}, items.map(n => el("li", {},
          el("code", {}, n.name), " · " + text(n.source), n.wildcard || n.name?.includes("*") ? " · wildcard evidence; no DNS query" : "", n.forward_confirmed != null ? " · forward " + (n.forward_confirmed ? "confirmed" : "not confirmed") : "")))));
        content.append(panel("Resolved names", namePanels, !names.length ? el("p", {class:"muted"}, "No names in saved evidence. Resolve names explicitly to check PTR records.") : null));
        const caps = context.actions || {};
        const resolve = btn("Resolve names", () => action(resolve, output, async () => showResult(output, await request("/resolve", payload, signal), load, signal), signal), {disabled:caps.resolve === false});
        controls.append(panel("Names", el("p", {class:"small muted"}, "Explicit PTR and bounded forward confirmation. No paid provider query starts here."), resolve));
        const dnsTarget = select("dns_target", domains.map(n => [n,n]), domains[0]);
        const dns = btn("DNS records", () => action(dns, output, async () => showResult(output, await request("/dns", {target:dnsTarget.value}, signal), load, signal), signal), {disabled:!domains.length || caps.dns === false});
        controls.append(panel("DNS", field("Concrete domain", dnsTarget), el("p", {class:"small muted"}, "Bounded record queries and third-party service signals. Wildcard names are evidence only."), dns));
        const entraTarget=select("entra_domain",domains.map(name=>[name,name]),domains[0]);
        const publicIdentifiers=link("View saved public identifiers","/correlations/?public=1"+(entraTarget.value?"&q="+encodeURIComponent(entraTarget.value):""));
        entraTarget.addEventListener("change",()=>{publicIdentifiers.href="/correlations/?public=1&q="+encodeURIComponent(entraTarget.value);});
        const entra=btn("Collect Entra tenant",()=>action(entra,output,async()=>showResult(output,await request("/entra",{domain:entraTarget.value,demo:true},signal),load,signal),signal),{disabled:!domains.length});
        controls.append(el("details",{class:"panel asset-panel asset-entra-identity"},el("summary",{},"Microsoft Entra identity"),el("div",{class:"panel-body"},
          el("p",{class:"small muted"},"Collect the domain's public Microsoft identity declaration with one fixed metadata request, or reuse cached evidence. No provider search or login is performed. A shared tenant is a relationship to review, not proof of ownership."),
          field("Concrete domain",entraTarget),el("div",{class:"asset-actions"},entra,publicIdentifiers))));
        const registrationTarget = select("registration_target", domains.map(n => [n,n]), domains[0]);
        const registration = btn("Domain registration", () => action(registration, output, async () => showResult(output, await request("/registration", {target:registrationTarget.value}, signal), load, signal), signal), {disabled:!domains.length || caps.registration === false});
        controls.append(panel("Domain registration", field("Concrete domain", registrationTarget), el("p", {class:"small muted"}, "Keyless authoritative RDAP for the registrable domain. Redacted records and registrar identities are shown separately; no paid reverse-WHOIS request starts here."), registration));
        const netTarget = select("netblock_target", ips.map(ip => [ip,ip]), ips[0]);
        const netblock = btn("Netblock info", () => action(netblock, output, async () => showResult(output, await request("/netblock", {target:netTarget.value}, signal), load, signal), signal), {disabled:!ips.length || caps.netblock === false});
        controls.append(panel("Public allocation", field("Observed IP", netTarget), el("p", {class:"small muted"}, "Registration describes the allocation holder. Hosting does not establish ownership. Private IPs have no public allocation."), netblock));
        const networkUI=window.LeakScopeNetworkContext;
        const networkIPs=networkUI?[...new Set([context.target,...ips].filter(networkUI.isIP))]:[];
        if(networkIPs.length){
          const networkTarget=select("network_context_ip",networkIPs.map(ip=>[ip,ip]),networkIPs[0]),networkHolder=el("div"),networkBox=el("div",{class:"asset-network-context"});
          const showNetwork=()=>{const contextPanel=networkUI.panel(networkTarget.value,{signal});networkHolder.replaceChildren(...(contextPanel?[contextPanel]:[]));};
          networkTarget.addEventListener("change",showNetwork);showNetwork();
          if(networkIPs.length>1)networkBox.append(field("Saved IP for network context",networkTarget));
          networkBox.append(networkHolder);controls.append(networkBox);
        }
        const monitorNames = [...new Set([context.target,...names.map(n => n.name)].filter(n => concreteName(n) || (typeof n === "string" && n.startsWith("*.") && concreteName(n.slice(2)))))];
        const monitorTarget = select("monitor_target", monitorNames.map(n => [n,n]), monitorNames[0]);
        controls.append(panel("Certificate monitoring", field("Domain scope", monitorTarget), btn("Monitor", () => monitor(monitorTarget.value), {disabled:!monitorNames.length || caps.monitor === false})));
        if (caps.services === true && (isIP(context.target) || concreteName(context.target))) {
          const research = researchButton("/services", {target:context.target}, output, load, signal);
          controls.append(panel("Web service research", el("p", {class:"small muted"}, "Search public web evidence with Firecrawl. Findings are service candidates with cited sources; they do not prove ownership."), research));
        }
        content.append(controls, panel("Saved evidence", evidence(context.evidence || [])));
      } catch(error) {if (!signal.aborted) content.replaceChildren(notice(error.message,true));}
    }
    content.append(notice("Loading saved context…")); load();
  }
  function demoControl(detail) {
    const box=el("input",{type:"checkbox",name:"demo"});
    return {box,control:el("label",{class:"check-row api-demo"},box,el("span",{},el("strong",{},"Demo · minimal API use"),el("small",{},detail)))};
  }
  function researchButton(path, payload, output, refresh, signal) {
    const demo=demoControl("One real Firecrawl search result, with no follow-up scrape. This is a partial sample; minimum provider billing may apply.");
    const button = btn("Research services with Firecrawl", () => action(button, output, async () => {
      const response = await request(path, {...payload,...(demo.box.checked ? {demo:true} : {})}, signal);
      if (!signal.aborted) showResult(output, response, refresh, signal);
    }, signal));
    return el("div",{class:"api-demo-action"},demo.control,button);
  }
  function scopeSummary(data) {
    const count = value => Array.isArray(value) ? value.length : Number(value || 0);
    return `${count(data.added)} added · ${count(data.already_linked)} already linked.`;
  }
  function addToOrganization(referenceOrTarget) {
    const reference = referenceOrTarget && typeof referenceOrTarget === "object" && referenceOrTarget.type && referenceOrTarget.id != null
      ? {type:referenceOrTarget.type,id:referenceOrTarget.id} : null;
    const target = reference ? null : String(typeof referenceOrTarget === "object" ? referenceOrTarget.target || referenceOrTarget.value || "" : referenceOrTarget || "");
    const query = new URLSearchParams(reference || {target});
    const {body,signal} = modal("Add to organization");
    const content = el("div", {}, notice("Loading saved organizations and result context…"));
    const output = el("div", {"aria-live":"polite"});
    body.append(content,output);
    async function load() {
      try {
        const [data,context] = await Promise.all([request("/organizations",undefined,signal), request("/context?"+query,undefined,signal)]);
        if (signal.aborted) return;
        const organizations = (data.results || []).filter(org => !org.status || org.status === "active");
        const destination = select("organization_id", [["","Choose an organization"],...organizations.map(org=>[org.id,org.label || org.value])], "");
        const names = [...new Set((context.names || []).map(item=>item.name).filter(name=>typeof name==="string" && name))];
        const choices = el("fieldset", {class:"asset-domain-choices"}, el("legend", {}, "Optional saved domain scopes"));
        names.forEach(name=>choices.append(el("label", {class:"check-row"}, el("input", {type:"checkbox",name:"organization_domain",value:name}),el("code",{},name))));
        const submit = el("button", {class:"btn btn-primary",type:"submit",disabled:true}, "Add to organization");
        destination.addEventListener("change",()=>{submit.disabled=!destination.value;});
        const literalIP = !reference && isIP(context.target || target);
        const form = el("form", {class:"asset-form"},
          el("p", {}, reference ? "Selected result: " : "Selected target: ", el("strong",{},context.target || target)),
          notice("This records user scope and a saved result association. Ownership is unverified; saving does not run DNS or provider lookups."),
          field("Organization",destination));
        if (reference || literalIP) {
          if (names.length) form.append(choices);
          else form.append(el("p",{class:"muted"},"No saved domain names. The selected result will still be linked."));
          form.append(el("p",{class:"small muted"},"The selected result is always linked. Domain scopes are added only when checked; wildcard names stay literal."));
        } else form.append(el("p",{},"Domain scope: ",el("code",{},context.target || target)));
        if (!organizations.length) form.append(notice("Create an organization before linking this result."),link("Create organization","/organizations/"));
        form.append(submit);
        form.addEventListener("submit",event=>{event.preventDefault();action(submit,output,async()=>{
          if (!destination.value) throw new Error("Choose an organization.");
          const domains = [...choices.querySelectorAll("input:checked")].map(item=>item.value);
          if (domains.length>30) throw new Error("Select at most 30 domain scopes per save.");
          const member = reference || literalIP;
          const organizationId = destination.value;
          const payload = member ? {...(reference ? {reference} : {target:context.target || target}),domains} : {domains:[context.target || target]};
          const result = await request("/organizations/"+encodeURIComponent(organizationId)+(member ? "/members" : "/domains"),payload,signal);
          if (signal.aborted) return;
          const message = member ? (result.result_already_linked ? "Result already linked to organization. " : "Result linked to organization. ") : "Domain scope saved. ";
          output.replaceChildren(notice(message+scopeSummary(result)),link("Manage domains & results","/organizations/?id="+encodeURIComponent(organizationId)));
        },signal);});
        content.replaceChildren(form);
      } catch(error) {if(!signal.aborted)content.replaceChildren(notice(error.message,true));}
    }
    load();
  }
  function monitor(domain) {
    const {body,signal} = modal("Monitor certificates");
    const scope = input("domain", domain), name = input("name");
    const output = el("div", {"aria-live":"polite"});
    const form = el("form", {class:"asset-form"}, notice("Create an observe-only watch. It saves certificate matches without fetching target services."),
      field("Domain scope", scope), field("Watch name (optional)", name), el("p", {class:"small muted"}, "An exact domain includes descendants. A wildcard scope excludes the apex."));
    const existing = select("watch_id", [["","Create a new watch"]], "");
    const consent = el("input", {type:"checkbox", name:"allow_auto_fetch"});
    const consentLabel = el("label", {class:"check-row", hidden:true}, consent, "I approve adding this scope to the selected watch's active follow-up actions.");
    let watches = [];
    existing.addEventListener("change", () => {
      const watch = watches.find(w => String(w.id) === existing.value);
      const active = !!watch && (!!watch.auto_fetch || !!watch.actions?.length || !!watch.actions?.auto_fetch || !!watch.actions?.fetch);
      consent.checked = false; consentLabel.hidden = !active;
    });
    const pick = btn("Choose existing watch", () => action(pick, output, async () => {
      const data = await request("/api/operations/watches", undefined, signal);if(signal.aborted)return;
      watches = data.results || data.watches || [];
      existing.replaceChildren(el("option", {value:""}, "Create a new watch"), ...watches.map(w => el("option", {value:w.id}, w.name)));
      pick.replaceWith(field("Destination watch", existing));
    }, signal));
    const submit = el("button", {type:"submit", class:"btn btn-primary"}, "Create watch");
    existing.addEventListener("change", () => {submit.textContent = existing.value ? "Add to watch" : "Create watch";});
    form.append(pick, consentLabel, submit, output);
    form.addEventListener("submit", event => {event.preventDefault(); action(submit, output, async () => {
      if (!consentLabel.hidden && !consent.checked) throw new Error("Approve the selected watch's active follow-up actions before adding this scope.");
      const data = await request("/monitor", {domain:scope.value.trim(),...(name.value.trim() ? {name:name.value.trim()} : {}),allow_auto_fetch:consent.checked,...(existing.value ? {watch_id:existing.value} : {})}, signal);
      if (!signal.aborted) output.replaceChildren(notice(data.already_covered ? "This scope is already covered by a watch." : "Watch saved."), link("Open certificate watches →", "/monitoring/"));
    },signal);});
    body.append(form);
  }
  function preservePagerFocus(control, oppositeLabel, container, redraw) {
    const focused = document.activeElement === control;
    const label = control.textContent;
    redraw();
    if (!focused) return;
    const enabled = [...container.querySelectorAll("button")].filter(button => !button.disabled);
    (enabled.find(button => button.textContent === label) || enabled.find(button => button.textContent === oppositeLabel))?.focus({preventScroll:true});
  }
  function pagedDetailsTable({id,title,headers,items,renderRow,caption,noun="records",searchText,open=false,sortColumns}) {
    const values = Array.isArray(items) ? items : [];
    const search = input(id+"_filter");search.placeholder="Filter loaded "+noun;search.setAttribute("aria-label","Filter loaded "+noun);
    const details=el("details",{class:"panel asset-list-disclosure","data-list-id":id,open},el("summary",{},title+" · "+values.length));
    const body=el("div",{class:"asset-list-body"}),count=el("p",{class:"asset-list-count small muted","aria-live":"polite"}),pager=el("div",{class:"asset-actions asset-list-pager"});
    let page=1;
    const sorting={key:null,direction:"asc"};
    const columns=sortColumns || window.LeakScopeTables?.defaults(headers) || [];
    function draw(){
      const term=search.value.trim().toLowerCase();
      const filtered=values.filter(item=>text(searchText ? searchText(item) : item).toLowerCase().includes(term));
      const matching=window.LeakScopeTables?.sort(filtered,sorting,columns) || filtered;
      const pages=Math.max(1,Math.ceil(matching.length/25));page=Math.max(1,Math.min(page,pages));const start=(page-1)*25;
      count.textContent="Showing "+(matching.length ? start+1 : 0)+"–"+Math.min(start+25,matching.length)+" of "+matching.length+" matching "+noun+" in this loaded list ("+values.length+" loaded).";
      body.replaceChildren(table(headers,matching.slice(start,start+25).map(renderRow),caption,{columns,state:sorting,onSort:()=>{page=1;draw();},scope:"Sort all matching "+noun+" in this loaded list"}));
      const previous=btn("Previous "+noun,()=>preservePagerFocus(previous,"Next "+noun,pager,()=>{page--;draw();}),{disabled:page<=1}),next=btn("Next "+noun,()=>preservePagerFocus(next,"Previous "+noun,pager,()=>{page++;draw();}),{disabled:page>=pages});
      pager.replaceChildren(previous,el("span",{class:"small muted"},"Page "+page+" of "+pages),next);
    }
    search.addEventListener("input",()=>{page=1;draw();});
    details.append(el("div",{class:"asset-list-tools"},field("Filter loaded "+noun,search)),count,body,pager);draw();return details;
  }
  function certificateNames(values) {
    const names=Array.isArray(values)?values:[];
    const output=el("div",{class:"asset-certificate-names"},el("ul",{class:"asset-names"},names.slice(0,3).map(name=>el("li",{},name))));
    if(names.length>3){
      const more=el("details",{class:"asset-certificate-more"},el("summary",{},"+"+(names.length-3)+" more names · "+names.length+" total"));let loaded=false;
      more.addEventListener("toggle",()=>{
        if(!more.open || loaded)return;loaded=true;let shown=3;const rows=el("ul",{class:"asset-names"}),next=btn("Show more names",append);
        function append(){const end=Math.min(shown+25,names.length);for(;shown<end;shown++)rows.append(el("li",{},names[shown]));next.hidden=shown>=names.length;next.textContent="Show "+Math.min(25,names.length-shown)+" more names";}
        append();more.append(rows,next);
      });output.append(more);
    }
    return output;
  }
  function certificateIssuer(value) {
    if(!value || typeof value!=="object")return el("span",{},text(value));
    const candidate=value.CN || value.common_name || value.commonName || value.name || value.O || value.organization;
    const summary=typeof candidate==="string"?candidate:Object.values(value).filter(item=>typeof item==="string").slice(0,2).join(" · ") || "Saved issuer";
    const fields=el("details",{class:"asset-certificate-issuer"},el("summary",{},"Full issuer fields"));let loaded=false;
    fields.addEventListener("toggle",()=>{if(fields.open&&!loaded){loaded=true;fields.append(dataView(value,"Certificate issuer"));}});
    return el("div",{},el("span",{class:"asset-certificate-issuer-summary"},summary),fields);
  }
  function certificateSummary(c) {
    return el("dl", {class:"asset-facts"}, ...[
      ["Fingerprint", c.fingerprint || c.key], ["Issuer", c.issuer], ["Serial", c.serial], ["Observed", date(c.observed_at)],
      ["First seen", date(c.first_seen)], ["Last seen", date(c.last_seen)], ["Valid from", date(c.not_before)], ["Valid until", date(c.not_after)]
    ].flatMap(([key,value]) => [el("dt", {}, key), el("dd", {}, displayValue(value, key))]));
  }
  async function certificates(root, signal) {
    const params = new URLSearchParams(location.search), id = params.get("id");
    root.append(el("div", {class:"page-heading"}, el("div", {}, el("h1", {}, "Certificates"), el("p", {}, "Search saved Certstream matches locally. External certificate sources are not enabled."))));
    if (id) {
      const data = await request("/certificates/" + encodeURIComponent(id), undefined, signal); if(signal.aborted)return;
      const c = data.certificate;
      root.append(el("div",{class:"asset-actions"},link("← Certificate search", "/certificates/"),window.LeakScopeInspector ? btn("Inspect",()=>window.LeakScopeInspector.open({type:"certificate",id:c.id})) : null,btn("Add to organization",()=>addToOrganization({type:"certificate",id:c.id}))), panel("Certificate evidence", certificateSummary(c)),
        pagedDetailsTable({id:"certificate-names",title:"Certificate names",noun:"names",headers:["Name","Actions"],items:c.domains,open:(c.domains || []).length<=10,
          sortColumns:[{key:"name",value:name=>name},null],searchText:name=>name,renderRow:name=>row(name,el("div",{class:"asset-actions"},btn("Monitor",()=>monitor(name)),!name.includes("*")?btn("Asset tools",()=>openTools(name)):el("span",{class:"muted small"},"Wildcard evidence; no hostname query"))),caption:"Saved certificate names"}));
      if(data.node_id) root.append(link("Explore relationships →", "/relationships/?root=" + encodeURIComponent(data.node_id)));
      root.append(panel("Watch matches", table(["Watch", "Matched names", "First seen", "Last seen"], (c.watch_matches || []).map(m => row(m.watch_name || m.watch_id, (m.matched_names || []).join(", "), el("time",{"data-sort-value":m.first_seen || ""},date(m.first_seen)), el("time",{"data-sort-value":m.last_seen || ""},date(m.last_seen)))))), raw(data.data || c.data || {}, "Certificate evidence"));
      return;
    }
    const sorting=window.LeakScopeTables?.fromParams(params,["subject","names","issuer","watches","first_seen","last_seen"],"last_seen") || {key:"last_seen",direction:"desc"};
    const q = input("q", params.get("q") || "");
    const mode = select("mode", [["domain","Domain"],["keyword","Keyword"]],params.get("mode") || "domain");
    const watch = select("watch", [["","All watches"]], "");
    const since = input("since", params.get("since") || "", "date"), until = input("until", params.get("until") || "", "date");
    const form = el("form", {class:"asset-filters"}, field("Search",q),field("Match",mode),field("Watch",watch),field("Since",since),field("Until",until),el("button",{class:"btn btn-primary",type:"submit"},"Search certificates"));
    const output = el("div", {"aria-live":"polite"}); root.append(form,output);
    let version = 0;
    async function load(page = 1) {
      const current = ++version;
      const query = new URLSearchParams({q:q.value,mode:mode.value,watch:watch.value || params.get("watch") || "",since:since.value,until:until.value,page:String(page),page_size:"25",sort:sorting.key,direction:sorting.direction});
      const data = await request("/certificates?"+query, undefined, signal); if(signal.aborted || current !== version)return;
      history.replaceState(null,"","?"+query);
      const selected = watch.value || params.get("watch") || "";
      watch.replaceChildren(el("option", {value:""}, "All watches"), ...(data.watches || []).map(w => el("option", {value:w.id,selected:String(w.id)===selected},w.name)));
      params.delete("watch");
      const stats = data.stats || {};
      output.replaceChildren(notice(`${data.count || 0} matching certificates · ${stats.certificates || 0} saved certificates · ${stats.names || 0} names · ${stats.matches || 0} watch matches`));
      if (!(data.results || []).length) output.append(panel("No saved matches", el("p", {}, "Certificate matches appear here after an enabled watch sees a matching event. This search does not query external CT services.")));
      else output.append(table(["Certificate", "Names", "Issuer", "Watch matches", "Last seen", "Actions"], data.results.map(c => row(link(c.fingerprint || c.key || c.id,"/certificates/?id="+encodeURIComponent(c.id)),
        certificateNames(c.domains), certificateIssuer(c.issuer), el("div",{},String((c.watch_matches || []).length),el("div",{class:"small muted"},(c.watch_matches || []).map(w => w.watch_name || w.watch_id).join(", "))), date(c.last_seen),el("div",{class:"asset-actions"},window.LeakScopeInspector ? btn("Inspect",()=>window.LeakScopeInspector.open({type:"certificate",id:c.id})) : null,btn("Add to organization",()=>addToOrganization({type:"certificate",id:c.id}))))), "Locally saved certificate matches",
        {columns:[{key:"subject",description:"Certificate fingerprint or saved key"},{key:"names",description:"First alphabetical saved DNS name"},{key:"issuer",description:"Issuer organization, then common name"},{key:"watches",type:"number",description:"Number of matching watches"},{key:"last_seen",type:"date"},null],state:sorting,onSort:()=>reload(1),scope:"Sort all matching saved certificates"}));
      const shown=output.querySelector("table");
      if(shown){const all=new URLSearchParams(query);all.delete("page");all.delete("page_size");shown.dataset.exportUrl="/export/certificates/?"+all;}
      const currentPage = Number(data.page || page), pages = Number(data.pages || 1);
      output.append(el("div", {class:"asset-actions"}, btn("Previous",()=>reload(currentPage-1),{disabled:currentPage<=1}), el("span",{},`Page ${currentPage} of ${pages}`), btn("Next",()=>reload(currentPage+1),{disabled:currentPage>=pages})));
    }
    async function reload(page) {try{await load(page);}catch(error){if(!signal.aborted)output.append(notice(error.message,true));}}
    form.addEventListener("submit", event => {event.preventDefault();params.delete("watch");reload(1);});
    await load(Number(params.get("page") || 1));
  }
  const organizationStatus = org => org.status || "active";
  function organizationCounts(counts = {}) {
    const labels = [["domains","Domain scopes"],["members","Saved results"],["evidence","Evidence records"],["incoming","Incoming relationships"],["outgoing","Outgoing relationships"]];
    return labels.map(([key,label]) => row(label, Number.isFinite(Number(counts[key])) ? Number(counts[key]) : "—"));
  }
  function organizationActions(org, onDone, output, signal, compact=false) {
    const archived=organizationStatus(org)==="archived";
    const controls=el("div",{class:"asset-actions asset-organization-controls","data-organization-actions":org.id,role:"group","aria-label":"Actions for "+(org.label||org.value)});
    if(compact)controls.append(link(archived ? "Open archive" : "Open","/organizations/?id="+encodeURIComponent(org.id)));
    if(!archived)controls.append(btn(compact ? "Merge" : "Merge organization",()=>organizationManagement(org,"merge",result=>onDone(result,"merge"),signal),{title:"Merge / combine into another organization"}));
    const archive=btn(archived ? (compact ? "Restore" : "Restore organization") : "Ignore / Archive",()=>action(archive,output,async()=>{
      output.replaceChildren(notice((archived ? "Restoring " : "Archiving ")+(org.label||org.value)+"…"));
      let result;
      try {result=await request("/organizations/"+encodeURIComponent(org.id)+(archived ? "/restore" : "/archive"),{},signal);}
      catch(error){output.replaceChildren();throw new Error((org.label||org.value)+": "+error.message);}
      if(signal.aborted)return;
      const focus=await onDone(result,archived ? "restore" : "archive");
      if(focus?.isConnected && document.activeElement===document.body)focus.focus();
    },signal),{title:archived ? "Restore this organization to active views" : "Hide from active views. Restore anytime; existing watches and lookups keep their settings."});
    controls.append(archive,btn(compact ? "Delete" : "Delete organization",()=>organizationManagement(org,"delete",result=>onDone(result,"delete"),signal),{class:"btn btn-small btn-danger",title:"Review removal of the organization; collected evidence is retained"}));
    return controls;
  }
  async function organizationManagement(org, mode, onDone, pageSignal) {
    const merging = mode === "merge";
    const {body,signal,close} = modal(merging ? "Merge organization" : "Delete organization");
    body.parentElement.classList.add("asset-lifecycle-dialog");
    pageSignal?.addEventListener("abort",close,{once:true,signal});
    const output = el("div", {"aria-live":"polite"});
    const preview = el("div", {"aria-live":"polite"}, notice("Loading saved organization impact…"));
    const confirmation = input("organization_confirmation"); confirmation.autocomplete = "off"; confirmation.maxLength = 500;
    const submit = el("button", {type:"submit",class:"btn " + (merging ? "btn-primary" : "btn-danger"),disabled:true}, merging ? "Confirm merge" : "Confirm delete");
    const destination = select("merge_target",[["","Choose destination organization"]],"");
    let ready = false, previewVersion = 0, noDestinations = false;
    function update() { submit.disabled = !ready || confirmation.value !== org.value || (merging && !destination.value); }
    confirmation.addEventListener("input",update);
    const form = el("form",{class:"asset-form"},
      notice(merging ? "The selected destination keeps its name. Aliases, scopes, saved results and evidence are combined; the source points to that destination. Review the direction before confirming." : "Delete removes this organization from the workspace and disconnects its memberships. Underlying findings, captures, certificates and watches remain."),
      merging ? field("Destination organization",destination) : null,
      preview,
      field("Type " + org.value + " to confirm",confirmation),
      el("div",{class:"asset-actions"},submit,btn("Cancel",close)),output);
    body.append(form);
    async function loadPreview() {
      const version = ++previewVersion;
      ready = false; confirmation.value = ""; update(); output.replaceChildren();
      preview.replaceChildren(notice("Loading saved organization impact…"));
      try {
        const query = merging && destination.value ? "?" + new URLSearchParams({target_id:destination.value}) : "";
        const result = await request("/organizations/"+encodeURIComponent(org.id)+"/manage"+query,undefined,signal);
        if (signal.aborted || version !== previewVersion) return;
        const data = result.preview;
        if (!data?.organization) throw new Error("The organization impact preview is unavailable. Close and reopen this dialog.");
        preview.replaceChildren();
        if (merging) {
          preview.append(el("div",{class:"asset-merge-direction"},
            el("div",{},el("span",{class:"field-label"},"From"),el("strong",{},data.organization.label || data.organization.value)),
            el("span",{"aria-hidden":"true",class:"asset-merge-arrow"},"→"),
            el("div",{},el("span",{class:"field-label"},"Into"),el("strong",{},data.target ? data.target.label || data.target.value : "Choose a destination above"))));
          const sourceRows = organizationCounts(data.counts);
          if (data.target) {
            const targetRows = organizationCounts(data.target_counts);
            sourceRows.forEach((entry,index) => entry.append(targetRows[index].lastChild));
          }
          preview.append(table(data.target ? ["Saved scope", "From", "Into"] : ["Saved scope", "From"],sourceRows,"Organization merge impact"));
          preview.append(el("p",{class:"small muted"},"Equivalent memberships appear once. Original evidence sources and observation times stay attached. This merge does not prove ownership and has no automatic unmerge."));
          ready = Boolean(destination.value && data.target && data.target.id === destination.value && data.target.id !== org.id && organizationStatus(data.target) === "active");
        } else {
          preview.append(el("h3",{},"Remove " + (data.organization.label || data.organization.value)),
            table(["Saved scope", "Current count"],organizationCounts(data.counts),"Organization deletion impact"));
          ready = true;
        }
        preview.append(el("p",{class:"small muted"},"No provider lookups run here. Findings, captures, certificates and watches are retained."));
        update();
      } catch (error) {
        if (!signal.aborted && version === previewVersion) {preview.replaceChildren(notice(error.message,true));ready=false;update();}
      }
    }
    destination.addEventListener("change",loadPreview);
    form.addEventListener("submit",event=>{event.preventDefault();action(submit,output,async()=>{
      if (!ready || confirmation.value !== org.value || (merging && !destination.value)) throw new Error("Review the impact and type the source organization name exactly.");
      const result = await request("/organizations/"+encodeURIComponent(org.id)+"/"+mode,
        {...(merging ? {target_id:destination.value} : {}),confirmation:confirmation.value},signal);
      if (signal.aborted) return;
      const focus=await onDone(result);
      close();
      if(focus?.isConnected)focus.focus();
    },signal);});
    try {
      if (merging) {
        const data = await request("/organizations?status=active",undefined,signal);
        if (signal.aborted) return;
        const candidates = (data.results || []).filter(item=>item.id !== org.id && organizationStatus(item) === "active");
        destination.replaceChildren(el("option",{value:""},"Choose destination organization"),...candidates.map(item=>el("option",{value:item.id},item.label || item.value)));
        noDestinations = !candidates.length;
      }
      await loadPreview();
      if(!signal.aborted && noDestinations) output.append(notice("Create or restore another organization before merging."));
    } catch (error) {if (!signal.aborted) output.append(notice(error.message,true));}
  }
  async function organizations(root, signal) {
    const params = new URLSearchParams(location.search), id = params.get("id");
    if (id) return organizationDetail(root,id,signal);
    root.append(el("div", {class:"page-heading"},el("div",{},el("h1",{},"Organizations"),el("p",{},"Record organization scope and explore evidence-backed candidates."))),notice("Seed domains, networks and ASNs define user scope. They are not proof of ownership."));
    const name = input("name"), aliases = el("textarea",{name:"aliases",class:"input",rows:2}), domains = el("textarea",{name:"domains",class:"input",rows:2}), networks = el("textarea",{name:"networks",class:"input",rows:2}), asns = el("textarea",{name:"asns",class:"input",rows:2}); name.required = true;
    const output = el("div", {"aria-live":"polite"});
    const submit = el("button", {type:"submit",class:"btn btn-primary"},"Create organization");
    const form = el("form",{class:"asset-form"},field("Legal name",name),field("Aliases (one per line)",aliases),field("Seed domains (one per line)",domains),field("Seed networks (CIDR, one per line)",networks),field("Seed ASNs (one per line)",asns),submit,output);
    const initialStatus = ["active","archived","all"].includes(params.get("status")) ? params.get("status") : "active";
    const status = select("organization_status",[["active","Active organizations"],["archived","Archived organizations"],["all","Active and archived"]],initialStatus);
    const entries = el("div"), organizationRows=el("div",{class:"asset-organization-rows"}), organizationCount=el("p",{class:"small muted","aria-live":"polite"}), organizationPager=el("div",{class:"asset-actions asset-list-pager"});
    const listStatus=el("div",{class:"asset-organization-list-status","aria-live":"polite"});
    const lifecycleFeedback=el("div",{"data-organization-feedback":"","aria-live":"polite",tabindex:"-1"});
    const localFilter=input("organization_filter");localFilter.placeholder="Filter loaded organizations";localFilter.setAttribute("aria-label","Filter loaded organizations");
    const create=el("details",{class:"panel asset-organization-create asset-list-disclosure"},el("summary",{},"Create organization scope"),el("div",{class:"panel-body"},form));
    form.addEventListener("invalid",()=>{create.open=true;},true);
    root.append(create,el("div",{class:"asset-filters"},field("Organization view",status),field("Filter loaded organizations",localFilter)),lifecycleFeedback,listStatus,entries);
    entries.append(organizationCount,organizationRows,organizationPager);
    let loadVersion = 0, organizationPage=1, loadedOrganizations=[];const cards=new Map();
    function drawOrganizations(){
      const term=localFilter.value.trim().toLowerCase();
      const matching=loadedOrganizations.filter(org=>[org.label,org.value,...(org.attributes?.aliases || [])].join(" ").toLowerCase().includes(term));
      const pages=Math.max(1,Math.ceil(matching.length/12));organizationPage=Math.max(1,Math.min(pages,organizationPage));const start=(organizationPage-1)*12;
      organizationCount.textContent=matching.length+" matching · "+loadedOrganizations.length+" loaded organizations · showing "+(matching.length?start+1:0)+"–"+Math.min(start+12,matching.length)+". The API returns at most 300 organization records.";
      organizationRows.replaceChildren(...matching.slice(start,start+12).map(org=>{if(!cards.has(org.id))cards.set(org.id,organizationCard(org));return cards.get(org.id);}));
      if(!matching.length)organizationRows.append(notice(loadedOrganizations.length?"No loaded organizations match this filter.":"No saved organizations in this view."));
      const previous=btn("Previous organizations",()=>preservePagerFocus(previous,"Next organizations",organizationPager,()=>{organizationPage--;drawOrganizations();}),{disabled:organizationPage<=1}),next=btn("Next organizations",()=>preservePagerFocus(next,"Previous organizations",organizationPager,()=>{organizationPage++;drawOrganizations();}),{disabled:organizationPage>=pages});
      organizationPager.replaceChildren(previous,el("span",{class:"small muted"},"Page "+organizationPage+" of "+pages),next);
    }
    localFilter.addEventListener("input",()=>{organizationPage=1;drawOrganizations();});
    async function load() {
      const version = ++loadVersion, requestedStatus = status.value;
      loadedOrganizations=[];cards.clear();organizationRows.replaceChildren();organizationPager.replaceChildren();organizationCount.textContent="";
      entries.setAttribute("aria-busy","true");localFilter.disabled=true;
      listStatus.replaceChildren(notice("Loading "+requestedStatus+" organizations…"));
      try {
        const data = await request("/organizations?"+new URLSearchParams({status:requestedStatus}), undefined, signal);
        if(signal.aborted || version !== loadVersion)return;
        loadedOrganizations=(data.results || []).filter(org=>requestedStatus === "all" || organizationStatus(org) === requestedStatus);
        listStatus.replaceChildren();drawOrganizations();localFilter.disabled=false;
      } catch(error) {
        if(signal.aborted || version !== loadVersion)return;
        listStatus.replaceChildren(notice("Unable to load "+requestedStatus+" organizations: "+error.message,true),btn("Retry organization list",()=>load()));
      } finally {
        if(!signal.aborted && version===loadVersion)entries.setAttribute("aria-busy","false");
      }
    }
    function organizationCard(org) {
        const archived = organizationStatus(org) === "archived";
        const result = el("div", {"aria-live":"polite"});
        const paid = el("input", {type:"checkbox",name:"include_netlas"});
        const demo=demoControl("Minimal real registration lookup. Netlas stays opt-in; Demo uses at most one paid WHOIS collection when enabled.");
        const paidChoice = el("label", {class:"check-row"}, paid, el("span", {}, "Also query Netlas WHOIS (uses provider credits; up to 2 requests)"));
        const discover = btn("Discover candidates", () => action(discover, result, async () => showResult(result, await request("/organizations/"+encodeURIComponent(org.id)+"/discover", {include_netlas:paid.checked,max_requests:demo.box.checked ? 1 : 2,...(demo.box.checked ? {demo:true} : {})}, signal),()=>result.append(link("Explore updated relationships →","/relationships/?root="+encodeURIComponent(org.id))),signal),signal));
        const details=el("details",{class:"asset-organization-details"},el("summary",{},"Scope, discovery and research"),raw(org.attributes || {},"Scope details"),
          archived ? notice("Archived organizations retain their scopes and evidence. Open the organization to restore it or review its saved results.") : el("p",{class:"small muted"},"Discovery starts with free public registry identity searches and network/ASN pivots. Results are candidates, public records can be redacted, and coverage is not exhaustive. Netlas is used only when selected below."),
          archived ? null : paidChoice,
          archived ? null : demo.control,
          el("div",{class:"asset-actions"},link(archived ? "Review archived organization" : "Manage domains & results","/organizations/?id="+encodeURIComponent(org.id)),link("Explore relationships →","/relationships/?root="+encodeURIComponent(org.id)),
            archived ? null : expansionLink({organization_id:org.id}),expansionLink({organization_id:org.id,view:"results"},"Expansion results"),archived ? null : discover,archived ? null : researchButton("/organizations/"+encodeURIComponent(org.id)+"/services",{},result,()=>result.append(link("Review saved evidence →","/relationships/?root="+encodeURIComponent(org.id))),signal)));
        const aliases=Array.isArray(org.attributes?.aliases)?org.attributes.aliases:[];
        return el("section",{class:"panel asset-organization-row","data-organization-id":org.id},
          el("div",{class:"asset-organization-summary"},el("div",{},el("h3",{},org.label || org.value),
            el("p",{class:"asset-organization-meta"},aliases.length+" "+(aliases.length===1?"alias":"aliases")+" recorded · Last seen: "+date(org.last_seen))),
            el("span",{class:"asset-organization-status","data-organization-status":organizationStatus(org)},archived?"Archived":"Active"),
            organizationActions(org,async(data,mode)=>{
              if(signal.aborted)return;
              await load();if(signal.aborted)return;
              const label=org.label||org.value;
              const message=mode==="merge" ? label+" merged into "+(data.organization?.label||data.organization?.value||"the selected destination")+". Combined scope and evidence are retained."
                : mode==="delete" ? label+" removed from the workspace. Underlying evidence, captures, certificates and watches are retained."
                : mode==="archive" ? label+" archived and hidden from active views. Restore it from the archive; existing watches and lookups keep their settings."
                : label+" restored to active organizations.";
              lifecycleFeedback.replaceChildren(...[notice(message),
                mode==="merge" && data.organization?.id ? link("Open combined organization","/organizations/?id="+encodeURIComponent(data.organization.id)) : null,
                mode==="archive" ? link("View archived organizations","/organizations/?status=archived") : null].filter(Boolean));
              return lifecycleFeedback;
            },lifecycleFeedback,signal,true)),details,result);
    }
    status.addEventListener("change",()=>{const url=new URL(location.href);url.searchParams.set("status",status.value);history.replaceState(null,"",url);load();});
    form.addEventListener("submit",event=>{event.preventDefault();action(submit,output,async()=>{
      const data=await request("/organizations",{name:name.value,aliases:list(aliases.value),domains:list(domains.value),networks:list(networks.value),asns:list(asns.value)},signal);
      if(signal.aborted)return;output.replaceChildren(notice("Organization scope saved. Discovery starts only when you select Discover candidates."));status.value="active";await load();
    },signal);});
    await load();
  }
  async function organizationDetail(root, id, signal) {
    let path = "/organizations/"+encodeURIComponent(id), current;
    const heading=el("div");
    const content = el("div"), editing = el("div"), management = el("div",{"data-organization-management":""});
    const summary=el("div"), evidence=el("div"), workspace=el("div"), status=el("div");
    const output = el("div", {"aria-live":"polite"});
    const researchOutput = el("div", {"aria-live":"polite"});
    const lifecycleOutput = el("div", {"aria-live":"polite"});
    const domains = el("textarea", {class:"input",name:"add_domains",rows:4,required:true,maxlength:8000,placeholder:"example.com\n*.example.org"});
    const submit = el("button",{class:"btn btn-primary",type:"submit"},"Add domains");
    const form = el("form",{class:"asset-form"},field("Domains (one per line, up to 30)",domains),
      el("p",{class:"small muted"},"Add literal domain or wildcard scopes to this organization. Saving records user scope without DNS queries, provider discovery or an ownership claim."),submit,output);
    root.append(link("← All organizations","/organizations/"),heading,status,workspace);
    async function reload(nextId=id) {const data=await request("/organizations/"+encodeURIComponent(nextId),undefined,signal);if(!signal.aborted)display(data);}
    async function completed(result,mode) {
      if(signal.aborted)return;
      if(mode === "delete") {
        history.replaceState(null,"","/organizations/");
        root.replaceChildren();
        await organizations(root,signal);
        if(!signal.aborted) root.prepend(notice("Organization removed from the workspace. Its findings, captures, certificates and watches remain."));
        return;
      }
      await reload(result.organization?.id || id);
      lifecycleOutput.replaceChildren(notice(mode === "merge" ? "Organizations merged. The destination retains the combined scope and evidence." : mode === "archive" ? "Organization archived. Its scopes and evidence are retained." : "Organization restored to the active workspace."));
    }
    function display(data) {
      const org = data.organization;
      if(!org)throw new Error("The saved organization is unavailable.");
      const redirected = data.redirected_from || org.id !== id;
      id = org.id; path = "/organizations/"+encodeURIComponent(id); current=org;
      if(redirected) history.replaceState(null,"","/organizations/?id="+encodeURIComponent(id));
      const archived=organizationStatus(org) === "archived";
      heading.replaceChildren(el("div",{class:"page-heading"},el("div",{},el("h1",{},org.label || org.value),el("p",{},"Manage organization domain scopes and saved results."))),
        el("p",{class:"asset-organization-status","data-organization-status":organizationStatus(org)},archived ? "Archived" : "Active"),
        window.LeakScopeExport?.links ? window.LeakScopeExport.links("/export/organizations/"+encodeURIComponent(id)+"/",[["HTML report","html","A formatted report of everything saved for this organization"],["TXT","txt"],["CSV","csv"],["JSON","json"]],"Export this organization") : null);
      status.replaceChildren(
        ...(redirected ? [notice("This organization was merged. Showing the canonical destination.")] : []),
        ...(archived ? [notice("Archived organization · read-only. Restore it to add scope or start organization research. Existing findings, captures, certificates and watches are retained.")] : []));
      const overviewUI=window.LeakScopeOrganizationOverview;
      summary.replaceChildren(overviewUI ? overviewUI.render(data) : notice("Review this organization's saved domains, results and evidence below. User scope does not establish ownership."));
      content.replaceChildren(
        panel("Domain scopes",data.domains?.length ? table(["Domain", "Source", "Actions"],data.domains.map(domain=>row(domain.name,domain.source || "user_scope",el("div",{class:"asset-actions"},
          archived ? null : btn("Asset tools",()=>openTools(domain.name)),archived ? null : btn("Monitor",()=>monitor(domain.name)),domain.node_id ? link("Relationships","/relationships/?root="+encodeURIComponent(domain.node_id)) : null))),"Organization domain scopes") : el("p",{class:"muted"},archived ? "No saved domain scopes." : "No domain scopes yet. Add domains in Collection & editing or link a saved result.")),
        panel("Saved results",data.members?.length ? table(["Kind", "Result", "Reference", "Actions"],data.members.map(member=>{
          const url = safeSource(member.url);
          return row(member.kind,url ? el("a",{href:url,rel:"noopener noreferrer"},member.label || member.value) : member.label || member.value,
            el("div",{},el("code",{},member.value),member.reference ? raw(member.reference,"Saved reference") : null),
            overviewUI ? overviewUI.inspect(member.reference,null) : null);
        }),"Organization saved results") : el("p",{class:"muted"},archived ? "No saved results linked." : "No saved results linked yet. Use Add to organization on any saved result.")));
      evidence.replaceChildren(el("div",{class:"asset-actions"},link("Explore relationships →","/relationships/?root="+encodeURIComponent(id)),link("Public identifiers","/correlations/?public=1&organization_id="+encodeURIComponent(id))),
        el("p",{class:"small muted"},"Explore the saved evidence behind these associations. Shared infrastructure does not establish ownership."),
        ...(window.LeakScopeViz ? [window.LeakScopeViz.organizationMap({organization:org,domains:data.domains,members:data.members,
          open:typeof window.innerWidth !== "number" || window.innerWidth > 800})] : []),raw(org.attributes || {},"Organization details"));
      management.replaceChildren(organizationActions(org,completed,lifecycleOutput,signal),
        el("details",{class:"asset-organization-action-help"},el("summary",{},"What these actions do"),
          el("p",{class:"small muted"},"Ignore / Archive hides this organization from active views and can be restored. Existing watches and scheduled lookups keep their own settings. Merge combines it into the chosen destination. Delete removes organization memberships while retaining collected evidence.")),lifecycleOutput);
      editing.replaceChildren(el("p",{class:"small muted organization-collection-intro"},"Collection starts only when you choose an action. Viewing saved results does not run discovery."),
        el("div",{class:"asset-actions"},archived ? null : expansionLink({organization_id:id}),expansionLink({organization_id:id,view:"results"},"Expansion results")));
      if(!archived)editing.append(panel("Add domain scopes",form),panel("Web service research",el("p",{class:"small muted"},"Explicit Firecrawl research searches public web evidence for third-party service candidates. Cited pages are evidence to review, not proof of ownership."),
        researchButton(path+"/services",{},researchOutput,()=>researchOutput.append(link("Review saved evidence →","/relationships/?root="+encodeURIComponent(id))),signal),researchOutput));
      else editing.append(notice("Restore this organization from Manage to add scopes or start collection. Saved expansion results remain available."));
      if(overviewUI)workspace.replaceChildren(overviewUI.layout({key:id,sections:[
        {id:"overview",label:"Overview",content:summary},{id:"scope",label:"Scope & results",content},
        {id:"evidence",label:"Evidence & relationships",content:evidence},{id:"collection",label:"Collection & editing",content:editing},
        {id:"manage",label:"Manage",content:management}]}));
      else workspace.replaceChildren(summary,content,evidence,editing,management);
    }
    form.addEventListener("submit",event=>{event.preventDefault();action(submit,output,async()=>{
      const values = list(domains.value);
      if (!values.length || values.length>30) throw new Error("Enter between 1 and 30 domain scopes per save.");
      const result = await request(path+"/domains",{domains:values},signal);
      if (signal.aborted) return;
      output.replaceChildren(notice(scopeSummary(result)));
      if (result.organization && Array.isArray(result.domains) && Array.isArray(result.members)) display(result);
      else await reload();
      if (!signal.aborted) domains.value="";
    },signal);});
    await reload();
  }
  function svgEl(tag, attrs, ...children) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    children.forEach(child=>node.append(child instanceof Node ? child : document.createTextNode(text(child))));return node;
  }
  async function relationships(root, signal) {
    const params = new URLSearchParams(location.search);
    root.append(el("div",{class:"page-heading"},el("div",{},el("h1",{},"Relationships"),el("p",{},"Explore saved asset relationships and their evidence. Shared IPs or certificates do not establish ownership."))));
    const rootId = select("root", [["","Recent saved assets"], ...(params.get("root") ? [[params.get("root"),"Focused saved asset"]] : [])], params.get("root") || "");
    const includeCandidates = select("include_candidates", [["false","Evidence relationships"],["true","Include research candidates"]], params.get("include_candidates")==="true" ? "true" : "false");
    const form = el("form",{class:"asset-filters"},field("Focus asset",rootId),field("Relationship layer",includeCandidates),el("button",{type:"submit",class:"btn"},"Load relationships"));
    const output = el("div");root.append(form,output);
    let loadVersion = 0;
    async function load() {
      const version = ++loadVersion;
      const layerMode = includeCandidates.value;
      const query = new URLSearchParams({max_nodes:"150",include_candidates:layerMode});if(rootId.value.trim())query.set("root",rootId.value.trim());
      const graph = await request("/graph?"+query,undefined,signal);if(signal.aborted || version !== loadVersion)return;
      const nodes = (graph.nodes || []).slice(0,150), ids = new Set(nodes.map(n=>n.id));
      const edges = (graph.edges || []).filter(e=>ids.has(e.source)&&ids.has(e.target));
      const focused=rootId.value;
      rootId.replaceChildren(el("option",{value:""},"Recent saved assets"),
        ...nodes.map(n=>el("option",{value:n.id},n.kind+": "+(n.label||n.value))));
      if(focused && !ids.has(focused))rootId.append(el("option",{value:focused},"Focused saved asset"));
      rootId.value=focused;
      const details = el("div",{class:"asset-selection","aria-live":"polite"},notice("Select a node or relationship in the graph or tables to inspect evidence."));
      const proof = el("div", {"aria-live":"polite"});
      const selectionPanel=el("details",{class:"panel asset-proof-panel"},
        el("summary",{class:"panel-heading"},"Asset & evidence details"),el("div",{class:"panel-body"},details,proof));
      let relationshipMap;
      let selectionVersion = 0;
      let constellation = null;
      function mark(id) { relationshipMap?.select(id); constellation?.select(id); }
      function inspectNode(n) {
        selectionVersion++;selectionPanel.open=true;mark(n.id);proof.replaceChildren();details.replaceChildren(el("h3",{},n.label || n.value),el("p",{},n.kind + " · " + n.value),raw(n.attributes || {},"Node attributes"),
          el("div",{class:"asset-actions"},link("Focus graph here","/relationships/?root="+encodeURIComponent(n.id)+"&include_candidates="+layerMode),["domain","ip"].includes(n.kind) ? btn("Asset tools",()=>openTools(n.value)) : null,
          n.kind==="domain" ? btn("Monitor",()=>monitor(n.value)) : null,
          n.kind!=="organization" ? btn("Add to organization",()=>addToOrganization({type:"asset",id:n.id})) : link("Manage domains & results","/organizations/?id="+encodeURIComponent(n.id))));
      }
      function inspectEdge(edge, openProof = false) {
        const version=++selectionVersion;selectionPanel.open=true;mark(edge.id);proof.replaceChildren();details.replaceChildren(...[el("h3",{},edge.relation),el("p",{},"Confidence: "+text(edge.confidence)+" / 100 · Source: "+text(edge.evidence_source)),
          el("p",{},"Observed: "+date(edge.observed_at)+" · Expires: "+date(edge.expires_at)),sourceLink(edge.source_url),raw(edge,"Relationship data")].filter(child=>child!=null));
        if(edge.evidence_id){
          const view = btn("Load proof",async()=>{
            view.disabled=true;
            try {
              const value=await request("/evidence/"+encodeURIComponent(edge.evidence_id),undefined,signal);
              if(!signal.aborted && version===selectionVersion)proof.replaceChildren(raw(value.evidence || value,"Source evidence"));
            } catch(error) {
              if(!signal.aborted && version===selectionVersion)proof.replaceChildren(notice(error.message,true));
            } finally { view.disabled=false; }
          });details.append(view);if(openProof)view.click();
        }
      }
      relationshipMap = window.LeakScopeRelationshipMap.create({nodes,edges,onNode:inspectNode,onEdge:inspectEdge});
      constellation = window.LeakScopeScope?.graph ? window.LeakScopeScope.graph({nodes,edges,onNode:inspectNode,onEdge:inspectEdge}) : null;
      const graphArea=constellation ? el("details",{class:"scope-secondary"},el("summary",{},"Lanes and overlap matrix"),relationshipMap.element) : relationshipMap.element;
      output.replaceChildren();
      output.append(el("p",{class:"small muted","data-graph-bounds":""},
        (graph.layer?.label || (layerMode==="true" ? "Including research candidates" : "Evidence relationships"))+" · up to 150 assets and 500 relationships · focused assets expand up to 3 relationship hops."));
      if(graph.limited || graph.nodes?.length>150)output.append(notice("Some saved assets or relationships fall outside these bounds. Focus an asset to explore its neighborhood."));
      const caveats=Array.isArray(graph.caveats)?graph.caveats:[graph.caveats].filter(Boolean);
      if(caveats.length)output.append(el("details",{class:"asset-graph-context"},
        el("summary",{class:"small muted"},"Evidence context · "+caveats.length+" notes"),
        el("div",{},caveats.map(c=>el("p",{class:"small muted"},text(c))))));
      if(!nodes.length)output.append(notice("No saved relationships yet. Record scope or explicitly enrich an observed asset to add evidence."));
      if(constellation)output.append(constellation.element);
      output.append(graphArea,selectionPanel,
        pagedDetailsTable({id:"graph-nodes",title:"Nodes",noun:"nodes",headers:["Kind","Asset","Actions"],items:nodes,caption:"Graph nodes",
          sortColumns:[{key:"kind",value:n=>n.kind},{key:"asset",value:n=>n.label||n.value},null],searchText:n=>[n.kind,n.label,n.value].join(" "),
          renderRow:n=>row(n.kind,btn(n.label||n.value,()=>inspectNode(n)),["domain","ip"].includes(n.kind)?btn("Asset tools",()=>openTools(n.value)):"—")}),
        pagedDetailsTable({id:"graph-edges",title:"Relationships",noun:"relationships",headers:["Source","Relation","Target","Confidence","Evidence source","Observed / expires","Proof"],items:edges,caption:"Evidence-backed relationships",
          sortColumns:[{key:"source",value:e=>nodes.find(n=>n.id===e.source)?.label || e.source},{key:"relation",value:e=>e.relation},{key:"target",value:e=>nodes.find(n=>n.id===e.target)?.label || e.target},{key:"confidence",type:"number",value:e=>e.confidence},{key:"evidence_source",value:e=>e.evidence_source},{key:"observed",type:"date",value:e=>e.observed_at},null],
          searchText:edge=>[edge.relation,edge.evidence_source,nodes.find(n=>n.id===edge.source)?.label,nodes.find(n=>n.id===edge.target)?.label].join(" "),
          renderRow:edge=>row(nodes.find(n=>n.id===edge.source)?.label || edge.source,btn(edge.relation,()=>inspectEdge(edge)),nodes.find(n=>n.id===edge.target)?.label || edge.target,text(edge.confidence)+" / 100",edge.evidence_source,
            date(edge.observed_at)+" / "+date(edge.expires_at),edge.evidence_id?btn("View proof",()=>inspectEdge(edge,true)):"—")}));
    }
    form.addEventListener("submit",event=>{event.preventDefault();
      const url=new URL(location.href);url.searchParams.set("include_candidates",includeCandidates.value);
      if(rootId.value.trim())url.searchParams.set("root",rootId.value.trim());else url.searchParams.delete("root");
      history.replaceState(null,"",url);
      load().catch(error=>{if(!signal.aborted)output.replaceChildren(notice(error.message,true));});});await load();
  }
  async function render(root, page) {
    disposePage();const controller=new AbortController();disposePage=()=>controller.abort();
    root.classList.add("asset-page");root.replaceChildren();root.setAttribute("aria-busy","true");
    try {await ({certificates,organizations,relationships}[page] || relationships)(root,controller.signal);}
    catch(error){if(!controller.signal.aborted)root.append(notice(error.message,true));}
    finally {if(!controller.signal.aborted)root.setAttribute("aria-busy","false");}
    return disposePage;
  }
  window.LeakScopeAssets={render,openTools,monitor,addToOrganization};
})();
