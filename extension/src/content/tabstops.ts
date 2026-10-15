// What the browser's OWN Tab would land on. The assistant walks assist fields itself (design 7.4), but it must never skip a
// control the user did not ask it to skip: a link, a button, a plain input or a frame lying between two questions is a stop
// of the natural tab order (WCAG 2.1.1, 2.4.3), so there the key stays native. Controls that belong to a question's own widget
// (an editor's toolbar, a phone field's flag button, the "Attach / Dropbox" buttons of an upload, a help link inside the label)
// are part of that question and are skipped together with it, exactly as before.

import type { AssistField } from "./state";
import { closestDeep, compareTreeOrder, inUiHost } from "./util";
import { controlIsHumanVisible } from "./visibility";

const CANDIDATES = "a[href],area[href],button,input,select,textarea,summary,iframe,[tabindex],[contenteditable],audio[controls],video[controls]";
/** A question's block never grows across one of these: they are the form, not a question. */
const BLOCK_BOUNDARY = "form,main,dialog,body,html,[role=dialog],[role=main],[role=form],[role=application]";
/** How far above its own container a question's block may reach (label wrapper, field wrapper, row). */
const MAX_BLOCK_CLIMB = 3;

export interface TabScope {
  /** open shadow roots to search in addition to the document */
  roots: readonly ShadowRoot[];
  /** every assist field of this frame: a question's block never swallows another question */
  fields: readonly AssistField[];
}

export type TabEnd = AssistField | Element | null;

/**
 * Safari (default settings) stops on text fields only: links, buttons, checkboxes, radios and menus need "Press Tab to highlight each
 * item", which this code cannot read. There the assistant's own traversal is the only way to reach those controls, and skipping a link
 * or a button is what the browser itself does, so only what Safari's Tab really stops on counts as "the browser's own stop".
 * (E2E builds can switch the model on to exercise it in Playwright's WebKit.)
 */
export function safariModel(): boolean {
  if (__SAFARI__) return true;
  return __E2E__ && (globalThis as unknown as Record<string, unknown>).__MERIDIAN_SAFARI_TAB__ === true;
}

const SAFARI_NON_TEXT = new Set(["checkbox", "radio", "button", "submit", "reset", "image", "color", "range", "file"]);

function safariStops(node: HTMLElement): boolean {
  switch (node.tagName) {
    case "INPUT": return !SAFARI_NON_TEXT.has((node as HTMLInputElement).type);
    case "TEXTAREA":
    case "IFRAME": return true;
    default: return node.hasAttribute("contenteditable") && node.getAttribute("contenteditable")?.toLowerCase() !== "false";
  }
}

function explicitTabIndex(el: Element): number | null {
  const raw = el.getAttribute("tabindex");
  if (raw === null || raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

function isVisible(el: HTMLElement): boolean {
  try {
    if (!el.getClientRects().length) return false;
    const check = (el as unknown as { checkVisibility?: (o?: Record<string, boolean>) => boolean }).checkVisibility;
    if (typeof check === "function") return check.call(el, { checkVisibilityCSS: true, visibilityProperty: true });
    return getComputedStyle(el).visibility === "visible";
  } catch {
    return false;
  }
}

/**
 * A form control a person cannot see (1 px, off-screen, transparent, behind a cover, clipped away, a ghost): the scanner does not treat it as
 * a field (visibility.ts), so it is no stop for the assistant's walk either: a walk that handed the focus to a control nobody can see would
 * be the assistant filling (or leaving the person on) something invisible. A checkbox, radio or file input styled away is seen through its label.
 */
function looksUnseen(el: HTMLElement): boolean {
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) return false;
  if (el instanceof HTMLInputElement && ["button", "submit", "reset", "image"].includes(el.type)) return false;
  try {
    return !controlIsHumanVisible(el);
  } catch {
    return false;
  }
}

/** Radio buttons of one group are ONE stop for the browser: the checked one, else the first. */
function isRadioStop(radio: HTMLInputElement): boolean {
  if (!radio.name) return true;
  const root = radio.getRootNode() as Document | ShadowRoot;
  const group = [...root.querySelectorAll<HTMLInputElement>('input[type="radio"]')]
    .filter((r) => r.name === radio.name && r.form === radio.form && !r.disabled && isVisible(r));
  const checked = group.find((r) => r.checked);
  return (checked ?? group[0]) === radio;
}

/** Would the browser's Tab stop on this element? (The assistant's own UI never counts.) */
export function isTabStop(node: Element): boolean {
  if (!(node instanceof HTMLElement) || inUiHost(node)) return false;
  const tag = node.tagName;
  const explicit = explicitTabIndex(node);
  if (explicit !== null && explicit < 0) return false;
  let native = false;
  switch (tag) {
    case "BUTTON":
    case "SELECT":
    case "TEXTAREA":
    case "IFRAME":
      native = true;
      break;
    case "INPUT":
      native = (node as HTMLInputElement).type !== "hidden";
      break;
    case "A":
    case "AREA":
      native = node.hasAttribute("href");
      break;
    case "SUMMARY": {
      const details = node.parentElement;
      native = !!details && details.tagName === "DETAILS" && details.querySelector(":scope > summary") === node;
      break;
    }
    case "AUDIO":
    case "VIDEO":
      native = node.hasAttribute("controls");
      break;
    default:
      native = node.hasAttribute("contenteditable") && node.getAttribute("contenteditable")?.toLowerCase() !== "false";
  }
  if (!native && explicit === null) return false;
  if (safariModel() && !safariStops(node)) return false;
  try {
    if (node.matches(":disabled")) return false;
  } catch {
    /* an engine without :disabled on this element */
  }
  if (closestDeep(node, "[inert]")) return false;
  if (!isVisible(node) || looksUnseen(node)) return false;
  if (tag === "IFRAME") {
    // a tracking pixel in an iframe is no stop for anybody (the scanner ignores frames this small as well)
    const r = node.getBoundingClientRect();
    if (r.width < 24 || r.height < 24) return false;
  }
  if (tag === "INPUT" && (node as HTMLInputElement).type === "radio" && explicit === null) return isRadioStop(node as HTMLInputElement);
  return true;
}

// ---- tree helpers -----------------------------------------------------------------------------------------------------------

/** `ancestor.contains(node)` that also climbs out of open shadow roots. */
export function composedContains(ancestor: Node, node: Node): boolean {
  let cur: Node | null = node;
  for (let guard = 0; cur && guard < 400; guard++) {
    if (cur === ancestor) return true;
    cur = cur.parentNode ?? (cur as ShadowRoot).host ?? null;
  }
  return false;
}

/** Document order (shadow content after its host's light children). 0 for the same node. */
export function orderOf(a: Node, b: Node): number {
  if (a === b) return 0;
  try {
    if (a.getRootNode() === b.getRootNode()) {
      const pos = a.compareDocumentPosition(b);
      if (pos & Node.DOCUMENT_POSITION_CONTAINED_BY) return -1; // a contains b: a starts first
      if (pos & Node.DOCUMENT_POSITION_CONTAINS) return 1;
      return pos & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    }
  } catch {
    /* fall through */
  }
  return compareTreeOrder(a, b);
}

function lowestCommonAncestor(nodes: Element[]): Element | null {
  if (!nodes.length) return null;
  let common: Element | null = nodes[0];
  for (const node of nodes.slice(1)) {
    while (common && !composedContains(common, node)) common = common.parentElement ?? ((common.getRootNode() as ShadowRoot).host ?? null);
  }
  return common;
}

/** The controls that make up a field (it, its group members, their visible proxies, its popup trigger, search box, clear button). */
function seedsOf(field: AssistField): Element[] {
  const list: (Element | null | undefined)[] = [
    field.el, field.focusEl, field.anchorEl, field.links.trigger, field.links.search, field.links.proxy, field.links.clear,
    ...field.members.flatMap((m) => [m.el, m.proxy]),
  ];
  return [...new Set(list.filter((e): e is Element => !!e && e.isConnected))];
}

function labelsOf(field: AssistField): Element[] {
  const out: Element[] = [];
  const labelled = field.el as HTMLInputElement;
  try {
    if (labelled.labels) out.push(...labelled.labels);
  } catch {
    /* not a labelable element */
  }
  const wrap = field.el.closest("label");
  if (wrap) out.push(wrap);
  return out;
}

/**
 * The question's own block: the smallest container of the field's controls, grown by at most MAX_BLOCK_CLIMB wrappers (field row,
 * label wrapper) while the wrapper holds no OTHER question and is not the form itself. Tab stops inside it belong to the question.
 */
export function ownBlock(field: AssistField, fields: readonly AssistField[]): Element | null {
  const seeds = seedsOf(field);
  let block = lowestCommonAncestor(seeds);
  if (!block) return null;
  const others = fields.filter((f) => f.key !== field.key && f.el.isConnected);
  for (let up = 0; up < MAX_BLOCK_CLIMB; up++) {
    const parent: Element | null = block.parentElement ?? ((block.getRootNode() as ShadowRoot).host ?? null);
    if (!parent) break;
    try {
      if (parent.matches(BLOCK_BOUNDARY)) break;
    } catch {
      break;
    }
    if (others.some((o) => composedContains(parent, o.el))) break;
    block = parent;
  }
  return block;
}

function collectCandidates(scope: TabScope): HTMLElement[] {
  const found = new Set<HTMLElement>();
  const add = (root: Document | ShadowRoot) => {
    try {
      for (const el of root.querySelectorAll<HTMLElement>(CANDIDATES)) found.add(el);
    } catch {
      /* a detached root */
    }
  };
  add(document);
  for (const root of scope.roots) add(root);
  return [...found];
}

function isField(end: TabEnd): end is AssistField {
  return !!end && !(end instanceof Element);
}

function endElement(end: TabEnd): Element | null {
  if (!end) return null;
  return isField(end) ? end.el : end;
}

/**
 * Every control the browser's Tab would stop on between `from` and `to` (exclusive), in document order; `to` null = the edge of the
 * document. Controls belonging to `from`, `to` or any of `skipped` (fields the assistant passes over on purpose, such as
 * already-filled ones) never count.
 */
export function tabStopsBetween(scope: TabScope, from: AssistField | Element, to: TabEnd, dir: 1 | -1, skipped: readonly AssistField[] = []): HTMLElement[] {
  const start = isField(from) ? from.el : from;
  const stop = endElement(to);
  const region = collectCandidates(scope).filter((el) => {
    if (dir === 1) return orderOf(start, el) < 0 && (!stop || orderOf(el, stop) < 0);
    return orderOf(el, start) < 0 && (!stop || orderOf(stop, el) < 0);
  });
  if (!region.length) return [];
  const members: AssistField[] = [...(isField(from) ? [from] : []), ...skipped];
  if (isField(to)) members.push(to);
  const seeds: Element[] = [];
  const blocks: Element[] = [];
  for (const field of members) {
    seeds.push(...seedsOf(field), ...labelsOf(field));
    const block = ownBlock(field, scope.fields);
    if (block) blocks.push(block);
  }
  if (stop && !isField(to)) seeds.push(stop);
  if (!isField(from)) seeds.push(from);
  const real = region.filter((el) => !seeds.some((s) => composedContains(s, el)) && !blocks.some((b) => composedContains(b, el)) && isTabStop(el));
  return real.sort((a, b) => orderOf(a, b));
}

/** The control the browser's Tab would reach FIRST from `from` (the nearest one in `dir`), or null when nothing lies in between. */
export function tabStopBetween(scope: TabScope, from: AssistField | Element, to: TabEnd, dir: 1 | -1, skipped: readonly AssistField[] = []): HTMLElement | null {
  const stops = tabStopsBetween(scope, from, to, dir, skipped);
  if (!stops.length) return null;
  return dir === 1 ? stops[0] : stops[stops.length - 1];
}

/**
 * Where a hand-off INTO a frame should land: the browser's Tab enters a frame on its first control, Shift+Tab on its last. When a
 * control comes before the first assist field (a Back button) or after the last one (Save and Continue), that control is the edge.
 */
export function edgeTabStop(scope: TabScope, which: "first" | "last", pick: AssistField, skipped: readonly AssistField[] = []): HTMLElement | null {
  const stops = tabStopsBetween(scope, pick, null, which === "first" ? -1 : 1, skipped);
  if (!stops.length) return null;
  return which === "first" ? stops[0] : stops[stops.length - 1];
}
