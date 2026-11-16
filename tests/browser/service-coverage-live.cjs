// Real browser verification against local fixture responses: no provider searches or target probes.
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../..");
const output = path.join(root, ".runtime/service-coverage-ui");
const endpointID = "11111111-1111-4111-8111-111111111111";
const rows = [
  ["elastic", "Elasticsearch", "database", "tcp", 9200],
  ["mongo", "MongoDB", "database", "tcp", 27017],
  ["minio", "MinIO", "storage", "tcp", 9000],
  ["modbus", "Modbus TCP", "ot", "tcp", 502],
  ["bacnet", "BACnet/IP", "ot", "udp", 47808],
  ["s7", "Siemens S7", "ot", "tcp", 102],
  ["opcua", "OPC UA", "ot", "tcp", 4840],
  ["mqtt", "MQTT", "messaging", "tcp", 1883],
  ["rtsp", "RTSP", "iot", "tcp", 554],
  ["tasmota", "Tasmota", "iot", "tcp", 80],
  ["coap", "CoAP", "iot", "udp", 5683],
].map(([key, label, category, transport, port]) => ({
  key,
  label,
  category,
  categories: key === "mqtt" ? ["messaging", "iot"] : [category],
  legacy: ["elastic", "mongo"].includes(key),
  discovery: true,
  transport,
  ports: [port],
  fetch_actions: [transport],
  default_actions: [],
  discovery_modes: {
    fingerprint: ["shodan", "zoomeye"],
    nonstandard: ["shodan", "zoomeye"],
    ports: ["shodan", "zoomeye", "netlas"],
    legacy: ["elastic", "mongo"].includes(key) ? ["shodan", "zoomeye"] : [],
  },
  query_variants: {
    fingerprint: {
      shodan: {
        supported: true,
        query: '"' + label + '"',
        basis: "Indexed response banner",
      },
      netlas: { supported: false, reason: "No verified fingerprint" },
    },
    nonstandard: {
      shodan: {
        supported: true,
        query: '"' + label + '" -port:' + port,
        basis: "Banner excluding usual ports",
      },
    },
    ports: {
      shodan: {
        supported: true,
        query: "port:" + port,
        basis: "Port-only candidate",
      },
    },
  },
}));
const endpoint = {
  id: endpointID,
  address: "192.0.2.7",
  port: 32517,
  transport: "tcp",
  scheme: "",
  service_type: "modbus",
  title: "Modbus evidence fixture",
  fetch_actions: ["tcp", "identity"],
  default_actions: ["tcp"],
  tags: [],
};
const catalog = {
  results: rows,
  tools: { curl: true, httpx: true, gowitness: true },
  providers: {
    shodan: { configured: true },
    zoomeye: { configured: true },
    netlas: { configured: true },
  },
};
const calls = [];
function pageHTML(page) {
  return fs
    .readFileSync(
      path.join(root, "leakscope_app/templates/workbench/app.html"),
      "utf8",
    )
    .replace("{% load static %}", "")
    .replace(/\{% static '([^']+)' %\}/g, "/static/$1")
    .replace("{{ csrf_token }}", "fixture")
    .replace("{{ page|default:'overview' }}", page)
    .replace("{{ type|default:'' }}", "")
    .replace("{{ target_id|default:'' }}", "");
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const chrome =
    process.env.CHROME_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const browser = await chromium.launch({
    headless: true,
    ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}),
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  await context.route("**/*", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      pathname = url.pathname;
    if (url.hostname !== "fixture.test") return route.abort();
    if (pathname.startsWith("/static/")) {
      const filename = path.join(root, "leakscope_app", pathname.slice(1));
      if (!fs.existsSync(filename))
        return route.fulfill({ status: 204, body: "" });
      return route.fulfill({
        status: 200,
        contentType: pathname.endsWith(".js")
          ? "application/javascript"
          : pathname.endsWith(".css")
            ? "text/css"
            : "image/x-icon",
        body: fs.readFileSync(filename),
      });
    }
    if (pathname === "/artifacts/wire")
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          events: [
            { direction: "sent", hex: "6869", text: "hi", bytes: 2 },
            {
              direction: "received",
              hex: "3c7363726970743e",
              text: "<script>window.injected=true</script>",
              bytes: 8,
            },
          ],
        }),
      });
    if (pathname.startsWith("/api/")) {
      const body = req.postDataJSON();
      calls.push({ pathname, body });
      let data = { results: [] };
      if (pathname === "/api/types") data = { results: rows };
      if (pathname === "/api/query-preview")
        data = {
          queries: (body.types || []).map((type) => ({
            type,
            ...rows.find((x) => x.key === type)?.query_variants[
              body.query_mode
            ]?.[body.provider],
          })),
        };
      if (pathname === "/api/discover")
        data = {
          runs: [{ id: "fixture-done", state: "succeeded" }],
          results_url: "/assets/",
        };
      if (pathname === "/api/operations/catalog") data = catalog;
      if (pathname === "/api/operations/endpoints/" + endpointID)
        data = {
          endpoint,
          observations: [],
          artifacts: [
            {
              kind: "wire-transcript",
              url: "/artifacts/wire",
              tool: "tcp",
              size: 250,
              created_at: "2026-11-07T10:00:00Z",
              metadata: { wire_summary: { bytes_sent: 2, bytes_received: 8 } },
            },
          ],
        };
      if (pathname === "/api/operations/tags")
        data = { tags: [], manual_tags: [], inherited_tags: [] };
      if (pathname.endsWith("/fetch"))
        data = { run: { id: "fixture-done", state: "succeeded" } };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: pageHTML(
        pathname.startsWith("/assets")
          ? "assets"
          : pathname.startsWith("/technologies")
            ? "catalog"
            : "discover",
      ),
    });
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    for (const [name, width, height] of [
      ["desktop", 1440, 1000],
      ["mobile", 390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      await page.goto("http://fixture.test/discover/");
      await page.waitForSelector("#page-root[aria-busy=false]");
      assert.equal(
        await page.locator("[name=query_mode]").inputValue(),
        "fingerprint",
      );
      await page
        .getByRole("button", { name: "ICS / SCADA / OT (4)", exact: true })
        .click();
      assert.equal(await page.locator("[name=types]:visible").count(), 4);
      await page.locator("[name=types][value=modbus]").check();
      await page.locator("[name=query_mode]").selectOption("nonstandard");
      await page.waitForFunction(() =>
        document.querySelector(".code-box").textContent.includes("-port:502"),
      );
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: path.join(output, "discover-ot-" + name + ".png"),
        fullPage: true,
      });
      await page.locator("[name=service_category]").selectOption("iot");
      await page.locator("[name=service_search]").fill("tasmota");
      assert.equal(await page.locator("[name=types]:visible").count(), 1);
      await page
        .getByRole("button", { name: "Run discovery →", exact: true })
        .click();
      await page
        .getByRole("link", { name: "Open observations →", exact: true })
        .last()
        .waitFor({ state: "visible" });
      assert.equal(
        calls.filter((x) => x.pathname === "/api/discover").at(-1).body
          .query_mode,
        "nonstandard",
      );
      await page.goto("http://fixture.test/assets/?endpoint=" + endpointID);
      await page.waitForSelector("#page-root[aria-busy=false]");
      assert.equal(
        await page.locator("[name=actions][value=screenshot]").isDisabled(),
        true,
      );
      await page.locator("[name=tcp_encoding]").selectOption("text");
      await page.locator("[name=tcp_payload]").fill("hi");
      await page
        .getByRole("button", { name: "Fetch selected actions", exact: true })
        .click();
      await page.waitForFunction(
        () =>
          document.querySelector(".run-inline") ||
          document.body.textContent.includes("View runs"),
      );
      assert.deepEqual(
        calls.filter((x) => x.pathname.endsWith("/fetch")).at(-1).body.options,
        {
          tcp: {
            payload: "hi",
            encoding: "text",
            tls: false,
            timeout_seconds: 3,
          },
        },
      );
      await page
        .getByRole("button", { name: "Inspect transcript", exact: true })
        .click();
      await page
        .locator(".ops-transcript")
        .filter({ hasText: "<script>window.injected=true</script>" })
        .waitFor();
      assert.equal(await page.evaluate(() => window.injected), undefined);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: path.join(output, "evidence-" + name + ".png"),
        fullPage: true,
      });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        name + " overflow",
      );
    }
    await page.goto("http://fixture.test/technologies/");
    await page.waitForSelector("#page-root[aria-busy=false]");
    await page.locator("[name=category]").selectOption("ot");
    await page.locator(".ops-query-details").first().locator("summary").click();
    assert.ok(
      await page
        .getByText("No verified fingerprint", { exact: true })
        .first()
        .isVisible(),
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(output, "catalog-mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(output, "report.json"),
      JSON.stringify(
        {
          checkedAt: new Date().toISOString(),
          viewports: [1440, 390],
          errors,
          discoveryRequests: calls.filter(
            (x) => x.pathname === "/api/discover",
          ),
          fetchRequests: calls.filter((x) => x.pathname.endsWith("/fetch")),
        },
        null,
        2,
      ),
    );
    console.log(
      "Service coverage Chrome fixtures passed at 1440px and 390px. Screenshots: " +
        output,
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
