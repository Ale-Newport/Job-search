// In-page measurements for the preview rig: contrast of rendered colours, overflow, focusability, accessible names.
// Runs inside the page against the (open, e2e) shadow root of the UI under test.

export interface Rgba { r: number; g: number; b: number; a: number }

const COLOR = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)/;
const SRGB = /color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+%?))?\s*\)/;

function alphaOf(raw: string | undefined): number {
  if (raw === undefined) return 1;
  return raw.endsWith("%") ? parseFloat(raw) / 100 : parseFloat(raw);
}

export function parseColor(input: string): Rgba | null {
  const rgb = COLOR.exec(input);
  if (rgb) return { r: parseFloat(rgb[1]), g: parseFloat(rgb[2]), b: parseFloat(rgb[3]), a: alphaOf(rgb[4]) };
  const srgb = SRGB.exec(input);
  if (srgb) return { r: parseFloat(srgb[1]) * 255, g: parseFloat(srgb[2]) * 255, b: parseFloat(srgb[3]) * 255, a: alphaOf(srgb[4]) };
  return null;
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(c: Rgba): number {
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
}

export function contrast(a: Rgba, b: Rgba): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function over(top: Rgba, bottom: Rgba): Rgba {
  const a = top.a + bottom.a * (1 - top.a);
  if (a <= 0) return { r: 0, g: 0, b: 0, a: 0 };
  return {
    r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
    g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
    b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a,
    a,
  };
}

function parentOf(el: Element): Element | null {
  if (el.parentElement) return el.parentElement;
  const root = el.getRootNode();
  return root instanceof ShadowRoot ? root.host : null;
}

export function effectiveBackground(el: Element, fallback: Rgba = { r: 255, g: 255, b: 255, a: 1 }): Rgba {
  const layers: Rgba[] = [];
  for (let node: Element | null = el; node; node = parentOf(node)) {
    const bg = parseColor(getComputedStyle(node).backgroundColor);
    if (bg && bg.a > 0) {
      layers.push(bg);
      if (bg.a >= 0.999) break;
    }
  }
  let out = fallback;
  for (const layer of layers.reverse()) out = over(layer, out);
  return { ...out, a: 1 };
}

function opacityChain(el: Element): number {
  let o = 1;
  for (let node: Element | null = el; node; node = parentOf(node)) o *= parseFloat(getComputedStyle(node).opacity || "1");
  return o;
}

export function describe(el: Element): string {
  const id = el.getAttribute("data-testid");
  const cls = el.getAttribute("class");
  return `${el.tagName.toLowerCase()}${id ? `[${id}]` : ""}${cls ? `.${cls.trim().split(/\s+/).join(".")}` : ""}`;
}

function visible(el: Element): boolean {
  const cs = getComputedStyle(el);
  if (cs.display === "none" || cs.visibility === "hidden") return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return false;
  for (let node: Element | null = el; node; node = parentOf(node)) {
    if (getComputedStyle(node).display === "none") return false;
  }
  return true;
}

function isSrOnly(el: Element): boolean {
  for (let node: Element | null = el; node; node = parentOf(node)) {
    if (node.classList.contains("sr")) return true;
  }
  return false;
}

export interface AuditResult {
  contrast: string[];
  overflow: string[];
  focusable: string[];
  names: string[];
  testids: string[];
  roles: string[];
  links: string[];
  clickthrough: string[];
  texts: string[];
  expect: string[];
  marks: { key: string; tone: string; placement: string; x: number; y: number; w: number; h: number }[];
  optionMarks: number;
  chip: { x: number; y: number; w: number; h: number } | null;
  chipPlacement: string | null;
  bounds: { x: number; y: number; w: number; h: number } | null;
  viewport: { w: number; h: number };
  minContrast: number;
}

export interface ExpectSpec {
  tone?: string; quiet?: boolean; rich?: boolean; noChip?: boolean; placement?: string; has?: string[]; lacks?: string[];
  text?: Record<string, { en?: string; es?: string }>;
  noCover?: string[];
  marks?: { count: number; tones?: string[] };
  options?: number;
}

const TIGHT_HIDDEN = new Set(["chip-sources", "chip-regen", "chip-meta", "chip-detail", "chip-conflict", "chip-protected", "chip-review", "chip-more", "chip-question", "chip-stale", "chip-already", "chip-duplicate", "hint-shift-tab", "hint-esc", "hint-regenerate", "hint-undo"]);
const FORBIDDEN_ROLES = new Set(["dialog", "alertdialog", "menu", "menubar", "listbox", "tooltip", "combobox", "grid"]);

function checkContrast(root: ShadowRoot, out: AuditResult): void {
  let min = 99;
  for (const el of root.querySelectorAll("*")) {
    if (isSrOnly(el) || el.namespaceURI === "http://www.w3.org/2000/svg" || !visible(el)) continue;
    const hasText = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim() !== "");
    if (!hasText) continue;
    const cs = getComputedStyle(el);
    const fg = parseColor(cs.color);
    if (!fg) { out.contrast.push(`${describe(el)}: unparsable colour ${cs.color}`); continue; }
    const bg = effectiveBackground(el);
    const alpha = fg.a * opacityChain(el);
    const blended = over({ ...fg, a: alpha }, bg);
    const size = parseFloat(cs.fontSize);
    const bold = parseInt(cs.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const ratio = contrast(blended, bg);
    min = Math.min(min, ratio);
    if (ratio < need) out.contrast.push(`${describe(el)}: ${ratio.toFixed(2)} < ${need} (${cs.color} on rgb(${bg.r.toFixed(0)},${bg.g.toFixed(0)},${bg.b.toFixed(0)})) "${(el.textContent ?? "").trim().slice(0, 40)}"`);
  }
  for (const input of root.querySelectorAll("input")) {
    if (!visible(input)) continue;
    const ph = parseColor(getComputedStyle(input, "::placeholder").color);
    if (!ph) continue;
    const bg = effectiveBackground(input);
    const ratio = contrast(over(ph, bg), bg);
    min = Math.min(min, ratio);
    if (ratio < 4.5) out.contrast.push(`${describe(input)} placeholder: ${ratio.toFixed(2)} < 4.5`);
  }
  out.minContrast = min;
}

function clippingAncestor(el: Element, stop: Element): Element {
  for (let node = el.parentElement; node && node !== stop; node = node.parentElement) {
    const cs = getComputedStyle(node);
    if (cs.overflowX !== "visible" || cs.overflowY !== "visible") return node;
  }
  return stop;
}

function checkOverflow(root: ShadowRoot, out: AuditResult): void {
  for (const container of root.querySelectorAll(".chip:not(.is-hidden), .panel, .pill-btn, .card, .toast")) {
    const cs = getComputedStyle(container);
    if (cs.display === "none") continue;
    for (const el of container.querySelectorAll("*")) {
      if (isSrOnly(el) || !visible(el)) continue;
      const clip = clippingAncestor(el, container);
      const c = clip.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      if (r.left < c.left - 1 || r.right > c.right + 1) out.overflow.push(`${describe(el)} spills horizontally out of ${describe(clip)} (${r.left.toFixed(0)}..${r.right.toFixed(0)} vs ${c.left.toFixed(0)}..${c.right.toFixed(0)})`);
      const ecs = getComputedStyle(el);
      const hasText = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim() !== "");
      if (hasText && ecs.display !== "inline" && ecs.textOverflow !== "ellipsis" && el.scrollWidth > el.clientWidth + 1) {
        out.overflow.push(`${describe(el)} text wider than its box (${el.scrollWidth} > ${el.clientWidth})`);
      }
    }
    if (container.classList.contains("chip") && !container.classList.contains("tight") && !(container as HTMLElement).style.maxHeight
      && container.getAttribute("data-placement") !== "overlay" && container.scrollHeight > container.clientHeight + 1) {
      out.overflow.push(`chip content clipped vertically (${container.scrollHeight} > ${container.clientHeight})`);
    }
  }
}

function accessibleName(el: Element): string {
  const label = el.getAttribute("aria-label");
  if (label && label.trim()) return label.trim();
  const by = el.getAttribute("aria-labelledby");
  if (by) {
    const target = (el.getRootNode() as ShadowRoot).getElementById?.(by);
    if (target?.textContent?.trim()) return target.textContent.trim();
  }
  return (el.textContent ?? "").trim() || (el.getAttribute("title") ?? "").trim();
}

const OWN_TAGS = new Set(["div", "span", "button", "a", "input", "svg", "path", "circle", "rect", "ul", "li", "nav", "section", "kbd"]);

function checkStructure(root: ShadowRoot, out: AuditResult): void {
  // anything outside the tags the UI creates itself means untrusted text was parsed as HTML
  for (const el of root.querySelectorAll("*")) {
    if (!OWN_TAGS.has(el.tagName.toLowerCase())) out.roles.push(`unexpected element <${el.tagName.toLowerCase()}> (HTML parsed from data?)`);
    for (const attr of el.getAttributeNames()) {
      if (/^on/i.test(attr)) out.roles.push(`${describe(el)} has an event-handler attribute ${attr}`);
    }
  }
  for (const el of root.querySelectorAll("[part], [exportparts]")) out.roles.push(`${describe(el)} exposes a shadow part (page CSS could reach it through ::part)`);
  for (const el of root.querySelectorAll(".chip-layer *, .toasts *, .flash-layer *, .marks-layer *")) {
    const node = el as HTMLElement;
    if (node.tabIndex >= 0) out.focusable.push(`${describe(el)} is in the tab order inside the chip/toast layers`);
    const cs = getComputedStyle(el);
    if ((cs.overflowY === "auto" || cs.overflowY === "scroll") && !el.hasAttribute("tabindex")) out.focusable.push(`${describe(el)} is a scroller without tabindex=-1 (keyboard-focusable in Chrome)`);
  }
  for (const el of root.querySelectorAll("button, a[href], input, [role=switch], [role=button]")) {
    if (!accessibleName(el)) out.names.push(`${describe(el)} has no accessible name`);
    if (!el.hasAttribute("data-testid")) out.testids.push(`${describe(el)} has no data-testid`);
  }
  for (const el of root.querySelectorAll("[role]")) {
    const role = el.getAttribute("role") ?? "";
    if (FORBIDDEN_ROLES.has(role)) out.roles.push(`${describe(el)} uses role=${role}`);
  }
  for (const el of root.querySelectorAll("[aria-modal], [autofocus], [contenteditable]")) out.roles.push(`${describe(el)} has a focus-stealing attribute`);
  for (const a of root.querySelectorAll("a")) {
    const href = a.getAttribute("href") ?? "";
    if (!/^https?:/i.test(href)) out.links.push(`${describe(a)} href is not http(s): ${href}`);
    if (a.getAttribute("target") !== "_blank" || !/noopener/.test(a.getAttribute("rel") ?? "") || !/noreferrer/.test(a.getAttribute("rel") ?? "")) out.links.push(`${describe(a)} lacks target=_blank rel=noopener noreferrer`);
  }
}

function collectTexts(root: ShadowRoot, out: AuditResult): void {
  const seen = new Set<string>();
  const add = (s: string | null | undefined): void => {
    const text = (s ?? "").trim();
    if (text) seen.add(text);
  };
  for (const el of root.querySelectorAll("*")) {
    if (el.namespaceURI === "http://www.w3.org/2000/svg") continue;
    for (const n of el.childNodes) if (n.nodeType === Node.TEXT_NODE) add(n.textContent);
    for (const attr of ["aria-label", "title", "placeholder", "alt"]) add(el.getAttribute(attr));
  }
  out.texts = [...seen];
}


const SELF_CONTROLS = "button, a[href], .value.open";

/**
 * The chip must be click-through: a click on a pixel that is not one of its controls reaches whatever the page has there
 * (often the next field). Real hit testing through the page's `document.elementFromPoint`, which reports the assistant
 * host for any pixel the assistant would take.
 */
function checkClickThrough(root: ShadowRoot, out: AuditResult): void {
  const chip = root.querySelector<HTMLElement>(".chip:not(.is-hidden)");
  if (!chip || getComputedStyle(chip).visibility === "hidden" || getComputedStyle(chip).display === "none") return;
  const box = chip.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const controls = [...chip.querySelectorAll<HTMLElement>(SELF_CONTROLS)].filter(visible).map((el) => el.getBoundingClientRect());
  const others = [...root.querySelectorAll<HTMLElement>(".panel, .card, .pill-btn, .toast")].filter(visible).map((el) => el.getBoundingClientRect());
  const inside = (r: DOMRect, x: number, y: number, pad = 0): boolean => x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad;
  const isHost = (el: Element | null): boolean => !!el && el.hasAttribute("data-meridian-assistant");
  let sampled = 0;
  for (let y = Math.max(box.top + 3, 1); y < Math.min(box.bottom - 3, vh - 1); y += 9) {
    for (let x = Math.max(box.left + 3, 1); x < Math.min(box.right - 3, vw - 1); x += 9) {
      if (others.some((r) => inside(r, x, y, 2))) continue;
      const onControl = controls.some((r) => inside(r, x, y, 3));
      const hitHost = isHost(document.elementFromPoint(x, y));
      if (!onControl) {
        sampled += 1;
        if (hitHost) { out.clickthrough.push(`the chip swallows a click at (${x.toFixed(0)},${y.toFixed(0)}) where it has no control`); return; }
      }
    }
  }
  for (const r of controls) {
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > vw || y > vh || others.some((o) => inside(o, x, y, 0))) continue;
    if (!isHost(document.elementFromPoint(x, y))) out.clickthrough.push(`a chip control at (${x.toFixed(0)},${y.toFixed(0)}) does not receive the pointer`);
  }
  const overlapsOthers = others.some((r) => r.left < box.right && r.right > box.left && r.top < box.bottom && r.bottom > box.top);
  if (sampled === 0 && box.width > 40 && !overlapsOthers) out.clickthrough.push("click-through check sampled nothing");
}

/** The text a chip offers (a value, a draft) must actually be readable: not squeezed to nothing or clipped out of the box. */
function checkValueReadable(root: ShadowRoot, out: AuditResult): void {
  const chip = root.querySelector<HTMLElement>(".chip:not(.is-hidden)");
  if (!chip || getComputedStyle(chip).visibility === "hidden") return;
  const value = chip.querySelector<HTMLElement>('[data-testid="chip-value"]');
  if (!value || !visible(value)) return;
  const box = chip.getBoundingClientRect();
  const r = value.getBoundingClientRect();
  if (r.height < 12 || r.bottom > box.bottom + 1.5 || r.top < box.top - 1.5) {
    out.overflow.push(`the chip's value is squeezed or clipped (${r.top.toFixed(0)}..${r.bottom.toFixed(0)} in a chip ${box.top.toFixed(0)}..${box.bottom.toFixed(0)})`);
  }
}

function checkMarks(root: ShadowRoot, out: AuditResult): void {
  for (const mark of root.querySelectorAll<HTMLElement>('[data-testid="mark"]')) {
    if (!visible(mark)) continue;
    const r = mark.getBoundingClientRect();
    const cls = mark.getAttribute("class") ?? "";
    const tone = /tone-([a-z]+)/.exec(cls)?.[1] ?? "";
    out.marks.push({ key: mark.getAttribute("data-key") ?? "", tone, placement: mark.getAttribute("data-placement") ?? "", x: r.x, y: r.y, w: r.width, h: r.height });
    if (!tone) out.expect.push("a mark has no tone class");
    if (mark.getAttribute("role") !== "img" || !(mark.getAttribute("aria-label") ?? "").trim()) out.names.push(`${describe(mark)} needs role=img and an accessible name`);
    if (mark.tabIndex >= 0 || mark.querySelector("button, a, input, [tabindex]")) out.focusable.push(`${describe(mark)} is focusable`);
    if (getComputedStyle(mark).pointerEvents !== "none") out.clickthrough.push(`${describe(mark)} takes the pointer`);
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    if (cx > 0 && cy > 0 && cx < document.documentElement.clientWidth && cy < document.documentElement.clientHeight
      && !root.querySelector(".panel, .card, .pill-btn, .chip:not(.is-hidden)") && (document.elementFromPoint(cx, cy)?.hasAttribute("data-meridian-assistant") ?? false)) {
      out.clickthrough.push("a mark swallows a click on the field it decorates");
    }
    if (r.left < -0.5 || r.top < -0.5 || r.right > document.documentElement.clientWidth + 0.5 || r.bottom > document.documentElement.clientHeight + 0.5) out.expect.push(`a mark leaves the viewport (${r.left.toFixed(0)},${r.top.toFixed(0)})`);
  }
}

/** The outlines of recommended options: decoration only (never named, never focusable, never taking the pointer). */
function checkOptionMarks(root: ShadowRoot, out: AuditResult): void {
  for (const mark of root.querySelectorAll<HTMLElement>('[data-testid="option-mark"]')) {
    if (!visible(mark)) continue;
    out.optionMarks += 1;
    if (mark.getAttribute("aria-hidden") !== "true") out.roles.push(`${describe(mark)} is not aria-hidden`);
    if (mark.tabIndex >= 0 || mark.querySelector("button, a, input, [tabindex]")) out.focusable.push(`${describe(mark)} is focusable`);
    if (getComputedStyle(mark).pointerEvents !== "none") out.clickthrough.push(`${describe(mark)} takes the pointer`);
    const r = mark.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) out.expect.push(`an option outline has no size (${r.width.toFixed(0)}x${r.height.toFixed(0)})`);
  }
}

function checkExpect(root: ShadowRoot, expect: ExpectSpec | undefined, lang: "en" | "es", out: AuditResult): void {
  if (!expect) return;
  const chip = root.querySelector<HTMLElement>(".chip");
  const shown = !!chip && !chip.classList.contains("is-hidden") && getComputedStyle(chip).display !== "none";
  if (expect.noChip) {
    if (shown) out.expect.push("a chip is showing but none was expected");
    return;
  }
  const byId = (id: string): Element | null => root.querySelector(`[data-testid="${id}"]`);
  const needsChip = expect.tone !== undefined || expect.quiet !== undefined || expect.rich !== undefined || expect.placement !== undefined;
  if (needsChip && !shown) { out.expect.push("expected a chip but none is showing"); return; }
  if (chip && shown) {
    if (expect.tone !== undefined && chip.getAttribute("data-tone") !== expect.tone) out.expect.push(`tone ${chip.getAttribute("data-tone")} != ${expect.tone}`);
    if (expect.quiet !== undefined && chip.getAttribute("data-quiet") !== String(expect.quiet)) out.expect.push(`quiet ${chip.getAttribute("data-quiet")} != ${expect.quiet}`);
    if (expect.rich !== undefined && chip.getAttribute("data-rich") !== String(expect.rich)) out.expect.push(`rich ${chip.getAttribute("data-rich")} != ${expect.rich}`);
    if (expect.placement !== undefined && chip.getAttribute("data-placement") !== expect.placement) out.expect.push(`placement ${chip.getAttribute("data-placement")} != ${expect.placement}`);
  }
  if (expect.noCover && chip && shown) {
    const mine = chip.getBoundingClientRect();
    for (const id of expect.noCover) {
      const other = document.getElementById(id)?.getBoundingClientRect();
      if (!other) { out.expect.push(`noCover: no page element #${id}`); continue; }
      const w = Math.min(mine.right, other.right) - Math.max(mine.left, other.left);
      const hgt = Math.min(mine.bottom, other.bottom) - Math.max(mine.top, other.top);
      if (w > 0.5 && hgt > 0.5 && other.bottom > 0 && other.top < document.documentElement.clientHeight) out.expect.push(`the chip covers #${id} (${(w * hgt).toFixed(0)} px squared)`);
    }
  }
  const tight = !!chip && chip.classList.contains("tight");
  for (const id of expect.has ?? []) {
    if (tight && TIGHT_HIDDEN.has(id)) continue; // squeezed into a short viewport: secondary rows are dropped by design
    const el = byId(id);
    if (!el || !visible(el)) out.expect.push(`missing or hidden [${id}]`);
  }
  for (const id of expect.lacks ?? []) {
    const el = byId(id);
    if (el && visible(el)) out.expect.push(`unexpected [${id}]`);
  }
  for (const [id, wanted] of Object.entries(expect.text ?? {})) {
    const need = wanted[lang];
    if (need === undefined) continue;
    const el = byId(id);
    if (!el || !(el.textContent ?? "").includes(need)) out.expect.push(`[${id}] text "${(el?.textContent ?? "").slice(0, 60)}" lacks "${need}"`);
  }
}

export function auditRoot(root: ShadowRoot, expect: ExpectSpec | undefined, lang: "en" | "es"): AuditResult {
  const out: AuditResult = {
    contrast: [], overflow: [], focusable: [], names: [], testids: [], roles: [], links: [], clickthrough: [], texts: [], expect: [], marks: [],
    optionMarks: 0, chip: null, chipPlacement: null, bounds: null, viewport: { w: document.documentElement.clientWidth, h: document.documentElement.clientHeight }, minContrast: 99,
  };
  checkContrast(root, out);
  checkOverflow(root, out);
  checkStructure(root, out);
  checkClickThrough(root, out);
  checkValueReadable(root, out);
  checkMarks(root, out);
  checkOptionMarks(root, out);
  collectTexts(root, out);
  checkExpect(root, expect, lang, out);
  if (expect?.marks) {
    const tones = out.marks.map((m) => m.tone).sort();
    if (out.marks.length !== expect.marks.count) out.expect.push(`${out.marks.length} marks showing, expected ${expect.marks.count}`);
    else if (expect.marks.tones && tones.join(",") !== [...expect.marks.tones].sort().join(",")) out.expect.push(`mark tones ${tones.join(",")} != ${expect.marks.tones.join(",")}`);
  }
  if (expect?.options !== undefined && out.optionMarks !== expect.options) out.expect.push(`${out.optionMarks} option outlines showing, expected ${expect.options}`);
  const chip = root.querySelector<HTMLElement>(".chip:not(.is-hidden)");
  if (chip && getComputedStyle(chip).display !== "none" && getComputedStyle(chip).visibility !== "hidden") {
    const r = chip.getBoundingClientRect();
    out.chip = { x: r.x, y: r.y, w: r.width, h: r.height };
    out.chipPlacement = chip.getAttribute("data-placement");
  }
  let left = Infinity; let top = Infinity; let right = -Infinity; let bottom = -Infinity;
  for (const el of root.querySelectorAll(".chip:not(.is-hidden), .panel, .pill-btn, .card, .toast, .flash, .field-mark, .opt-mark")) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    left = Math.min(left, r.left); top = Math.min(top, r.top); right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom);
  }
  if (left !== Infinity) out.bounds = { x: left, y: top, w: right - left, h: bottom - top };
  return out;
}

/** First pixel inside the chip (scanning in a grid) that is not on one of its controls or on another part of the UI. */
export function freePointInChip(root: ShadowRoot): { x: number; y: number } | null {
  const chip = root.querySelector<HTMLElement>(".chip:not(.is-hidden)");
  if (!chip) return null;
  const box = chip.getBoundingClientRect();
  const controls = [...chip.querySelectorAll<HTMLElement>(SELF_CONTROLS)].filter(visible).map((el) => el.getBoundingClientRect());
  for (let y = box.top + 6; y < box.bottom - 6; y += 6) {
    for (let x = box.left + 6; x < box.right - 6; x += 6) {
      if (!controls.some((r) => x >= r.left - 4 && x <= r.right + 4 && y >= r.top - 4 && y <= r.bottom + 4)) return { x, y };
    }
  }
  return null;
}
