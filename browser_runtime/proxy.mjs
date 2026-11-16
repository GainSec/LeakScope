import http from "node:http";
import net from "node:net";
import { LIMITS } from "./policy.mjs";
import { diagnostic } from "./diagnostics.mjs";

export async function createProxy(
  policy,
  { deadline = Date.now() + 60000 } = {},
) {
  const state = {
    bytes: 0,
    requests: 0,
    blocked: [],
    failures: [],
    expired: false,
    budget_exhausted: false,
  };
  const sockets = new Set();
  const failure = (host, port, error) => {
    if (state.failures.length < LIMITS.diagnostics) {
      const info = diagnostic(error);
      state.failures.push({ host, port, code: info.code === "browser_operation_failed" ? "network_error" : info.code });
    }
  };
  const note = (host, reason) => {
    if (state.blocked.length < LIMITS.diagnostics)
      state.blocked.push({
        host: String(host).slice(0, 253),
        reason: String(reason).slice(0, 120),
      });
  };
  const check = () => {
    if (Date.now() >= deadline) {
      state.expired = true;
      throw new Error("Browser network deadline exceeded");
    }
    if (state.bytes > LIMITS.bytes || state.requests >= LIMITS.requests) {
      state.budget_exhausted = true;
      throw new Error("Browser network budget exhausted");
    }
  };
  const count = (b) => {
    state.bytes += b.length;
    try {
      check();
    } catch {
      for (const s of sockets) s.destroy();
    }
  };
  const track = (s) => {
    sockets.add(s);
    s.on("close", () => sockets.delete(s));
    s.on("error", () => {});
    s.setTimeout(30000, () => s.destroy());
    return s;
  };
  const destination = async (host, port) => {
    check();
    state.requests++;
    let timer;
    try {
      const result = await Promise.race([
        policy.destination(host, port),
        new Promise((_, reject) => {
          timer = setTimeout(
            () => reject(new Error("DNS resolution timed out")),
            5000,
          );
        }),
      ]);
      check();
      return result;
    } finally {
      clearTimeout(timer);
    }
  };
  const server = http.createServer(async (req, res) => {
    let host = "";
    try {
      const u = new URL(req.url);
      host = u.hostname.replace(/^\[|\]$/g, "");
      if (u.protocol !== "http:" || u.username || u.password)
        throw new Error("Only HTTP proxy requests permitted");
      if (!["GET", "HEAD", "POST", "OPTIONS"].includes(req.method))
        throw new Error("HTTP method not permitted");
      const dest = await destination(host, Number(u.port || 80));
      const headers = { ...req.headers, host: u.host };
      delete headers["proxy-authorization"];
      delete headers["proxy-connection"];
      delete headers.connection;
      const upstream = http.request(
        {
          hostname: dest.address,
          family: dest.family,
          port: dest.port,
          method: req.method,
          path: u.pathname + u.search,
          headers,
          timeout: 30000,
        },
        (response) => {
          res.writeHead(response.statusCode, response.headers);
          response.on("data", count);
          response.pipe(res);
          response.on("error", () => res.destroy());
        },
      );
      upstream.on("socket", track);
      upstream.on("timeout", () => upstream.destroy(Object.assign(new Error(), { code: "ETIMEDOUT" })));
      upstream.on("error", (error) => {
        failure(host, Number(u.port || 80), error);
        if (!res.headersSent) res.writeHead(502);
        res.end();
      });
      let sent = 0;
      req.on("data", (b) => {
        sent += b.length;
        count(b);
        if (sent > LIMITS.body) {
          upstream.destroy();
          req.destroy();
        }
      });
      req.pipe(upstream);
    } catch (e) {
      note(host, e.message);
      res.writeHead(403, { "content-type": "text/plain", connection: "close" });
      res.end("Browser network policy blocked this request");
    }
  });
  server.on("connect", async (req, client, head) => {
    let host = "";
    try {
      const u = new URL("https://" + req.url);
      host = u.hostname.replace(/^\[|\]$/g, "");
      if (u.username || u.password || u.pathname !== "/" || u.search || u.hash)
        throw new Error("Invalid CONNECT destination");
      const dest = await destination(host, Number(u.port || 443));
      const upstream = track(
        net.connect({
          host: dest.address,
          port: dest.port,
          family: dest.family,
        }),
      );
      upstream.once("connect", () => {
        client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (head.length) {
          count(head);
          upstream.write(head);
        }
        client.on("data", count);
        upstream.on("data", count);
        client.pipe(upstream);
        upstream.pipe(client);
      });
      upstream.on("error", (error) => {
        failure(host, Number(u.port || 443), error);
        client.destroy();
      });
      client.on("close", () => upstream.destroy());
      upstream.on("close", () => client.destroy());
    } catch (e) {
      note(host, e.message);
      client.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
    }
  });
  server.on("connection", track);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const timer = setTimeout(
    () => {
      state.expired = true;
      for (const s of sockets) s.destroy();
    },
    Math.max(1, deadline - Date.now()),
  );
  timer.unref();
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    state,
    note,
    close: async () => {
      clearTimeout(timer);
      for (const s of sockets) s.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}
