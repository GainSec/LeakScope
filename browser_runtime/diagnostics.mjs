// Only fixed descriptions cross the controller boundary. Playwright/Node error
// messages can contain target URLs, proxy credentials and browser launch paths.
const messages = Object.freeze({
  timeout: "Browser operation timed out",
  tls_certificate: "TLS certificate verification failed",
  connection_refused: "Target connection was refused",
  connection_reset: "Target connection was reset or closed",
  dns_resolution: "Target name could not be resolved",
  navigation_blocked: "Main navigation outside the selected endpoint was blocked",
  resource_blocked: "Requests were blocked by browser policy",
  http_error: "Main document returned an HTTP error",
  auth_required: "Main document requires authentication; no credentials were sent",
  network_error: "Target network request failed",
  browser_unavailable: "Browser executable is unavailable",
  capture_expired: "Browser capture deadline expired",
  browser_operation_failed: "Browser operation failed",
});

export function diagnostic(error) {
  let code = Object.hasOwn(messages, error?.code) ? error.code : null;
  const message = String(error?.message || "");
  if (!code) {
    if (error?.name === "TimeoutError" || /ETIMEDOUT|ERR_TIMED_OUT|DNS resolution timed out/.test(message) || error?.code === "ETIMEDOUT") code = "timeout";
    else if (/ERR_CERT_|CERT_HAS_EXPIRED|SELF_SIGNED_CERT|UNABLE_TO_VERIFY_LEAF_SIGNATURE/.test(message) || /CERT_|SELF_SIGNED_CERT/.test(error?.code || "")) code = "tls_certificate";
    else if (error?.code === "ECONNREFUSED" || /ERR_CONNECTION_REFUSED/.test(message)) code = "connection_refused";
    else if (["ENOTFOUND", "EAI_AGAIN"].includes(error?.code) || /ERR_NAME_NOT_RESOLVED|DNS lookup returned no addresses/.test(message)) code = "dns_resolution";
    else if (["ECONNRESET", "EPIPE"].includes(error?.code) || /ERR_CONNECTION_(?:RESET|CLOSED)|ERR_EMPTY_RESPONSE/.test(message)) code = "connection_reset";
    else if (/ERR_(?:INVALID_AUTH_CREDENTIALS|MISSING_AUTH_CREDENTIALS|PROXY_AUTH_REQUESTED)/.test(message)) code = "auth_required";
    else if (/Executable doesn't exist|Failed to launch|ENOENT/.test(message)) code = "browser_unavailable";
    else code = "browser_operation_failed";
  }
  return { code, message: messages[code] };
}

export function captureError(error, fallbackCode) {
  const info = diagnostic(error);
  const selected = info.code === "browser_operation_failed" && fallbackCode
    ? diagnostic({ code: fallbackCode }) : info;
  return Object.assign(new Error(selected.message), { code: selected.code });
}

export function diagnosticStatus(code) {
  return ["timeout", "capture_expired"].includes(code) ? 504 : 502;
}
