import http from "node:http";
import fs from "node:fs";
import { timingSafeEqual } from "node:crypto";
import { fileURLToPath } from "node:url";
import { BrowserRuntime } from "./runtime.mjs";
import { validateOptions, validateInput } from "./policy.mjs";
import { diagnostic, diagnosticStatus } from "./diagnostics.mjs";

export async function createController({
  token,
  runtime = new BrowserRuntime(),
  host = "0.0.0.0",
  port = 9227,
} = {}) {
  if (typeof token !== "string" || token.length < 16)
    throw new Error(
      "Browser control token must contain at least 16 characters",
    );
  const credential = Buffer.from("Bearer " + token);
  const send = (res, status, body) => {
    const value = Buffer.from(JSON.stringify(body));
    res.writeHead(status, {
      "Content-Type": "application/json",
      "Content-Length": value.length,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(value);
  };
  const json = async (req) => {
    let length = 0;
    const chunks = [];
    for await (const chunk of req) {
      length += chunk.length;
      if (length > 65536) {
        const e = new Error("Request body too large");
        e.status = 413;
        throw e;
      }
      chunks.push(chunk);
    }
    try {
      return JSON.parse(Buffer.concat(chunks).toString() || "{}");
    } catch {
      throw new Error("Invalid JSON body");
    }
  };
  const server = http.createServer(async (req, res) => {
    const auth = Buffer.from(req.headers.authorization || "");
    if (
      auth.length !== credential.length ||
      !timingSafeEqual(auth, credential)
    ) {
      send(res, 401, { error: "Authentication required" });
      return;
    }
    try {
      const path = new URL(req.url, "http://controller").pathname;
      if (req.method === "GET" && path === "/health") {
        send(res, 200, runtime.health());
        return;
      }
      if (req.method === "POST" && path === "/capture") {
        const options = validateOptions(await json(req));
        send(res, 200, await runtime.capture(options));
        return;
      }
      if (req.method === "POST" && path === "/sessions") {
        const options = validateOptions(await json(req));
        send(res, 200, await runtime.createSession(options));
        return;
      }
      const m = path.match(
        /^\/sessions\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\/(frame\.png|input|capture|close))?$/,
      );
      if (m) {
        const [, id, action] = m;
        if (req.method === "GET" && !action) {
          send(res, 200, runtime.describe(runtime.session(id)));
          return;
        }
        if (req.method === "GET" && action === "frame.png") {
          const image = await runtime.frame(id);
          res.writeHead(200, {
            "Content-Type": "image/png",
            "Content-Length": image.length,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
          });
          res.end(image);
          return;
        }
        if (req.method === "POST" && action === "input") {
          send(
            res,
            200,
            await runtime.input(id, validateInput(await json(req))),
          );
          return;
        }
        if (req.method === "POST" && action === "capture") {
          await json(req);
          send(res, 200, await runtime.captureSession(id));
          return;
        }
        if (req.method === "POST" && action === "close") {
          await json(req);
          send(res, 200, await runtime.closeSession(id));
          return;
        }
      }
      send(res, 404, { error: "Browser operation not found" });
    } catch (e) {
      const message = String(e.message || "Browser operation failed");
      const info = diagnostic(e);
      if (info.code !== "browser_operation_failed") {
        send(res, diagnosticStatus(info.code), { error: info.message, code: info.code });
      } else if (e.status === 413) {
        send(res, 413, { error: "Request body too large", code: "invalid_request" });
      } else if (/^Browser session (?:not found or expired|expired)$/.test(message)) {
        send(res, 404, { error: "Browser session not found or expired", code: "session_unavailable" });
      } else if (/already active|busy/.test(message)) {
        send(res, 409, { error: "Browser operation already active or busy", code: "browser_busy" });
      } else if (/^Invalid |^Unsupported |^Request body|^A browser session|^Capture options/.test(message)) {
        send(res, 400, { error: "Invalid browser request", code: "invalid_request" });
      } else {
        send(res, 502, { error: info.message, code: info.code });
      }
    }
  });
  server.requestTimeout = 70000;
  server.headersTimeout = 10000;
  server.keepAliveTimeout = 1000;
  server.maxConnections = 20;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  return {
    server,
    runtime,
    close: async () => {
      await runtime.close();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const tokenFile = process.env.BROWSER_CONTROL_TOKEN_FILE;
  if (!tokenFile) throw new Error("BROWSER_CONTROL_TOKEN_FILE is required");
  const controller = await createController({
    token: fs.readFileSync(tokenFile, "utf8").trim(),
    host: process.env.BROWSER_CONTROL_HOST || "0.0.0.0",
    port: Number(process.env.BROWSER_CONTROL_PORT || 9227),
  });
  console.info("LeakScope browser controller ready");
  const stop = async () => {
    await controller.close();
    process.exit(0);
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
}
