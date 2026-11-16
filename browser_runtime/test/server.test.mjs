import test from "node:test";
import assert from "node:assert/strict";
import { createController } from "../server.mjs";

test("controller preserves safe timeout codes and redacts untrusted exception messages", async () => {
  const controller = await createController({
    token: "test-private-token-value", host: "127.0.0.1", port: 0,
    runtime: {
      capture: async () => { throw Object.assign(new Error("secret http://controller/?token=x"), { name: "TimeoutError" }); },
      close: async () => {},
    },
  });
  try {
    const response = await fetch(`http://127.0.0.1:${controller.server.address().port}/capture`, {
      method: "POST", headers: { Authorization: "Bearer test-private-token-value" },
      body: JSON.stringify({ url: "http://127.0.0.1:1" }),
    });
    assert.equal(response.status, 504);
    assert.deepEqual(await response.json(), { error: "Browser operation timed out", code: "timeout" });
  } finally { await controller.close(); }
});

test("controller requires token for health/session/capture and uses bounded JSON", async () => {
  let calls = 0;
  const runtime = {
    health: () => ({ available: true }),
    capture: async (o) => {
      calls++;
      return { metadata: { url: o.url }, artifacts: [] };
    },
    close: async () => {},
  };
  const controller = await createController({
    token: "test-private-token-value",
    runtime,
    host: "127.0.0.1",
    port: 0,
  });
  const base = `http://127.0.0.1:${controller.server.address().port}`;
  try {
    assert.equal((await fetch(base + "/health")).status, 401);
    assert.equal(
      (await fetch(base + "/capture", { method: "POST", body: "{}" })).status,
      401,
    );
    assert.equal(calls, 0);
    const headers = {
      Authorization: "Bearer test-private-token-value",
      "Content-Type": "application/json",
    };
    assert.equal((await fetch(base + "/health", { headers })).status, 200);
    assert.equal(
      (
        await fetch(base + "/capture", {
          method: "POST",
          headers,
          body: JSON.stringify({ url: "file:///tmp/x" }),
        })
      ).status,
      400,
    );
    assert.equal(calls, 0);
    assert.equal(
      (
        await fetch(base + "/capture", {
          method: "POST",
          headers,
          body: JSON.stringify({ url: "https://example.com" }),
        })
      ).status,
      200,
    );
    assert.equal(calls, 1);
    assert.equal(
      (
        await fetch(base + "/capture", {
          method: "POST",
          headers,
          body: "x".repeat(70000),
        })
      ).status,
      413,
    );
    assert.equal(
      (
        await fetch(base + "/sessions/bad/capture", {
          method: "POST",
          headers,
          body: "{}",
        })
      ).status,
      404,
    );
  } finally {
    await controller.close();
  }
});
