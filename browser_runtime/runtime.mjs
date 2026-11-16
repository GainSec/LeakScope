import { randomUUID } from "node:crypto";
import fs from "node:fs";
import { chromium } from "playwright-core";
import {
  BrowserPolicy,
  cleanURL,
  validateOptions,
  validateInput,
  VIEWPORT,
  LIMITS,
} from "./policy.mjs";
import { createProxy } from "./proxy.mjs";
import { diagnostic, captureError } from "./diagnostics.mjs";

const challenge =
  /checking your browser|just a moment|verify (?:you are|that you)|attention required|security verification|access denied|please verify/i;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export class BrowserRuntime {
  constructor({
    executablePath = process.env.BROWSER_CHROMIUM_PATH || "/usr/bin/chromium",
    manualHeadless = false,
    sessionMs = LIMITS.sessionMs,
  } = {}) {
    this.executablePath = executablePath;
    this.manualHeadless = manualHeadless;
    this.sessionMs = Math.min(sessionMs, LIMITS.sessionMs);
    this.sessions = new Map();
    this.resources = new Set();
    this.captures = 0;
    this.creatingSession = false;
  }
  health() {
    return {
      available: fs.existsSync(this.executablePath),
      backend: "playwright",
      manual_available:
        this.manualHeadless ||
        Boolean(process.env.DISPLAY) ||
        process.platform === "darwin",
      profiles: ["strict", "web_page"],
      viewport: VIEWPORT,
      active_sessions: this.sessions.size,
      session_ttl_seconds: Math.floor(this.sessionMs / 1000),
      limits: {
        network_bytes: LIMITS.bytes,
        requests: LIMITS.requests,
        png_bytes: LIMITS.png,
        full_page_height: LIMITS.fullHeight,
      },
    };
  }
  async open(raw, { manual = false } = {}) {
    const options = validateOptions(raw),
      policy = new BrowserPolicy(options),
      created = Date.now();
    const deadline =
      created + (manual ? this.sessionMs : options.timeout_seconds * 1000);
    const proxy = await createProxy(policy, { deadline });
    let browser;
    try {
      browser = await chromium.launch({
        executablePath: this.executablePath,
        headless: manual ? this.manualHeadless : true,
        timeout: 15000,
        proxy: { server: proxy.url, bypass: "" },
        args: [
          "--no-sandbox",
          "--disable-dev-shm-usage",
          "--proxy-bypass-list=<-loopback>",
          "--disable-background-networking",
          "--disable-component-update",
          "--disable-domain-reliability",
          "--disable-sync",
          "--disable-quic",
          "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
          "--use-gl=angle",
          "--use-angle=swiftshader",
          "--enable-unsafe-swiftshader",
        ],
      });
      const context = await browser.newContext({
        viewport: VIEWPORT,
        deviceScaleFactor: 1,
        ignoreHTTPSErrors: true,
        serviceWorkers: "block",
        acceptDownloads: false,
        permissions: [],
        javaScriptEnabled: true,
      });
      const page = await context.newPage();
      page.setDefaultTimeout(5000);
      const resource = {
        browser,
        context,
        page,
        proxy,
        policy,
        options,
        created,
        deadline,
        console: [],
        errors: [],
        failed_requests: [],
        status_code: null,
        navigation_error: null,
        request_count: 0,
        busy: false,
        closed: false,
      };
      this.resources.add(resource);
      resource.close = async () => {
        if (resource.closed) return;
        resource.closed = true;
        clearTimeout(resource.timer);
        this.resources.delete(resource);
        await browser.close().catch(() => {});
        await proxy.close().catch(() => {});
      };
      resource.timer = setTimeout(
        () => resource.close(),
        Math.max(1, deadline - Date.now()),
      );
      resource.timer.unref();
      context.on("page", (other) => {
        if (other !== page) other.close().catch(() => {});
      });
      page.on("download", (d) => d.cancel().catch(() => {}));
      page.on("dialog", (d) => d.dismiss().catch(() => {}));
      page.on("console", (message) => {
        if (resource.console.length < LIMITS.diagnostics) {
          const loc = message.location();
          resource.console.push({
            type: message.type(),
            url: cleanURL(loc.url),
            line: loc.lineNumber,
          });
        }
      });
      page.on("pageerror", (e) => {
        if (resource.errors.length < LIMITS.diagnostics)
          resource.errors.push({ type: e.name || "Error" });
      });
      page.on("requestfailed", (request) => {
        if (resource.failed_requests.length < LIMITS.diagnostics)
          resource.failed_requests.push({
            url: cleanURL(request.url()),
            resource_type: request.resourceType(),
            code: diagnostic({ message: request.failure()?.errorText }).code,
          });
      });
      // Redirect hops do not always invoke Playwright's route callback. The
      // Strict proxy still confines the destination; retain that reason before
      // a proxy-generated HTTP response can obscure it. Do not claim optional
      // web_page redirect hops were blocked when CONNECT cannot prove that.
      page.on("request", (request) => {
        if (options.profile === "strict" && request.isNavigationRequest() && request.frame() === page.mainFrame() &&
            !policy.navigation(request.url()))
          resource.navigation_error = diagnostic({ code: "navigation_blocked" });
      });
      page.on("response", (response) => {
        try {
          if (
            response.request().isNavigationRequest() &&
            response.request().frame() === page.mainFrame()
          )
            resource.status_code = response.status();
        } catch {}
      });
      await context.routeWebSocket("**/*", (socket) => {
        proxy.note(
          new URL(socket.url()).host,
          "WebSocket not enabled for capture",
        );
        socket.close();
      });
      await context.route("**/*", async (route) => {
        const request = route.request();
        let u;
        try {
          u = new URL(request.url());
        } catch {
          await route.abort();
          return;
        }
        let reason = null;
        resource.request_count++;
        if (resource.request_count > LIMITS.requests) {
          reason = "Browser request budget exhausted";
          proxy.state.budget_exhausted = true;
        } else if (Date.now() >= deadline)
          reason = "Browser network deadline exceeded";
        else if (!["http:", "https:"].includes(u.protocol))
          reason = "Non-HTTP dependency blocked";
        else if (
          request.isNavigationRequest() &&
          request.frame() === page.mainFrame() &&
          !policy.navigation(u.href)
        )
          reason = "Main navigation outside selected site";
        else if (!["GET", "HEAD", "POST", "OPTIONS"].includes(request.method()))
          reason = "HTTP method not permitted";
        else if ((request.postDataBuffer()?.length || 0) > LIMITS.body)
          reason = "Browser request body exceeded limit";
        if (reason) {
          if (request.isNavigationRequest() && request.frame() === page.mainFrame())
            resource.navigation_error = diagnostic({ code: reason === "Main navigation outside selected site" ? "navigation_blocked" : "network_error" });
          proxy.note(u.host, reason);
          await route.abort("blockedbyclient");
          return;
        }
        try {
          await policy.destination(
            u.hostname,
            Number(u.port || (u.protocol === "https:" ? 443 : 80)),
          );
          if (Date.now() >= deadline)
            throw new Error("Browser network deadline exceeded");
          await route.continue();
        } catch (e) {
          if (request.isNavigationRequest() && request.frame() === page.mainFrame())
            resource.navigation_error = diagnostic(e);
          proxy.note(u.host, e.message);
          await route.abort("blockedbyclient");
        }
      });
      try {
        await page.goto(options.url, {
          waitUntil: "domcontentloaded",
          timeout: Math.max(
            1000,
            Math.min(
              options.timeout_seconds * 1000 - 3000,
              deadline - Date.now() - 1000,
            ),
          ),
        });
      } catch (e) {
        resource.navigation_error ||= diagnostic(e);
      }
      await this.settle(resource);
      return resource;
    } catch (e) {
      await browser?.close().catch(() => {});
      await proxy.close();
      throw e;
    }
  }
  async settle(resource) {
    const remaining = () =>
      Math.max(0, Math.min(3000, resource.deadline - Date.now() - 1000));
    try {
      await resource.page.waitForFunction(
        () => document.readyState !== "loading",
        null,
        { timeout: remaining() || 1 },
      );
    } catch {}
    try {
      await Promise.race([
        resource.page.evaluate(() => document.fonts?.ready.then(() => true)),
        delay(remaining()),
      ]);
    } catch {}
    await delay(Math.min(resource.options.wait_seconds * 1000, remaining()));
  }
  async screenshot(resource, { frame = false } = {}) {
    if (resource.closed) throw new Error("Browser session expired");
    const { page, options } = resource;
    if (!frame && options.full_page) {
      const height = await page.evaluate(() =>
        Math.min(
          Math.max(document.documentElement.scrollHeight, window.innerHeight),
          9000,
        ),
      );
      return page.screenshot({
        type: "png",
        animations: "disabled",
        timeout: 5000,
        fullPage: true,
        clip: {
          x: 0,
          y: 0,
          width: VIEWPORT.width,
          height: Math.min(height, LIMITS.fullHeight),
        },
      });
    }
    return page.screenshot({
      type: "png",
      animations: "disabled",
      timeout: 5000,
    });
  }
  async result(resource, { manual = false } = {}) {
    const png = await this.screenshot(resource);
    if (png.length > LIMITS.png)
      throw new Error("Screenshot exceeded artifact limit");
    const title = String(await resource.page.title().catch(() => "")).slice(
      0,
      512,
    );
    const stats = await resource.page
      .evaluate(() => ({
        ready_state: document.readyState,
        fonts_status: document.fonts?.status || "unavailable",
        images: Array.from(document.images).length,
        incomplete_images: Array.from(document.images).filter(
          (i) => !i.complete || i.naturalWidth === 0,
        ).length,
        document_height: document.documentElement.scrollHeight,
        viewport: { width: innerWidth, height: innerHeight },
        webgl: Boolean(
          document.createElement("canvas").getContext("webgl2") ||
            document.createElement("canvas").getContext("webgl"),
        ),
      }))
      .catch(() => ({ ready_state: "unknown" }));
    const reasons = [];
    const blocked = resource.proxy.state.blocked;
    if (challenge.test(title))
      reasons.push(
        "Browser verification or access challenge detected from title",
      );
    if (resource.status_code === null)
      reasons.push("Main document HTTP status unavailable");
    else if (resource.status_code >= 400)
      reasons.push(`Main document returned HTTP ${resource.status_code}`);
    if (blocked.length)
      reasons.push(`${blocked.length} requests blocked by browser policy`);
    if (resource.navigation_error) reasons.push(resource.navigation_error.message);
    if (resource.failed_requests.length)
      reasons.push(
        `${resource.failed_requests.length} network requests failed`,
      );
    if (resource.errors.length)
      reasons.push(`${resource.errors.length} JavaScript errors`);
    if (stats.incomplete_images)
      reasons.push(`${stats.incomplete_images} images incomplete`);
    if (stats.document_height > LIMITS.fullHeight && resource.options.full_page)
      reasons.push("Full page height truncated to capture limit");
    if (resource.proxy.state.budget_exhausted)
      reasons.push("Network budget exhausted");
    const upstream = resource.proxy.state.failures.filter((item) =>
      item.host === resource.policy.host && item.port === resource.policy.port);
    const reasonCodes = new Set(upstream.map((item) => item.code));
    if (resource.navigation_error) reasonCodes.add(resource.navigation_error.code);
    if (blocked.length) reasonCodes.add("resource_blocked");
    if (resource.status_code === 401 || resource.status_code === 407) reasonCodes.add("auth_required");
    else if (resource.status_code >= 400) reasonCodes.add("http_error");
    for (const code of new Set(upstream.map((item) => item.code))) reasons.push(diagnostic({ code }).message);
    const state = challenge.test(title)
      ? "challenge"
      : resource.status_code >= 400
        ? "http_error"
        : reasons.length
          ? "restricted"
          : "unassessed";
    const metadata = {
      tool: "playwright",
      browser: "chromium",
      url: cleanURL(resource.page.url()),
      requested_url: cleanURL(resource.options.url),
      title,
      status: resource.status_code,
      status_code: resource.status_code,
      profile: resource.options.profile,
      wait_seconds: resource.options.wait_seconds,
      timeout_seconds: resource.options.timeout_seconds,
      full_page: resource.options.full_page,
      manual_session: manual,
      partial: reasons.length > 0,
      capture_quality: { state, reasons, reason_codes: [...reasonCodes], visual_completeness: "unassessed" },
      navigation_error: resource.navigation_error,
      tls: { certificate_errors_ignored: true, certificate_validity: "unverified" },
      render: stats,
      network: {
        bytes: resource.proxy.state.bytes,
        requests: resource.request_count,
        connections: resource.proxy.state.requests,
        expired: resource.proxy.state.expired,
        budget_exhausted: resource.proxy.state.budget_exhausted,
        failures: resource.proxy.state.failures,
      },
      blocked_external_requests: blocked.length,
      blocked_requests: blocked,
      failed_requests: resource.failed_requests,
      console: resource.console,
      errors: resource.errors,
      diagnostics_note:
        "Console messages and session input are not recorded. A successful PNG does not prove visual completeness.",
    };
    return {
      metadata,
      partial: metadata.partial,
      artifacts: [
        {
          kind: "screenshot",
          mime: "image/png",
          extension: ".png",
          base64: png.toString("base64"),
        },
        {
          kind: "browser-metadata",
          mime: "application/json",
          extension: ".json",
          base64: Buffer.from(JSON.stringify(metadata, null, 2)).toString(
            "base64",
          ),
        },
      ],
    };
  }
  async capture(options) {
    if (this.captures >= 1) throw new Error("Browser capture already active");
    this.captures++;
    let resource;
    try {
      resource = await this.open(options);
      return await this.result(resource);
    } catch (error) {
      throw captureError(error, resource?.navigation_error?.code ||
        (resource?.closed ? "capture_expired" : null));
    } finally {
      await resource?.close();
      this.captures--;
    }
  }
  session(id) {
    const s = this.sessions.get(id);
    if (!s || s.resource.closed || Date.now() >= s.expires) {
      if (s) this.closeSession(id).catch(() => {});
      throw new Error("Browser session not found or expired");
    }
    return s;
  }
  describe(s) {
    return {
      id: s.id,
      expires_at: new Date(s.expires).toISOString(),
      viewport: VIEWPORT,
      url: cleanURL(s.resource.page.url()),
      profile: s.resource.options.profile,
      backend: "playwright",
      status_code: s.resource.status_code,
    };
  }
  async createSession(options) {
    if (this.sessions.size || this.creatingSession)
      throw new Error("A browser session is already active");
    this.creatingSession = true;
    try {
      const resource = await this.open(options, { manual: true });
      const id = randomUUID(),
        expires = resource.deadline;
      const s = { id, expires, resource };
      this.sessions.set(id, s);
      s.timer = setTimeout(
        () => this.closeSession(id),
        Math.max(1, expires - Date.now()),
      );
      s.timer.unref();
      return this.describe(s);
    } finally {
      this.creatingSession = false;
    }
  }
  async exclusive(id, fn) {
    const s = this.session(id);
    if (s.resource.busy) throw new Error("Browser session is busy");
    s.resource.busy = true;
    try {
      return await fn(s);
    } finally {
      s.resource.busy = false;
    }
  }
  async frame(id) {
    return this.exclusive(id, async (s) => {
      const image = await this.screenshot(s.resource, { frame: true });
      if (image.length > LIMITS.png)
        throw new Error("Screenshot exceeded artifact limit");
      return image;
    });
  }
  async input(id, raw) {
    const input = validateInput(raw);
    return this.exclusive(id, async (s) => {
      const p = s.resource.page;
      if (input.type === "click") await p.mouse.click(input.x, input.y);
      if (input.type === "scroll")
        await p.mouse.wheel(input.delta_x, input.delta_y);
      if (input.type === "text") await p.keyboard.insertText(input.text);
      if (input.type === "key") await p.keyboard.press(input.key);
      return { ok: true };
    });
  }
  async captureSession(id) {
    return this.exclusive(id, (s) => this.result(s.resource, { manual: true }));
  }
  async closeSession(id) {
    const s = this.sessions.get(id);
    if (!s) return { closed: true };
    this.sessions.delete(id);
    clearTimeout(s.timer);
    await s.resource.close();
    return { closed: true };
  }
  async close() {
    for (const id of [...this.sessions.keys()]) await this.closeSession(id);
    await Promise.all([...this.resources].map((r) => r.close()));
  }
}
