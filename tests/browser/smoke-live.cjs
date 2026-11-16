const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const baseURL = process.env.LEAKSCOPE_BASE_URL || "http://127.0.0.1:2629";
const artifactDir = path.resolve(".runtime/browser-qa");
(async () => {
  fs.mkdirSync(artifactDir, { recursive: true });
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
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const results = [];
  try {
    for (const route of [
      "/",
      "/discover/",
      "/findings/",
      "/runs/",
      "/custom-queries/",
      "/preview/",
      "/explore/",
      "/export-import/",
      "/settings/",
    ]) {
      const response = await page.goto(baseURL + route);
      assert.equal(response.status(), 200, route + " must open directly");
      await page.waitForSelector("#page-root[aria-busy=false]");
      const data = await page.evaluate(() => ({
        heading: document.querySelector("h1")?.textContent,
        overflow: document.documentElement.scrollWidth > innerWidth,
        loadError: document
          .querySelector("#page-root")
          ?.textContent.includes("The page could not load its data."),
      }));
      assert.ok(data.heading, route + " heading");
      assert.equal(data.overflow, false, route + " desktop overflow");
      assert.equal(data.loadError, false, route + " loaded");
      results.push({ route, ...data });
    }
    await page.goto(baseURL + "/");
    await page.waitForSelector("#page-root[aria-busy=false]");
    await page.screenshot({
      path: path.join(artifactDir, "overview-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of ["/", "/discover/", "/findings/", "/settings/"]) {
      await page.goto(baseURL + route);
      await page.waitForSelector("#page-root[aria-busy=false]");
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        route + " mobile overflow",
      );
    }
    await page.screenshot({
      path: path.join(artifactDir, "settings-mobile.png"),
      fullPage: true,
    });
    await page.locator("#menu-toggle").click();
    assert.equal(
      await page.locator("#menu-toggle").getAttribute("aria-expanded"),
      "true",
    );
    assert.deepEqual(errors, [], "No browser console or JavaScript errors");
    const report = {
      baseURL,
      checkedAt: new Date().toISOString(),
      routes: results,
      mobileWidth: 390,
      errors,
    };
    fs.writeFileSync(
      path.join(artifactDir, "report.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
