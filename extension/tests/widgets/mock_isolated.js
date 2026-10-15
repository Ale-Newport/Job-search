// The mock extension runtime for the ISOLATED world (CDP Page.addScriptToEvaluateOnNewDocument with a worldName), i.e. where a
// real content script lives: its own JS globals and prototypes over the page's DOM. The page's world (main_shim.js) owns the
// Playwright binding; the two talk through synchronous DOM events carrying JSON strings (the only thing both worlds share).
(() => {
  if (window.__meridianMockInstalled) return;
  window.__meridianMockInstalled = true;
  const listeners = [];
  const pending = new Map();
  let seq = 0;
  const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail: JSON.stringify(detail) }));
  const runtime = {
    get id() {
      return window.__mockOrphan ? undefined : "mock-extension-id";
    },
    sendMessage(message) {
      if (window.__mockOrphan) return Promise.reject(new Error("Extension context invalidated."));
      return new Promise((resolve, reject) => {
        const id = ++seq;
        pending.set(id, { resolve, reject });
        emit("__mk_req", { id, message });
      });
    },
    onMessage: {
      addListener(fn) { listeners.push(fn); },
      removeListener(fn) { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); },
    },
  };
  Object.defineProperty(window, "chrome", { value: { runtime }, configurable: true, writable: true });
  document.addEventListener("__mk_res", (e) => {
    const { id, res, error } = JSON.parse(e.detail);
    const p = pending.get(id);
    if (!p) return;
    pending.delete(id);
    if (error) p.reject(new Error(error));
    else p.resolve(res);
  });
  // chrome.tabs.sendMessage reaching this frame's onMessage listeners
  document.addEventListener("__mk_deliver", (e) => {
    const { id, message } = JSON.parse(e.detail);
    let answered = false;
    const answer = (res) => { if (!answered) { answered = true; emit("__mk_deliver_res", { id, res: res === undefined ? null : res }); } };
    for (const fn of listeners) {
      try {
        const r = fn(JSON.parse(JSON.stringify(message)), { id: "mock-extension-id" }, answer);
        if (r === true) return;
      } catch (err) { /* a listener failing must not stop the others */ }
    }
    answer(undefined);
  });
  // synchronous reads of the content script's debug surface for the Python helpers
  document.addEventListener("__mk_call", (e) => {
    const { name, arg } = JSON.parse(e.detail);
    let out = null;
    try {
      if (name === "debug") out = window.__meridianAssist && window.__meridianAssist.debug ? window.__meridianAssist.debug() : null;
      else if (name === "uilog") out = window.__meridianUiLog || [];
      else if (name === "emit") out = window.__meridianUiEmit ? window.__meridianUiEmit(arg) : null;
    } catch (err) { out = { __error: String(err) }; }
    document.documentElement.setAttribute("data-mk-out", JSON.stringify(out === undefined ? null : out));
  });
})();
