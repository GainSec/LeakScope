# Workbench browser checks

Install local test dependencies with `npm ci` from the repository root, then run:

```sh
npm test
npm run test:browser
```

`npm test` runs pure utility and fixture DOM checks with Node's test runner and jsdom. These checks make no network requests. They cover all ten page controllers, untrusted string rendering, explicit sensitive-field reveal, custom-query parameters and terminal-state helpers.

`npm run test:browser` uses Playwright against `http://127.0.0.1:2629` by default. Override with `LEAKSCOPE_BASE_URL`. It uses the installed Google Chrome on macOS; set `CHROME_EXECUTABLE` for a different executable, or install Playwright Chromium for other platforms. The live smoke visits pages, checks direct access, console errors and overflow, and tests the narrow-screen navigation. It does not click account checks, run searches or start active tools. Screenshots and the report are written under ignored `.runtime/browser-qa/`.

Browser tooling is development-only and is not installed in the application image. Node 22 or newer is required for the locked test dependencies.
