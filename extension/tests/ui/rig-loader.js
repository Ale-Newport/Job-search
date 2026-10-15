const params = new URLSearchParams(location.search);
document.body.classList.add(params.get("theme") === "dark" ? "dark" : "light");
if (params.get("bg") === "busy") document.body.classList.add("busy-bg");
if (params.get("hostile") === "1") {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "hostile.css";
  document.head.append(link);
}
if (params.get("dir") === "rtl") document.documentElement.setAttribute("dir", "rtl");
if (params.get("ancestors") === "1") {
  document.body.classList.add("ancestors");
  const cover = document.createElement("div");
  cover.className = "cover";
  cover.id = "cover";
  document.body.append(cover);
}
if (params.get("transformhtml")) document.documentElement.style.transform = params.get("transformhtml");
if (params.get("transformbody")) document.body.style.transform = params.get("transformbody");
if (params.get("zoombody")) document.body.style.zoom = params.get("zoombody");
if (params.get("zoomhtml")) document.documentElement.style.zoom = params.get("zoomhtml");
if (params.get("isolate") === "1") {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "isolate.css";
  document.head.append(link);
}
if (params.get("shadow") === "1") {
  // a page component with its own (open) shadow root holding a field and a dialog
  const host = document.createElement("div");
  host.id = "shadow-host";
  const inner = host.attachShadow({ mode: "open" });
  inner.innerHTML = '<style>input { position: fixed; top: 60%; left: 16px; width: 240px; height: 34px; } dialog { padding: 16px; width: 360px; }</style>'
    + '<input id="sh-field" type="text" aria-label="Field inside a page component">'
    + '<dialog id="sh-dialog"><label>Field in a shadow dialog <input id="sd-field" type="text" style="position: static; width: 100%"></label><button id="sd-close">Close</button></dialog>';
  document.body.append(host);
}
const script = document.createElement("script");
script.src = { closed: "rig-closed.js", safari: "rig-safari.js" }[params.get("bundle")] || "rig-open.js";
script.addEventListener("load", () => { document.documentElement.setAttribute("data-rig", "ready"); });
document.body.append(script);
