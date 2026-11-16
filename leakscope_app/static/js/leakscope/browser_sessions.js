(function () {
  "use strict";
  const API = "/api/browser";
  let activeClose = null;
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value == null || value === false) return;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (["disabled", "checked"].includes(key)) node[key] = !!value;
      else node.setAttribute(key, value === true ? "" : String(value));
    });
    children.flat(Infinity).forEach(child => { if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child))); });
    return node;
  }
  const btn = (label, onclick) => el("button", {type:"button",class:"btn",onclick}, label);
  async function request(path, data, signal, keepalive = false) {
    const response = await fetch(API + path, {method:data === undefined ? "GET" : "POST",credentials:"same-origin",signal,keepalive,
      headers:{Accept:"application/json",...(data === undefined ? {} : {"Content-Type":"application/json","X-CSRFToken":document.querySelector('meta[name="csrf-token"]')?.content || ""})},
      ...(data === undefined ? {} : {body:JSON.stringify(data)})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || result.detail || "Browser request failed.");
    return result;
  }
  function validID(value) { return typeof value === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value); }
  async function open(endpoint, options = {}) {
    if (activeClose) activeClose();
    const controller = new AbortController(), signal = controller.signal;
    let session = null, closed = false, timer = null, imageURL = null, busy = false, frameBusy = false, capturePending = false;
    const output = el("div", {class:"browser-session-output","aria-live":"polite"});
    const status = el("p", {class:"small muted",role:"status"}, "Checking browser availability…");
    const image = el("img", {alt:"Live browser viewport for the selected endpoint",draggable:"false"});
    const viewport = el("div", {class:"browser-session-viewport",tabindex:"0","aria-label":"Browser viewport. Click to interact; use the controls below for text, scrolling and keys."}, image);
    viewport.hidden = true;
    const target = String(endpoint.address || "") + ":" + String(endpoint.port || "");
    const dialog = el("dialog", {class:"run-dialog browser-session-dialog","aria-label":"Verification browser"});
    function message(value, error = false) { output.replaceChildren(el("div", {class:"notice" + (error ? " notice-error" : ""),role:error ? "alert" : "status"}, value)); }
    function releaseImage() { if (imageURL) URL.revokeObjectURL(imageURL); imageURL = null; }
    function closeRemote(id) { request("/sessions/" + id + "/close", {}, undefined, true).catch(() => {}); }
    function close() {
      if (closed) return;
      closed = true; clearTimeout(timer); controller.abort(); releaseImage();
      if (session && !capturePending) closeRemote(session.id);
      if (dialog.open && typeof dialog.close === "function") dialog.close();
      dialog.remove(); if (activeClose === close) activeClose = null;
    }
    activeClose = close;
    async function perform(fn) {
      if (closed || !session || busy) return;
      busy = true; clearTimeout(timer); controls.setAttribute("aria-busy", "true");
      try {
        while (frameBusy && !closed) await new Promise(resolve => setTimeout(resolve, 20));
        if (!closed) await fn();
      } catch (error) { if (!closed) message(error.message, true); }
      finally { busy = false; controls.removeAttribute("aria-busy"); if (!closed) { clearTimeout(timer); timer = setTimeout(frame, 100); } }
    }
    async function sendInput(payload) {
      return perform(async () => { await request("/sessions/" + session.id + "/input", payload, signal); if (!closed) { message("Input sent. The next frame will show the result."); clearTimeout(timer); await frame(); } });
    }
    function dimensions() {
      const width = Number(session?.viewport?.width), height = Number(session?.viewport?.height);
      return {width:Number.isInteger(width) && width > 0 && width <= 4096 ? width : 1280,height:Number.isInteger(height) && height > 0 && height <= 4096 ? height : 800};
    }
    image.addEventListener("click", event => {
      if (!session || closed) return;
      const box = image.getBoundingClientRect(), size = dimensions(); if (!box.width || !box.height) return;
      const x = Math.max(0, Math.min(size.width - 1, Math.floor((event.clientX - box.left) * size.width / box.width)));
      const y = Math.max(0, Math.min(size.height - 1, Math.floor((event.clientY - box.top) * size.height / box.height)));
      viewport.focus(); sendInput({type:"click",x,y});
    });
    const text = el("input", {type:"text",name:"browser_text",class:"input",maxlength:"512",autocomplete:"off","aria-label":"Verification text",placeholder:"Text for the focused page field"});
    const sendText = btn("Send text", () => {
      if (!text.value || busy) return;
      const value = text.value; text.value = "";
      sendInput({type:"text",text:value});
    });
    const key = el("select", {class:"select",name:"browser_key","aria-label":"Browser key"}, ["Tab","Enter","Escape","Backspace","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Space","Home","End","PageUp","PageDown"].map(value => el("option", {value}, value)));
    viewport.addEventListener("keydown", event => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !["ArrowUp","ArrowDown","ArrowLeft","ArrowRight","PageUp","PageDown","Home","End"].includes(event.key)) return;
      event.preventDefault(); sendInput({type:"key",key:event.key});
    });
    const progress = el("div");
    const save = btn("Save screenshot", () => perform(async () => {
      // The API freezes this exact viewport before queueing persistence. Keep it alive if the dialog closes meanwhile.
      capturePending = true;
      const freeze = new AbortController();
      const freezeTimeout = setTimeout(() => freeze.abort(), 80000);
      let result;
      try { result = await request("/sessions/" + session.id + "/capture", {}, freeze.signal); }
      finally { clearTimeout(freezeTimeout); capturePending = false; if (closed) closeRemote(session.id); }
      if (closed) return;
      message("Screenshot queued from this session. Saved evidence remains attached to the original endpoint.");
      if (result.run && window.LeakScopeWorkbench?.watchRun) window.LeakScopeWorkbench.watchRun(result.run, progress, () => {
        if (!closed) progress.append(el("a", {class:"btn",href:"/assets/?endpoint=" + encodeURIComponent(endpoint.id)}, "Refresh saved screenshots"));
      }, {signal});
      else if (result.run) {
        progress.append(el("a", {class:"btn",href:"/runs/"}, "View capture job"));
        if(window.LeakScopeRunLogs)progress.append(window.LeakScopeRunLogs.panel(result.run,{key:"browser-capture-"+result.run.id}));
      }
    }));
    const controls = el("div", {class:"browser-session-controls"},
      el("div", {class:"browser-session-text"}, el("label", {}, el("span", {class:"field-label"}, "Verification text"), text), sendText),
      el("div", {class:"row-actions"}, key, btn("Send key", () => sendInput({type:"key",key:key.value})),
        btn("Scroll up", () => sendInput({type:"scroll",delta_x:0,delta_y:-500})), btn("Scroll down", () => sendInput({type:"scroll",delta_x:0,delta_y:500})), save));
    controls.hidden = true;
    dialog.append(el("div", {class:"dialog-heading"}, el("h2", {}, "Verification browser"), btn("Close browser", close)),
      el("p", {class:"mono"}, target),
      el("p", {class:"small muted"}, "Ephemeral session for this target only. Interact with a browser verification page, then save a screenshot. The session closes on expiry; its profile is not reused. Do not enter account credentials here."),
      status,viewport,controls,output,progress);
    dialog.addEventListener("cancel", event => {event.preventDefault();close();});
    dialog.addEventListener("close", close, {once:true});
    window.addEventListener("pagehide", close, {once:true,signal});
    document.body.append(dialog);
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    async function frame() {
      if (closed || !session || frameBusy || busy) return;
      frameBusy = true;
      try {
        const response = await fetch(API + "/sessions/" + session.id + "/frame", {credentials:"same-origin",signal,cache:"no-store",headers:{Accept:"image/png"}});
        if (!response.ok) {
          let problem; try {problem = await response.json();} catch (_) {problem = {};}
          throw new Error(problem.error || "Browser session unavailable or expired. Close this browser and open a new session.");
        }
        const blob = await response.blob();
        if (blob.size > 8 * 1024 * 1024 || (blob.type && blob.type !== "image/png")) throw new Error("Unexpected browser frame.");
        if (closed) return;
        releaseImage(); imageURL = URL.createObjectURL(blob); image.src = imageURL;
        if (!busy) timer = setTimeout(frame, 1000);
      } catch (error) { if (!closed) {message(error.message, true); status.textContent = "Live view paused. Close and reopen the browser to retry.";} }
      finally { frameBusy = false; }
    }
    try {
      const caps = await request("/capabilities", undefined, signal);
      if (closed) return;
      if (!caps.available || caps.manual_available === false || caps.manual_sessions === false) throw new Error(caps.reason || caps.error || "Verification browser unavailable on the capture worker.");
      status.textContent = "Opening the target…";
      const captureOptions = Object.fromEntries(["profile","wait_seconds","timeout_seconds","full_page"].filter(key => options[key] !== undefined).map(key => [key,options[key]]));
      // Let creation finish after the dialog closes so its returned session can be disposed.
      const creation = new AbortController();
      const creationTimeout = setTimeout(() => creation.abort(), 80000);
      let result;
      try { result = await request("/sessions", {endpoint_id:endpoint.id,options:captureOptions}, creation.signal); }
      finally { clearTimeout(creationTimeout); }
      if (!validID(result.session?.id)) throw new Error("Invalid browser session response.");
      if (closed) { closeRemote(result.session.id); return; }
      session = result.session;
      const expiry = new Date(session.expires_at);
      status.textContent = "Live " + (captureOptions.profile === "web_page" ? "Web page" : "Strict endpoint") + " session" + (Number.isNaN(expiry.valueOf()) ? "" : " · Expires " + expiry.toLocaleTimeString()) + ".";
      viewport.hidden = false; controls.hidden = false;
      await frame();
    } catch (error) { if (!closed) { status.textContent = "Browser unavailable."; message(error.message, true); } }
  }
  window.LeakScopeBrowser = {open,close:() => activeClose?.()};
})();
