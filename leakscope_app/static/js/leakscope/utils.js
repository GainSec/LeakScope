(function (root) {
  "use strict";
  const terminal = new Set([
    "success",
    "succeeded",
    "failure",
    "failed",
    "skipped",
    "cancelled",
    "canceled",
    "revoked",
    "lost",
    "expired",
    "paused",
  ]);
  const api = {
    safeUrl(value) {
      try {
        const url = new URL(String(value));
        return ["http:", "https:"].includes(url.protocol) ? url.href : null;
      } catch (_) {
        return null;
      }
    },
    isTerminal(value) {
      return terminal.has(String(value || "").toLowerCase());
    },
    state(value) {
      const s = String(value || "queued").toLowerCase();
      return (
        { success: "succeeded", failure: "failed", revoked: "cancelled" }[s] ||
        s
      );
    },
    startedRuns(value) {
      return (
        value.runs ||
        (value.run
          ? [value.run]
          : value.run_id || value.id || value.task_id
            ? [
                {
                  id: value.run_id || value.id || value.task_id,
                  task_id: value.task_id,
                  state: "queued",
                },
              ]
            : [])
      );
    },
    isSensitive(name) {
      return /secret|password|token|credential|api.?key|context/i.test(
        String(name),
      );
    },
    progress(run) {
      if (run.kind === "domain_expansion" || run.progress_mode === "indeterminate") return null;
      const total = Number(run.progress_total || run.total || 0),
        current = Number(run.progress_current || run.current || 0);
      return total > 0
        ? Math.max(0, Math.min(100, Math.round((current / total) * 100)))
        : null;
    },
    text(value) {
      if (value == null) return "—";
      return typeof value === "object"
        ? JSON.stringify(value, null, 2)
        : String(value);
    },
    date(value) {
      if (!value) return "—";
      const d = new Date(value);
      return Number.isNaN(d.getTime())
        ? String(value)
        : d.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          });
    },
    number(value) {
      return new Intl.NumberFormat().format(Number(value || 0));
    },
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.LeakScopeUI = api;
})(typeof window !== "undefined" ? window : globalThis);
