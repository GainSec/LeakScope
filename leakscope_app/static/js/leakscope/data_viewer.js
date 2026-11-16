(function (root) {
  "use strict";
  const PAGE_SIZE = 32;
  const exactNumbers = new WeakMap();
  const sourceContextAvailable = (() => {
    let source;
    try { JSON.parse("0", (key, value, context) => {source = context?.source;return value;}); }
    catch (_) { return false; }
    return source === "0";
  })();
  function needsNumericSource(value) {
    // Skip complete JSON strings, including escaped quotes, before considering number tokens.
    const tokens = /"(?:\\.|[^"\\])*"|(-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    for (const match of value.matchAll(tokens)) {
      const token = match[1];
      if (token && (/[.eE]/.test(token) || token === "-0" || !Number.isSafeInteger(Number(token)))) return true;
    }
    return false;
  }
  function parseExact(value) {
    if (!sourceContextAvailable) {
      // Conservatively retain fractions and exponents as literal text on older engines.
      return needsNumericSource(value) ? {literal:true} : {value:JSON.parse(value)};
    }
    const parsed = JSON.parse(value, (key, item, context) => {
      if (typeof item !== "number") return item;
      if (typeof context?.source !== "string") throw new Error("Numeric source is unavailable");
      const exact = Object.freeze({});
      exactNumbers.set(exact, context.source);
      return exact;
    });
    return {value:parsed};
  }
  const labels = {
    ip: "IP address", ip_str: "IP address", host: "Hostname", hostname: "Hostname",
    dns: "DNS", dns_names: "DNS names", rdap: "RDAP", asn: "ASN", cn: "Common name",
    http: "HTTP", https: "HTTPS", tls: "TLS", ssl: "TLS / SSL", tcp: "TCP", udp: "UDP",
    sha256: "SHA-256 fingerprint", sha1: "SHA-1 fingerprint", md5: "MD5 fingerprint",
    unit_id: "Unit ID", device_id: "Device ID", id: "ID", url: "URL", uri: "URI",
    a: "A records", aaaa: "AAAA records", mx: "MX records", txt: "TXT records", cname: "CNAME records",
  };
  const distinguishedNameLabels = {
    C:"Country", c:"Country", L:"Locality", l:"Locality", O:"Organization", o:"Organization",
    OU:"Organizational unit", ou:"Organizational unit", ST:"State / province", st:"State / province",
    CN:"Common name", cn:"Common name",
  };
  function label(key, path = "") {
    if (/(?:^|\.)(?:issuer|subject|issuer_dn|subject_dn)(?:\.|$)/i.test(path) && Object.hasOwn(distinguishedNameLabels,key)) return distinguishedNameLabels[key];
    if (Object.hasOwn(labels, key)) return labels[key];
    const value = String(key).replace(/([a-z\d])([A-Z])/g, "$1 $2").replace(/[_\-.]+/g, " ");
    return value.charAt(0).toUpperCase() + value.slice(1);
  }
  function element(tag, className, text) {
    const node = root.document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function count(value) {
    return Array.isArray(value) ? value.length : value && typeof value === "object" ? Object.keys(value).length : 1;
  }
  function countLabel(value) {
    const amount = count(value), noun = Array.isArray(value) ? "item" : "field";
    return amount + " " + noun + (amount === 1 ? "" : "s");
  }
  function scalar(value, key) {
    if (value && typeof value === "object" && exactNumbers.has(value)) {
      const node = element("span", "data-number", exactNumbers.get(value));
      node.title = "Exact JSON numeric source";
      return node;
    }
    if (value === null) return element("span", "data-empty", "No value");
    if (value === undefined) return element("span", "data-empty", "Not recorded");
    if (typeof value === "boolean") return element("span", "data-boolean " + (value ? "is-true" : "is-false"), value ? "True" : "False");
    if (typeof value === "number") return element("span", "data-number", value);
    const text = String(value);
    if (text === "") return element("span", "data-empty", "Empty text");
    if (/(?:^|_)(?:at|date|valid_from|valid_to)$/.test(key || "") && /^\d{4}-\d\d-\d\d(?:T|$)/.test(text)) {
      const date = new Date(text);
      if (!Number.isNaN(date.valueOf())) {
        const node = element("time", "data-date", date.toLocaleString(undefined, {dateStyle:"medium", ...(text.includes("T") ? {timeStyle:"short"} : {timeZone:"UTC"})}));
        node.dateTime = text; node.title = text; return node;
      }
    }
    if (/^(?:sha256|sha1|md5|fingerprint|hash)$/.test(key || "") && text.length > 32) {
      const node = element("span", "data-fingerprint", text.slice(0,12) + "…" + text.slice(-12));
      node.title = text; return node;
    }
    if (text.length > 4000) {
      const details = element("details", "data-long-text");
      details.append(element("summary", null, text.length.toLocaleString() + " characters · expand text"));
      let loaded = false;
      details.addEventListener("toggle", () => {if (details.open && !loaded) {loaded = true;details.append(element("pre", "data-text", text));}});
      return details;
    }
    return element(text.includes("\n") || text.includes("\r") ? "pre" : "span", /[\r\n]/.test(text) ? "data-text" : "data-string" + (["source", "provider", "protocol", "transport"].includes(key) ? " data-chip" : ""), text);
  }
  function nested(value, path) {
    const size = count(value);
    if (!size) return element("span", "data-empty", Array.isArray(value) ? "Empty list" : "Empty object");
    const details = element("details", "data-nested");
    const summary = element("summary", null, countLabel(value));
    details.append(summary);
    let loaded = false;
    details.addEventListener("toggle", () => {
      if (details.open && !loaded) { loaded = true; details.append(collection(value, path)); }
    });
    return details;
  }
  function valueNode(value, key, path) {
    return value && typeof value === "object" && !exactNumbers.has(value) ? nested(value, path) : scalar(value, key);
  }
  function collection(value, path) {
    const array = Array.isArray(value), keys = array ? null : Object.keys(value), total = array ? value.length : keys.length;
    const container = element("div", "data-collection");
    if (!total) { container.append(element("span", "data-empty", array ? "Empty list" : "Empty object")); return container; }
    const fields = element(array ? "ol" : "dl", array ? "data-items" : "data-fields");
    const more = element("button", "btn btn-small data-more"); more.type = "button";
    let offset = 0;
    function appendPage() {
      const end = Math.min(total, offset + PAGE_SIZE);
      for (; offset < end; offset++) {
        const key = array ? offset : keys[offset], itemPath = path + "." + key;
        if (array) {
          const row = element("li"); row.append(valueNode(value[key], "", itemPath)); fields.append(row);
        } else {
          const row = element("div", "data-field"), term = element("dt", "data-key", label(key,itemPath)), description = element("dd", "data-value");
          term.title = itemPath; description.append(valueNode(value[key], key, itemPath)); row.append(term,description); fields.append(row);
        }
      }
      more.textContent = "Show " + Math.min(PAGE_SIZE,total-offset) + " more (" + offset + " of " + total + ")";
      if (offset >= total) more.remove();
    }
    more.addEventListener("click", appendPage);
    container.append(fields,more); appendPage();
    return container;
  }
  function formatted(value, path = "record") {
    let display = value, encoded = false, literal = false;
    if (typeof value === "string" && /^[\s]*[\[{]/.test(value)) {
      try {
        const parsed = parseExact(value);
        literal = !!parsed.literal;
        if (parsed.value && typeof parsed.value === "object") {display = parsed.value;encoded = true;}
      } catch (_) { /* Plain or unparseable evidence remains literal text. */ }
    }
    const panel = element("div", "data-formatted");
    panel.setAttribute("data-viewer-formatted", "");
    if (encoded) panel.append(element("p", "data-viewer-caption", "Formatted JSON content. Raw JSON retains the original string."));
    if (literal) panel.append(element("p", "data-viewer-caption", "Original JSON text is shown to preserve numeric precision; exact numeric parsing is unavailable in this browser."));
    if (display && typeof display === "object") {
      const section = element("section", "data-section");section.append(collection(display,path));panel.append(section);
    } else panel.append(scalar(display,""));
    return panel;
  }
  function viewer(value, options = {}) {
    const labelText = options.label || "Saved data";
    const viewer = element("section", "data-viewer"); viewer.setAttribute("aria-label", labelText + " viewer");
    const heading = element("div", "data-viewer-heading"); heading.append(element("strong", "data-viewer-title", labelText),element("span", "data-viewer-count", countLabel(value)));
    const controls = element("div", "data-viewer-switch"); controls.setAttribute("role", "group"); controls.setAttribute("aria-label", "Data display");
    const content = element("div", "data-viewer-content");
    const distinguishedName = /\b(issuer|subject)\b/i.exec(labelText);
    const pretty = formatted(value, distinguishedName ? "record." + distinguishedName[1].toLowerCase() : "record"); let raw;
    const buttons = ["formatted", "raw"].map(mode => {
      const button = element("button", "data-viewer-toggle", mode === "raw" ? "Raw JSON" : "Formatted");
      button.type = "button";button.dataset.viewerMode = mode;
      button.setAttribute("aria-pressed", String(mode === "formatted"));
      button.addEventListener("click", () => {
        if (mode === "raw" && !raw) {
          raw = element("pre", "code-box data-raw");raw.tabIndex = 0;raw.setAttribute("aria-label", "Original JSON");
          try { const serialized = JSON.stringify(value,null,2); raw.textContent = serialized === undefined ? "undefined" : serialized; }
          catch (_) { raw.textContent = "This value cannot be serialized as JSON."; }
        }
        content.replaceChildren(mode === "raw" ? raw : pretty);
        buttons.forEach(other => other.setAttribute("aria-pressed", String(other === button)));
      });
      return button;
    });
    controls.append(...buttons);content.append(pretty);viewer.append(heading,controls,content);
    return viewer;
  }
  root.LeakScopeData = {viewer,label};
})(typeof window !== "undefined" ? window : globalThis);
