/* Shared drawing of evidence levels, capture states and image classes.
   Every figure reads saved data handed to it; nothing here requests anything. */
(function () {
  "use strict";
  const LEVELS = [
    ["candidate", "Discovery candidate", "candidate", "candidates"],
    ["provider_reported", "Provider reported", "provider reported", "provider reported"],
    ["protocol_confirmed", "Live protocol confirmed", "live confirmed", "live confirmed"],
    ["signature_matched", "Implementation signature matched", "signature matched", "signature matched"],
    ["implementation_verified", "Implementation verified", "implementation verified", "implementation verified"]
  ];
  // Endpoints that no saved record names at all; shown so a strip always adds up.
  const NONE = ["none", "No identity evidence", "unidentified", "unidentified"];
  const STATES = [
    ["succeeded", "Captured", "captured"], ["partial", "Partial capture", "partial"], ["failed", "Capture failed", "failed"],
    ["pending", "Capture queued", "queued"], ["not_attempted", "Not attempted", "not attempted"],
    ["unsupported", "Unsupported", "unsupported"], ["not_applicable", "Not applicable", "not applicable"]
  ];
  const CLASSES = {browser: "Browser screenshot", framebuffer: "Native framebuffer capture", rendered: "Rendered protocol evidence"};
  const CLASS_NOTES = {
    browser: "A browser loaded the page and saved what it drew.",
    framebuffer: "Pixels sent by the service over its remote-display protocol, without input or credentials.",
    rendered: "Drawn by LeakScope from saved response bytes. It is not a screenshot and not a terminal session."
  };
  // One simple pictogram per class: a browser window, a display, a ruled document.
  const ICONS = {
    browser: "M2 3.5h12v9H2zM2 6h12M4 4.75h.01M6 4.75h.01",
    framebuffer: "M2 3h12v8H2zM6 13.5h4M8 11v2.5",
    rendered: "M4 1.5h6l3 3v10H4zM10 1.5v3h3M6 8h5M6 10.5h5M6 5.5h2"
  };

  function el(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  const count = value => Number(value || 0).toLocaleString();
  const when = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "";

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16"); svg.setAttribute("class", "ev-icon"); svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICONS[name] || ICONS.rendered); svg.append(path);
    return svg;
  }

  /* A proportional strip with a counted key beneath it. `order` rows are
     [key, label, singular, plural]; `href(key)` makes each key entry a link. */
  function strip(kind, counts, order, options = {}) {
    const present = order.filter(([key]) => Number(counts?.[key]) > 0);
    const total = present.reduce((sum, [key]) => sum + Number(counts[key]), 0);
    const summary = present.map(([key, label]) => count(counts[key]) + " " + label.toLowerCase()).join(", ") || options.empty || "Nothing recorded";
    const node = el("div", {class: "ev-strip ev-strip-" + kind});
    if (options.title) node.append(el("span", {class: "ev-strip-title"}, options.title));
    const bar = el("div", {class: "ev-bar", role: "img", "aria-label": (options.title ? options.title + ": " : "") + summary});
    for (const [key] of present) bar.append(el("span", {class: "ev-seg ev-" + kind + "-" + key, style: "flex-grow:" + Number(counts[key])}));
    if (!total) bar.append(el("span", {class: "ev-seg ev-seg-empty", style: "flex-grow:1"}));
    const key = el("ul", {class: "ev-key"});
    for (const [name, label, singular, plural] of present) {
      const words = [el("span", {class: "ev-mark ev-" + kind + "-" + name, "aria-hidden": "true"}), el("strong", {}, count(counts[name])), " ", Number(counts[name]) === 1 ? singular : plural || singular];
      const href = name === "none" ? null : options.href?.(name);
      key.append(el("li", {title: label}, href ? el("a", {href, class: "ev-key-link"}, words) : el("span", {}, words)));
    }
    if (!total) key.append(el("li", {}, options.empty || "Nothing recorded"));
    node.append(bar, key);
    return node;
  }
  const levelStrip = (levels, options = {}) => strip("level", levels, [NONE, ...LEVELS], {title: "Evidence", empty: "No identity evidence", ...options});
  const captureStrip = (captures, options = {}) => strip("state", captures, STATES.map(([key, label, word]) => [key, label, word, word]), {title: "Captures", ...options});

  function classBadge(name, label) {
    if (!CLASSES[name]) return null;
    return el("span", {class: "ev-class ev-class-" + name, title: CLASS_NOTES[name]}, icon(name), label || CLASSES[name]);
  }
  function stateChip(state, label) {
    const known = STATES.find(([key]) => key === state);
    return el("span", {class: "ev-chip ev-chip-" + (known ? state : "not_attempted")}, el("span", {class: "ev-mark ev-state-" + (known ? state : "not_attempted"), "aria-hidden": "true"}), label || known?.[1] || "Unknown");
  }
  function levelChip(level, label) {
    const known = LEVELS.find(([key]) => key === level);
    return el("span", {class: "ev-chip ev-chip-level"}, el("span", {class: "ev-mark ev-level-" + (known ? level : "none"), "aria-hidden": "true"}), label || known?.[1] || "No identity evidence");
  }

  /* Four steps from "matched a search" to "implementation verified". A step is
     filled only when a saved record or a deliberate tag supports it. */
  function ladder(attribution) {
    const node = el("div", {class: "ev-attribution"});
    if (!attribution?.ladder) return node;
    node.append(el("p", {class: "ev-headline"}, levelChip(attribution.level, attribution.label), el("span", {class: "ev-headline-note"}, attribution.caution || "")));
    const steps = el("ol", {class: "ev-ladder"});
    for (const step of attribution.ladder) {
      const item = el("li", {class: "ev-rung" + (step.established ? " is-established" : ""), "data-level": step.level},
        el("span", {class: "ev-rung-node ev-level-" + step.level, "aria-hidden": "true"}),
        el("div", {class: "ev-rung-body"}, el("strong", {}, step.label), el("span", {class: "ev-rung-state"}, step.established ? "Established" : "Not established")));
      const body = item.querySelector(".ev-rung-body");
      for (const entry of (step.items || []).slice(0, 6)) {
        body.append(el("p", {class: "ev-rung-item ev-rung-" + entry.kind}, entry.text, entry.at ? el("time", {datetime: entry.at}, " · " + when(entry.at)) : null));
      }
      if (step.level === "implementation_verified" && !step.established) {
        const detail = attribution.implementation || {};
        body.append(el("p", {class: "ev-rung-item"}, detail.note || ""));
        for (const note of detail.collector_notes || []) body.append(el("p", {class: "ev-rung-item"}, "Collector note: " + note));
        if ((detail.limit_tags || []).length) body.append(el("p", {class: "ev-rung-item"}, "Tagged " + detail.limit_tags.map(tag => "“" + tag + "”").join(", ") + ": the implementation is stated as not established."));
      } else if (!step.established && !(step.items || []).length) body.append(el("p", {class: "ev-rung-item ev-rung-none"}, "No saved record supports this level."));
      steps.append(item);
    }
    node.append(steps);
    const tags = (attribution.tags || []);
    if (tags.length) {
      const list = el("ul", {class: "ev-tags"});
      for (const row of tags) list.append(el("li", {}, el("span", {class: "ev-tag ev-tag-" + row.role}, row.tag), el("span", {class: "ev-tag-origin"}, row.origin_label + (row.note ? " — " + row.note : ""))));
      node.append(el("details", {class: "ev-details"}, el("summary", {}, "Tags and who assigned them (" + tags.length + ")"), list));
    }
    node.append(el("details", {class: "ev-details"}, el("summary", {}, "What each level means"),
      el("dl", {class: "ev-meanings"}, attribution.ladder.map(step => [el("dt", {}, step.label), el("dd", {}, step.meaning)]))));
    return node;
  }

  function channel(title, value) {
    if (!value) return null;
    return el("div", {class: "ev-channel"}, el("span", {class: "ev-channel-title"}, title), stateChip(value.state, value.label), el("p", {class: "ev-reason"}, value.reason || ""),
      value.operation ? el("p", {class: "ev-operation"}, "Applies: " + value.operation) : null);
  }

  /* Why this endpoint has, or lacks, a live capture: two channels, the images
     that exist by class, then every saved attempt. */
  function capturePanel(status) {
    const node = el("div", {class: "ev-capture"});
    if (!status?.state) return node;
    node.append(el("p", {class: "ev-headline"}, stateChip(status.state, status.label), el("span", {class: "ev-headline-note"}, status.reason || "")));
    node.append(el("div", {class: "ev-channels"}, channel("Screenshot", status.screenshot), channel("Protocol capture", status.protocol)));
    const images = el("div", {class: "ev-images"});
    for (const name of Object.keys(CLASSES)) {
      const total = Number(status.images?.[name] || 0);
      images.append(el("span", {class: "ev-image-count" + (total ? "" : " is-empty")}, classBadge(name), el("strong", {}, count(total))));
    }
    node.append(images);
    if (status.visual?.class === "rendered") node.append(el("p", {class: "ev-reason"}, "The image for this endpoint is rendered protocol evidence. " + (status.visual.note || "")));
    const history = status.history || [];
    if (history.length) {
      const list = el("ol", {class: "ev-attempts"});
      for (const attempt of history) {
        const actions = (attempt.actions || []).map(action => el("span", {class: "ev-attempt-action"}, stateChip(action.state === "not_run" || action.state === "cancelled" ? "not_attempted" : action.state, action.label),
          action.message ? el("span", {class: "ev-attempt-message"}, action.message) : null));
        list.append(el("li", {}, el("time", {datetime: attempt.at || ""}, when(attempt.at) || "Unknown time"),
          el("span", {class: "ev-attempt-origin"}, (attempt.collection_mode && attempt.collection_mode !== "custom" ? attempt.collection_mode.replaceAll("_", " ") + " collection" : "requested by hand") + (attempt.routed_by_hint ? " · reader chosen from a provider banner" : "")),
          el("div", {class: "ev-attempt-actions"}, actions)));
      }
      const total = status.attempts?.total ?? history.length;
      node.append(el("details", {class: "ev-details", open: history.length <= 2 ? "" : null}, el("summary", {}, total + (total === 1 ? " saved attempt" : " saved attempts") + (status.history_truncated ? " (latest 10 shown)" : "")), list));
    }
    if (status.basis) node.append(el("p", {class: "ev-basis"}, status.basis));
    return node;
  }

  window.LeakScopeEvidence = {LEVELS, STATES, CLASSES, CLASS_NOTES, levelStrip, captureStrip, classBadge, stateChip, levelChip, ladder, capturePanel};
})();
