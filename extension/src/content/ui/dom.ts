// Tiny DOM helpers for the visual layer.
// Every dynamic string reaches the page through text nodes or setAttribute; HTML is never parsed from data.

export type Child = Node | string | number | null | undefined | false;
export type Prop = string | number | boolean | null | undefined;
export type Props = Record<string, Prop>;

const SVG_NS = "http://www.w3.org/2000/svg";

function applyProps(el: Element, props: Props | null | undefined): void {
  if (!props) return;
  for (const [name, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") el.setAttribute("class", String(value));
    else if (name === "testid") el.setAttribute("data-testid", String(value));
    else el.setAttribute(name, value === true ? "" : String(value));
  }
}

function append(el: Element, kids: Child[]): void {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
}

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, props?: Props | null, ...kids: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  applyProps(el, props);
  append(el, kids);
  return el;
}

export function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K, props?: Props | null, ...kids: SVGElement[]
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  applyProps(el, props);
  for (const kid of kids) el.append(kid);
  return el;
}

// ---- icons ------------------------------------------------------------------------------------------------------
type Shape = ["path", string] | ["circle", number, number, number] | ["rect", number, number, number, number, number];
interface IconDef { shapes: Shape[]; fill?: boolean }

const ICONS = {
  check: { shapes: [["path", "M5 12.5l4.5 4.5L19 7.5"]] },
  eye: { shapes: [["path", "M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"], ["circle", 12, 12, 3]] },
  sparkle: { shapes: [["path", "M12 2.5l2.2 6.3 6.3 2.2-6.3 2.2L12 19.5l-2.2-6.3L3.5 11l6.3-2.2z"]], fill: true },
  lock: { shapes: [["rect", 5, 11, 14, 9, 2], ["path", "M8 11V8a4 4 0 0 1 8 0v3"]] },
  close: { shapes: [["path", "M6.5 6.5l11 11M17.5 6.5l-11 11"]] },
  chevron: { shapes: [["path", "M6 9.5l6 6 6-6"]] },
  external: { shapes: [["path", "M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"]] },
  undo: { shapes: [["path", "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11"]] },
  refresh: { shapes: [["path", "M20 11a8 8 0 1 0-2.3 5.7M20 4.5V11h-6.5"]] },
  more: { shapes: [["circle", 5, 12, 1.4], ["circle", 12, 12, 1.4], ["circle", 19, 12, 1.4]], fill: true },
  minus: { shapes: [["path", "M6 12h12"]] },
  info: { shapes: [["circle", 12, 12, 9], ["path", "M12 11v5.5M12 7.6v.01"]] },
  alert: { shapes: [["path", "M12 4l9.5 16.5h-19z"], ["path", "M12 10v4.5M12 17.4v.01"]] },
  pencil: { shapes: [["path", "M4 20l4.2-1L19.5 7.7 16.3 4.5 5 15.8z"]] },
  dash: { shapes: [["path", "M8 12h8"]] },
  search: { shapes: [["circle", 11, 11, 6.5], ["path", "M16 16l4.5 4.5"]] },
  dockLeft: { shapes: [["rect", 3.5, 5, 17, 14, 2], ["path", "M9.5 5v14"]] },
  dockRight: { shapes: [["rect", 3.5, 5, 17, 14, 2], ["path", "M14.5 5v14"]] },
  dot: { shapes: [["circle", 12, 12, 4]], fill: true },
  plug: { shapes: [["path", "M9 3v5M15 3v5M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0zM12 17v4"]] },
  key: { shapes: [["rect", 3, 6, 18, 12, 3], ["path", "M7 10h.01M11 10h.01M15 10h.01M8 14h8"]] },
  list: { shapes: [["path", "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"]] },
} as const satisfies Record<string, IconDef>;

export type IconName = keyof typeof ICONS;

export function icon(name: IconName, size = 16): SVGSVGElement {
  const def: IconDef = ICONS[name];
  const svg = svgEl("svg", {
    viewBox: "0 0 24 24", width: size, height: size, class: "ic", "aria-hidden": "true", focusable: "false",
    fill: def.fill ? "currentColor" : "none", stroke: def.fill ? "none" : "currentColor",
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round",
  });
  for (const shape of def.shapes) {
    if (shape[0] === "path") svg.append(svgEl("path", { d: shape[1] }));
    else if (shape[0] === "circle") svg.append(svgEl("circle", { cx: shape[1], cy: shape[2], r: shape[3] }));
    else svg.append(svgEl("rect", { x: shape[1], y: shape[2], width: shape[3], height: shape[4], rx: shape[5] }));
  }
  return svg;
}

/** The Meridian symbol (docs/brand): two arcs and the sand orientation point. Colours come from CSS. */
export function markIcon(size = 20): SVGSVGElement {
  const svg = svgEl("svg", { viewBox: "0 0 128 128", width: size, height: size, class: "mark", "aria-hidden": "true", focusable: "false" });
  svg.append(
    svgEl("path", { class: "mark-arc", d: "M19 102V57C19 42 30 31 44 31C58 31 69 42 69 57V102H53V58C53 52 49 47 44 47C39 47 35 52 35 58V102Z" }),
    svgEl("path", { class: "mark-arc", d: "M53 102V44C53 28 65 16 81 16C97 16 109 28 109 44V72H93V44C93 37 88 32 81 32C74 32 69 37 69 44V102Z" }),
    svgEl("circle", { class: "mark-dot", cx: 101, cy: 94, r: 8 }),
  );
  return svg;
}

// ---- focus preservation across re-renders ------------------------------------------------------------------------
export interface FocusSnapshot { fid: string; start: number | null; end: number | null }

export function captureFocus(root: ShadowRoot): FocusSnapshot | null {
  const active = root.activeElement;
  if (!(active instanceof HTMLElement)) return null;
  const fid = active.getAttribute("data-fid");
  if (!fid) return null;
  const input = active instanceof HTMLInputElement ? active : null;
  return { fid, start: input?.selectionStart ?? null, end: input?.selectionEnd ?? null };
}

/** True when the element that had focus was found again (or there was nothing to restore). */
export function restoreFocus(root: ShadowRoot, snap: FocusSnapshot | null): boolean {
  if (!snap) return true;
  const next = [...root.querySelectorAll<HTMLElement>("[data-fid]")].find((el) => el.getAttribute("data-fid") === snap.fid);
  // a control that is switched off cannot take the keyboard (focus() on it does nothing and the page would get it): the caller picks another
  if (!next || (next as HTMLButtonElement).disabled) return false;
  try {
    next.focus({ preventScroll: true });
    if (next instanceof HTMLInputElement && snap.start !== null && snap.end !== null) next.setSelectionRange(snap.start, snap.end);
  } catch {
    /* element not focusable any more */
  }
  return true;
}

/** Only http(s) links are ever rendered as anchors; anything else (javascript:, data:, file:) stays plain text. */
export function safeHref(url: string): string | null {
  try {
    const parsed = new URL(url, location.href);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export function hostOf(url: string): string {
  try {
    return new URL(url, location.href).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function clamp(n: number, min: number, max: number): number {
  return Math.min(Math.max(n, min), Math.max(min, max));
}

export function truncate(text: string, max: number): string {
  const chars = Array.from(text);
  return chars.length <= max ? text : `${chars.slice(0, Math.max(1, max - 1)).join("").trimEnd()}…`;
}

/** The marks pages put on a required field: * (U+002A), the full-width and the small star, ✱ ✲ (U+2731, U+2732) and the asterisk operator. */
const STARS = "*\\uFF0A\\uFE61\\u2731\\u2732\\u2217\\u204E";
const STAR_TAIL = new RegExp(`(?:\\s*[${STARS}]+)+\\s*$`);
const STAR_HEAD = new RegExp(`^\\s*[${STARS}]+\\s*`);
const REQUIRED_WORD = /\s*[(\[]\s*(?:required|mandatory|obligatori[oa]s?|requerid[oa]s?)\s*[)\]]\s*$/i;

/**
 * A field's name without the page's "this one is required" decoration: "Email *", "Full name✱" and "Phone (required)" are "Email",
 * "Full name" and "Phone". The panel row carries its own required marker and the screen reader hears "required" once, from the panel;
 * a name that read "Inserted Email *: x" or "Go to Email *" said "star" aloud and showed it twice. Stars inside the text stay.
 */
export function plainLabel(text: string | undefined | null): string {
  let out = (text ?? "").replace(/\s+/g, " ").trim();
  for (let pass = 0; pass < 3; pass++) {
    const next = out.replace(STAR_TAIL, "").replace(REQUIRED_WORD, "").replace(STAR_HEAD, "").trim();
    if (next === out) break;
    out = next;
  }
  return out;
}

export function countWords(text: string): number {
  const matches = text.trim().match(/\S+/g);
  return matches ? matches.length : 0;
}
