// The Meridian symbol as an SVG element built with DOM calls (no innerHTML), shared by the options page and the hint.

export function svgMark(size = 28): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 128 128");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const paths: [string, string][] = [
    ["M19 102V57C19 42 30 31 44 31C58 31 69 42 69 57V102H53V58C53 52 49 47 44 47C39 47 35 52 35 58V102Z", "#A8E4CE"],
    ["M53 102V44C53 28 65 16 81 16C97 16 109 28 109 44V72H93V44C93 37 88 32 81 32C74 32 69 37 69 44V102Z", "#A8E4CE"],
  ];
  for (const [d, fill] of paths) {
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", fill);
    svg.append(path);
  }
  const dot = document.createElementNS(ns, "circle");
  dot.setAttribute("cx", "101");
  dot.setAttribute("cy", "94");
  dot.setAttribute("r", "8");
  dot.setAttribute("fill", "#D6B782");
  svg.append(dot);
  return svg;
}
