(function () {
  "use strict";
  const API = "/api/discovery-presets";
  const PROVIDERS = [["shodan", "Shodan"], ["zoomeye", "ZoomEye"], ["netlas", "Netlas"]];
  const text = value => value == null ? "" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, text(value));
  }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (name === "class") node.className = value;
      else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
      else if (["checked", "disabled", "selected"].includes(name)) node[name] = !!value;
      else node.setAttribute(name, value === true ? "" : text(value));
    }
    children.flat(Infinity).forEach(child => {
      if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(text(child)));
    });
    return node;
  }
  const button = (label, action, cls = "btn") => el("button", {type:"button", class:cls, onclick:action}, label);
  const notice = (message, error = false) => el("div", {class:"notice" + (error ? " notice-error" : ""), role:error ? "alert" : "status"}, message);
  const field = (label, control) => el("label", {class:"field"}, el("span", {class:"field-label"}, label), control);
  const input = (name, value = "", attrs = {}) => el("input", {name, value, class:"input", ...attrs});
  function discoverLink(preset) {
    return el("a", {class:"btn btn-primary", href:"/discover/?service_type=" + encodeURIComponent(preset.key || "preset:" + preset.id) + "&query_mode=fingerprint"}, "Use in Discover");
  }
  async function request(path, data, signal) {
    const response = await fetch(API + path, {method:data === undefined ? "GET" : "POST", credentials:"same-origin", signal,
      headers:{Accept:"application/json", ...(data === undefined ? {} : {"Content-Type":"application/json", "X-CSRFToken":document.querySelector("meta[name=csrf-token]")?.content || ""})},
      ...(data === undefined ? {} : {body:JSON.stringify(data)})});
    let payload;
    try {payload = await response.json();} catch (_) {throw new Error("The server returned an unexpected response.");}
    if (!response.ok) throw new Error(text(payload.error || payload.detail || "The preset request failed."));
    return payload;
  }
  function modal(title, description) {
    document.querySelectorAll("dialog.preset-dialog").forEach(item => item.close());
    const controller = new AbortController();
    const dialog = el("dialog", {class:"run-dialog intel-dialog preset-dialog", "aria-label":title});
    const body = el("div", {class:"intel-dialog-body"}, notice("Loading saved evidence…"));
    dialog.append(el("div", {class:"dialog-heading"}, el("h2", {}, title), button("Close", () => dialog.close(), "btn btn-small")),
      el("p", {class:"muted intel-dialog-description"}, description), body);
    dialog.addEventListener("cancel", event => {event.preventDefault(); dialog.close();});
    dialog.addEventListener("close", () => {controller.abort(); dialog.remove();}, {once:true});
    document.body.append(dialog); dialog.showModal();
    return {body, signal:controller.signal, alive:() => dialog.isConnected && !controller.signal.aborted};
  }
  function referenceCopy(references) {
    if (!Array.isArray(references) || !references.length || references.length > 10) throw new Error("Choose 1–10 saved results.");
    return references.map(reference => {
      if (!reference || typeof reference.type !== "string" || reference.id == null) throw new Error("Choose a saved result.");
      return {type:reference.type, id:reference.id};
    });
  }
  function providerPreviews(queries) {
    return el("div", {class:"intel-query-grid"}, PROVIDERS.map(([key, label]) => {
      const query = queries?.[key] || {};
      return el("article", {class:"intel-query"}, el("h3", {}, label),
        query.supported && query.query ? el("pre", {class:"code-box"}, query.query) : notice("Unavailable for these selected fields."),
        (query.warnings || []).map(message => el("p", {class:"small muted"}, message)),
        query.reason ? el("p", {class:"small muted"}, query.reason) : null);
    }));
  }
  async function openCreate(references) {
    const view = modal("Create discovery preset", "Choose observed identity fields, review each provider query, then save it for Discover. Previewing and saving do not run a search or contact a device.");
    try {
      const refs = referenceCopy(references);
      const initial = await request("/preview", {references:refs}, view.signal);
      if (!view.alive()) return;
      let version = 0, previewedVersion = 0, savedVersion = null, busy = false, scopeOverride = false;
      let currentQueries = initial.queries || {};
      let unresolved = new Set(initial.scope_conflicts || []);
      const sourceFields = initial.fields || [];
      const selected = new Set(initial.selected_fields || []);
      const choices = el("div", {class:"intel-fields"}, sourceFields.map(item => {
        const checkbox = input("evidence", item.id, {type:"checkbox", checked:selected.has(item.id)});
        const provenance = item.provenance || {};
        checkbox.addEventListener("change", invalidate);
        return el("label", {class:"check-row intel-evidence"}, checkbox, el("span", {},
          el("strong", {}, item.label || item.field), el("small", {}, item.value),
          el("small", {class:"muted"}, [({"fetch:ot":"Native protocol read",shodan:"Shodan",zoomeye:"ZoomEye",netlas:"Netlas"})[provenance.provider] || provenance.provider || "Saved result", provenance.observed_at].filter(Boolean).join(" · "))));
      }));
      const name = input("name", initial.suggested_name || "Discovery preset", {maxlength:255, required:true});
      const description = el("textarea", {name:"description", class:"input", rows:2, maxlength:4000});
      const mode = el("select", {name:"mode", class:"select"}, el("option", {value:"all"}, "Match all selected field groups"), el("option", {value:"any"}, "Match any selected field"));
      mode.value = initial.mode || "all";
      const scopeControls = Object.fromEntries(["network", "country", "exclude"].map(key => [key, input(key, initial.scope?.[key] || "", {maxlength:key === "country" ? 2 : 1000})]));
      const resolution = el("div", {class:"small muted"});
      const status = el("div", {"aria-live":"polite"});
      const output = el("div", {class:"intel-result"}, providerPreviews(currentQueries));
      const errors = el("div", {"aria-live":"polite"});
      const saved = el("div", {"aria-live":"polite"});
      const preview = button("Preview provider queries", previewQueries);
      const save = button("Save discovery preset", savePreset, "btn btn-primary");
      function selectedIDs() {return [...choices.querySelectorAll("input:checked")].map(control => control.value);}
      function scope() {return Object.fromEntries(Object.entries(scopeControls).map(([key, control]) => [key, control.value.trim()]));}
      function payload() {return {references:refs, selected_fields:selectedIDs(), mode:mode.value, scope:scope(), scope_override:scopeOverride};}
      function hasQuery() {return Object.values(currentQueries).some(query => query?.supported && query.query);}
      function update() {
        const chosen = selectedIDs().length > 0;
        preview.disabled = busy || !chosen || unresolved.size > 0;
        save.disabled = busy || !chosen || unresolved.size > 0 || previewedVersion !== version || savedVersion === version || !hasQuery() || !name.value.trim();
        resolution.textContent = unresolved.size ? "Selected results have differing scopes. Explicitly edit or clear: " + [...unresolved].join(", ") + "." : "";
      }
      function invalidate() {
        version++; savedVersion = null;
        status.replaceChildren(notice("Options changed. Preview provider queries again before saving."));
        errors.replaceChildren(); saved.replaceChildren(); update();
      }
      async function previewQueries() {
        if (preview.disabled) return;
        const requestedVersion = version;
        const body = payload();
        busy = true; errors.replaceChildren(); update();
        status.replaceChildren(notice("Generating provider query previews…"));
        try {
          const data = await request("/preview", body, view.signal);
          if (!view.alive() || version !== requestedVersion) return;
          currentQueries = data.queries || {}; previewedVersion = version;
          unresolved = new Set(data.scope_conflicts || []);
          status.replaceChildren(...(data.warnings || []).map(message => notice(message)));
          output.replaceChildren(providerPreviews(currentQueries));
        } catch (error) {
          if (view.alive() && version === requestedVersion) {previewedVersion = -1; errors.replaceChildren(notice(error.message, true)); status.replaceChildren(notice("Preview unavailable. Review the options and try again."));}
        } finally {if (view.alive()) {busy = false; update();}}
      }
      async function savePreset() {
        if (save.disabled) return;
        const requestedVersion = version;
        const body = {...payload(), name:name.value.trim(), description:description.value.trim()};
        busy = true; errors.replaceChildren(); update();
        try {
          const data = await request("", body, view.signal);
          if (!view.alive()) return;
          if (!data.preset?.id) throw new Error("The server did not return a saved preset.");
          if (version === requestedVersion) savedVersion = version;
          saved.replaceChildren(notice(data.created === false ? "An existing matching preset is available in Discover." : "Discovery preset saved. It is available to every browser using this LeakScope instance."),
            ...(version !== requestedVersion ? [notice("The saved preset uses the previously reviewed options. Preview your current edits before saving another preset.")] : []),
            el("div", {class:"row-actions mt"}, discoverLink(data.preset), button("Saved presets", openManager)));
        } catch (error) {if (view.alive()) errors.replaceChildren(notice(error.message, true));}
        finally {if (view.alive()) {busy = false; update();}}
      }
      mode.addEventListener("change", invalidate);
      name.addEventListener("input", update);
      Object.entries(scopeControls).forEach(([key, control]) => control.addEventListener("input", () => {scopeOverride = true; unresolved.delete(key); invalidate();}));
      view.body.replaceChildren(...(initial.warnings || []).map(message => notice(message)),
        el("div", {class:"field-grid"}, field("Preset name", name), field("Description (optional)", description)),
        el("h3", {}, "Choose observed identity"), el("p", {class:"small muted"}, "Use vendor and model to find similar devices. Address, hostname, port and version fields are optional; selecting them narrows the query."),
        choices, field("Combination", mode),
        el("h3", {}, "Keep or explicitly change the originating scope"),
        el("div", {class:"intel-scope"}, field("Network or IP", scopeControls.network), field("Country", scopeControls.country), field("Exclusions", scopeControls.exclude)),
        resolution, status, errors, output,
        el("div", {class:"row-actions mt"}, preview, save), saved);
      update();
    } catch (error) {if (view.alive()) view.body.replaceChildren(notice(error.message, true));}
  }
  async function openManager() {
    const view = modal("Saved discovery presets", "These presets are stored in LeakScope and shared across your browsers. Using a preset opens Discover for review; it does not start a search.");
    async function load() {
      const data = await request("", undefined, view.signal);
      if (!view.alive()) return;
      const rows = data.results || [];
      view.body.replaceChildren(rows.length ? el("div", {class:"intel-result"}, rows.map(preset => {
        const output = el("div", {"aria-live":"polite"});
        const remove = button("Delete preset", async () => {
          if (remove.disabled) return;
          remove.disabled = true;
          try {await request("/" + encodeURIComponent(preset.id) + "/delete", {}, view.signal); if (view.alive()) await load();}
          catch (error) {if (view.alive()) {output.replaceChildren(notice(error.message, true)); remove.disabled = false;}}
        }, "btn btn-small");
        const scope = Object.entries(preset.scope || {}).filter(([, value]) => value).map(([key, value]) => key + ": " + (Array.isArray(value) ? value.join(", ") : typeof value === "object" ? Object.keys(value).length + " fields" : text(value))).join(" · ");
        return el("article", {class:"panel"}, el("div", {class:"panel-body"}, el("h3", {}, preset.name || preset.label),
          preset.description ? el("p", {}, preset.description) : null,
          el("p", {class:"small muted"}, "Service: " + text(preset.service_type || "unclassified")),
          el("p", {class:"small muted"}, scope ? "Saved scope — " + scope : "Saved scope — all indexed locations and ports unless selected signals constrain them."),
          el("details", {}, el("summary", {}, "Saved scope fields"), dataView(preset.scope || {}, "Saved scope")),
          el("details", {}, el("summary", {}, "Saved provider queries"), providerPreviews(preset.queries)),
          el("div", {class:"row-actions"}, discoverLink(preset), remove), output));
      })) : notice("No saved discovery presets. Create one from an observed result."));
    }
    try {await load();} catch (error) {if (view.alive()) view.body.replaceChildren(notice(error.message, true));}
  }
  window.LeakScopePresets = {openCreate, openManager};
})();
