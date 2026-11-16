(function () {
  "use strict";
  const BASE = "/api/assets/expansions";
  const SOURCES = [
    ["saved", "Saved evidence", "Local observations and previously collected relationships."],
    ["certificates", "Local certificates", "Retained local CertStream records only; no historical CT lookup."],
    ["crtsh", "Historical certificates · crt.sh", "Previously logged certificate names, including expired certificates. Public CT requests; no API key."],
    ["certspotter", "Unexpired certificates · Cert Spotter", "Unexpired certificate issuances and full DNS-name sets. Limited anonymous evaluation; an API key is recommended for production."],
    ["hackertarget", "Passive hostname records · HackerTarget", "Search existing hostname records. Keyless daily quotas and result limits apply; no target scan."],
    ["urlscan", "Existing public scans · urlscan", "Search previously recorded public scans only. No new scan is submitted; anonymous quotas are limited."],
    ["commoncrawl", "Historical URLs · Common Crawl", "Optional archive index lookup for domain and subdomain names. No archived target is fetched. Uses the shared public index API budget."],
    ["wayback", "Historical URLs · Wayback Machine", "Review archived hostname and URL index records with their capture dates. Uses the shared public index API budget."],
    ["gleif", "Legal entities · GLEIF", "Find legal names, aliases and reported direct children. Not every company has an LEI record; entity relationships do not prove domain ownership."],
    ["web", "Company & website research", "Firecrawl search/pages; shared website identifiers and brand leads. Uses web requests."],
    ["registration", "Domain registration", "Keyless RDAP checks of candidate domains. Uses RDAP requests."],
    ["dns", "DNS relationships", "DNS answers provide relationship clues. Uses DNS requests."],
    ["variants", "Name variants", "Other TLDs and spelling variants; lookalikes remain separate from ownership evidence."],
    ["netlas", "Netlas reverse registration", "Optional paid provider requests. Disabled unless selected with a positive Netlas budget."],
    ["netlas_dns", "Netlas DNS history", "Search apex and subdomain DNS records in selected dated Netlas snapshots. Uses the shared Netlas request budget; no target access."],
    ["passive_dns", "Reverse-IP hostname index", "Query existing reverse-IP hostname records for IP and network seeds. Shared hosting provides leads, not ownership proof."],
    ["reverse_dns", "Reverse DNS · PTR", "Resolve names for discovered or supplied IP addresses. Uses DNS lookups."],
    ["infrastructure", "IP, network & ASN pivots", "Follow public routing records from explicit infrastructure seeds. Network associations remain research leads."],
    ["brute", "DNS wordlist enumeration", "Resolve candidate subdomains from a wordlist with wildcard detection. Optional DNS activity; does not scan services."],
    ["alterations", "Validated hostname alterations", "Resolve label variations of known first-party names. Optional DNS activity; unrelated names remain unverified."]
  ];
  const BUCKETS = [["public_ct", "Public CT", 20, 10000], ["domain_api", "Passive DNS / infrastructure APIs", 20, 10000], ["org_api", "Legal entity APIs", 10, 10000], ["web", "Web / Firecrawl", 8, 10000], ["dns", "DNS", 5000, 1000000], ["rdap", "RDAP", 20, 10000], ["netlas", "Netlas", 0, 10000]];
  const DEFAULT_SOURCES = ["saved", "certificates", "crtsh", "certspotter", "hackertarget", "urlscan", "gleif", "passive_dns"];
  const CATEGORY_LABELS = {domains:"Domain names", related:"Related-domain leads", services:"Third-party services", references:"External references", lookalikes:"Name variants"};
  const TERMINAL = new Set(["succeeded", "failed", "cancelled", "lost", "skipped", "completed", "paused"]);
  let cleanup = () => {};
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, text(value));
  }
  function displayValue(value, label) {
    return value && typeof value === "object" ? dataView(value, label) : text(value);
  }
  function dispose() {cleanup(); cleanup = () => {};}
  const text = value => value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (name === "class") node.className = value;
      else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
      else if (["checked", "disabled", "selected"].includes(name)) node[name] = !!value;
      else node.setAttribute(name, text(value));
    }
    children.flat(Infinity).forEach(value => {if (value != null && value !== false) node.append(value instanceof Node ? value : document.createTextNode(text(value)));});
    return node;
  }
  const btn = (label, fn, attrs = {}) => el("button", {type:"button", class:"btn btn-small", onclick:fn, ...attrs}, label);
  const link = (label, href) => el("a", {href}, label);
  const field = (label, control) => el("label", {class:"field"}, el("span", {class:"field-label"}, label), control);
  const note = (message, error = false) => el("div", {class:"notice"+(error ? " notice-error" : ""), role:error ? "alert" : "status"}, message);
  const input = (name, value = "", type = "text", attrs = {}) => el("input", {name, value, type, class:"input", maxlength:500, ...attrs});
  const select = (name, options, chosen = "") => el("select", {name, class:"select"}, options.map(([value,label]) => el("option", {value,selected:value===chosen}, label)));
  const panel = (title, ...children) => el("section", {class:"panel expansion-panel"}, el("div", {class:"panel-body"}, el("h2", {}, title), ...children));
  const list = value => [...new Set(String(value || "").split(/[,\n]/).map(x=>x.trim()).filter(Boolean))];
  const companiesList = value => [...new Set(String(value || "").split(/\n/).map(x=>x.trim()).filter(Boolean))];
  const date = value => value && !Number.isNaN(new Date(value).valueOf()) ? new Date(value).toLocaleString() : text(value);
  const label = value => value === "shared_hosting_certificate" ? "High-fanout certificate · reference heuristic" : text(value).replace(/_/g," ");
  function category(candidate, expansion) {
    if(Object.hasOwn(CATEGORY_LABELS,candidate.category))return candidate.category;
    const name=String(candidate.domain || "").replace(/^\*\./,"").toLowerCase();
    const seeds=(expansion.seeds || []).filter(seed=>seed.kind==="domain").map(seed=>String(seed.value || "").replace(/^\*\./,"").toLowerCase());
    if(candidate.relation==="lookalike")return "lookalikes";
    if(name && seeds.some(seed=>seed && (name===seed || name.endsWith("."+seed) || candidate.root===seed)))return "domains";
    if(candidate.relation==="service_provider" || (candidate.evidence || []).some(proof=>proof.signal==="service_dependency"))return "services";
    if(candidate.relation==="subdomain")return "references";
    if(["acquired_brand","subsidiary","primary_site","historical"].includes(candidate.relation))return "related";
    if((candidate.evidence || []).some(proof=>["certificate_san","same_certificate","registration_match","acquisition_statement","legal_statement","shared_identifier"].includes(proof.signal)))return "related";
    if((candidate.evidence || []).some(proof=>["web_link","shared_infrastructure"].includes(proof.signal)))return "references";
    return "related";
  }
  function url(options = {}) {
    const params = new URLSearchParams();
    for (const name of ["organization_id", "domain", "company", "ip", "network", "asn", "id", "view"]) if(options[name]) params.set(name,String(options[name]));
    if(options.focused)params.set("focused","1");
    for(const [kind,plural] of [["domain","domains"],["ip","ips"],["network","networks"],["asn","asns"]])if(options[plural]?.length)options[plural].forEach(value=>params.append(kind,value));
    if (options.reference?.type && options.reference.id != null) {params.set("reference_type", options.reference.type);params.set("reference_id",String(options.reference.id));}
    return "/domain-expansion/"+(params.size ? "?"+params : "");
  }
  function open(options = {}) {window.location.assign(url(options));}
  async function request(path = "", body, signal) {
    const response = await fetch(path.startsWith("/api/") ? path : BASE+path, {method:body === undefined ? "GET" : "POST", credentials:"same-origin", signal,
      headers:{Accept:"application/json", ...(body === undefined ? {} : {"Content-Type":"application/json", "X-CSRFToken":document.querySelector('meta[name="csrf-token"]')?.content || ""})},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) {const error=new Error(text(data.error || data.detail || "Expansion request failed"));error.status=response.status;throw error;}
    return data;
  }
  function safeLink(value, title) {
    if (typeof value !== "string") return null;
    try {const u = new URL(value);if (!["https:","http:"].includes(u.protocol) || u.username || u.password) return null;return el("a", {href:u.href,target:"_blank",rel:"noopener noreferrer"}, title || u.hostname+u.pathname);}
    catch (_) {return null;}
  }
  async function render(root) {
    dispose();
    const controller = new AbortController(), signal = controller.signal;
    let timer, filterTimer, detailVersion = 0, current = null, candidates = [], orgs = [], selected = new Set(), savedTotal = 0;
    let pageState={id:null,page:1,page_size:100,category:"discovery",status:"",relation:"",q:""};
    let launchPending=false, launchAttempted=false, acceptedExpansion=null, lastChecked=null, requestedId=null;
    cleanup = () => {controller.abort(); clearTimeout(timer);clearTimeout(filterTimer);};
    window.addEventListener("pagehide", cleanup, {once:true,signal});
    const params = new URLSearchParams(location.search);
    const focused=params.get("focused")==="1";
    let selectedOrg = params.get("organization_id") || "";
    let historyPage=1, historyScope=selectedOrg, historyVersion=0;
    let autoCompanies = [], autoDomains = [];
    const withoutAuto = (values, automatic) => {const keys=new Set(automatic.map(x=>x.toLowerCase()));return values.filter(x=>!keys.has(x.toLowerCase()));};
    root.classList.add("asset-page", "expansion-page");root.replaceChildren();root.setAttribute("aria-busy","true");
    const errors = el("div", {"aria-live":"polite"}), detail = el("div", {"data-expansion-detail":""}), history = el("div", {"data-expansion-history":"",id:"expansion-results",tabindex:"-1"});
    const activityTitle=el("h2",{role:"status","aria-live":"polite","aria-atomic":"true"});
    const activityMessage=el("p",{class:"expansion-activity-message",role:"status","aria-live":"polite"});
    const activityFacts=el("div",{class:"expansion-activity-facts"});
    const activityMeter=el("div"), activityActions=el("div",{class:"asset-actions"});
    const activity=el("section",{class:"panel expansion-activity","data-expansion-activity":"",role:"region","aria-label":"Expansion progress",tabindex:"-1",hidden:true},
      el("div",{class:"panel-body"},activityTitle,activityMessage,activityFacts,activityMeter,activityActions));
    function setCopy(node,value){if(node.textContent!==value)node.textContent=value;}
    function revealActivity(){activity.scrollIntoView?.({block:"start",behavior:"auto"});activity.focus({preventScroll:true});}
    function paintActivity(exp, error="", starting=false) {
      if(signal.aborted)return;
      const ownedFocus=activityActions.contains(document.activeElement);
      activity.hidden=false;
      const state=starting ? "submitting" : (exp?.state || exp?.run?.state || "queued");
      const active=starting || !TERMINAL.has(state);
      activity.dataset.state=state;activity.classList.toggle("is-active",active);
      const titles={submitting:"Starting expansion…",submitted:"Expansion submitted",queued:"Expansion queued",running:"Expansion running",succeeded:"Expansion completed",completed:"Expansion completed",failed:"Expansion failed",cancelled:"Expansion cancelled",lost:"Expansion interrupted",skipped:"Expansion skipped",paused:"Expansion paused"};
      setCopy(activityTitle,titles[state] || "Expansion status: "+label(state));
      const messages={submitting:"Submitting your request. Waiting for the server to confirm the saved expansion.",submitted:"The server saved the expansion. Waiting to read its current status.",queued:"Waiting for a background worker. Progress updates automatically.",running:"Collecting evidence. Saved candidates and request usage update automatically.",succeeded:"Research finished. Review the saved candidates below.",completed:"Research finished. Review the saved candidates below.",failed:"Research stopped. Any saved candidates remain available below.",cancelled:"Research was cancelled. Saved candidates remain available below.",lost:"The background run stopped reporting. Saved candidates remain available below.",skipped:"Research was skipped. See the saved run details.",paused:"Research is paused. Saved results and its checkpoint are retained; adjust limits below and resume when ready."};
      setCopy(activityMessage,error || (state==="paused" && exp?.checkpoint_summary?.reason ? "Paused: "+exp.checkpoint_summary.reason+". Saved results and pending work are retained." : messages[state]) || "Saved expansion status.");
      activityMessage.classList.toggle("is-error",Boolean(error));
      activityFacts.replaceChildren();activityMeter.replaceChildren();activityActions.replaceChildren();
      if(active)activityMeter.append(el("progress",{"aria-label":starting ? "Submitting expansion" : "Expansion is "+state}));
      if(exp && !starting) {
        const processed=Number(exp.checkpoint_summary?.processed ?? exp.run?.progress_current), used=BUCKETS.reduce((sum,[key])=>sum+(Number.isFinite(Number(exp.usage?.[key])) ? Math.max(0,Number(exp.usage[key])) : 0),0);
        const count=current?.id===exp.id ? savedTotal : 0;
        const facts=[];
        if(current?.id===exp.id)facts.push(count+" candidate"+(count===1?"":"s")+" saved");
        if(exp.usage && typeof exp.usage==="object")facts.push(used+" request"+(used===1?"":"s")+" used");
        if(Number.isFinite(processed) && processed>=0)facts.unshift(processed+" seed"+(processed===1?"":"s")+" processed");
        const from=Date.parse(exp.run?.started_at || exp.created_at), until=exp.run?.finished_at ? Date.parse(exp.run.finished_at) : Date.now();
        if(Number.isFinite(from) && Number.isFinite(until)){const elapsed=Math.max(0,Math.floor((until-from)/1000));facts.push("Elapsed "+(elapsed>=60?Math.floor(elapsed/60)+"m ":"")+(elapsed%60)+"s");}
        if(exp.checkpoint_summary?.current_source)facts.push("Source: "+label(exp.checkpoint_summary.current_source));
        if(Number.isFinite(exp.checkpoint_summary?.pending_seeds))facts.push(exp.checkpoint_summary.pending_seeds+" source queries pending");
        if(lastChecked)facts.push("Last checked "+new Date(lastChecked).toLocaleTimeString());
        facts.forEach(value=>activityFacts.append(el("span",{},value)));
        activityActions.append(link("Saved expansion",url({id:exp.id})));
        if(exp.run?.id)activityActions.append(link("Background run","/runs/?id="+encodeURIComponent(exp.run.id)));
        if(error)activityActions.append(btn("Retry status",()=>refreshDetail(exp.id)));
        if(exp.resumable && ["paused","failed","lost","cancelled"].includes(state))activityActions.append(link("Adjust limits & resume","#expansion-resume"));
        if(exp.partial)activityFacts.append(el("span",{},"Partial results · source errors or limits are recorded below"));
        if(["failed","lost","skipped"].includes(state) && exp.run?.error)activityFacts.append(el("span",{class:"expansion-activity-error"},text(exp.run.error)));
      }
      if(ownedFocus)activity.focus({preventScroll:true});
    }
    function schedulePoll(id){clearTimeout(timer);if(!signal.aborted)timer=setTimeout(()=>refreshDetail(id),2500);}
    async function refreshDetail(id) {
      try{await loadDetail(id);}catch(_){/* loadDetail retains the last snapshot and schedules its retry. */}
    }
    const names = el("textarea", {name:"companies",class:"input",rows:3,maxlength:8000,placeholder:"Company, legal name or brand\nOne per line"}, params.get("company") || "");
    const domains = el("textarea", {name:"domains",class:"input",rows:3,maxlength:260000,placeholder:"example.com\nportal.other-brand.net"}, params.getAll("domain").join("\n"));
    const organization = select("organization_id", [["","Research without an organization"]]);
    const country = input("country", ""), industry = input("industry", "");
    const demo = input("demo", "", "checkbox"), maxDepth = input("max_depth", "2", "number", {min:0,max:10}), maxCandidates = input("max_candidates", "0", "number", {min:0,max:10000000});
    const ips=el("textarea",{name:"ips",class:"input",rows:3,maxlength:16000,placeholder:"192.0.2.5\n2001:db8::5"},params.getAll("ip").join("\n")), networks=el("textarea",{name:"networks",class:"input",rows:3,maxlength:16000,placeholder:"192.0.2.0/24"},params.getAll("network").join("\n")), asns=el("textarea",{name:"asns",class:"input",rows:3,maxlength:16000,placeholder:"AS64500"},params.getAll("asn").join("\n"));
    const webResults=input("web_results","20","number",{min:1,max:100});
    const runtime=input("runtime_seconds","1800","number",{min:60,max:86400}), dnsRate=input("dns_rate","20","number",{min:1,max:100});
    const wordlist=el("textarea",{name:"wordlist",class:"input",rows:5,maxlength:640000,placeholder:"Leave blank for the built-in common labels.\nportal\nvpn\nmail"});
    const sourceBoxes = new Map(), budgetInputs = new Map();
    const sourceOptions = el("div", {class:"expansion-sources"}, SOURCES.map(([name,title,description]) => {
      const box = input("source_"+name,"","checkbox");box.checked=DEFAULT_SOURCES.includes(name);sourceBoxes.set(name,box);
      return el("label",{class:"expansion-source"},box,el("span",{},el("strong",{},title),el("small",{},description)));
    }));
    const archive=input("commoncrawl_index","CC-MAIN-2026-43","text",{maxlength:24,placeholder:"CC-MAIN-2026-43"});
    const archiveOptions=el("details",{hidden:true},el("summary",{},"Common Crawl archive"),field("Archive index ID",archive),el("p",{class:"small muted","data-history-preview":""},""));
    const netlasDates=el("textarea",{name:"netlas_indices",class:"input",rows:3,maxlength:300,placeholder:"2024-01-01\n2023-01-01"});
    const netlasStatus=el("div",{"aria-live":"polite"}), netlasChoices=el("div",{class:"expansion-sources"});
    const netlasFilter=input("netlas_snapshot_filter","","search",{placeholder:"Filter dates, e.g. 2024"});
    let netlasSnapshots=[];
    function paintSnapshots(){
      const chosen=new Set(list(netlasDates.value));
      netlasChoices.replaceChildren(...netlasSnapshots.filter(s=>s.label.includes(netlasFilter.value.trim())).map(s=>{
        const box=input("netlas_snapshot",s.label,"checkbox");box.checked=chosen.has(s.label);
        box.addEventListener("change",()=>{const values=new Set(list(netlasDates.value));if(box.checked)values.add(s.label);else values.delete(s.label);netlasDates.value=[...values].join("\n");updateCosts();});
        return el("label",{class:"check-row"},box,el("span",{},s.label+(s.is_default ? " · default" : "")+(s.state ? " · "+s.state : "")));
      }));
    }
    netlasFilter.addEventListener("input",paintSnapshots);
    netlasDates.addEventListener("input",paintSnapshots);
    const loadNetlas=btn("Load available DNS snapshots",async()=>{
      if(loadNetlas.disabled)return;loadNetlas.disabled=true;netlasStatus.replaceChildren(note("Loading Netlas DNS snapshot metadata…"));
      try{const data=await request("/netlas-indices",{},signal);if(signal.aborted)return;netlasSnapshots=data.indices || [];paintSnapshots();netlasStatus.replaceChildren(note(netlasSnapshots.length+" DNS snapshots available · "+(data.cached ? "cached metadata, no provider request" : "one metadata request")+(netlasSnapshots.length ? ". Select dates below or enter labels directly." : ". No DNS snapshot dates were returned; you can enter known date labels directly.")));}
      catch(error){if(!signal.aborted)netlasStatus.replaceChildren(note(error.message,true));}finally{loadNetlas.disabled=false;}
    });
    const netlasOptions=el("details",{hidden:true,"data-netlas-dns-options":""},el("summary",{},"Netlas DNS snapshots"),
      el("p",{class:"small muted"},"Optional metadata loading makes one Netlas request, cached for 15 minutes; this is separate from the expansion search budget. Search pages contain up to 20 DNS documents and account permissions apply."),loadNetlas,netlasStatus,
      field("Filter available snapshot dates",netlasFilter),el("div",{class:"netlas-snapshot-picker"},netlasChoices),
      field("Netlas DNS snapshot dates (one per line; maximum 24)",netlasDates),
      el("p",{class:"small muted"},"Blank uses only Netlas’s default DNS index. Choose older dates for history. Set a positive Netlas request limit under Provider request limits; it is shared across snapshots, pages and reverse registration. Indexed time is not first-ever appearance."));
    const costs = el("div", {class:"expansion-costs","aria-live":"polite"}), bootstrapInfo=el("div",{"data-company-bootstrap":"","aria-live":"polite"});
    let explicitWebChoice=false;const explicitInfrastructureChoices=new Set();
    const budgetFields = BUCKETS.map(([name,title,value,maximum]) => {const control=input("budget_"+name,String(value),"number",{min:0,max:maximum});budgetInputs.set(name,control);return field(title+" request limit",control);});
    const hint = note("Start with passive certificate, hostname and legal-entity sources. Local certificates search retained observations. Website dependencies and external references stay separate from domain discovery. Candidates need review; no watches or target captures start automatically.");
    const submit = el("button", {class:"btn btn-primary",type:"submit"}, "Start expansion");
    const formOutput = el("div", {"aria-live":"polite"}), orgInfo = el("div");
    const form = el("form", {class:"asset-form expansion-form"}, field("Save research under organization",organization),orgInfo,
      el("div",{class:"expansion-columns"},field("Company names / brands (one per line)",names),field("Domains / subdomains (one per line)",domains)),
      el("details",{},el("summary",{},"Company disambiguation (optional)"),el("div",{class:"expansion-columns"},field("Country (ISO2 for registry, e.g. US)",country),field("Industry",industry))),
      el("details",{},el("summary",{},"Infrastructure seeds (optional)"),el("p",{class:"small muted"},"Supply IP addresses, CIDRs or ASNs to begin infrastructure research. Public routing and shared-hosting relationships require review."),el("div",{class:"expansion-columns"},field("IP addresses (one per line)",ips),field("Networks / CIDRs (one per line)",networks),field("ASNs (one per line)",asns))),
      el("details",{open:true},el("summary",{},"Discovery sources & techniques"),el("fieldset",{},el("legend",{},"Discovery sources"),sourceOptions,archiveOptions,netlasOptions)),bootstrapInfo,
      el("details",{},el("summary",{},"Provider request limits"),el("p",{class:"small muted"},"Absolute ceilings shared across every seed, page and recursive step. Provider quotas still apply. Firecrawl and Netlas usage can consume account credits; DNS activity has its own control."),el("div",{class:"expansion-columns"},...budgetFields)),
      el("details",{},el("summary",{},"Saved results & execution"),el("div",{class:"expansion-columns"},field("Expansion depth",maxDepth),field("Saved candidate storage quota (0 = no application limit)",maxCandidates),field("Runtime allowance (seconds)",runtime),field("Web results per search",webResults)),el("p",{class:"small muted"},"Result pages do not limit collection or storage. A reached runtime, request or storage ceiling pauses with pending work retained for resume.")),
      el("details",{},el("summary",{},"DNS enumeration settings"),field("DNS lookups per second",dnsRate),field("Wordlist labels (one per line; maximum 10,000)",wordlist),el("p",{class:"small muted"},"Wordlist and hostname-alteration techniques run only when selected. Wildcard answers are checked before accepting guessed names.")),
      el("label",{class:"check-row"},demo,el("span",{},"Demo · minimum real requests and one candidate; no recursive expansion")),costs,submit,formOutput);
    const setupDisclosure=el("details",{"data-expansion-setup":"",open:!params.get("id") && params.get("view")!=="results"},el("summary",{},"Start an expansion"),form);
    root.append(el("div",{class:"page-heading"},el("div",{},el("h1",{},"Domain expansion"),el("p",{},"Find subdomains and differently named domains connected to a company, brand or organization."))),
      el("ol",{class:"expansion-flow","aria-label":"Expansion workflow"},["Seed an identity","Collect evidence","Review candidates","Save or monitor"].map(value=>el("li",{},value))),activity,hint,errors,panel("Expansion setup",setupDisclosure),detail,panel("Expansion results",history));
    function options() {
      const sources = [...sourceBoxes].filter(([,box])=>box.checked).map(([name])=>name), budgets = {};
      for (const [bucket,control] of budgetInputs) budgets[bucket] = Number(control.value);
      if(!sources.some(source=>["crtsh","certspotter"].includes(source)))budgets.public_ct=0;
      if(!sources.some(source=>["hackertarget","urlscan","passive_dns","infrastructure","commoncrawl","wayback"].includes(source)))budgets.domain_api=0;
      if(!sources.includes("gleif"))budgets.org_api=0;
      if(!sources.includes("web"))budgets.web=0;
      if(!sources.some(source=>["dns","reverse_dns","brute","alterations"].includes(source)))budgets.dns=0;
      if(!sources.includes("registration"))budgets.rdap=0;
      if(!sources.some(source=>["netlas","netlas_dns"].includes(source)))budgets.netlas=0;
      if(demo.checked) for(const bucket of Object.keys(budgets))budgets[bucket]=Math.min(budgets[bucket],1);
      return {sources,budgets,commoncrawl_index:archive.value.trim(),netlas_indices:list(netlasDates.value),demo:demo.checked,max_depth:demo.checked ? 0 : Number(maxDepth.value),max_candidates:demo.checked ? 1 : Number(maxCandidates.value),runtime_seconds:Number(runtime.value),web_results:demo.checked ? 1 : Number(webResults.value),dns_rate:Number(dnsRate.value),wordlist:companiesList(wordlist.value)};
    }
    function updateCosts() {
      const infrastructureSeeds=[ips,networks,asns].some(control=>list(control.value).length>0);
      for(const source of ["infrastructure","reverse_dns"])if(!explicitInfrastructureChoices.has(source))sourceBoxes.get(source).checked=infrastructureSeeds;
      const companyOnly=companiesList(names.value).length>0 && list(domains.value).length===0 && (focused || autoDomains.length===0);
      const web=sourceBoxes.get("web");if(!explicitWebChoice)web.checked=companyOnly;
      bootstrapInfo.replaceChildren();
      if(companyOnly)bootstrapInfo.append(note(web.checked ? "Company-only searches use a bounded web identity lookup to find an initial website. Passive datasets require a domain seed before hostname discovery can proceed." : "Entity-only coverage · Web identity lookup is disabled. Company and registry leads may be found, but the initial domain remains unresolved until you supply a domain seed or enable Web research."));
      const settings=options();archiveOptions.hidden=!sourceBoxes.get("commoncrawl").checked;netlasOptions.hidden=!sourceBoxes.get("netlas_dns").checked;
      archiveOptions.querySelector("[data-history-preview]").textContent=demo.checked ? "Demo: one minimal archive page request; at most one name retained. No metadata request and no target access." : "Archive "+archive.value.trim()+": one pagination metadata request, then one request per archive page, bounded by the shared "+settings.budgets.domain_api+" public index requests. No target access.";
      costs.replaceChildren(el("strong",{},"Whole-run request ceilings"),el("span",{},BUCKETS.map(([key,title])=>title+": "+settings.budgets[key]).join(" · ")),
        el("small",{},demo.checked ? "Demo samples selected sources with minimal real usage. Results are intentionally incomplete." : "Shared across all seeds, pages and recursive steps. DNS activity, provider requests and saved-result storage are separate. Request counts are not a monetary quote."));
    }
    const chooseWeb=()=>{explicitWebChoice=true;updateCosts();};
    sourceBoxes.get("web").addEventListener("input",chooseWeb);sourceBoxes.get("web").addEventListener("change",chooseWeb);
    for(const source of ["infrastructure","reverse_dns"]){const choose=()=>{explicitInfrastructureChoices.add(source);updateCosts();};sourceBoxes.get(source).addEventListener("input",choose);sourceBoxes.get(source).addEventListener("change",choose);}
    for(const control of [names,domains,ips,networks,asns])control.addEventListener("input",updateCosts);
    form.addEventListener("input",updateCosts);form.addEventListener("change",updateCosts);updateCosts();
    function launchRequest(path,payload){launchAttempted=true;return request(path,payload,signal);}
    async function busy(button,output,fn,launch=false) {
      if(button.disabled || signal.aborted || (launch && launchPending))return;
      button.disabled=true;const originalLabel=button.textContent;
      if(launch){clearTimeout(timer);detailVersion++;requestedId=null;launchPending=true;launchAttempted=false;acceptedExpansion=null;lastChecked=null;button.textContent="Starting expansion…";paintActivity(null,"",true);revealActivity();}
      try {await fn();}catch(error){if(!signal.aborted){
        output.append(note(error.message,true));
        if(launch && acceptedExpansion){const saved=current?.id===acceptedExpansion.id ? current : acceptedExpansion;paintActivity(saved,"Expansion saved. A view refresh failed; retrying the saved status automatically without starting new research.");requestedId=acceptedExpansion.id;schedulePoll(acceptedExpansion.id);}
        else if(launch){
          const rejected=!launchAttempted || (error.status>=400 && error.status<500);
          activity.dataset.state=rejected ? "request_failed" : "request_unknown";activity.classList.remove("is-active");
          setCopy(activityTitle,rejected ? "Expansion could not start" : "Expansion start unconfirmed");
          setCopy(activityMessage,rejected ? (error.message || "Request rejected.") : "The server may have accepted the request before its response was lost. Check saved expansions before starting again.");
          activityMessage.classList.add("is-error");activityMeter.replaceChildren();
          if(!rejected)activityActions.append(btn("Check saved expansions",()=>busy(activityActions.querySelector("button"),errors,async()=>{await loadHistory();if(!signal.aborted){history.scrollIntoView?.({block:"start"});history.focus({preventScroll:true});}})));
        }
      }}
      finally {if(launch)launchPending=false;if(!signal.aborted){button.disabled=false;button.textContent=originalLabel;}}
    }
    async function loadOrganizations() {
      const result=await request("/api/assets/organizations",undefined,signal);if(signal.aborted)return;
      orgs=(result.results || []).filter(o=>!o.status || o.status==="active");
      organization.replaceChildren(el("option",{value:""},"Research without an organization"),...orgs.map(o=>el("option",{value:o.id,selected:o.id===selectedOrg},o.label || o.value)));
      if(selectedOrg)await loadOrganization(selectedOrg,true);
    }
    async function loadOrganization(id,prefill) {
      prefill=prefill && !focused;
      selectedOrg=id;orgInfo.replaceChildren();
      if(!id){names.value=withoutAuto(companiesList(names.value),autoCompanies).join("\n");domains.value=withoutAuto(list(domains.value),autoDomains).join("\n");autoCompanies=[];autoDomains=[];submit.disabled=false;updateCosts();return;}
      submit.disabled=true;orgInfo.append(note("Loading organization context…"));
      const result=await request("/api/assets/organizations/"+encodeURIComponent(id),undefined,signal);
      if(signal.aborted || selectedOrg!==id)return;
      const org=result.organization || {};const canonicalId=org.id || id;
      if(canonicalId!==id) {selectedOrg=canonicalId;if(![...organization.options].some(o=>o.value===canonicalId))organization.append(el("option",{value:canonicalId},org.value));organization.value=canonicalId;}
      orgInfo.replaceChildren(link("Open organization","/organizations/?id="+encodeURIComponent(canonicalId)));
      if(focused)orgInfo.append(note("Focused research uses only the supplied seeds. The selected organization records the research association; its other names and domains are not added to this search."));
      if(prefill) {
        const manualCompanies=withoutAuto(companiesList(names.value),autoCompanies), manualDomains=withoutAuto(list(domains.value),autoDomains);
        const companyContext=[...new Set([org.value,...(org.attributes?.aliases || []).slice(0,8)].filter(Boolean))];
        const domainContext=[...new Set((result.domains || []).map(d=>d.name).filter(Boolean))].slice(0,Math.max(0,1000-manualDomains.length));
        autoCompanies=withoutAuto(companyContext,manualCompanies);autoDomains=withoutAuto(domainContext,manualDomains);
        names.value=[...new Set([...manualCompanies,...companyContext])].join("\n");
        domains.value=[...new Set([...manualDomains,...domainContext])].join("\n");
        orgInfo.append(el("p",{class:"small muted"},"Organization context includes its saved name, first 8 aliases and up to 1,000 domains. These automatic seeds are replaced when you choose another organization; manually added seeds remain."));
        if((org.attributes?.aliases || []).length>8 || (result.domains || []).length>domainContext.length)orgInfo.append(note("Organization context was bounded to the displayed aliases and domain limit."));
      }
      const active=!org.status || org.status==="active";submit.disabled=!active;
      if(!active)orgInfo.append(note("This organization is "+text(org.status)+". Restore it before starting research."));
      updateCosts();
    }
    organization.addEventListener("change",()=>{const next=organization.value;submit.disabled=true;loadOrganization(next,true).then(loadHistory).catch(e=>{if(!signal.aborted)formOutput.append(note(e.message,true));});});
    async function loadHistory(nextPage=historyPage) {
      if(historyScope!==selectedOrg){historyScope=selectedOrg;nextPage=1;}
      const scope=selectedOrg, version=++historyVersion, query=new URLSearchParams();
      if(scope)query.set("organization_id",scope);
      if(nextPage>1){query.set("page",String(nextPage));query.set("page_size","100");}
      const result=await request(query.size ? "?"+query : "",undefined,signal);if(signal.aborted || version!==historyVersion || scope!==selectedOrg)return;
      history.replaceChildren();
      historyPage=Number.isInteger(result.page) ? result.page : nextPage;
      const rows=result.results || result.expansions || [];
      if(!rows.length)history.append(el("p",{class:"muted"},"No saved expansions in this view. Start research explicitly above."));
      for(const run of rows)history.append(el("article",{class:"expansion-history-row"},
        el("div",{},el("strong",{},run.organization_name || (run.seeds || []).map(s=>s.value).join(", ") || "Domain research"),el("small",{},date(run.created_at))),
        el("span",{class:"pill"},run.state || run.run?.state || "queued"),
        btn("Open results",()=>loadDetail(run.id).catch(e=>{if(!signal.aborted)errors.append(note(e.message,true));})),link("Permanent link",url({id:run.id}))));
      if(Number.isInteger(result.page) && Number.isInteger(result.pages) && Number.isFinite(result.count)){
        const page=historyPage, pages=Math.max(1,result.pages), pager=el("nav",{class:"expansion-pagination","data-history-pagination":"","aria-label":"Expansion history pages"});
        const previous=btn("Previous history page",()=>busy(previous,errors,()=>loadHistory(page-1)),{disabled:page<=1});
        const next=btn("Next history page",()=>busy(next,errors,()=>loadHistory(page+1)),{disabled:page>=pages});
        pager.append(previous,el("span",{role:"status","aria-live":"polite"},"Page "+page+" of "+pages+" · "+result.count+" saved expansions"),next);history.append(pager);
      }
    }
    async function applyResult(result) {
      if(signal.aborted)return;
      clearTimeout(filterTimer);
      const id=result.expansion?.id || result.id;
      if(!id)throw new Error("The expansion response did not include an ID.");
      if(launchPending)acceptedExpansion=result.expansion || {id,state:"submitted"};
      const locationURL=new URL(location.href);locationURL.searchParams.set("id",id);historyReplace(locationURL);
      if(result.expansion && Array.isArray(result.candidates)){
        detailVersion++;setupDisclosure.open=false;
        if(Number.isInteger(result.page)){
          if(current?.id!==id){candidates=[];selected.clear();detail.replaceChildren();}
          current=result.expansion;acceptedExpansion=result.expansion;savedTotal=Number(result.counts?.total) || 0;paintActivity(current);
          await loadDetail(id);
        }else showDetail(result);
      }else await loadDetail(id);
      if(signal.aborted)return;
      revealActivity();
    }
    function historyReplace(locationURL) {window.history.replaceState(null,"",locationURL);}
    form.addEventListener("submit",event=>{event.preventDefault();busy(submit,formOutput,async()=>{
      formOutput.replaceChildren();const companies=organization.value ? withoutAuto(companiesList(names.value),autoCompanies) : companiesList(names.value), seeds=organization.value ? withoutAuto(list(domains.value),autoDomains) : list(domains.value), opts=options();
      if(companies.length>(organization.value ? 20 : 30))throw new Error("Enter at most "+(organization.value ? "20 additional" : "30")+" company names.");
      if(seeds.length>1000)throw new Error("Enter at most 1,000 domain seeds.");
      const ipSeeds=list(ips.value), networkSeeds=list(networks.value), asnSeeds=list(asns.value);
      if(!companies.length && !seeds.length && !ipSeeds.length && !networkSeeds.length && !asnSeeds.length && !organization.value && !params.get("reference_id"))throw new Error("Enter a company, domain or infrastructure seed, or choose an organization.");
      if(opts.sources.includes("commoncrawl")&&!/^CC-MAIN-20[0-9]{2}-(?:0[1-9]|[1-4][0-9]|5[0-3])$/.test(opts.commoncrawl_index))throw new Error("Enter a Common Crawl archive ID such as CC-MAIN-2026-43.");
      if(opts.wordlist.length>10000)throw new Error("Enter at most 10,000 wordlist labels.");
      if(!opts.sources.length)throw new Error("Choose at least one discovery source.");
      if(opts.sources.includes("netlas") && opts.budgets.netlas<1)throw new Error("Set a positive Netlas request limit to enable paid reverse registration.");
      const payload={companies,domains:seeds,ips:ipSeeds,networks:networkSeeds,asns:asnSeeds,...opts,context:{country:country.value.trim(),industry:industry.value.trim()}};
      if(focused)payload.focused=true;
      if(organization.value)payload.organization_id=organization.value;
      if(params.get("reference_type") && params.get("reference_id"))payload.reference={type:params.get("reference_type"),id:params.get("reference_id")};
      await applyResult(await launchRequest("",payload));if(!signal.aborted)await loadHistory();
    },true);});
    async function loadDetail(id) {
      requestedId=id;const version=++detailVersion;clearTimeout(timer);
      if(pageState.id!==id)pageState={id,page:1,page_size:100,category:"discovery",status:"",relation:"",q:""};
      const pageQuery=new URLSearchParams(Object.entries(pageState).filter(([key,value])=>key!=="id" && value!==""));
      try {
        const result=await request("/"+encodeURIComponent(id)+"?"+pageQuery,undefined,signal);
        if(signal.aborted || version!==detailVersion)return;showDetail(result);
      } catch(error) {
        if(signal.aborted || version!==detailVersion)return;
        const snapshot=current?.id===id ? current : acceptedExpansion?.id===id ? acceptedExpansion : null;
        if(snapshot){paintActivity(snapshot,"Status refresh failed. Last known state: "+label(snapshot.state || "queued")+". Retrying automatically; this does not restart the expansion.");schedulePoll(id);}
        throw error;
      }
    }
    function showDetail(result) {
      clearTimeout(timer);
      const sameSession=current?.id===result.expansion?.id;
      const previousInfrastructure=sameSession ? detail.querySelector("[data-infrastructure-leads]") : null;
      const infrastructureState={open:previousInfrastructure?.open || false,page:Number(previousInfrastructure?.dataset.page) || 1};
      const savedReviewTarget=sameSession ? detail.querySelector('[name="review_organization_id"]')?.value || "" : "";
      const savedReviewTags=sameSession ? detail.querySelector('[name="candidate_tags"]')?.value || "" : "";
      const active=document.activeElement;
      const savedFocus=sameSession && detail.contains(active) && ["INPUT","SELECT","TEXTAREA"].includes(active.tagName) ?
        {tag:active.tagName,name:active.name,value:active.value,start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection} : null;
      const evidenceState=new Map();
      function rememberEvidence(container) {
        for(const article of container.querySelectorAll('[data-candidate-id]'))evidenceState.set(article.dataset.candidateId,[...article.querySelectorAll("details")].map(item=>item.open));
      }
      if(sameSession)rememberEvidence(detail);
      current=result.expansion; candidates=Array.isArray(result.candidates) ? result.candidates : [];
      const paginated=Number.isInteger(result.page);
      if(pageState.id!==current?.id)pageState={id:current?.id,page:1,page_size:100,category:"discovery",status:"",relation:"",q:""};
      if(paginated){pageState.page=result.page;pageState.page_size=result.page_size || pageState.page_size;}
      savedTotal=Number.isFinite(result.counts?.total) ? result.counts.total : (Number.isFinite(result.count) && !paginated ? result.count : candidates.length);
      if(!current)throw new Error("The saved expansion is unavailable.");
      const ids=new Set(candidates.map(c=>c.id));selected=new Set([...selected].filter(id=>ids.has(id)));
      const oldQuery=paginated ? pageState.q : sameSession ? detail.querySelector('[name="candidate_query"]')?.value || "" : "", oldStatus=paginated ? pageState.status : sameSession ? detail.querySelector('[name="candidate_status"]')?.value || "" : "", oldRelation=paginated ? pageState.relation : sameSession ? detail.querySelector('[name="candidate_relation"]')?.value || "" : "", oldCategory=paginated ? pageState.category : sameSession ? detail.querySelector('[name="candidate_category"]')?.value || "discovery" : "discovery";
      detail.replaceChildren();
      const state=current.state || current.run?.state || "queued", running=!TERMINAL.has(state);
      requestedId=current.id;lastChecked=Date.now();paintActivity(current);
      const counts={domains:0,related:0,services:0,references:0,lookalikes:0};if(result.counts)for(const key of Object.keys(counts))counts[key]=Number(result.counts[key]) || 0;else for(const candidate of candidates)counts[category(candidate,current)]++;
      const discoveryCounts=el("div",{class:"expansion-result-counts","data-discovery-counts":"","aria-label":"Saved result groups"},
        el("strong",{},counts.domains+" domain names"),el("strong",{},counts.related+" related-domain leads"),
        el("span",{},counts.services+" third-party services"),el("span",{},counts.references+" external references"),el("span",{},counts.lookalikes+" name variants"));
      const message=el("div",{"aria-live":"polite"});
      const actions=el("div",{class:"asset-actions"},link("Permanent link",url({id:current.id})),btn("Refresh results",()=>busy(refresh,message,()=>loadDetail(current.id))));
      const refresh=actions.querySelector("button");
      if(current.organization_id)actions.append(link("Open organization","/organizations/?id="+encodeURIComponent(current.organization_id)),link("Relationship graph","/relationships/?root="+encodeURIComponent(current.organization_id)));
      if(current.run?.id)actions.append(link("Background run","/runs/?id="+encodeURIComponent(current.run.id)));
      if(running){const cancel=btn("Cancel expansion",()=>busy(cancel,message,async()=>{await applyResult(await request("/"+encodeURIComponent(current.id)+"/cancel",{},signal));}));actions.append(cancel);}
      const usage=el("dl",{class:"expansion-usage"});for(const [bucket,title] of BUCKETS)usage.append(el("div",{},el("dt",{},title),el("dd",{},text(current.usage?.[bucket] || 0)+" / "+text(current.budgets?.[bucket] ?? 0))));
      const summary=panel("Research results",el("div",{class:"expansion-summary"},el("strong",{},current.organization_name || "Domain research"),el("span",{class:"pill"},state)),
        el("p",{class:"small muted"},"Started "+date(current.created_at)+" · "+(current.seeds || []).map(s=>s.value).join(", ")),actions,discoveryCounts,usage,
        current.partial ? note("Partial results · some sources, limits or errors prevented complete collection. Saved evidence remains available.") : null,
        ...(current.notes || []).map(n=>typeof n === "object" && n && !n.message ? dataView(n, "Research note") : note(typeof n==="string" ? n : n.message || text(n))),
        note("Candidates describe evidenced relationships; they are not automatic ownership claims. Shared infrastructure, third-party services and lookalikes require separate review."),message);
      if(current.run?.id && window.LeakScopeRunLogs)summary.append(window.LeakScopeRunLogs.panel({...current.run,kind:"domain_expansion",expansion:{id:current.id,state:current.state,saved_candidates:savedTotal,...(current.checkpoint_summary || {})},result:current.run.result},{key:"expansion-"+current.id}));
      const query=input("candidate_query",oldQuery), status=select("candidate_status",[["","All review states"],...["pending","accepted","rejected"].map(v=>[v,label(v)])],oldStatus);
      const relation=select("candidate_relation",[["","All relationships"],...[...new Set(["subdomain","related","primary_site","subsidiary","acquired_brand","historical","service_provider","lookalike",...candidates.map(c=>c.relation)])].filter(Boolean).sort().map(v=>[v,label(v)])],oldRelation);
      const resultGroup=select("candidate_category",[["discovery","Domain discovery"],...Object.entries(CATEGORY_LABELS),["all","All saved entries"]],oldCategory);
      const groupExplanation=el("p",{class:"small muted","data-category-explanation":""});
      const target=select("review_organization_id",[["","Choose destination organization"],...orgs.map(o=>[o.id,o.label || o.value])],current.organization_id || savedReviewTarget);
      if(current.organization_id && !orgs.some(o=>o.id===current.organization_id))target.append(el("option",{value:current.organization_id,selected:true},current.organization_name || current.organization_id));
      const tagInput=input("candidate_tags",savedReviewTags), selection=el("span",{class:"small muted","aria-live":"polite"}), candidateList=el("div",{"data-candidate-list":""});
      function selectedIds() {return [...selected];}
      const accept=btn("Add to organization",()=>review("accept")),reject=btn("Reject selected",()=>review("reject")),tag=btn("Apply tags",()=>review("tag"));
      const expand=btn("Expand selected",()=>busy(expand,message,async()=>{await applyResult(await launchRequest("/"+encodeURIComponent(current.id)+"/expand",{candidate_ids:selectedIds(),...options()}));if(!signal.aborted)await loadHistory();},true));
      const expandable=candidate=>["domains","related"].includes(category(candidate,current));
      function updateSelected() {selection.textContent=selected.size+" selected"+(paginated ? " on this page" : "");accept.disabled=!selected.size || !target.value;reject.disabled=!selected.size;tag.disabled=!selected.size || !list(tagInput.value).length;expand.disabled=!selected.size || candidates.some(candidate=>selected.has(candidate.id) && !expandable(candidate));expand.title=expand.disabled && selected.size ? "Only domain names and related-domain leads can be expanded. Clear service, reference or name-variant selections first." : "Expand selected domain discoveries";}
      async function review(action) {
        const button={accept,reject,tag}[action];await busy(button,message,async()=>{
          const payload={candidate_ids:selectedIds(),action};if(action==="accept")payload.organization_id=target.value;if(action==="tag")payload.tags=list(tagInput.value);
          const reviewed=await request("/"+encodeURIComponent(current.id)+"/review",payload,signal);if(signal.aborted)return;selected.clear();
          if(paginated)await loadDetail(current.id);else await applyResult(reviewed);if(!signal.aborted)detail.querySelector('[aria-live="polite"]')?.append(note(action==="accept" ? "Selected relationships saved under the organization with the review decision recorded." : action==="reject" ? "Selected candidates rejected. Future runs retain this decision." : "Tags saved."));
        });updateSelected();
      }
      target.addEventListener("change",updateSelected);tagInput.addEventListener("input",updateSelected);
      function visible() {if(paginated)return candidates;const needle=query.value.trim().toLowerCase();return candidates.filter(c=>(resultGroup.value==="all" || (resultGroup.value==="discovery" ? ["domains","related"].includes(category(c,current)) : category(c,current)===resultGroup.value)) && (!status.value || c.status===status.value) && (!relation.value || c.relation===relation.value) && (!needle || [c.domain,c.relation,...(c.tags || [])].join(" ").toLowerCase().includes(needle)));}
      function drawCandidates() {
        rememberEvidence(candidateList);candidateList.replaceChildren();const shown=visible();
        groupExplanation.textContent=resultGroup.value==="discovery" ? "Domain discovery shows names under the supplied domain scope and sourced leads to other domains. Third-party services, ordinary outbound links and name variants have separate views; their counts do not establish organization ownership." : resultGroup.value==="services" ? "These are website or DNS service dependencies. Attaching one records a relationship to the organization; it does not establish that the organization owns the provider's domain." : resultGroup.value==="references" ? "These are external links or weak correlations. They need additional evidence before being treated as related organization domains." : resultGroup.value==="lookalikes" ? "These are generated name variations. Similar names do not establish ownership, and these entries do not automatically expand." : "Review each source and relationship before accepting a domain. A related-domain lead remains unverified until corroborated.";
        if(!shown.length)candidateList.append(el("p",{class:"muted"},candidates.length ? (resultGroup.value==="discovery" && !counts.domains && !counts.related ? "No domain discovery candidates yet. Other saved entry types are available under Result group; review source notes and request limits." : "No candidates match these filters.") : "No candidates saved yet. Review source notes and request limits."));
        for(const c of shown) {
          const box=input("candidate_id",c.id,"checkbox",{"aria-label":"Select "+c.domain});box.checked=selected.has(c.id);box.addEventListener("change",()=>{if(box.checked)selected.add(c.id);else selected.delete(c.id);updateSelected();});
          const child=expandable(c) ? btn("Expand from here",()=>busy(child,message,async()=>{await applyResult(await launchRequest("/"+encodeURIComponent(current.id)+"/expand",{candidate_ids:[c.id],...options()}));if(!signal.aborted)await loadHistory();},true)) : null;
          const monitor=btn("Monitor",()=>{if(window.LeakScopeAssets?.monitor)window.LeakScopeAssets.monitor(c.domain);else message.append(note("Monitoring controls are unavailable. Open Monitoring from the navigation.",true));});
          const evidence=el("details",{class:"expansion-evidence"},el("summary",{},"Evidence & discovery path ("+(c.evidence || []).length+")"),
            ...(c.evidence || []).map(proof=>el("article",{class:"expansion-proof"},el("strong",{},label(proof.signal || proof.source)),el("p",{class:"small muted"},text(proof.source)+" · "+date(proof.observed_at)),
              safeLink(proof.url || proof.source_url),proof.index_label ? el("p",{class:"small"},"Snapshot: "+proof.index_label+" · "+(proof.observed_at ? "Provider indexed: "+date(proof.observed_at) : "Indexed time unknown")) : null,
              proof.dns_records && Object.values(proof.dns_records).some(values=>values.length) ? el("dl",{class:"netlas-dns-records"},Object.entries(proof.dns_records).map(([kind,values])=>el("div",{},el("dt",{},kind),el("dd",{},values.map(value=>el("div",{},displayValue(value,"DNS "+kind+" value"))))))) : proof.source==="netlas_dns" ? el("p",{class:"small muted"},"No DNS record values were returned in this snapshot. The dated domain-index observation is retained.") : null,
              proof.excerpt ? el("blockquote",{},proof.excerpt) : null,proof.via ? el("div",{class:"small"},"Via: ",displayValue(proof.via, "Discovery step")) : null)),
            (c.paths || []).length ? el("details",{},el("summary",{},"Recorded paths"),dataView(c.paths, "Recorded paths")) : null);
          candidateList.append(el("article",{class:"expansion-candidate","data-candidate-id":c.id,"data-candidate-category":category(c,current)},
            el("div",{class:"expansion-candidate-heading"},el("label",{class:"check-row"},box,el("strong",{},c.domain)),el("span",{class:"pill"},label(c.status || "pending"))),
            el("div",{class:"expansion-candidate-meta"},el("span",{},CATEGORY_LABELS[category(c,current)]),el("span",{},label(c.relation)),el("span",{},"Evidence confidence: "+text(c.confidence_label || c.confidence)),el("span",{},"DNS: "+text(c.dns_status || "unchecked"))),
            c.domain?.startsWith("*.") ? el("p",{class:"small muted"},"Wildcard observation; no concrete host or DNS resolution is implied.") : null,
            (c.tags || []).length ? el("p",{class:"small"},"Tags: "+c.tags.join(", ")) : null,
            el("div",{class:"asset-actions"},window.LeakScopeInspector ? btn("Inspect",()=>window.LeakScopeInspector.open({type:"candidate",id:c.id,record:c,expansion_id:current.id})) : null,child,monitor,c.node_id ? link("Relationships","/relationships/?root="+encodeURIComponent(c.node_id)) : null),evidence));
        }
        for(const article of candidateList.querySelectorAll('[data-candidate-id]')) {
          const open=evidenceState.get(article.dataset.candidateId);
          if(open)[...article.querySelectorAll("details")].forEach((item,index)=>{if(index<open.length)item.open=open[index];});
        }
        updateSelected();
      }
      function remoteFilter(){pageState={...pageState,page:1,category:resultGroup.value,status:status.value,relation:relation.value,q:query.value.trim()};selected.clear();clearTimeout(filterTimer);loadDetail(current.id).catch(error=>{if(!signal.aborted)message.append(note(error.message,true));});}
      for(const control of [query,status,resultGroup])control.addEventListener(control===query ? "input" : "change",()=>{if(!paginated){drawCandidates();return;}pageState={...pageState,page:1,category:resultGroup.value,status:status.value,relation:relation.value,q:query.value.trim()};selected.clear();clearTimeout(filterTimer);if(control===query)filterTimer=setTimeout(remoteFilter,200);else remoteFilter();});
      relation.addEventListener("change",()=>{if(relation.value==="lookalike")resultGroup.value="lookalikes";else if(relation.value==="service_provider")resultGroup.value="services";if(paginated)remoteFilter();else drawCandidates();});
      const reviewControls=el("div",{class:"expansion-review"},
        el("div",{class:"asset-actions"},btn("Select visible",()=>{visible().forEach(c=>selected.add(c.id));drawCandidates();}),btn("Clear selection",()=>{selected.clear();drawCandidates();}),selection),
        field("Destination organization",target),el("div",{class:"asset-actions"},accept,reject,expand),field("Tags (comma separated)",tagInput),tag);
      detail.append(summary);
      if(Array.isArray(current.coverage) && current.coverage.length){
        const coverageBody=el("tbody",{});
        const numeric=value=>Number.isFinite(Number(value)) ? String(Number(value)) : "0";
        for(const row of current.coverage){
          if(!row || typeof row!=="object")continue;
          const sourceTitle=SOURCES.find(([source])=>source===row.source)?.[1] || label(row.source);
          const requests=row.requests && typeof row.requests==="object" ? Object.entries(row.requests).map(([key,value])=>label(key)+": "+numeric(value)).join(" · ") : numeric(row.requests);
          coverageBody.append(el("tr",{},el("th",{scope:"row"},el("strong",{},sourceTitle),el("small",{},text(row.source)+" · "+label(row.kind))),el("td",{},text(row.seed)),el("td",{},el("span",{class:"pill"},label(row.state))),el("td",{},numeric(row.candidates)),el("td",{},numeric(row.pages)),el("td",{},requests),
            el("td",{},Array.isArray(row.notes) && row.notes.length ? el("details",{},el("summary",{},row.notes.length+" note"+(row.notes.length===1?"":"s")),...row.notes.map(value=>el("p",{class:"small"},typeof value==="string" ? value : value?.message || "Source reported a collection issue."))) : "—")));
        }
        detail.append(panel("Source coverage",el("details",{"data-expansion-coverage":"",open:state==="paused" || current.partial},el("summary",{},"Techniques, pages & skipped work ("+current.coverage.length+")"),el("p",{class:"small muted"},"Coverage describes this run. A completed lookup can still be limited by upstream indexing, quotas or available records; it does not prove complete organizational ownership."),el("div",{class:"expansion-table-scroll",tabindex:"0",role:"region","aria-label":"Source coverage table"},el("table",{class:"expansion-coverage-table"},el("thead",{},el("tr",{},...["Source / technique","Seed","State","Candidates returned","Pages","Requests","Notes"].map(value=>el("th",{scope:"col"},value)))),coverageBody)))));
      }
      if(current.resumable && ["paused","failed","lost","cancelled"].includes(state)){
        const resumeBudgets=new Map(), resumeQuota=input("resume_max_candidates",String(current.max_candidates ?? 0),"number",{min:0,max:10000000}), resumeRuntime=input("resume_runtime_seconds",String(current.runtime_seconds || 1800),"number",{min:60,max:86400});
        const resumeOutput=el("div",{"aria-live":"polite"}), resumeFields=BUCKETS.map(([bucket,title,,maximum])=>{
          const used=Number(current.usage?.[bucket]) || 0, ceiling=Math.max(used,Number(current.budgets?.[bucket]) || 0), control=input("resume_budget_"+bucket,String(ceiling),"number",{min:used,max:maximum});resumeBudgets.set(bucket,control);
          return field(title+" total request ceiling · "+used+" already used",control);
        });
        const resumeButton=btn("Resume expansion",()=>busy(resumeButton,resumeOutput,async()=>{
          const budgets=Object.fromEntries([...resumeBudgets].map(([bucket,control])=>[bucket,Number(control.value)]));
          if([...resumeBudgets].some(([bucket,control])=>!control.checkValidity() || budgets[bucket]<(Number(current.usage?.[bucket]) || 0)) || !resumeQuota.checkValidity() || !resumeRuntime.checkValidity())throw new Error("Enter valid total ceilings at or above recorded usage.");
          resumeOutput.replaceChildren();
          try{
            const resumed=await request("/"+encodeURIComponent(current.id)+"/resume",{budgets,max_candidates:Number(resumeQuota.value),runtime_seconds:Number(resumeRuntime.value)},signal);
            if(signal.aborted)return;await applyResult(resumed);await loadHistory();
          }catch(error){
            if(!signal.aborted && (!error.status || error.status>=500)){
              const warning="Resume could not be confirmed. The server may have accepted it; check status before retrying. No resume request will be repeated automatically.";
              paintActivity(current,warning);resumeOutput.append(note(warning,true),btn("Check expansion status",()=>busy(resumeOutput.querySelector("button"),resumeOutput,()=>loadDetail(current.id))));
            }else throw error;
          }
        }),{class:"btn btn-primary"});
        detail.append(panel("Continue saved research",el("details",{"data-expansion-resume":"",id:"expansion-resume",open:state==="paused"},el("summary",{},"Adjust ceilings & resume from the saved checkpoint"),note(current.checkpoint_summary?.reason || "Pending work is retained. Resume continues this expansion instead of starting it over."),el("p",{class:"small muted"},"These are total request ceilings, including requests already spent. Resuming preserves usage and review decisions. Netlas remains disabled unless this expansion already selected it and its ceiling permits requests."),el("div",{class:"expansion-columns"},...resumeFields,field("Saved candidate storage quota (0 = no application limit)",resumeQuota),field("Runtime allowance (seconds)",resumeRuntime)),resumeButton,resumeOutput)));
      }
      const entityLeads=(current.leads || result.leads || []).filter(lead=>lead && lead.kind==="company" && typeof lead.value==="string");
      if(entityLeads.length) {
        const leadList=el("div",{"data-entity-leads":""},el("p",{class:"small muted"},"Reported legal entities and aliases are research leads. They do not supply or prove ownership of a website domain."));
        for(const lead of entityLeads)leadList.append(el("article",{class:"expansion-entity"},el("strong",{},lead.value),el("span",{class:"pill"},label(lead.relation || "entity lead")),
          lead.ambiguous ? note("Ambiguous entity match · compare legal name, jurisdiction and source record before expanding.") : null,
          ...(lead.evidence || []).map(proof=>el("div",{class:"expansion-proof"},el("p",{class:"small muted"},text(proof.source),proof.source_record_id ? " · Record: "+text(proof.source_record_id) : ""),safeLink(proof.url || proof.source_url),proof.excerpt ? el("p",{class:"small"},proof.excerpt) : null)),
          el("a",{href:url({company:lead.value,focused:true,...(current.organization_id ? {organization_id:current.organization_id} : {})}),"data-company-expansion":""},"Research this company")));
        detail.append(panel("Legal entity leads",leadList));
      }
      const pagination=el("nav",{class:"expansion-pagination","data-candidate-pagination":"","aria-label":"Candidate result pages"});
      if(paginated){
        const pages=Math.max(1,Number(result.pages) || 1), total=Number(result.count) || 0;
        const go=next=>{pageState.page=next;selected.clear();loadDetail(current.id).catch(error=>{if(!signal.aborted)message.append(note(error.message,true));});};
        const pageSize=select("candidate_page_size",[["25","25 per page"],["100","100 per page"],["250","250 per page"]],String(pageState.page_size));
        pageSize.addEventListener("change",()=>{pageState.page_size=Number(pageSize.value);pageState.page=1;selected.clear();go(1);});
        pagination.append(btn("Previous page",()=>go(pageState.page-1),{disabled:pageState.page<=1}),el("span",{role:"status","aria-live":"polite"},"Page "+pageState.page+" of "+pages+" · "+total+" matching saved candidates"),btn("Next page",()=>go(pageState.page+1),{disabled:pageState.page>=pages}),field("Display page size",pageSize));
      }
      const infrastructureLeads=(current.leads || result.leads || []).filter(lead=>lead && ["ip","network","asn"].includes(lead.kind) && typeof lead.value==="string");
      if(infrastructureLeads.length){
        const leadBody=el("tbody",{}), leadPager=el("nav",{class:"expansion-pagination","aria-label":"Infrastructure lead pages"}), types={ip:"IP address",network:"Network / CIDR",asn:"ASN"};
        const leadSection=el("details",{"data-infrastructure-leads":"",open:infrastructureState.open},el("summary",{},"Infrastructure leads ("+infrastructureLeads.length+")"),
          el("p",{class:"small muted"},"Addresses, routing origins and shared networks are research leads. They do not establish ownership. A research link opens a focused setup; collection starts only after you submit it."),
          leadPager,el("div",{class:"expansion-table-scroll",tabindex:"0",role:"region","aria-label":"Infrastructure leads table"},el("table",{class:"expansion-coverage-table expansion-infrastructure-table"},el("thead",{},el("tr",{},...["Type","Value","Relationship","Evidence","Research"].map(value=>el("th",{scope:"col"},value)))),leadBody)));
        function drawLeads(next){
          const pages=Math.max(1,Math.ceil(infrastructureLeads.length/100)), page=Math.min(pages,Math.max(1,next));leadSection.dataset.page=String(page);leadBody.replaceChildren();leadPager.replaceChildren();
          for(const lead of infrastructureLeads.slice((page-1)*100,page*100)){
            const proofs=Array.isArray(lead.evidence) ? lead.evidence.filter(proof=>proof && typeof proof==="object") : [];
            const evidence=proofs.length ? el("details",{},el("summary",{},proofs.length+" evidence record"+(proofs.length===1?"":"s")),...proofs.map(proof=>el("article",{class:"expansion-proof"},el("strong",{},label(proof.signal || proof.source)),el("p",{class:"small muted"},text(proof.source),proof.observed_at ? " · "+date(proof.observed_at) : ""),safeLink(proof.url || proof.source_url),proof.excerpt ? el("p",{class:"small"},proof.excerpt) : null,proof.via ? el("p",{class:"small"},"Via: ",displayValue(proof.via,"Discovery step")) : null))) : el("span",{class:"muted"},"No recorded evidence");
            const shownValue=lead.kind==="asn" && !/^AS/i.test(lead.value) ? "AS"+lead.value : lead.value;
            leadBody.append(el("tr",{},el("th",{scope:"row"},types[lead.kind]),el("td",{},el("code",{},shownValue)),el("td",{},label(lead.relation || "infrastructure lead")),el("td",{},evidence),el("td",{},el("a",{href:url({[lead.kind]:lead.value,focused:true,...(current.organization_id ? {organization_id:current.organization_id} : {})}),"data-infrastructure-expansion":lead.kind},"Research this "+({ip:"address",network:"network",asn:"ASN"}[lead.kind])))));
          }
          leadPager.append(btn("Previous leads page",()=>drawLeads(page-1),{disabled:page<=1}),el("span",{role:"status","aria-live":"polite"},"Page "+page+" of "+pages+" · "+infrastructureLeads.length+" infrastructure leads"),btn("Next leads page",()=>drawLeads(page+1),{disabled:page>=pages}));
        }
        drawLeads(infrastructureState.page);detail.append(panel("Infrastructure research",leadSection));
      }
      detail.append(panel("Review candidates",el("div",{class:"expansion-columns"},field("Result group",resultGroup),field("Filter domains or tags",query),field("Review status",status),field("Relationship",relation)),groupExplanation,pagination,reviewControls,candidateList));
      drawCandidates();
      if(savedFocus) {
        const restored=[...detail.querySelectorAll("input,select,textarea")].find(control=>control.tagName===savedFocus.tag && control.name===savedFocus.name && (savedFocus.name!=="candidate_id" || control.value===savedFocus.value));
        if(restored){restored.focus({preventScroll:true});if(typeof savedFocus.start==="number" && typeof restored.setSelectionRange==="function")restored.setSelectionRange(savedFocus.start,savedFocus.end,savedFocus.direction || "none");}
      }
      if(running && !signal.aborted)schedulePoll(current.id);
    }
    try {
      await loadOrganizations();if(signal.aborted)return dispose;
      if(params.get("reference_type") && params.get("reference_id")) {
        const ref=new URLSearchParams({type:params.get("reference_type"),id:params.get("reference_id")});
        const context=await request("/api/assets/context?"+ref,undefined,signal);if(signal.aborted)return dispose;
        const saved=(context.names || []).map(n=>n.name).filter(Boolean);if(context.target && !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(context.target) && !context.target.includes(":"))saved.unshift(context.target);
        domains.value=[...new Set([...list(domains.value),...saved])].join("\n");
        updateCosts();
        formOutput.append(note("Seeded from a saved finding. Starting expansion remains an explicit action."));
      }
      if(params.get("id"))await loadDetail(params.get("id"));
      if(!signal.aborted)await loadHistory();
      if(!signal.aborted && params.get("view")==="results"){history.scrollIntoView?.({block:"start"});history.focus();}
    } catch(error) {if(!signal.aborted)errors.append(note(error.message,true));}
    finally {if(!signal.aborted)root.setAttribute("aria-busy","false");}
    return dispose;
  }
  window.LeakScopeExpansion = {render,open,url,dispose};
})();
