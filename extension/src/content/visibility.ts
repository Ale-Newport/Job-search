// Visibility: what a PERSON can see and click. "Assist field" must mean exactly that: the assistant writes the user's profile (and the
// approved CV) only into controls its user can see, because a field the user cannot see is the classic autofill-phishing channel
// (design principles 2 and 8: it only fills what is in front of the user).
//
// A control (or the visible proxy that stands for a styled-away one: its label, a drop area, a custom switch) counts when ALL hold:
//   - opacity: the product of `opacity` and `filter: opacity()` over the control and EVERY ancestor is not (nearly) zero;
//   - clipping: it survives every ancestor that clips it (`overflow`, `contain: paint`, an emptied `clip-path` / `clip`), following
//     the containing blocks (an absolutely positioned control is clipped only by the ancestors from its containing block up, a fixed
//     one by none), and it lies inside the part of the page a person can reach (not past the right edge, not below a fixed viewport);
//     what a SCROLLABLE ancestor has scrolled away is only "not now": that is not a honeypot, so such a control is kept
//     (its container must itself be a real, sizeable box);
//   - text: a text control whose text is transparent (or the colour of its background, or 0px) is a ghost: what is inserted could
//     never be read;
//   - hit test: when it is on screen, `elementsFromPoint` at its centre and inset corners must reach the control, its label or its
//     declared proxy BEFORE anything that paints (an opaque cover, an image, a frame). A fixed/sticky bar that covers only a strip of
//     the viewport (cookie banner, sticky header, chat bubble) hides nothing for good: scrolling reveals the control.
//     Two ways to fail: `covered` (something that paints is on top of it: a static cover, but also the popup of the control above it,
//     which is gone a moment later: callers may be patient with a control they have already seen) and `blocked` (it cannot be hit at all:
//     behind a modal, `pointer-events: none`, or under a veil over the whole viewport: never patient).
//   What is off screen (below the fold, scrolled away inside a container) cannot be hit-tested without scrolling it into view, which
//   the assistant never does on its own: such a verdict is `tested: "geometry"` and callers re-check it once it is on screen.
//
//   - paint (SEC-01, second pass): the hit test above is repeated with `pointer-events: auto !important` forced on every element of the
//     trees involved, through a constructed stylesheet that exists for the length of the pass only (never across a task). A cover with
//     `pointer-events: none` (a div, an `::after`, an `<img>`, a veil in the top layer) is invisible to a pointer hit test and used to hide
//     a control from its user while the assistant filled it (the classic autofill-phishing channel): with the pass it is hit like any other
//     layer, and what is on top of the control, its label or its proxy and paints is a cover. A control that cannot be clicked at all
//     (its own `pointer-events: none`) is still no field: that is decided by the first, natural, pass.
//   IntersectionObserver v2 (`trackVisibility`) was measured as a further gate and is NOT used: Chromium reports `isVisible: false` for
//   every target as soon as ANY element paints later, even a transparent `position: fixed; inset: 0` layer, which the assistant's own UI
//   always has, so it says nothing about a cover of the page.
//
// Known limits: a cover that is not an element with a background, an image or a border (`box-shadow`, blend modes, masks, backdrop
// filters), and a cover whose `pointer-events: none` is an inline `!important` or sits in an earlier `@layer` (the forced rule cannot
// beat those). The inserted value is always shown next to the field, which is the last line of defence.

import { inUiHost } from "./util";

export type VisibilityReason =
  | "" | "detached" | "not-rendered" | "transparent" | "clipped" | "outside" | "tiny" | "ghost-text" | "covered" | "blocked" | "frame";

export interface VisibilityVerdict {
  visible: boolean;
  reason: VisibilityReason;
  /** `hit`: seen through `elementsFromPoint`; `geometry`: off screen, so only the styles and the layout were checked */
  tested: "hit" | "geometry";
  /** the paint pass (not the pointer's hit test) is what said no */
  byPaint?: boolean;
}

/** What the paint pass needs to repeat a hit test (kept off the public verdict). */
interface Probe { el: Element; box: Box; subjects: readonly Element[]; controlFixed: boolean; natural: Set<Element> }
type Judged = VisibilityVerdict & { probe?: Probe; skipPaint?: boolean };

export interface VisibilityOptions {
  /**
   * Elements that stand for the control when its own box is styled away: its labels, the drop area of a file input, the custom
   * switch of a checkbox, the control box of a combobox. One of them being visible (and clickable) is enough.
   */
  stands?: readonly (Element | null | undefined)[];
  /** the smallest visible box (px) the control itself may have; default 6 */
  minSize?: number;
  /** check transparent text; default: for text controls only */
  ghost?: boolean;
  /**
   * run the paint pass for this control (default true). A scan leaves it out for a control that passed it a moment ago: the pass costs a
   * style recalculation of the page, and what is written is asked about again, with the pass, right before it is written.
   */
  paint?: boolean;
}

interface Box { l: number; t: number; r: number; b: number }

/** An effective opacity below this is "not there" for a person. */
export const MIN_OPACITY = 0.1;
/** A cover must hide at least this much of what is below it. */
const COVER_ALPHA = 0.5;
/** A fixed/sticky cover of at most this share of the viewport is a bar (header, banner): scrolling gets the control out from under it. */
const BAR_SHARE = 0.6;
/** A scroll container (a modal body) must be at least this big to be the reason why a control is "scrolled away" and not hidden. */
const SCROLL_BOX = 40;
const MIN_STAND = 4;

const CLIPS = new Set(["hidden", "clip", "scroll", "auto", "overlay"]);
const SCROLLS = new Set(["scroll", "auto", "overlay"]);
const CLIPPED_PATH = /(?:inset\(\s*(?:50|100)%|circle\(\s*0|ellipse\(\s*0|polygon\(\s*0|path\(\s*["']?\s*["']?\s*\))/i;
const CLIP_RECT = /^rect\(\s*([\d.]+)(?:px)?[,\s]+([\d.]+)(?:px)?[,\s]+([\d.]+)(?:px)?[,\s]+([\d.]+)(?:px)?\s*\)$/i;
const FILTER_OPACITY = /opacity\(\s*([\d.]+)\s*(%?)\s*\)/gi;
const CONTAINS_BLOCK = /paint|layout|strict|content/;
const WILL_CHANGE_BLOCK = /transform|perspective|filter|contain|backdrop/;
const REPLACED = new Set(["IMG", "VIDEO", "CANVAS", "IFRAME", "OBJECT", "EMBED", "AUDIO", "INPUT", "SELECT", "TEXTAREA", "BUTTON", "SVG", "PICTURE"]);
const TEXT_INPUT_TYPES = new Set(["", "text", "search", "tel", "url", "email", "number"]);

// ---- pure helpers (unit tested) ---------------------------------------------------------------------------------------

/** Alpha channel (0..1) of a computed CSS colour: `transparent`, `rgba(...)`, `rgb(r g b / 50%)`, `color(srgb ... / 0)`. */
export function colorAlpha(value: string): number {
  const v = String(value ?? "").trim().toLowerCase();
  if (v === "" || v === "transparent") return 0;
  const open = v.indexOf("(");
  if (open < 0) return 1;
  const body = v.slice(open + 1, v.lastIndexOf(")") >= 0 ? v.lastIndexOf(")") : v.length);
  let raw: string | undefined;
  const slash = body.indexOf("/");
  if (slash >= 0) raw = body.slice(slash + 1).trim();
  else if (body.includes(",")) {
    const parts = body.split(",");
    if (parts.length >= 4) raw = parts[3].trim();
  }
  if (raw === undefined || raw === "") return 1;
  const n = raw.endsWith("%") ? parseFloat(raw) / 100 : parseFloat(raw);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 1;
}

/** Red, green and blue of a computed colour, or null when it is not in a form that can be compared. */
export function colorRgb(value: string): [number, number, number] | null {
  const m = /^(?:rgba?)\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(String(value ?? "").trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** The product of every `opacity(x)` of a `filter` value (`opacity(0)` and `opacity(0%)` hide the element). */
export function filterOpacity(filter: string): number {
  let out = 1;
  for (const m of String(filter ?? "").matchAll(FILTER_OPACITY)) {
    const n = parseFloat(m[1]);
    if (Number.isFinite(n)) out *= m[2] === "%" ? Math.min(1, n / 100) : Math.min(1, n);
  }
  return out;
}

/** Is this `clip-path` / `clip` value a cut that leaves nothing (or a sliver) of the element? */
export function clipsEverything(clipPath: string, clip: string): boolean {
  if (clipPath && clipPath !== "none" && CLIPPED_PATH.test(clipPath)) return true;
  const rect = CLIP_RECT.exec(String(clip ?? "").trim());
  if (rect) {
    const [top, right, bottom, left] = [Number(rect[1]), Number(rect[2]), Number(rect[3]), Number(rect[4])];
    if (bottom - top < 6 || right - left < 6) return true;
  }
  return false;
}

function inter(a: Box, b: Box): Box | null {
  const l = Math.max(a.l, b.l);
  const t = Math.max(a.t, b.t);
  const r = Math.min(a.r, b.r);
  const bt = Math.min(a.b, b.b);
  return r > l && bt > t ? { l, t, r, b: bt } : null;
}

const width = (b: Box): number => b.r - b.l;
const height = (b: Box): number => b.b - b.t;

function num(value: string, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** The flat-tree parent: the slot a light child is assigned to, the element above, or the host of a shadow root. */
export function flatParent(el: Element): Element | null {
  try {
    const slot = el.assignedSlot;
    if (slot) return slot;
  } catch {
    /* no slots */
  }
  const parent = el.parentElement;
  if (parent) return parent;
  const root = el.getRootNode();
  return root && root.nodeType === 11 ? (root as ShadowRoot).host : null;
}

/** `ancestor` contains `node` in the composed tree (through open shadow roots and slots). */
export function composedContains(ancestor: Node, node: Node): boolean {
  if (ancestor === node || ancestor.contains(node)) return true;
  let cur: Node | null = node;
  for (let guard = 0; cur && guard < 200; guard++) {
    if (cur === ancestor) return true;
    cur = cur.nodeType === 1 ? flatParent(cur as Element) : ((cur as ShadowRoot).host ?? cur.parentNode);
  }
  return false;
}

// ---- what one element contributes (read once per scan) ----------------------------------------------------------------------

type Mode = "inflow" | "absolute" | "fixed";

interface Info {
  /** own `opacity` times `filter: opacity()` */
  opacity: number;
  /** `content-visibility: hidden` or a clip that leaves nothing: nothing inside can be seen */
  gone: boolean;
  clipX: boolean;
  clipY: boolean;
  scrollable: boolean;
  /** which containing block this element's box is placed against: decides which ancestors clip it */
  mode: Mode;
  fixed: boolean;
  sticky: boolean;
  /** a containing block for absolutely positioned descendants */
  cbAbs: boolean;
  /** a containing block for fixed descendants (transform, filter, contain ...) */
  cbFixed: boolean;
  topLayer: boolean;
  box?: Box;
}

function readInfo(el: Element, ctx: VisibilityContext): Info {
  const s = getComputedStyle(el);
  let opacity = num(s.opacity, 1);
  const filter = s.filter;
  const hasFilter = !!filter && filter !== "none";
  if (hasFilter) opacity *= filterOpacity(filter);
  const position = s.position;
  const display = s.display;
  const html = el === document.documentElement;
  // overflow does not apply to inline boxes, to `display: contents`, to inline SVG, nor to the root (the viewport handles it)
  const boxy = el instanceof HTMLElement && display !== "inline" && display !== "contents" && !html;
  const root = ctx.rootOverflow();
  const propagated = el === document.body && root.bodyPropagates;
  const ox = boxy && !propagated ? s.overflowX : "visible";
  const oy = boxy && !propagated ? s.overflowY : "visible";
  const contained = CONTAINS_BLOCK.test(s.contain || "");
  const paintClip = boxy && /paint|strict|content/.test(s.contain || "");
  const clipX = CLIPS.has(ox) || paintClip;
  const clipY = CLIPS.has(oy) || paintClip;
  const scrollable = (SCROLLS.has(ox) || SCROLLS.has(oy)) && !paintClip;
  const transformed = (s.transform && s.transform !== "none") || (s.perspective && s.perspective !== "none") ||
    (s.translate && s.translate !== "none") || (s.rotate && s.rotate !== "none") || (s.scale && s.scale !== "none") ||
    hasFilter || (s.backdropFilter && s.backdropFilter !== "none") || contained || WILL_CHANGE_BLOCK.test(s.willChange || "") ||
    (!!s.containerType && s.containerType !== "normal");
  let topLayer = false;
  if (el.tagName === "DIALOG" || el.hasAttribute("popover")) {
    try {
      topLayer = el.matches(":modal") || el.matches(":popover-open");
    } catch {
      topLayer = false;
    }
  }
  return {
    opacity,
    gone: s.contentVisibility === "hidden" || clipsEverything(s.clipPath, position === "absolute" || position === "fixed" ? s.clip : ""),
    clipX,
    clipY,
    scrollable,
    mode: topLayer || position === "fixed" ? "fixed" : position === "absolute" ? "absolute" : "inflow",
    fixed: position === "fixed" || topLayer,
    sticky: position === "sticky",
    cbAbs: position !== "static" || !!transformed,
    cbFixed: !!transformed,
    topLayer,
  };
}

function paddingBox(el: Element): Box {
  const r = el.getBoundingClientRect();
  const l = r.left + el.clientLeft;
  const t = r.top + el.clientTop;
  return { l, t, r: l + el.clientWidth, b: t + el.clientHeight };
}

/**
 * Per-scan memory: every ancestor shared by many controls (the form, its sections) is read once, so checking a whole page costs a
 * few hundred style reads and not tens of thousands. A context must not outlive the scan that made it (layout changes).
 */
export class VisibilityContext {
  private infos = new Map<Element, Info>();
  private chains = new Map<Element, number>();
  private paintMemo = new Map<Element, boolean>();
  private fixedMemo = new Map<Element, Element | null>();
  private root: { clipX: boolean; clipY: boolean; bodyPropagates: boolean } | null = null;
  private vp: Box | null = null;

  /** `pageRoots`: the open shadow roots of the page; the paint pass forces hit testing in them as well as in the trees of the controls. */
  readonly pageRoots: readonly ShadowRoot[];
  /** the paint pass of a write (see `withHitTesting`): the rule that no page rule can outrank, at the price of a dearer style recalculation */
  readonly strong: boolean;

  constructor(pageRoots: readonly ShadowRoot[] = [], strong = false) {
    this.pageRoots = pageRoots;
    this.strong = strong;
  }

  /** The viewport in its own coordinates (the part of the page that is on screen). */
  viewport(): Box {
    if (!this.vp) {
      const de = document.documentElement;
      this.vp = { l: 0, t: 0, r: de.clientWidth || window.innerWidth, b: de.clientHeight || window.innerHeight };
    }
    return this.vp;
  }

  /** Does the root (html, or body when it propagates) forbid scrolling on an axis? Then a person cannot reach what is beyond the viewport. */
  rootOverflow(): { clipX: boolean; clipY: boolean; bodyPropagates: boolean } {
    if (!this.root) {
      const html = getComputedStyle(document.documentElement);
      const body = document.body ? getComputedStyle(document.body) : html;
      const propagates = html.overflowX === "visible" && html.overflowY === "visible";
      const source = propagates ? body : html;
      const hard = (v: string) => v === "hidden" || v === "clip";
      this.root = { clipX: hard(source.overflowX), clipY: hard(source.overflowY), bodyPropagates: propagates };
    }
    return this.root;
  }

  info(el: Element): Info {
    let info = this.infos.get(el);
    if (!info) {
      info = readInfo(el, this);
      this.infos.set(el, info);
    }
    return info;
  }

  clipBox(el: Element, info: Info): Box {
    if (!info.box) info.box = paddingBox(el);
    return info.box;
  }

  /** Opacity of an element as it reaches the screen: its own and every ancestor's. */
  chainOpacity(el: Element): number {
    const known = this.chains.get(el);
    if (known !== undefined) return known;
    const parent = flatParent(el);
    const value = this.info(el).opacity * (parent ? this.chainOpacity(parent) : 1);
    this.chains.set(el, value);
    return value;
  }

  /** The nearest fixed or sticky element at or above `el` (or null): such a thing stays where it is while the page scrolls. */
  fixedRoot(el: Element): Element | null {
    if (this.fixedMemo.has(el)) return this.fixedMemo.get(el) ?? null;
    const info = this.info(el);
    const parent = flatParent(el);
    const found = info.fixed || info.sticky ? el : parent ? this.fixedRoot(parent) : null;
    this.fixedMemo.set(el, found);
    return found;
  }

  /** Does this element hide what is below it? An opaque background, an image, a replaced element or a fat opaque border. */
  paints(el: Element): boolean {
    const known = this.paintMemo.get(el);
    if (known !== undefined) return known;
    let paints = false;
    if (REPLACED.has(el.tagName.toUpperCase())) {
      // a picture, a field or a frame at (nearly) no opacity hides nothing (the invisible native input of a Material select lies over it)
      paints = this.chainOpacity(el) >= COVER_ALPHA;
    } else if (this.chainOpacity(el) >= COVER_ALPHA) {
      const s = getComputedStyle(el);
      const alpha = colorAlpha(s.backgroundColor) * this.chainOpacity(el);
      if (alpha >= COVER_ALPHA || (s.backgroundImage && s.backgroundImage !== "none")) paints = true;
      else {
        for (const side of ["Top", "Right", "Bottom", "Left"] as const) {
          const w = parseFloat(s[`border${side}Width` as "borderTopWidth"]);
          const style = s[`border${side}Style` as "borderTopStyle"];
          if (w >= 20 && style !== "none" && style !== "hidden" && colorAlpha(s[`border${side}Color` as "borderTopColor"]) >= COVER_ALPHA) paints = true;
        }
      }
    }
    this.paintMemo.set(el, paints);
    return paints;
  }
}

// ---- geometry ---------------------------------------------------------------------------------------------------------------

interface Geometry {
  ok: boolean;
  reason: VisibilityReason;
  /** the part of the control a person sees, in viewport coordinates */
  box: Box | null;
  /** on screen: the part of it inside the viewport (null: off screen, or scrolled away inside a container) */
  onScreen: Box | null;
  fixedBound: boolean;
}

const failed = (reason: VisibilityReason): Geometry => ({ ok: false, reason, box: null, onScreen: null, fixedBound: false });

function geometry(ctx: VisibilityContext, el: Element, minSize: number): Geometry {
  if (!el.isConnected) return failed("detached");
  let r: DOMRect;
  try {
    const fragments = el.getClientRects();
    if (!fragments.length) return failed("not-rendered");
    r = el.getBoundingClientRect();
    // an inline box (a <label> around block children, a link over two lines) is only where its fragments are: the middle of the box
    // that holds them all may be empty page. A person sees, and clicks, the biggest fragment.
    if (fragments.length > 1) {
      let best: DOMRect = fragments[0];
      for (const fragment of fragments) if (fragment.width * fragment.height > best.width * best.height) best = fragment;
      if (best.width > 0 && best.height > 0) r = best;
    }
  } catch {
    return failed("not-rendered");
  }
  let rect: Box = { l: r.left, t: r.top, r: r.right, b: r.bottom };
  if (!(width(rect) > 0 && height(rect) > 0)) return failed("tiny");
  const own = ctx.info(el);
  let alpha = own.opacity;
  if (own.gone) return failed("clipped");
  let mode: Mode = own.mode;
  let hard: Box | null = null;
  const soft: Box[] = [];
  let stop = own.topLayer;
  for (let a = flatParent(el), guard = 0; a && !stop && guard < 400; a = flatParent(a), guard++) {
    const info = ctx.info(a);
    alpha *= info.opacity;
    if (alpha < MIN_OPACITY) return failed("transparent");
    if (info.gone) return failed("clipped");
    const applies = mode === "inflow" || (mode === "absolute" ? info.cbAbs : info.cbFixed);
    if (applies) {
      if (info.clipX || info.clipY) {
        const b = ctx.clipBox(a, info);
        const clip: Box = {
          l: info.clipX ? b.l : -Infinity, r: info.clipX ? b.r : Infinity, t: info.clipY ? b.t : -Infinity, b: info.clipY ? b.b : Infinity,
        };
        if (info.scrollable) soft.push(clip);
        else hard = hard ? { l: Math.max(hard.l, clip.l), t: Math.max(hard.t, clip.t), r: Math.min(hard.r, clip.r), b: Math.min(hard.b, clip.b) } : clip;
      }
      mode = info.mode;
    }
    if (info.topLayer) stop = true; // a modal dialog is drawn on top of everything: no ancestor clips or fades it
  }
  if (alpha < MIN_OPACITY) return failed("transparent");
  const fixedBound = mode === "fixed";
  // the part of the page a person can reach: never past the right edge; below the viewport only when the page scrolls
  const vp = ctx.viewport();
  const root = ctx.rootOverflow();
  const band: Box = fixedBound
    ? vp
    : {
        l: root.clipX ? 0 : -(window.scrollX || 0),
        t: root.clipY ? 0 : -(window.scrollY || 0),
        r: vp.r,
        b: root.clipY ? vp.b : Math.max(vp.b, document.documentElement.scrollHeight - (window.scrollY || 0)),
      };
  let seen: Box | null = inter(rect, band);
  if (!seen) return failed("outside");
  if (hard) {
    const cut = inter(seen, hard); // an axis that is not clipped has infinite bounds: they cut nothing
    if (!cut) return failed("clipped");
    seen = cut;
  }
  let deferred = false;
  for (const clip of soft) {
    const box = finite(clip, band);
    const cut = inter(seen, box);
    if (cut) {
      seen = cut;
    } else if (width(box) >= SCROLL_BOX && height(box) >= SCROLL_BOX) {
      deferred = true; // scrolled away inside a real scroll container: reachable, not hidden
    } else {
      return failed("clipped");
    }
  }
  if (!deferred && (width(seen) < minSize || height(seen) < minSize)) return failed("tiny");
  const onScreen = deferred ? null : inter(seen, vp);
  if (onScreen && (width(onScreen) < 2 || height(onScreen) < 2)) return { ok: true, reason: "", box: seen, onScreen: null, fixedBound };
  return { ok: true, reason: "", box: seen, onScreen, fixedBound };
}

/** An axis that is not clipped has infinite bounds: replace them with the reachable band. */
function finite(b: Box, band: Box): Box {
  return {
    l: Number.isFinite(b.l) ? b.l : band.l, r: Number.isFinite(b.r) ? b.r : band.r,
    t: Number.isFinite(b.t) ? b.t : band.t, b: Number.isFinite(b.b) ? b.b : band.b,
  };
}

// ---- text that cannot be read -----------------------------------------------------------------------------------------------

function isTextual(el: Element): boolean {
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLInputElement) return TEXT_INPUT_TYPES.has((el.getAttribute("type") ?? "").toLowerCase());
  return el instanceof HTMLElement && el.isContentEditable && el.getAttribute("contenteditable") !== null;
}

/**
 * A "ghost": a text control whose text cannot be read (transparent, 0px, pushed out of the box by a huge indent, or exactly the colour
 * of what is behind it). The value the assistant inserts into it could never be seen by the person.
 */
export function isGhostText(el: Element): boolean {
  if (!isTextual(el)) return false;
  try {
    const s = getComputedStyle(el);
    if (colorAlpha(s.color) < 0.05 || colorAlpha(s.getPropertyValue("-webkit-text-fill-color")) < 0.05) return true;
    if (parseFloat(s.fontSize) < 3) return true;
    const rect = el.getBoundingClientRect();
    const indent = parseFloat(s.textIndent);
    if (Number.isFinite(indent) && Math.abs(indent) >= rect.width && rect.width > 0) return true;
    const text = colorRgb(s.color);
    if (!text) return false;
    for (let cur: Element | null = el, depth = 0; cur && depth < 8; cur = flatParent(cur), depth++) {
      const cs = cur === el ? s : getComputedStyle(cur);
      if (cs.backgroundImage && cs.backgroundImage !== "none") return false; // something is drawn there: colours say nothing
      if (colorAlpha(cs.backgroundColor) >= 0.95) {
        const bg = colorRgb(cs.backgroundColor);
        return !!bg && Math.abs(bg[0] - text[0]) <= 6 && Math.abs(bg[1] - text[1]) <= 6 && Math.abs(bg[2] - text[2]) <= 6;
      }
    }
  } catch {
    return false;
  }
  return false;
}

// ---- the hit test -----------------------------------------------------------------------------------------------------------

/**
 * `pointer-events: auto !important` for every element and pseudo-element, in a constructed stylesheet that is adopted for the length of one
 * synchronous pass and taken away again before anything else runs: the page never has a task in which its own styles are altered, and no
 * mutation record is made.
 *   - `strong` (what a WRITE asks): in a cascade layer. An important declaration of a layer beats an important declaration that is not in
 *     one, whatever its specificity (the order of the layers is reversed for `!important`). Taking a layer away costs a second, dear style
 *     recalculation of the page (about 30 ms at 15,000 nodes), which one write can afford.
 *   - otherwise (a scan, which asks for every control of the page): a selector of five ids' specificity, which only a page rule with six ids
 *     or an inline `!important` outranks; it costs about 10 ms at 15,000 nodes each way.
 */
const HIT_RULES = (extra: string): string => `*${extra}, *${extra}::before, *${extra}::after { pointer-events: auto !important; }`;
const HIT_CSS = {
  strong: `@layer meridian-hit-probe { ${HIT_RULES("")} }`,
  scan: HIT_RULES(":not(#_)".repeat(5)),
};
const hitSheets: { strong?: CSSStyleSheet | null; scan?: CSSStyleSheet | null } = {};

function forcedSheet(strong: boolean): CSSStyleSheet | null {
  const kind = strong ? "strong" : "scan";
  if (hitSheets[kind] === undefined) {
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(HIT_CSS[kind]);
      hitSheets[kind] = sheet;
    } catch {
      hitSheets[kind] = null; // no constructable stylesheets: the paint pass cannot run (the natural pass and the other gates still do)
    }
  }
  return hitSheets[kind] ?? null;
}

/**
 * Runs `run` while every element of `trees` answers hit tests as if it had `pointer-events: auto`. `forced` is false when the rule could not
 * be put in place (the pass then says nothing). Nests: an inner call leaves what an outer one adopted to the outer one.
 */
export function withHitTesting<T>(trees: Iterable<Document | ShadowRoot>, run: (forced: boolean) => T, strong = false): T {
  const sheet = forcedSheet(strong);
  const mine: (Document | ShadowRoot)[] = [];
  let forced = false;
  if (sheet) {
    for (const tree of trees) {
      try {
        const list = tree.adoptedStyleSheets;
        if (list.includes(sheet)) {
          if (tree === document) forced = true;
          continue;
        }
        tree.adoptedStyleSheets = [...list, sheet];
        mine.push(tree);
        if (tree === document) forced = true;
      } catch {
        /* a tree that refuses (a closed or foreign one): its covers are not forced */
      }
    }
  }
  try {
    return run(forced);
  } finally {
    for (const tree of mine) {
      try {
        tree.adoptedStyleSheets = tree.adoptedStyleSheets.filter((s) => s !== sheet);
      } catch {
        /* nothing to restore */
      }
    }
  }
}

type Hit = "ok" | "covered" | "blocked";

function pointsOf(box: Box): [number, number][] {
  const inset = Math.max(1, Math.min(6, width(box) / 4, height(box) / 4));
  const cx = (box.l + box.r) / 2;
  const cy = (box.t + box.b) / 2;
  const last = (v: number) => v - 0.01;
  return [
    [cx, cy],
    [box.l + inset, box.t + inset], [last(box.r - inset), last(box.b - inset)],
    [last(box.r - inset), box.t + inset], [box.l + inset, last(box.b - inset)],
  ];
}

function viewportArea(vp: Box): number {
  return Math.max(1, width(vp) * height(vp));
}

/**
 * What a person would hit at one point: the subject (or something that stands for it) before anything that hides it (`ok`); a painting
 * element on top of it (`covered`); or no way to hit it at all, or a veil over the whole viewport (`blocked`).
 */
function hitAt(
  ctx: VisibilityContext, root: Document | ShadowRoot, x: number, y: number, owner: Element, subjects: readonly Element[], controlFixed: boolean,
  collect?: Set<Element>, natural?: ReadonlySet<Element>,
): Hit {
  let stack: Element[];
  try {
    stack = root.elementsFromPoint(x, y);
  } catch {
    return "ok"; // no hit testing in this context: nothing to say
  }
  let cover: Hit | null = null;
  if (collect) for (const el of stack) collect.add(el);
  for (const top of stack) {
    if (inUiHost(top)) continue; // the assistant's own chip and marks float over fields
    const subject = subjects.find((s) => composedContains(s, top));
    if (subject) {
      // The page chooses its labels, so a label (or any other stand-in) is no excuse: one that PAINTS above the control it is meant to
      // explain (an opaque label laid over its own input) hides it as well as any other layer. A floating label or an icon has no
      // background of its own. The control itself (subjects[0], whatever it looks like) and what belongs to the element being judged are never a cover.
      if (cover || subject === subjects[0] || composedContains(owner, top) || !paintsWithin(ctx, top, subject)) return cover ?? "ok";
      cover = "covered";
      continue;
    }
    if (cover) continue;
    // `clickless`: the paint pass hit it and a pointer does not (a layer with `pointer-events: none`): a real bar (a header, a banner) takes clicks
    const clickless = !!natural && !natural.has(top);
    if (!ctx.paints(top) && !(subjects.some((s) => composedContains(top, s)) && pseudoPaints(ctx, top))) continue;
    if (!controlFixed) {
      if (!clickless && ctx.fixedRoot(top)) {
        // the share is the PAINTER's: a fixed wrapper over the whole page (a modal root) with a small menu inside is no veil
        const r = top.getBoundingClientRect();
        const seen = inter({ l: r.left, t: r.top, r: r.right, b: r.bottom }, ctx.viewport());
        const share = seen ? width(seen) * height(seen) / viewportArea(ctx.viewport()) : 0;
        if (share < BAR_SHARE) continue; // scrolling gets the control out from under it
        cover = "blocked"; // a veil over (nearly) the whole viewport: nothing behind it can be clicked
        continue;
      }
    }
    cover = "covered";
  }
  // the subject never showed up in the stack: it cannot be hit at all (clipped away, pointer-events: none, behind a modal)
  return stack.length ? "blocked" : "ok";
}

/**
 * An element that is hit ABOVE one of its own descendants is hit through a pseudo-element of its own (an ::after laid over its content):
 * does one of them paint (an opaque background or an image, laid over the element)?
 */
function pseudoPaints(ctx: VisibilityContext, el: Element): boolean {
  try {
    for (const which of ["::after", "::before"] as const) {
      const s = getComputedStyle(el, which);
      if (!s.content || s.content === "none" || s.content === "normal" || s.display === "none" || s.visibility !== "visible") continue;
      if (s.position !== "absolute" && s.position !== "fixed") continue;
      if (num(s.opacity, 1) * ctx.chainOpacity(el) < COVER_ALPHA) continue;
      if (colorAlpha(s.backgroundColor) >= COVER_ALPHA || (s.backgroundImage && s.backgroundImage !== "none")) return true;
    }
  } catch {
    /* no pseudo-element styles: nothing to say */
  }
  return false;
}

/** Does the element hit, or one of its ancestors up to `top` (the stand-in it belongs to), paint (an opaque background, an image ...)? */
function paintsWithin(ctx: VisibilityContext, hit: Element, top: Element): boolean {
  for (let cur: Element | null = hit, guard = 0; cur && guard < 50; cur = flatParent(cur), guard++) {
    if (ctx.paints(cur)) return true;
    if (cur === top) return false;
  }
  return false;
}

/** Centre first (it must be reached); the inset corners follow until two of four agree: half the control is visible and clickable. */
function hits(
  ctx: VisibilityContext, el: Element, box: Box, subjects: readonly Element[], controlFixed: boolean, collect?: Set<Element>, natural?: ReadonlySet<Element>,
): Hit {
  const root = (el.getRootNode() as Document | ShadowRoot);
  if (typeof root.elementsFromPoint !== "function") return "ok";
  const points = pointsOf(box);
  const centre = hitAt(ctx, root, points[0][0], points[0][1], el, subjects, controlFixed, collect, natural);
  if (centre !== "ok") return centre;
  let agree = 0;
  let tried = 0;
  let worst: Hit = "covered";
  for (let i = 1; i < points.length; i++) {
    tried++;
    const hit = hitAt(ctx, root, points[i][0], points[i][1], el, subjects, controlFixed, collect, natural);
    if (hit === "ok") agree++;
    else if (hit === "blocked") worst = "blocked";
    if (agree >= 2) return "ok";
    if (agree + (points.length - 1 - tried) < 2) return worst;
  }
  return agree >= 2 ? "ok" : worst;
}

// ---- verdicts ---------------------------------------------------------------------------------------------------------------

/** Labels that stand for a styled-away toggle or file input (the control is `labels`, or sits inside a `<label>`). */
export function defaultStands(el: Element): Element[] {
  const out: Element[] = [];
  if (el instanceof HTMLInputElement && ["file", "radio", "checkbox"].includes(el.type)) {
    try {
      out.push(...Array.from(el.labels ?? []));
    } catch {
      /* no labels */
    }
    const wrapper = el.closest("label");
    if (wrapper && !out.includes(wrapper)) out.push(wrapper);
  }
  return out;
}

function judge(ctx: VisibilityContext, el: Element, subjects: readonly Element[], minSize: number, ghost: boolean): Judged {
  const g = geometry(ctx, el, minSize);
  if (!g.ok || !g.box) return { visible: false, reason: g.reason, tested: "geometry" };
  if (ghost && isGhostText(el)) return { visible: false, reason: "ghost-text", tested: "geometry" };
  if (!g.onScreen) return { visible: true, reason: "", tested: "geometry" };
  const controlFixed = g.fixedBound || ctx.fixedRoot(el) !== null;
  const natural = new Set<Element>();
  const hit = hits(ctx, el, g.onScreen, subjects, controlFixed, natural);
  if (hit !== "ok") return { visible: false, reason: hit, tested: "hit" };
  // what a pointer reaches at EVERY point the paint pass may look at (the hit test above stops as soon as it is sure)
  const root = el.getRootNode() as Document | ShadowRoot;
  try {
    for (const [x, y] of pointsOf(g.onScreen)) for (const found of root.elementsFromPoint(x, y)) natural.add(found);
  } catch {
    /* no hit testing: nothing was reached */
  }
  return { visible: true, reason: "", tested: "hit", probe: { el, box: g.onScreen, subjects, controlFixed, natural } };
}

/** The natural pass: what a pointer would hit. Every verdict that rests on a hit test carries the probe the paint pass repeats. */
function naturalVisibility(el: Element, opts: VisibilityOptions, ctx: VisibilityContext): Judged {
  try {
    const stands = (opts.stands ?? defaultStands(el)).filter((s): s is Element => !!s && s !== el && s.isConnected && !inUiHost(s));
    const subjects = [el, ...stands];
    const direct = judge(ctx, el, subjects, opts.minSize ?? 6, opts.ghost ?? true);
    if (direct.visible) return direct;
    // a ghost is a ghost whatever stands next to it; every other failure may be a control styled away behind its visible proxy
    if (direct.reason === "ghost-text") return direct;
    for (const stand of stands) {
      const verdict = judge(ctx, stand, subjects, MIN_STAND, false);
      if (verdict.visible) return verdict;
    }
    return direct;
  } catch {
    return { visible: true, reason: "", tested: "geometry" }; // never block on a failure of the check itself: the other guards still apply
  }
}

function plain(v: Judged): VisibilityVerdict {
  return { visible: v.visible, reason: v.reason, tested: v.tested, ...(v.byPaint ? { byPaint: true } : {}) };
}

/** The trees whose elements the paint pass must be able to hit: the document, the tree of every probe (and its hosts) and the page's open roots. */
function treesOf(ctx: VisibilityContext, probes: readonly Probe[]): Set<Document | ShadowRoot> {
  const trees = new Set<Document | ShadowRoot>([document]);
  for (const root of ctx.pageRoots) trees.add(root);
  for (const probe of probes) {
    let node: Node = probe.el;
    for (let guard = 0; guard < 50; guard++) {
      const root = node.getRootNode() as Document | ShadowRoot;
      trees.add(root);
      const host = (root as ShadowRoot).host as Element | undefined;
      if (!host) break;
      node = host;
    }
  }
  return trees;
}

/**
 * The paint pass: every verdict that rests on a hit test is repeated with `pointer-events` forced on, so a cover that a pointer would fall
 * through (and so an ordinary hit test never sees) is a cover. Only a "no" can come out of it. Done for all the verdicts together: adding and
 * removing the rule costs a style recalculation of the page each time, so a scan of 200 controls pays it once.
 */
function paintPass(verdicts: Judged[], ctx: VisibilityContext): VisibilityVerdict[] {
  const pending = verdicts.filter((v) => v.visible && v.probe && !v.skipPaint);
  if (pending.length) {
    withHitTesting(treesOf(ctx, pending.map((v) => v.probe as Probe)), (forced) => {
      if (!forced) return;
      for (const v of pending) {
        const probe = v.probe as Probe;
        let hit: Hit = "ok";
        try {
          hit = hits(ctx, probe.el, probe.box, probe.subjects, probe.controlFixed, undefined, probe.natural);
        } catch {
          hit = "ok";
        }
        if (hit !== "ok") {
          v.visible = false;
          v.reason = hit;
          v.byPaint = true;
        }
      }
    }, ctx.strong);
  }
  return verdicts.map(plain);
}

/**
 * Can a person see and click these controls right now? `opts.stands` are the elements that stand for a control when it is styled away.
 * `ctx` is shared by the controls of one scan; the paint pass runs once for all of them.
 */
export function humanVisibilityMany(items: readonly { el: Element; opts?: VisibilityOptions }[], ctx: VisibilityContext = new VisibilityContext()): VisibilityVerdict[] {
  return paintPass(items.map((i) => {
    const verdict = naturalVisibility(i.el, i.opts ?? {}, ctx);
    if (i.opts?.paint === false) verdict.skipPaint = true;
    return verdict;
  }), ctx);
}

/** Can a person see and click this control right now (natural and paint pass)? */
export function humanVisibility(el: Element, opts: VisibilityOptions = {}, ctx: VisibilityContext = new VisibilityContext()): VisibilityVerdict {
  return humanVisibilityMany([{ el, opts }], ctx)[0];
}

/** The natural pass only (cheap: no style recalculation): for decisions about where the browser's own Tab goes, never for a write. */
export function controlIsHumanVisible(el: Element, opts: VisibilityOptions = {}, ctx: VisibilityContext = new VisibilityContext()): boolean {
  return naturalVisibility(el, opts, ctx).visible;
}

/**
 * Is this `<iframe>` something a person sees and can use? A frame that is invisible (transparent, tiny, outside the page, behind a
 * cover, `pointer-events: none`) must never get the profile: the fields inside it are as hidden as the frame.
 */
export function frameIsHumanVisible(frame: HTMLIFrameElement, ctx: VisibilityContext = new VisibilityContext()): boolean {
  try {
    if (inUiHost(frame)) return false;
    const style = getComputedStyle(frame);
    if (style.pointerEvents === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
    const verdict = judge(ctx, frame, [frame], 24, false);
    if (!verdict.visible) return false;
    if (!verdict.probe) return true;
    // the paint pass costs a style recalculation: the answer for one frame is good for a moment (the frame code asks several times per event)
    const now = Date.now();
    const known = framePaint.get(frame);
    if (known && now - known.at < FRAME_PAINT_MS) return known.ok;
    const ok = paintPass([verdict], ctx)[0].visible;
    framePaint.set(frame, { at: now, ok });
    return ok;
  } catch {
    return false;
  }
}

const FRAME_PAINT_MS = 250;
const framePaint = new WeakMap<Element, { at: number; ok: boolean }>();
