(function () {
  "use strict";
  // Build: assemble LeakScope's jobs into a workflow as a plain list of steps, confirm what a run can
  // spend, start it and watch it. Every value from the server is placed with DOM text.
  const API = "/api/build";
  const GROUPS = {trigger: "Starts", find: "Find", collect: "Collect", enrich: "Enrich", organize: "Organize", logic: "Filter, sort and flow", data: "Values", send: "Send", unknown: "Unavailable"};
  const REACH = {local: "Local", public: "Public lookup", credits: "Spends credits", target: "Contacts target", send: "Sends out"};
  const STATE = {done: "Done", waiting: "Waiting", running: "Running", failed: "Failed", skipped: "Skipped", queued: "Queued",
    succeeded: "Finished", cancelled: "Stopped", lost: "Lost"};
  const ACTIVE = ["queued", "running"];
  const POLL = 3000;
  let timer = null, generation = 0;

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key === "hidden" || key === "disabled") node[key] = !!value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child);
    return node;
  }
  // replaceChildren with the same flattening as el(): arrays are spread, null and false are skipped.
  function fill(node, ...children) { node.replaceChildren(...children.flat(Infinity).filter(child => child != null && child !== false)); return node; }
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  async function api(path, body) {
    const options = {credentials: "same-origin"};
    if (body !== undefined) Object.assign(options, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify(body)});
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) { const error = new Error(data.error || "Request failed (" + response.status + ")"); error.status = response.status; error.data = data; throw error; }
    return data;
  }
  const button = (text, run, kind = "") => el("button", {type: "button", class: "btn btn-small " + kind, onclick: run}, text);
  const reach = kind => el("span", {class: "build-reach build-reach-" + kind}, REACH[kind] || kind);
  const mark = group => el("i", {class: "build-mark build-group-" + group, title: GROUPS[group] || group});
  const plural = (count, word) => count + " " + word + (count === 1 ? "" : "s");
  function stop() { clearTimeout(timer); timer = null; generation += 1; }

  // ------------------------------------------------------------------ export and import
  // A workflow file holds names and steps only: no runs, no results, and nothing about automatic starts.
  const FILE = "leakscope-workflows";
  async function exportWorkflows(ids, label) {
    const workflows = [];
    for (const id of ids) { const detail = await api(API + "/workflows/" + encodeURIComponent(id)); workflows.push({name: detail.name, definition: detail.definition}); }
    const Export = window.LeakScopeExport;
    Export.save(Export.slug(label) + "-" + Export.today() + ".json", JSON.stringify({format: FILE, version: 1, exported_at: new Date().toISOString(), workflows}, null, 2) + "\n", "application/json", false);
    return workflows.length;
  }
  function readWorkflows(text) {
    let data;
    try { data = JSON.parse(text); } catch (_) { throw new Error("This is not a workflow file: it is not valid JSON."); }
    const found = data && data.format === FILE && Array.isArray(data.workflows) ? data.workflows : data && typeof data.name === "string" && data.definition ? [data] : null;
    if (!found || !found.length) throw new Error("This is not a LeakScope workflow file.");
    if (found.length > 100) throw new Error("A workflow file holds at most 100 workflows.");
    return found.map((item, index) => {
      if (!item || typeof item.name !== "string" || !item.name.trim() || !item.definition || typeof item.definition !== "object" || Array.isArray(item.definition)) throw new Error("Workflow " + (index + 1) + " in this file has no name or no steps.");
      return {name: item.name.trim().slice(0, 200), definition: item.definition};
    });
  }
  // Imported workflows are new copies and always start switched off; the server checks every step as it does for a saved edit.
  async function importWorkflows(file) {
    if (file.size > 4 * 1024 * 1024) throw new Error("The file is larger than 4 MB.");
    const found = readWorkflows(await file.text()), made = [], failed = [];
    for (const item of found) {
      try { made.push(await api(API + "/workflows", item)); } catch (error) { failed.push(item.name + ": " + error.message); }
    }
    return {made, failed};
  }

  // ------------------------------------------------------------------ list
  async function list(root, notice) {
    stop();
    const status = el("p", {class: "small muted", role: "status"}, "Loading workflows…"), rows = el("div", {class: "build-list"});
    const create = button("New workflow", async () => {
      try {
        const made = await api(API + "/workflows", {name: "New workflow", definition: {steps: []}});
        history.pushState(null, "", "/build/" + made.id + "/");
        editor(root, made.id);
      } catch (error) { status.textContent = error.message; status.className = "small error"; }
    }, "btn-primary");
    let known = [];
    const picker = el("input", {type: "file", accept: ".json,application/json", hidden: true, "aria-label": "Workflow file"});
    const say = (text, bad) => { status.textContent = text; status.className = bad ? "small error" : "small muted"; };
    picker.addEventListener("change", async () => {
      const file = picker.files[0]; picker.value = "";
      if (!file) return;
      try {
        const {made, failed} = await importWorkflows(file);
        const flawed = made.filter(item => item.problems).length;
        list(root, {text: "Imported " + plural(made.length, "workflow") + ". They are switched off until you turn them on."
          + (flawed ? " " + flawed + (flawed === 1 ? " needs" : " need") + " fixing before " + (flawed === 1 ? "it" : "they") + " can run." : "") + (failed.length ? " Not imported: " + failed.join("; ") : ""), bad: !!failed.length});
      } catch (error) { say(error.message, true); }
    });
    const importer = button("Import", () => picker.click(), "btn-quiet"), exporter = button("Export all", async () => {
      try { say("Exported " + plural(await exportWorkflows(known.map(item => item.id), "workflows"), "workflow") + "."); } catch (error) { say(error.message, true); }
    }, "btn-quiet");
    exporter.hidden = true;
    fill(root, el("section", {class: "panel"}, el("div", {class: "panel-body"},
      el("div", {class: "build-bar"}, el("div", {}, el("h2", {}, "Workflows"), el("p", {class: "muted small"}, "Connect LeakScope's jobs into steps that run in order. A run asks once, before it starts.")), el("div", {class: "build-bar-actions"}, picker, importer, exporter, create)),
      status, rows)));
    try {
      const data = await api(API + "/workflows");
      known = data.results; exporter.hidden = !known.length;
      if (notice) say(notice.text, notice.bad);
      else status.textContent = data.results.length ? plural(data.results.length, "workflow") : "No workflows yet. Create one to chain a search, a filter and a capture.";
      for (const item of data.results) {
        const last = item.last_run;
        rows.append(el("a", {class: "build-row", href: "/build/" + item.id + "/"},
          el("strong", {class: "build-row-name"}, item.name),
          el("span", {class: "build-marks", "aria-label": plural(item.steps, "step")}, item.groups.map(mark), el("span", {class: "small muted"}, plural(item.steps, "step"))),
          el("span", {class: "build-reaches"}, item.reaches.map(reach), item.automatic?.enabled ? el("span", {class: "build-auto-chip"}, "Automatic") : null),
          el("span", {class: "small muted"}, last ? (STATE[last.state] || last.state) + (last.partial ? " · stopped early" : "") : "Never run")));
      }
    } catch (error) { status.textContent = error.message; status.className = "small error"; }
  }

  // ------------------------------------------------------------------ editor
  async function editor(root, id) {
    stop();
    const mine = generation;
    let catalog, detail;
    try { [catalog, detail] = await Promise.all([api(API + "/blocks"), api(API + "/workflows/" + encodeURIComponent(id))]); }
    catch (error) { fill(root, el("div", {class: "notice error"}, error.message), el("a", {href: "/build/", class: "btn btn-small"}, "← All workflows")); return; }
    if (mine !== generation) return;
    // A Map, not an object: a stored block key such as "constructor" must not resolve to a prototype member.
    const known = new Map(catalog.blocks.map(block => [block.key, block])), blocks = new Proxy({}, {get: (_, key) => known.get(key)});
    const definition = {steps: Array.isArray(detail.definition?.steps) ? detail.definition.steps.map(step => ({id: String(step?.id ?? ""), block: String(step?.block ?? ""), settings: {...(step?.settings || {})}, ...(step?.when ? {when: step.when} : {}), ...(Array.isArray(step?.after) ? {after: step.after.map(String)} : {})})) : [],
      limits: {...catalog.limits.defaults, ...(detail.definition?.limits || {})}};
    let open = -1, dirty = false, pending = null, picked = null, lastRun = null;
    const name = el("input", {class: "input build-name", "aria-label": "Workflow name", maxlength: 200, value: detail.name});
    const status = el("p", {class: "small muted", role: "status", "aria-live": "polite"});
    const problems = el("div", {class: "notice error build-problems", hidden: true});
    const steps = el("ol", {class: "build-steps"}), shelf = el("div", {class: "build-shelf"});
    const canvas = el("div", {class: "build-canvas", role: "group", "aria-label": "Workflow map", hidden: true}), mapBar = el("div", {class: "build-map-bar", hidden: true});
    const find = el("input", {class: "input", type: "search", placeholder: "Search blocks", "aria-label": "Search blocks"});
    const kinds = el("div", {class: "build-kinds", role: "group", "aria-label": "Show one group of blocks"}); let only = null;
    const confirm = el("div", {class: "build-confirm", role: "dialog", "aria-label": "Confirm this run", hidden: true});
    const runView = el("section", {class: "build-run", "aria-live": "polite"});
    // Automatic starts: turned on once with the same confirmation as a run; later runs do not ask.
    const auto = el("div", {class: "build-auto"});
    function drawAuto(state) {
      state = state || {};
      const event = !!blocks[definition.steps[0]?.block]?.event;
      const when = state.enabled ? (state.event ? "starts by itself when new records appear (checked every minute)" : "every " + state.schedule_minutes + " minutes" + (state.next_run_at ? " · next " + new Date(state.next_run_at).toLocaleString() : "")) : "";
      fill(auto, el("strong", {}, state.enabled ? "Runs automatically" : "Runs only when you press Run"), when ? el("span", {class: "small muted"}, when) : null,
        state.note ? el("span", {class: "small build-why"}, state.note) : null,
        state.enabled ? button("Turn off", async () => { try { drawAuto((await api(API + "/workflows/" + encodeURIComponent(id) + "/disable", {})).automatic); say("Automatic runs are off"); } catch (error) { say(error.message, true); } })
          : button(event ? "Run automatically…" : "Run on a schedule…", () => turnOn(event)));
    }
    async function turnOn(event) {
      try {
        if (dirty) { const saved = await save(); if (saved.problems) return; }
        showAuto(await api(API + "/workflows/" + encodeURIComponent(id) + "/estimate", {}), event);
      } catch (error) { showProblems(error.status === 400 ? error.message : ""); say(error.status === 400 ? "Fix the problems above before turning it on" : error.message, true); }
    }
    // What a run reaches beyond saved data, other than the counted provider pages and target contacts.
    const outward = numbers => (numbers.reaches || []).filter(kind => kind === "public" || kind === "send");
    function showAuto(numbers, event) {
      const every = el("input", {class: "input", type: "number", name: "schedule_minutes", min: 5, max: 43200, value: 60, "aria-label": "Minutes between runs"});
      const line = (kind, text) => el("li", {}, reach(kind), el("span", {}, text));
      fill(confirm, el("h3", {}, "Let this workflow start by itself?"), el("ul", {class: "build-confirm-list"},
        el("li", {}, el("span", {class: "build-reach"}, "Starts"), event ? el("span", {}, "whenever new records appear, checked every minute") : el("span", {class: "build-every"}, "every ", every, " minutes")),
        numbers.pages ? line("credits", "each run: up to " + plural(numbers.pages, "provider page")) : null,
        numbers.contacts ? line("target", "each run: up to " + plural(numbers.contacts, "target contact")) : null,
        outward(numbers).includes("public") ? line("public", "each run asks public sources such as DNS and registries") : null,
        outward(numbers).includes("send") ? line("send", "each run sends data to the addresses set in its steps") : null,
        !numbers.pages && !numbers.contacts && !outward(numbers).length ? line("local", "each run reads and changes saved data only") : null,
        el("li", {}, el("span", {class: "build-reach"}, "Limit"), el("span", {}, "at most " + plural(numbers.limits.runs_per_day, "run") + " a day; it will not ask again until you change the workflow"))),
        el("div", {class: "build-confirm-actions"}, button("Turn on", async pressed => {
          const pressedButton = pressed.currentTarget; pressedButton.disabled = true;
          try {
            const saved = await api(API + "/workflows/" + encodeURIComponent(id) + "/enable", {token: numbers.token, ...(event ? {} : {schedule_minutes: Number(every.value)})});
            confirm.hidden = true; drawAuto(saved.automatic); say("Automatic runs are on");
          } catch (error) {
            if (error.status === 409 && error.data.estimate) { showAuto(error.data.estimate, event); say("The workflow changed. Check the numbers again.", true); }
            else { say(error.message, true); pressedButton.disabled = false; }
          }
        }, "btn-primary"), button("Cancel", () => { confirm.hidden = true; })));
      confirm.hidden = false;
    }
    const say = (text, bad) => { status.textContent = text; status.className = "small " + (bad ? "error" : "muted"); };
    const touch = () => { dirty = true; lastRun = null; say("Unsaved changes"); };
    name.addEventListener("input", touch);
    function showProblems(text) { if (!definition.steps.length) text = ""; problems.hidden = !text; fill(problems, ...String(text || "").split("\n").map(line => el("p", {}, line))); }

    function summary(step, block) {
      const parts = [];
      for (const field of block.fields) {
        const value = step.settings[field.key];
        if (value == null || value === "" || value === false || (Array.isArray(value) && !value.length)) continue;
        if (value === true) { parts.push(field.label); continue; }
        if (field.type === "conditions") parts.push((value.rules || []).map(rule => rule.field + " " + (catalog.conditions.ops.find(op => op[0] === rule.op)?.[1] || rule.op) + (rule.value ? " " + rule.value : "")).join(value.match === "any" ? " or " : " and "));
        else if (field.type === "choice") parts.push((field.options ? field.label + ": " : "") + (choicesFor(field).find(choice => choice[0] === value) || [0, value])[1]);
        else parts.push(field.label + ": " + (Array.isArray(value) ? value.join(", ") : value));
      }
      if (step.when?.rules?.length) parts.push("only when " + rulesText(step.when));
      return parts.join(" · ").slice(0, 200);
    }
    const rulesText = value => (value.rules || []).map(rule => rule.field + " " + (catalog.conditions.ops.find(op => op[0] === rule.op)?.[1] || rule.op) + (rule.value ? " " + rule.value : "")).join(value.match === "any" ? " or " : " and ");
    // The values a step can use: the fields of every result, plus names given by earlier Take a value / Set a value steps.
    // What flows into a step: the kind of record, and the values available on it.
    const takes = block => block.takes === "any" || block.takes == null ? [] : [].concat(block.takes);
    const fieldsOf = kind => catalog.fields?.[kind] || (kind === "endpoint" ? catalog.conditions.fields : []);
// Connections. A step takes its results from the steps in "after"; without it, from the step above.
    const portsOf = block => block?.ports?.length ? block.ports : [""];
    const refOf = (step, port) => step.id + (port ? ":" + port : "");
    const indexOfId = id => definition.steps.findIndex(step => step.id === id);
    const sideName = port => port.charAt(0).toUpperCase() + port.slice(1);
    function sourcesOf(index) {
      if (!index) return [];
      const step = definition.steps[index];
      if (Array.isArray(step.after)) return step.after;
      const above = definition.steps[index - 1];
      return blocks[above.block]?.ports?.length ? [] : [above.id];
    }
    const branched = () => definition.steps.some(step => Array.isArray(step.after));
    function refName(ref) {
      const [id, port] = ref.split(":"), step = definition.steps[indexOfId(id)];
      return (blocks[step?.block]?.name || "Unavailable block") + (port ? " · " + sideName(port) : "");
    }
    function flows() {
      const out = {}, inp = [];
      definition.steps.forEach((step, index) => {
        const block = blocks[step.block], from = sourcesOf(index).map(ref => out[ref]).filter(Boolean);
        let kind = from.find(item => item.kind)?.kind ?? null;
        let known = from.length ? from.map(item => item.known).reduce((left, right) => left.filter(key => right.includes(key))) : [];
        inp[index] = {kind, known: [...known]};
        if (block) {
          if (block.gives !== "same") {
            if (block.gives !== kind || block.start || block.takes === "any" || !takes(block).includes(block.gives)) known = [...fieldsOf(block.gives)];
            kind = block.gives;
          }
          const added = block.names_setting ? String(step.settings[block.names_setting] || "").trim() : "";
          for (const name of [added, ...(block.adds_fixed || [])]) if (name && !known.includes(name)) known.push(name);
        }
        for (const port of portsOf(block)) out[refOf(step, port)] = {kind, known: [...known]};
      });
      return {inp, out};
    }
    const outOf = (out, step) => step ? out[refOf(step, portsOf(blocks[step.block])[0])] : null;
    function flowAt(index) {
      const {inp, out} = flows();
      return (index < definition.steps.length ? inp[index] : outOf(out, definition.steps[index - 1])) || {kind: null, known: []};
    }
    // Every structural change works on explicit connections, then drops the ones that only say "the step above".
    function explicit() {
      definition.steps.forEach((step, index) => { if (index && !Array.isArray(step.after)) step.after = sourcesOf(index); });
    }
    function tidy() {
      // Keep sources above their users (stable), then forget connections that match the plain order.
      const placed = [], left = [...definition.steps];
      while (left.length) {
        const at = left.findIndex(step => (step.after || []).every(ref => placed.some(done => done.id === ref.split(":")[0]) || !left.some(other => other.id === ref.split(":")[0])));
        placed.push(...left.splice(at < 0 ? 0 : at, 1));
      }
      definition.steps.splice(0, definition.steps.length, ...placed);
      const lines = definition.steps.map((step, index) => index && Array.isArray(step.after) && step.after.length === 1 && step.after[0] === definition.steps[index - 1].id && !blocks[definition.steps[index - 1].block]?.ports?.length);
      if (definition.steps.every((step, index) => !index || lines[index])) definition.steps.forEach(step => { delete step.after; });
      else delete definition.steps[0]?.after;
    }
    function feeds(id, target) {
      // Does the step "id" (directly or through others) feed the step "target"?
      const seen = new Set(), queue = [target];
      while (queue.length) {
        const at = indexOfId(queue.pop());
        for (const ref of at > 0 ? sourcesOf(at) : []) { const from = ref.split(":")[0]; if (from === id) return true; if (!seen.has(from)) { seen.add(from); queue.push(from); } }
      }
      return false;
    }
    function connect(ref, targetId) {
      const from = ref.split(":")[0], target = definition.steps[indexOfId(targetId)], block = blocks[target?.block];
      pending = null;
      if (!target || from === targetId) return draw();
      if (block?.start) { say("A start block cannot come after another step", true); return draw(); }
      if (feeds(targetId, from)) { say("That would make a loop: " + refName(targetId) + " already leads to " + refName(from) + ".", true); return draw(); }
      const kind = flows().out[ref]?.kind;
      if (block && kind && block.takes !== "any" && !takes(block).includes(kind)) { say(block.name + " needs " + takes(block).join(" or ") + " items, but " + refName(ref) + " gives " + kind + ".", true); return draw(); }
      explicit();
      if (!target.after.includes(ref)) target.after.push(ref);
      tidy(); open = indexOfId(targetId); touch(); draw();
    }
    function disconnect(targetId, ref) {
      explicit();
      const target = definition.steps[indexOfId(targetId)];
      if (target) target.after = target.after.filter(item => item !== ref);
      picked = null; tidy(); open = indexOfId(targetId); touch(); draw();
    }
    function removeStep(index) {
      if (branched() && index) {
        // What came after the removed step now comes after whatever fed it.
        explicit();
        const gone = definition.steps[index], inherited = gone.after || [];
        for (const step of definition.steps) if (Array.isArray(step.after) && step !== gone)
          step.after = [...new Set(step.after.flatMap(ref => ref.split(":")[0] === gone.id ? inherited : [ref]))];
        definition.steps.splice(index, 1); tidy();
      } else definition.steps.splice(index, 1);
      open = -1; pending = picked = null; touch(); draw();
    }
    const knownAt = index => flowAt(index).known;
    // Conditions editor shared by a block's own condition setting and a step's "only when".
    function rulesEditor(value, required, fields, changed) {
      const box = el("div", {class: "build-conditions"});
      const paint = () => {
        const match = el("select", {class: "select", "aria-label": "Match"}, el("option", {value: "all"}, "All of these"), el("option", {value: "any"}, "Any of these"));
        match.value = value.match || "all"; match.addEventListener("change", () => { value.match = match.value; changed(); });
        fill(box, value.rules.length > 1 ? match : null, value.rules.map((rule, index) => {
          const listed = fields.includes(rule.field) ? fields : [...fields, rule.field];
          const name = el("select", {class: "select", "aria-label": "Field"}, listed.map(key => el("option", {value: key}, key.replace(/_/g, " "))));
          const op = el("select", {class: "select", "aria-label": "Comparison"}, catalog.conditions.ops.map(([key, label]) => el("option", {value: key}, label)));
          const text = el("input", {class: "input", "aria-label": "Value", value: rule.value || ""});
          name.value = rule.field; op.value = rule.op;
          name.addEventListener("change", () => { rule.field = name.value; changed(); });
          op.addEventListener("change", () => { rule.op = op.value; changed(); });
          text.addEventListener("input", () => { rule.value = text.value; changed(); });
          return el("div", {class: "build-rule"}, name, op, text, value.rules.length > 1 || !required ? button("Remove", () => { value.rules.splice(index, 1); changed(); paint(); }, "btn-quiet") : null);
        }), value.rules.length < 10 ? button("Add condition", () => { value.rules.push({field: fields.includes("technology") ? "technology" : fields[0] || "technology", op: "is", value: ""}); changed(); paint(); }) : null);
      };
      paint();
      return box;
    }
    // "Insert a value": puts {{name}} into a setting that accepts values.
    function withPicker(input, index, changed) {
      const known = knownAt(index);
      if (!known.length) return input;
      const pick = el("select", {class: "select build-picker", "aria-label": "Insert a value"}, el("option", {value: ""}, "Insert a value…"), known.map(key => el("option", {value: key}, "{{" + key + "}}")));
      pick.addEventListener("change", () => {
        if (!pick.value) return;
        const at = input.selectionStart ?? input.value.length;
        input.value = input.value.slice(0, at) + "{{" + pick.value + "}}" + input.value.slice(input.selectionEnd ?? at);
        pick.value = ""; changed();
      });
      return el("span", {class: "build-templated"}, input, pick);
    }
    // A choice either lists its own values or draws them from saved data (organizations).
    const choicesFor = field => field.options ? (catalog.options?.[field.options] || []) : field.choices;
    function suggestions(name) {
      const id = "build-suggest-" + name;
      if (!document.getElementById(id)) document.body.append(el("datalist", {id}, (catalog.options?.[name] || []).map(value => el("option", {value}))));
      return id;
    }
    function control(step, field, index) {
      const set = value => { step.settings[field.key] = value; touch(); refreshSummary(); };
      const current = step.settings[field.key] ?? field.default ?? "";
      if (field.type === "choice") {
        const listed = choicesFor(field), missing = current && !listed.some(choice => choice[0] === current);
        const input = el("select", {class: "select", name: field.key},
          field.options || (field.required && !current) ? el("option", {value: ""}, field.required ? "Choose…" : "Any") : null,
          listed.map(([value, label]) => el("option", {value}, label)), missing ? el("option", {value: current}, "No longer available") : null);
        input.value = current; input.addEventListener("change", () => set(input.value));
        return input;
      }
      if (field.type === "flag") {
        const box = el("input", {type: "checkbox", name: field.key});
        box.checked = step.settings[field.key] === true || (step.settings[field.key] == null && field.default === true);
        box.addEventListener("change", () => set(box.checked));
        return box;
      }
      if (field.type === "number") {
        const input = el("input", {class: "input", type: "number", name: field.key, min: field.minimum, max: field.maximum, value: current});
        input.addEventListener("input", () => set(input.value === "" ? null : Number(input.value)));
        return input;
      }
      if (field.type === "tags") {
        const input = el("input", {class: "input", name: field.key, placeholder: "comma separated", value: (Array.isArray(current) ? current : []).join(", ")});
        const read = () => set(input.value.split(",").map(tag => tag.trim()).filter(Boolean));
        input.addEventListener("input", read);
        return field.template ? withPicker(input, index, read) : input;
      }
      if (field.type === "list") {
        // Targets chosen by hand: typed one per line, or ticked from saved addresses and domains.
        const chosen = () => Array.isArray(step.settings[field.key]) ? step.settings[field.key] : [];
        const area = el("textarea", {class: "input build-listbox", name: field.key, rows: 4, placeholder: "One per line"});
        area.value = chosen().join("\n");
        const count = el("span", {class: "small muted"});
        const tell = () => { count.textContent = plural(chosen().length, "entry").replace("entrys", "entries") + " chosen"; };
        const read = () => { set([...new Set(area.value.split(/[\n,]+/).map(part => part.trim()).filter(Boolean))]); tell(); };
        area.addEventListener("input", read);
        tell();
        if (!field.suggest) return el("div", {class: "build-pick"}, area, count);
        const what = field.suggest === "domain" ? "domains" : "addresses";
        const find = el("input", {class: "input", type: "search", name: field.key + "-find", placeholder: "Find saved " + what + " to add", "aria-label": "Find saved " + what});
        const found = el("div", {class: "build-pick-results"});
        const put = (value, on) => { const now = chosen().filter(item => item !== value); if (on) now.push(value); area.value = now.join("\n"); set(now); tell(); };
        let asked = 0;
        find.addEventListener("input", async () => {
          const mine = ++asked, text = find.value.trim();
          if (text.length < 2) { fill(found); return; }
          try {
            const data = await api(API + "/pick?kind=" + encodeURIComponent(field.suggest) + "&q=" + encodeURIComponent(text));
            if (mine !== asked) return;
            const boxes = [];
            fill(found, data.results.length ? [button("Add all " + data.results.length, () => { for (const row of data.results) put(row.value, true); boxes.forEach(box => { box.checked = true; }); }, "btn-quiet"),
              data.results.map(row => { const box = el("input", {type: "checkbox"}); box.checked = chosen().includes(row.value); box.addEventListener("change", () => put(row.value, box.checked)); boxes.push(box);
                return el("label", {class: "build-pick-row"}, box, el("span", {}, row.value), row.note ? el("span", {class: "small muted"}, row.note) : null); })]
              : el("p", {class: "small muted"}, "Nothing saved matches “" + text + "”. You can still type it above."));
          } catch (error) { fill(found, el("p", {class: "small error"}, error.message)); }
        });
        return el("div", {class: "build-pick"}, area, count, find, found);
      }
      if (field.type === "conditions") {
        // A required condition starts with one rule to fill in; an optional one starts empty and is sent as nothing.
        const value = step.settings[field.key] && typeof step.settings[field.key] === "object" ? step.settings[field.key] : {match: "all", rules: field.required ? [{field: "technology", op: "is", value: ""}] : []};
        if (value.rules.length) step.settings[field.key] = value;
        const fields = knownAt(index + (blocks[step.block]?.start ? 1 : 0));
        return rulesEditor(value, field.required, fields.length ? fields : catalog.conditions.fields, () => set(value.rules.length ? value : null));
      }
      if (field.suggest === "fields") {
        const pick = el("select", {class: "select", name: field.key}, el("option", {value: ""}, "Choose…"), [...new Set([...knownAt(index), current].filter(Boolean))].map(key => el("option", {value: key}, key.replace(/_/g, " "))));
        pick.value = current; pick.addEventListener("change", () => set(pick.value));
        return pick;
      }
      const input = el("input", {class: "input", name: field.key, value: current, list: field.suggest ? suggestions(field.suggest) : null});
      const read = () => set(input.value);
      input.addEventListener("input", read);
      return field.template ? withPicker(input, index, read) : input;
    }
    let refreshSummary = () => {};
    const arrow = (text, label, action) => { const node = button(text, action, "btn-quiet"); node.setAttribute("aria-label", label); return node; };
    function draw() {
      fill(steps, ...definition.steps.map((step, index) => {
        const block = blocks[step.block];
        const move = (to) => { const [item] = definition.steps.splice(index, 1); definition.steps.splice(to, 0, item); open = -1; touch(); draw(); };
        const remove = button("Remove", () => removeStep(index), "btn-quiet");
        if (!block) return el("li", {class: "build-step is-unavailable"}, el("div", {class: "build-step-head"}, el("span", {class: "build-number"}, index + 1), mark("unknown"),
          el("div", {class: "build-step-title"}, el("strong", {}, "Unavailable block"), el("span", {class: "small muted"}, step.block || "No block chosen")), remove));
        const line = el("span", {class: "small muted build-summary"}, summary(step, block));
        const body = el("div", {class: "build-step-body", hidden: open !== index}, block.fields.length
          ? block.fields.map(field => el("label", {class: "field"}, el("span", {class: "field-label"}, field.label + (field.required ? "" : " (optional)")), control(step, field, index), field.help ? el("small", {class: "muted"}, field.help) : null))
          : el("p", {class: "small muted"}, "Nothing to set."),
          index ? comesAfter(step, index) : null,
          // If / otherwise: a step can run for some results only; the rest pass through to the next step.
          !block.start && (block.gives === "same" || takes(block).includes(block.gives)) ? (() => {
            const when = step.when && typeof step.when === "object" ? step.when : {match: "all", rules: []};
            const known = knownAt(index);
            return el("div", {class: "field build-when"}, el("span", {class: "field-label"}, "Only for results where (optional)"),
              rulesEditor(when, false, known.length ? known : catalog.conditions.fields, () => { if (when.rules.length) step.when = when; else delete step.when; touch(); refreshSummary(); }),
              el("small", {class: "muted"}, "Other results skip this step and continue to the next one."));
          })() : null);
        if (open === index) refreshSummary = () => { line.textContent = summary(step, block); };
        const toggle = button(open === index ? "Close" : "Edit", () => { open = open === index ? -1 : index; draw(); });
        toggle.setAttribute("aria-expanded", String(open === index));
        return el("li", {class: "build-step build-group-edge-" + block.group, "data-step": step.id},
          el("div", {class: "build-step-head"}, el("span", {class: "build-number"}, index + 1), mark(block.group),
            el("div", {class: "build-step-title"}, el("strong", {}, block.name), line), reach(block.reach),
            el("span", {class: "build-step-actions"}, toggle,
              !branched() && index > 1 ? arrow("↑", "Move up", () => move(index - 1)) : null, !branched() && index > 0 && index < definition.steps.length - 1 ? arrow("↓", "Move down", () => move(index + 1)) : null,
              remove)), body);
      }));
      if (!definition.steps.length) steps.append(el("li", {class: "build-empty"}, el("strong", {}, "Pick how this workflow starts."), el("span", {class: "small muted"}, "Choose a block under Starts in the library. Then add blocks in the order they should run.")));
      library();
      drawMap();
    }
    // "Comes after": the same connections as the lines on the map, changeable without dragging.
    function comesAfter(step, index) {
      const from = sourcesOf(index), {out} = flows();
      const more = Object.keys(out).filter(ref => !from.includes(ref) && ref.split(":")[0] !== step.id && !feeds(step.id, ref.split(":")[0]));
      const pick = el("select", {class: "select", name: "after", "aria-label": "Add a step this comes after"}, el("option", {value: ""}, from.length ? "Also after…" : "Choose a step…"), more.map(ref => el("option", {value: ref}, refName(ref))));
      pick.addEventListener("change", () => { if (pick.value) connect(pick.value, step.id); });
      return el("div", {class: "field build-after"}, el("span", {class: "field-label"}, "Comes after"),
        el("span", {class: "build-after-list"}, from.length ? from.map(ref => el("span", {class: "build-after-chip"}, refName(ref),
          from.length > 1 || branched() ? el("button", {type: "button", class: "build-after-drop", "aria-label": "Disconnect from " + refName(ref), onclick: () => disconnect(step.id, ref)}, "×") : null))
          : el("span", {class: "small build-why"}, "Not connected. Choose the step it follows."), more.length ? pick : null),
        from.length > 1 ? el("small", {class: "muted"}, "Results from every listed step arrive together, each once.") : null);
    }
    // The map: every step as a box, top to bottom, with a line for each connection. Press a dot under a box, then
    // press (or drag to) the step that should come next, or pick a block in the library to add one there.
    const W = 196, H = 54, GX = 26, GY = 46, PAD = 14;
    const svg = (tag, attrs) => { const node = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); return node; };
    function drawMap() {
      const count = definition.steps.length;
      canvas.hidden = mapBar.hidden = !count;
      if (!count) return;
      const level = [], rows = [];
      definition.steps.forEach((step, index) => {
        const from = sourcesOf(index).map(ref => indexOfId(ref.split(":")[0])).filter(at => at >= 0 && at < index);
        level[index] = from.length ? Math.max(...from.map(at => level[at])) + 1 : index ? level[index - 1] + 1 : 0;
        (rows[level[index]] = rows[level[index]] || []).push(index);
      });
      // Each box sits under the middle of what feeds it; boxes in a row are then pushed apart so none overlap.
      const at = [], sidesOf = index => portsOf(blocks[definition.steps[index].block]);
      const offset = (index, port) => { const sides = sidesOf(index), n = Math.max(0, sides.indexOf(port)); return sides.length > 1 ? W * (0.27 + 0.46 * n / (sides.length - 1)) : W / 2; };
      rows.forEach((row, depth) => {
        const wanted = row.map(index => {
          const from = sourcesOf(index).map(ref => [indexOfId(ref.split(":")[0]), ref.split(":")[1] || ""]).filter(([source]) => source >= 0 && source < index && at[source]);
          return {index, x: from.length ? from.reduce((sum, [source, port]) => sum + at[source].x + offset(source, port), 0) / from.length - W / 2 : 0};
        }).sort((left, right) => left.x - right.x || left.index - right.index);
        wanted.forEach((item, order) => { if (order && item.x < wanted[order - 1].x + W + GX) item.x = wanted[order - 1].x + W + GX; at[item.index] = {x: item.x, y: PAD + depth * (H + GY)}; });
      });
      const least = Math.min(...at.map(place => place.x)), inner = Math.max(...at.map(place => place.x)) - least + W;
      at.forEach(place => { place.x += PAD - least; });
      const portX = (index, port) => at[index].x + offset(index, port);
      const width = inner + PAD * 2, height = rows.length * (H + GY) - GY + PAD * 2 + 12;
      const lines = svg("svg", {class: "build-lines", width, height, "aria-hidden": "true"});
      const board = el("div", {class: "build-board", style: "width:" + width + "px;height:" + height + "px"}, lines);
      definition.steps.forEach((step, index) => {
        for (const ref of sourcesOf(index)) {
          const [id, port] = ref.split(":"), source = indexOfId(id);
          if (source < 0 || source >= index) continue;
          const x1 = portX(source, port || ""), y1 = at[source].y + H + 7, x2 = at[index].x + W / 2, y2 = at[index].y, mid = (y1 + y2) / 2;
          const d = "M" + x1 + " " + y1 + " C" + x1 + " " + mid + " " + x2 + " " + mid + " " + x2 + " " + y2;
          const chosen = picked && picked.to === step.id && picked.ref === ref;
          const reached = lastRun && lastRun.get(id)?.state === "done" && (port ? (lastRun.get(id).branches || {})[port] > 0 : lastRun.get(id).items_out > 0);
          lines.append(svg("path", {d, class: "build-line" + (chosen ? " is-picked" : "") + (reached ? " is-live" : "")}));
          const hit = svg("path", {d, class: "build-line-hit", "data-line": ref + ">" + step.id});
          hit.addEventListener("click", () => { picked = chosen ? null : {to: step.id, ref}; pending = null; drawMap(); });
          lines.append(hit);
        }
      });
      const wire = svg("path", {class: "build-line is-wire", d: ""});
      lines.append(wire);
      definition.steps.forEach((step, index) => {
        const block = blocks[step.block], run = lastRun?.get(step.id), loose = index > 0 && !sourcesOf(index).length;
        const line = run ? (STATE[run.state] || run.state) + (run.state === "skipped" ? "" : " · " + run.items_in + " → " + (run.branches ? Object.entries(run.branches).map(([side, n]) => n + " " + side).join(" · ") : run.items_out))
          : loose ? "Not connected" : block ? summary(step, block) || REACH[block.reach] : step.block || "No block chosen";
        const main = el("button", {type: "button", class: "build-node-main", "aria-pressed": String(open === index), onclick: () => {
          if (pending && pending.split(":")[0] !== step.id) return connect(pending, step.id);
          pending = picked = null; open = open === index ? -1 : index; draw();
          if (open === index) steps.querySelector('[data-step="' + CSS.escape(step.id) + '"]')?.scrollIntoView?.({block: "nearest"});
        }}, el("strong", {}, block ? block.name : "Unavailable block"), el("span", {class: "build-node-line"}, line));
        const node = el("div", {class: "build-node build-group-edge-" + (block ? block.group : "unknown") + (open === index ? " is-open" : "") + (loose ? " is-loose" : "") + (run ? " is-" + run.state : "") + (block ? "" : " is-missing"),
          "data-node": step.id, style: "left:" + at[index].x + "px;top:" + at[index].y + "px;width:" + W + "px;height:" + H + "px"}, main,
          portsOf(block).map(port => {
            const ref = refOf(step, port);
            return el("button", {type: "button", class: "build-port" + (port ? " has-name" : "") + (pending === ref ? " is-pending" : ""), "data-port": ref, style: "left:" + (portX(index, port) - at[index].x) + "px",
              "aria-label": "Connect from " + refName(ref), "aria-pressed": String(pending === ref),
              onclick: () => { pending = pending === ref ? null : ref; picked = null; draw(); },
              onpointerdown: event => {
                event.target.releasePointerCapture?.(event.pointerId);
                const box = board.getBoundingClientRect(), x1 = portX(index, port), y1 = at[index].y + H + 7;
                const move = moved => { wire.setAttribute("d", "M" + x1 + " " + y1 + " L" + (moved.clientX - box.left) + " " + (moved.clientY - box.top)); };
                const up = released => {
                  board.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); wire.setAttribute("d", "");
                  const target = released.target?.closest?.("[data-node]")?.getAttribute("data-node");
                  if (target && target !== step.id) connect(ref, target);
                };
                board.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
              }}, port ? sideName(port) : "");
          }));
        board.append(node);
      });
      fill(canvas, board);
      fill(mapBar, el("span", {class: "small muted"}, pending ? "Now press the step that comes after " + refName(pending) + ", or pick a block to add there." : picked ? refName(picked.ref) + " → " + refName(picked.to)
          : lastRun ? "Showing the last run. Edit anything to return to the plan." : "Press the dot under a step, then the step that comes next. Press a line to remove it."),
        pending ? button("Cancel", () => { pending = null; draw(); }, "btn-quiet") : null,
        picked ? button("Remove connection", () => disconnect(picked.to, picked.ref), "btn-quiet") : null);
    }
    // The block library is always on screen: every block, searchable, with the reason when one cannot go next.
    function why(block) {
      if (!definition.steps.length) return block.start ? null : "Pick how the workflow starts first.";
      if (block.start) return null;
      const anchor = pending ? definition.steps[indexOfId(pending.split(":")[0])] : definition.steps[definition.steps.length - 1];
      const previous = blocks[anchor?.block], kind = pending ? flows().out[pending]?.kind : flowAt(definition.steps.length).kind;
      if (!previous || block.takes === "any" || takes(block).includes(kind)) return null;
      if (takes(block).includes("endpoint")) return kind === "domain" ? "Needs results, and this workflow holds domains here. Add “Saved results for each domain” or a search first." : "Needs results to work on. Add a search first, or start with saved results.";
      if (takes(block).includes("domain")) return "Needs domains. Add “Names of each result”, or start with saved domains or certificate names.";
      return "Does not fit after " + previous.name + ".";
    }
    function add(block) {
      const taken = new Set(definition.steps.map(step => step.id)); let n = definition.steps.length + 1; while (taken.has("s" + n)) n += 1;
      if (block.start && definition.steps.length) { definition.steps[0] = {id: definition.steps[0].id, block: block.key, settings: {}}; open = 0; }
      else {
        // After the port that was chosen on the map; otherwise after the last step (the first free side of an If).
        const last = definition.steps[definition.steps.length - 1], sides = blocks[last?.block]?.ports || [];
        const used = new Set(definition.steps.flatMap(step => step.after || []));
        const from = pending || (sides.length ? refOf(last, sides.find(port => !used.has(refOf(last, port))) || sides[0]) : null);
        if (from || branched()) explicit();
        definition.steps.push({id: "s" + n, block: block.key, settings: {}, ...(from ? {after: [from]} : branched() ? {after: [last.id]} : {})});
        if (from || branched()) tidy();
        open = indexOfId("s" + n); pending = null;
      }
      touch(); draw();
    }
    function library() {
      const text = find.value.trim().toLowerCase();
      const groups = [...new Set(catalog.blocks.map(block => block.group))];
      fill(kinds, groups.map(group => el("button", {type: "button", class: "build-kind build-group-" + group, "aria-pressed": only === group ? "true" : "false",
        onclick: () => { only = only === group ? null : group; library(); }}, mark(group), GROUPS[group] || group)));
      const shown = catalog.blocks.filter(block => (!only || block.group === only) && (!text || (block.name + " " + (block.says || "") + " " + GROUPS[block.group] + " " + (REACH[block.reach] || "")).toLowerCase().includes(text)));
      fill(shelf, shown.length ? [...new Set(shown.map(block => block.group))].map(group => el("div", {class: "build-choose-group"}, el("h4", {}, mark(group), GROUPS[group]),
        shown.filter(block => block.group === group).map(block => {
          const reason = why(block);
          return el("button", {type: "button", class: "build-choice" + (reason ? " is-off" : ""), "data-block": block.key, "aria-disabled": reason ? "true" : null,
            onclick: () => { if (!why(block)) add(block); }},
            el("span", {class: "build-choice-name"}, el("strong", {}, block.name), reach(block.reach)),
            block.says ? el("span", {class: "small muted"}, block.says) : null,
            reason ? el("span", {class: "small build-why"}, reason) : block.start && definition.steps.length ? el("span", {class: "small build-why"}, "Replaces step 1.") : null);
        }))) : el("p", {class: "small muted"}, "No block" + (only ? " in " + GROUPS[only] : "") + " matches “" + find.value.trim() + "”."));
    }
    find.addEventListener("input", library);
    const limits = el("div", {class: "build-limits"}, [["pages", "Provider pages per run"], ["contacts", "Target contacts per run"], ["items", "Items per step"], ["runs_per_day", "Automatic runs per day"]].map(([key, label]) => {
      const input = el("input", {class: "input", type: "number", name: "limit-" + key, min: 1, max: catalog.limits.max[key], value: definition.limits[key]});
      input.addEventListener("input", () => { definition.limits[key] = Number(input.value); touch(); });
      return el("label", {class: "field"}, el("span", {class: "field-label"}, label), input);
    }));
    const payload = () => ({name: name.value, definition});
    async function save() {
      const saved = await api(API + "/workflows/" + encodeURIComponent(id) + "/save", payload());
      dirty = false; showProblems(saved.problems); say(saved.problems ? "Saved as a draft; it cannot run yet" : "Saved");
      drawAuto(saved.automatic);
      return saved;
    }
    function showConfirm(numbers) {
      const line = (kind, text) => el("li", {}, reach(kind), el("span", {}, text));
      const local = !numbers.pages && !numbers.contacts && !outward(numbers).length;
      fill(confirm, el("h3", {}, "Run this workflow now?"), el("ul", {class: "build-confirm-list"},
        numbers.pages ? line("credits", "up to " + plural(numbers.pages, "provider page")) : null,
        numbers.contacts ? line("target", "up to " + plural(numbers.contacts, "target contact")) : null,
        outward(numbers).includes("public") ? line("public", "asks public sources such as DNS and registries") : null,
        outward(numbers).includes("send") ? line("send", "sends data to the addresses set in its steps") : null,
                local ? line("local", "reads and changes saved data only") : null,
        el("li", {}, el("span", {class: "build-reach"}, "Items"), el("span", {}, "up to " + numbers.items + " at any step"))),
        el("div", {class: "build-confirm-actions"}, button("Run", async event => {
          // One click starts one run; the server also refuses a second run while one is active.
          const pressed = event.currentTarget; pressed.disabled = true;
          try {
            const started = await api(API + "/workflows/" + encodeURIComponent(id) + "/run", {token: numbers.token});
            confirm.hidden = true; say("Run started"); watch(started.run.id);
          } catch (error) {
            if (error.status === 409 && error.data.estimate) { showConfirm(error.data.estimate); say("The workflow changed. Check the numbers again.", true); }
            else { say(error.message, true); pressed.disabled = false; }
          }
        }, "btn-primary"), button("Cancel", () => { confirm.hidden = true; })));
      confirm.hidden = false;
    }
    async function run() {
      try {
        if (dirty) { const saved = await save(); if (saved.problems) return; }
        showConfirm(await api(API + "/workflows/" + encodeURIComponent(id) + "/estimate", {}));
      } catch (error) { showProblems(error.status === 400 ? error.message : ""); say(error.status === 400 ? "Fix the problems above before running" : error.message, true); }
    }
    function drawRun(view) {
      const state = view.run.state, active = ACTIVE.includes(state);
      if (!dirty) { lastRun = new Map(view.steps.map(step => [step.id, step])); drawMap(); }
      fill(runView, el("div", {class: "build-bar"}, el("h3", {}, "Run · " + (STATE[state] || state)),
        view.run.can_cancel ? button("Stop", async () => { try { await api("/api/runs/" + encodeURIComponent(view.run.id) + "/cancel", {}); watch(view.run.id); } catch (error) { say(error.message, true); } }, "btn-danger") : null),
        view.partial && view.stopped_at ? el("div", {class: "notice build-partial"}, "Stopped early at " + view.stopped_at.name + ": " + view.stopped_at.reason + ".") : null,
        state === "failed" && view.run.error ? el("div", {class: "notice error"}, view.run.error) : null,
        el("p", {class: "small muted"}, plural(view.spent.pages || 0, "provider page") + " of " + (view.limits.pages ?? "—") + " · " + plural(view.spent.contacts || 0, "target contact") + " of " + (view.limits.contacts ?? "—")),
        window.LeakScopeExport ? window.LeakScopeExport.bar(() => ({headers: ["Step", "State", "In", "Out", "Note"], rows: view.steps.map(step => [step.name, STATE[step.state] || step.state, String(step.items_in ?? ""), step.branches ? Object.entries(step.branches).map(([side, count]) => count + " " + side).join(", ") : String(step.items_out ?? ""), step.note || ""])}), "Workflow run steps") : null,
        el("ol", {class: "build-run-steps"}, view.steps.map(step => el("li", {class: "build-run-step is-" + step.state},
          el("strong", {}, step.name), el("span", {class: "build-run-state"}, STATE[step.state] || step.state),
          el("span", {class: "small muted"}, step.state === "skipped" ? "" : step.items_in + " in → " + (step.branches ? Object.entries(step.branches).map(([side, count]) => count + " " + side).join(" · ") : step.items_out + " out") + (step.passed_by ? " · " + step.passed_by + " passed by" : "") + (step.pages ? " · " + plural(step.pages, "page") : "") + (step.contacts ? " · " + plural(step.contacts, "contact") : "")),
          step.note ? el("span", {class: "small build-note"}, step.note) : null,
          // An export step's file is offered as a link; the path is checked, never taken as written.
          (() => { const found = /\/export\/download\/([a-f0-9]{32})\b/.exec(step.note || ""); return found ? el("a", {class: "btn btn-small", href: "/export/download/" + found[1]}, "Download file") : null; })()))),
        el("div", {class: "build-run-links"},
          view.results_url ? el("a", {class: "btn btn-small btn-primary", href: view.results_url}, "Open results in Observations") : null,
          el("a", {class: "btn btn-small", href: "/runs/?run=" + encodeURIComponent(view.run.id)}, "Open in Runs")));
      return active;
    }
    async function watch(runId) {
      stop();
      const current = generation;
      const tick = async () => {
        if (current !== generation) return;
        if (document.hidden) { timer = setTimeout(tick, POLL); return; }
        try {
          const view = await api(API + "/runs/" + encodeURIComponent(runId));
          if (current !== generation) return;
          if (drawRun(view)) timer = setTimeout(tick, POLL);
        } catch (error) { say(error.message, true); }
      };
      await tick();
    }
    const recent = el("div", {class: "build-history"}, detail.runs.length ? [el("h3", {}, "Recent runs"), detail.runs.map(item => button((STATE[item.state] || item.state) + (item.partial ? " · stopped early" : "") + " · " + new Date(item.created_at).toLocaleString(), () => watch(item.id), "btn-quiet"))] : null);
    fill(root, el("section", {class: "panel build-editor"}, el("div", {class: "panel-body"},
      el("div", {class: "build-bar"}, el("a", {href: "/build/", class: "btn btn-small btn-quiet"}, "← All workflows"), name,
        button("Save", () => save().catch(error => say(error.message, true))), button("Run", run, "btn-primary"),
        button("Export", async () => { try { await exportWorkflows([id], name.value || "workflow"); say("Exported the saved version of this workflow."); } catch (error) { say(error.message, true); } }, "btn-quiet"),
        button("Delete", async () => { if (!window.confirm("Delete this workflow and its finished runs?")) return; try { await api(API + "/workflows/" + encodeURIComponent(id) + "/delete", {}); location.assign("/build/"); } catch (error) { say(error.message, true); } }, "btn-quiet")),
      status, auto, problems, confirm, el("div", {class: "build-layout"},
        el("aside", {class: "build-library", "aria-label": "Block library"}, el("h3", {}, "Blocks ", el("span", {class: "small muted"}, catalog.blocks.length)), find, kinds, shelf),
        el("div", {class: "build-flow"}, mapBar, canvas, steps, el("details", {class: "build-limits-box"}, el("summary", {}, "Ceilings for one run"), limits), runView, recent)))));
    showProblems(detail.problems);
    draw();
    drawAuto(detail.automatic);
    if (detail.runs.length) watch(detail.runs[0].id);
  }

  function render(root, id) { return id ? editor(root, id) : list(root); }
  window.addEventListener("pagehide", stop);
  window.LeakScopeBuild = {render, stop};
})();
