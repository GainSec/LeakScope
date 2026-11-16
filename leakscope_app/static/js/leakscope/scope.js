/* LeakScope instruments: visual, read-only views over saved evidence.
 * Every instrument draws from the same saved-data endpoints the workspace
 * pages already read. None of them starts a job or contacts a target. */
(function (root) {
  "use strict";
  const doc = root.document;
  if (!doc) return;
  const TAU = Math.PI * 2;
  const SVG = "http://www.w3.org/2000/svg";

  /* Technology families keep one hue everywhere. The order is fixed: it is the
   * legend order, the left-to-right cluster order, and the validated
   * adjacent-pair order for colour-vision separation on the dark surface. */
  const FAMILIES = [
    { key: "database", label: "Databases", color: "#3987e5", dot: "#5fa2f2" },
    { key: "ot", label: "ICS / SCADA / OT", color: "#d95926", dot: "#ee7340" },
    { key: "web", label: "Web / applications", color: "#199e70", dot: "#2fc08b" },
    { key: "storage", label: "Data storage", color: "#c98500", dot: "#e39d14" },
    { key: "messaging", label: "Messaging", color: "#d55181", dot: "#ea6f9c" },
    { key: "iot", label: "IoT / devices", color: "#9085e9", dot: "#aaa1f4" },
    { key: "other", label: "Other", color: "#6f7d96", dot: "#93a1ba" },
    { key: "unknown", label: "Unknown", color: "#4a566b", dot: "#6b7891" },
  ];
  const FAMILY = new Map(FAMILIES.map((family, order) => [family.key, { ...family, order }]));
  const familyOf = key => FAMILY.get(key) || FAMILY.get("other");

  const numberFormat = new Intl.NumberFormat();
  let compactFormat;
  try { compactFormat = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }); }
  catch (_) { compactFormat = numberFormat; }
  const finite = value => value != null && value !== "" && Number.isFinite(Number(value));
  const count = value => finite(value) ? numberFormat.format(Number(value)) : "—";
  const compact = value => finite(value) ? compactFormat.format(Number(value)) : "—";
  const list = value => Array.isArray(value) ? value : [];
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const text = (value, fallback = "") => String(value == null || value === "" ? fallback : value).slice(0, 240);

  function h(tag, attrs, ...children) {
    const node = doc.createElement(tag);
    assign(node, attrs);
    append(node, children);
    return node;
  }
  function s(tag, attrs, ...children) {
    const node = doc.createElementNS(SVG, tag);
    assign(node, attrs);
    append(node, children);
    return node;
  }
  function assign(node, attrs) {
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false) node.append(child instanceof root.Node ? child : doc.createTextNode(String(child)));
    }
  }
  function localPath(value) {
    if (typeof value !== "string" || /[\x00-\x1f\x7f]/.test(value)) return null;
    try {
      const url = new URL(value, root.location.href);
      return url.origin === root.location.origin && ["http:", "https:"].includes(url.protocol) ? url.pathname + url.search : null;
    } catch (_) { return null; }
  }
  async function getJSON(path, signal) {
    const response = await root.fetch(path, { credentials: "same-origin", signal, headers: { Accept: "application/json" } });
    if (response.ok === false) throw Error("Saved data is unavailable (" + response.status + ")");
    return response.json();
  }
  function ago(value, now = Date.now()) {
    const stamp = Date.parse(value);
    if (!Number.isFinite(stamp)) return "";
    const seconds = Math.max(0, Math.round((now - stamp) / 1000));
    if (seconds < 90) return seconds + " s ago";
    if (seconds < 5400) return Math.round(seconds / 60) + " min ago";
    if (seconds < 129600) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " d ago";
  }
  const canDraw = () => typeof root.CanvasRenderingContext2D === "function" && typeof root.requestAnimationFrame === "function";
  const motionOn = () => !doc.body.classList.contains("motion-paused") &&
    !(typeof root.matchMedia === "function" && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
  function onMotionChange(callback) {
    if (typeof root.MutationObserver !== "function") return () => {};
    const observer = new root.MutationObserver(callback);
    observer.observe(doc.body, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }
  function whenVisible(node, callback) {
    if (typeof root.IntersectionObserver !== "function") { callback(true); return () => {}; }
    const observer = new root.IntersectionObserver(entries => callback(entries.some(entry => entry.isIntersecting)), { rootMargin: "80px" });
    observer.observe(node);
    return () => observer.disconnect();
  }
  function onResize(node, callback) {
    if (typeof root.ResizeObserver !== "function") { root.addEventListener("resize", callback); return () => root.removeEventListener("resize", callback); }
    const observer = new root.ResizeObserver(callback);
    observer.observe(node);
    return () => observer.disconnect();
  }
  // Small deterministic generator so layouts are identical on every visit.
  function seeded(seedText) {
    let seed = 2166136261;
    for (let i = 0; i < seedText.length; i++) { seed ^= seedText.charCodeAt(i); seed = Math.imul(seed, 16777619); }
    return () => {
      seed += 0x6d2b79f5; let t = seed;
      t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* One shared hover readout for every instrument. */
  const tip = (() => {
    let node = null;
    function ensure() {
      if (!node) { node = h("div", { class: "scope-tip", role: "tooltip", hidden: true }); doc.body.append(node); }
      return node;
    }
    function place(x, y) {
      const width = node.offsetWidth || 220, height = node.offsetHeight || 60;
      const left = clamp(x + 16, 8, (root.innerWidth || 1200) - width - 8);
      const top = y + 18 + height > (root.innerHeight || 800) ? y - height - 14 : y + 18;
      node.style.left = left + "px"; node.style.top = Math.max(8, top) + "px";
    }
    return {
      show(children, x, y) { ensure(); node.replaceChildren(...[children].flat().filter(Boolean)); node.hidden = false; place(x, y); },
      move(x, y) { if (node && !node.hidden) place(x, y); },
      hide() { if (node) node.hidden = true; },
    };
  })();
  function tipBody(title, lines, family) {
    return [
      h("strong", { class: "scope-tip-title" }, family ? h("i", { class: "scope-mark", style: "--family:" + family.color }) : null, text(title, "Saved record")),
      ...list(lines).filter(Boolean).map(line => h("span", { class: "scope-tip-line" }, line)),
    ];
  }
  function hoverTip(node, build) {
    node.addEventListener("pointerenter", event => tip.show(build(), event.clientX, event.clientY));
    node.addEventListener("pointermove", event => tip.move(event.clientX, event.clientY));
    node.addEventListener("pointerleave", () => tip.hide());
    node.addEventListener("focus", () => { const box = node.getBoundingClientRect(); tip.show(build(), box.left + box.width / 2, box.bottom); });
    node.addEventListener("blur", () => tip.hide());
  }

  /* ---------------------------------------------------------------- dial */
  /* A polar plan-position view: the north pole at the centre, longitude as
   * bearing. Every located address is on screen at once, which a globe cannot do. */
  function glow(color) {
    const canvas = doc.createElement("canvas"); canvas.width = canvas.height = 48;
    const g = canvas.getContext("2d"), fill = g.createRadialGradient(24, 24, 0, 24, 24, 24);
    fill.addColorStop(0, color + "ff"); fill.addColorStop(0.28, color + "9c"); fill.addColorStop(1, color + "00");
    g.fillStyle = fill; g.fillRect(0, 0, 48, 48);
    return canvas;
  }
  function dial(options = {}) {
    const canvas = h("canvas", { class: "scope-dial-canvas", role: "img", "aria-label": options.label || "Polar map of located saved addresses" });
    const status = h("figcaption", { class: "scope-dial-status", role: "status" }, options.status || "");
    const figure = h("figure", { class: "scope-dial" }, canvas, status,
      h("p", { class: "scope-dial-hint" }, "North pole at the centre, longitude around the rim. Select any mark to open its evidence."));
    const api = { element: figure, setPoints() {}, setLand() {}, setFilter() {}, setStatus(message) { status.textContent = message || ""; }, destroy() {} };
    if (!canDraw()) return api;
    const ctx = canvas.getContext("2d");
    if (!ctx) return api;
    const SOUTH = -58, MERIDIAN = 12, PERIOD = 9000;
    const sprites = new Map(FAMILIES.map(family => [family.key, glow(family.color)]));
    let size = 0, dpr = 1, radius = 0, centre = 0, base = null, land = [], points = [], filter = null, hover = null;
    let frameId = 0, visible = true, destroyed = false, last = 0;
    const started = root.performance.now();
    const ring = latitude => (90 - Math.max(latitude, SOUTH)) / (90 - SOUTH) * radius;
    const bearing = longitude => (longitude - MERIDIAN) * Math.PI / 180;
    function project(longitude, latitude) {
      const r = ring(latitude), a = bearing(longitude);
      return [centre + r * Math.sin(a), centre + r * Math.cos(a)];
    }
    function place(point) {
      const [x, y] = project(point.lon, point.lat);
      point.x = x; point.y = y; point.theta = Math.atan2(y - centre, x - centre);
    }
    function drawBase() {
      base = doc.createElement("canvas"); base.width = base.height = canvas.width;
      const g = base.getContext("2d"); g.scale(dpr, dpr);
      const disc = g.createRadialGradient(centre, centre, radius * 0.04, centre, centre, radius);
      disc.addColorStop(0, "#13293d"); disc.addColorStop(0.62, "#0c1a2b"); disc.addColorStop(1, "#08121e");
      g.beginPath(); g.arc(centre, centre, radius, 0, TAU); g.fillStyle = disc; g.fill();
      g.save(); g.beginPath(); g.arc(centre, centre, radius, 0, TAU); g.clip();
      if (land.length) {
        g.beginPath();
        for (const outline of land) {
          for (let i = 0; i < outline.length; i++) { const [x, y] = project(outline[i][0], outline[i][1]); i ? g.lineTo(x, y) : g.moveTo(x, y); }
          g.closePath();
        }
        g.fillStyle = "#182f44"; g.fill("evenodd");
        g.strokeStyle = "#35566f"; g.lineWidth = 0.6; g.stroke();
      }
      for (const latitude of [60, 30, 0, -30]) {
        g.beginPath(); g.arc(centre, centre, ring(latitude), 0, TAU);
        g.setLineDash(latitude === 0 ? [] : [2, 5]);
        g.strokeStyle = latitude === 0 ? "rgba(168,208,228,.24)" : "rgba(150,190,215,.13)"; g.lineWidth = latitude === 0 ? 1 : 0.75; g.stroke();
      }
      g.setLineDash([]);
      for (let longitude = -180; longitude < 180; longitude += 30) {
        const [x, y] = project(longitude, SOUTH);
        g.beginPath(); g.moveTo(centre, centre); g.lineTo(x, y); g.strokeStyle = "rgba(150,190,215,.08)"; g.lineWidth = 0.75; g.stroke();
      }
      g.restore();
      g.beginPath(); g.arc(centre, centre, radius, 0, TAU); g.strokeStyle = "#40607c"; g.lineWidth = 1; g.stroke();
      const every = size < 420 ? 90 : 30;
      g.font = "10px 'B612 Mono', ui-monospace, monospace"; g.textAlign = "center"; g.textBaseline = "middle";
      for (let longitude = -180; longitude < 180; longitude += 10) {
        const a = bearing(longitude), major = longitude % 30 === 0, inner = radius + 3, outer = radius + (major ? 9 : 5);
        g.beginPath(); g.moveTo(centre + inner * Math.sin(a), centre + inner * Math.cos(a)); g.lineTo(centre + outer * Math.sin(a), centre + outer * Math.cos(a));
        g.strokeStyle = major ? "#6f8ca8" : "#35495f"; g.lineWidth = 1; g.stroke();
        if (longitude % every === 0) {
          const label = longitude === 0 ? "0°" : longitude === -180 ? "180°" : Math.abs(longitude) + "°" + (longitude > 0 ? "E" : "W");
          g.fillStyle = "#8ba3bd"; g.fillText(label, centre + (radius + 21) * Math.sin(a), centre + (radius + 21) * Math.cos(a));
        }
      }
    }
    function draw(now) {
      if (!size) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, size, size);
      if (base) ctx.drawImage(base, 0, 0, size, size);
      const beam = motionOn() && visible ? ((now - started) % PERIOD) / PERIOD * TAU : null;
      ctx.save(); ctx.beginPath(); ctx.arc(centre, centre, radius, 0, TAU); ctx.clip();
      if (beam != null) {
        if (typeof ctx.createConicGradient === "function") {
          const span = 1.2, edge = span / TAU, wedge = ctx.createConicGradient(beam - span, centre, centre);
          wedge.addColorStop(0, "rgba(131,229,213,0)"); wedge.addColorStop(edge * 0.55, "rgba(131,229,213,.03)");
          wedge.addColorStop(edge, "rgba(131,229,213,.19)"); wedge.addColorStop(Math.min(1, edge + 0.0006), "rgba(131,229,213,0)"); wedge.addColorStop(1, "rgba(131,229,213,0)");
          ctx.fillStyle = wedge; ctx.beginPath(); ctx.arc(centre, centre, radius, 0, TAU); ctx.fill();
        }
        ctx.beginPath(); ctx.moveTo(centre, centre); ctx.lineTo(centre + Math.cos(beam) * radius, centre + Math.sin(beam) * radius);
        ctx.strokeStyle = "rgba(176,244,232,.5)"; ctx.lineWidth = 1; ctx.stroke();
      }
      // Halos add up so density reads as brightness; cores stay in family colour on top.
      ctx.globalCompositeOperation = "lighter";
      for (const point of points) {
        const on = !filter || point.family === filter;
        point.lift = 0;
        if (beam != null && on) { const behind = (((beam - point.theta) % TAU) + TAU) % TAU; point.lift = Math.exp(-behind * 1.9); }
        const reach = (point.size + point.lift * (point.named ? 7 : 1.5)) * (on ? 1 : 0.7);
        ctx.globalAlpha = on ? Math.min(1, (point.named ? 0.24 : 0.045) + point.lift * (point.named ? 0.4 : 0.05)) : 0.02;
        ctx.drawImage(sprites.get(point.family), point.x - reach, point.y - reach, reach * 2, reach * 2);
      }
      ctx.globalCompositeOperation = "source-over";
      for (const point of points) {
        const on = !filter || point.family === filter;
        ctx.globalAlpha = on ? 1 : 0.12;
        // The sweep flares named families to white; the unnamed majority only brightens, so dense areas keep their detail.
        ctx.fillStyle = point.lift > 0.6 ? (point.named ? "#f3fbff" : "#c7d4e5") : point.dot;
        ctx.beginPath(); ctx.arc(point.x, point.y, (point.named ? 1.9 : 1.35) + point.lift * (point.named ? 1.3 : 0.35), 0, TAU); ctx.fill();
      }
      ctx.globalAlpha = 1;
      if (hover) {
        ctx.beginPath(); ctx.moveTo(centre, centre); ctx.lineTo(hover.x, hover.y); ctx.strokeStyle = "rgba(236,246,255,.28)"; ctx.lineWidth = 1; ctx.stroke();
        ctx.beginPath(); ctx.arc(hover.x, hover.y, 9, 0, TAU); ctx.strokeStyle = "#eef7ff"; ctx.lineWidth = 1.25; ctx.stroke();
      }
      ctx.restore();
      ctx.beginPath(); ctx.arc(centre, centre, 2.2, 0, TAU); ctx.fillStyle = "#b0f4e8"; ctx.fill();
    }
    function frame(now) {
      frameId = 0;
      if (destroyed) return;
      if (now - last >= 30) { last = now; draw(now); }
      if (motionOn() && visible && !doc.hidden) frameId = root.requestAnimationFrame(frame);
    }
    function kick() { if (!frameId && !destroyed) frameId = root.requestAnimationFrame(frame); }
    function layout() {
      const width = Math.round(figure.clientWidth);
      if (!width || destroyed) return;
      dpr = Math.min(2, root.devicePixelRatio || 1); size = width; centre = size / 2; radius = size / 2 - 34;
      canvas.width = canvas.height = Math.round(size * dpr);
      points.forEach(place); drawBase(); last = 0; kick();
    }
    function nearest(event) {
      const box = canvas.getBoundingClientRect(), x = event.clientX - box.left, y = event.clientY - box.top;
      let best = null, distance = 14 * 14;
      for (const point of points) {
        if (filter && point.family !== filter) continue;
        const d = (point.x - x) ** 2 + (point.y - y) ** 2;
        if (d < distance) { best = point; distance = d; }
      }
      return best;
    }
    canvas.addEventListener("pointermove", event => {
      const found = nearest(event);
      if (found !== hover) {
        hover = found; canvas.style.cursor = found ? "pointer" : "";
        if (found) tip.show(tipBody(found.title, [found.address, found.technology, found.place], familyOf(found.family)), event.clientX, event.clientY);
        else tip.hide();
        last = 0; kick();
      } else if (found) tip.move(event.clientX, event.clientY);
    });
    canvas.addEventListener("pointerleave", () => { hover = null; tip.hide(); last = 0; kick(); });
    canvas.addEventListener("click", () => { if (hover && typeof options.onSelect === "function") { tip.hide(); options.onSelect(hover); } });
    const cleanups = [
      onResize(figure, layout), onMotionChange(() => { last = 0; kick(); }),
      whenVisible(figure, state => { visible = state; if (state) kick(); }),
    ];
    const wake = () => { if (!doc.hidden) kick(); };
    doc.addEventListener("visibilitychange", wake);
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(() => { if (size && !destroyed) { drawBase(); last = 0; kick(); } }).catch(() => {});
    api.setPoints = rows => {
      points = list(rows).filter(row => finite(row.lon) && finite(row.lat)).map(row => {
        const family = familyOf(row.family);
        return { ...row, family: family.key, dot: family.dot, lift: 0, named: family.key !== "other" && family.key !== "unknown",
          size: 4.5 + Math.min(4, Math.log2(Math.max(1, row.count || 1)) * 1.4) };
      });
      // Named families are drawn last so they stay readable above the neutral majority.
      points.sort((a, b) => familyOf(b.family).order - familyOf(a.family).order);
      points.forEach(place); last = 0; kick();
    };
    api.setLand = outlines => { land = list(outlines); if (size) { drawBase(); last = 0; kick(); } };
    api.setFilter = key => { filter = key || null; hover = null; last = 0; kick(); };
    api.destroy = () => { destroyed = true; cleanups.forEach(stop => stop()); doc.removeEventListener("visibilitychange", wake); if (frameId) root.cancelAnimationFrame(frameId); };
    layout();
    return api;
  }
  function outlines(geojson) {
    const rings = [];
    const add = polygon => { for (const outline of list(polygon)) if (outline.length > 2 && outline.some(position => position[1] > -58)) rings.push(outline); };
    for (const feature of list(geojson && geojson.features)) {
      const geometry = feature && feature.geometry;
      if (!geometry) continue;
      if (geometry.type === "Polygon") add(geometry.coordinates);
      else if (geometry.type === "MultiPolygon") list(geometry.coordinates).forEach(add);
    }
    return rings;
  }

  /* ------------------------------------------------------- family legend */
  function families(categories, options = {}) {
    const byKey = new Map(list(categories).map(row => [row.key, row]));
    const peak = Math.max(1, ...list(categories).map(row => Number(row.endpoints) || 0));
    let locked = null;
    const rows = [];
    const group = h("div", { class: "scope-families", role: "group", "aria-label": "Technology families" });
    const apply = key => {
      for (const row of rows) row.classList.toggle("is-dim", !!key && row.dataset.family !== key);
      if (typeof options.onFilter === "function") options.onFilter(key);
    };
    for (const family of FAMILIES) {
      const data = byKey.get(family.key);
      if (!data && options.hideEmpty) continue;
      const total = data ? Number(data.endpoints) || 0 : 0;
      const row = h("button", { type: "button", class: "scope-family", "data-family": family.key, "aria-pressed": "false", style: "--family:" + family.color,
        "aria-label": family.label + ": " + count(total) + " saved endpoints" },
        h("i", { class: "scope-mark" }), h("span", { class: "scope-family-name" }, family.label),
        h("span", { class: "scope-family-track", "aria-hidden": "true" }, h("i", { style: "width:" + (total / peak * 100).toFixed(2) + "%" })),
        h("strong", { class: "scope-family-count" }, count(total)));
      row.addEventListener("pointerenter", () => { if (!locked) apply(family.key); });
      row.addEventListener("pointerleave", () => { if (!locked) apply(null); });
      row.addEventListener("click", () => {
        locked = locked === family.key ? null : family.key;
        for (const other of rows) other.setAttribute("aria-pressed", String(other.dataset.family === locked));
        apply(locked);
      });
      rows.push(row); group.append(row);
    }
    return group;
  }

  /* ------------------------------------------------- technology constellation */
  /* One circle per technology, area in proportion to saved endpoints, gathered
   * into its family. Families sit left to right in legend order. */
  function constellation(data, options = {}) {
    const groups = list(data && data.groups).filter(group => Number(group.endpoints) > 0);
    const figure = h("figure", { class: "scope-constellation" });
    if (!groups.length) { figure.append(h("p", { class: "scope-empty" }, options.empty || "No technologies in saved evidence yet.")); return figure; }
    const total = groups.reduce((sum, group) => sum + Number(group.endpoints), 0);
    const unit = Math.sqrt(120000 / (Math.PI * total));
    const nodes = groups.map(group => {
      const family = familyOf(group.category);
      return { group, family, r: Math.max(2.6, unit * Math.sqrt(Number(group.endpoints))), x: 0, y: 0, vx: 0, vy: 0 };
    });
    const clusters = FAMILIES.map(family => ({ family, nodes: nodes.filter(node => node.family.key === family.key) })).filter(cluster => cluster.nodes.length);
    for (const cluster of clusters) cluster.reach = Math.sqrt(cluster.nodes.reduce((sum, node) => sum + node.r * node.r, 0)) * 1.2 + 6;
    const named = clusters.filter(cluster => cluster.family.key !== "other" && cluster.family.key !== "unknown");
    const rest = clusters.filter(cluster => !named.includes(cluster));
    const lines = named.length > 3 ? 2 : 1, perLine = Math.ceil(named.length / lines) || 1, caption = 30;
    const columns = Array.from({ length: perLine }, (_, column) => Math.max(...named.filter((_, index) => index % perLine === column).map(cluster => cluster.reach)) * 2 + 34);
    const heights = Array.from({ length: lines }, (_, line) => Math.max(...named.filter((_, index) => Math.floor(index / perLine) === line).map(cluster => cluster.reach)) * 2 + caption + 10);
    const tall = heights.reduce((sum, value) => sum + value, 0);
    named.forEach((cluster, index) => {
      const column = index % perLine, line = Math.floor(index / perLine);
      cluster.x = columns.slice(0, column).reduce((sum, value) => sum + value, 0) + columns[column] / 2;
      cluster.y = heights.slice(0, line).reduce((sum, value) => sum + value, 0) + (heights[line] - caption) / 2 - tall / 2;
    });
    let cursor = named.length ? columns.reduce((sum, value) => sum + value, 0) + 20 : 0;
    for (const cluster of rest) { cluster.x = cursor + cluster.reach; cluster.y = 0; cursor += cluster.reach * 2 + 26; }
    for (const cluster of clusters) {
      const random = seeded(cluster.family.key);
      for (const node of cluster.nodes) { node.ax = cluster.x; node.ay = cluster.y; node.x = cluster.x + (random() - 0.5) * cluster.reach; node.y = cluster.y + (random() - 0.5) * cluster.reach; }
    }
    for (let step = 0; step < 280; step++) {
      const pull = 0.035 * (1 - step / 420);
      for (const node of nodes) { node.vx = (node.vx + (node.ax - node.x) * pull) * 0.8; node.vy = (node.vy + (node.ay - node.y) * pull) * 0.8; node.x += node.vx; node.y += node.vy; }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j], dx = b.x - a.x, dy = b.y - a.y, gap = a.r + b.r + 1.6, d2 = dx * dx + dy * dy;
          if (d2 >= gap * gap) continue;
          const d = Math.sqrt(d2) || 0.01, push = (gap - d) / d * 0.5, share = b.r / (a.r + b.r);
          a.x -= dx * push * share; a.y -= dy * push * share; b.x += dx * push * (1 - share); b.y += dy * push * (1 - share);
        }
      }
    }
    const totals = new Map(list(data.categories).map(row => [row.key, row.endpoints]));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const cluster of clusters) {
      let left = Infinity, right = -Infinity, bottom = -Infinity;
      for (const node of cluster.nodes) {
        left = Math.min(left, node.x - node.r); right = Math.max(right, node.x + node.r); bottom = Math.max(bottom, node.y + node.r);
        minY = Math.min(minY, node.y - node.r);
      }
      cluster.cx = (left + right) / 2; cluster.bottom = bottom;
      minX = Math.min(minX, left, cluster.cx - 70); maxX = Math.max(maxX, right, cluster.cx + 70); maxY = Math.max(maxY, bottom + caption);
    }
    const pad = 6, width = maxX - minX + pad * 2, height = maxY - minY + pad * 2;
    const svg = s("svg", { class: "scope-constellation-plot", viewBox: [minX - pad, minY - pad, width, height].join(" "), role: "group",
      "aria-label": "Technologies sized by saved endpoints, grouped by family" });
    const marks = [];
    for (const node of nodes.slice().sort((a, b) => b.r - a.r)) {
      const group = node.group, href = typeof options.href === "function" ? options.href(group) : "/assets/?technology=" + encodeURIComponent(group.key);
      const name = text(group.label || group.key, "Unnamed technology");
      const link = s("a", { class: "scope-star", href, "data-family": node.family.key, tabindex: node.r > 9 ? "0" : "-1",
        "aria-label": name + ", " + node.family.label + ": " + count(group.endpoints) + " saved endpoints" },
        s("circle", { cx: node.x.toFixed(2), cy: node.y.toFixed(2), r: node.r.toFixed(2), fill: node.family.color }));
      link.dataset.r = node.r.toFixed(2);
      link.dataset.label = name;
      link.dataset.x = node.x.toFixed(2); link.dataset.y = node.y.toFixed(2);
      hoverTip(link, () => tipBody(name, [node.family.label, count(group.endpoints) + " saved endpoints", Number(group.screenshots) ? count(group.screenshots) + " captures" : null], node.family));
      svg.append(link); marks.push(link);
    }
    const labels = s("g", { class: "scope-star-labels", "aria-hidden": "true" }), captions = s("g", { class: "scope-star-captions" });
    svg.append(labels, captions);
    function label() {
      const scale = (svg.getBoundingClientRect().width || 900) / width, drawn = Math.min(scale, (svg.getBoundingClientRect().height || 400) / height) || scale;
      labels.replaceChildren(); captions.replaceChildren();
      for (const mark of marks) {
        // A name is shown only when the whole of it fits inside its circle.
        const r = Number(mark.dataset.r) * drawn, name = mark.dataset.label;
        let size = Math.min(15, r * 0.5);
        const room = r * 2 * 0.86, needs = name.length * size * 0.56;
        if (needs > room) size = room / (name.length * 0.56);
        if (size < 10) continue;
        labels.append(s("text", { x: mark.dataset.x, y: mark.dataset.y, "font-size": (size / drawn).toFixed(2), "data-family": mark.dataset.family }, name));
      }
      // On narrow screens the family list above the plot carries the names and totals.
      for (const cluster of drawn < 0.62 ? [] : clusters) {
        const total = totals.has(cluster.family.key) ? totals.get(cluster.family.key) : cluster.nodes.reduce((sum, node) => sum + Number(node.group.endpoints), 0);
        captions.append(s("text", { x: cluster.cx.toFixed(2), y: (cluster.bottom + 20).toFixed(2), "font-size": (13 / drawn).toFixed(2), "data-family": cluster.family.key },
          cluster.family.label + " ", s("tspan", { class: "scope-star-total" }, count(total))));
      }
    }
    figure.append(svg);
    figure.setFilter = key => {
      for (const mark of marks) mark.classList.toggle("is-dim", !!key && mark.dataset.family !== key);
      for (const node of svg.querySelectorAll("text[data-family]")) node.classList.toggle("is-dim", !!key && node.dataset.family !== key);
    };
    onResize(figure, label);
    root.setTimeout(label, 0);
    return figure;
  }

  /* --------------------------------------------------- certificate stream */
  /* The stream tape draws readings, not decoration. A bar spans two readings of the collector's certificate
   * counter that differ; its height is certificates per second across that span. Markers are saved watch matches
   * at the time they were saved. Unmatched certificates are not stored, so nothing here stands for a single one. */
  const TAPE_WINDOW = 180000, TAPE_GAP = 45000, TAPE_KEY = "leakscope.stream.samples";
  const perSecond = value => value >= 100 ? compact(Math.round(value)) : value.toFixed(value >= 10 ? 0 : 1);
  function streamTape(samples, matches, now = Date.now(), windowMs = TAPE_WINDOW) {
    const bars = [], start = now - windowMs;
    let base = null, last = null;
    for (const sample of list(samples)) {
      const broken = !base || sample.received < base.received || sample.at - last.at > TAPE_GAP;
      last = sample;
      if (broken) { base = sample; continue; }
      if (sample.received === base.received || sample.at <= base.at) continue;
      if (sample.at > start) bars.push({ from: base.at, to: sample.at, count: sample.received - base.received, rate: (sample.received - base.received) / ((sample.at - base.at) / 1000) });
      base = sample;
    }
    const marks = [];
    for (const row of list(matches)) {
      const at = Date.parse(row && row.collected_at);
      if (Number.isFinite(at) && at > start && at <= now + 5000) marks.push({ id: row.id, at, names: list(row.names).slice(0, 4).join(", ") || "Matched certificate", watch: text(row.watch_name, "unnamed") });
    }
    return { bars, marks, peak: bars.reduce((peak, bar) => Math.max(peak, bar.rate), 0) };
  }

  function stream(options = {}) {
    const state = h("span", { class: "scope-state" }, h("i", {}), h("span", {}, "Checking"));
    const readout = (label, key) => { const value = h("dd", { "data-readout": key }, "—"); return { value, node: h("div", {}, value, h("dt", {}, label)) }; };
    const seen = readout("certificates seen", "received"), matched = readout("matched your watches", "matched"), pace = readout("per second", "rate"), lag = readout("behind live", "lag");
    const canvas = h("canvas", { class: "scope-stream-flow", role: "img", "aria-label": "Certificates per second: waiting for a first reading." });
    const scale = h("span", { class: "scope-stream-scale" }), note = h("span", { class: "scope-stream-note" }, "Measuring. The first bar appears after two readings.");
    const ticks = h("div", { class: "scope-stream-matches" });
    const latest = h("a", { class: "scope-stream-latest", href: "/monitoring/" });
    const section = h("section", { class: "scope-block scope-stream" },
      h("header", { class: "scope-block-head" }, h("h2", {}, h("a", { href: "/monitoring/" }, "Certificate stream")), state),
      h("div", { class: "scope-stream-stage" }, canvas, scale, note),
      h("div", { class: "scope-axis scope-stream-axis" }, h("span", {}, "3 min ago"), h("span", {}, "certificates per second"), h("span", {}, "now")),
      h("dl", { class: "scope-readouts" }, seen.node, matched.node, pace.node, lag.node), ticks, latest);
    let rate = null, timer = 0, destroyed = false, current = { bars: [], marks: [], peak: 0 };
    const samples = [], history = [];
    try {
      const floor = Date.now() - TAPE_WINDOW - TAPE_GAP;
      for (const row of list(JSON.parse(root.sessionStorage.getItem(TAPE_KEY) || "[]"))) {
        if (Array.isArray(row) && Number.isFinite(row[0]) && Number.isFinite(row[1]) && row[0] > floor && row[0] <= Date.now() + 1000) history.push({ at: row[0], received: row[1] });
      }
    } catch (_) { history.length = 0; }
    const controller = typeof root.AbortController === "function" ? new root.AbortController() : null;

    let frameId = 0, visible = true, lastPaint = 0, width = 0, height = 0, dpr = 1, hovered = null;
    const ctx = canDraw() ? canvas.getContext("2d") : null;
    const xOf = (at, now) => width - (now - at) / TAPE_WINDOW * width;
    function paint(stamp) {
      frameId = 0;
      if (!ctx || destroyed || !width) return;
      // The tape moves because time passes: one window width every three minutes.
      const moving = motionOn() && visible && !doc.hidden;
      if (moving) frameId = root.requestAnimationFrame(paint);
      if (lastPaint && stamp - lastPaint < 60) return;
      lastPaint = stamp;
      const now = Date.now(), floor = height - 1, room = height - 18, top = current.peak || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
      if (current.bars.length) {
        ctx.strokeStyle = "rgba(150,176,206,.2)"; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
        ctx.beginPath(); ctx.moveTo(0, floor - room + 0.5); ctx.lineTo(width, floor - room + 0.5); ctx.stroke(); ctx.setLineDash([]);
      }
      const newest = current.bars[current.bars.length - 1];
      for (const bar of current.bars) {
        const right = xOf(bar.to, now), left = Math.max(0, xOf(bar.from, now));
        if (right <= 0) continue;
        const tall = Math.max(2, bar.rate / top * room), wide = Math.max(1, right - left - 2), radius = Math.min(3, wide / 2);
        ctx.fillStyle = bar === hovered ? "rgba(176,244,232,1)" : bar === newest ? "rgba(131,229,213,.8)" : "rgba(150,176,206,.5)";
        ctx.beginPath();
        if (typeof ctx.roundRect === "function") ctx.roundRect(left + 1, floor - tall, wide, tall, [radius, radius, 0, 0]); else ctx.rect(left + 1, floor - tall, wide, tall);
        ctx.fill();
      }
      for (const mark of current.marks) {
        const x = xOf(mark.at, now);
        if (x < 0) continue;
        ctx.strokeStyle = "#f2c14e"; ctx.fillStyle = "#f2c14e"; ctx.lineWidth = mark === hovered ? 3 : 2;
        ctx.beginPath(); ctx.moveTo(x, 20); ctx.lineTo(x, floor); ctx.stroke();
        ctx.beginPath(); ctx.arc(x, 20, 4, 0, TAU); ctx.fill();
      }
    }
    const kick = () => { if (ctx && !destroyed) { lastPaint = 0; if (!frameId) frameId = root.requestAnimationFrame(paint); } };
    function layout() {
      const box = canvas.getBoundingClientRect();
      if (!box.width) return;
      dpr = Math.min(2, root.devicePixelRatio || 1); width = box.width; height = box.height || 56;
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); kick();
    }
    function under(event) {
      const box = canvas.getBoundingClientRect(), x = event.clientX - box.left, now = Date.now();
      return current.marks.find(mark => Math.abs(xOf(mark.at, now) - x) <= 7) ||
        current.bars.find(bar => x >= xOf(bar.from, now) && x <= xOf(bar.to, now)) || null;
    }
    function point(event) {
      const found = under(event), now = Date.now();
      if (found !== hovered) {
        hovered = found; kick();
        if (!found) tip.hide();
        else if (found.names) tip.show(tipBody(found.names, ["Matched watch " + found.watch, ago(new Date(found.at).toISOString(), now)]), event.clientX, event.clientY);
        else tip.show(tipBody(perSecond(found.rate) + " certificates per second", [count(found.count) + " counted over " + Math.round((found.to - found.from) / 1000) + " s", "reading taken " + ago(new Date(found.to).toISOString(), now)]), event.clientX, event.clientY);
      } else if (found) tip.move(event.clientX, event.clientY);
    }
    canvas.addEventListener("pointermove", point);
    canvas.addEventListener("pointerleave", () => { hovered = null; tip.hide(); kick(); });
    const cleanups = ctx ? [onResize(canvas, layout), onMotionChange(kick), whenVisible(section, value => { visible = value; if (value) kick(); })] : [];
    function describe() {
      const bars = current.bars, newest = bars[bars.length - 1];
      note.hidden = bars.length > 0;
      scale.textContent = bars.length ? "peak " + perSecond(current.peak) + "/s" : "";
      canvas.setAttribute("aria-label", bars.length
        ? "Certificates per second over the last 3 minutes: latest " + perSecond(newest.rate) + " per second, peak " + perSecond(current.peak) + " per second, " + current.marks.length + " watch " + (current.marks.length === 1 ? "match" : "matches") + "."
        : "Certificates per second: waiting for a second reading.");
    }

    function update(data) {
      const now = Date.now(), live = String(data.state || "").toLowerCase();
      section.dataset.state = data.enabled === false ? "off" : live || "unknown";
      state.lastChild.textContent = data.enabled === false ? "Paused" : live ? live.charAt(0).toUpperCase() + live.slice(1) : "Unknown";
      seen.value.textContent = compact(data.received); seen.value.title = count(data.received) + " certificates received since counting began";
      matched.value.textContent = count(data.matched);
      lag.value.textContent = finite(data.lag_seconds) ? count(data.lag_seconds) + " s" : "—";
      if (finite(data.received)) {
        // The counter is persisted in batches, so the rate is taken across a window rather than between two readings.
        samples.push({ at: now, received: Number(data.received) });
        while (samples.length > 2 && now - samples[1].at > 45000) samples.shift();
        const oldest = samples[0], delta = Number(data.received) - oldest.received, elapsed = (now - oldest.at) / 1000;
        if (delta > 0 && elapsed > 1) { rate = delta / elapsed; pace.value.textContent = rate >= 100 ? compact(Math.round(rate)) : rate.toFixed(rate >= 10 ? 0 : 1); }
        else if (rate == null) pace.value.textContent = "…";
        history.push({ at: now, received: Number(data.received) });
        while (history.length && history[0].at < now - TAPE_WINDOW - TAPE_GAP) history.shift();
        try { root.sessionStorage.setItem(TAPE_KEY, JSON.stringify(history.map(row => [row.at, row.received]))); } catch (_) {}
      }
      current = streamTape(history, data.recent_matches, now);
      describe();
      const matches = list(data.recent_matches).filter(row => Number.isFinite(Date.parse(row.collected_at)));
      ticks.replaceChildren();
      if (matches.length) {
        const times = matches.map(row => Date.parse(row.collected_at)), first = Math.min(...times), span = Math.max(1, now - first);
        const track = h("div", { class: "scope-stream-track", role: "list", "aria-label": "Recent watch matches" });
        for (const row of matches) {
          const names = list(row.names).slice(0, 4).join(", ") || "Matched certificate";
          const mark = h("a", { class: "scope-stream-tick", role: "listitem", href: "/certificates/", style: "left:" + ((Date.parse(row.collected_at) - first) / span * 100).toFixed(2) + "%",
            "aria-label": names + ", watch " + text(row.watch_name, "unnamed") + ", " + ago(row.collected_at, now) });
          hoverTip(mark, () => tipBody(names, ["Watch " + text(row.watch_name, "unnamed"), ago(row.collected_at, now)]));
          track.append(mark);
        }
        ticks.append(track, h("div", { class: "scope-axis" }, h("span", {}, ago(new Date(first).toISOString(), now)), h("span", {}, "now")));
        const newest = matches.reduce((a, b) => Date.parse(a.collected_at) > Date.parse(b.collected_at) ? a : b);
        latest.replaceChildren(h("span", {}, text(list(newest.names)[0], "Matched certificate")), h("small", {}, "matched " + text(newest.watch_name, "a watch") + " " + ago(newest.collected_at, now)));
        latest.hidden = false;
      } else { latest.hidden = true; ticks.append(h("p", { class: "scope-empty" }, "No watch matches saved yet.")); }
      kick();
    }
    async function poll() {
      if (destroyed || doc.hidden) return;
      try { update(await getJSON("/api/operations/stream", controller ? controller.signal : undefined)); }
      catch (error) { if (error.name !== "AbortError") { section.dataset.state = "unknown"; state.lastChild.textContent = "Unavailable"; } }
    }
    section.start = () => {
      poll();
      if (options.poll !== false && typeof root.setInterval === "function") { root.setTimeout(poll, 2600); timer = root.setInterval(poll, options.interval || 8000); }
      root.addEventListener("pagehide", section.stop, { once: true });
      return section;
    };
    section.stop = () => { destroyed = true; if (timer) root.clearInterval(timer); if (controller) controller.abort(); cleanups.forEach(stop => stop()); if (frameId) root.cancelAnimationFrame(frameId); };
    section.update = update;
    section.tape = () => current;
    root.setTimeout(layout, 0);
    return section;
  }

  /* ------------------------------------------------------- capture sheet */
  function sheet(rows, options = {}) {
    const grid = h("div", { class: "scope-sheet" });
    for (const row of list(rows).slice(0, options.limit || 10)) {
      const source = localPath(row.thumbnail_url);
      if (!source) continue;
      const address = [row.address, row.port].filter(value => value != null && value !== "").join(":");
      const family = familyOf(list(row.technologies).map(item => item.category).find(key => key && key !== "other") || "other");
      const title = text(row.title, address || "Saved capture");
      // A drawn evidence document is labelled so it is never read as a screenshot.
      const badge = row.capture_class && row.capture_class !== "browser" ? root.LeakScopeEvidence?.classBadge(row.capture_class, row.capture_label) : null;
      grid.append(h("a", { class: "scope-shot", href: "/screenshots/?q=" + encodeURIComponent(row.address || ""), style: "--family:" + family.color,
        "aria-label": (badge ? row.capture_label + ": " : "") + title + (address ? ", " + address : "") },
        badge ? h("span", { class: "ev-class-overlay" }, badge) : null,
        h("img", { src: source, alt: "", loading: "lazy", decoding: "async" }),
        h("span", { class: "scope-shot-caption" }, h("strong", {}, title), h("small", { class: "scope-mono" }, address))));
    }
    if (!grid.children.length) grid.append(h("p", { class: "scope-empty" }, "No retained captures yet. Screenshots appear here after a capture job."));
    return grid;
  }

  /* --------------------------------------------------------- job history */
  const STATES = {
    succeeded: { label: "Succeeded", tone: "good" }, failed: { label: "Failed", tone: "critical" }, cancelled: { label: "Cancelled", tone: "muted" },
    running: { label: "Running", tone: "live" }, queued: { label: "Queued", tone: "wait" }, paused: { label: "Paused", tone: "wait" },
  };
  const stateOf = run => {
    const raw = String(run.state || run.status || "queued").toLowerCase();
    const key = { success: "succeeded", failure: "failed", revoked: "cancelled", canceled: "cancelled", started: "running", pending: "queued" }[raw] || raw;
    return { key, ...(STATES[key] || { label: key.replaceAll("_", " "), tone: "muted" }) };
  };
  const runName = run => { const name = text(run.kind || run.type, "job").replaceAll("_", " "); return name.charAt(0).toUpperCase() + name.slice(1); };
  function seconds(run) {
    const a = Date.parse(run.started_at || run.created_at), b = Date.parse(run.finished_at || run.updated_at);
    return Number.isFinite(a) && Number.isFinite(b) && b >= a ? (b - a) / 1000 : null;
  }
  const duration = value => value == null ? null : value < 1 ? "under a second" : value < 90 ? Math.round(value) + " s" : value < 5400 ? Math.round(value / 60) + " min" : (value / 3600).toFixed(1) + " h";
  function runTip(run, state) {
    return tipBody(runName(run), [state.label + (run.provider ? " · " + text(run.provider) : ""), ago(run.created_at), duration(seconds(run)) ? "ran for " + duration(seconds(run)) : null]);
  }
  /* Compact row of marks for a handful of jobs. */
  function pulses(runs, options = {}) {
    const rows = list(runs).slice(0, options.limit || 12).reverse();
    const strip = h("div", { class: "scope-pulses", role: "list", "aria-label": "Latest jobs, oldest first" });
    const peak = Math.max(1, ...rows.map(run => Math.log10(1 + (seconds(run) || 0))));
    for (const run of rows) {
      const state = stateOf(run), size = 26 + 44 * Math.log10(1 + (seconds(run) || 0)) / peak;
      const mark = h("a", { class: "scope-pulse", role: "listitem", href: "/runs/", "data-tone": state.tone, style: "--h:" + size.toFixed(0) + "%",
        "aria-label": runName(run) + ", " + state.label + ", " + ago(run.created_at) });
      hoverTip(mark, () => runTip(run, state));
      strip.append(mark);
    }
    if (!rows.length) strip.append(h("p", { class: "scope-empty" }, "No jobs have run yet."));
    return strip;
  }
  /* Swimlane history: one lane per kind of job, time left to right. Jobs arrive in bursts
   * separated by long quiet spells, so quiet time is compressed; every lane shares the scale. */
  function timeline(runs) {
    const rows = list(runs).filter(run => Number.isFinite(Date.parse(run.created_at))).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
    const figure = h("figure", { class: "scope-timeline" });
    if (!rows.length) return figure;
    const position = new Map(), breaks = [];
    let cursor = 0, before = null;
    for (const run of rows) {
      const at = Date.parse(run.created_at);
      if (before != null) {
        const quiet = at - before;
        cursor += 0.22 + 1.6 * (1 - Math.exp(-quiet / 600000));
        if (quiet > 3 * 3600000) { cursor += 1.4; breaks.push({ at: cursor - 1.1, quiet }); }
      }
      position.set(run, cursor); before = at;
    }
    const extent = cursor || 1, percent = value => (value / extent * 100).toFixed(3) + "%";
    const lanes = new Map();
    for (const run of rows) { const name = runName(run); if (!lanes.has(name)) lanes.set(name, []); lanes.get(name).push(run); }
    const tally = new Map();
    const longest = Math.max(1, ...rows.map(run => Math.log10(1 + (seconds(run) || 0))));
    for (const [name, items] of [...lanes].sort((a, b) => b[1].length - a[1].length)) {
      const track = h("div", { class: "scope-lane-track", role: "list", "aria-label": name + " jobs" });
      for (const gap of breaks) track.append(h("i", { class: "scope-lane-break", style: "left:" + percent(gap.at), "aria-hidden": "true" }));
      for (const run of items) {
        const state = stateOf(run); tally.set(state.key, (tally.get(state.key) || 0) + 1);
        const mark = h("a", { class: "scope-lane-mark", role: "listitem", href: "/runs/?id=" + encodeURIComponent(run.id || ""), "data-tone": state.tone,
          style: "left:" + percent(position.get(run)) + ";height:" + (7 + 11 * Math.log10(1 + (seconds(run) || 0)) / longest).toFixed(1) + "px",
          "aria-label": name + ", " + state.label + ", " + ago(run.created_at) });
        hoverTip(mark, () => runTip(run, state));
        track.append(mark);
      }
      figure.append(h("div", { class: "scope-lane" }, h("span", { class: "scope-lane-name" }, name, h("small", {}, count(items.length))), track));
    }
    const stamp = value => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    figure.append(h("div", { class: "scope-lane scope-lane-axis" }, h("span", { class: "scope-hint" }, "Quiet spells are compressed. Taller marks ran longer."),
      h("div", { class: "scope-axis", "aria-hidden": "true" }, h("span", {}, stamp(Date.parse(rows[0].created_at))), h("span", {}, stamp(Date.parse(rows[rows.length - 1].created_at))))));
    figure.prepend(h("figcaption", { class: "scope-legend" }, [...tally].map(([key, total]) => {
      const state = STATES[key] || { label: key, tone: "muted" };
      return h("span", { class: "scope-legend-item", "data-tone": state.tone }, h("i", {}), state.label + " ", h("strong", {}, count(total)));
    })));
    return figure;
  }

  /* ----------------------------------------------- relationship constellation */
  /* Saved assets as points, evidence-backed relationships as lines. Kind is
   * carried by shape as well as hue, so it never depends on colour alone. */
  const KINDS = {
    domain: { label: "Domains", color: "#3987e5", shape: "circle" },
    certificate: { label: "Certificates", color: "#c98500", shape: "diamond" },
    ip: { label: "IP addresses", color: "#199e70", shape: "square" },
    organization: { label: "Organizations", color: "#d55181", shape: "hexagon" },
    identifier: { label: "Public identifiers", color: "#9085e9", shape: "triangle" },
  };
  const kindOf = key => KINDS[key] || { label: text(key, "Other").replaceAll("_", " "), color: "#7f8da6", shape: "circle" };
  function trace(ctx, shape, x, y, r) {
    ctx.beginPath();
    if (shape === "square") ctx.rect(x - r * 0.86, y - r * 0.86, r * 1.72, r * 1.72);
    else if (shape === "circle") ctx.arc(x, y, r, 0, TAU);
    else {
      const sides = shape === "triangle" ? 3 : shape === "hexagon" ? 6 : 4, reach = shape === "triangle" ? r * 1.25 : shape === "diamond" ? r * 1.18 : r * 1.08;
      for (let i = 0; i < sides; i++) { const a = -Math.PI / 2 + i * TAU / sides; i ? ctx.lineTo(x + Math.cos(a) * reach, y + Math.sin(a) * reach) : ctx.moveTo(x + Math.cos(a) * reach, y + Math.sin(a) * reach); }
      ctx.closePath();
    }
  }
  function graph(options = {}) {
    if (!canDraw()) return null;
    const source = list(options.nodes), ids = new Map();
    const nodes = source.map((item, index) => { const node = { item, index, id: item.id, kind: kindOf(item.kind), links: [], x: 0, y: 0 }; ids.set(item.id, node); return node; });
    const edges = list(options.edges).map(item => ({ item, id: item.id, a: ids.get(item.source), b: ids.get(item.target) })).filter(edge => edge.a && edge.b && edge.a !== edge.b);
    for (const edge of edges) { edge.a.links.push(edge); edge.b.links.push(edge); }
    if (!nodes.length) return null;
    // Each connected group is laid out on its own (Fruchterman–Reingold, run to rest before the
    // first paint so nothing drifts), then the groups are packed side by side. Unlinked assets
    // form a tidy block instead of being flung to the edges.
    const groups = [], seen = new Set();
    for (const node of nodes) {
      if (seen.has(node)) continue;
      const members = [], queue = [node]; seen.add(node);
      while (queue.length) { const next = queue.pop(); members.push(next); for (const edge of next.links) for (const other of [edge.a, edge.b]) if (!seen.has(other)) { seen.add(other); queue.push(other); } }
      groups.push(members);
    }
    const linked = groups.filter(members => members.length > 1).sort((a, b) => b.length - a.length), loose = groups.filter(members => members.length === 1).map(members => members[0]);
    const circles = [];
    for (const members of linked) {
      const random = seeded("group:" + members.length + ":" + text(members[0].id)), inside = new Set(members), k = 1;
      members.forEach((node, index) => { const a = index / members.length * TAU, r = 0.4 + random() * Math.sqrt(members.length) * 0.5; node.x = Math.cos(a) * r; node.y = Math.sin(a) * r; });
      const local = edges.filter(edge => inside.has(edge.a));
      for (let step = 0; step < 300; step++) {
        const heat = Math.sqrt(members.length) * 0.35 * (1 - step / 300) + 0.01;
        for (const node of members) { node.dx = -node.x * 0.02; node.dy = -node.y * 0.02; }
        for (let i = 0; i < members.length; i++) for (let j = i + 1; j < members.length; j++) {
          const a = members[i], b = members[j]; let dx = a.x - b.x, dy = a.y - b.y; const d2 = dx * dx + dy * dy || 0.0001, force = k * k / d2;
          dx *= force; dy *= force; a.dx += dx; a.dy += dy; b.dx -= dx; b.dy -= dy;
        }
        for (const edge of local) {
          const dx = edge.a.x - edge.b.x, dy = edge.a.y - edge.b.y, d = Math.sqrt(dx * dx + dy * dy) || 0.0001, force = d / k;
          edge.a.dx -= dx * force; edge.a.dy -= dy * force; edge.b.dx += dx * force; edge.b.dy += dy * force;
        }
        for (const node of members) { const d = Math.sqrt(node.dx * node.dx + node.dy * node.dy) || 0.0001, move = Math.min(d, heat); node.x += node.dx / d * move; node.y += node.dy / d * move; }
      }
      let cx = 0, cy = 0; for (const node of members) { cx += node.x; cy += node.y; } cx /= members.length; cy /= members.length;
      let reach = 0; for (const node of members) { node.x -= cx; node.y -= cy; reach = Math.max(reach, Math.hypot(node.x, node.y)); }
      const radius = 26 * Math.sqrt(members.length) + 14, scale = reach ? (radius - 12) / reach : 1;
      for (const node of members) { node.x *= scale; node.y *= scale; }
      circles.push({ members, r: radius, x: 0, y: 0 });
    }
    if (loose.length) {
      loose.sort((a, b) => a.kind.label.localeCompare(b.kind.label));
      const perRow = Math.ceil(Math.sqrt(loose.length * 1.6)), gap = 30, rows = Math.ceil(loose.length / perRow);
      loose.forEach((node, index) => { node.x = (index % perRow - (perRow - 1) / 2) * gap; node.y = (Math.floor(index / perRow) - (rows - 1) / 2) * gap; });
      circles.push({ members: loose, r: Math.hypot(perRow, rows) * gap / 2 + 18, x: 0, y: 0, loose: true });
    }
    // Greedy packing along a wide spiral, largest group first.
    const placed = [];
    for (const circle of circles) {
      let angle = 0, distance = 0;
      for (let attempt = 0; attempt < 4000; attempt++) {
        const x = Math.cos(angle) * distance * 1.9, y = Math.sin(angle) * distance;
        if (placed.every(other => Math.hypot(other.x - x, other.y - y) >= other.r + circle.r + 18)) { circle.x = x; circle.y = y; break; }
        angle += 0.35; distance += 2.2;
      }
      placed.push(circle);
      for (const node of circle.members) { node.x += circle.x; node.y += circle.y; }
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const node of nodes) { minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x); minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y); node.r = 4 + Math.min(7, Math.sqrt(node.links.length) * 1.7); }
    const spanX = maxX - minX || 1, spanY = maxY - minY || 1;
    const named = new Set(nodes.slice().sort((a, b) => b.links.length - a.links.length).slice(0, 14).filter(node => node.links.length > 1));

    const canvas = h("canvas", { class: "scope-graph-canvas", role: "img", "aria-label": "Constellation of " + count(nodes.length) + " saved assets and " + count(edges.length) + " relationships. The tables below list each one." });
    const tally = new Map();
    for (const node of nodes) tally.set(node.kind, (tally.get(node.kind) || 0) + 1);
    const legend = h("figcaption", { class: "scope-legend scope-graph-legend" }, [...tally].sort((a, b) => b[1] - a[1]).map(([kind, total]) =>
      h("span", { class: "scope-legend-item" }, h("i", { class: "scope-shape scope-shape-" + kind.shape, style: "--family:" + kind.color }), kind.label + " ", h("strong", {}, count(total)))),
      h("span", { class: "scope-hint" }, "Select a point or line to inspect its evidence. Drag a point to rearrange."));
    const figure = h("figure", { class: "scope-graph" }, legend, canvas);
    const ctx = canvas.getContext("2d");
    let width = 0, height = 0, dpr = 1, hover = null, hoverEdge = null, selected = null, drag = null, moved = false, queued = false;
    const pad = 46;
    let zoom = 1, shiftX = 0, shiftY = 0;
    const place = node => { node.px = shiftX + (node.x - minX) * zoom; node.py = shiftY + (node.y - minY) * zoom; };
    function draw() {
      queued = false;
      if (!width) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
      const focus = hover || (selected && selected.links ? selected : null), near = new Set();
      if (focus) { near.add(focus); for (const edge of focus.links) { near.add(edge.a); near.add(edge.b); } }
      const lit = edge => edge === hoverEdge || edge === selected || (focus && (edge.a === focus || edge.b === focus));
      ctx.lineCap = "round";
      for (const edge of edges) {
        const on = lit(edge);
        ctx.beginPath(); ctx.moveTo(edge.a.px, edge.a.py); ctx.lineTo(edge.b.px, edge.b.py);
        ctx.strokeStyle = on ? "rgba(176,244,232,.95)" : focus || hoverEdge ? "rgba(120,150,180,.1)" : "rgba(128,160,192,.3)";
        ctx.lineWidth = on ? 1.8 : 1; ctx.setLineDash(edge.item.historical || edge.item.expired ? [4, 4] : []); ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const node of nodes) {
        const dim = focus && !near.has(node);
        ctx.globalAlpha = dim ? 0.2 : 1;
        trace(ctx, node.kind.shape, node.px, node.py, node.r);
        ctx.fillStyle = node.kind.color; ctx.fill();
        ctx.lineWidth = node === hover || node === selected ? 2 : 1.25; ctx.strokeStyle = node === hover || node === selected ? "#f2f8ff" : "#070c15"; ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.font = "600 12px 'Sofia Sans', system-ui, sans-serif"; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
      const drawn = [];
      const write = node => {
        const label = text(node.item.label || node.item.value, "Saved asset"), shown = label.length > 34 ? label.slice(0, 33) + "…" : label;
        const w = ctx.measureText(shown).width, left = node.px + node.r + 7 + w > width - 4, x = left ? node.px - node.r - 7 - w : node.px + node.r + 7, box = [x - 2, node.py - 9, x + w + 2, node.py + 9];
        if (node !== hover && node !== selected && drawn.some(other => box[0] < other[2] && box[2] > other[0] && box[1] < other[3] && box[3] > other[1])) return;
        drawn.push(box);
        ctx.strokeStyle = "rgba(7,12,21,.92)"; ctx.lineWidth = 4; ctx.strokeText(shown, x, node.py);
        ctx.fillStyle = node === hover || node === selected ? "#ffffff" : "#d6e2f0"; ctx.fillText(shown, x, node.py);
      };
      if (hover) write(hover);
      if (selected && selected.links && selected !== hover) write(selected);
      for (const node of focus ? near : named) if (node !== hover && node !== selected) write(node);
    }
    const paint = () => { if (!queued) { queued = true; root.requestAnimationFrame(draw); } };
    function layout() {
      const box = canvas.getBoundingClientRect();
      if (!box.width) return;
      dpr = Math.min(2, root.devicePixelRatio || 1); width = box.width; height = box.height;
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
      zoom = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY, 2.4);
      shiftX = (width - spanX * zoom) / 2; shiftY = (height - spanY * zoom) / 2;
      nodes.forEach(place); paint();
    }
    function pick(event) {
      const box = canvas.getBoundingClientRect(), x = event.clientX - box.left, y = event.clientY - box.top;
      let node = null, best = 15 * 15;
      for (const item of nodes) { const d = (item.px - x) ** 2 + (item.py - y) ** 2; if (d < best) { best = d; node = item; } }
      if (node) return { node };
      let edge = null, reach = 6;
      for (const item of edges) {
        const ax = item.a.px, ay = item.a.py, bx = item.b.px - ax, by = item.b.py - ay, t = clamp(((x - ax) * bx + (y - ay) * by) / (bx * bx + by * by || 1), 0, 1);
        const d = Math.hypot(x - ax - bx * t, y - ay - by * t);
        if (d < reach) { reach = d; edge = item; }
      }
      return { edge };
    }
    canvas.addEventListener("pointermove", event => {
      if (drag) {
        const box = canvas.getBoundingClientRect(); moved = true;
        drag.x = minX + (clamp(event.clientX - box.left, 8, width - 8) - shiftX) / zoom; drag.y = minY + (clamp(event.clientY - box.top, 8, height - 8) - shiftY) / zoom; place(drag); paint(); return;
      }
      const found = pick(event);
      if (found.node !== hover || found.edge !== hoverEdge) {
        hover = found.node || null; hoverEdge = found.edge || null; canvas.style.cursor = hover || hoverEdge ? "pointer" : "";
        if (hover) tip.show(tipBody(hover.item.label || hover.item.value, [hover.kind.label.replace(/s$/, ""), count(hover.links.length) + (hover.links.length === 1 ? " relationship" : " relationships")]), event.clientX, event.clientY);
        else if (hoverEdge) tip.show(tipBody(text(hoverEdge.item.relation, "related").replaceAll("_", " "), [text(hoverEdge.a.item.label || hoverEdge.a.item.value) + " to " + text(hoverEdge.b.item.label || hoverEdge.b.item.value), hoverEdge.item.evidence_source ? "Source: " + text(hoverEdge.item.evidence_source) : null]), event.clientX, event.clientY);
        else tip.hide();
        paint();
      } else tip.move(event.clientX, event.clientY);
    });
    canvas.addEventListener("pointerdown", event => { const found = pick(event); moved = false; if (found.node) { drag = found.node; canvas.setPointerCapture?.(event.pointerId); tip.hide(); } });
    canvas.addEventListener("pointerup", event => {
      const dragged = drag && moved; drag = null;
      if (dragged) return;
      const found = pick(event);
      if (found.node) { selected = found.node; paint(); if (typeof options.onNode === "function") options.onNode(found.node.item); }
      else if (found.edge) { selected = found.edge; paint(); if (typeof options.onEdge === "function") options.onEdge(found.edge.item); }
    });
    canvas.addEventListener("pointerleave", () => { if (!drag) { hover = null; hoverEdge = null; tip.hide(); paint(); } });
    onResize(canvas, layout);
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(paint).catch(() => {});
    root.setTimeout(layout, 0);
    return { element: figure, select(id) { selected = ids.get(id) || edges.find(edge => edge.id === id) || null; paint(); } };
  }

  /* ------------------------------------------------------------ overview */
  function block(title, href, more, ...children) {
    return h("section", { class: "scope-block" },
      h("header", { class: "scope-block-head" }, h("h2", {}, href ? h("a", { href }, title) : title), more || null), children);
  }
  function overview(parts = {}) {
    const summary = parts.summary || {}, controller = typeof root.AbortController === "function" ? new root.AbortController() : null;
    const signal = controller ? controller.signal : undefined;
    const open = point => {
      if (root.LeakScopeInspector && typeof root.LeakScopeInspector.open === "function" && point.id) root.LeakScopeInspector.open({ type: "endpoint", id: point.id });
      else if (point.id) root.location.assign("/assets/?endpoint=" + encodeURIComponent(point.id));
    };
    const scope = dial({ status: "Locating saved addresses…", onSelect: open });
    const continents = h("ul", { class: "scope-continents", "aria-label": "Located addresses by continent" });
    const legendHost = h("div", { class: "scope-family-host" }, h("p", { class: "scope-empty", role: "status" }, "Reading saved technologies…"));
    const starsHost = h("div", { class: "scope-stars-host" });
    const sheetHost = h("div", { class: "scope-sheet-host" });
    const captureCount = h("a", { class: "scope-more", href: "/screenshots/" }, "Open the library");
    const feed = stream();
    let stars = null;
    const filter = key => { scope.setFilter(key); if (stars && stars.setFilter) stars.setFilter(key); };

    const hero = h("section", { class: "scope-hero", "aria-label": "Saved exposure at a glance" },
      h("div", { class: "scope-hero-dial" }, scope.element, continents),
      h("div", { class: "scope-hero-side" }, parts.metrics || null,
        block("What is exposed", "/technology-dashboard/", null, legendHost), feed));
    const techBand = block("Technologies in saved evidence", "/technology-dashboard/", h("span", { class: "scope-hint" }, "Circle area is saved endpoints. Select one to open its observations."), starsHost);
    techBand.classList.add("scope-band"); techBand.hidden = true;
    const lower = h("div", { class: "scope-band scope-band-split" },
      block("Latest captures", "/screenshots/", captureCount, sheetHost),
      block("Latest jobs", "/runs/", h("a", { class: "scope-more", href: "/runs/" }, "Job history"), pulses(summary.recent_runs)));

    const land = doc.body.dataset.countries || "/static/maps/countries.geojson";
    getJSON(land, signal).then(data => scope.setLand(outlines(data))).catch(() => {});
    getJSON("/api/atlas/geography?limit=2000", signal).then(data => {
      const families = new Map(list(data.filter_choices && data.filter_choices.technologies).map(row => [row.key, row]));
      const rows = list(data.features).map(feature => {
        const info = feature.properties || {}, position = feature.geometry && feature.geometry.coordinates, known = families.get(info.technology);
        return { id: info.endpoint_id || info.id, lon: position && position[0], lat: position && position[1], count: info.endpoint_count,
          family: known ? known.category : "other", title: text(info.title, info.address),
          address: [info.address, info.port].filter(value => value != null && value !== "").join(":"),
          technology: known ? known.label : text(info.technology, ""), place: [info.city, info.country_name].filter(Boolean).join(", ") };
      });
      scope.setPoints(rows);
      const counts = data.counts || {};
      scope.setStatus(rows.length ? count(rows.length) + " located addresses" + (finite(counts.unlocated) && counts.unlocated > 0 ? ", " + count(counts.unlocated) + " without a location" : "") : "No located addresses yet");
      scope.element.querySelector("canvas").setAttribute("aria-label", "Polar map of " + count(rows.length) + " located saved addresses. The geography page lists them by country.");
      const peak = Math.max(1, ...list(data.continents).map(row => Number(row.count) || 0));
      continents.replaceChildren(...list(data.continents).map(row => h("li", {}, h("a", { href: "/geography/" },
        h("strong", {}, count(row.count)), h("span", {}, text(row.name, "Unknown")), h("i", { style: "width:" + (Number(row.count) / peak * 100).toFixed(1) + "%", "aria-hidden": "true" })))));
    }).catch(error => { if (error.name !== "AbortError") scope.setStatus("The map of saved addresses is unavailable right now."); });
    getJSON("/api/atlas/technologies", signal).then(data => {
      legendHost.replaceChildren(list(data.categories).length ? families(data.categories, { onFilter: filter }) : h("p", { class: "scope-empty" }, "Technology families appear after the first saved result."));
      if (list(data.groups).length) { stars = constellation(data); starsHost.replaceChildren(stars); techBand.hidden = false; }
    }).catch(error => { if (error.name !== "AbortError") legendHost.replaceChildren(h("p", { class: "scope-empty" }, "Technology families are unavailable right now.")); });
    getJSON("/api/atlas/screenshots?limit=10&capture_class=captured", signal).then(data => {
      sheetHost.replaceChildren(sheet(data.results, { limit: 10 }));
      if (finite(data.total_visible_count) && data.total_visible_count > 0) captureCount.textContent = "All " + count(data.total_visible_count) + " captures";
    }).catch(error => { if (error.name !== "AbortError") sheetHost.replaceChildren(h("p", { class: "scope-empty" }, "Captures are unavailable right now.")); });
    feed.start();
    root.addEventListener("pagehide", () => { scope.destroy(); if (controller) controller.abort(); }, { once: true });
    return [hero, techBand, lower];
  }

  /* --------------------------------------------- quiet long explanations */
  /* Long provenance notes stay one click away instead of leading every page. */
  const NOTES = ".atlas-coverage,.workspace-coverage,.ls-viz-coverage";
  function tidy(container) {
    for (const note of container.querySelectorAll(".scope-note")) if (!note.querySelector(NOTES)) note.remove();
    for (const note of container.querySelectorAll(NOTES)) {
      if (note.closest(".scope-note") || (note.textContent || "").trim().length < 150) continue;
      const wrap = h("details", { class: "scope-note" }, h("summary", {}, "About this data"));
      note.replaceWith(wrap); wrap.append(note);
    }
  }
  function watch() {
    const page = doc.getElementById("page-root");
    if (!page || typeof root.MutationObserver !== "function") return;
    let queued = false;
    const run = () => { queued = false; tidy(page); };
    new root.MutationObserver(() => { if (!queued) { queued = true; root.setTimeout(run, 60); } }).observe(page, { childList: true, subtree: true, characterData: true });
    run();
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", watch); else watch();

  root.LeakScopeScope = { FAMILIES, familyOf, dial, outlines, families, constellation, stream, streamTape, sheet, pulses, timeline, graph, overview, tip, tidy };
})(typeof window !== "undefined" ? window : globalThis);
