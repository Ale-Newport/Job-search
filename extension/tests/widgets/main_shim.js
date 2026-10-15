// The PAGE-world half of the isolated-world rig: the Playwright binding and the helpers tests.harness expects on `window`
// (window.__deliver, window.__meridianAssist.debug, window.__meridianUiLog, window.__meridianUiEmit) forwarded to the content
// script living in its own world. Page code never sees the extension's objects, only these JSON strings.
(() => {
  if (window.__meridianShim) return;
  window.__meridianShim = true;
  const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail: JSON.stringify(detail) }));
  document.addEventListener("__mk_req", (e) => {
    const { id, message } = JSON.parse(e.detail);
    Promise.resolve(window.__bg(message)).then(
      (res) => emit("__mk_res", { id, res: res === undefined ? null : res }),
      (err) => emit("__mk_res", { id, error: String(err) }),
    );
  });
  const waiting = new Map();
  let seq = 0;
  window.__deliver = (message) => new Promise((resolve) => {
    const id = ++seq;
    waiting.set(id, resolve);
    emit("__mk_deliver", { id, message });
  });
  document.addEventListener("__mk_deliver_res", (e) => {
    const { id, res } = JSON.parse(e.detail);
    const resolve = waiting.get(id);
    if (resolve) { waiting.delete(id); resolve(res); }
  });
  const call = (name, arg) => {
    const root = document.documentElement;
    if (!root) return null;
    root.removeAttribute("data-mk-out");
    document.dispatchEvent(new CustomEvent("__mk_call", { detail: JSON.stringify({ name, arg }) }));
    const out = root.getAttribute("data-mk-out");
    root.removeAttribute("data-mk-out");
    return out === null ? null : JSON.parse(out);
  };
  window.__meridianAssist = { debug: () => call("debug") };
  Object.defineProperty(window, "__meridianUiLog", { get: () => call("uilog") || [], configurable: true });
  window.__meridianUiEmit = (action) => call("emit", action);
})();
