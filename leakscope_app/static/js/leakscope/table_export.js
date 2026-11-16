(function (root) {
  "use strict";
  // Every results table gets "Export CSV" and "Export TXT". The file is built in the browser from
  // the rows the table already holds (all rows matching its own search, not only the page shown),
  // so exporting never makes a request. Opt a table out with data-export="false".
  const doc = root.document;
  const SKIP = /^(select|actions?|proof|evidence links|evidence \/ inspection)$/i;
  const bars = new WeakMap(), cardBars = new WeakMap();

  const clean = value => String(value == null ? "" : value).replace(/\s+/g, " ").trim();
  const HIDDEN = "[hidden], [aria-hidden=true], input, select, textarea, script, style";
  function cellText(cell) {
    if (!cell) return "";
    const copy = cell.cloneNode(true);
    copy.querySelectorAll(HIDDEN).forEach(node => node.remove());
    // Stacked parts of one cell (a title over a sub-line) are separate words, not run together.
    const walker = doc.createTreeWalker(copy, 4), parts = [];
    for (let piece = walker.nextNode(); piece; piece = walker.nextNode()) {
      const joined = parts.length && (piece.parentNode.nodeName === "MARK" || piece.previousSibling?.nodeName === "MARK" || piece.previousSibling?.nodeName === "WBR");
      if (joined) parts[parts.length - 1] += piece.data; else parts.push(piece.data);
    }
    const value = clean(parts.join(" "));
    return value === "—" ? "" : value;
  }
  function label(header, index) {
    return clean(header.querySelector(".ls-table-sort-label")?.textContent || header.textContent) || "Column " + (index + 1);
  }
  function bodyRows(table) {
    const rows = typeof table.lsExportRows === "function" ? table.lsExportRows() : Array.from(table.tBodies).flatMap(body => Array.from(body.rows));
    return rows.filter(row => !row.dataset.empty && !(row.cells.length === 1 && row.cells[0].colSpan > 1));
  }
  function data(table) {
    const head = Array.from(table?.tHead?.rows[0]?.cells || []), rows = bodyRows(table);
    const keep = head.map((header, index) => ({header, index, name: label(header, index)}))
      .filter(column => !column.header.hidden && !SKIP.test(column.name) && clean(column.header.textContent));
    return {headers: keep.map(column => column.name), rows: rows.map(row => keep.map(column => cellText(row.cells[column.index])))};
  }
  // Results shown as cards, not a table: each card is one row of a name and its details.
  const CARDS = [
    [".asset-organization-rows > section.panel", "Organizations"],
    [".workspace-event-list > article", "Correlations"],
    [".ops-lookup-items > section.panel", "Scheduled lookups"],
    [".atlas-tech-grid > article", "Technologies"],
    [".atlas-gallery > article", "Screenshots"],
    [".build-list > .build-row", "Workflows"],
    [".expansion-history-row", "Domain expansions"],
    [".discovery-recent-run", "Recent discovery runs"],
  ];
  const TITLE = "h1, h2, h3, h4, h5, strong, .cell-title, [class*=title], [class*=name]";
  function card(item) {
    const copy = item.cloneNode(true);
    copy.querySelectorAll(HIDDEN + ", button, .btn, .row-actions, table, svg, .ls-export, details:not([open]) > :not(summary)").forEach(node => node.remove());
    const heading = copy.querySelector(TITLE), name = clean(heading?.textContent);
    heading?.remove();
    const walker = doc.createTreeWalker(copy, 4), parts = [];
    for (let piece = walker.nextNode(); piece; piece = walker.nextNode()) { const value = clean(piece.data); if (value) parts.push(value); }
    return name ? [name, parts.join(" · ")] : [parts[0] || "", parts.slice(1).join(" · ")];
  }
  const cards = items => ({headers: ["Name", "Details"], rows: items.filter(item => item.isConnected).map(card)});

  // A cell that a spreadsheet would run as a formula is stored as text instead.
  const defuse = value => /^[=+\-@\t\r]/.test(value) ? "'" + value : value;
  const quote = value => /[",\n\r]/.test(value) ? '"' + value.replaceAll('"', '""') + '"' : value;
  function csv(source) {
    return [source.headers, ...source.rows].map(row => row.map(value => quote(defuse(clean(value)))).join(",")).join("\r\n") + "\r\n";
  }
  function text(source) {
    return [source.headers, ...source.rows].map(row => row.map(clean).join("\t")).join("\n") + "\n";
  }
  function slug(value) {
    return clean(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "results";
  }
  function title(table) {
    const words = table.caption?.cloneNode(true);
    words?.querySelectorAll(".ls-table-sort-scope").forEach(node => node.remove());
    const caption = clean(words?.textContent).split(" · ")[0];
    if (caption) return caption;
    // A fold made for a long table is not a heading; look to the panel around it.
    const section = (table.closest(".ls-results-section") || table).parentElement?.closest("section, .panel, details, dialog, [role=dialog]");
    const heading = section?.querySelector("h1, h2, h3, h4, summary, .panel-title");
    return clean(heading?.textContent) || clean(doc.querySelector("#page-root h1")?.textContent) || "results";
  }
  function today() {
    const now = new Date(), two = value => String(value).padStart(2, "0");
    return now.getFullYear() + "-" + two(now.getMonth() + 1) + "-" + two(now.getDate());
  }
  function save(name, content, type, mark = true) {
    const link = doc.createElement("a");
    link.href = root.URL.createObjectURL(new root.Blob([(mark ? "﻿" : "") + content], {type: type + ";charset=utf-8"}));
    link.download = name;
    doc.body.append(link); link.click(); link.remove();
    root.setTimeout(() => root.URL.revokeObjectURL(link.href), 1000);
  }
  // A download the server builds (every matching row, an archive, a report): same-origin paths only.
  function fetchFile(url) {
    if (!/^\/(?!\/)/.test(String(url))) return;
    const link = doc.createElement("a");
    link.href = url; link.download = "";
    doc.body.append(link); link.click(); link.remove();
  }
  // A server download that needs a list sent with it (chosen items): a plain form post, so the browser saves the reply.
  function postFile(url, fields) {
    if (!/^\/(?!\/)/.test(String(url))) return;
    const form = doc.createElement("form"); form.method = "post"; form.action = url; form.hidden = true;
    const token = doc.querySelector("meta[name=csrf-token]")?.content || "";
    for (const [name, value] of Object.entries({csrfmiddlewaretoken: token, ...fields})) { const input = doc.createElement("input"); input.type = "hidden"; input.name = name; input.value = value; form.append(input); }
    doc.body.append(form); form.submit(); form.remove();
  }
  function saveURL(name, url) { const link = doc.createElement("a"); link.href = url; link.download = name; doc.body.append(link); link.click(); link.remove(); }
  function action(label, run, hint) {
    const button = doc.createElement("button");
    button.type = "button"; button.className = "btn btn-small ls-export-button"; button.textContent = label;
    if (hint) button.title = hint;
    button.addEventListener("click", run);
    return button;
  }
  // links(base, [["HTML report", "html"], …]) → a bar of server downloads, one per format.
  function links(base, formats, label) {
    const box = doc.createElement("div"); box.className = "ls-export"; box.dataset.export = "false";
    if (label) { const words = doc.createElement("span"); words.className = "ls-export-label"; words.textContent = label; box.append(words); }
    for (const [word, format, hint] of formats) box.append(action(word, () => fetchFile(base + (base.includes("?") ? "&" : "?") + "format=" + format), hint));
    return box;
  }
  // source() returns {headers, rows}; name() returns the words the file is named after.
  function bar(source, name, remote) {
    const box = doc.createElement("div"); box.className = "ls-export";
    const add = (word, extension, type, build) => {
      const button = doc.createElement("button");
      button.type = "button"; button.className = "btn btn-small ls-export-button"; button.dataset.export = extension;
      button.textContent = "Export " + word;
      button.addEventListener("click", () => {
        const all = remote?.();
        if (all) return fetchFile(all + (all.includes("?") ? "&" : "?") + "format=" + extension);
        const found = source();
        if (!found.rows.length) return;
        save(slug(typeof name === "function" ? name() : name) + "-" + today() + "." + extension, build(found), type);
      });
      box.append(button);
    };
    add("CSV", "csv", "text/csv", csv);
    add("TXT", "txt", "text/plain", text);
    return box;
  }
  // The bar sits above the whole table block, so it is reachable while a long table is folded away.
  const host = table => table.closest(".ls-results-section") || table.closest(".ls-table-browser") || (table.parentElement?.matches(".table-scroll, .ls-table-viewport") ? table.parentElement : table);
  // A page marks a table (or the element it fills with one) whose rows the server can export in full.
  const remoteURL = table => table.dataset.exportUrl || table.closest("[data-export-url]")?.dataset.exportUrl || "";
  function eligible(table) {
    return !!(table.tHead?.rows[0]?.cells.length && table.tBodies[0] && table.dataset.export !== "false" && !table.closest("[data-export=false]"));
  }
  function attach(table) {
    if (!eligible(table) || !table.isConnected) return null;
    const anchor = host(table);
    let box = bars.get(table);
    if (!box || box.nextElementSibling !== anchor) {
      box?.remove();
      box = bar(() => data(table), () => title(table), () => remoteURL(table));
      bars.set(table, box);
      anchor.before(box);
    }
    const count = bodyRows(table).length, hide = count === 0;
    if (box.hidden !== hide) box.hidden = hide;
    const hint = remoteURL(table) ? "Every matching row, not only the page shown" : count.toLocaleString() + (count === 1 ? " row" : " rows") + " loaded in this table";
    if (box.title !== hint) box.title = hint;
    return box;
  }
  function sweep(scope = doc.body) {
    const tables = new Set(scope.querySelectorAll("table"));
    for (const box of scope.querySelectorAll(".ls-export[data-table]")) {
      const next = box.nextElementSibling, owner = next?.matches("table") ? next : next?.querySelector("table");
      if (!owner || bars.get(owner) !== box) box.remove();
    }
    for (const table of tables) { const box = attach(table); if (box && !box.dataset.table) box.dataset.table = "true"; }
    for (const box of scope.querySelectorAll(".ls-export[data-cards]")) if (box.nextElementSibling !== cardBars.get(box)) box.remove();
    for (const [selector, name] of CARDS) {
      const groups = new Map();
      for (const item of scope.querySelectorAll(selector)) { if (!groups.has(item.parentElement)) groups.set(item.parentElement, []); groups.get(item.parentElement).push(item); }
      for (const parent of groups.keys()) {
        if (parent.previousElementSibling?.matches(".ls-export[data-cards]") || parent.closest("[data-export=false]")) continue;
        const box = bar(() => cards(Array.from(parent.querySelectorAll(":scope > *")).filter(item => item.matches(selector))), name);
        box.dataset.cards = "true"; cardBars.set(box, parent); parent.before(box);
      }
    }
  }
  function watch() {
    if (!doc?.body || typeof root.MutationObserver !== "function") return;
    let queued = false;
    const run = () => { queued = false; sweep(); };
    new root.MutationObserver(() => { if (!queued) { queued = true; root.setTimeout(run, 80); } }).observe(doc.body, {childList: true, subtree: true});
    run();
  }
  if (doc) { if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", watch); else watch(); }
  root.LeakScopeExport = {data, cards, csv, text, bar, attach, sweep, slug, title, save, today, fetchFile, postFile, action, links, saveURL};
})(typeof window !== "undefined" ? window : globalThis);
