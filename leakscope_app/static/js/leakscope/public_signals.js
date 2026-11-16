(function () {
  "use strict";
  const PROVIDERS = [
    ["shodan", "Shodan"],
    ["zoomeye", "ZoomEye"],
    ["netlas", "Netlas"],
  ];
  const LABELS = {
    "entra.tenant": "Microsoft Entra tenant",
    "tracking.google_analytics": "Google Analytics",
    "tracking.google_tag_manager": "Google Tag Manager",
    "tracking.meta_pixel": "Meta Pixel",
    "tracking.linkedin_partner": "LinkedIn partner",
    "tracking.microsoft_uet": "Microsoft UET",
    "tracking.microsoft_clarity": "Microsoft Clarity",
    "tracking.hubspot_portal": "HubSpot portal",
    "tracking.tiktok_pixel": "TikTok Pixel",
    "tracking.hotjar_site": "Hotjar site",
    "tracking.google_ads": "Google Ads",
    "tracking.google_adsense": "Google AdSense",
    "declaration.openapi.server": "OpenAPI server",
    "declaration.swagger.server": "Swagger server",
    "declaration.asyncapi.server": "AsyncAPI server",
    "declaration.wsdl.server": "WSDL server",
    "declaration.openapi.oauth_authorize": "OpenAPI OAuth authorization",
    "declaration.openapi.oauth_token": "OpenAPI OAuth token",
    "declaration.swagger.oauth_authorize": "Swagger OAuth authorization",
    "declaration.swagger.oauth_token": "Swagger OAuth token",
  };
  function el(tag, attrs = {}, ...children) {
    const n = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") n.className = value;
      else if (key.startsWith("on")) n.addEventListener(key.slice(2), value);
      else if (["disabled", "open", "readOnly"].includes(key)) n[key] = !!value;
      else n.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity))
      if (child != null && child !== false)
        n.append(
          child instanceof Node
            ? child
            : document.createTextNode(String(child)),
        );
    return n;
  }
  const button = (label, onclick) =>
    el("button", { type: "button", class: "btn btn-small", onclick }, label);
  const notice = (value, error = false) =>
    el(
      "p",
      {
        class: "notice" + (error ? " notice-error" : ""),
        role: error ? "alert" : "status",
      },
      value,
    );
  const count = (value) =>
    Number.isFinite(Number(value))
      ? Math.max(0, Number(value)).toLocaleString()
      : "0";
  function safeURL(value) {
    try {
      const u = new URL(value);
      return ["http:", "https:"].includes(u.protocol) &&
        !u.username &&
        !u.password
        ? u.href
        : null;
    } catch (_) {
      return null;
    }
  }
  async function request(path, payload, signal) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken":
          document.querySelector('meta[name="csrf-token"]')?.content || "",
      },
      body: JSON.stringify(payload),
    });
    let dto;
    try {
      dto = await response.json();
    } catch (_) {
      throw Error("The server returned an unreadable response. Try again.");
    }
    if (!response.ok)
      throw Error(dto.error || dto.detail || "The request failed.");
    return dto;
  }
  function card(row, { signal } = {}) {
    const label =
      LABELS[row.kind] ||
      String(row.kind || "Public identifier").replaceAll("_", " ");
    const refs = Array.isArray(row.proof_refs) ? row.proof_refs : [];
    const reference =
      refs.find(
        (ref) =>
          ref.id === row.reference?.id && ref.type === row.reference?.type,
      ) ||
      row.reference ||
      refs[0];
    const kind = String(row.kind || "");
    const family =
      kind === "entra.tenant"
        ? "Identity declaration"
        : kind.startsWith("tracking.")
          ? "Public marketing identifier"
          : "API / service declaration";
    const details = el("details", {
      class: "public-signal-card",
      "data-public-signal-kind": kind,
    });
    const identity = el(
      "code",
      { class: "public-signal-value" },
      row.value || "",
    );
    const facts = el(
      "div",
      { class: "public-signal-facts" },
      el(
        "span",
        {},
        count(row.endpoint_count) +
          " endpoint" +
          (Number(row.endpoint_count) === 1 ? "" : "s"),
      ),
      el(
        "span",
        {},
        count(row.domain_count) +
          " domain" +
          (Number(row.domain_count) === 1 ? "" : "s"),
      ),
    );
    const summary = el(
      "summary",
      {},
      el(
        "div",
        { class: "public-signal-title" },
        el("small", {}, family),
        el("strong", {}, label),
      ),
      identity,
      facts,
    );
    const output = el("div", {
      "aria-live": "polite",
      class: "public-signal-preview",
    });
    const actions = el("div", { class: "public-signal-actions" });
    if (reference?.id) {
      actions.append(
        button("Inspect saved proof", () =>
          window.LeakScopeInspector?.open(reference, { tab: "Evidence" }),
        ),
      );
    }
    const extra = refs.filter((ref) => ref.id && ref !== reference);
    if (reference?.artifact_id)
      actions.append(
        el(
          "a",
          {
            class: "btn btn-small",
            href:
              "/api/operations/artifacts/" +
              encodeURIComponent(reference.artifact_id),
            target: "_blank",
            rel: "noopener noreferrer",
          },
          "Open retained capture ↗",
        ),
      );
    const body = el(
      "div",
      { class: "public-signal-body" },
      el(
        "p",
        { class: "small muted" },
        row.basis ||
          "Public configuration can connect unrelated properties. Review the saved evidence before inferring ownership or reachability.",
      ),
      actions,
      reference?.observation_id
        ? el(
            "p",
            { class: "small muted" },
            "Saved observation: ",
            el("code", {}, reference.observation_id),
          )
        : null,
    );
    if (row.last_seen && Number.isFinite(Date.parse(row.last_seen)))
      body.append(
        el(
          "p",
          { class: "small muted" },
          "Last seen in saved evidence: ",
          el(
            "time",
            { datetime: row.last_seen },
            new Date(row.last_seen).toLocaleString(),
          ),
        ),
      );
    if (extra.length) {
      const proofList = el(
        "details",
        { class: "public-signal-proofs" },
        el("summary", {}, "More saved proof · " + extra.length),
        el(
          "div",
          { class: "public-signal-actions" },
          extra.map((ref, index) =>
            button("Inspect proof " + (index + 2), () =>
              window.LeakScopeInspector?.open(ref, { tab: "Evidence" }),
            ),
          ),
        ),
      );
      body.append(proofList);
    }
    const payload = row.preview_payload;
    if (payload && (payload.evidence_id || (payload.kind && payload.value))) {
      const preview = button("Preview provider queries", async () => {
        if (preview.disabled) return;
        preview.disabled = true;
        output.replaceChildren(notice("Compiling local query previews…"));
        try {
          const dto = await request(
            "/api/intelligence/pivot-queries",
            payload,
            signal,
          );
          if (signal?.aborted || !details.isConnected) return;
          output.replaceChildren(
            el(
              "p",
              { class: "small muted" },
              dto.basis ||
                "Literal indexed-body matches are investigation leads, not ownership claims.",
            ),
            notice(
              "Local preview only. Saving a template makes no provider request. Run it later with Demo in Saved queries.",
            ),
            el(
              "div",
              { class: "public-signal-provider-grid" },
              PROVIDERS.map(([key, name]) =>
                providerCard(
                  key,
                  name,
                  dto.queries?.[key] || {},
                  row,
                  reference,
                  signal,
                ),
              ),
            ),
          );
        } catch (error) {
          if (
            error.name !== "AbortError" &&
            !signal?.aborted &&
            details.isConnected
          )
            output.replaceChildren(notice(error.message, true));
        } finally {
          if (!signal?.aborted) preview.disabled = false;
        }
      });
      actions.append(preview);
    }
    body.append(output);
    details.append(summary, body);
    return details;
  }
  function providerCard(provider, name, query, row, reference, signal) {
    const card = el(
      "article",
      { class: "public-signal-provider" },
      el("h3", {}, name),
    );
    if (!query.supported || !query.query) {
      card.append(
        notice(
          query.reason || "A literal query is unavailable for this provider.",
        ),
      );
      return card;
    }
    const value = el("textarea", {
      class: "input public-signal-query",
      rows: 3,
      readOnly: true,
      "aria-label": name + " query",
    });
    value.value = String(query.query);
    const output = el("div", { "aria-live": "polite" });
    const save = button("Save " + name + " template", async () => {
      if (save.disabled) return;
      save.disabled = true;
      output.replaceChildren(notice("Saving a local query template…"));
      try {
        const label = LABELS[row.kind] || row.kind || "Public identifier";
        const description =
          "Literal indexed-body research lead from retained " +
          row.kind +
          " evidence. Shared identifiers do not establish common ownership. Saved proof: " +
          JSON.stringify(reference || {}) +
          ". No search was run when this template was saved.";
        const dto = await request(
          "/api/queries",
          {
            name: (
              label +
              " · " +
              String(row.value || "").slice(0, 90) +
              " · " +
              name
            ).slice(0, 255),
            description,
            provider,
            query_template: String(query.query),
            default_type: "",
            active_probe_default: false,
          },
          signal,
        );
        if (signal?.aborted || !card.isConnected) return;
        if (!dto.query?.id)
          throw Error("The server did not return a saved query.");
        output.replaceChildren(
          notice("Template saved. No provider request was made."),
          el(
            "a",
            {
              class: "btn btn-small",
              href:
                "/custom-queries/?id=" +
                encodeURIComponent(dto.query.id) +
                "&demo=1",
            },
            "Open saved query · Demo",
          ),
        );
      } catch (error) {
        if (
          error.name !== "AbortError" &&
          !signal?.aborted &&
          card.isConnected
        ) {
          output.replaceChildren(notice(error.message, true));
          save.disabled = false;
        }
      }
    });
    const controls = el(
      "div",
      { class: "public-signal-actions" },
      button("Select query", () => {
        value.focus();
        value.select();
      }),
      save,
    );
    const url = safeURL(query.url);
    if (url)
      controls.append(
        el(
          "a",
          { href: url, target: "_blank", rel: "noopener noreferrer" },
          "Open provider manually ↗",
        ),
      );
    card.append(
      value,
      controls,
      (query.warnings || []).map((warning) =>
        el("p", { class: "small muted" }, warning),
      ),
      output,
    );
    return card;
  }
  window.LeakScopePublicSignals = {
    card,
    label: (kind) => LABELS[kind] || String(kind || "").replaceAll("_", " "),
  };
})();
