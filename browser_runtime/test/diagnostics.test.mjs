import test from "node:test";
import assert from "node:assert/strict";
import { diagnostic } from "../diagnostics.mjs";

test("browser diagnostics classify causes without returning raw error text", () => {
  for (const [error, code] of [
    [Object.assign(new Error("secret http://internal/?token=x"), { name: "TimeoutError" }), "timeout"],
    [new Error("page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://secret/"), "tls_certificate"],
    [Object.assign(new Error("connect secret"), { code: "ECONNREFUSED" }), "connection_refused"],
    [new Error("page.goto: net::ERR_NAME_NOT_RESOLVED at https://secret/"), "dns_resolution"],
    [new Error("page.goto: net::ERR_CONNECTION_RESET at https://secret/"), "connection_reset"],
    [new Error("page.goto: net::ERR_INVALID_AUTH_CREDENTIALS at https://secret/"), "auth_required"],
    [new Error("secret arbitrary failure"), "browser_operation_failed"],
  ]) {
    const result = diagnostic(error);
    assert.equal(result.code, code);
    assert.ok(!JSON.stringify(result).includes("secret"));
  }
});
