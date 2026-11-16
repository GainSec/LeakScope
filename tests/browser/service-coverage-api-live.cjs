// Read-only Chrome verification against a local isolated Django test_settings server.
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const base = process.env.LEAKSCOPE_BASE_URL || "http://127.0.0.1:2630";
const output = path.resolve(".runtime/service-coverage-ui");
fs.mkdirSync(output, { recursive: true });
(async () => {
  const chrome =
    process.env.CHROME_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const browser = await chromium.launch({
    headless: true,
    ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const response = await page.request.get(base + "/api/types");
    assert.equal(response.status(), 200);
    const rows = (await response.json()).results;
    const categoryCount = (category) =>
      rows.filter((r) => (r.categories || [r.category]).includes(category))
        .length;
    assert.ok(categoryCount("ot") >= 18);
    assert.ok(categoryCount("iot") >= 14);
    for (const [label, width, height] of [
      ["desktop", 1440, 1000],
      ["mobile", 390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      await page.goto(base + "/discover/");
      await page.waitForSelector("#page-root[aria-busy=false]");
      assert.equal(await page.locator("[name=types]:checked").count(), 0, "Discover must not preselect an unrelated service");
      await page
        .getByRole("button", {
          name: "ICS / SCADA / OT (" + categoryCount("ot") + ")",
          exact: true,
        })
        .click();
      assert.equal(
        await page.locator("[name=types]:visible").count(),
        categoryCount("ot"),
      );
      await page.locator("[name=types][value=s7]").check();
      await page.waitForFunction(() =>
        document.querySelector(".code-box").textContent.includes("shodan.module:s7"),
      );
      assert.equal(await page.locator("[name=types]:checked").count(), 1);
      const allPorts = await page.locator(".code-box").first().textContent();
      assert.ok(allPorts.includes("All indexed ports"));
      assert.ok(allPorts.includes("Matches:"));
      assert.ok(!allPorts.includes("Elasticsearch"));
      assert.ok(!/[-]?port:[0-9]/.test(allPorts));
      await page.screenshot({path: path.join(output, "api-s7-all-ports-" + label + ".png"), fullPage:true});
      await page.locator("[name=query_mode]").selectOption("nonstandard");
      await page.waitForFunction(() =>
        document.querySelector(".code-box").textContent.includes("-port:102"),
      );
      const preview = await page.locator(".code-box").first().textContent();
      assert.ok(!preview.includes("product:"));
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: path.join(output, "api-discover-ot-" + label + ".png"),
        fullPage: true,
      });
      await page.locator("[name=service_category]").selectOption("iot");
      await page.locator("[name=service_search]").fill("tasmota");
      assert.equal(await page.locator("[name=types]:visible").count(), 1);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: path.join(output, "api-discover-iot-" + label + ".png"),
        fullPage: true,
      });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        label + " Discover overflow",
      );
    }
    for (const key of ["mongo", "modbus"]) {
      await page.goto(base + "/discover/?service_type=" + key);
      await page.waitForSelector("#page-root[aria-busy=false]");
      assert.equal(await page.locator('[name=provider]:checked').inputValue(), rows.find(r => r.key === key).discovery_modes.fingerprint[0]);
      assert.equal(await page.locator('[name=types][value=' + key + ']').isChecked(), true);
      await page.waitForFunction(() => document.querySelector('.code-box').textContent.includes('All indexed ports'));
      assert.ok(!(await page.locator('.code-box').textContent()).includes('Elasticsearch'));
    }
    await page.goto(base + "/technologies/");
    await page.waitForSelector("#page-root[aria-busy=false]");
    await page.locator("[name=catalog_search]").fill("s7");
    await page.locator(".ops-query-details summary").click();
    assert.ok(
      (
        await page.locator(".ops-provider-preset pre").first().textContent()
      ).includes("shodan.module:s7"),
    );
    await page.locator("[name=catalog_query_mode]").selectOption("nonstandard");
    await page.locator(".ops-query-details summary").click();
    assert.ok(
      (
        await page.locator(".ops-provider-preset pre").first().textContent()
      ).includes("-port:102"),
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(output, "api-catalog-s7-mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    const report = {
      base,
      checkedAt: new Date().toISOString(),
      types: rows.length,
      ot: categoryCount("ot"),
      iot: categoryCount("iot"),
      viewports: [1440, 390],
      errors,
    };
    fs.writeFileSync(
      path.join(output, "api-report.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
