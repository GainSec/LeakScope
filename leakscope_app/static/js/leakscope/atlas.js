(function () {
  "use strict";
  const API = "/api/atlas";
  let active = null;

  function el(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (["hidden", "disabled", "checked", "selected", "open"].includes(key)) node[key] = !!value;
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  const button = (text, callback, attrs = {}) => el("button", {type: "button", class: "btn btn-small", onclick: callback, ...attrs}, text);
  const input = (name, value = "", attrs = {}) => el("input", {class: "input", name, value, ...attrs});
  const field = (text, control, attrs = {}) => el("label", {class: "field", ...attrs}, el("span", {class: "field-label"}, text), control);
  const select = (name, options, value = "") => {
    const choices = [...options]; if (value && !choices.some(([key]) => String(key) === String(value))) choices.push([value, pretty(value)]);
    return el("select", {class: "select", name}, choices.map(([key, text]) => el("option", {value: key, selected: String(key) === String(value)}, text)));
  };
  const notice = (text, error = false) => el("p", {class: "atlas-notice" + (error ? " atlas-error" : ""), role: error ? "alert" : "status"}, text);
  const pretty = value => String(value || "Unknown").replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase());
  const count = value => value != null && Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "—";
  const date = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "Unknown date";
  const endpointId = row => row?.endpoint_id || row?.reference?.id || row?.id;
  const identityLabel = row => (row.technology_basis === "unverified" ? "Unverified · " : "") + pretty(row.technology);
  const endpointLabel = row => [row.address || row.ip || "Unknown endpoint", row.port].filter(value => value != null && value !== "").join(":");
  const manualTags = value => [...new Set(String(value || "").split(/[,\n]/).map(tag => tag.trim()).filter(Boolean))];
  const Evidence = () => window.LeakScopeEvidence;
  const LEVEL_OPTIONS = [["", "All evidence levels"], ["candidate", "Discovery candidate"], ["provider_reported", "Provider reported"], ["protocol_confirmed", "Live protocol confirmed"], ["signature_matched", "Implementation signature matched"], ["implementation_verified", "Implementation verified"]];
  const CLASS_OPTIONS = [["", "All image types"], ["browser", "Browser screenshots"], ["framebuffer", "Native framebuffer captures"], ["rendered", "Rendered protocol evidence"]];
  const isRendered = item => item?.capture_class === "rendered";
  // Says what the image is before anything else, so a drawn document is never read as a screenshot.
  const imageNoun = item => isRendered(item) ? "evidence image" : item?.capture_class === "framebuffer" ? "framebuffer capture" : "screenshot";
  function localURL(value, fallback = null) {
    if (typeof value !== "string" || /[\x00-\x1f\x7f]/.test(value)) return fallback;
    try {
      const url = new URL(value, location.href);
      if (url.origin !== location.origin || !["http:", "https:"].includes(url.protocol)) return fallback;
      return url.pathname + url.search;
    } catch (_) { return fallback; }
  }
  function safeStyle(style) {
    if (!style || typeof style !== "object" || style.version !== 8) throw Error("The local map style is unavailable.");
    const urls = [];
    if (style.glyphs) urls.push(style.glyphs);
    if (typeof style.sprite === "string") urls.push(style.sprite);
    if (Array.isArray(style.sprite)) urls.push(...style.sprite.map(item => item.url));
    for (const source of Object.values(style.sources || {})) {
      if (source.url) urls.push(source.url);
      if (typeof source.data === "string") urls.push(source.data);
      if (Array.isArray(source.tiles)) urls.push(...source.tiles);
    }
    if (urls.some(url => !localURL(url))) throw Error("A non-local map resource was rejected. The saved-data table remains available.");
    return style;
  }
  async function request(path, payload, signal) {
    const response = await fetch(API + path, {
      method: payload === undefined ? "GET" : "POST", credentials: "same-origin", signal,
      headers: {Accept: "application/json", ...(payload === undefined ? {} : {"Content-Type": "application/json", "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || ""})},
      ...(payload === undefined ? {} : {body: JSON.stringify(payload)})
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || result.detail || "Request failed (" + response.status + ")");
    return result;
  }
  async function perform(control, status, work) {
    control.disabled = true; status.replaceChildren();
    try { await work(); }
    catch (error) { if (error.name !== "AbortError") status.replaceChildren(notice(error.message, true)); }
    finally { control.disabled = false; }
  }
  function makeContext(root, page) {
    active?.dispose();
    const controller = new AbortController(), cleanups = new Set();
    const ctx = {root, page, controller, cleanups, disposed: false, viewer: null, version: 0, queryController: null};
    ctx.dispose = () => {
      if (ctx.disposed) return;
      ctx.disposed = true; ctx.version++; controller.abort(); ctx.queryController?.abort(); ctx.viewer?.close();
      for (const cleanup of cleanups) { try { cleanup(); } catch (_) {} }
      root.classList.remove("atlas-root");
      if (active === ctx) active = null;
    };
    root.classList.add("atlas-root"); active = ctx; return ctx;
  }
  function heading(title, description, eyebrow) {
    return el("header", {class: "atlas-heading"}, el("div", {class: "atlas-eyebrow"}, eyebrow), el("h1", {}, title), el("p", {}, description));
  }
  function filters(ctx, extras = []) {
    const params = new URLSearchParams(location.search), controls = {};
    const create = (name, label, kind = "text", options) => {
      const control = kind === "select" ? select(name, options, params.get(name) || "") : input(name, params.get(name) || "", {type: kind, placeholder: kind === "search" ? "Search retained data" : ""});
      controls[name] = control; return field(label, control, name === "q" ? {class: "field atlas-search-field"} : {});
    };
    const form = el("form", {class: "atlas-filters"},
      create("q", "Search", "search"), create("technology", "Technology"),
      create("provider", "Provider", "select", [["", "All providers"], ["shodan", "Shodan"], ["zoomeye", "ZoomEye"], ["netlas", "Netlas"], ["direct", "Direct captures"]]),
      create("tag", "Tag"));
    const advanced = el("details", {class: "atlas-filter-details"}, el("summary", {}, "More filters")), advancedBody = el("div", {class: "atlas-filter-grid"});
    advancedBody.append(create("organization_id", "Organization", "select", [["", "All organizations"]]), create("country", "Country code"), create("since", "Saved after", "datetime-local"), create("until", "Saved before", "datetime-local"));
    for (const name of ["since", "until"]) {
      const value = params.get(name);
      if (value && Number.isFinite(Date.parse(value))) {
        const stamp = new Date(value); controls[name].value = new Date(stamp - stamp.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
    }
    for (const extra of extras) {
      const control = extra.options ? select(extra.name, extra.options, params.get(extra.name) || extra.value || "") : input(extra.name, params.get(extra.name) || extra.value || "", {type: extra.type || "text"});
      controls[extra.name] = control; (extra.primary ? form : advancedBody).append(field(extra.label, control));
    }
    advanced.append(advancedBody); form.append(advanced);
    const submit = el("button", {type: "submit", class: "btn btn-small atlas-apply"}, "Apply filters"); form.append(submit);
    function query() {
      const next = new URLSearchParams();
      for (const [name, control] of Object.entries(controls)) {
        const value = control.value.trim(); if (!value) continue;
        if (["since", "until"].includes(name)) {
          const stamp = new Date(value); if (!Number.isFinite(stamp.valueOf())) throw Error("Choose a valid saved time.");
          next.set(name, stamp.toISOString());
        } else next.set(name, value);
      }
      return next;
    }
    const organizationControl = controls.organization_id;
    if (organizationControl.value) organizationControl.selectedOptions[0].textContent = "Selected organization";
    (async () => {
      try {
        const response = await fetch("/api/assets/organizations?status=all", {credentials: "same-origin", signal: ctx.controller.signal, headers: {Accept: "application/json"}});
        if (!response.ok) return;
        const data = await response.json(); if (ctx.disposed) return;
        const selected = organizationControl.value, organizations = data.results || [];
        organizationControl.replaceChildren(el("option", {value: ""}, "All organizations"), ...organizations.map(row => el("option", {value: row.id}, row.label || row.value || row.name || "Saved organization")));
        if (selected && !organizations.some(row => String(row.id) === selected)) organizationControl.append(el("option", {value: selected}, "Selected organization"));
        organizationControl.value = selected;
      } catch (error) { if (error.name !== "AbortError") organizationControl.title = "Saved organization names unavailable; the current filter is retained."; }
    })();
    return {form, controls, submit, query};
  }
  function metric(label, value, detail = "") {
    return el("article", {class: "atlas-metric"}, el("span", {}, label), el("strong", {}, count(value)), detail ? el("small", {}, detail) : null);
  }
  function evidenceActions(row, beforeInspect) {
    const id = endpointId(row), actions = el("div", {class: "atlas-actions"});
    if (id && window.LeakScopeInspector?.open) actions.append(button("Inspect endpoint", () => { beforeInspect?.(); window.LeakScopeInspector.open({type: "endpoint", id}); }));
    if (id) actions.append(el("a", {href: "/assets/?endpoint=" + encodeURIComponent(id)}, "Open observation →"));
    return actions;
  }
  function queryLink(path, query, extra = {}) {
    const params = new URLSearchParams(query);
    for (const key of ["category", "cursor", "limit"]) params.delete(key);
    for (const [key, value] of Object.entries(extra)) value ? params.set(key, value) : params.delete(key);
    return path + (params.size ? "?" + params : "");
  }
  function bindFilters(ctx, state, load) {
    state.form.addEventListener("submit", event => {
      event.preventDefault();
      try {
        const query = state.query(); history.replaceState(null, "", location.pathname + (query.size ? "?" + query : ""));
        load(false, true);
      } catch (error) { state.status.replaceChildren(notice(error.message, true)); }
    });
  }

  async function technologies(ctx) {
    const {root} = ctx;
    const families = {database: "Databases", storage: "Data storage", web: "Web / applications", ot: "ICS / SCADA / OT", iot: "IoT / connected devices", messaging: "Messaging / coordination", unknown: "Unknown", other: "Other"};
    const state = filters(ctx, [{name: "category", label: "Technology family", options: [["", "All families"], ...Object.entries(families)]}, {name: "attribution", label: "Evidence level", options: LEVEL_OPTIONS}]);
    const levels = el("section", {class: "ev-levels-panel", "aria-label": "Evidence levels and capture states in this scope", hidden: true});
    const metrics = el("div", {class: "atlas-metrics"}), categories = el("div", {class: "atlas-categories"}), cards = el("div", {class: "atlas-tech-grid"}), status = el("div", {"aria-live": "polite"}), coverage = el("p", {class: "atlas-coverage"}), stars = el("div", {class: "atlas-stars"}); state.status = status;
    root.replaceChildren(heading("Technology atlas", "Explore technologies identified in saved evidence, then open their observations and captures.", "Investigate / Technologies"), metrics, levels, stars, state.form, categories, coverage, status, cards);
    async function load() {
      ctx.queryController?.abort(); ctx.queryController = new AbortController(); const mine = ++ctx.version;
      status.replaceChildren(notice("Loading saved technologies…")); cards.replaceChildren(); metrics.replaceChildren(); categories.replaceChildren(); stars.replaceChildren(); levels.replaceChildren(); levels.hidden = true; coverage.textContent = ""; state.submit.disabled = true;
      try {
        const query = state.query(), data = await request("/technologies?" + query, undefined, ctx.queryController.signal);
        if (ctx.disposed || mine !== ctx.version) return;
        const totals = data.totals || {};
        const categoryLabels = new Map([...Object.entries(families), ...(data.categories || []).map(group => [group.key, group.label])]);
        metrics.replaceChildren(metric("Saved endpoints", totals.endpoints), metric("Technologies", totals.technologies), metric("Screenshots", totals.screenshots), metric("Unclassified", totals.unknown, count(totals.unverified || 0) + " unverified identities"));
        if (Evidence() && totals.levels) {
          // How much of this scope is only a search match, and how much has a live capture.
          levels.replaceChildren(
            Evidence().levelStrip(totals.levels, {title: "Evidence level of saved endpoints", href: level => queryLink(location.pathname, query, {attribution: level})}),
            Evidence().captureStrip(totals.captures || {}, {title: "Live capture state of saved endpoints"}));
          levels.hidden = false;
        }
        categories.replaceChildren(...(data.categories || []).map(group => button(group.label + " · " + count(group.endpoints), () => {
          const select = state.controls.category;
          if (![...select.options].some(option => option.value === group.key)) select.append(el("option", {value: group.key}, group.label));
          select.value = group.key; state.form.requestSubmit();
        }, {class: "atlas-category" + (state.controls.category.value === group.key ? " is-active" : "")})));
        if (window.LeakScopeScope?.constellation && (data.groups || []).length) stars.replaceChildren(window.LeakScopeScope.constellation(data, {href: group => queryLink("/assets/", query, {technology: group.key, type: ""})}));
        coverage.textContent = (data.basis || "Visible retained evidence only.") + (data.truncated ? " · This is a bounded sample; more technologies may exist." : "") + " An endpoint may appear under more than one technology.";
        for (const group of data.groups || []) {
          const quality = group.quality || "provider_reported", preview = group.preview || group.screenshot_preview;
          const galleryScope = {technology: group.key, tag_scope: query.get("tag") ? "endpoint" : ""};
          const card = el("article", {class: "atlas-tech-card"}, el("div", {class: "atlas-card-top"}, el("span", {class: "atlas-pill"}, categoryLabels.get(group.category) || pretty(group.category)), el("span", {class: "atlas-quality " + (quality === "unverified" ? "is-unverified" : "")}, {direct: "Direct fingerprint", provider_reported: "Provider reported", unverified: "Unverified", mixed: "Mixed evidence"}[quality] || pretty(quality))), el("h2", {}, group.label || pretty(group.key)), el("div", {class: "atlas-tech-count"}, el("strong", {}, count(group.endpoints)), " saved endpoints"), el("p", {class: "atlas-small"}, count(group.screenshots || 0) + " retained captures" + (group.images?.rendered ? " · " + count(group.images.rendered) + (group.images.rendered === 1 ? " rendered evidence image" : " rendered evidence images") : "")));
          if (Evidence() && group.levels) {
            card.append(Evidence().levelStrip(group.levels, {href: level => queryLink("/assets/", query, {technology: group.key, type: "", attribution: level})}));
            card.append(Evidence().captureStrip(group.captures || {}));
            // The commonest reason an endpoint in this group has no live capture.
            if (group.capture_note?.reason) card.append(el("p", {class: "ev-capture-note", title: group.capture_note.reason}, count(group.capture_note.endpoints) + " without a live capture: " + group.capture_note.reason));
          }
          if (preview && localURL(preview.thumbnail_url)) card.append(el("a", {href: queryLink("/screenshots/", query, {...galleryScope, capture_class: preview.capture_class === "rendered" ? "rendered" : ""}), class: "atlas-tech-preview"},
            Evidence()?.classBadge(preview.capture_class) ? el("span", {class: "ev-class-overlay"}, Evidence().classBadge(preview.capture_class)) : null,
            el("img", {src: localURL(preview.thumbnail_url), loading: "lazy", decoding: "async", alt: preview.capture_class === "rendered" ? "Rendered protocol evidence for " + (group.label || group.key) + "; not a screenshot" : preview.title || "Saved technology screenshot"})));
          if (quality === "unverified") card.append(el("p", {class: "atlas-small"}, "The requested search profile has not confirmed this identity."));
          card.append(el("div", {class: "atlas-actions"}, el("a", {href: queryLink("/screenshots/", query, galleryScope)}, "Open gallery →"), el("a", {href: queryLink("/assets/", query, {technology: group.key, type: ""})}, "View observations →"))); cards.append(card);
        }
        status.replaceChildren(); if (!(data.groups || []).length) cards.append(notice("No retained technology evidence matches these filters."));
      } catch (error) { if (error.name !== "AbortError" && mine === ctx.version && !ctx.disposed) status.replaceChildren(notice(error.message, true), button("Retry saved technologies", load)); }
      finally { if (mine === ctx.version) state.submit.disabled = false; }
    }
    bindFilters(ctx, state, load); await load();
  }

  function fullImage(ctx, options) {
    ctx.viewer?.close();
    const trigger = document.activeElement, previousOverflow = document.body.style.overflow;
    const overlay = el("div", {class: "atlas-viewer-backdrop"}), dialog = el("section", {class: "atlas-image-viewer", role: "dialog", "aria-modal": "true", "aria-label": options.label || "Saved screenshot viewer", tabindex: "-1"});
    const title = el("strong", {class: "atlas-viewer-title"}), stage = el("div", {class: "atlas-image-stage", tabindex: "0", "aria-label": "Image. Zoom to pan."}), image = el("img", {class: "atlas-full-image", alt: ""}), mediaStatus = el("div", {class: "atlas-media-status", "aria-live": "polite"}), info = el("aside", {class: "atlas-image-info"}), status = el("div", {class: "atlas-viewer-status", "aria-live": "polite"});
    let current = options.start || 0, scale = 1, x = 0, y = 0, drag = null, closed = false, panorama = null;
    const getItems = () => options.items() || [], getItem = () => getItems()[current];
    function transform() { image.style.transform = "translate(" + x + "px," + y + "px) scale(" + scale + ")"; }
    function fit() { scale = 1; x = y = 0; image.classList.remove("is-original-size"); image.style.width = ""; transform(); }
    function destroyPanorama() { const previous = panorama; panorama = null; try { previous?.destroy?.(); } catch (_) {} }
    function close() {
      if (closed) return; closed = true; destroyPanorama(); document.removeEventListener("keydown", keydown); overlay.remove(); document.body.style.overflow = previousOverflow;
      if (ctx.viewer?.close === close) ctx.viewer = null;
      if (trigger?.isConnected) trigger.focus(); else ctx.root.querySelector("button,input,select,a")?.focus();
    }
    const previous = button("Previous screenshot", () => show((current - 1 + getItems().length) % getItems().length), {"aria-label": "Previous screenshot"});
    const next = button("Next screenshot", () => show((current + 1) % getItems().length), {"aria-label": "Next screenshot"});
    const zoomOut = button("Zoom out", () => { scale = Math.max(0.25, scale / 1.25); transform(); }, {class: "btn btn-small atlas-native-image-control"});
    const zoomIn = button("Zoom in", () => { scale = Math.min(8, scale * 1.25); transform(); }, {class: "btn btn-small atlas-native-image-control"});
    const originalSize = button("1:1", () => { fit(); image.classList.add("is-original-size"); image.style.width = (image.naturalWidth || getItem()?.width || 1200) + "px"; }, {class: "btn btn-small atlas-native-image-control"});
    const download = el("a", {class: "btn btn-small", target: "_blank", rel: "noopener noreferrer"}, "Open original ↗");
    const closeButton = button("Close viewer", close, {class: "btn btn-small atlas-viewer-close"});
    dialog.append(el("header", {class: "atlas-viewer-header"}, title, closeButton), el("div", {class: "atlas-viewer-toolbar"}, previous, next, button("Fit image", fit, {class: "btn btn-small atlas-native-image-control"}), originalSize, zoomOut, zoomIn, download), el("div", {class: "atlas-viewer-body"}, el("div", {class: "atlas-image-column"}, stage, mediaStatus), info), status);
    stage.append(image); overlay.append(dialog); document.body.append(overlay); document.body.style.overflow = "hidden";
    function keydown(event) {
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key === "Tab") {
        const focusable = [...dialog.querySelectorAll('button:not([disabled]),input,select,a[href],[tabindex="0"]')].filter(node => !node.hidden);
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
      if (event.key === "ArrowRight") { event.preventDefault(); next.click(); }
      if (event.key === "ArrowLeft") { event.preventDefault(); previous.click(); }
    }
    document.addEventListener("keydown", keydown);
    image.addEventListener("load", () => { if (!panorama) image.hidden = false; mediaStatus.replaceChildren(); });
    image.addEventListener("error", () => { image.hidden = true; mediaStatus.replaceChildren(notice("The original image is unavailable or has expired. Saved metadata remains available.", true)); });
    stage.addEventListener("pointerdown", event => { if (panorama) return; if (event.button != null && event.button !== 0) return; drag = {id: event.pointerId, startX: event.clientX - x, startY: event.clientY - y}; stage.setPointerCapture?.(event.pointerId); });
    stage.addEventListener("pointermove", event => { if (!drag || drag.id !== event.pointerId) return; x = event.clientX - drag.startX; y = event.clientY - drag.startY; transform(); });
    const stopDrag = () => { drag = null; }; stage.addEventListener("pointerup", stopDrag); stage.addEventListener("pointercancel", stopDrag);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    function show(index) {
      destroyPanorama(); stage.querySelectorAll(".atlas-panorama").forEach(node => node.remove());
      const items = getItems(); if (!items.length) return close(); current = Math.max(0, Math.min(index, items.length - 1)); const item = items[current]; fit();
      title.textContent = options.imagery ? item.title || "Street imagery near estimated location" : isRendered(item) ? "Rendered protocol evidence · " + endpointLabel(item) : item.title || endpointLabel(item);
      const src = localURL(options.imagery ? item.image_url : item.original_url, options.imagery ? null : "/api/operations/artifacts/" + encodeURIComponent(item.id));
      mediaStatus.replaceChildren(notice("Loading saved image…")); image.hidden = false; image.alt = options.imagery ? item.title || "Street photograph near an approximate IP location" : isRendered(item) ? (item.evidence?.description || "Rendered protocol evidence for " + endpointLabel(item) + "; not a screenshot") : item.title || "Saved screenshot of " + endpointLabel(item);
      if (src) { image.src = src; download.href = src; download.hidden = false; } else { image.removeAttribute("src"); image.hidden = true; download.hidden = true; mediaStatus.replaceChildren(notice("No locally served original is available.", true)); }
      previous.disabled = next.disabled = items.length < 2; previous.hidden = next.hidden = !!options.imagery && items.length < 2;
      info.replaceChildren(); status.replaceChildren();
      if (options.imagery) {
        info.append(el("h2", {}, "Approximate vicinity"), el("p", {}, "Street imagery is near an estimated IP location. It does not identify the device, organization or facility."), el("dl", {class: "atlas-facts"}, el("dt", {}, "Photographed"), el("dd", {}, date(item.captured_at)), el("dt", {}, "Distance from estimate"), el("dd", {}, Number.isFinite(item.distance_m) ? count(item.distance_m) + " m" : "Unknown"), el("dt", {}, "Source"), el("dd", {}, "Panoramax")));
        if (item.is_panorama && src && window.PhotoSphereViewer?.Viewer) {
          const host = el("div", {class: "atlas-panorama"}); stage.append(host); image.hidden = true;
          try { panorama = new window.PhotoSphereViewer.Viewer({container: host, panorama: src, navbar: true, loadingTxt: "Loading local panorama…"}); mediaStatus.replaceChildren(); }
          catch (_) { host.remove(); image.hidden = false; info.append(notice("Interactive panorama unavailable; displaying the original panorama image.")); }
        } else if (item.is_panorama) info.append(notice("Panorama original. Interactive 360° rendering is unavailable in this browser."));
      } else options.info?.(item, info, status, show);
    }
    ctx.viewer = {close, show, getItem}; show(current); closeButton.focus(); return ctx.viewer;
  }

  async function screenshots(ctx) {
    const {root} = ctx;
    const state = filters(ctx, [
      {name: "collection", label: "Collection", options: [["", "All collections"]], primary: true},
      {name: "capture_class", label: "Image type", options: CLASS_OPTIONS, primary: true},
      {name: "attribution", label: "Evidence level", options: LEVEL_OPTIONS},
      {name: "tag_scope", label: "Tag matches", options: [["any", "Capture or endpoint tags"], ["endpoint", "Endpoint tags only"]]},
      {name: "group", label: "Group sections", value: "none", options: [["none", "No grouping"], ["technology", "Technology"], ["organization", "Organization"], ["country", "Country"]]},
      {name: "latest", label: "Capture history", options: [["", "All retained captures"], ["1", "Latest per endpoint"]]},
      {name: "duplicates", label: "Exact duplicates", value: "all", options: [["all", "Show every capture"], ["collapse", "Collapse identical files"]]},
      {name: "sort", label: "Sort by", value: "created_at", options: [["created_at", "Saved date"], ["endpoint", "Endpoint"], ["technology", "Stored service type"], ["size", "File size"]]},
      {name: "direction", label: "Sort direction", value: "desc", options: [["desc", "Descending"], ["asc", "Ascending"]]}
    ]);
    const metrics = el("div", {class: "atlas-metrics"}), status = el("div", {"aria-live": "polite"}), coverage = el("p", {class: "atlas-coverage"}), cards = el("div", {class: "atlas-gallery"}); state.status = status;
    let rows = [], cursor = null, activeQuery = null, collections = [], grouping = new Map(); const selected = new Set();
    const collectionStatus = el("div", {class: "atlas-collection-status", "aria-live": "polite"}), collectionName = input("collection_name", "", {maxlength: 120, placeholder: "Collection name"}), collectionColor = input("collection_color", "#77aaff", {type: "color", "aria-label": "Collection color"}), manageCollection = select("manage_collection", [["", "Choose collection"]]);
    const bulkCollection = select("bulk_collection", [["", "Choose collection"]]), bulkTags = input("bulk_tags", "", {placeholder: "Comma-separated manual tags"}), bulkCount = el("strong", {}, "0 selected"), bulkStatus = el("div", {"aria-live": "polite"});
    const toolbar = el("section", {class: "atlas-bulk", "aria-label": "Selected screenshot actions"}, bulkCount);
    const all = el("input", {type: "checkbox", "aria-label": "Select loaded screenshots"});
    function updateSelection() { bulkCount.textContent = count(selected.size) + " selected"; { const zip = archiveBar.querySelector("button"); if (zip) zip.textContent = selected.size ? "Export ZIP · " + count(selected.size) + " selected" : "Export ZIP"; } toolbar.classList.toggle("has-selection", !!selected.size); for (const control of toolbar.querySelectorAll("button")) control.disabled = !selected.size; all.checked = !!rows.length && rows.every(row => selected.has(row.id)); }
    all.addEventListener("change", () => { if (all.checked) rows.slice(0, 500).forEach(row => selected.add(row.id)); else rows.forEach(row => selected.delete(row.id)); for (const box of cards.querySelectorAll(".atlas-shot-select")) box.checked = selected.has(box.value); updateSelection(); });
    const selectionBar = el("label", {class: "atlas-select-loaded"}, all, "Select loaded screenshots");
    async function curate(ids, patch, targetStatus = bulkStatus) {
      if (!ids.length) throw Error("Choose at least one screenshot.");
      if (ids.length > 500) throw Error("Choose at most 500 screenshots for one change.");
      await request("/screenshots/curate", {artifact_ids: ids, ...patch}, ctx.controller.signal);
      await load(false);
      targetStatus.replaceChildren(notice("Saved screenshot organization. Original files remain unchanged."));
      return rows.find(row => row.id === ids[0]);
    }
    const bulkAction = (label, patch) => { let control; control = button(label, () => perform(control, bulkStatus, () => curate([...selected], typeof patch === "function" ? patch() : patch))); return control; };
    toolbar.append(bulkAction("Keep selected", {pinned: true}), bulkAction("Remove Keep", {pinned: false}), field("Collection", bulkCollection), bulkAction("Add to collection", () => { if (!bulkCollection.value) throw Error("Choose a collection."); return {add_collections: [bulkCollection.value]}; }), bulkAction("Remove from collection", () => { if (!bulkCollection.value) throw Error("Choose a collection."); return {remove_collections: [bulkCollection.value]}; }), field("Manual tags", bulkTags), bulkAction("Replace manual tags", () => ({tags: manualTags(bulkTags.value)})), bulkStatus);
    let createCollection, renameCollection, deleteCollection;
    createCollection = button("Create collection", () => perform(createCollection, collectionStatus, async () => { if (!collectionName.value.trim()) throw Error("Enter a collection name."); const data = await request("/collections", {name: collectionName.value.trim(), color: collectionColor.value}, ctx.controller.signal); await loadCollections(data.collection?.id); collectionStatus.replaceChildren(notice("Created collection. Add screenshots using the selection controls.")); }));
    renameCollection = button("Rename collection", () => perform(renameCollection, collectionStatus, async () => { if (!manageCollection.value || !collectionName.value.trim()) throw Error("Choose a collection and enter its name."); await request("/collections", {id: manageCollection.value, name: collectionName.value.trim(), color: collectionColor.value}, ctx.controller.signal); await loadCollections(manageCollection.value); await load(false); collectionStatus.replaceChildren(notice("Updated collection.")); }));
    deleteCollection = button("Delete collection", () => perform(deleteCollection, collectionStatus, async () => {
      const id = manageCollection.value; if (!id) throw Error("Choose a collection.");
      if (deleteCollection.dataset.confirm !== id) { deleteCollection.dataset.confirm = id; deleteCollection.textContent = "Confirm delete collection"; collectionStatus.replaceChildren(notice("Delete only this collection. Screenshots remain stored. Click Confirm delete collection to continue.")); return; }
      await request("/collections/" + encodeURIComponent(id) + "/delete", {}, ctx.controller.signal); deleteCollection.textContent = "Delete collection"; delete deleteCollection.dataset.confirm;
      if (state.controls.collection.value === id) state.controls.collection.value = "";
      await loadCollections(); await load(false); collectionStatus.replaceChildren(notice("Deleted collection; screenshots remain stored."));
    }));
    manageCollection.addEventListener("change", () => { const row = collections.find(item => item.id === manageCollection.value); if (row) { collectionName.value = row.name; collectionColor.value = row.color || "#77aaff"; } deleteCollection.textContent = "Delete collection"; delete deleteCollection.dataset.confirm; });
    const collectionTools = el("details", {class: "atlas-toolbox"}, el("summary", {}, "Manage manual collections"), el("p", {class: "atlas-small"}, "Collections organize captures without copying their files. Collection members and kept screenshots are protected from routine cleanup."), el("div", {class: "atlas-tool-row"}, field("Existing collection", manageCollection), field("Collection name", collectionName), field("Color", collectionColor), createCollection, renameCollection, deleteCollection), collectionStatus);
    const thumbnailStatus = el("div", {"aria-live": "polite"}), thumbnailLimit = input("thumbnail_limit", 50, {type: "number", min: 1, max: 1000}), thumbnailProgress = el("div");
    let generate; generate = button("Generate thumbnails", () => perform(generate, thumbnailStatus, async () => {
      const limit = Number(thumbnailLimit.value); if (!Number.isInteger(limit) || limit < 1 || limit > 1000) throw Error("Choose between 1 and 1,000 saved screenshots.");
      const data = await request("/screenshots/index", {limit, ...(selected.size ? {artifact_ids: [...selected]} : {})}, ctx.controller.signal);
      if (!data.run?.id) throw Error("The local thumbnail run was not returned.");
      thumbnailStatus.append(notice("Local thumbnail generation queued. No target requests or provider searches are started."), el("a", {href: "/runs/?id=" + encodeURIComponent(data.run.id)}, "Open thumbnail Run →"));
      if (window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(data.run, thumbnailProgress, run => { if (!ctx.disposed && ["succeeded", "success"].includes(run.state)) load(false); }, {signal: ctx.controller.signal});
      else if (window.LeakScopeRunLogs?.panel) thumbnailProgress.append(window.LeakScopeRunLogs.panel(data.run, {key: "atlas-thumbnails"}));
    }));
    const thumbnailTools = el("details", {class: "atlas-toolbox"}, el("summary", {}, "Prepare saved thumbnails"), el("p", {class: "atlas-small"}, "Explicit local work creates small previews from existing files. Originals are preserved. Select captures first to limit the job to those files."), el("div", {class: "atlas-tool-row"}, field("Maximum saved screenshots", thumbnailLimit), generate), thumbnailStatus, thumbnailProgress);
    const renderStatus = el("div", {"aria-live": "polite"}), renderLimit = input("render_limit", 200, {type: "number", min: 1, max: 5000}), renderProgress = el("div");
    let draw; draw = button("Draw evidence images", () => perform(draw, renderStatus, async () => {
      const limit = Number(renderLimit.value); if (!Number.isInteger(limit) || limit < 1 || limit > 5000) throw Error("Choose between 1 and 5,000 saved endpoints.");
      const data = await request("/evidence/render", {limit}, ctx.controller.signal);
      if (!data.run?.id) throw Error("The local drawing run was not returned.");
      renderStatus.append(notice("Local drawing queued. It reads saved banners and protocol exchanges only; no target requests or provider searches are started."), el("a", {href: "/runs/?id=" + encodeURIComponent(data.run.id)}, "Open drawing Run →"));
      if (window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(data.run, renderProgress, run => { if (!ctx.disposed && ["succeeded", "success"].includes(run.state)) load(false); }, {signal: ctx.controller.signal});
    }));
    const renderTools = el("details", {class: "atlas-toolbox"}, el("summary", {}, "Draw evidence images from saved responses"), el("p", {class: "atlas-small"}, "Non-web services have no page to screenshot. This draws each saved banner or protocol exchange once as a labelled image, keeps the raw bytes beside it, and never contacts an endpoint."), el("div", {class: "atlas-tool-row"}, field("Maximum saved endpoints", renderLimit), draw), renderStatus, renderProgress);
    const more = button("Load more screenshots", () => load(true), {hidden: true});
    // The images themselves, not only their fields: every match of the current filters, up to 500 per archive.
    const archiveBar = el("div", {class: "ls-export", "data-export": "false", hidden: true});
    if (window.LeakScopeExport?.action) archiveBar.append(el("span", {class: "ls-export-label"}, "Images and details together"), window.LeakScopeExport.action("Export ZIP", () => {
      const filters = new URLSearchParams(activeQuery || ""); filters.delete("limit"); filters.delete("cursor");
      if (selected.size) window.LeakScopeExport.postFile("/export/screenshots/", {...Object.fromEntries(filters), ids: [...selected].join(",")});
      else window.LeakScopeExport.fetchFile("/export/screenshots/?" + filters);
    }, "A ZIP of the selected images, or of every image matching these filters when none is selected (up to 500), with a CSV of their details and an HTML page showing both"));
    // One-click switch between the three image classes, with how many of each match.
    const typeBar = el("div", {class: "ev-type-bar", role: "group", "aria-label": "Image type", hidden: true});
    function drawTypes(counts) {
      if (!counts || !Evidence()) return;
      const current = state.controls.capture_class.value, total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
      const choose = value => { state.controls.capture_class.value = value; state.form.requestSubmit(); };
      typeBar.replaceChildren(el("button", {type: "button", class: "ev-type", "aria-pressed": String(!current), onclick: () => choose("")}, "All images", el("strong", {}, count(total))),
        ...Object.keys(Evidence().CLASSES).map(name => el("button", {type: "button", class: "ev-type", "aria-pressed": String(current === name), disabled: !counts[name] && current !== name, onclick: () => choose(name)}, Evidence().classBadge(name), el("strong", {}, count(counts[name])))));
      typeBar.hidden = false;
    }
    root.replaceChildren(heading("Screenshot library", "Review retained captures, organize evidence and open originals at full size.", "Investigate / Screenshots"), state.form, metrics, el("div", {class: "atlas-toolboxes"}, collectionTools, thumbnailTools, renderTools), coverage, typeBar, selectionBar, toolbar, status, archiveBar, cards, more);
    updateSelection();
    async function loadCollections(selectedId = manageCollection.value) {
      try {
        const data = await request("/collections", undefined, ctx.controller.signal); if (ctx.disposed) return;
        collections = data.results || [];
        for (const [control, placeholder] of [[state.controls.collection, "All collections"], [bulkCollection, "Choose collection"], [manageCollection, "Choose collection"]]) {
          const current = control === manageCollection ? selectedId : control.value || (control === state.controls.collection ? new URLSearchParams(location.search).get("collection") : "");
          control.replaceChildren(el("option", {value: ""}, placeholder), ...collections.map(row => el("option", {value: row.id}, row.name + (control === state.controls.collection ? " · " + count(row.count || 0) : "")))); control.value = current || "";
        }
      } catch (error) { if (error.name !== "AbortError" && !ctx.disposed) collectionStatus.replaceChildren(notice("Collections unavailable. " + error.message, true)); }
    }
    function captureInfo(item, info, viewerStatus) {
      const facts = el("dl", {class: "atlas-facts"}, el("dt", {}, "Endpoint"), el("dd", {}, endpointLabel(item)), el("dt", {}, "Technology"), el("dd", {}, identityLabel(item)), el("dt", {}, "Provider"), el("dd", {}, pretty(item.provider)), el("dt", {}, "Saved"), el("dd", {}, date(item.created_at)), el("dt", {}, "Original size"), el("dd", {}, item.width && item.height ? item.width + " × " + item.height : "Available when the image loads"));
      const tags = input("capture_tags", (item.manual_tags || []).join(", "), {placeholder: "Comma-separated screenshot tags"});
      const protectionNote = el("p", {class: "atlas-small"}), membershipNote = el("p", {class: "atlas-small"});
      function updateNotes() {
        protectionNote.textContent = (item.collections || []).length ? "Protected by manual collection membership." : item.pinned ? "Protected from routine capture cleanup." : "Use Keep or a manual collection to retain this capture.";
        membershipNote.textContent = "Current collections: " + ((item.collections || []).map(row => row.name).join(", ") || "None");
      }
      updateNotes();
      let save, keep, add, remove;
      save = button("Save screenshot tags", () => perform(save, viewerStatus, async () => { const updated = await curate([item.id], {tags: manualTags(tags.value)}, viewerStatus); item.manual_tags = updated?.manual_tags || manualTags(tags.value); tags.value = item.manual_tags.join(", "); }));
      keep = button(item.pinned ? "Remove Keep" : "Keep screenshot", () => perform(keep, viewerStatus, async () => { const next = !item.pinned; await curate([item.id], {pinned: next}, viewerStatus); item.pinned = next; keep.textContent = item.pinned ? "Remove Keep" : "Keep screenshot"; updateNotes(); }));
      const collection = select("capture_collection", [["", "Choose collection"], ...collections.map(row => [row.id, row.name])]);
      add = button("Add screenshot to collection", () => perform(add, viewerStatus, async () => { if (!collection.value) throw Error("Choose a collection."); const updated = await curate([item.id], {add_collections: [collection.value]}, viewerStatus); item.collections = updated?.collections || [...(item.collections || []), collections.find(row => row.id === collection.value)].filter(Boolean); updateNotes(); await loadCollections(); }));
      remove = button("Remove screenshot from collection", () => perform(remove, viewerStatus, async () => { if (!collection.value) throw Error("Choose a collection."); const updated = await curate([item.id], {remove_collections: [collection.value]}, viewerStatus); item.collections = updated?.collections || (item.collections || []).filter(row => row.id !== collection.value); updateNotes(); await loadCollections(); }));
      info.append(el("h2", {}, item.title || "Saved capture"), imageFacts(item), facts, evidenceActions(item, () => ctx.viewer?.close()), el("section", {class: "atlas-capture-tags"}, field("Screenshot tags", tags), save, el("p", {class: "atlas-small"}, "Inherited endpoint tags: " + ((item.inherited_tags || []).join(", ") || "None") + ". Editing screenshot tags changes this capture only.")), el("section", {class: "atlas-capture-organize"}, el("h3", {}, "Keep and organize"), keep, protectionNote, field("Collection", collection), el("div", {class: "atlas-capture-membership-actions"}, add, remove), membershipNote), el("details", {class: "atlas-image-metadata"}, el("summary", {}, "File details"), el("p", {class: "atlas-small"}, (item.mime || "Unknown format") + " · " + count(item.size) + " bytes"), el("code", {}, item.sha256 || "Unknown hash")));
    }
    // What kind of image this is, how it was made, and where its raw bytes are.
    function imageFacts(item) {
      const box = el("section", {class: "ev-evidence-facts"});
      if (!Evidence() || !item.capture_class) return box;
      box.append(Evidence().classBadge(item.capture_class, item.capture_label), el("p", {class: "atlas-small"}, item.capture_note || ""));
      if (isRendered(item) && item.evidence) {
        const proof = item.evidence, facts = el("dl", {class: "atlas-facts"});
        const add = (label, value) => { if (value) facts.append(el("dt", {}, label), el("dd", {}, value)); };
        add("Shows", proof.headline); add("Outcome", proof.state_label); add("Operation", proof.operation);
        add("Seen by source", proof.source_observed_at ? date(proof.source_observed_at) : ""); add("Evidence saved", proof.source_collected_at ? date(proof.source_collected_at) : ""); add("Image drawn", date(proof.rendered_at || item.created_at));
        box.append(facts);
        if ((proof.raw_files || []).length) box.append(el("p", {class: "atlas-small"}, "Raw files saved with this exchange:"), el("ul", {class: "ev-raw-files"}, proof.raw_files.map(file => el("li", {}, el("a", {class: "btn btn-small", href: localURL(file.url) || "#", target: "_blank", rel: "noopener noreferrer"}, pretty(file.kind.replaceAll("-", " ")) + " · " + count(file.size) + " bytes")))));
        else box.append(el("p", {class: "atlas-small"}, "The source record with the full response text is under Inspect endpoint → Evidence."));
      }
      if (item.capture && (isRendered(item) || item.partial)) box.append(el("p", {class: "ev-shot-status"}, Evidence().stateChip(item.capture.screenshot_state, "Screenshot: " + (Evidence().STATES.find(([key]) => key === item.capture.screenshot_state)?.[1] || "unknown").toLowerCase()), item.capture.channel === "screenshot" || !isRendered(item) ? item.capture.reason : ""));
      return box;
    }
    function card(item) {
      const selectBox = el("input", {class: "atlas-shot-select", type: "checkbox", value: item.id, checked: selected.has(item.id), "aria-label": "Select " + (item.title || endpointLabel(item))});
      selectBox.addEventListener("change", () => { if (selectBox.checked && selected.size >= 500) { selectBox.checked = false; bulkStatus.replaceChildren(notice("At most 500 screenshots can be selected for one change.", true)); return; } selectBox.checked ? selected.add(item.id) : selected.delete(item.id); updateSelection(); });
      const preview = button("", () => fullImage(ctx, {items: () => rows, start: Math.max(0, rows.findIndex(row => row.id === item.id)), info: captureInfo}), {class: "atlas-shot-open", "aria-label": "Open " + imageNoun(item) + ": " + (item.title || endpointLabel(item))});
      const badge = Evidence()?.classBadge(item.capture_class, item.capture_label); if (badge) preview.append(el("span", {class: "ev-class-overlay"}, badge));
      const thumbnail = localURL(item.thumbnail_url);
      if (thumbnail) {
        const image = el("img", {src: thumbnail, alt: (isRendered(item) ? "Rendered protocol evidence for " : "Saved preview of ") + endpointLabel(item), loading: "lazy", decoding: "async"}); image.addEventListener("error", () => preview.replaceChildren(el("span", {class: "atlas-preview-placeholder"}, "Preview unavailable"))); preview.append(image);
      } else preview.append(el("span", {class: "atlas-preview-placeholder"}, "No saved thumbnail"));
      const labels = el("div", {class: "atlas-shot-labels"}, ...(item.manual_tags || []).slice(0, 8).map(tag => el("span", {class: "atlas-tag"}, tag)), ...(item.collections || []).slice(0, 3).map(row => { const badge = el("span", {class: "atlas-pill"}, row.name); if (/^#[0-9a-f]{6}$/i.test(row.color || "")) badge.style.borderColor = row.color; return badge; }));
      const node = el("article", {class: "atlas-shot-card", "data-artifact-id": item.id, "data-capture-class": item.capture_class || null}, el("div", {class: "atlas-shot-controls"}, el("label", {}, selectBox, el("span", {class: "atlas-sr-only"}, "Select screenshot")), item.pinned ? el("span", {class: "atlas-keep"}, "Kept") : null), preview, el("div", {class: "atlas-shot-caption"}, el("div", {class: "atlas-card-top"}, el("span", {class: "atlas-pill"}, identityLabel(item)), el("span", {class: "atlas-small"}, pretty(item.provider))), el("h3", {}, item.title || endpointLabel(item)), el("p", {class: "atlas-small atlas-endpoint"}, endpointLabel(item)), el("p", {class: "atlas-small atlas-shot-time"}, isRendered(item) && item.evidence?.source_collected_at ? "Evidence saved " + date(item.evidence.source_collected_at) + " · drawn " + date(item.evidence.rendered_at || item.created_at) : "Saved " + date(item.created_at)),
        isRendered(item) && item.evidence?.state_label ? el("p", {class: "ev-shot-status"}, item.evidence.state_label) : item.partial && Evidence() ? el("p", {class: "ev-shot-status"}, Evidence().stateChip("partial", "Partial capture")) : null, labels));
      return node;
    }
    async function load(append = false, resetSelection = false) {
      let query;
      try { query = append ? new URLSearchParams(activeQuery) : state.query(); }
      catch (error) { status.replaceChildren(notice(error.message, true)); return; }
      if (append && cursor) query.set("cursor", cursor); query.set("limit", "48");
      ctx.queryController?.abort(); ctx.queryController = new AbortController(); const mine = ++ctx.version;
      if (!append) { rows = []; grouping = new Map(); cards.replaceChildren(); metrics.replaceChildren(); coverage.textContent = ""; cursor = null; activeQuery = query.toString(); }
      if (resetSelection) selected.clear(); updateSelection();
      status.replaceChildren(notice(append ? "Loading more saved screenshots…" : "Loading saved screenshots…")); more.disabled = state.submit.disabled = true;
      try {
        const data = await request("/screenshots?" + query, undefined, ctx.queryController.signal); if (ctx.disposed || mine !== ctx.version) return;
        const incoming = (data.results || []).filter(item => !rows.some(row => row.id === item.id)); rows.push(...incoming); cursor = data.next_cursor || null; more.hidden = !data.has_more; archiveBar.hidden = !rows.length || !archiveBar.childElementCount;
        metrics.replaceChildren(metric("Matching captures", data.count), metric("Retained visible captures", data.total_visible_count ?? data.count), metric("Loaded here", rows.length), metric("Selected", selected.size, "Actions affect captures only"));
        coverage.textContent = (data.basis || "Visible retained screenshot files only.") + " Original availability is checked when opened.";
        if (!append) drawTypes(data.class_counts);
        if (!(data.results || []).length && !append) cards.append(notice("No retained screenshots match these filters."));
        for (const item of incoming) {
          let target = cards;
          if (state.controls.group.value !== "none") {
            const key = item.group?.key || "unknown";
            if (!grouping.has(key)) { const grid = el("div", {class: "atlas-shot-grid"}); cards.append(el("section", {class: "atlas-gallery-section"}, el("h2", {}, item.group?.label || "Unknown group"), grid)); grouping.set(key, grid); } target = grouping.get(key);
          }
          target.append(card(item));
        }
        status.replaceChildren(); updateSelection();
      } catch (error) { if (error.name !== "AbortError" && mine === ctx.version && !ctx.disposed) status.replaceChildren(notice(error.message, true), button("Retry saved screenshots", () => load(append))); }
      finally { if (mine === ctx.version) more.disabled = state.submit.disabled = false; }
    }
    bindFilters(ctx, state, load); await Promise.all([loadCollections(), load(false)]);
  }

  async function geography(ctx) {
    const {root} = ctx;
    const state = filters(ctx, [
      {name: "category", label: "Technology family", primary: true, options: [["", "All families"]]},
      {name: "source", label: "Location source", value: "local", primary: true, options: [["local", "Local IP database"], ["shodan", "Shodan estimate"], ["zoomeye", "ZoomEye estimate"], ["netlas", "Netlas estimate"]]},
      {name: "count_mode", label: "Count", value: "ips", primary: true, options: [["ips", "Unique IPs"], ["endpoints", "Service endpoints"]]},
      {name: "continent", label: "Continent", options: [["", "All continents"], ["NA", "North America"], ["SA", "South America"], ["EU", "Europe"], ["AF", "Africa"], ["AS", "Asia"], ["OC", "Oceania"], ["AN", "Antarctica"], ["unknown", "Unknown continent"]]}
    ]);
    const family = state.controls.category, technology = state.controls.technology;
    const suggestions = el("datalist", {id: "geography-technology-options"});
    technology.setAttribute("list", suggestions.id);
    technology.placeholder = "Search catalog or type a key";
    state.form.append(suggestions);
    let filterChoices;
    function updateTechnologySuggestions() {
      const choices = filterChoices?.technologies || [];
      suggestions.replaceChildren(...choices
        .filter(row => !family.value || row.category === family.value)
        .map(row => el("option", {value: row.key, label: row.label})));
    }
    family.addEventListener("change", updateTechnologySuggestions);
    function setFilterChoices(choices) {
      if (!choices || filterChoices) return;
      filterChoices = choices;
      const selected = family.value;
      const options = [["", "All families"], ...choices.families.map(row => [row.key, row.label])];
      family.replaceChildren(...select("category", options, selected).options);
      family.value = selected;
      updateTechnologySuggestions();
    }
    const metrics = el("div", {class: "atlas-metrics"}), status = el("div", {"aria-live": "polite"}), coverage = el("p", {class: "atlas-coverage"}), regions = el("div", {class: "atlas-region-grid"}), countries = el("div", {class: "atlas-country-grid"}), mapHost = el("div", {class: "atlas-map", role: "region", "aria-label": "Local geography map"}), mapNotice = el("div", {class: "atlas-map-notice", "aria-live": "polite"}), tableHost = el("div", {class: "atlas-geo-table"}), detail = el("section", {class: "atlas-geo-detail", hidden: true, "aria-live": "polite"}), dataPanel = el("details", {class: "atlas-toolbox"}, el("summary", {}, "Map and geolocation data coverage")), dataBody = el("div"); dataPanel.append(dataBody); state.status = status;
    const resultCount = el("span", {class: "ls-results-count"}, "Loading rows…"), resultSection = el("details", {class: "ls-results-section atlas-results-section", "data-workspace-disclosure": "geography-results"}, el("summary", {}, el("strong", {}, "Saved results and location coverage"), resultCount), tableHost);
    const resultState = {query: "", filters: {}, sort: {key: null, direction: "asc"}, pageSize: 50, page: 1};
    let map = null, mapReady = false, features = [], latestData = null, detailController = null, detailVersion = 0, detailTrigger = null, mapInfo = null, projection = new URLSearchParams(location.search).get("projection") === "globe" ? "globe" : "mercator", mapInitialized = false;
    const flat = button("Flat map", () => setProjection("mercator")), globe = button("3D globe", () => setProjection("globe"));
    const mapTools = el("div", {class: "atlas-map-toolbar"}, flat, globe, el("span", {class: "atlas-small"}, "Drag to explore · scroll to zoom · clustered saved results"));
    // The map as it is drawn now. The picture is read during a draw, the only moment the map's canvas holds it.
    if (window.LeakScopeExport?.action) mapTools.append(window.LeakScopeExport.action("Export PNG", () => {
      if (!map?.getCanvas) return;
      map.once("render", () => { try { window.LeakScopeExport.saveURL("geography-map-" + window.LeakScopeExport.today() + ".png", map.getCanvas().toDataURL("image/png")); } catch (_) { mapNotice.replaceChildren(notice("The map picture could not be saved in this browser.", true)); } });
      map.triggerRepaint();
    }, "The map as shown now, as a picture"));
    const imageryConfigStatus = el("div", {"aria-live": "polite"}), imageryApi = input("imagery_api_url", "", {type: "url", maxlength: 2048, placeholder: "http://panoramax:5000/api"}), imageryMode = select("imagery_mode", [["local", "Local Panoramax"], ["external", "Configured external instance"]], "local"), imageryEnabled = el("input", {name: "imagery_enabled", type: "checkbox"});
    const sourceForm = el("form", {class: "atlas-source-form"}, field("Panoramax API URL", imageryApi), field("Source mode", imageryMode), el("label", {class: "atlas-check"}, imageryEnabled, "Enable street imagery"));
    const sourceSave = el("button", {type: "submit", class: "btn btn-small"}, "Save imagery source"); sourceForm.append(sourceSave);
    sourceForm.addEventListener("submit", event => { event.preventDefault(); perform(sourceSave, imageryConfigStatus, async () => {
      await request("/config", {api_url: imageryApi.value.trim(), mode: imageryMode.value, enabled: imageryEnabled.checked}, ctx.controller.signal);
      imageryConfigStatus.replaceChildren(notice("Saved imagery source. No imagery is loaded until requested."));
    }); });
    const sourcePanel = el("details", {class: "atlas-toolbox atlas-sources"}, el("summary", {}, "Configure street imagery source"), el("p", {class: "atlas-small"}, "Use the local Panoramax companion or explicitly configure another instance. The map and IP lookups remain local."), sourceForm, imageryConfigStatus);
    async function loadImageryConfig() {
      try { const value = await request("/config", undefined, ctx.controller.signal); if (ctx.disposed) return; const config = value.imagery || {}; imageryApi.value = config.api_url || ""; imageryMode.value = config.mode === "external" ? "external" : "local"; imageryEnabled.checked = !!config.enabled; }
      catch (error) { if (error.name !== "AbortError" && !ctx.disposed) imageryConfigStatus.replaceChildren(notice("Imagery configuration unavailable. " + error.message, true)); }
    }
    root.replaceChildren(heading("Geography atlas", "Explore the approximate network location of saved results, with local lookups and independent provider comparisons.", "Investigate / Geography"), metrics, mapTools, mapNotice, mapHost, detail, regions, state.form, coverage, status, el("details", {class: "atlas-country-list", open: true}, el("summary", {}, "Countries in this scope"), countries), dataPanel, sourcePanel, resultSection);
    function setProjection(value) {
      projection = value; if (state.controls.projection) state.controls.projection.value = value === "globe" ? "globe" : ""; flat.setAttribute("aria-pressed", String(value === "mercator")); globe.setAttribute("aria-pressed", String(value === "globe"));
      try { map?.setProjection?.({type: value}); } catch (_) { mapNotice.replaceChildren(notice("This browser cannot display the selected projection. The results table remains available.")); }
      const query = new URLSearchParams(location.search); value === "globe" ? query.set("projection", "globe") : query.delete("projection"); history.replaceState(null, "", location.pathname + (query.size ? "?" + query : ""));
    }
    state.controls.projection = input("projection", projection === "globe" ? "globe" : "", {type: "hidden"}); state.form.append(state.controls.projection);
    setProjection(projection);
    const updateMap = () => { if (mapReady) map?.getSource?.("atlas-results")?.setData?.({type: "FeatureCollection", features}); };
    async function initializeMap() {
      if (mapInitialized || ctx.disposed) return; mapInitialized = true;
      if (!window.maplibregl?.Map) { mapHost.hidden = true; mapNotice.replaceChildren(notice("Interactive map unavailable in this browser. Continent, country and saved-result views remain available.")); return; }
      try {
        const style = safeStyle(await request("/maps/style", undefined, ctx.controller.signal)); if (ctx.disposed) return;
        map = new window.maplibregl.Map({container: mapHost, style, center: [0, 20], zoom: 1.2, projection: {type: projection}, attributionControl: true, cooperativeGestures: true});
        if (map.addControl && window.maplibregl.NavigationControl) map.addControl(new window.maplibregl.NavigationControl(), "top-right");
        map.on("load", () => {
          if (ctx.disposed) return;
          mapReady = true;
          try { map.setProjection?.({type: projection}); mapNotice.replaceChildren(); } catch (_) { mapNotice.replaceChildren(notice("The requested projection is unavailable; saved-result comparisons remain available.")); }
          map.addSource("atlas-results", {type: "geojson", data: {type: "FeatureCollection", features}, cluster: true, clusterMaxZoom: 10, clusterRadius: 44});
          map.addLayer({id: "atlas-clusters", type: "circle", source: "atlas-results", filter: ["has", "point_count"], paint: {"circle-color": "#69bdb5", "circle-radius": ["step", ["get", "point_count"], 16, 10, 21, 100, 28], "circle-opacity": 0.83, "circle-stroke-width": 2, "circle-stroke-color": "#c0eee6"}});
          map.addLayer({id: "atlas-cluster-labels", type: "symbol", source: "atlas-results", filter: ["has", "point_count"], layout: {"text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Regular"], "text-size": 12}, paint: {"text-color": "#082331"}});
          map.addLayer({id: "atlas-points", type: "circle", source: "atlas-results", filter: ["!", ["has", "point_count"]], paint: {"circle-color": "#a9f0e3", "circle-radius": 6, "circle-stroke-width": 2, "circle-stroke-color": "#163c49"}});
          map.on("click", "atlas-points", event => { const row = event.features?.[0]?.properties; if (row?.endpoint_id) showDetail(row.endpoint_id); });
          map.on("click", "atlas-clusters", event => {
            const row = event.features?.[0]; if (!row) return;
            try { Promise.resolve(map.getSource("atlas-results").getClusterExpansionZoom(row.properties.cluster_id)).then(zoom => { if (!ctx.disposed) map.easeTo({center: row.geometry.coordinates, zoom, duration: document.body.classList.contains("motion-paused") || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 350}); }).catch(() => {}); } catch (_) {}
          });
          for (const name of ["atlas-points", "atlas-clusters"]) {
            map.on("mouseenter", name, () => { const canvas = map.getCanvas?.(); if (canvas) canvas.style.cursor = "pointer"; });
            map.on("mouseleave", name, () => { const canvas = map.getCanvas?.(); if (canvas) canvas.style.cursor = ""; });
          }
        });
        map.on("error", () => { if (!ctx.disposed) mapNotice.replaceChildren(notice("Some local map detail could not be loaded. Saved-result comparisons remain available.")); });
      } catch (error) { if (error.name !== "AbortError" && !ctx.disposed) { mapHost.hidden = true; mapNotice.replaceChildren(notice("Local map unavailable. " + error.message)); } }
    }
    const visibility = () => { if (!document.hidden) map?.resize?.(); };
    document.addEventListener("visibilitychange", visibility);
    ctx.cleanups.add(() => { detailController?.abort(); map?.remove?.(); document.removeEventListener("visibilitychange", visibility); });
    function regionButton(row, type) {
      return button("", () => { state.controls[type].value = row.code; state.form.requestSubmit(); }, {class: "atlas-region-card", "aria-label": "Filter " + row.name});
    }
    function showEstimates(data, controller, mine) {
      if (ctx.disposed || mine !== detailVersion) return;
      const endpoint = data.endpoint || {}, ip = data.ip || "Unknown IP";
      const close = button("Close location comparison", () => { detail.hidden = true; detailController?.abort(); if (detailTrigger?.isConnected) detailTrigger.focus(); });
      detail.replaceChildren(el("header", {class: "atlas-detail-heading"}, el("div", {}, el("span", {class: "atlas-eyebrow"}, "Same-IP source comparison"), el("h2", {}, endpointLabel(endpoint)), el("p", {class: "atlas-small"}, "Observed IP: " + ip)), close), el("p", {class: "atlas-coverage"}, data.basis || "Compare independent estimates for this retained IP. Country agreement does not establish an exact address."));
      const table = el("table", {class: "atlas-comparison-table"}, el("thead", {}, el("tr", {}, ["Source", "Country / city", "Coordinates", "Compared with local", "Source date"].map(text => el("th", {}, text))))), body = el("tbody");
      const estimates = [["Local", data.local || {}], ...["shodan", "zoomeye", "netlas"].map(name => [pretty(name), data.providers?.[name] || {status: "not_collected"}])];
      for (const [name, item] of estimates) {
        const comparison = data.comparisons?.[name.toLowerCase()];
        const accuracyText = Number.isFinite(item.accuracy_radius_km) ? "Accuracy radius " + count(item.accuracy_radius_km) + " km" : "Accuracy unknown";
        const comparisonText = name === "Local" ? ["Primary local estimate", accuracyText].join(" · ") : [pretty(comparison?.status || "not_comparable"), Number.isFinite(comparison?.distance_km) ? Number(comparison.distance_km).toLocaleString(undefined, {maximumFractionDigits: 1}) + " km" : "Distance unknown", comparison?.within_local_accuracy === true ? "Within local accuracy radius" : comparison?.within_local_accuracy === false ? "Outside local accuracy radius" : "Local accuracy comparison unknown", accuracyText].join(" · ");
        body.append(el("tr", {}, el("th", {scope: "row"}, name, el("small", {}, pretty(item.status)), item.ip ? el("small", {}, "IP " + item.ip) : null), el("td", {"data-label": "Country / city"}, [item.country_name || item.country_code, item.region, item.city].filter(Boolean).join(" / ") || "Not collected"), el("td", {"data-label": "Coordinates"}, Number.isFinite(item.latitude) && Number.isFinite(item.longitude) ? item.latitude.toFixed(4) + ", " + item.longitude.toFixed(4) : "Not available"), el("td", {"data-label": "Compared with local"}, comparisonText), el("td", {"data-label": "Source date"}, item.source_date ? date(item.source_date) : "Unknown", item.collected_at ? el("small", {}, "Collected " + date(item.collected_at)) : null)));
      }
      table.append(body); detail.append(el("div", {class: "table-scroll"}, table), evidenceActions({endpoint_id: endpoint.id}));
      const imageryStatus = el("div", {"aria-live": "polite"}), imageryList = el("div", {class: "atlas-imagery-grid"}), coords = estimates.filter(([, row]) => Number.isFinite(row.latitude) && Number.isFinite(row.longitude));
      const estimateSource = select("imagery_source", coords.map(([name, row]) => [name, name + " · " + row.latitude.toFixed(3) + ", " + row.longitude.toFixed(3)]), coords[0]?.[0] || "");
      let imagery; imagery = button("Street imagery near estimate", () => perform(imagery, imageryStatus, async () => {
        const selected = coords.find(([name]) => name === estimateSource.value)?.[1]; if (!selected) throw Error("No source has usable coordinates for nearby imagery.");
        const query = new URLSearchParams({lat: String(selected.latitude), lon: String(selected.longitude)}), result = await request("/imagery/nearby?" + query, undefined, controller.signal);
        if (ctx.disposed || mine !== detailVersion) return;
        imageryList.replaceChildren(); imageryStatus.replaceChildren(notice(result.basis || "Approximate IP vicinity. Street photographs do not identify the facility."));
        if (!(result.results || []).length) imageryList.append(notice("No street imagery is available near this estimate in the configured Panoramax source."));
        for (const [index, item] of (result.results || []).slice(0, 20).entries()) {
          const thumb = localURL(item.thumbnail_url), open = button("Open street photograph", () => fullImage(ctx, {label: "Street imagery near estimated IP location", imagery: true, items: () => result.results || [], start: index}));
          imageryList.append(el("article", {class: "atlas-imagery-card"}, thumb ? el("img", {src: thumb, loading: "lazy", alt: "Panoramax photograph near estimated coordinates"}) : el("span", {class: "atlas-preview-placeholder"}, "No preview"), el("p", {class: "atlas-small"}, date(item.captured_at) + (Number.isFinite(item.distance_m) ? " · " + count(item.distance_m) + " m from estimate" : "")), open));
        }
      }), {disabled: !coords.length});
      detail.append(el("details", {class: "atlas-imagery-panel", open: true}, el("summary", {}, "Street-level imagery"), el("p", {class: "atlas-small"}, "Approximate vicinity only. The configured Panoramax source is contacted only when you click the button."), field("Estimate to explore", estimateSource), imagery, imageryStatus, imageryList));
    }
    async function showDetail(id) {
      detailController?.abort(); detailController = new AbortController(); const controller = detailController, mine = ++detailVersion; if (!detail.contains(document.activeElement)) detailTrigger = document.activeElement; detail.hidden = false; detail.setAttribute("tabindex", "-1"); detail.replaceChildren(notice("Loading retained location comparison…")); detail.focus({preventScroll: true}); detail.scrollIntoView?.({block: "start", behavior: "auto"});
      try { const data = await request("/geography/" + encodeURIComponent(id) + "?source=" + encodeURIComponent(state.controls.source.value || "local"), undefined, controller.signal); showEstimates(data, controller, mine); }
      catch (error) { if (error.name !== "AbortError" && !ctx.disposed && mine === detailVersion) detail.replaceChildren(notice(error.message, true), button("Retry location comparison", () => showDetail(id))); }
    }
    async function load() {
      ctx.queryController?.abort(); ctx.queryController = new AbortController(); const mine = ++ctx.version;
      state.submit.disabled = true; status.replaceChildren(notice("Loading saved geographic evidence…")); resultCount.textContent = "Loading rows…"; resultState.page = 1; tableHost.replaceChildren(); metrics.replaceChildren(); coverage.textContent = ""; regions.replaceChildren(); countries.replaceChildren(); features = []; updateMap(); detail.hidden = true; detailController?.abort(); detailVersion++;
      try {
        const query = state.query(); query.delete("projection"); query.set("limit", "500");
        const data = await request("/geography?" + query, undefined, ctx.queryController.signal); if (ctx.disposed || mine !== ctx.version) return;
        setFilterChoices(data.filter_choices);
        latestData = data; features = (data.features || []).filter(row => Array.isArray(row.geometry?.coordinates) && row.geometry.coordinates.length >= 2).slice(0, 2000); updateMap();
        const c = data.counts || {};
        metrics.replaceChildren(metric("Scoped endpoints", c.scoped_endpoints), metric("Unique IPs", c.unique_ips), metric("Located", c.located), metric("Location unknown", c.unlocated));
        coverage.textContent = (data.basis || "Retained approximate IP locations.") + (data.truncated ? " · Bounded sample: " + count(c.sampled_endpoints) + " endpoints; more records may exist." : "") + " Coordinates describe an estimated network area, not a facility address.";
        for (const row of [...(data.continents || []), ...(c.continent_unknown > 0 ? [{code: "unknown", name: "Unknown continent", count: c.continent_unknown}] : [])]) { const control = regionButton(row, "continent"); control.append(el("span", {}, row.name), el("strong", {}, count(row.count)), el("small", {}, data.count_mode === "endpoints" ? "saved endpoints" : "unique IPs")); regions.append(control); }
        for (const row of [...(data.countries || []), ...(c.country_unknown > 0 ? [{code: "unknown", name: "Unknown country", count: c.country_unknown}] : [])]) { const control = regionButton(row, "country"); control.append(el("span", {}, row.name), el("strong", {}, count(row.count))); countries.append(control); }
        if (!(data.continents || []).length) regions.append(notice("No continent locations are available in this scope. Unknown-location records remain in the table."));
        const db = data.local_database || {};
        dataBody.replaceChildren(el("p", {class: "atlas-small"}, "Local IP database: " + pretty(db.source || "Unavailable") + " · " + pretty(db.status) + (db.version ? " · " + db.version : "")), el("p", {class: "atlas-small"}, "Source observation dates and the local database edition may differ. Provider comparisons use saved values; browsing does not refresh providers."));
        if (mapInfo) dataBody.append(mapInfo);
        const table = el("table", {}, el("caption", {}, "Saved geographic results"), el("thead", {}, el("tr", {}, ["Endpoint / IP", "Technology", "Country / city", "Location source", "Actions"].map(text => el("th", {}, text))))), body = el("tbody");
        for (const raw of data.results || []) {
          const row = raw.properties || raw, location = row.location || row;
          const coordinates = typeof location.latitude === "number" && Number.isFinite(location.latitude) && typeof location.longitude === "number" && Number.isFinite(location.longitude);
          const kind = coordinates ? "coordinates" : location.country_code || location.country_name ? "country_only" : "unknown";
          body.append(el("tr", {"data-location-coverage": kind, "data-comparison-status": row.comparison_status || ""}, el("td", {}, el("strong", {}, endpointLabel(row)), el("small", {}, row.ip || "Observed IP unavailable")), el("td", {}, pretty(row.technology)), el("td", {}, [location.country_name || location.country_code, location.city].filter(Boolean).join(" / ") || "Location unknown"), el("td", {}, pretty(location.source || data.source), el("small", {}, {coordinates: "Coordinates available", country_only: "Country only", unknown: "Location unknown"}[kind]), row.comparison_status === "country_differs" ? el("small", {}, "Country disagreement") : null), el("td", {}, button("Compare locations", () => showDetail(endpointId(row))), evidenceActions(row))));
        }
        table.append(body);
        const counts = ({total, matched}) => { resultCount.textContent = (matched === total ? count(total) : count(matched) + " of " + count(total)) + " loaded rows" + (data.truncated ? " · bounded" : ""); };
        if (window.LeakScopeTables?.browse) tableHost.replaceChildren(window.LeakScopeTables.browse(table, {state: resultState, onCounts: counts, filters: [{key: "coverage", label: "Location coverage", options: [["", "All coverage"], ["coordinates", "Coordinates available"], ["country_only", "Country only"], ["unknown", "Location unknown"], ["disagreement", "Country disagreement"]], match: (row, value) => value === "disagreement" ? row.dataset.comparisonStatus === "country_differs" : row.dataset.locationCoverage === value}]}));
        else { tableHost.replaceChildren(table); counts({total: body.rows.length, matched: body.rows.length}); window.LeakScopeTables?.enhance?.(table); if (!body.rows.length) tableHost.append(notice("No saved results match this geographic scope.")); }
        if (c.matched_endpoints === 0) status.replaceChildren(notice("No saved results match these map filters."));
        else status.replaceChildren();
      } catch (error) { if (error.name !== "AbortError" && !ctx.disposed && mine === ctx.version) { resultCount.textContent = "Rows unavailable"; status.replaceChildren(notice(error.message, true), button("Retry saved geography", load)); } }
      finally { if (mine === ctx.version) state.submit.disabled = false; }
    }
    async function mapStatus() {
      try { const data = await request("/maps/status", undefined, ctx.controller.signal); if (!ctx.disposed) { const details = el("p", {class: "atlas-small atlas-map-data-status"}, "Local basemap: " + (data.coverage || pretty(data.status)) + (data.archive?.version ? " · " + data.archive.version : "") + (data.basis ? " · " + data.basis : "")); mapInfo = details; dataBody.append(details); } }
      catch (error) { if (error.name !== "AbortError" && !ctx.disposed) dataBody.append(notice("Local basemap status unavailable. " + error.message)); }
    }
    bindFilters(ctx, state, load); await Promise.all([load(), initializeMap(), loadImageryConfig()]); await mapStatus();
  }

  async function render(root, page) {
    const ctx = makeContext(root, page);
    if (page === "screenshots") await screenshots(ctx);
    else if (page === "technologies") await technologies(ctx);
    else if (page === "geography") await geography(ctx);
    else root.replaceChildren(notice("Unknown Atlas view.", true));
    return ctx.dispose;
  }
  function dispose() { active?.dispose(); }
  window.addEventListener("pagehide", dispose);
  window.LeakScopeAtlas = {render, dispose};
})();
