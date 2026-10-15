// Mock of the extension runtime for the content-only rig. Installed with add_init_script BEFORE the content bundle.
// sendMessage goes through a Playwright binding (`window.__bg`) to the Python "background", which always answers on a
// later macrotask (like real extension IPC) and knows which frame is calling. `window.__deliver(msg)` plays the part of
// chrome.tabs.sendMessage reaching this frame's onMessage listeners.
(() => {
  if (window.__meridianMockInstalled) return;
  window.__meridianMockInstalled = true;
  const listeners = [];
  const runtime = {
    get id() {
      return window.__mockOrphan ? undefined : "mock-extension-id";
    },
    sendMessage(message) {
      if (window.__mockOrphan) return Promise.reject(new Error("Extension context invalidated."));
      return window.__bg(message);
    },
    onMessage: {
      addListener(fn) { listeners.push(fn); },
      removeListener(fn) { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); },
    },
  };
  try {
    Object.defineProperty(window, "chrome", { value: { runtime }, configurable: true, writable: true });
  } catch (e) {
    window.chrome = { runtime };
  }
  window.__deliver = (message) => new Promise((resolve) => {
    let answered = false;
    for (const fn of listeners) {
      try {
        const r = fn(JSON.parse(JSON.stringify(message)), { id: "mock-extension-id" }, (resp) => { answered = true; resolve(resp); });
        if (r === true) return;
      } catch (e) { /* a listener failing must not stop the others */ }
    }
    if (!answered) resolve(undefined);
  });
})();
