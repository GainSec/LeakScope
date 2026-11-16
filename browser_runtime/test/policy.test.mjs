import test from "node:test";
import assert from "node:assert/strict";
import {
  BrowserPolicy,
  isPublicAddress,
  cleanURL,
  validateOptions,
  validateInput,
} from "../policy.mjs";

test("public destinations exclude non-global IPv4/IPv6 and mapped private addresses", () => {
  for (const ip of [
    "127.0.0.1",
    "10.0.0.1",
    "169.254.169.254",
    "100.64.0.1",
    "192.168.1.1",
    "0.0.0.0",
    "224.0.0.1",
    "198.18.0.1",
    "::1",
    "fc00::1",
    "fe80::1",
    "ff02::1",
    "::ffff:127.0.0.1",
    "2001:db8::1",
    "::",
  ])
    assert.equal(isPublicAddress(ip), false, ip);
  for (const ip of ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
    assert.equal(isPublicAddress(ip), true, ip);
});

test("strict stays on selected endpoint and preserves nonstandard ports", async () => {
  const p = new BrowserPolicy(
    { url: "http://plc.example:8089", profile: "strict" },
    async () => [{ address: "192.168.3.4", family: 4 }],
  );
  assert.equal(
    (await p.destination("plc.example", 8089)).address,
    "192.168.3.4",
  );
  await assert.rejects(p.destination("plc.example", 80), /profile/);
  await assert.rejects(p.destination("cdn.example", 8089), /profile/);
  assert.equal(p.navigation("http://plc.example:8089/status"), true);
  assert.equal(p.navigation("https://plc.example/"), false);
});

test("web profile pins DNS and permits only public dependencies and canonical same-site navigation", async () => {
  let calls = 0;
  const p = new BrowserPolicy(
    {
      url: "https://example.com",
      site_domain: "example.com",
      profile: "web_page",
    },
    async (host) => {
      calls++;
      return [
        {
          address: host === "private.example.com" ? "10.0.0.3" : "8.8.8.8",
          family: 4,
        },
      ];
    },
  );
  assert.equal(p.navigation("https://www.example.com/"), true);
  assert.equal(p.navigation("https://evil-example.com/"), false);
  assert.equal(p.navigation("https://www.example.com:555/"), false);
  assert.equal((await p.destination("cdn.assets.com", 443)).address, "8.8.8.8");
  await p.destination("cdn.assets.com", 443);
  assert.equal(calls, 1);
  await assert.rejects(p.destination("private.example.com", 443), /public/);
  await assert.rejects(p.destination("cdn.assets.com", 6666), /port/);
  await assert.rejects(p.destination("127.0.0.1", 443), /public/);
});

test("mixed DNS answers fail closed and endpoint DNS result pinned", async () => {
  const p = new BrowserPolicy(
    {
      url: "https://example.com",
      profile: "web_page",
      site_domain: "example.com",
    },
    async () => [
      { address: "8.8.8.8", family: 4 },
      { address: "127.0.0.1", family: 4 },
    ],
  );
  await assert.rejects(p.destination("cdn.example.net", 443), /public/);
});

test("URL diagnostics redact credentials, query and fragments and are bounded", () => {
  assert.equal(
    cleanURL("https://u:secret@a.test/path?token=secret#secret"),
    "https://a.test/path",
  );
  assert.ok(cleanURL("https://a.test/" + "x".repeat(2000)).length <= 512);
});

test("capture options reject unsafe URLs and unbounded parameters", () => {
  const o = validateOptions({
    url: "https://example.com",
    profile: "web_page",
    site_domain: "example.com",
  });
  assert.equal(o.timeout_seconds, 30);
  for (const url of [
    "file:///etc/passwd",
    "data:text/html,x",
    "https://u:p@example.com",
    "https://example.com/#fragment",
    "http://foo.local/%00",
  ])
    assert.throws(() => validateOptions({ url }));
  for (const options of [
    { url: "https://example.com", timeout_seconds: 61 },
    { url: "https://example.com", wait_seconds: 11 },
    { url: "https://example.com", profile: "oops" },
    { url: "https://example.com", site_domain: "evil.com" },
  ])
    assert.throws(() => validateOptions(options));
});

test("manual controls bounded, credentials/navigation/eval unsupported", () => {
  assert.deepEqual(validateInput({ type: "click", x: 100, y: 300 }), {
    type: "click",
    x: 100,
    y: 300,
  });
  assert.throws(() => validateInput({ type: "click", x: 1280, y: 1 }));
  assert.throws(() => validateInput({ type: "text", text: "a".repeat(1025) }));
  assert.throws(() => validateInput({ type: "key", key: "Control+L" }));
  assert.throws(() => validateInput({ type: "evaluate", source: "x" }));
});

test("IPv6 literal target accepts Python unbracketed site domain", () => {
  const o = validateOptions({ url: "http://[::1]:8080", site_domain: "::1" });
  assert.equal(o.site_domain, "::1");
  const p = new BrowserPolicy(o);
  assert.equal(p.navigation("http://[::1]:8080/path"), true);
});

test("DNS lookup is bounded when resolver never returns", async () => {
  const p = new BrowserPolicy(
    { url: "https://example.com" },
    () => new Promise(() => {}),
    { dnsTimeout: 20 },
  );
  await assert.rejects(p.destination("example.com", 443), /DNS.*timed out/);
});
