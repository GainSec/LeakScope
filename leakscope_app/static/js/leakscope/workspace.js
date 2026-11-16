(function() {
  "use strict";
  const doc = document,
    body = doc.body;
  const read = key => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  };
  const write = (key, value) => {
    try {
      localStorage.setItem(key, String(value));
    } catch (_) {}
  };
  const media = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : {
    matches: false
  };
  let motion = read("leakscope.motion") !== "false";
  const motionButton = doc.getElementById("motion-toggle");

  function applyMotion() {
    const enabled = motion && !media.matches;
    body.classList.toggle("motion-paused", !enabled);
    if (motionButton) {
      motionButton.setAttribute("aria-pressed", String(enabled));
      motionButton.disabled = !!media.matches;
      motionButton.title = media.matches ? "Reduced motion is enabled in your device settings" : enabled ? "Pause ambient effects and transitions" : "Enable ambient effects and transitions";
      motionButton.querySelector(".motion-label")?.remove();
      const label = doc.createElement("span");
      label.className = "motion-label";
      label.textContent = media.matches ? "Reduced motion" : "Motion " + (enabled ? "on" : "off");
      motionButton.append(label);
    }
  }
  motionButton?.addEventListener("click", () => {
    motion = !motion;
    write("leakscope.motion", motion);
    applyMotion();
  });
  media.addEventListener?.("change", applyMotion);
  applyMotion();

  const iconPaths = {
    scope: "M21 12a9 9 0 1 1-9-9 M17 12a5 5 0 1 1-5-5 M12 12l7-7 M12 12h.01",
    search: "M20 20l-4-4 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
    cloud: "M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5 5 0 0 1 0 11z",
    stars: "M9 8a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0 M22 7a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0 M19 17a5 5 0 1 1-10 0 5 5 0 0 1 10 0",
    image: "M3 5h18v14H3z M3 16l5-5 4 4 3-3 6 6 M15.5 9.5h.01",
    globe: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M2 12h20 M12 2c6 5 6 15 0 20-6-5-6-15 0-20",
    note: "M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h5",
    eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
    org: "M4 21V3h11v18 M15 8h5v13 M8 7h3 M8 11h3 M8 15h3 M2 21h20",
    coverage: "M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6z M9 12l2 2 4-4",
    overlap: "M15 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0 M21 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0",
    graph: "M6 12h12 M12 4v16 M15 4a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M7 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M23 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M15 20a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
    expand: "M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6 M3 3l6 6 M21 21l-6-6",
    history: "M3 12a9 9 0 1 0 3-6.7 M3 4v5h5 M12 7v5l3 2",
    monitor: "M2 12h4l3-8 5 16 3-8h5",
    certificate: "M4 4h16v11H4z M8 8h8 M8 11h4 M14 15v6l2-1.5 2 1.5v-6",
    schedule: "M4 5h16v15H4z M4 9h16 M8 3v4 M16 3v4 M12 12v3l2 1",
    jobs: "M4 6h9 M4 12h9 M4 18h5 M16 9l5 3-5 3z",
    code: "M8 6l-6 6 6 6 M16 6l6 6-6 6",
    flask: "M9 3h6 M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3 M7.5 15h9",
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    bars: "M4 20V9h4v11 M10 20V4h4v16 M16 20v-7h4v7 M2 21h20",
    window: "M3 5h18v14H3z M3 9h18 M6.5 7h.01 M9.5 7h.01",
    compass: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M15.5 8.5l-2 5-5 2 2-5z",
    transfer: "M7 4v13 M3 8l4-4 4 4 M17 20V7 M13 16l4 4 4-4",
    gear: "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M4.9 4.9l2.1 2.1 M17 17l2.1 2.1 M4.9 19.1L7 17 M17 7l2.1-2.1"
  };
  const iconFor = {
    overview: "scope",
    discover: "search",
    cloud: "cloud",
    findings: "note",
    assets: "eye",
    organizations: "org",
    "domain-coverage": "coverage",
    relationships: "graph",
    "domain-expansion": "expand",
    monitoring: "monitor",
    certificates: "certificate",
    lookups: "schedule",
    runs: "jobs",
    queries: "code",
    research: "flask",
    catalog: "grid",
    technologies: "stars",
    screenshots: "image",
    geography: "globe",
    analysis: "bars",
    changes: "history",
    correlations: "overlap",
    preview: "window",
    explore: "compass",
    exports: "transfer",
    settings: "gear"
  };
  // Pages without a sidebar entry of their own are filed under the entry that leads to them.
  const navAlias = {detail: "findings", explore: "preview"};
  const navKey = page => navAlias[page] || page;
  doc.querySelectorAll("a[data-nav] .nav-icon").forEach(holder => {
    const path = iconPaths[iconFor[holder.closest("a").dataset.nav] || "note"];
    const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg"),
      shape = doc.createElementNS("http://www.w3.org/2000/svg", "path");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    shape.setAttribute("d", path);
    svg.append(shape);
    holder.replaceChildren(svg);
  });

  const crumbGroup = doc.getElementById("breadcrumb-group");
  if (crumbGroup) crumbGroup.textContent = "Workspace";
  const compactButton = doc.getElementById("nav-compact-toggle");
  const navPreferences = new Map();
  const navGroups = [...doc.querySelectorAll("[data-nav-group]")];
  const compactActive = () => body.classList.contains("nav-compact") && innerWidth > 800;
  navGroups.forEach(group => {
    const key = "leakscope.nav.group." + group.dataset.navGroup;
    const active = [...group.querySelectorAll("[data-nav]")].some(a => a.dataset.nav === navKey(body.dataset.page));
    // Rarely used tools stay folded so the labelled list fits a laptop window.
    group.open = active || (group.dataset.navGroup === "tools" ? read(key) === "true" : read(key) !== "false");
    if (active) {
      const crumb = doc.getElementById("breadcrumb-group");
      if (crumb) crumb.textContent = group.querySelector("summary")?.textContent.trim() || "Workspace";
    }
    navPreferences.set(group, group.open);
    // Only a click on the group's own heading is the person's choice; opening a group for the
    // current page, or for the icon rail, must not be remembered.
    let chosen = false;
    group.querySelector("summary")?.addEventListener("click", () => { chosen = true; });
    group.addEventListener("toggle", () => {
      if (!chosen) return;
      chosen = false;
      if (!compactActive()) {
        navPreferences.set(group, group.open);
        write(key, group.open);
      }
    });
  });

  function layoutGroups() {
    for (const group of navGroups) group.open = compactActive() || navPreferences.get(group);
  }

  function compact(value, remember = true) {
    body.classList.toggle("nav-compact", value);
    compactButton?.setAttribute("aria-expanded", String(!value));
    compactButton?.setAttribute("aria-label", value ? "Show labels" : "Collapse to icons");
    const compactLabel = compactButton?.querySelector(".nav-title");
    if (compactLabel) compactLabel.textContent = value ? "Show labels" : "Collapse to icons";
    if (remember) write("leakscope.nav.mode", value ? "rail" : "full");
    layoutGroups();
  }
  // Labels are the default; the icon rail is an explicit choice made with the toggle or the [ key.
  compact(read("leakscope.nav.mode") === "rail", false);
  compactButton?.addEventListener("click", () => compact(!body.classList.contains("nav-compact")));
  let desktopNavigation = innerWidth > 800;
  addEventListener("resize", () => {
    const desktop = innerWidth > 800;
    if (desktop !== desktopNavigation) {
      desktopNavigation = desktop;
      layoutGroups();
    }
  });

  const dialog = doc.getElementById("workspace-commands"),
    search = doc.getElementById("command-search"),
    results = doc.getElementById("command-results");
  let returnFocus = null;

  function closeCommand() {
    if (!dialog?.open) return;
    dialog.close();
    if (returnFocus?.isConnected) returnFocus.focus();
  }

  function filterCommands() {
    const term = (search?.value || "").trim().toLowerCase();
    results?.querySelectorAll("a").forEach(a => {
      a.hidden = !a.textContent.toLowerCase().includes(term);
    });
    const status = doc.getElementById("command-empty");
    if (status) status.hidden = !!results.querySelector("a:not([hidden])");
  }

  function openCommand() {
    if (!dialog || dialog.open) return;
    returnFocus = doc.activeElement;
    results.replaceChildren();
    const seen = new Set();
    doc.querySelectorAll("#sidebar a[data-nav], #command-extra a").forEach(source => {
      if (seen.has(source.getAttribute("href"))) return;
      seen.add(source.getAttribute("href"));
      const a = doc.createElement("a");
      a.href = source.getAttribute("href");
      a.className = "command-result";
      a.textContent = source.querySelector(".nav-title")?.textContent || source.textContent.trim();
      a.addEventListener("click", closeCommand);
      results.append(a);
    });
    search.value = "";
    filterCommands();
    dialog.showModal();
    search.focus();
  }
  doc.getElementById("command-toggle")?.addEventListener("click", openCommand);
  doc.querySelectorAll("[data-close-command]").forEach(b => b.addEventListener("click", closeCommand));
  search?.addEventListener("input", filterCommands);
  dialog?.addEventListener("cancel", e => {
    e.preventDefault();
    closeCommand();
  });
  dialog?.addEventListener("click", e => {
    if (e.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeCommand();
    }
  });
  doc.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      dialog?.open ? closeCommand() : openCommand();
    } else if (e.key === "Escape" && dialog?.open) {
      e.preventDefault();
      closeCommand();
    } else if (e.key === "[" && !e.target.closest?.("input,textarea,select,[contenteditable=true],dialog")) {
      e.preventDefault();
      compact(!body.classList.contains("nav-compact"));
    } else if (dialog?.open && ["ArrowDown", "ArrowUp"].includes(e.key)) {
      const links = [...results.querySelectorAll("a:not([hidden])")];
      if (!links.length) return;
      e.preventDefault();
      const index = links.indexOf(doc.activeElement),
        direction = e.key === "ArrowDown" ? 1 : -1;
      links[index < 0 ? (direction > 0 ? 0 : links.length - 1) : (index + direction + links.length) % links.length].focus();
    }
  });

  // Menus built from <details>: one open at a time, closed by Escape or a click elsewhere.
  const popovers = ".status-menu,.ls-popover";
  function closePopovers(except, refocus) {
    doc.querySelectorAll(popovers).forEach(menu => {
      if (!menu.open || menu === except || menu.contains(except)) return;
      const held = menu.contains(doc.activeElement);
      menu.open = false;
      if (refocus || held) menu.querySelector("summary")?.focus();
    });
  }
  doc.addEventListener("click", e => closePopovers(e.target.closest?.(popovers), false));
  // A row menu is placed with fixed coordinates, so it closes when anything behind it scrolls.
  addEventListener("scroll", e => doc.querySelectorAll(".ls-rowmenu[open]").forEach(menu => { if (!menu.contains(e.target)) menu.open = false; }), true);
  doc.addEventListener("keydown", e => {
    if (e.key !== "Escape" || dialog?.open || !doc.querySelector(":is(" + popovers + ")[open]")) return;
    e.preventDefault();
    closePopovers(null, true);
  });

  const enhanced = new WeakSet();

  function enhance(container) {
    const table = container.matches?.('table') ? container : container.closest?.('table');
    if(table)window.LeakScopeTables?.collapse?.(table);
    container.querySelectorAll('table').forEach(node=>window.LeakScopeTables?.collapse?.(node));
    if(table)window.LeakScopeTables?.fit?.(table);
    container.querySelectorAll('table').forEach(node=>window.LeakScopeTables?.fit?.(node));
    // Shared structural roles keep action emphasis independent of labels and page copy.
    const actions = [container, ...container.querySelectorAll('.btn')];
    const eligible = node => node.matches?.('.btn') && !node.matches('.btn-primary,.btn-danger,.btn-quiet,[data-action-tone="secondary"],[data-action-tone="danger"]');
    actions.forEach(control => {
      if (!eligible(control)) return;
      const header = control.closest('.heading-actions');
      if (control.matches('button[type="submit"],input[type="submit"],[data-action-tone="primary"]') ||
          (header && !header.querySelector('.btn-primary') && [...header.querySelectorAll('.btn')].find(eligible) === control)) control.classList.add('btn-primary');
    });
    const surfaces = '.panel,.metric,.run-card,.workspace-correlation-group,.workspace-change-card,.workspace-saved-views,.workspace-local-index';
    const candidates = [container, ...container.querySelectorAll('[data-disclosure-id],' + surfaces)];
    candidates.forEach(el => {
      if (!(el instanceof Element) || enhanced.has(el)) return;
      enhanced.add(el);
      if (el.matches(surfaces)) el.classList.add("holo-surface");
      if (el.matches("details[data-disclosure-id]")) {
        const key = "leakscope.disclosure." + el.dataset.disclosureId,
          saved = read(key);
        if (saved !== null) el.open = saved === "true";
        el.addEventListener("toggle", () => write(key, el.open));
      }
    });
  }
  const root = doc.getElementById("page-root");
  if (root) {
    enhance(root);
    new MutationObserver(records => records.forEach(r => r.addedNodes.forEach(n => {
      if (n instanceof Element) enhance(n);
    }))).observe(root, {
      childList: true,
      subtree: true
    });
  }
  window.LeakScopeShell = {
    enhance,
    openCommand,
    closeCommand,
    navKey,
    motionEnabled: () => motion && !media.matches
  };
})();
