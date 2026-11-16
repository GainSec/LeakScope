(function (root) {
  "use strict";
  const MAX_NODES = 150, MAX_EDGES = 500, MATRIX_SIZE = 60;
  const laneOrder = ["organization", "domain", "ip", "network", "asn", "service", "certificate", "identifier"];
  const names = { organization: "Organizations", domain: "Domains", ip: "IP addresses", network: "Netblocks", asn: "AS numbers", service: "Services", certificate: "Certificates", identifier: "Public identifiers" };
  let instance = 0;
  const list = value => Array.isArray(value) ? value : [];
  const text = value => String(value == null ? "" : value);
  const assetName = node => text(node.label || node.value || node.id);
  const relationName = edge => text(edge.relation || "related").replace(/_/g, " ");
  function element(tag, className, ...children) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    children.flat(Infinity).forEach(child => {
      if (child != null) item.append(child instanceof root.Node ? child : document.createTextNode(text(child)));
    });
    return item;
  }
  function svg(tag, attributes, content) {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes || {}).forEach(([key, value]) => item.setAttribute(key, text(value)));
    if (content != null) item.textContent = text(content);
    return item;
  }
  function button(className, label, callback) {
    const item = element("button", className, label);
    item.type = "button";
    item.addEventListener("click", callback);
    return item;
  }
  function timestamp(value) {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : "Unknown";
  }
  function create(options = {}) {
    const suppliedNodes = list(options.nodes), suppliedEdges = list(options.edges);
    const byId = new Map();
    suppliedNodes.slice(0, MAX_NODES).forEach(node => { if (node && node.id != null && !byId.has(node.id)) byId.set(node.id, node); });
    const nodes = [...byId.values()].sort((a, b) => {
      const rank = n => laneOrder.includes(n.kind) ? laneOrder.indexOf(n.kind) : laneOrder.length;
      return rank(a) - rank(b) || text(a.kind).localeCompare(text(b.kind)) || assetName(a).localeCompare(assetName(b)) || text(a.id).localeCompare(text(b.id));
    });
    const edgeIds = new Set();
    const edges = suppliedEdges.slice(0, MAX_EDGES).filter(edge => {
      if (!edge || edge.id == null || edgeIds.has(edge.id) || !byId.has(edge.source) || !byId.has(edge.target)) return false;
      edgeIds.add(edge.id); return true;
    });
    const edgeById = new Map(edges.map(edge => [edge.id, edge]));
    const kinds = [...new Set(nodes.map(node => text(node.kind) || "other"))];
    const elementRoot = element("section", "ls-rel-map");
    const controlId = "ls-rel-" + (++instance);
    let view = "lanes", selectedId = null, matches = new Set(), shownNodes = nodes, shownEdges = edges, matrixIds = new Set();
    const tabs = element("div", "ls-rel-views"); tabs.setAttribute("aria-label", "Relationship view");
    const search = element("input", "input ls-rel-search"); search.type = "search"; search.id = controlId + "-search";
    search.placeholder = "Search asset name, address or kind";
    const searchLabel = element("label", "ls-rel-search-label", "Find in saved graph", search);
    searchLabel.htmlFor = search.id;
    const clear = button("btn btn-small ls-rel-clear", "Clear", () => { search.value = ""; filter(); search.focus(); });
    clear.dataset.relClear = "";
    const count = element("p", "ls-rel-counts"); count.setAttribute("role", "status");
    const bounded = element("p", "ls-rel-bounds");
    if (suppliedNodes.length > MAX_NODES || suppliedEdges.length > MAX_EDGES) {
      bounded.textContent = nodes.length + " of " + suppliedNodes.length + " supplied assets · " + edges.length + " of " + suppliedEdges.length + " supplied relationships. Narrow the graph to inspect more.";
    } else bounded.hidden = true;
    const canvas = element("div", "ls-rel-canvas");
    const selection = element("div", "ls-rel-selection"); selection.setAttribute("aria-live", "polite");
    const laneButton = button("btn btn-small", "Relationship lanes", () => changeView("lanes")); laneButton.dataset.relView = "lanes";
    const matrixButton = button("btn btn-small", "Overlap matrix", () => changeView("matrix")); matrixButton.dataset.relView = "matrix";
    tabs.append(laneButton, matrixButton);
    elementRoot.append(element("div", "ls-rel-toolbar", tabs, element("div", "ls-rel-find", searchLabel, clear)), count, bounded, canvas, selection,
      element("p", "ls-rel-note", "Saved associations do not establish ownership or current reachability. Shared IPs and certificates may connect unrelated assets. Historical evidence is marked separately from expired evidence."));

    function notifyNode(node) { select(node.id); if (typeof options.onNode === "function") options.onNode(node); }
    function notifyEdge(edge) { select(edge.id); if (typeof options.onEdge === "function") options.onEdge(edge); }
    function adjacent(nodeId) { return edges.filter(edge => edge.source === nodeId || edge.target === nodeId); }
    function edgeTitle(edge) {
      return assetName(byId.get(edge.source)) + " → " + relationName(edge) + " → " + assetName(byId.get(edge.target));
    }
    function evidenceOption(edge) {
      const item = button("ls-rel-evidence-option", "", () => notifyEdge(edge));
      item.dataset.relEvidence = text(edge.id);
      item.append(element("span", "ls-rel-evidence-route", edgeTitle(edge)), element("span", "ls-rel-evidence-meta", text(edge.evidence_source || "Unknown source") +
        (edge.confidence != null ? " · Confidence " + text(edge.confidence) + "/100" : "") +
        (edge.expired ? " · Expired" : "") + (edge.historical ? " · Historical" : "")));
      if (edge.observed_at || edge.expires_at) item.append(element("span", "ls-rel-evidence-meta", (edge.observed_at ? "Observed " + timestamp(edge.observed_at) : "") +
        (edge.expires_at ? " · Expires " + timestamp(edge.expires_at) : "")));
      return item;
    }
    function showOptions(items, heading) {
      selection.replaceChildren(element("h3", "ls-rel-selection-title", heading), element("p", "ls-rel-selection-hint", "Choose a saved relationship to inspect its source evidence."));
      const content = element("div", "ls-rel-evidence-list"); items.forEach(edge => content.append(evidenceOption(edge)));
      selection.append(content);
    }
    function drawSelection() {
      const focused = document.activeElement;
      const ownedFocus = focused && focused !== selection && selection.contains(focused);
      const node = byId.get(selectedId), edge = edgeById.get(selectedId);
      if (node) {
        const linked = adjacent(node.id);
        selection.replaceChildren(element("h3", "ls-rel-selection-title", assetName(node)), element("p", "ls-rel-selection-hint", text(node.kind) + " · " + text(node.value)));
        if (linked.length) {
          selection.append(element("p", "ls-rel-selection-hint", linked.length + " saved relationship" + (linked.length === 1 ? "" : "s") + ". Arrows show source → target; select a row to inspect its evidence."));
          const content = element("div", "ls-rel-evidence-list"); linked.forEach(item => content.append(evidenceOption(item))); selection.append(content);
        } else selection.append(element("p", "ls-rel-selection-hint", "No saved relationships for this asset in the loaded graph."));
      } else if (edge) {
        selection.replaceChildren(element("h3", "ls-rel-selection-title", edgeTitle(edge)),
          element("p", "ls-rel-selection-hint", (edge.historical ? "Saved historical evidence" : "Saved evidence") + " · " + text(edge.evidence_source || "Unknown source") +
            (edge.confidence != null ? " · Confidence " + text(edge.confidence) + "/100" : "") + (edge.expired ? " · Expired" : "")));
        if (edge.observed_at || edge.expires_at) selection.append(element("p", "ls-rel-selection-hint", (edge.observed_at ? "Observed " + timestamp(edge.observed_at) : "") +
          (edge.expires_at ? " · Expires " + timestamp(edge.expires_at) : "")));
      } else selection.replaceChildren(element("p", "ls-rel-selection-hint", "Select an asset to highlight its directed relationships, or choose a connection to inspect saved evidence."));
      if (ownedFocus && !focused.isConnected) {
        const target = selection.querySelector(".ls-rel-selection-title") || selection;
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }
    }
    function updateSelection() {
      const selectedEdge = edgeById.get(selectedId), selectedNode = byId.get(selectedId);
      const adjacentIds = new Set(selectedNode ? adjacent(selectedId).map(edge => edge.id) : selectedEdge ? [selectedEdge.id] : []);
      elementRoot.querySelectorAll("[data-rel-node]").forEach(item => {
        const id = item.dataset.relNode;
        item.classList.toggle("is-selected", id === selectedId);
        item.classList.toggle("is-neighbor", Boolean(selectedEdge && (id === selectedEdge.source || id === selectedEdge.target)) || Boolean(selectedNode && adjacent(selectedId).some(edge => edge.source === id || edge.target === id)));
        item.setAttribute("aria-pressed", String(id === selectedId));
      });
      elementRoot.querySelectorAll("[data-rel-edge]").forEach(item => {
        item.classList.toggle("is-adjacent", adjacentIds.has(item.dataset.relEdge));
        item.classList.toggle("is-selected", item.dataset.relEdge === selectedId);
        item.classList.toggle("is-muted", selectedId != null && !adjacentIds.has(item.dataset.relEdge));
      });
      elementRoot.querySelectorAll("[data-rel-source]").forEach(item => {
        const chosen = selectedEdge ? item.dataset.relSource === selectedEdge.source && item.dataset.relTarget === selectedEdge.target :
          selectedNode ? item.dataset.relSource === selectedId || item.dataset.relTarget === selectedId : false;
        item.classList.toggle("is-selected", chosen);
      });
      drawSelection();
    }
    function select(id) {
      selectedId = byId.has(id) || edgeById.has(id) ? id : null;
      const edge = edgeById.get(selectedId);
      const outside = byId.has(selectedId) ? !matrixIds.has(selectedId) : edge && (!matrixIds.has(edge.source) || !matrixIds.has(edge.target));
      if (view === "matrix" && outside) draw(); else updateSelection();
    }
    function drawLanes() {
      const visibleIds = new Set(shownNodes.map(node => node.id));
      const laneKinds = kinds.filter(kind => shownNodes.some(node => (text(node.kind) || "other") === kind));
      // Lanes share the space there is, so the board never needs sideways scrolling: columns narrow to a floor,
      // and below that the whole board is scaled to fit.
      const room = canvas.clientWidth ? canvas.clientWidth - 2 : 0;
      const pitch = room ? Math.max(150, Math.min(264, Math.floor((room - 24) / Math.max(1, laneKinds.length)))) : 264;
      const cardWidth = pitch - 44;
      const width = Math.max(laneKinds.length * pitch + 24, room ? Math.min(552, room) : 552), cardHeight = 90, top = 152;
      lanesRoom = room;
      const positions = new Map();
      const maxRows = Math.max(1, ...laneKinds.map(kind => shownNodes.filter(node => (text(node.kind) || "other") === kind).length));
      const height = Math.max(280, maxRows * cardHeight + top + 20);
      const scroll = element("div", "ls-rel-scroll"); scroll.tabIndex = 0; scroll.setAttribute("aria-label", "Relationship lanes. Scroll to explore asset columns.");
      const board = element("div", "ls-rel-board"); board.style.width = width + "px"; board.style.height = height + "px";
      if (room && width > room) board.style.zoom = String(Math.floor(room / width * 1000) / 1000);
      laneKinds.forEach((kind, laneIndex) => {
        const lane = element("div", "ls-rel-lane"); lane.dataset.kind = kind;
        lane.style.left = (laneIndex * pitch + 12) + "px"; lane.style.width = (pitch - 16) + "px"; lane.style.height = height + "px";
        const allKindNodes = shownNodes.filter(node => (text(node.kind) || "other") === kind);
        const inLane = allKindNodes.filter(node => visibleIds.has(node.id));
        lane.append(element("h3", "ls-rel-lane-title", names[kind] || kind, element("span", "ls-rel-lane-count", inLane.length)));
        allKindNodes.forEach((node, index) => {
          if (!visibleIds.has(node.id)) return;
          const x = laneIndex * pitch + 28, y = top + index * cardHeight;
          positions.set(node.id, { x, y, width: cardWidth, height: 80 });
          const card = button("ls-rel-node", "", () => notifyNode(node));
          card.dataset.relNode = text(node.id); card.dataset.graphNode = text(node.id); card.dataset.kind = kind;
          card.style.left = x + "px"; card.style.top = y + "px"; card.style.width = cardWidth + "px";
          card.title = text(node.kind) + ": " + assetName(node);
          card.setAttribute("aria-label", card.title);
          if (matches.has(node.id)) card.classList.add("is-match");
          card.append(element("span", "ls-rel-node-name", assetName(node)), element("span", "ls-rel-node-kind", text(node.kind) + " · " + adjacent(node.id).length + " connection" + (adjacent(node.id).length === 1 ? "" : "s")));
          board.append(card);
        });
        board.prepend(lane);
      });
      // Keep lane headers in the same reading order as their visual columns.
      [...board.querySelectorAll(".ls-rel-lane")].reverse().forEach(lane => board.insertBefore(lane, board.querySelector(".ls-rel-node")));
      const drawing = svg("svg", { class: "ls-rel-links", width, height, viewBox: "0 0 " + width + " " + height, "aria-label": "Directed saved relationships" });
      const defs = svg("defs"); const markerId = controlId + "-arrow";
      const marker = svg("marker", { id: markerId, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
      marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "context-stroke" })); defs.append(marker); drawing.append(defs);
      const labeled = new Set();
      shownEdges.forEach((edge, edgeIndex) => {
        const a = positions.get(edge.source), b = positions.get(edge.target); if (!a || !b) return;
        const sameLane = a.x === b.x, forward = b.x > a.x;
        const sx = sameLane || forward ? a.x + a.width : a.x, sy = a.y + a.height / 2;
        const tx = sameLane ? b.x + b.width : forward ? b.x : b.x + b.width, ty = b.y + b.height / 2;
        const reach = sameLane ? 42 : Math.max(45, Math.abs(tx - sx) * .45);
        const rail = 66 + (edgeIndex % 5) * 16;
        const exit = sx + (forward || sameLane ? 12 : -12), entry = tx + (forward ? -12 : 12);
        const d = sameLane
          ? "M " + sx + " " + sy + " C " + (sx + reach) + " " + (sy === ty ? sy - 30 : sy) + " " + (tx + reach) + " " + (sy === ty ? ty + 30 : ty) + " " + tx + " " + ty
          : "M " + sx + " " + sy + " H " + exit + " V " + rail + " H " + entry + " V " + ty + " H " + tx;
        const group = svg("g", { class: "ls-rel-edge" + (edge.expired ? " is-expired" : ""), "data-rel-edge": edge.id, role: "button", tabindex: 0, "aria-label": edgeTitle(edge) + " · " + text(edge.evidence_source || "Unknown source") + (edge.expired ? " · expired evidence" : "") });
        group.append(svg("title", {}, edgeTitle(edge)), svg("path", { d, class: "ls-rel-edge-hit" }), svg("path", { d, class: "ls-rel-edge-path", "marker-end": "url(#" + markerId + ")" }));
        const labelKey = JSON.stringify([edge.source, edge.target, edge.relation]);
        if (!labeled.has(labelKey)) {
          labeled.add(labelKey);
          const labelX = sameLane ? sx + 22 : (sx + tx) / 2;
          const labelY = sameLane ? (sy + ty) / 2 : rail - 5;
          group.append(svg("text", { x: labelX, y: labelY, "text-anchor": "middle", class: "ls-rel-path-label",
            ...(sameLane ? { transform: "rotate(90 " + labelX + " " + labelY + ")" } : {}) }, relationName(edge)));
        }
        group.addEventListener("click", () => notifyEdge(edge));
        group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); notifyEdge(edge); } });
        drawing.append(group);
      });
      board.prepend(drawing); scroll.append(board);
      canvas.append(element("p", "ls-rel-help", "Asset type determines the column. Select a card to highlight its paths; arrows read source → target. Scroll to explore."), scroll);
    }
    function drawMatrix() {
      const degree = new Map(shownNodes.map(node => [node.id, 0]));
      const order = new Map(shownNodes.map((node, index) => [node.id, index]));
      shownEdges.forEach(edge => { degree.set(edge.source, degree.get(edge.source) + 1); degree.set(edge.target, degree.get(edge.target) + 1); });
      const ranked = [...shownNodes].sort((a, b) => degree.get(b.id) - degree.get(a.id) || order.get(a.id) - order.get(b.id));
      const selectedEdge = edgeById.get(selectedId), chosen = new Set();
      const include = id => { if (degree.has(id) && chosen.size < MATRIX_SIZE) chosen.add(id); };
      if (selectedEdge) { include(selectedEdge.source); include(selectedEdge.target); }
      else if (byId.has(selectedId)) {
        include(selectedId);
        const neighbors = new Set();
        shownEdges.forEach(edge => { if (edge.source === selectedId) neighbors.add(edge.target); if (edge.target === selectedId) neighbors.add(edge.source); });
        ranked.filter(node => neighbors.has(node.id)).forEach(node => include(node.id));
      }
      ranked.forEach(node => include(node.id));
      const matrixNodes = shownNodes.filter(node => chosen.has(node.id)), ids = new Set(matrixNodes.map(node => node.id));
      matrixIds = ids;
      const matrixEdges = shownEdges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
      const pairs = new Map();
      matrixEdges.forEach(edge => { const key = JSON.stringify([edge.source, edge.target]); if (!pairs.has(key)) pairs.set(key, []); pairs.get(key).push(edge); });
      canvas.append(element("p", "ls-rel-help", "Rows → columns are directed saved relationships. Shared marked columns reveal common targets. Cell counts are evidence-backed relationships, not traffic or ownership."));
      canvas.append(element("p", "ls-rel-matrix-limit", matrixNodes.length + " of " + shownNodes.length + " assets · " + matrixEdges.length + " of " + shownEdges.length + " relationships in this matrix. " + (shownNodes.length > MATRIX_SIZE ? "Both axes are limited to 60 assets, prioritizing the selected asset, direct neighbors, then loaded relationship count. Search above to narrow the subset." : "Both axes use the same saved assets.")));
      const scroll = element("div", "ls-rel-matrix-scroll"); scroll.tabIndex = 0; scroll.setAttribute("aria-label", "Directed relationship matrix. Scroll to explore rows and columns.");
      const table = element("table", "ls-rel-matrix");
      // A relationship matrix is the visualization, not a saved-results disclosure.
      table.dataset.sortable = "false";
      table.dataset.collapsible = "false";
      const caption = element("caption", "ls-rel-sr", "Saved relationship overlap matrix. Source asset on each row, target asset on each column.");
      const thead = element("thead"), headRow = element("tr");
      const corner = element("td", "ls-rel-matrix-corner", "Source ↓ / Target →"); headRow.append(corner);
      matrixNodes.forEach(node => {
        const cell = element("th", "ls-rel-matrix-column"); cell.scope = "col"; cell.title = text(node.kind) + ": " + assetName(node);
        cell.append(button("ls-rel-axis", assetName(node), () => notifyNode(node))); headRow.append(cell);
      });
      thead.append(headRow); const tbody = element("tbody");
      matrixNodes.forEach(source => {
        const row = element("tr"), heading = element("th", "ls-rel-matrix-row"); heading.scope = "row"; heading.title = text(source.kind) + ": " + assetName(source);
        heading.append(button("ls-rel-axis", assetName(source), () => notifyNode(source))); row.append(heading);
        matrixNodes.forEach(target => {
          const cell = element("td"); const items = pairs.get(JSON.stringify([source.id, target.id]));
          if (items) {
            const control = button("ls-rel-matrix-cell", items.length, () => {
              if (items.length === 1) notifyEdge(items[0]);
              else showOptions(items, assetName(source) + " → " + assetName(target) + " · " + items.length + " saved relationships");
            });
            control.dataset.relSource = text(source.id); control.dataset.relTarget = text(target.id);
            control.setAttribute("aria-label", assetName(source) + " → " + assetName(target) + ": " + items.length + " saved relationship" + (items.length === 1 ? "" : "s"));
            control.title = control.getAttribute("aria-label");
            if (items.every(edge => edge.expired)) control.classList.add("is-expired");
            cell.append(control);
          } else cell.className = "ls-rel-matrix-empty";
          row.append(cell);
        });
        tbody.append(row);
      });
      table.append(caption, thead, tbody); scroll.append(table); canvas.append(scroll);
    }
    let lanesRoom = 0;
    if (typeof ResizeObserver === "function") new ResizeObserver(() => {
      if (view === "lanes" && canvas.clientWidth && Math.abs(canvas.clientWidth - 2 - lanesRoom) > 8 && canvas.querySelector(".ls-rel-board")) draw();
    }).observe(canvas);
    function draw() {
      canvas.replaceChildren();
      laneButton.setAttribute("aria-pressed", String(view === "lanes")); matrixButton.setAttribute("aria-pressed", String(view === "matrix"));
      laneButton.classList.toggle("is-active", view === "lanes"); matrixButton.classList.toggle("is-active", view === "matrix");
      const matching = matches.size, searching = Boolean(search.value.trim());
      count.textContent = searching ? matching + " match" + (matching === 1 ? "" : "es") + " · " + (shownNodes.length - matching) + " direct neighbors · " + shownEdges.length + " saved relationships in this subset" :
        shownNodes.length + " saved assets · " + shownEdges.length + " saved relationships in the loaded graph";
      if (!shownNodes.length) canvas.append(element("p", "ls-rel-empty", nodes.length ? "No saved assets match this search. Clear the search to restore the loaded graph." : "No saved relationships to map yet. Saved assets and enrichment evidence will appear here."));
      else if (view === "matrix") drawMatrix(); else drawLanes();
      updateSelection();
    }
    function filter() {
      const query = search.value.trim().toLocaleLowerCase(); matches = new Set();
      if (query) {
        nodes.forEach(node => { if ([node.kind, node.value, node.label].map(text).join(" ").toLocaleLowerCase().includes(query)) matches.add(node.id); });
        const included = new Set(matches);
        edges.forEach(edge => { if (matches.has(edge.source) || matches.has(edge.target)) { included.add(edge.source); included.add(edge.target); } });
        shownNodes = nodes.filter(node => included.has(node.id));
        shownEdges = edges.filter(edge => included.has(edge.source) && included.has(edge.target));
      } else { shownNodes = nodes; shownEdges = edges; }
      draw();
    }
    function changeView(nextView) { if (view !== nextView) { view = nextView; draw(); } }
    search.addEventListener("input", filter);
    draw();
    return { element: elementRoot, select };
  }
  root.LeakScopeRelationshipMap = { create };
})(window);
