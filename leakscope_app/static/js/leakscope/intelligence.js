(function () {
  "use strict";
  const API = "/api/intelligence";
  const PROVIDERS = ["shodan", "zoomeye", "netlas"];
  function text(value) {
    return value == null
      ? ""
      : typeof value === "object"
        ? JSON.stringify(value, null, 2)
        : String(value);
  }
  function dataView(value, label = "Saved data") {
    return window.LeakScopeData?.viewer(value, {label}) || el("pre", {class:"code-box"}, text(value));
  }
  function el(tag, attrs = {}, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") element.className = value;
      else if (key === "text") element.textContent = text(value);
      else if (key.startsWith("on") && typeof value === "function")
        element.addEventListener(key.slice(2), value);
      else if (["checked", "disabled", "selected"].includes(key))
        element[key] = !!value;
      else element.setAttribute(key, value === true ? "" : text(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child != null && child !== false)
        element.append(
          child instanceof Node ? child : document.createTextNode(text(child)),
        );
    }
    return element;
  }
  function btn(label, action, cls = "btn") {
    return el("button", { type: "button", class: cls, onclick: action }, label);
  }
  function notice(message, error = false) {
    return el(
      "div",
      {
        class: "notice" + (error ? " notice-error" : ""),
        role: error ? "alert" : "status",
      },
      message && typeof message === "object" ? dataView(message, "Response details") : text(message),
    );
  }
  function warnings(items) {
    return (items || []).filter(Boolean).map((item) => notice(item));
  }
  function field(label, control) {
    return el(
      "label",
      { class: "field" },
      el("span", { class: "field-label" }, label),
      control,
    );
  }
  function demoControl(detail) {
    const box = el("input", {type:"checkbox",name:"demo"});
    return {box,control:el("label", {class:"check-row api-demo"}, box, el("span", {}, el("strong", {}, "Demo · minimal API use"), el("small", {}, detail)))};
  }
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
  function external(label, value, cls = "") {
    const url = safeURL(value);
    return url
      ? el(
          "a",
          {
            href: url,
            target: "_blank",
            rel: "noopener noreferrer",
            class: cls,
          },
          label,
        )
      : el("span", { class: "muted" }, label);
  }
  async function request(path, data, signal) {
    const response = await fetch(API + path, {
      method: data === undefined ? "GET" : "POST",
      credentials: "same-origin",
      signal,
      headers: {
        Accept: "application/json",
        ...(data === undefined
          ? {}
          : {
              "Content-Type": "application/json",
              "X-CSRFToken":
                document.querySelector("meta[name=csrf-token]")?.content || "",
            }),
      },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
    });
    let payload;
    try {
      payload = await response.json();
    } catch (_) {
      throw new Error("The server returned an unexpected response.");
    }
    if (!response.ok)
      throw new Error(
        text(payload.error || payload.detail || "Request failed. Try again."),
      );
    return payload;
  }
  async function copy(value, button) {
    try {
      if (navigator.clipboard?.writeText)
        await navigator.clipboard.writeText(text(value));
      else {
        const input = el(
          "textarea",
          { class: "intel-copy-buffer", readonly: true },
          text(value),
        );
        document.body.append(input);
        input.select();
        if (!document.execCommand?.("copy"))
          throw new Error("Copy unavailable");
        input.remove();
      }
      button.textContent = "Copied";
    } catch (_) {
      button.textContent = "Select the query to copy";
    }
  }
  function refsCopy(references) {
    if (!Array.isArray(references) || references.length > 10)
      throw new Error("Select up to 10 findings.");
    return references.map((ref) => ({ type: text(ref.type), id: ref.id }));
  }
  function modal(title, description) {
    document
      .querySelectorAll("dialog.intel-dialog")
      .forEach((item) => item.close());
    const controller = new AbortController();
    const titleId = "intel-dialog-title";
    const dialog = el("dialog", {
      class: "run-dialog intel-dialog",
      "aria-labelledby": titleId,
    });
    const body = el(
      "div",
      { class: "intel-dialog-body" },
      notice("Loading selected evidence…"),
    );
    const close = () => dialog.close();
    dialog.append(
      el(
        "div",
        { class: "dialog-heading" },
        el(
          "div",
          {},
          el("span", { class: "eyebrow" }, "RESULT INTELLIGENCE"),
          el("h2", { id: titleId }, title),
        ),
        btn("×", close, "icon-button"),
      ),
      el("p", { class: "muted intel-dialog-description" }, description),
      body,
    );
    dialog
      .querySelector("button")
      .setAttribute("aria-label", "Close " + title.toLowerCase());
    dialog.addEventListener("close", () => {
      controller.abort();
      dialog.remove();
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    document.body.append(dialog);
    dialog.showModal();
    return {
      dialog,
      body,
      signal: controller.signal,
      alive: () => dialog.isConnected && !controller.signal.aborted,
    };
  }
  function evidenceFields(fields, similar = false) {
    const holder = el("div", { class: "intel-fields" });
    for (const item of fields || []) {
      const preferred = [
        "title",
        "product",
        "device",
        "server",
        "body",
        "port",
        "certificate.sha256",
        "favicon.sha256",
        "favicon.mmh3",
      ];
      const checkbox = el("input", {
        type: "checkbox",
        name: "signals",
        value: item.id,
        checked: !similar || preferred.includes(item.field),
      });
      const signal = el("div", {class:"intel-signal"});
      signal.append(
        el(
          "label",
          { class: "check-row intel-evidence" },
          checkbox,
          el(
            "span",
            {},
            el("strong", {}, item.label || item.field),
            el("small", { class: "mono" }, item.value && typeof item.value === "object" ? (Array.isArray(item.value) ? item.value.length + " items" : Object.keys(item.value).length + " fields") : text(item.value)),
            el("small", { class: "muted" }, text(item.source)),
          ),
        ),
      );
      if (item.value && typeof item.value === "object") signal.append(el("details", {}, el("summary", {}, "Observed signal value"), dataView(item.value, "Observed signal")));
      holder.append(signal);
    }
    if (!holder.children.length)
      holder.append(
        notice(
          "No supported finding fields are available. Saved suggestion context can still be included in Ask.",
        ),
      );
    return holder;
  }
  function selectedFields(holder) {
    return [...holder.querySelectorAll("input[name=signals]:checked")].map(
      (input) => input.value,
    );
  }
  function contextPreview(payload) {
    const context = payload.context || {};
    const preview = el(
      "details",
      { class: "intel-context" },
      el("summary", {}, "Preview context sent with your question"),
      el("div", {class:"intel-context-data"}, dataView(context, "Selected context")),
    );
    return preview;
  }
  function sourceList(sources) {
    const list = el("div", { class: "intel-sources" });
    for (const source of sources || [])
      list.append(
        el(
          "article",
          { class: "intel-source" },
          external(
            source.title || source.url || source.id || "Source",
            source.url,
          ),
          source.excerpt
            ? el("p", { class: "small muted" }, source.excerpt)
            : null,
          source.retrieved_at
            ? el(
                "small",
                { class: "muted" },
                "Retrieved " + source.retrieved_at,
              )
            : null,
          source.content_sha256
            ? el(
                "details",
                {},
                el("summary", { class: "small" }, "Source excerpt fingerprint"),
                el("code", {}, source.content_sha256),
              )
            : null,
        ),
      );
    return list;
  }
  function queryBlocks(queries, suggestion, onSaved) {
    const grid = el("div", { class: "intel-query-grid" });
    for (const provider of PROVIDERS) {
      const item = queries?.[provider];
      if (!item) continue;
      const card = el(
        "section",
        { class: "intel-query" },
        el(
          "h4",
          {},
          provider === "zoomeye"
            ? "ZoomEye"
            : provider === "netlas"
              ? "Netlas"
              : "Shodan",
        ),
      );
      if (item.supported !== false && item.query) {
        const editor = el(
          "textarea",
          {
            class: "input intel-query-editor",
            "data-query-provider": provider,
            "aria-label": provider + " query",
            rows: "4",
            maxlength: "8000",
            spellcheck: "false",
          },
          item.query,
        );
        const copyButton = btn(
          "Copy query",
          () => copy(editor.value, copyButton),
          "btn btn-small",
        );
        const open = el(
          "a",
          {
            class: "btn btn-small",
            target: "_blank",
            rel: "noopener noreferrer",
          },
          "Open provider ↗",
        );
        const feedback = el("div", {
          class: "small muted",
          "aria-live": "polite",
        });
        let savedQuery = item.query;
        let savedURL = safeURL(item.url);
        const createLookup = window.LeakScopeOperations?.openLookup
          ? btn(
              "Create lookup",
              () => {
                window.LeakScopeOperations.openLookup({
                  name: suggestion.title,
                  provider,
                  query: savedQuery,
                  scope: suggestion.scope || {},
                  service_type: "",
                });
                card.closest("dialog.intel-dialog")?.close();
              },
              "btn btn-small",
            )
          : null;
        function updateEditor() {
          const dirty = editor.value !== savedQuery;
          save.disabled = !dirty || !editor.value.trim();
          if (createLookup) createLookup.disabled = dirty;
          if (!dirty && savedURL) {
            open.href = savedURL;
            open.removeAttribute("aria-disabled");
            open.removeAttribute("tabindex");
          } else {
            open.removeAttribute("href");
            open.setAttribute("aria-disabled", "true");
            open.tabIndex = -1;
          }
          feedback.textContent = dirty
            ? "Edited query: save a draft before opening the provider. Syntax has not been revalidated."
            : "";
        }
        const save = btn(
          "Save edits",
          async () => {
            save.disabled = true;
            editor.disabled = true;
            feedback.textContent = "Saving draft…";
            try {
              const result = await request(
                "/suggestions/" +
                  encodeURIComponent(suggestion.id) +
                  "/queries",
                { provider, query: editor.value },
              );
              const updated = result.suggestion?.queries?.[provider];
              if (!updated?.query)
                throw new Error("The server did not return the saved query.");
              editor.value = savedQuery = updated.query;
              savedURL = safeURL(updated.url);
              if (onSaved) onSaved(result.suggestion);
              updateEditor();
              feedback.textContent =
                "Saved as a manual draft; not executed or syntax-validated.";
            } catch (error) {
              updateEditor();
              feedback.replaceChildren(notice(error.message, true));
            } finally {
              editor.disabled = false;
            }
          },
          "btn btn-small",
        );
        editor.addEventListener("input", updateEditor);
        updateEditor();
        card.append(
          editor,
          el(
            "div",
            { class: "row-actions" },
            copyButton,
            save,
            open,
            createLookup,
          ),
          feedback,
        );
      } else
        card.append(
          el(
            "p",
            { class: "small muted" },
            "These signals cannot be represented by this provider.",
          ),
        );
      card.append(...warnings(item.warnings));
      grid.append(card);
    }
    return grid;
  }
  function suggestionCard(suggestion, refresh) {
    const card = el("article", { class: "panel intel-suggestion" });
    const body = el("div", { class: "panel-body" });
    const status = el("span", { class: "badge" }, suggestion.status || "draft");
    const errors = el("div");
    const actions = el("div", { class: "row-actions" });
    async function review(state, button) {
      button.disabled = true;
      errors.replaceChildren();
      try {
        await request(
          "/suggestions/" + encodeURIComponent(suggestion.id) + "/review",
          { status: state },
        );
        if (refresh) await refresh();
        else {
          status.textContent = state;
          suggestion.status = state;
        }
      } catch (error) {
        errors.replaceChildren(notice(error.message, true));
      } finally {
        button.disabled = false;
      }
    }
    for (const [label, state] of [
      ["Adopt", "adopted"],
      ["Reject", "rejected"],
      ["Return to draft", "draft"],
    ]) {
      const button = btn(label, () => review(state, button), "btn btn-small");
      if (state === suggestion.status) button.disabled = true;
      actions.append(button);
    }
    actions.append(
      btn(
        "Ask about this",
        () => openAsk([], { suggestion_id: suggestion.id }),
        "btn btn-small",
      ),
    );
    body.append(
      el(
        "div",
        { class: "intel-card-heading" },
        el("h3", {}, suggestion.title || "Query suggestion"),
        status,
      ),
      suggestion.rationale
        ? el("p", { class: "muted" }, suggestion.rationale)
        : null,
      queryBlocks(suggestion.queries, suggestion, (saved) => {
        status.textContent = saved.status || "draft";
        suggestion.status = saved.status || "draft";
        suggestion.queries = saved.queries;
      }),
      sourceList(suggestion.sources),
      actions,
      el(
        "p",
        { class: "small muted" },
        "Adopting saves this suggestion to your library. It does not run a query or create a watch.",
      ),
      errors,
    );
    card.append(body);
    return card;
  }
  async function openSimilar(references) {
    const refs = refsCopy(references);
    const view = modal(
      "Find similar",
      "Choose observed signals, then preview a separate query for each provider. Generation does not run a search.",
    );
    try {
      const payload = await request(
        "/context",
        { references: refs },
        view.signal,
      );
      if (!view.alive()) return;
      const fields = evidenceFields(payload.fields, true);
      const mode = el(
        "select",
        { name: "mode", class: "select" },
        el("option", { value: "all" }, "Match all selected signal groups"),
        el("option", { value: "any" }, "Match any selected signal"),
      );
      const network = el("input", {
        name: "network",
        class: "input",
        placeholder: "Optional IP or CIDR",
        maxlength: "100",
      });
      const country = el("input", {
        name: "country",
        class: "input",
        placeholder: "Optional country code",
        maxlength: "2",
      });
      const exclude = el("input", {
        name: "exclude",
        class: "input",
        placeholder: "Comma-separated exclusions",
        maxlength: "1000",
      });
      const unresolved = new Set(payload.scope_conflicts || []);
      let scopeOverride = false;
      for (const control of [network, country, exclude]) {
        control.value = payload.scope?.[control.name] || "";
        control.addEventListener("input", () => {
          scopeOverride = true;
          unresolved.delete(control.name);
          updateResolution();
        });
      }
      const resolutionNotice = el("div", { class: "small muted" });
      const result = el("div", {
        class: "intel-result",
        "aria-live": "polite",
      });
      const errors = el("div");
      const submit = btn(
        "Generate queries",
        async () => {
          errors.replaceChildren();
          const selected = selectedFields(fields);
          if (!selected.length) {
            errors.append(notice("Select at least one observed signal.", true));
            return;
          }
          submit.disabled = true;
          result.replaceChildren(notice("Generating provider previews…"));
          try {
            const data = await request(
              "/similar",
              {
                references: refs,
                selected_fields: selected,
                mode: mode.value,
                scope_override: scopeOverride,
                scope: {
                  network: network.value.trim(),
                  country: country.value.trim(),
                  exclude: exclude.value.trim(),
                },
              },
              view.signal,
            );
            if (view.alive())
              result.replaceChildren(
                ...warnings(data.warnings),
                suggestionCard(data.suggestion),
              );
          } catch (error) {
            if (view.alive())
              result.replaceChildren(notice(error.message, true));
          } finally {
            if (view.alive()) updateResolution();
          }
        },
        "btn btn-primary",
      );
      function updateResolution() {
        submit.disabled = !(payload.fields || []).length || unresolved.size > 0;
        resolutionNotice.textContent = unresolved.size
          ? "The selected findings have different scopes. Edit these fields explicitly to resolve them: " +
            [...unresolved].join(", ") +
            ". Clear a field only if you intend to broaden that scope."
          : "";
      }
      updateResolution();
      view.body.replaceChildren(
        ...warnings(payload.warnings),
        fields,
        el(
          "div",
          { class: "intel-scope" },
          field("Combination", mode),
          field("Keep within network", network),
          field("Keep within country", country),
          field("Keep exclusions", exclude),
        ),
        resolutionNotice,
        el(
          "p",
          { class: "small muted" },
          "Alternative values in one field are grouped together. Provider limitations are shown rather than silently dropping a signal.",
        ),
        errors,
        submit,
        result,
      );
    } catch (error) {
      if (view.alive()) view.body.replaceChildren(notice(error.message, true));
    }
  }
  async function openAsk(references = [], options = {}) {
    const refs = refsCopy(references);
    const extra =
      options.suggestion_id == null
        ? {}
        : { suggestion_id: options.suggestion_id };
    const view = modal(
      "Ask about evidence",
      "Your question and the selected context are sent to your configured model endpoint.",
    );
    try {
      const [settings, payload] = await Promise.all([
        request("/settings", undefined, view.signal),
        request("/context", { references: refs, ...extra }, view.signal),
      ]);
      if (!view.alive()) return;
      const fields = evidenceFields(payload.fields);
      const question = el("textarea", {
        name: "question",
        class: "input intel-question",
        rows: "4",
        maxlength: "4000",
        placeholder: "What would you like to understand about this evidence?",
      });
      question.value = options.question || "";
      const demo = demoControl("One real model request with limited evidence context and at most 256 output tokens. The answer may be shorter or incomplete; normal model settings are retained.");
      const result = el("div", {
        class: "intel-answer",
        "aria-live": "polite",
      });
      const configured = !!settings.llm?.configured;
      const submit = btn(
        "Ask",
        async () => {
          if (!question.value.trim()) {
            result.replaceChildren(notice("Enter a question first.", true));
            question.focus();
            return;
          }
          const demoRequested = demo.box.checked;
          submit.disabled = true;
          result.replaceChildren(notice("Waiting for the configured model…"));
          try {
            const data = await request(
              "/ask",
              {
                references: refs,
                selected_fields: selectedFields(fields),
                question: question.value.trim(),
                ...extra,
                ...(demoRequested ? {demo:true} : {}),
              },
              view.signal,
            );
            if (view.alive())
              result.replaceChildren(
                el(
                  "div",
                  { class: "intel-answer-heading" },
                  el("strong", {}, "Response"),
                  el(
                    "span",
                    { class: "small muted" },
                    data.model || settings.llm?.model,
                  ),
                ),
                el(
                  "pre",
                  { class: "intel-answer-text", tabindex: "0" },
                  data.answer,
                ),
                ...(demoRequested ? [notice("Demo response · limited evidence context and a short output budget. This sample may omit details or be truncated."), ...warnings(data.warnings)] : []),
                notice(
                  "Model output is analysis, not independently verified evidence.",
                ),
              );
          } catch (error) {
            if (view.alive())
              result.replaceChildren(notice(error.message, true));
          } finally {
            if (view.alive()) submit.disabled = !configured;
          }
        },
        "btn btn-primary",
      );
      submit.disabled = !configured;
      const preview = contextPreview(payload);
      function updatePreview() {
        preview.querySelector("summary").textContent = demo.box.checked ? "Selected context before Demo condensation limits" : "Preview context sent with your question";
        const selected = new Set(selectedFields(fields));
        const previewContext = JSON.parse(
          JSON.stringify(payload.context || {}),
        );
        if (previewContext.findings)
          previewContext.findings = previewContext.findings.map((record) => ({
            ...record,
            facts: Object.fromEntries(
              Object.entries(record.facts || {}).filter(([key]) =>
                selected.has(record.reference + ":" + key),
              ),
            ),
          }));
        preview.querySelector(".intel-context-data").replaceChildren(dataView(previewContext, "Selected context"));
      }
      fields.addEventListener("change", updatePreview);
      demo.box.addEventListener("change", updatePreview);
      updatePreview();
      view.body.replaceChildren(
        configured
          ? notice(
              "Endpoint: " +
                text(settings.llm.base_url) +
                " · Model: " +
                text(settings.llm.model),
            )
          : notice(
              "Configure a model endpoint and model in Integration settings to enable Ask.",
            ),
        ...(!configured
          ? [btn("Configure integrations", () =>
              openSettings(() =>
                openAsk(refs, { ...options, question: question.value }),
              ),
            )]
          : []),
        ...warnings(payload.warnings),
        fields,
        preview,
        field("Your question", question),
        demo.control,
        submit,
        result,
      );
    } catch (error) {
      if (view.alive()) view.body.replaceChildren(notice(error.message, true));
    }
  }
  async function renderResearch(root) {
    root.replaceChildren();
    root.setAttribute("aria-busy", "true");
    const heading = el(
      "div",
      { class: "page-heading" },
      el(
        "div",
        {},
        el("h1", {}, "Research"),
        el(
          "p",
          {},
          "Discover sourced query suggestions and review your saved fingerprints.",
        ),
      ),
    );
    const status = el("div");
    const topic = el("input", {
      name: "topic",
      class: "input",
      maxlength: "300",
      placeholder: "Technology, vendor or protocol to research",
      required: true,
    });
    topic.value =
      new URLSearchParams(location.search).get("topic")?.slice(0, 300) || "";
    const demo = demoControl("Real minimal research: one search result and one short synthesis when a model is configured. Provider minimum billing may still apply.");
    const output = el("div", { class: "intel-result", "aria-live": "polite" });
    const inbox = el("div", { class: "intel-inbox" });
    const filter = el(
      "select",
      { class: "select", "aria-label": "Filter suggestion status" },
      el("option", { value: "" }, "All statuses"),
      ...["draft", "adopted", "rejected"].map((value) =>
        el("option", { value }, value),
      ),
    );
    let cached = [];
    function drawInbox() {
      const items = cached.filter(
        (item) => !filter.value || item.status === filter.value,
      );
      inbox.replaceChildren(
        ...(items.length
          ? items.map((item) => suggestionCard(item, loadInbox))
          : [
              notice(
                "No saved suggestions match. Generate from a finding or research a technology.",
              ),
            ]),
      );
    }
    filter.addEventListener("change", loadInbox);
    async function loadInbox() {
      try {
        const response = await request(
          "/suggestions" +
            (filter.value ? "?status=" + encodeURIComponent(filter.value) : ""),
        );
        cached = response.results || [];
        drawInbox();
        if (Number(response.count) > cached.length)
          inbox.prepend(
            notice(
              "Showing the latest " +
                cached.length +
                " of " +
                response.count +
                " suggestions. Filter by status to narrow the inbox.",
            ),
          );
      } catch (error) {
        inbox.replaceChildren(notice(error.message, true));
      }
    }
    const run = el(
      "button",
      { type: "submit", class: "btn btn-primary" },
      "Discover queries",
    );
    const form = el(
      "form",
      { class: "intel-research-form" },
      field("Research topic", topic),
      demo.control,
      run,
    );
    let available = false;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!available || !topic.value.trim() || run.disabled) return;
      const demoRequested = demo.box.checked;
      run.disabled = true;
      output.replaceChildren(
        notice(
          "Searching sources and preparing suggestions… This may take a moment.",
        ),
      );
      try {
        const response = await request("/research", {
          topic: topic.value.trim(),
          ...(demoRequested ? {demo:true} : {}),
        });
        output.replaceChildren(
          ...(demoRequested ? [notice("Demo research · one real source and a bounded synthesis. This is a partial sample, not exhaustive research.")] : []),
          el("h3", {}, "Research sources"),
          sourceList(response.sources),
          ...warnings(response.warnings),
          ...(response.suggestions || []).map((item) =>
            suggestionCard(item, loadInbox),
          ),
        );
        if (
          !(response.sources || []).length &&
          !(response.suggestions || []).length
        )
          output.append(
            notice(
              "No sources or suggestions were returned. Try a more specific product or vendor.",
            ),
          );
        await loadInbox();
      } catch (error) {
        output.replaceChildren(notice(error.message, true));
      } finally {
        run.disabled = !available;
      }
    });
    heading.append(
      btn("Configure integrations", () =>
        openSettings(() => renderResearch(root)),
      ),
    );
    root.append(
      heading,
      el(
        "section",
        { class: "panel" },
        el(
          "div",
          { class: "panel-body" },
          el("h2", {}, "Discover queries"),
          el(
            "p",
            { class: "muted" },
            "Search public documentation and repositories for technology fingerprints. Suggestions remain drafts until reviewed; searches do not run against providers automatically.",
          ),
          status,
          form,
          output,
        ),
      ),
      el(
        "div",
        { class: "intel-inbox-heading" },
        el("h2", {}, "Suggestion inbox"),
        filter,
      ),
      inbox,
    );
    run.disabled = true;
    try {
      const settings = await request("/settings");
      available = !!settings.firecrawl?.configured;
      run.disabled = !available;
      if (!available)
        status.append(
          notice(
            "Web research is not configured. Use Configure integrations to set the Firecrawl API URL and any required key.",
          ),
        );
      if (!settings.llm?.configured)
        status.append(
          notice(
            "Sources can be retrieved without synthesis. Configure a model endpoint to generate sourced query suggestions.",
          ),
        );
      await loadInbox();
    } catch (error) {
      status.replaceChildren(notice(error.message, true));
    } finally {
      root.setAttribute("aria-busy", "false");
    }
  }
  async function openSettings(onSaved) {
    const view = modal(
      "Configure integrations",
      "Choose your model endpoint and private integration keys. Leave a key blank to keep it; select Clear saved key to remove it.",
    );
    try {
      let settings = await request("/settings", undefined, view.signal);
      if (!view.alive()) return;
      const base = el("input", {
        class: "input",
        name: "llm_base",
        type: "url",
        maxlength: "2000",
        placeholder: "http://host.docker.internal:11434/v1",
        autocomplete: "off",
      });
      const model = el("input", {
        class: "input",
        name: "llm_model",
        maxlength: "200",
        autocomplete: "off",
        placeholder: "Model name",
      });
      const thinkingMode = el("select", {name:"llm_thinking_mode", class:"select"},
        el("option", {value:"default"}, "Server default · standard request"),
        el("option", {value:"disabled"}, "Disable thinking · compatible endpoints"),
        el("option", {value:"enabled"}, "Enable thinking · compatible endpoints"));
      thinkingMode.value = settings.llm?.thinking_mode || "default";
      const crawlBase = el("input", {
        class: "input",
        name: "firecrawl_base",
        type: "url",
        maxlength: "2000",
        autocomplete: "off",
        placeholder: "https://api.firecrawl.dev/v2",
      });
      base.value = settings.llm?.base_url || "";
      model.value = settings.llm?.model || "";
      crawlBase.value =
        settings.firecrawl?.base_url || "https://api.firecrawl.dev/v2";
      function secretControl(name, configured) {
        const key = el("input", {
          class: "input",
          type: "password",
          name: name + "_key",
          maxlength: "4096",
          autocomplete: "new-password",
          spellcheck: "false",
          placeholder: configured
            ? "Configured · leave blank to keep"
            : "Not configured",
        });
        const clear = el("input", { type: "checkbox", name: name + "_clear" });
        clear.addEventListener("change", () => {
          key.disabled = clear.checked;
          if (clear.checked) key.value = "";
        });
        return {
          key,
          clear,
          element: el(
            "div",
            {},
            field("API key", key),
            el(
              "label",
              { class: "check-row" },
              clear,
              el("span", {}, "Clear saved key"),
            ),
          ),
        };
      }
      const llm = secretControl("llm", settings.llm?.key_configured);
      const firecrawl = secretControl(
        "firecrawl",
        settings.firecrawl?.key_configured,
      );
      const netlas = secretControl("netlas", settings.netlas?.configured);
      const certspotter = secretControl("certspotter", settings.domain_sources?.certspotter?.key_configured);
      const urlscan = secretControl("urlscan", settings.domain_sources?.urlscan?.key_configured);
      const output = el("div", { "aria-live": "polite" });
      const save = btn(
        "Save integrations",
        async () => {
          save.disabled = true;
          output.replaceChildren(notice("Saving integration settings…"));
          const payload = {
            llm: { base_url: base.value.trim(), model: model.value.trim(), thinking_mode: thinkingMode.value },
            firecrawl: { base_url: crawlBase.value.trim() },
            netlas: {},
          };
          for (const [name, controls] of [
            ["llm", llm],
            ["firecrawl", firecrawl],
            ["netlas", netlas],
            ["certspotter", certspotter],
            ["urlscan", urlscan],
          ]) {
            if (!payload[name]) {
              if (!controls.clear.checked && !controls.key.value) continue;
              payload[name] = {};
            }
            if (controls.clear.checked) payload[name].api_key = "";
            else if (controls.key.value)
              payload[name].api_key = controls.key.value;
          }
          try {
            const result = await request("/settings", payload, view.signal);
            if (!view.alive()) return;
            settings = result.settings || result;
            for (const [name, controls] of [
              ["llm", llm],
              ["firecrawl", firecrawl],
              ["netlas", netlas],
            ["certspotter", certspotter],
            ["urlscan", urlscan],
            ]) {
              controls.key.value = "";
              controls.clear.checked = false;
              controls.key.disabled = false;
              controls.key.placeholder = (
                name === "llm"
                  ? settings.llm?.key_configured
                  : settings.domain_sources?.[name]?.key_configured || settings[name]?.configured
              )
                ? "Configured · leave blank to keep"
                : "Not configured";
            }
            output.replaceChildren(
              notice(
                "Integration settings saved. New requests will use this configuration.",
              ),
            );
            if (onSaved) await onSaved(settings);
          } catch (error) {
            if (view.alive())
              output.replaceChildren(notice(error.message, true));
          } finally {
            if (view.alive()) save.disabled = false;
          }
        },
        "btn btn-primary",
      );
      view.body.replaceChildren(
        el(
          "section",
          { class: "intel-integration" },
          el("h3", {}, "Ask · OpenAI-compatible endpoint"),
          field("API base URL", base),
          field("Model", model),
          field("Reasoning mode", thinkingMode),
          el("p", {class:"small muted"}, "Optional for compatible endpoints such as Qwen/vLLM. Server default sends a standard request; other modes use an endpoint-specific thinking flag and are not supported by every OpenAI-compatible server."),
          llm.element,
          el(
            "p",
            { class: "small muted" },
            "Use the API prefix expected by your server, such as /v1. Local endpoints may not require a key.",
          ),
        ),
        el(
          "section",
          { class: "intel-integration" },
          el("h3", {}, "Web research · Firecrawl"),
          el("p", { class: "muted" }, "The built-in Firecrawl service needs no API key. External services may require one."),
          field("API base URL", crawlBase),
          firecrawl.element,
        ),
        el(
          "section",
          { class: "intel-integration" },
          el("h3", {}, "Asset discovery · Netlas"),
          netlas.element,
        ),
        el("section",{class:"intel-integration"},el("h3",{},"Passive domain sources"),
          el("p",{class:"muted"},"crt.sh, HackerTarget and GLEIF need no key. Cert Spotter permits limited anonymous evaluation of unexpired certificates; configure an account key for regular use. URLscan keys increase its limited public-search quota. These sources search existing records."),
          el("h4",{},"Cert Spotter / SSLMate"),certspotter.element,el("h4",{},"URLscan"),urlscan.element),
        save,
        output,
      );
    } catch (error) {
      if (view.alive()) view.body.replaceChildren(notice(error.message, true));
    }
  }
  window.LeakScopeIntelligence = {
    openSimilar,
    openAsk,
    renderResearch,
    openSettings,
  };
})();
