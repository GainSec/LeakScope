import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import { BrowserRuntime } from "../runtime.mjs";
const chrome =
  process.env.BROWSER_CHROMIUM_PATH ||
  (process.platform === "darwin"
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : "/usr/bin/chromium");
const available = fs.existsSync(chrome);
const listen = (s) =>
  new Promise((r) => s.listen(0, "127.0.0.1", () => r(s.address().port)));
const fixture = () =>
  http.createServer((req, res) => {
    res.setHeader("Content-Type", "text/html");
    res.end(
      `<!doctype html><html><head><title>LeakScope fixture</title></head><body style="background:#f0c040;color:#123"><canvas id="gpu" width="200" height="200"></canvas><h1 id="hello">Waiting</h1><button onclick="document.querySelector('h1').textContent='Clicked'">Continue</button><script>const gl=document.querySelector('canvas').getContext('webgl'); if(gl){gl.clearColor(0.1,0.5,0.2,1);gl.clear(gl.COLOR_BUFFER_BIT);const pixel=new Uint8Array(4);gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);if(pixel[1]>100)document.title='LeakScope fixture GPU ready';}setTimeout(()=>document.querySelector('h1').textContent='Rendered',100);console.error('secret browser diagnostic');</script><img src="http://127.0.0.1:1/private?secret=x"></body></html>`,
    );
  });

test("real Chromium distinguishes HTTP authentication, HTTP errors and endpoint navigation restrictions", { skip: !available, timeout: 30000 }, async () => {
  let foreignCalls = 0;
  const foreign = http.createServer((req, res) => { foreignCalls++; res.end("must not load"); });
  const foreignPort = await listen(foreign);
  const target = http.createServer((req, res) => {
    if (req.url.startsWith("/redirect")) {
      res.writeHead(302, { Location: `http://127.0.0.1:${foreignPort}/?token=secret` });
      res.end();
    } else { res.writeHead(req.url === "/auth" ? 401 : 503); res.end("<title>Service unavailable</title>"); }
  });
  const port = await listen(target);
  const runtime = new BrowserRuntime({ executablePath: chrome });
  try {
    for (const [path, code] of [["/auth", "auth_required"], ["/error", "http_error"], ["/redirect", "navigation_blocked"]]) {
      const result = await runtime.capture({ url: `http://127.0.0.1:${port}${path}`, wait_seconds: 0, timeout_seconds: 10 });
      assert.ok(result.partial);
      assert.ok(result.metadata.capture_quality.reason_codes.includes(code), JSON.stringify(result.metadata.capture_quality));
      assert.ok(!JSON.stringify(result.metadata).includes("secret"));
    }
    assert.equal(foreignCalls, 0);
  } finally { await runtime.close(); await new Promise((r) => target.close(r)); await new Promise((r) => foreign.close(r)); }
});

test("real Chromium retains upstream connection refusal through synthetic proxy HTTP 502", { skip: !available, timeout: 20000 }, async () => {
  const target = http.createServer();
  const port = await listen(target);
  await new Promise((r) => target.close(r));
  const runtime = new BrowserRuntime({ executablePath: chrome });
  try {
    const result = await runtime.capture({ url: `http://127.0.0.1:${port}/?token=secret`, wait_seconds: 0, timeout_seconds: 10 });
    assert.ok(result.metadata.capture_quality.reason_codes.includes("connection_refused"));
    assert.ok(result.partial);
    assert.ok(!JSON.stringify(result.metadata).includes("secret"));
  } finally { await runtime.close(); }
});

test("real Chromium timeout keeps a timeout reason instead of generic controller failure", { skip: !available, timeout: 20000 }, async () => {
  const target = http.createServer(() => {});
  const port = await listen(target);
  const runtime = new BrowserRuntime({ executablePath: chrome });
  try {
    await assert.rejects(
      runtime.capture({ url: `http://127.0.0.1:${port}`, wait_seconds: 0, timeout_seconds: 10 }),
      (error) => error.code === "timeout" && !error.message.includes("127.0.0.1"),
    );
  } finally { await runtime.close(); target.closeAllConnections(); await new Promise((r) => target.close(r)); }
});

test(
  "real Chromium captures delayed page, blocks off-endpoint resource, reports console safely",
  { skip: !available, timeout: 30000 },
  async () => {
    const target = fixture();
    const port = await listen(target);
    const runtime = new BrowserRuntime({ executablePath: chrome });
    try {
      const result = await runtime.capture({
        url: `http://127.0.0.1:${port}`,
        wait_seconds: 0.2,
        timeout_seconds: 10,
      });
      assert.equal(result.metadata.tool, "playwright");
      assert.equal(result.metadata.status_code, 200);
      assert.deepEqual(result.metadata.tls, { certificate_errors_ignored: true, certificate_validity: "unverified" });
      assert.equal(result.metadata.title, "LeakScope fixture GPU ready");
      assert.equal(result.metadata.render.webgl, true);
      assert.equal(result.partial, true);
      assert.equal(result.metadata.capture_quality.state, "restricted");
      assert.ok(result.metadata.capture_quality.reason_codes.includes("resource_blocked"));
      const png = Buffer.from(
        result.artifacts.find((a) => a.kind === "screenshot").base64,
        "base64",
      );
      assert.equal(png.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
      assert.ok(png.length > 5000);
      assert.ok(result.metadata.blocked_requests.length > 0);
      assert.ok(result.metadata.network.bytes > 0);
      assert.ok(result.metadata.console.some((m) => m.type === "error"));
      assert.ok(!JSON.stringify(result).includes("secret browser diagnostic"));
      assert.equal(
        result.metadata.capture_quality.visual_completeness,
        "unassessed",
      );
    } finally {
      await runtime.close();
      await new Promise((r) => target.close(r));
    }
  },
);

test(
  "manual session is bounded, interactive, target-bound and expires/releases its browser",
  { skip: !available, timeout: 30000 },
  async () => {
    const target = fixture();
    const port = await listen(target);
    const runtime = new BrowserRuntime({
      executablePath: chrome,
      manualHeadless: !process.env.BROWSER_TEST_HEADED,
      sessionMs: 10000,
    });
    try {
      const s = await runtime.createSession({
        url: `http://127.0.0.1:${port}`,
        wait_seconds: 0.1,
        timeout_seconds: 10,
      });
      assert.match(s.id, /^[0-9a-f-]{36}$/);
      assert.equal(s.viewport.width, 1280);
      await assert.rejects(
        runtime.createSession({ url: `http://127.0.0.1:${port}` }),
        /already active/,
      );
      const frame = await runtime.frame(s.id);
      assert.equal(frame.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
      await runtime.input(s.id, { type: "click", x: 45, y: 90 });
      const capture = await runtime.captureSession(s.id);
      assert.equal(capture.metadata.manual_session, true);
      assert.equal(capture.metadata.url, `http://127.0.0.1:${port}/`);
      await new Promise((r) =>
        setTimeout(r, Math.max(1, Date.parse(s.expires_at) - Date.now() + 80)),
      );
      assert.throws(() => runtime.session(s.id), /not found/);
    } finally {
      await runtime.close();
      await new Promise((r) => target.close(r));
    }
  },
);

test(
  "HTTPS request body limit is enforced before encrypted proxy tunnel sends it",
  { skip: !available, timeout: 30000 },
  async () => {
    const https = await import("node:https");
    let posts = 0;
    const target = https.createServer(
      {
        key: fs.readFileSync(new URL("./fixture-key.pem", import.meta.url)),
        cert: fs.readFileSync(new URL("./fixture-cert.pem", import.meta.url)),
      },
      (req, res) => {
        res.setHeader("Content-Type", "text/html");
        if (req.method === "POST") {
          posts++;
          res.end("unexpected");
          return;
        }
        res.end(
          `<title>Body limit</title><script>fetch('/post',{method:'POST',body:'x'.repeat(600000)}).catch(()=>{});</script>`,
        );
      },
    );
    const port = await listen(target);
    const runtime = new BrowserRuntime({ executablePath: chrome });
    try {
      const capture = await runtime.capture({
        url: `https://127.0.0.1:${port}`,
        wait_seconds: 0.1,
        timeout_seconds: 10,
      });
      assert.equal(posts, 0);
      assert.ok(
        capture.metadata.blocked_requests.some((b) =>
          /body.*limit/i.test(b.reason),
        ),
      );
    } finally {
      await runtime.close();
      await new Promise((r) => target.close(r));
    }
  },
);

test(
  "full-page PNG has bounded height and reports truncation",
  { skip: !available, timeout: 30000 },
  async () => {
    const target = http.createServer((req, res) =>
      res.end(
        '<title>Long page</title><div style="height:12000px;background:linear-gradient(#f00,#00f)">Long fixture</div>',
      ),
    );
    const port = await listen(target),
      runtime = new BrowserRuntime({ executablePath: chrome });
    try {
      const capture = await runtime.capture({
        url: `http://127.0.0.1:${port}`,
        wait_seconds: 0,
        timeout_seconds: 10,
        full_page: true,
      });
      const png = Buffer.from(capture.artifacts[0].base64, "base64");
      assert.equal(png.readUInt32BE(20), 9000);
      assert.ok(
        capture.metadata.capture_quality.reasons.some((r) =>
          /height truncated/.test(r),
        ),
      );
    } finally {
      await runtime.close();
      await new Promise((r) => target.close(r));
    }
  },
);
