import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { BrowserPolicy } from "../policy.mjs";
import { createProxy } from "../proxy.mjs";
const listen = (s) =>
  new Promise((r) => s.listen(0, "127.0.0.1", () => r(s.address().port)));
const close = (s) => new Promise((r) => s.close(r));
const get = (proxy, url) =>
  new Promise((resolve, reject) => {
    http
      .get(proxy, { path: url }, (r) => {
        let body = "";
        r.on("data", (b) => (body += b));
        r.on("end", () => resolve({ status: r.statusCode, body }));
      })
      .on("error", reject);
  });

test("proxy preserves connection refusal as a bounded diagnostic instead of only synthetic HTTP 502", async () => {
  const target = http.createServer();
  const port = await listen(target);
  await close(target);
  const proxy = await createProxy(new BrowserPolicy({ url: `http://127.0.0.1:${port}` }));
  try {
    assert.equal((await get(proxy.url, `http://127.0.0.1:${port}/?token=secret`)).status, 502);
    assert.equal(proxy.state.failures[0].code, "connection_refused");
    assert.ok(!JSON.stringify(proxy.state.failures).includes("secret"));
  } finally { await proxy.close(); }
});

test("proxy connects to exact endpoint, blocks foreign and counts bytes", async () => {
  const target = http.createServer((req, res) => res.end("selected endpoint"));
  const port = await listen(target);
  const proxy = await createProxy(
    new BrowserPolicy({ url: `http://127.0.0.1:${port}` }),
    { deadline: Date.now() + 5000 },
  );
  try {
    const result = await get(proxy.url, `http://127.0.0.1:${port}/`);
    assert.equal(result.status, 200);
    assert.equal(result.body, "selected endpoint");
    assert.equal((await get(proxy.url, "http://127.0.0.1:1/")).status, 403);
    assert.ok(proxy.state.bytes >= 17);
    assert.equal(proxy.state.blocked.length, 1);
  } finally {
    await proxy.close();
    await close(target);
  }
});

test("proxy denies unsupported methods and deadline/request budget", async () => {
  const p = await createProxy(
    new BrowserPolicy({ url: "http://127.0.0.1:1" }),
    { deadline: Date.now() - 1 },
  );
  try {
    let denied = false;
    try {
      denied = (await get(p.url, "http://127.0.0.1:1/")).status === 403;
    } catch (e) {
      denied = ["ECONNRESET", "ECONNREFUSED"].includes(e.code);
    }
    assert.equal(denied, true);
    assert.equal(p.state.expired, true);
  } finally {
    await p.close();
  }
});

test("CONNECT checks host before tunneling and closes cleanly", async () => {
  const p = await createProxy(
    new BrowserPolicy({ url: "https://127.0.0.1:1" }),
    { deadline: Date.now() + 5000 },
  );
  try {
    const response = await new Promise((resolve, reject) => {
      const s = net.connect(new URL(p.url).port, "127.0.0.1", () =>
        s.write(
          "CONNECT 169.254.169.254:80 HTTP/1.1\r\nHost: 169.254.169.254\r\n\r\n",
        ),
      );
      s.once("data", (b) => {
        resolve(b.toString());
        s.destroy();
      });
      s.on("error", reject);
    });
    assert.match(response, /403/);
    assert.equal(p.state.blocked[0].host, "169.254.169.254");
  } finally {
    await p.close();
  }
});

test("plaintext mutating methods outside browser allowlist are blocked before target", async () => {
  let calls = 0;
  const target = http.createServer((req, res) => {
    calls++;
    res.end("unexpected");
  });
  const port = await listen(target);
  const proxy = await createProxy(
    new BrowserPolicy({ url: `http://127.0.0.1:${port}` }),
  );
  try {
    const status = await new Promise((resolve, reject) => {
      const request = http.request(
        proxy.url,
        { method: "DELETE", path: `http://127.0.0.1:${port}/` },
        (r) => {
          r.resume();
          r.on("end", () => resolve(r.statusCode));
        },
      );
      request.on("error", reject);
      request.end();
    });
    assert.equal(status, 403);
    assert.equal(calls, 0);
  } finally {
    await proxy.close();
    await close(target);
  }
});

test("late DNS answer cannot open a tunnel after proxy deadline", async () => {
  let connections = 0;
  const target = net.createServer((s) => {
    connections++;
    s.destroy();
  });
  const port = await listen(target);
  const policy = new BrowserPolicy(
    { url: `https://delayed.test:${port}` },
    async () => {
      await new Promise((r) => setTimeout(r, 100));
      return [{ address: "127.0.0.1", family: 4 }];
    },
  );
  const proxy = await createProxy(policy, { deadline: Date.now() + 20 });
  try {
    const client = net.connect(new URL(proxy.url).port, "127.0.0.1", () =>
      client.write(
        `CONNECT delayed.test:${port} HTTP/1.1\r\nHost: delayed.test\r\n\r\n`,
      ),
    );
    client.on("error", () => {});
    client.on("data", () => client.destroy());
    await new Promise((r) => setTimeout(r, 180));
    client.destroy();
    assert.equal(connections, 0);
  } finally {
    await proxy.close();
    await close(target);
  }
});
