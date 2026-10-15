// Shared by every demo page (classic script). The assistant never submits: tests call __submit() themselves and read the
// FormData the browser would have posted (what a real server would receive), next to each demo's own state.
(() => {
  const entries = (form) => {
    const out = {};
    for (const [key, value] of new FormData(form).entries()) {
      const v = typeof value === "string" ? value : `file:${value.name}:${value.size}`;
      out[key] = key in out ? [].concat(out[key], v) : v;
    }
    return out;
  };
  window.__submitted = [];
  document.addEventListener("submit", (e) => {
    e.preventDefault();
    window.__submitted.push(entries(e.target));
  }, true);
  window.__formData = (selector) => entries(document.querySelector(selector || "form"));
  window.__submit = (selector) => {
    const form = document.querySelector(selector || "form");
    window.__submitted.length = 0;
    form.requestSubmit();
    return window.__submitted[window.__submitted.length - 1] ?? null;
  };
})();
