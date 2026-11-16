(function () {
  "use strict";
  // One toolbar for every list page: view menu, search, two inline filters, the rest behind
  // "Filters", active filters as chips, Insights and Columns, and a selection bar that exists
  // only while rows are selected. It arranges controls the page owns and never makes a request.
  const DEBOUNCE = 300;
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "hidden") node.hidden = !!value;
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child);
    return node;
  }
  function read(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
  function write(key, value) { try { localStorage.setItem(key, String(value)); } catch (_) {} }
  const isCheck = control => control.type === "checkbox" || control.type === "radio";
  const typed = control => control.tagName === "INPUT" && !isCheck(control) && !["date", "datetime-local", "number"].includes(control.type) || control.tagName === "TEXTAREA";
  const empty = control => control.dataset.default ?? "";
  const active = control => isCheck(control) ? control.checked : control.value !== empty(control);
  function reset(control) {
    if (isCheck(control)) control.checked = false;
    else {
      control.value = empty(control);
      if (control.tagName === "SELECT" && control.selectedIndex < 0) control.selectedIndex = 0;
    }
  }
  function display(entry) {
    const control = entry.control;
    if (isCheck(control)) return entry.label;
    const value = control.tagName === "SELECT" ? control.selectedOptions[0]?.textContent.trim() || control.value : control.value;
    return entry.label + ": " + value;
  }

  function create(options = {}) {
    const {page = "list", search = null, quick = [], more = [], describe = null, extraChips = [], onChange = () => {},
      views = null, insights = null, about = null, selection = null} = options;
    const entries = [...quick, ...more];
    let timer = null, disposed = false;
    const extras = [...extraChips];
    function report() { clearTimeout(timer); timer = null; if (!disposed) { refreshChips(); onChange(); } }
    function later() { clearTimeout(timer); timer = setTimeout(report, DEBOUNCE); }
    const listeners = [];
    function listen(node, type, handler) { node.addEventListener(type, handler); listeners.push(() => node.removeEventListener(type, handler)); }
    for (const control of [search, ...entries.map(entry => entry.control)].filter(Boolean)) {
      if (typed(control)) {
        listen(control, "input", later);
        listen(control, "keydown", event => { if (event.key === "Enter") { event.preventDefault(); report(); } });
      } else listen(control, "change", report);
    }

    const field = entry => el("label", {class: "ls-field"}, el("span", {class: "ls-field-label"}, entry.label), entry.control);
    const filtersToggle = el("button", {type: "button", class: "btn btn-small ls-filters-toggle", "aria-expanded": "false"}, "Filters");
    const panel = el("div", {class: "ls-filters-panel", hidden: true}, more.map(field));
    filtersToggle.hidden = !more.length;
    listen(filtersToggle, "click", () => { panel.hidden = !panel.hidden; filtersToggle.setAttribute("aria-expanded", String(!panel.hidden)); });

    const insightsKey = "leakscope.listbar.insights." + page;
    const insightsRegion = el("div", {class: "ls-insights", hidden: true}, insights);
    const insightsToggle = el("button", {type: "button", class: "btn btn-small ls-insights-toggle", "aria-pressed": "false", hidden: !insights}, "Insights");
    function showInsights(open, remember) {
      insightsRegion.hidden = !open;
      insightsToggle.setAttribute("aria-pressed", String(open));
      if (remember) write(insightsKey, open);
    }
    listen(insightsToggle, "click", () => showInsights(insightsRegion.hidden, true));
    if (insights) showInsights(read(insightsKey) === "true", false);

    const columns = views?.querySelector(".ls-columns") || null;
    const row = el("div", {class: "ls-listbar-row"}, views, search ? el("label", {class: "ls-search"}, el("span", {class: "visually-hidden"}, search.getAttribute("aria-label") || search.placeholder || "Search"), search) : null,
      quick.map(field), filtersToggle, el("span", {class: "ls-listbar-gap"}), insightsToggle, columns);
    const chips = el("div", {class: "ls-chips", hidden: true});

    const count = el("span", {class: "ls-selection-count", "aria-live": "polite"});
    const clear = el("button", {type: "button", class: "btn btn-small btn-quiet"}, "Clear");
    const selectionBar = el("div", {class: "ls-selection", hidden: true, role: "group", "aria-label": "Actions for selected rows"}, count, selection ? selection.actions : null, clear);
    const element = el("section", {class: "ls-listbar", "aria-label": "List controls"}, row, panel, chips, insightsRegion, selectionBar);
    listen(clear, "click", () => element.dispatchEvent(new CustomEvent("ls:clear-selection")));

    const total = el("span", {class: "ls-count"});
    const aboutRegion = el("div", {class: "ls-about", hidden: true}, about);
    const aboutToggle = el("button", {type: "button", class: "ls-about-toggle", "aria-expanded": "false", hidden: !about}, "ⓘ About this data");
    listen(aboutToggle, "click", () => { aboutRegion.hidden = !aboutRegion.hidden; aboutToggle.setAttribute("aria-expanded", String(!aboutRegion.hidden)); });
    const pager = el("div", {class: "ls-pager"});
    const footer = el("div", {class: "ls-listfoot"}, el("div", {class: "ls-listfoot-row"}, total, aboutToggle, pager), aboutRegion);

    function chip(text, remove) {
      const node = el("button", {type: "button", class: "ls-chip", "aria-label": "Remove filter: " + text}, text);
      node.addEventListener("click", remove);
      return node;
    }
    function refreshChips() {
      const nodes = [];
      for (const entry of entries) {
        if (!active(entry.control)) continue;
        const text = (describe && describe(entry.control)) || display(entry);
        nodes.push(chip(text, () => { reset(entry.control); report(); }));
      }
      for (const extra of extras) nodes.push(chip(extra.text, () => { extras.splice(extras.indexOf(extra), 1); extra.remove(); report(); }));
      const searching = !!(search && search.value.trim());
      if (nodes.length || searching) {
        const all = el("button", {type: "button", class: "ls-chip-clear"}, "Clear all");
        all.addEventListener("click", () => {
          entries.forEach(entry => reset(entry.control));
          if (search) search.value = "";
          extras.splice(0).forEach(extra => extra.remove());
          report();
        });
        nodes.push(all);
      }
      chips.replaceChildren(...nodes);
      chips.hidden = !nodes.length;
      const hidden = more.filter(entry => active(entry.control)).length;
      filtersToggle.textContent = hidden ? "Filters · " + hidden : "Filters";
    }
    function setSelected(value) {
      const number = Math.max(0, Number(value) || 0);
      selectionBar.hidden = !number;
      count.textContent = number + " selected" + (selection?.limit ? " · up to " + selection.limit + " per batch" : "");
    }
    refreshChips();
    setSelected(0);
    return {
      element, footer, pager, refreshChips, setSelected,
      setCount(text) { total.textContent = text; },
      dispose() { disposed = true; clearTimeout(timer); listeners.splice(0).forEach(off => off()); }
    };
  }
  window.LeakScopeListBar = {create};
})();
