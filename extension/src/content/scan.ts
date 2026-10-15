// Scanner: runs the bundled observation.js verbatim (asking for its widget patterns: {widgets: true}), prunes it to what the
// bridge may see, groups radio/checkbox groups into single assist fields, keeps the observer's node identities, and rescans when
// the page changes (mutations, URL/SPA steps, choice controls that reveal conditional questions). It never scans or measures the
// assistant's own UI host and never reads what the user typed into text controls (has_value only). A combobox shows a selection
// only when it really displays one: placeholders ("Select One"), invisible text and icons are nothing.
//
// An assist field is something a PERSON can see and click (visibility.ts): covered, clipped, transparent, ghost, off-page and
// frame-hidden controls are not fields, and neither are one-time-code, card, bank, ID-number, security-answer and password
// controls (sensitive.ts): the bridge never hears of them. Text that leaves for the bridge has its whitespace collapsed and is
// capped per field and per scan (a hostile page must not be able to stall the sidecar with one enormous label).

import observe, { type ObservationSnapshot } from "meridian-observation";
import type { ElementIn, OptionIn } from "../shared/protocol";
import type { AssistField, FieldKind, GroupMember, PageFlags } from "./state";
import { isTextKind } from "./state";
import { comboSelection, isComboPlaceholder, withoutDecoration } from "./fill";
import type { RootRegistry } from "./roots";
import { insideOpenPopup } from "./popups";
import { sensitiveClass } from "./sensitive";
import { composedContains, ownBlock } from "./tabstops";
import { UI_HOST_ATTRIBUTE } from "./ui-api";
import {
  after, clip, closestDeep, compareTreeOrder, composedTarget, debounce, every, guard, hashText, inUiHost, isRendered, normalizeText, squash, stripRequiredMarks, tidy,
  type Debounced,
} from "./util";
import { defaultStands, frameIsHumanVisible, humanVisibilityMany, VisibilityContext, type VisibilityReason, type VisibilityVerdict } from "./visibility";

type Raw = Record<string, any>;

export type StopRef =
  | { kind: "assist"; field: AssistField }
  | { kind: "native"; el: Element }
  | { kind: "frame"; el: HTMLIFrameElement };

export interface ScanResult {
  documentId: string;
  url: string;
  title: string;
  fields: AssistField[];
  stops: StopRef[];
  flags: PageFlags;
  snapshot: ObservationSnapshot;
  scannedAt: number;
  /** wall time of this scan (observer + pruning), the basis of the adaptive debounce */
  scanMs: number;
}

export interface ScanDiff {
  added: AssistField[];
  changed: AssistField[];
  removed: AssistField[];
  /** choice controls whose selection changed since the previous scan (conditional questions depend on them) */
  selectionChanged: AssistField[];
  /** the observation document identity changed: everything is new */
  reset: boolean;
  /** the page replaced a control's element by an equivalent one (blur validation, framework re-mount): the old state moves over */
  renamed: { from: string; to: AssistField }[];
}

export interface ScannerOptions {
  frameKey: string;
  isActive: () => boolean;
  onResult: (result: ScanResult, diff: ScanDiff) => void;
  onUrlChange?: (url: string) => void;
  /** new <iframe> elements appeared (top frame asks the worker to inject them) */
  onFramesChanged?: () => void;
  /** open shadow roots: observed for mutations and `change`, shared with the controller's focus/key listeners */
  roots: RootRegistry;
}

interface ObserverCache {
  documentId: string;
  ids: WeakMap<Node, number>;
  nodes: Map<number, Node>;
}

const CACHE_KEY = "__meridianObservedControlsV1";
/** The extension asks the observer for the widget patterns the Playwright filler does not drive (see observation.js `widgets`). */
const OBSERVE_OPTIONS = { widgets: true } as const;
/** Zero-width characters widgets render as "empty" content (MUI Select shows U+200B while nothing is selected). */
const ZERO_WIDTH = /[\u200b-\u200d\u2060\ufeff]/g;
const MAX_FIELDS = 600;
const MAX_OPTIONS = 1000;
const MIN_GAP_MS = 250;
/** Adaptive throttling: the more a scan costs, the longer the page is left alone afterwards (a duty cycle of about 1/8). */
const GAP_PER_SCAN_MS = 7;
const MAX_GAP_MS = 4000;
const MAX_MUTATION_DELAY_MS = 1500;

/**
 * What may leave the page for ONE field. The server accepts up to 20,000 characters of label/context/description (schemas.py), but a
 * question is a sentence or two: anything longer is a page trying to make the sidecar chew on text, so each text is cut to a size
 * that holds every real question (long consent paragraphs keep their beginning AND their end, where the actual question sits).
 */
const LIMITS: Record<string, number> = {
  role: 40, tag: 20, type: 30, explicit_type: 30, label: 2000, context: 4000, description: 3000, placeholder: 500,
  section_id: 80, section_label: 200, group: 500, field_path: 300, name: 300, id: 300, autocomplete: 60,
  value: 300, selected_text: 300, pattern: 300, accept: 200, manual_key: 600,
};
/** Characters of free text and options one scan may hand to the bridge (the body limit is 1 MiB; the sidecar reads every character). */
const SCAN_TEXT_BUDGET = 240_000;
/** Below this much budget left, fields are sent with their question only (no description, short context, few options). */
const LEAN_BELOW = 20_000;
const LEAN_TEXT = 200;
const LEAN_OPTIONS = 40;
/** Option text of one field (a country list is about 10,000; 1,000 options of 600 characters would be 600,000). */
const FIELD_OPTION_CHARS = 60_000;
/** A rejected control that may still become visible (a fade-in, a banner, a scroll) is looked at once more after this long. */
const SETTLE_MS = 650;
/** Rejections that are about WHERE or WHEN the control is, not about what it is (a ghost or a honeypot style stays rejected). */
const WATCHED_REASONS = new Set<VisibilityReason>(["covered", "blocked", "outside", "clipped", "transparent", "tiny"]);
/**
 * A control a person has been seen to see (it passed a hit test) is not dropped from the walk because something painted on top of it
 * a moment later: the calendar of the field above it, a menu, an autocomplete list. The moment the fields are WRITTEN the check is
 * strict again (`stillVisible`). A control nobody has seen is never granted this: a cover that was there from the start stays one.
 */
const SEEN_TTL_MS = 5 * 60_000;
/** Elements of a control that stand for it when its own box is styled away (they must be visible and clickable instead). */
const STAND_KEYS = ["upload_proxy_id", "interaction_proxy_id", "toggle_proxy_id", "visual_toggle_proxy_id", "dropdown_trigger_id"] as const;
const WATCH_LIMIT = 400;
/** A control that passed the paint pass at a scan skips it at the scans of the next seconds (a style recalculation of the whole page each). */
const PAINT_TTL_MS = 10_000;
/** Above this many nodes a scan does not run the paint pass at all (see `candidates`). */
const PAINT_MAX_NODES = 8000;
const MAX_COMBO_MEMORY = 400;
const MAX_OFFERED = 3000;
/** While some control is rejected for where or when it is (covered, transparent ...), look at it again this often: nothing says when a layer goes away. */
const RECHECK_MS = 1200;

const ASSIST_ROLES = new Set(["textbox", "combobox", "checkbox", "switch", "radio", "upload", "spinbutton"]);
const BLOCKED_TYPES = new Set(["password", "hidden", "submit", "button", "reset", "image", "range", "color"]);
const MUTATION_ATTRS = ["hidden", "disabled", "aria-hidden", "readonly", "required", "aria-required", "type", "open", "style", "class"];
const FORMISH = "form,fieldset,[role=group],[role=radiogroup],[data-field-path],[role=dialog],.form-group,.application-question";

function observerCache(): ObserverCache | null {
  try {
    return ((window as unknown as Record<string, unknown>)[CACHE_KEY] as ObserverCache | undefined) ?? null;
  } catch {
    return null;
  }
}

function str(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function clamp(key: string, value: unknown): string {
  const text = str(value);
  const max = LIMITS[key];
  return max && text.length > max ? text.slice(0, max) : text;
}

/** `text` in at most `max` characters keeping its beginning and its end (a long paragraph usually ends with the actual question). */
export function fit(text: string, max: number): string {
  if (text.length <= max) return text;
  if (max <= 0) return "";
  if (max < 8) return text.slice(0, max);
  const head = Math.ceil((max - 1) * 0.6);
  return `${text.slice(0, head)}…${text.slice(text.length - (max - head - 1))}`;
}

/** Free text of a field the way the bridge gets it: one line, no whitespace runs, bounded. */
function line(key: string, value: unknown, max = LIMITS[key]): string {
  return fit(tidy(str(value)), max);
}

function textSize(e: ElementIn): number {
  let n = e.label.length + e.context.length + e.description.length + e.placeholder.length + e.section_label.length + e.group.length;
  for (const o of e.options) n += o.label.length + o.value.length;
  return n;
}

interface DeepWalk {
  roots: ShadowRoot[];
  passwords: Element[];
  iframes: HTMLIFrameElement[];
  nodes: number;
}

/** ONE pass over the document and every open shadow root (the assistant's own UI excluded): roots, password inputs, iframes. */
function deepWalk(): DeepWalk {
  const out: DeepWalk = { roots: [], passwords: [], iframes: [], nodes: 0 };
  const pending: (Document | ShadowRoot)[] = [document];
  for (let guard = 0; pending.length && guard < 400; guard++) {
    const root = pending.pop() as Document | ShadowRoot;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const el = node as Element;
      out.nodes++;
      try {
        const shadow = el.shadowRoot;
        if (shadow && !el.hasAttribute(UI_HOST_ATTRIBUTE)) {
          out.roots.push(shadow);
          pending.push(shadow);
        }
        const tag = el.tagName;
        if (tag === "INPUT") {
          if ((el as HTMLInputElement).type === "password") out.passwords.push(el);
        } else if (tag === "IFRAME") {
          out.iframes.push(el as HTMLIFrameElement);
        }
      } catch {
        /* a node whose getters throw (a hostile page): it is left out, the rest of the page is still walked */
      }
    }
  }
  return out;
}

/**
 * Anti-spam honeypots are inputs a human never sees (off-screen, 1px, tabindex=-1). They are "visible" to the observer, so
 * they would otherwise be filled by "Fill verified" and flag the application as automated. Never touch them.
 */
function looksLikeTrap(node: HTMLElement, kind: FieldKind): boolean {
  if (kind === "file" || kind === "radio" || kind === "checkbox" || kind === "checkboxes") return false; // legitimately styled away
  try {
    const r = node.getBoundingClientRect();
    if ((r.width < 6 || r.height < 6) && !narrowComboboxInPlainSight(node, kind, r)) return true;
    if (r.right + window.scrollX <= 0 || r.bottom + window.scrollY <= 0) return true;
    // clipped away by a tiny overflow:hidden / clip-path ancestor (the 1px wrapper trick)
    let ancestor: HTMLElement | null = node.parentElement;
    for (let depth = 0; ancestor && depth < 6; depth++, ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      const clips = style.overflow !== "visible" || style.clipPath !== "none" || (style.clip !== "auto" && style.clip !== "rect(auto, auto, auto, auto)");
      if (!clips) continue;
      const box = ancestor.getBoundingClientRect();
      if (box.width < 6 || box.height < 6) return true;
    }
    if (isTextKind(kind) && hiddenByStyle(node)) return true;
    if (kind !== "combobox" && node.getAttribute("tabindex") === "-1") return true;
    if (kind === "combobox" && node.getAttribute("tabindex") === "-1" && (node.getAttribute("role") !== "combobox" || r.width < 20)) return true;
  } catch {
    return false;
  }
  return false;
}

/** The box a person sees and clicks around a combobox input (react-select's control, a design system's "...-control"). */
const CONTROL_BOX = '.select__control,[class*="control" i]';
/** A field container that is itself labelled (the label is what makes a narrow input a real field). */
const FIELD_BOX = "[data-field-path],.ashby-application-form-field-entry,.application-question,.form-group,.form-field,.field,fieldset,[role=group]";

function bigAndRendered(el: Element): boolean {
  if (!isRendered(el)) return false;
  const box = el.getBoundingClientRect();
  if (box.width < 24 || box.height < 24) return false;
  const style = getComputedStyle(el);
  return style.visibility !== "hidden" && Number(style.opacity) !== 0;
}

function hasVisibleLabel(node: HTMLElement, within?: Element): boolean {
  const labels: Element[] = [...((node as HTMLInputElement).labels ?? [])];
  const root = node.getRootNode() as Document | ShadowRoot;
  for (const id of (node.getAttribute("aria-labelledby") ?? "").split(/\s+/)) {
    const target = id ? root.getElementById?.(id) : null;
    if (target && target !== node) labels.push(target);
  }
  if (within) labels.push(...within.querySelectorAll("label,legend"));
  return labels.some((l) => isRendered(l) && tidy(l.textContent ?? "") !== "");
}

/**
 * A combobox INPUT can be a few pixels wide on purpose: react-select sizes its empty search input to its text (4px) inside a wide
 * control. That is no honeypot as long as the person can see what it belongs to: a visible control box of at least 24x24 around it
 * (react-select's control, `[class*=control]`, a labelled field container) or a visible label of its own. Hairline (1px) inputs,
 * inputs that are not reachable by keyboard and inputs outside the page never qualify (the off-screen/clip/tabindex rules still run).
 */
function narrowComboboxInPlainSight(node: HTMLElement, kind: FieldKind, rect: DOMRect): boolean {
  if (kind !== "combobox" || node.tagName !== "INPUT" || node.getAttribute("role") !== "combobox") return false;
  if (rect.width < 2 || rect.height < 12 || node.getAttribute("tabindex") === "-1") return false;
  let el: HTMLElement | null = node.parentElement;
  for (let depth = 0; el && depth < 6; depth++, el = el.parentElement) {
    if (el.matches(CONTROL_BOX) && bigAndRendered(el)) return true;
    if (el.matches(FIELD_BOX) && bigAndRendered(el) && hasVisibleLabel(node, el)) return true;
  }
  return hasVisibleLabel(node);
}

const CLIPPED_PATH = /(?:inset\(\s*(?:50|100)%|circle\(\s*0|ellipse\(\s*0|polygon\(\s*0)/i;
const CLIP_RECT = /^rect\(\s*([\d.]+)(?:px)?[,\s]+([\d.]+)(?:px)?[,\s]+([\d.]+)(?:px)?[,\s]+([\d.]+)(?:px)?\s*\)$/i;

/**
 * More honeypot styles a human never sees although the control has a normal size: clipped away by its own `clip` /
 * `clip-path`, or stacked behind the page (negative z-index on it or a positioned ancestor). Text controls only: custom
 * radios/checkboxes/files are legitimately hidden this way and are handled by their visible proxies.
 */
function hiddenByStyle(node: HTMLElement): boolean {
  const own = getComputedStyle(node);
  const path = own.clipPath;
  if (path && path !== "none" && CLIPPED_PATH.test(path)) return true;
  const rect = CLIP_RECT.exec(own.clip || "");
  if (rect) {
    const [top, right, bottom, left] = [Number(rect[1]), Number(rect[2]), Number(rect[3]), Number(rect[4])];
    if (bottom - top < 6 || right - left < 6) return true;
  }
  let el: HTMLElement | null = node;
  for (let depth = 0; el && depth < 6; depth++, el = el.parentElement) {
    const style = el === node ? own : getComputedStyle(el);
    if (style.position !== "static" && Number(style.zIndex) < 0) return true; // "auto" is NaN: not negative
  }
  return false;
}

/**
 * A `<button>` that is a dropdown: `<button role=combobox>` (Radix Select, Downshift useSelect) or the observer's
 * `<button aria-haspopup=listbox>` (Workday, Headless UI Listbox). Never a form-submit control: an explicit type=button, or no
 * form to submit at all.
 */
function isButtonCombobox(raw: Raw): boolean {
  if (str(raw.role) !== "combobox" || str(raw.tag) !== "button") return false;
  const explicit = raw.explicit_type === null || raw.explicit_type === undefined ? "" : str(raw.explicit_type).toLowerCase();
  return explicit === "button" || (explicit === "" && str(raw.form_id) === "page");
}

/** A dropdown button needs a question label of its own: a name that is just what the button shows (its value) names nothing. */
function namedBeyondItsValue(raw: Raw, node: HTMLElement): boolean {
  const label = tidy(str(raw.label));
  return label !== "" && label !== tidy(node.innerText ?? node.textContent ?? "") && !isComboPlaceholder(label);
}

/**
 * One-time codes, card, bank and ID numbers, security answers and password-like fields are never assist fields (sensitive.ts). The
 * autocomplete token names the class for every kind of control; label, name and id only for controls that take TEXT (a Yes/No question
 * about "social security benefits" is a question the assistant may help with).
 */
function isNeverTouched(raw: Raw, kind: FieldKind): boolean {
  const text = kind !== "radio" && kind !== "checkbox" && kind !== "checkboxes";
  return sensitiveClass({
    autocomplete: str(raw.autocomplete),
    label: text ? str(raw.label) : "",
    name: text ? str(raw.name) : "",
    id: text ? str(raw.id) : "",
    placeholder: text ? str(raw.placeholder) : "",
  }) !== "";
}

/** What stands for a styled-away control: the observer's proxies and, for toggles and files, their labels. */
function standsOf(raw: Raw, node: HTMLElement, kind: FieldKind, cache: ObserverCache | null): Element[] {
  const out: Element[] = [];
  for (const key of STAND_KEYS) {
    const el = nodeOf(cache, raw[key]);
    if (el && el !== node) out.push(el);
  }
  if (kind === "file" || kind === "radio" || kind === "checkbox" || kind === "checkboxes") out.push(...defaultStands(node));
  return out;
}

function kindOf(raw: Raw): FieldKind | null {
  const role = str(raw.role);
  const tag = str(raw.tag);
  const type = str(raw.type);
  if (!ASSIST_ROLES.has(role)) return null;
  // toggles may be <button>s (type defaults to "submit"): their ARIA role says they are choices, not submit buttons
  if (BLOCKED_TYPES.has(type) && role !== "radio" && role !== "checkbox" && role !== "switch" && !isButtonCombobox(raw)) return null;
  if (role === "upload" || type === "file") return "file";
  if (role === "checkbox" || role === "switch") return raw.multiple ? "checkboxes" : "checkbox";
  if (role === "radio") return "radio";
  if (tag === "select") return "select";
  if (role === "combobox") return "combobox";
  const ops: string[] = Array.isArray(raw.operations) ? raw.operations : [];
  if (!ops.includes("TYPE_TEXT")) return null;
  if (tag === "textarea") return "textarea";
  if (tag !== "input") return "editable";
  return "text";
}

/**
 * What a custom combobox shows as its selection, as the resolver may see it: zero-width characters and icons stripped, and a
 * placeholder ("Select One", "Select a country", MUI's invisible space, a Radix trigger that marks itself `data-placeholder`) is
 * NOTHING, so an empty dropdown never looks filled. Inputs are never read beyond the observer's own `selected_text` (what the person
 * typed into one is not a selection); other widgets whose display cannot be read may still have been filled by the assistant itself
 * (`comboSelection` remembers what it saw).
 */
function shownSelection(raw: Raw, node: HTMLElement): string {
  const input = node.tagName === "INPUT";
  let shown = str(raw.selected_text).replace(ZERO_WIDTH, "").trim();
  if (!input) {
    // the same rule as fill.ts (`comboSelection`): a trigger marks ITSELF `data-placeholder` while it shows its placeholder (Radix Select sets
    // the empty attribute until something is chosen); a text-valued attribute is a setting (Chosen, Select2), which only counts as the
    // placeholder when the widget shows exactly that text
    const mark = node.getAttribute("data-placeholder");
    if (mark !== null && (mark.trim() === "" || squash(mark) === squash(shown))) return "";
    shown = withoutDecoration(node, shown);
  }
  if (shown !== "" && !isComboPlaceholder(shown)) return clamp("selected_text", shown);
  if (input || shown !== "") return ""; // an input's text is never read as a selection; a placeholder says nothing is chosen
  return clamp("selected_text", comboSelection(node)); // nothing shown at all: a value the assistant filled and saw may still count
}

function optionValue(m: GroupMember): string {
  return m.value || m.label;
}

function pruneOptions(options: unknown, maxOptions = MAX_OPTIONS, maxChars = FIELD_OPTION_CHARS): OptionIn[] {
  if (!Array.isArray(options)) return [];
  const out: OptionIn[] = [];
  let chars = 0;
  for (const o of options.slice(0, maxOptions) as Raw[]) {
    const label = str(o?.label).slice(0, 300);
    const value = str(o?.value).slice(0, 300);
    chars += label.length + value.length;
    if (chars > maxChars) break; // fewer options than the page has can only make the answer "needs you", never a wrong one
    out.push({ label, value, disabled: !!o?.disabled });
  }
  return out;
}

/** One option of a select, as a comparable string (its label and its value). */
function optionSig(label: string, value: string): string {
  return `${label}\u241f${value}`;
}

/** The texts of the options of the lists a control points at (`aria-controls` / `aria-owns` / the observer's ids), when they are in the page. */
function listedOptionTexts(node: Element, trigger: Element, raw: Raw): string[] {
  const out: string[] = [];
  try {
    const ids = new Set<string>();
    for (const el of [node, trigger]) for (const attr of ["aria-controls", "aria-owns"]) for (const id of (el.getAttribute(attr) ?? "").split(/\s+/)) if (id) ids.add(id);
    if (Array.isArray(raw.listbox_ids)) for (const id of raw.listbox_ids) if (typeof id === "string" && id) ids.add(id);
    const tree = node.getRootNode() as Document | ShadowRoot;
    for (const id of [...ids].slice(0, 6)) {
      const list = typeof tree.getElementById === "function" ? tree.getElementById(id) : null;
      if (!list) continue;
      for (const option of Array.from(list.querySelectorAll('[role="option"],[role="menuitem"],[role="treeitem"],option,li')).slice(0, 500)) {
        const text = (option.textContent ?? "").trim();
        if (text && text.length <= 300) out.push(text);
      }
    }
  } catch {
    /* a page that throws from getters: the list stays unknown */
  }
  return out;
}

/** Does the control say that a list pops up from it (`aria-haspopup`, `aria-controls`, `aria-owns`)? A widget without any of them is not a listbox display. */
function hasPopupAttr(el: Element): boolean {
  try {
    const popup = (el.getAttribute("aria-haspopup") ?? "").toLowerCase();
    return (popup !== "" && popup !== "false") || !!el.getAttribute("aria-controls") || !!el.getAttribute("aria-owns") || !!el.getAttribute("list");
  } catch {
    return false;
  }
}

function nodeOf(cache: ObserverCache | null, id: unknown): HTMLElement | null {
  if (!cache || typeof id !== "number") return null;
  const node = cache.nodes.get(id);
  try {
    return node instanceof HTMLElement && node.isConnected ? node : null;
  } catch {
    return null; // a node whose `isConnected` throws (a page-defined getter) cannot be trusted to be on the page
  }
}

interface Candidate { raw: Raw; node: HTMLElement; kind: FieldKind }

/** A control whose visibility verdict depends on the scroll position: re-checked when the page (or a container) scrolls. */
interface Watched { node: HTMLElement; stands: Element[]; minSize: number; ghost: boolean; visible: boolean }

/** Selection signature of a choice control: conditional questions depend on it, never on typed text. */
function selectionSig(field: AssistField): string {
  const e = field.element;
  switch (field.kind) {
    case "select": return `${e.value}|${e.selected_values.join(",")}`;
    case "radio":
    case "checkboxes": return field.members.map((m) => (m.checked ? "1" : "0")).join("");
    case "checkbox": return e.checked ? "1" : "0";
    case "combobox": return `${e.has_value ? 1 : 0}|${e.selected_text}`;
    default: return "";
  }
}

export class Scanner {
  private fields: AssistField[] = [];
  private stopList: StopRef[] = [];
  private byNode = new Map<number, AssistField>();
  private byKey = new Map<string, AssistField>();
  private selections = new Map<string, string>();
  private snapshot: ObservationSnapshot | null = null;
  private documentId = "";
  private mo: MutationObserver | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private dueAt = 0;
  private lastScanAt = 0;
  private urlTimer: ReturnType<typeof setInterval> | null = null;
  private url = "";
  private running = false;
  private deferred = false;
  private frameCount = 0;
  private disposers: (() => void)[] = [];
  private walk: DeepWalk = { roots: [], passwords: [], iframes: [], nodes: 0 };
  /** controls whose visibility depends on WHERE the page is scrolled to (off screen: not hit-tested yet; covered: may be uncovered) */
  private watched: Watched[] = [];
  /** the rejected-but-maybe-transient set the last settle rescan was asked for (a rescan is asked once per distinct set) */
  private settleSig = "";
  private rescroll: Debounced | null = null;
  private recheck: ReturnType<typeof setInterval> | null = null;
  /** when a control was last seen to pass a hit test */
  private seen = new WeakMap<Element, number>();
  /** when a control last passed the paint pass at a scan (the pass is not repeated for it for a while: the write asks again) */
  private paintSeen = new WeakMap<Element, { at: number; ok: boolean; reason: VisibilityReason }>();
  /** the stand-ins (proxies, labels) the scan accepted a control through: the write-time check accepts it through the same ones */
  private standsFor = new WeakMap<Element, Element[]>();
  /** per native select: the option signatures the page offered, and the ones that were made by the person (LOW-2: `refusedOptions`) */
  private selectMemory = new WeakMap<HTMLSelectElement, { offered: Set<string>; refused: Set<string>; length: number }>();
  /** controls (and the combobox / popup owners around them) a person has typed into since the scan started (LOW-2) */
  private typed = new WeakSet<Element>();
  /** per custom combobox: every option text the page has ever listed for it (LOW-2: `offeredSelection`) */
  private comboMemory = new Map<string, { raw: Set<string>; norm: Set<string> | null; normFor: number }>();
  /** characters of free text this scan may still hand out */
  private textBudget = SCAN_TEXT_BUDGET;
  /** smoothed cost of one scan: drives the debounce on big pages */
  private costMs = 0;
  readonly stats = { scans: 0, lastMs: 0, maxMs: 0, nodes: 0, parts: { observe: 0, walk: 0, candidates: 0, fields: 0, stops: 0, result: 0 } };

  constructor(private readonly opts: ScannerOptions) {}

  // ---- lifecycle -------------------------------------------------------------------------------------------------------

  start(): void {
    if (this.running) return;
    this.running = true;
    this.url = location.href;
    const observe = (target: Node): MutationObserver | null => {
      try {
        const mo = new MutationObserver(guard((records: MutationRecord[]) => this.onMutations(records)));
        mo.observe(target, { childList: true, subtree: true, attributes: true, attributeFilter: MUTATION_ATTRS, characterData: true });
        return mo;
      } catch {
        return null;
      }
    };
    this.mo = observe(document.documentElement);
    // Everything the scanner hangs on the page runs through `guard`: a page can hand it a node that throws from any getter, and that must
    // never show up as an error of the PAGE, nor stop the next listener (D1c hand-off).
    const on = (target: EventTarget, type: string, fn: EventListener, capture = true) => {
      const safe = guard(fn);
      target.addEventListener(type, safe, capture);
      this.disposers.push(() => target.removeEventListener(type, safe, capture));
    };
    // a choice control changed: conditional questions may appear / disappear (twice: fast and after slow renders)
    const onChange = guard((ev: Event) => {
      const t = composedTarget(ev);
      if (!t || inUiHost(t)) return;
      let choice = true; // a node that cannot even say what it is (a hostile `matches`) is looked at again to be safe
      try {
        choice = !!t.matches?.("select,input[type=radio],input[type=checkbox],[role=radio],[role=checkbox],[role=combobox],[role=switch]");
      } catch {
        /* rescan */
      }
      if (choice) {
        this.request("change", 150);
        this.request("change-late", 700);
      }
    });
    on(document, "change", onChange);
    // LOW-2: a control that something was TYPED into by the person: what it shows afterwards is theirs unless the page offered it (`offeredSelection`)
    const onTyped = guard((ev: Event) => {
      if (!ev.isTrusted || inUiHost(composedTarget(ev))) return;
      if (ev instanceof KeyboardEvent && (ev.key.length !== 1 || ev.ctrlKey || ev.metaKey) && ev.key !== "Backspace" && ev.key !== "Delete") return;
      const t = composedTarget(ev);
      if (!t) return;
      this.typed.add(t);
      for (const anc of [closestDeep(t, '[role="combobox"]'), closestDeep(t, "[aria-haspopup]"), closestDeep(t, '[role="group"],[role="listbox"]')]) if (anc) this.typed.add(anc);
    });
    on(document, "keydown", onTyped);
    on(document, "input", onTyped);
    // what is covered, clipped or scrolled away depends on where the page is: look again (cheaply) once a scroll has settled
    this.rescroll = debounce(guard(() => this.reverify()), 140, 700);
    const settled = guard(() => this.rescroll?.());
    // `change` is not composed and a document-level observer does not see below a shadow root: every open root gets its own
    this.disposers.push(this.opts.roots.bind((root) => {
      const mo = observe(root);
      root.addEventListener("change", onChange, true);
      root.addEventListener("scroll", settled, { capture: true, passive: true }); // `scroll` is not composed: containers inside a root say so here
      this.request("shadow-root", 120); // the widget may already hold (or be about to render) a form
      return () => {
        mo?.disconnect();
        root.removeEventListener("change", onChange, true);
        root.removeEventListener("scroll", settled, true);
      };
    }));
    document.addEventListener("scroll", settled, { capture: true, passive: true });
    window.addEventListener("resize", settled, { passive: true });
    this.disposers.push(() => {
      document.removeEventListener("scroll", settled, true);
      window.removeEventListener("resize", settled);
    });
    on(window, "popstate", () => this.checkUrl());
    on(window, "hashchange", () => this.checkUrl());
    on(window, "load", () => this.request("load", 100), false);
    on(document, "visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      this.checkUrl(); // route changes of a background tab are noticed the moment it is shown
      if (this.deferred) this.request("visible", 50);
    }, false);
    this.urlTimer = every(() => this.checkUrl(), 600);
    this.request("start", 0);
  }

  stop(): void {
    this.running = false;
    this.mo?.disconnect();
    this.mo = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.urlTimer) clearInterval(this.urlTimer);
    this.urlTimer = null;
    this.rescroll?.cancel();
    this.rescroll = null;
    this.syncRecheck(false);
    this.watched = [];
    for (const d of this.disposers.splice(0)) d();
  }

  /** A slow look at the rejected controls for as long as there are any: a cookie layer hidden by a class, a fade-in nobody announced. */
  private syncRecheck(needed: boolean): void {
    if (needed && !this.recheck) this.recheck = every(() => this.reverify(), RECHECK_MS);
    else if (!needed && this.recheck) {
      clearInterval(this.recheck);
      this.recheck = null;
    }
  }

  /**
   * A scroll (or resize) settled: the controls whose visibility depends on where the page is look again. A verdict that flipped
   * (a control that scrolled into view turned out to be covered; a covered one came out from under a banner) asks for a rescan.
   */
  private reverify(): void {
    if (!this.running || !this.opts.isActive() || !this.watched.length || document.visibilityState === "hidden") return;
    const ctx = new VisibilityContext(this.opts.roots.list());
    const live = this.watched.filter((w) => w.node.isConnected); // removal is a mutation: the observer takes care of it
    const now = humanVisibilityMany(live.map((w) => ({ el: w.node, opts: { stands: w.stands, minSize: w.minSize, ghost: w.ghost } })), ctx);
    if (live.some((w, i) => now[i].visible !== w.visible)) {
      this.paintSeen = new WeakMap(); // what the paint pass said a moment ago is not to be trusted: something moved
      this.request("visibility", 0);
    }
  }

  /** Debounced scan; an earlier already-planned scan wins (mutation storms are throttled, never starved). */
  request(reason: string, delay = 300): void {
    if (!this.running) return;
    // adaptive: a scan that costs 40 ms on a big page leaves the page alone for ~280 ms afterwards, never less than MIN_GAP_MS
    const minGap = Math.min(MAX_GAP_MS, Math.max(MIN_GAP_MS, Math.round(this.costMs * GAP_PER_SCAN_MS)));
    const burst = reason === "mutation" ? Math.min(MAX_MUTATION_DELAY_MS, Math.max(delay, Math.round(this.costMs * 4))) : delay;
    const gap = Math.max(0, this.lastScanAt + minGap - Date.now());
    const due = Date.now() + Math.max(burst, gap);
    if (this.timer && this.dueAt <= due) return;
    if (this.timer) clearTimeout(this.timer);
    this.dueAt = due;
    this.timer = after(() => {
      this.timer = null;
      this.scanNow();
    }, Math.max(0, due - Date.now()));
  }

  private checkUrl(): void {
    // a hidden tab is not looked at: the URL poll is a cheap string compare, but there is no reason to wake a background tab for it
    if (!this.running || document.visibilityState === "hidden" || location.href === this.url) return;
    this.url = location.href;
    this.opts.onUrlChange?.(this.url);
    this.request("url", 60);
    this.request("url-late", 800);
  }

  private onMutations(records: MutationRecord[]): void {
    if (!this.running || !this.opts.isActive()) return;
    let relevant = false;
    let frames = false;
    for (const r of records) {
      const target = r.target as Node;
      if (inUiHost(target)) continue;
      if (r.type === "characterData") {
        // a label / legend / option text changed: only text inside form-ish containers can change a question
        if (closestDeep(target.parentElement, `${FORMISH},label,legend,option,[role=option]`)) relevant = true;
        continue;
      }
      if (r.type === "attributes") {
        const name = r.attributeName ?? "";
        const el = target as Element;
        if ((name === "style" || name === "class") && !closestDeep(el, FORMISH)) continue;
        relevant = true;
        continue;
      }
      const touched = [...r.addedNodes, ...r.removedNodes];
      if (touched.length && touched.every((n) => inUiHost(n) || n.nodeType === 8 || (n.nodeType === 3 && !(n.textContent ?? "").trim()))) continue;
      relevant = true;
      for (const n of r.addedNodes) {
        if (n.nodeType === 1 && ((n as Element).tagName === "IFRAME" || (n as Element).querySelector?.("iframe"))) frames = true;
      }
    }
    if (relevant) this.request("mutation", 300);
    if (frames) this.opts.onFramesChanged?.();
  }

  // ---- the scan ----------------------------------------------------------------------------------------------------------

  scanNow(): ScanResult | null {
    if (!this.running || !this.opts.isActive()) return null;
    if (document.visibilityState === "hidden" && this.fields.length) {
      this.deferred = true;
      return null;
    }
    this.deferred = false;
    const started = performance.now();
    let snap: ObservationSnapshot;
    try {
      snap = observe(OBSERVE_OPTIONS);
    } catch {
      return null; // (the observer itself survives a node whose `isConnected` throws: it drops that node and goes on)
    }
    const tObserved = performance.now();
    this.lastScanAt = Date.now();
    this.snapshot = snap;
    const cache = observerCache();
    const reset = !!this.documentId && this.documentId !== snap.document_id;
    this.documentId = snap.document_id;

    this.walk = deepWalk();
    this.opts.roots.sync(this.walk.roots);
    const tWalked = performance.now();
    const candidates = this.candidates(snap, cache);
    const tCandidates = performance.now();
    const fields = this.buildFields(candidates, cache);
    const tFields = performance.now();
    const stops = this.buildStops(fields);
    const tStops = performance.now();

    const previous = this.byKey;
    const added: AssistField[] = [];
    const changed: AssistField[] = [];
    const selectionChanged: AssistField[] = [];
    const nextSelections = new Map<string, string>();
    for (const f of fields) {
      const before = previous.get(f.key);
      const sig = selectionSig(f);
      nextSelections.set(f.key, sig);
      if (!before) added.push(f);
      else if (before.digest !== f.digest) changed.push(f);
      else if (this.selections.get(f.key) !== sig && isChoice(f)) selectionChanged.push(f);
    }
    const nextKeys = new Set(fields.map((f) => f.key));
    const removed = [...previous.values()].filter((f) => !nextKeys.has(f.key));
    const renamed = pairReplaced(added, removed);

    this.fields = fields;
    this.stopList = stops;
    this.byKey = new Map(fields.map((f) => [f.key, f]));
    this.byNode = new Map();
    for (const f of fields) for (const id of f.aliasIds) this.byNode.set(id, f);
    this.selections = nextSelections;

    const flags: PageFlags = { challenge: !!snap.challenge, mfa: !!snap.mfa, login: !!snap.login };
    const frameCount = stops.filter((s) => s.kind === "frame").length;
    if (frameCount !== this.frameCount) {
      this.frameCount = frameCount;
      this.opts.onFramesChanged?.();
    }
    const scanMs = performance.now() - started;
    const parts = this.stats.parts;
    parts.observe = round1(tObserved - started);
    parts.walk = round1(tWalked - tObserved);
    parts.candidates = round1(tCandidates - tWalked);
    parts.fields = round1(tFields - tCandidates);
    parts.stops = round1(tStops - tFields);
    const result: ScanResult = {
      documentId: snap.document_id, url: snap.url, title: snap.title, fields, stops, flags, snapshot: snap, scannedAt: this.lastScanAt, scanMs,
    };
    try {
      this.opts.onResult(result, { added, changed, removed, selectionChanged, reset, renamed });
    } finally {
      // the consumers' work counts too: it is the same main thread
      const total = performance.now() - started;
      parts.result = round1(performance.now() - tStops);
      this.costMs = this.costMs ? this.costMs * 0.5 + total * 0.5 : total;
      this.stats.scans++;
      this.stats.lastMs = Math.round(total * 10) / 10;
      this.stats.maxMs = Math.max(this.stats.maxMs, this.stats.lastMs);
      this.stats.nodes = this.walk.nodes;
    }
    return result;
  }

  /**
   * A fresh look (no memory of the last scan) at whether a person can still see and click this field: what is written to it, or what
   * moves focus to it, can ask right before it happens. A control that was off screen at the scan (judged by its styles only) is
   * hit-tested now if a scroll has brought it on screen. Never throws; "yes" when the check itself cannot tell.
   */
  stillVisible(field: AssistField, strict = false, forChip = false): boolean {
    try {
      const ctx = new VisibilityContext(this.opts.roots.list(), true); // a write: the strong paint pass
      const targets: { el: HTMLElement; proxies: (Element | null | undefined)[] }[] = field.members.length
        ? field.members.map((m) => ({ el: m.el, proxies: [m.proxy] }))
        : [{ el: field.el, proxies: [field.links.proxy, field.links.trigger] }];
      const live = targets.filter((t) => t.el.isConnected);
      const standsOfTarget = (t: { el: HTMLElement; proxies: (Element | null | undefined)[] }): Element[] =>
        [...new Set([...(this.standsFor.get(t.el) ?? []), ...t.proxies.filter((p): p is Element => !!p && p !== t.el), ...defaultStands(t.el)])];
      const verdicts = humanVisibilityMany(live.map((t) => {
        const narrow = field.kind === "combobox" && narrowComboboxInPlainSight(t.el, field.kind, t.el.getBoundingClientRect());
        return { el: t.el, opts: { stands: standsOfTarget(t), minSize: narrow ? 2 : 6, ghost: isTextKind(field.kind) } };
      }), ctx);
      const seen = live.some((t, i) => {
        const verdict = verdicts[i];
        // `strict` (a file, a write the person is looking at): what is off screen was judged by its styles only, which is not enough
        if (verdict.visible) return !strict || verdict.tested === "hit";
        // The calendar / list of the question the person has just left stays open over the next one (the key that would have closed it
        // went to the assistant, not to the widget): a control that was seen before and is covered by ANOTHER question's own popup is the
        // person's to fill. Any other cover (a layer of the page, a decoy anywhere else) still refuses the write.
        if (verdict.reason !== "covered" || !this.seen.has(t.el)) return false;
        const by = this.coveredBy(t.el, field, standsOfTarget(t), ctx);
        // a popup the page itself calls a dialog / listbox / menu over the control does not stop the chip (it says what is waiting, and the write is
        // refused with a calm note until the popup is closed), only the write
        return by === "neighbour" || (by === "popup" && forChip);
      });
      return seen;
    } catch {
      return true;
    }
  }

  /**
   * Is the control covered by something that sits inside the own block of another question of this scan (that question's date picker,
   * its list of suggestions)? The block is the question's container (tabstops.ts `ownBlock`): a popup that belongs to a neighbour lives in it.
   */
  private coveredBy(el: HTMLElement, field: AssistField, stands: readonly Element[], ctx: VisibilityContext): "neighbour" | "popup" | null {
    try {
      const box = el.getBoundingClientRect();
      const root = el.getRootNode() as Document | ShadowRoot;
      if (typeof root.elementsFromPoint !== "function" || box.width <= 0 || box.height <= 0) return null;
      const stack = root.elementsFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      for (const top of stack) {
        if (inUiHost(top) || top === el || composedContains(el, top) || stands.some((st) => composedContains(st, top))) continue;
        if (!ctx.paints(top)) continue;
        // A popup the page itself calls a dialog, a listbox, a grid or a menu (the calendar of a date picker that is still open) over the control
        // is no excuse: nobody can see the control, and the write waits until the popup is closed.
        if (insideOpenPopup(top, el)) return "popup";
        // the first thing that paints over the control: is it a neighbour's?
        const neighbours = this.fields.some((other) => {
          if (other.key === field.key || !other.el.isConnected) return false;
          const block = ownBlock(other, this.fields);
          return !!block && composedContains(block, top);
        });
        return neighbours ? "neighbour" : null;
      }
    } catch {
      /* cannot tell: the strict answer stands */
    }
    return null;
  }

  /** Current smoothed cost of a scan in ms (tests and the adaptive debounce). */
  scanCost(): number {
    return this.costMs;
  }

  private candidates(snap: ObservationSnapshot, cache: ObserverCache | null): Candidate[] {
    const out: Candidate[] = [];
    const ctx = new VisibilityContext(this.opts.roots.list()); // one memory of styles and boxes for the whole scan: shared ancestors are read once
    const watched: Watched[] = [];
    const settling: number[] = [];
    // the controls that are worth a visibility verdict, with what stands for each (the verdicts are made together: the paint pass of
    // visibility.ts costs a style recalculation, which a scan pays once and not once per control)
    const pending: { raw: Raw; node: HTMLElement; kind: FieldKind; stands: Element[]; minSize: number; ghost: boolean }[] = [];
    for (const raw of snap.elements as Raw[]) {
      try {
        const kind = kindOf(raw);
        if (!kind) continue;
        if (raw.auxiliary_control) continue;
        // a disabled radio/checkbox stays in its group (as a disabled option); every other disabled control is not an assist field
        if (raw.enabled === false && kind !== "radio" && kind !== "checkboxes") continue;
        const node = nodeOf(cache, raw.node_id);
        if (!node || inUiHost(node)) continue;
        if (hiddenAbove(node)) continue;
        if (isNeverTouched(raw, kind)) continue;
        if (kind === "combobox" && str(raw.tag) === "button" && !namedBeyondItsValue(raw, node)) continue;
        if (looksLikeTrap(node, kind)) continue;
        // a person must be able to see and click it (or its visible proxy): covered, clipped, transparent and ghost controls are not fields
        const stands = standsOf(raw, node, kind, cache);
        this.standsFor.set(node, stands);
        const minSize = kind === "combobox" && narrowComboboxInPlainSight(node, kind, node.getBoundingClientRect()) ? 2 : 6;
        pending.push({ raw, node, kind, stands, minSize, ghost: isTextKind(kind) });
      } catch {
        /* a control whose getters throw (a hostile page) is no field: the rest of the page is still scanned */
      }
    }
    let verdicts: VisibilityVerdict[] = [];
    try {
      const now = Date.now();
      // a page this big pays 30 to 60 ms of style recalculation for the pass: its scans skip it (the chip and every write still ask, with the pass)
      const heavy = this.walk.nodes > PAINT_MAX_NODES;
      const memo = pending.map((p) => {
        const known = this.paintSeen.get(p.node);
        return heavy ? { at: now, ok: true, reason: "" as VisibilityReason } : known && now - known.at < PAINT_TTL_MS ? known : null;
      });
      verdicts = humanVisibilityMany(pending.map((p, i) => ({
        el: p.node, opts: { stands: p.stands, minSize: p.minSize, ghost: p.ghost, paint: !memo[i] },
      })), ctx);
      pending.forEach((p, i) => {
        const v = verdicts[i];
        const known = memo[i];
        if (known && !known.ok && v.visible) verdicts[i] = { visible: false, reason: known.reason, tested: "hit", byPaint: true }; // it failed the pass a moment ago
        else if (!known && v.tested === "hit" && (v.visible || v.byPaint)) this.paintSeen.set(p.node, { at: now, ok: v.visible, reason: v.reason });
      });
    } catch {
      verdicts = pending.map(() => ({ visible: true, reason: "" as VisibilityReason, tested: "geometry" as const }));
    }
    pending.forEach((p, i) => {
      try {
        const { raw, node, kind, stands, minSize, ghost } = p;
        const verdict = verdicts[i];
        if (!verdict.visible) {
          const patient = verdict.reason === "covered" && Date.now() - (this.seen.get(node) ?? 0) < SEEN_TTL_MS;
          if ((WATCHED_REASONS.has(verdict.reason) || patient) && watched.length < WATCH_LIMIT) {
            watched.push({ node, stands, minSize, ghost, visible: false });
            if (!patient) settling.push(raw.node_id);
          }
          if (!patient) return;
          out.push({ raw, node, kind });
          return;
        }
        // seen only by its styles and layout (off screen): hit-tested the moment a scroll brings it into view
        if (verdict.tested === "geometry" && watched.length < WATCH_LIMIT) watched.push({ node, stands, minSize, ghost, visible: true });
        else if (verdict.tested === "hit") this.seen.set(node, Date.now());
        out.push({ raw, node, kind });
      } catch {
        /* a control whose getters throw (a hostile page) is no field: the rest of the page is still scanned */
      }
    });
    this.watched = watched;
    this.syncRecheck(watched.some((w) => !w.visible));
    // a fade-in, a banner on its way out or a layout that has not settled: ONE more look per distinct set of rejected controls
    const signature = settling.join(",");
    if (signature && signature !== this.settleSig) this.request("settle", SETTLE_MS);
    this.settleSig = signature;
    return out;
  }

  private buildFields(candidates: Candidate[], cache: ObserverCache | null): AssistField[] {
    this.textBudget = SCAN_TEXT_BUDGET;
    const sorted = [...candidates].sort((a, b) => compareTreeOrder(a.node, b.node));
    const groups = new Map<string, Candidate[]>();
    const singles: Candidate[] = [];
    for (const c of sorted) {
      const grouped = (c.kind === "radio" || c.kind === "checkboxes") && str(c.raw.group) !== "";
      if (!grouped) {
        singles.push(c);
        continue;
      }
      const key = JSON.stringify([c.raw.form_id, c.kind, c.raw.group]);
      const list = groups.get(key);
      if (list) list.push(c);
      else groups.set(key, [c]);
    }
    const built: AssistField[] = [];
    singles.splice(0, singles.length, ...singles.filter((c) => c.raw.enabled !== false));
    for (const list of [...groups.entries()]) if (list[1].every((c) => c.raw.enabled === false)) groups.delete(list[0]);
    for (const c of singles) {
      const f = this.safeBuild(c.kind, [c], cache);
      if (f) built.push(f);
    }
    for (const list of groups.values()) {
      const f = this.safeBuild(list[0].kind, list, cache);
      if (f) built.push(f);
    }
    built.sort((a, b) => compareTreeOrder(a.el, b.el));
    // `position` (document order within this scan) lets the server read a partial refresh in the order of the page
    built.forEach((f, i) => { f.order = i; f.element.position = i; });
    return built.slice(0, MAX_FIELDS);
  }

  /** One control whose getters throw (a hostile page) is no field; it never costs the page its other fields. */
  private safeBuild(kind: FieldKind, list: Candidate[], cache: ObserverCache | null): AssistField | null {
    try {
      return this.build(kind, list, cache);
    } catch {
      return null;
    }
  }

  private build(kind: FieldKind, list: Candidate[], cache: ObserverCache | null): AssistField | null {
    const first = list[0];
    const raw = first.raw;
    const node = first.node;
    const nodeId = raw.node_id as number;
    const frameKey = this.opts.frameKey;

    const members: GroupMember[] = [];
    const aliasIds: number[] = [];
    if (kind === "radio" || kind === "checkboxes") {
      for (const c of list) {
        const proxy = nodeOf(cache, c.raw.toggle_proxy_id) ?? nodeOf(cache, c.raw.visual_toggle_proxy_id);
        members.push({
          nodeId: c.raw.node_id, el: c.node, proxy, label: tidy(str(c.raw.label)), value: str(c.raw.value),
          checked: !!c.raw.checked, enabled: c.raw.enabled !== false && (kind !== "radio" || (c.raw.operations ?? []).includes("CHECK")),
        });
        aliasIds.push(c.raw.node_id);
        for (const k of ["toggle_proxy_id", "visual_toggle_proxy_id"]) if (typeof c.raw[k] === "number") aliasIds.push(c.raw[k]);
      }
    } else {
      aliasIds.push(nodeId);
    }
    const proxyEl = nodeOf(cache, raw.upload_proxy_id) ?? nodeOf(cache, raw.interaction_proxy_id) ?? nodeOf(cache, raw.toggle_proxy_id);
    const trigger = nodeOf(cache, raw.dropdown_trigger_id);
    const search = nodeOf(cache, raw.dropdown_search_id);
    const clear = nodeOf(cache, raw.clear_proxy_id);
    for (const k of ["upload_proxy_id", "interaction_proxy_id", "dropdown_trigger_id", "dropdown_search_id", "clear_proxy_id", "toggle_proxy_id"]) {
      if (typeof raw[k] === "number") aliasIds.push(raw[k]);
    }

    let label = tidy(str(raw.label));
    const context = tidy(str(raw.context));
    if (kind === "radio" || kind === "checkboxes") label = context || label;
    if (!label) label = tidy(str(raw.placeholder)) || str(raw.name) || str(raw.id);
    // contenteditable labels can fall back to the node's own text: never let typed text become a label
    if ((kind === "editable" || (kind === "combobox" && node.isContentEditable)) && label && label === tidy(node.innerText ?? "")) label = tidy(str(raw.placeholder));

    const element = this.prune(raw, kind, members, node, label, `${frameKey}:${nodeId}`);
    const anchorEl = isRendered(node) ? node : members.find((m) => m.proxy)?.proxy ?? proxyEl ?? node;
    const digestParts = [
      element.label, element.context, element.description, kind, element.role, element.tag, element.type, element.explicit_type ?? "",
      element.required, element.multiple, element.name, element.group, element.section_label, element.field_path, element.accept,
      element.max_length ?? "", element.min_length ?? "", element.pattern, element.placeholder,
      element.options.map((o) => `${o.label}␞${o.value}␞${o.disabled ? 1 : 0}`).join("␝"),
    ];
    return {
      key: `${frameKey}:${nodeId}`, frameKey, nodeId, kind, el: node, focusEl: node, anchorEl, members, aliasIds,
      element, digest: hashText(digestParts.join("␟")), order: 0, section: clip(str(raw.section_label), 120),
      // what the PERSON reads (chips, announcements, the panel's rows) has no required-marker glyphs; `element.label`, which Meridian reads, is the page's text
      label: clip(stripRequiredMarks(label), 300),
      required: !!raw.required,
      links: { trigger, search, proxy: proxyEl, clear, listboxIds: Array.isArray(raw.listbox_ids) ? raw.listbox_ids.map(String) : [] },
    };
  }

  /**
   * LOW-2: the options of a native `<select>` that the page did not offer. The first time a select is seen everything in it is the page's
   * (the person has not had the chance to add anything yet: the assistant starts first, or there is nothing to tell apart). Later, an option
   * that appears already selected and that no scan has seen before is one the person made (a tag input appends a selected `<option>`): it is
   * refused for good. An option that appears unselected is the page's (a state list that fills after the country was chosen).
   */
  private refusedOptions(select: HTMLSelectElement): ReadonlySet<string> {
    let memory = this.selectMemory.get(select);
    const first = !memory;
    if (!memory) {
      memory = { offered: new Set(), refused: new Set(), length: -1 };
      this.selectMemory.set(select, memory);
    }
    const list = select.options;
    const note = (o: HTMLOptionElement): void => {
      const sig = optionSig(o.label || o.text, o.value);
      if (memory.offered.has(sig) || memory.refused.has(sig)) return;
      if (first || !o.selected) memory.offered.add(sig);
      else memory.refused.add(sig);
    };
    if (first || list.length !== memory.length) {
      // the list is new or grew / shrank: every option is looked at (new unselected ones are the page's, new selected ones are the person's)
      for (let i = 0; i < list.length && i < MAX_OPTIONS; i++) note(list[i]);
      memory.length = list.length;
    } else {
      // the same options as the last time: only what is selected can be new
      let n = 0;
      for (const o of Array.from(select.selectedOptions)) if (n++ < 100) note(o);
    }
    return memory.refused;
  }

  /**
   * LOW-2: what a custom combobox shows, as far as it is a choice the page offered. Withheld (the field still says `has_value`) when the
   * widget has no list at all (a div that echoes what is typed), and, when the options of the control have been seen (now or in an earlier
   * scan: the list is only in the page while it is open), when the text holds anything that is not one of them (a typed value of a
   * creatable select). A list nobody has seen (a lazily rendered one that was never opened) is trusted as the page's own display.
   */
  private offeredSelection(key: string, raw: Raw, node: HTMLElement, options: readonly OptionIn[], shown: string): string {
    let memory = this.comboMemory.get(key);
    if (!memory) {
      if (this.comboMemory.size >= MAX_COMBO_MEMORY) this.comboMemory.delete(this.comboMemory.keys().next().value as string);
      memory = { raw: new Set(), norm: null, normFor: -1 };
      this.comboMemory.set(key, memory);
    }
    for (const o of options) {
      if (memory.raw.size >= MAX_OFFERED) break;
      if (o.label) memory.raw.add(o.label);
      if (o.value) memory.raw.add(o.value);
    }
    if (!shown) return "";
    const trigger = nodeOf(observerCache(), raw.dropdown_trigger_id) ?? node;
    // the list the control points at may be in the page even while it is closed
    for (const text of listedOptionTexts(node, trigger, raw)) if (memory.raw.size < MAX_OFFERED) memory.raw.add(text);
    const listed = options.length > 0 || memory.raw.size > 0 || (Array.isArray(raw.listbox_ids) && raw.listbox_ids.length > 0)
      || hasPopupAttr(node) || hasPopupAttr(trigger);
    if (!listed) return "";
    if (!memory.raw.size) return this.typed.has(node) || this.typed.has(trigger) ? "" : shown; // nobody has seen its list: what was typed there cannot be told from a choice
    if (!memory.norm || memory.normFor !== memory.raw.size) {
      memory.norm = new Set();
      for (const text of memory.raw) {
        const n = normalizeText(text);
        if (n) memory.norm.add(n);
      }
      memory.normFor = memory.raw.size;
    }
    let rest = ` ${normalizeText(shown)} `;
    for (const offered of [...memory.norm].sort((a, b) => b.length - a.length)) rest = rest.split(` ${offered} `).join("  ").split(` ${offered} `).join("  ");
    return rest.replace(/\s+/g, "").length <= 1 ? shown : "";
  }

  /** Observer element -> what the bridge may see. Text controls never carry what the user typed. */
  private prune(raw: Raw, kind: FieldKind, members: GroupMember[], node: HTMLElement, label: string, key: string): ElementIn {
    const text = isTextKind(kind);
    const group = kind === "radio" || kind === "checkboxes";
    const typedValue = str(raw.value);
    const editableCombo = kind === "combobox" && (str(raw.tag) !== "input" || !raw.readonly) && node.isContentEditable;
    // a scan that already handed out its share of text sends the rest of the fields lean: the question and a few options, nothing else
    const lean = this.textBudget < LEAN_BELOW;
    let options: OptionIn[] = pruneOptions(raw.options, lean ? LEAN_OPTIONS : MAX_OPTIONS);
    let selectedValues: string[] = Array.isArray(raw.selected_values) ? raw.selected_values.map((v: unknown) => str(v).slice(0, 300)).slice(0, 100) : [];
    let value = "";
    let selectedText = kind === "combobox" && !editableCombo ? shownSelection(raw, node) : "";
    let checked = !!raw.checked;
    let hasValue = false;
    if (kind === "select") {
      value = clamp("value", typedValue);
      hasValue = selectedValues.some((v) => v !== "");
      // LOW-2: an option the page did not offer (a tag / creatable select that builds `<option>`s out of what the person types) is theirs:
      // it never leaves the page, not as an option, not as a selection. That the field holds something still does (`has_value`).
      const refused = node instanceof HTMLSelectElement ? this.refusedOptions(node) : null;
      if (refused && refused.size) {
        const own = (label: string, v: string): boolean => refused.has(optionSig(label, v));
        options = options.filter((o) => !own(o.label, o.value));
        const taken = node instanceof HTMLSelectElement ? Array.from(node.selectedOptions).filter((o) => refused.has(optionSig(o.label || o.text, o.value))) : [];
        const gone = new Set(taken.map((o) => o.value));
        selectedValues = selectedValues.filter((v) => !gone.has(v));
        if (gone.has(typedValue)) value = "";
      }
    } else if (group) {
      options = members.map((m) => ({ label: m.label.slice(0, 300), value: optionValue(m).slice(0, 300), disabled: !m.enabled }));
      const on = members.filter((m) => m.checked);
      selectedValues = on.map((m) => optionValue(m).slice(0, 300));
      value = kind === "radio" ? (on[0] ? optionValue(on[0]).slice(0, 300) : "") : "";
      selectedText = on.map((m) => m.label).join(", ").slice(0, 300);
      checked = on.length > 0;
      hasValue = on.length > 0;
    } else if (kind === "checkbox") {
      value = clamp("value", typedValue);
      hasValue = checked;
    } else if (kind === "combobox") {
      hasValue = !!selectedText || (typedValue.replace(ZERO_WIDTH, "").trim() !== "" && !raw.expanded && !editableCombo);
      // LOW-2: what the control SHOWS counts as a selection only when the page offered it from a list (see `offeredSelection`); text a person
      // typed into a widget that shows it (a creatable select, a tag input, a div that echoes keys) is theirs and stays on the page
      selectedText = this.offeredSelection(key, raw, node, options, selectedText);
    } else if (kind === "file") {
      hasValue = typedValue.trim() !== "";
    } else if (text) {
      hasValue = typedValue.replace(ZERO_WIDTH, "").trim() !== "";
      selectedValues = [];
    }
    const element: ElementIn = {
      node_id: raw.node_id,
      role: clamp("role", raw.role),
      tag: clamp("tag", raw.tag),
      type: clamp("type", raw.type),
      explicit_type: raw.explicit_type === null || raw.explicit_type === undefined ? null : clamp("explicit_type", raw.explicit_type),
      label: fit(label, lean ? LEAN_TEXT : LIMITS.label),
      context: line("context", raw.context, lean ? LEAN_TEXT : LIMITS.context),
      description: lean ? "" : line("description", raw.description),
      placeholder: line("placeholder", raw.placeholder),
      section_id: clamp("section_id", raw.section_id),
      section_label: line("section_label", raw.section_label),
      form_id: typeof raw.form_id === "number" ? raw.form_id : clamp("section_id", raw.form_id),
      group: line("group", raw.group),
      field_path: clamp("field_path", raw.field_path),
      name: clamp("name", raw.name),
      id: clamp("id", raw.id),
      autocomplete: clamp("autocomplete", raw.autocomplete),
      required: !!raw.required,
      enabled: raw.enabled !== false,
      readonly: !!raw.readonly,
      multiple: group ? kind === "checkboxes" : !!raw.multiple,
      checked,
      expanded: !!raw.expanded,
      has_value: hasValue,
      value,
      selected_text: selectedText,
      selected_values: selectedValues,
      max_length: typeof raw.max_length === "number" ? raw.max_length : null,
      min_length: typeof raw.min_length === "number" ? raw.min_length : null,
      pattern: clamp("pattern", raw.pattern),
      min: raw.min ?? null,
      max: raw.max ?? null,
      step: raw.step ?? null,
      accept: clamp("accept", raw.accept),
      operations: Array.isArray(raw.operations) ? raw.operations.map(String).slice(0, 12) : [],
      options,
      phone_country_code: raw.phone_country_code ?? null,
      phone_national_only: !!raw.phone_national_only,
      auxiliary_control: !!raw.auxiliary_control,
      manual_key: clamp("manual_key", raw.manual_key),
    };
    this.textBudget -= textSize(element);
    return element;
  }

  private buildStops(fields: AssistField[]): StopRef[] {
    const stops: StopRef[] = fields.map((field) => ({ kind: "assist" as const, field }));
    const own = new Set<Element>();
    for (const f of fields) {
      own.add(f.el);
      for (const m of f.members) own.add(m.el);
    }
    for (const el of this.walk.passwords) {
      if (inUiHost(el) || own.has(el) || !isRendered(el) || (el as HTMLInputElement).disabled) continue;
      stops.push({ kind: "native", el });
    }
    const seen = new VisibilityContext();
    for (const el of this.walk.iframes) {
      if (inUiHost(el) || !isRendered(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 24 || r.height < 24) continue;
      const src = el.src || "";
      if (/recaptcha|hcaptcha|challenges\.cloudflare|turnstile/i.test(src)) stops.push({ kind: "native", el });
      // a frame a person cannot see (transparent, click-through, covered, far off the page) is no place to walk into: its fields are as hidden as it is
      else if (frameIsHumanVisible(el, seen)) stops.push({ kind: "frame", el });
    }
    return stops.sort((a, b) => compareTreeOrder(stopElement(a), stopElement(b)));
  }

  // ---- queries -----------------------------------------------------------------------------------------------------------

  list(): AssistField[] {
    return this.fields;
  }

  stops(): StopRef[] {
    return this.stopList;
  }

  get(key: string): AssistField | undefined {
    return this.byKey.get(key);
  }

  lastSnapshot(): ObservationSnapshot | null {
    return this.snapshot;
  }

  /** A fresh observation without touching scan state (the page report needs the employer sections before the first scan ran). */
  peek(): ObservationSnapshot | null {
    try {
      return observe();
    } catch {
      return null;
    }
  }

  documentIdentity(): string {
    return this.documentId;
  }

  /** The assist field an element (focus target) belongs to, through the observer's identity cache. */
  fieldFor(el: Element | null): AssistField | null {
    if (!el || inUiHost(el)) return null;
    const cache = observerCache();
    if (!cache) return null;
    let cur: Element | null = el;
    for (let i = 0; cur && i < 4; i++, cur = cur.parentElement) {
      const id = cache.ids.get(cur);
      if (id === undefined) continue;
      const f = this.byNode.get(id);
      if (f) return f;
      if (i === 0 && this.isControl(cur)) return null;
    }
    return null;
  }

  /** Is this element a control the observer knows (or should know)? Unknown focus targets trigger a fast rescan. */
  isControl(el: Element): boolean {
    return el.matches?.("input,select,textarea,[contenteditable],[role=textbox],[role=combobox],[role=radio],[role=checkbox],[role=switch]") ?? false;
  }

  isKnownControl(el: Element): boolean {
    const cache = observerCache();
    return !!cache && cache.ids.has(el);
  }

  /** Re-resolves a node id to its element (used by fill for links that appear after the scan). */
  nodeById(id: number): HTMLElement | null {
    return nodeOf(observerCache(), id);
  }
}

export function stopElement(stop: StopRef): Element {
  return stop.kind === "assist" ? stop.field.el : stop.el;
}

function isChoice(f: AssistField): boolean {
  return f.kind === "select" || f.kind === "radio" || f.kind === "checkbox" || f.kind === "checkboxes" || f.kind === "combobox";
}

/**
 * The observer already drops controls under `[hidden]`, `[inert]` and `[aria-hidden=true]`, but `closest` stops at a shadow
 * boundary: an aria-hidden duplicate of a whole widget (its host is the hidden element) would still be scanned.
 */
function hiddenAbove(node: Element): boolean {
  const root = node.getRootNode();
  if (!root || root.nodeType !== 11) return false;
  return !!closestDeep((root as ShadowRoot).host, '[hidden],[inert],[aria-hidden="true"]');
}

function identitySignature(f: AssistField): string {
  const e = f.element;
  return [f.kind, e.tag, e.type, e.name, e.id, e.field_path, e.group, f.label, f.section].join("\u241f");
}

/**
 * A page that re-renders a control on blur (validation, framework re-mount) gives the new element a new observer id, so its
 * key changes. The state of the old field (insertion record, marker, undo, suggestion) belongs to the replacement: pair each
 * disappeared field (its element is detached) with the ONE new field of identical identity.
 */
function pairReplaced(added: AssistField[], removed: AssistField[]): { from: string; to: AssistField }[] {
  if (!added.length || !removed.length) return [];
  const count = (list: AssistField[]) => {
    const map = new Map<string, AssistField[]>();
    for (const f of list) {
      const sig = identitySignature(f);
      const bucket = map.get(sig);
      if (bucket) bucket.push(f);
      else map.set(sig, [f]);
    }
    return map;
  };
  const gone = count(removed.filter((f) => !f.el.isConnected));
  const fresh = count(added);
  const pairs: { from: string; to: AssistField }[] = [];
  for (const [sig, olds] of gone) {
    const news = fresh.get(sig);
    if (olds.length === 1 && news?.length === 1) pairs.push({ from: olds[0].key, to: news[0] });
  }
  return pairs;
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}
