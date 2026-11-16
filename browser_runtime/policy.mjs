import dns from "node:dns/promises";
import net from "node:net";
import ipaddr from "ipaddr.js";

export const VIEWPORT = Object.freeze({ width: 1280, height: 800 });
export const LIMITS = Object.freeze({
  bytes: 32 * 1024 * 1024,
  requests: 350,
  body: 512 * 1024,
  png: 12 * 1024 * 1024,
  fullHeight: 9000,
  diagnostics: 200,
  sessionMs: 10 * 60 * 1000,
});

export function isPublicAddress(value) {
  try {
    let ip = ipaddr.parse(value.replace(/^\[|\]$/g, ""));
    if (ip.kind() === "ipv6" && ip.isIPv4MappedAddress())
      ip = ip.toIPv4Address();
    return ip.range() === "unicast";
  } catch {
    return false;
  }
}
export function cleanURL(raw) {
  try {
    const u = new URL(raw);
    return `${u.protocol}//${u.host}${u.pathname}`.slice(0, 512);
  } catch {
    return "[invalid URL]";
  }
}
export function validateOptions(body = {}) {
  if (!body || typeof body !== "object" || Array.isArray(body))
    throw new Error("Capture options must be an object");
  let u;
  try {
    u = new URL(body.url);
  } catch {
    throw new Error("Invalid HTTP(S) URL");
  }
  if (
    !["http:", "https:"].includes(u.protocol) ||
    u.username ||
    u.password ||
    u.hash ||
    /%00|%0a|%0d/i.test(u.href) ||
    u.href.length > 2048
  )
    throw new Error("Invalid HTTP(S) URL");
  const profile = body.profile ?? "strict";
  if (!["strict", "web_page"].includes(profile))
    throw new Error("Invalid profile");
  const num = (key, def, min, max) => {
    const n = body[key] ?? def;
    if (typeof n !== "number" || !Number.isFinite(n) || n < min || n > max)
      throw new Error(`Invalid ${key}`);
    return n;
  };
  const site = String(body.site_domain ?? u.hostname)
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
  const host = u.hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
  if (
    !site ||
    site.includes("/") ||
    (site.includes(":") && !net.isIP(site.replace(/^\[|\]$/g, ""))) ||
    !(host === site || host.endsWith("." + site))
  )
    throw new Error("Invalid site domain");
  if (body.full_page !== undefined && typeof body.full_page !== "boolean")
    throw new Error("Invalid full_page");
  return {
    url: u.href,
    site_domain: site,
    profile,
    timeout_seconds: num("timeout_seconds", 30, 10, 60),
    wait_seconds: num("wait_seconds", 2, 0, 10),
    full_page: body.full_page ?? false,
  };
}
export function validateInput(body = {}) {
  const type = body.type;
  const finite = (v, min, max) =>
    typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
  if (
    type === "click" &&
    finite(body.x, 0, VIEWPORT.width - 1) &&
    finite(body.y, 0, VIEWPORT.height - 1)
  )
    return { type, x: body.x, y: body.y };
  if (
    type === "scroll" &&
    finite(body.delta_x ?? 0, -1800, 1800) &&
    finite(body.delta_y, -1800, 1800)
  )
    return { type, delta_x: body.delta_x ?? 0, delta_y: body.delta_y };
  if (
    type === "text" &&
    typeof body.text === "string" &&
    body.text.length <= 1024 &&
    !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(body.text)
  )
    return { type, text: body.text };
  if (
    type === "key" &&
    [
      "Tab",
      "Shift+Tab",
      "Enter",
      "Space",
      "Backspace",
      "Delete",
      "ArrowUp",
      "ArrowDown",
      "ArrowLeft",
      "ArrowRight",
      "Home",
      "End",
      "PageUp",
      "PageDown",
      "Escape",
    ].includes(body.key)
  )
    return { type, key: body.key };
  throw new Error("Unsupported or out-of-range browser input");
}

export class BrowserPolicy {
  constructor(
    options,
    lookup = (host) => dns.lookup(host, { all: true, verbatim: true }),
    { dnsTimeout = 5000 } = {},
  ) {
    this.options = validateOptions(options);
    const u = new URL(this.options.url);
    this.host = u.hostname
      .toLowerCase()
      .replace(/^\[|\]$/g, "")
      .replace(/\.$/, "");
    this.port = Number(u.port || (u.protocol === "https:" ? 443 : 80));
    this.lookup = lookup;
    this.pins = new Map();
    this.dnsTimeout = Math.min(5000, dnsTimeout);
  }
  navigation(raw) {
    try {
      const u = new URL(raw);
      if (!["http:", "https:"].includes(u.protocol) || u.username || u.password)
        return false;
      const host = u.hostname
        .toLowerCase()
        .replace(/^\[|\]$/g, "")
        .replace(/\.$/, "");
      const port = Number(u.port || (u.protocol === "https:" ? 443 : 80));
      if (host === this.host && port === this.port) return true;
      return (
        this.options.profile === "web_page" &&
        [80, 443].includes(port) &&
        (host === this.options.site_domain ||
          host.endsWith("." + this.options.site_domain))
      );
    } catch {
      return false;
    }
  }
  async destination(rawHost, rawPort) {
    const host = String(rawHost)
      .toLowerCase()
      .replace(/^\[|\]$/g, "")
      .replace(/\.$/, "");
    const port = Number(rawPort);
    const selected = host === this.host && port === this.port;
    if (
      !host ||
      host.length > 253 ||
      !Number.isInteger(port) ||
      port < 1 ||
      port > 65535
    )
      throw new Error("Invalid destination");
    if (this.options.profile === "strict" && !selected)
      throw new Error("Blocked by strict profile");
    if (!selected && ![80, 443].includes(port))
      throw new Error("Dependency port not permitted");
    const key = `${host}:${port}`;
    if (this.pins.has(key)) return this.pins.get(key);
    const resolve = async () => {
      let timer;
      let answers;
      try {
        answers = net.isIP(host)
          ? [{ address: host, family: net.isIP(host) }]
          : await Promise.race([
              this.lookup(host),
              new Promise((_, reject) => {
                timer = setTimeout(
                  () => reject(new Error("DNS resolution timed out")),
                  this.dnsTimeout,
                );
              }),
            ]);
      } finally {
        clearTimeout(timer);
      }
      if (!Array.isArray(answers) || !answers.length)
        throw new Error("DNS lookup returned no addresses");
      if (!selected && answers.some((a) => !isPublicAddress(a.address)))
        throw new Error("Dependency must have public addresses");
      if (answers.some((a) => !net.isIP(a.address)))
        throw new Error("Invalid DNS address");
      return {
        address: answers[0].address,
        family: answers[0].family || net.isIP(answers[0].address),
        host,
        port,
      };
    };
    const pending = resolve();
    this.pins.set(key, pending);
    try {
      return await pending;
    } catch (e) {
      this.pins.delete(key);
      throw e;
    }
  }
}
