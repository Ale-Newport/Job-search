// Keyboard model (design 7.4). Trusted events only, IME-safe. The decision to prevent the native Tab is ALWAYS taken synchronously
// from the prefetched cache: an `await` before preventDefault would let the browser move focus first (notably in Safari).
//
//   capture phase : Tab that INSERTS an answer (and Tab while an asynchronous fill runs), Enter, Esc, Alt+R / Alt+Z (the letter the key prints,
//                   whatever the layout), Alt+Shift+M (a switch: into the panel from a field, back to the field from inside the panel; from a
//                   frame it is relayed to the top frame), Alt+Shift+Z (takes back the whole last "Fill verified"), the numbered chip
//                   actions (Control+Option+1..7 on a Mac, Alt+Shift+1..7 elsewhere: chords that type no character). A chord is swallowed
//                   ONLY when it really took an action: a key that did nothing still types what it types.
//   bubble phase  : Tab / Shift+Tab with nothing to insert. The page's own handlers have run by then: a widget that owns Tab (a tag
//                   input, an autocomplete) calls preventDefault and the assistant stays out of it. Esc with the focus inside the panel
//                   (a control of the panel that uses Esc itself stops it first) gives the focus back to the form field the person was on
//
// Own traversal makes checkboxes/radios/selects reachable where Safari's native Tab skips them, skips what is already filled, and
// hands the key back to the browser whenever the browser's own Tab has a better claim: a link, a button or a plain control the
// person did not ask us to skip lies in between, a native date input still has segments, a code editor indents, nothing follows.

import type { AssistField } from "./state";
import type { FillOutcome } from "./fill";
import { isOn } from "./fill";
import { anchorElement, anchorRect, scrollWithinContainers } from "./anchors";
import type { StopRef } from "./scan";
import { stopElement } from "./scan";
import { isTabStop, orderOf, safariModel } from "./tabstops";
import { altLetter, altShift, chipActionNumber, closestDeep, composedTarget, deepActiveElement, inUiHost, isMacPlatform, isRendered, spawn } from "./util";

export type NextStop =
  | { kind: "assist"; field: AssistField }
  | { kind: "frame"; iframe: HTMLIFrameElement; frameKey: string }
  | { kind: "parent" }
  /** the browser's own Tab decides; `el` is the control it would land on, when known (an asynchronous insertion needs it) */
  | { kind: "native"; el?: HTMLElement }
  | { kind: "end" };

export interface TraversalEnv {
  stops: StopRef[];
  skipFilled: boolean;
  hasValue(field: AssistField): boolean;
  frameKeyFor(iframe: HTMLIFrameElement): string | null;
  isChild: boolean;
  /** child frames: may focus leave this frame through the parent? */
  hint: { next: boolean; prev: boolean } | null;
  /**
   * The first control the browser's Tab would stop on between `from` and `to` (null = the edge of the document), walking in `dir`,
   * ignoring what belongs to `from`, `to` and the `skipped` fields; null when nothing lies in between. Design 7.4 (amended): the
   * assistant walks assist fields itself, but never over a link, button or plain control the person did not ask it to skip.
   */
  tabStopBetween(from: AssistField | Element, to: AssistField | Element | null, dir: 1 | -1, skipped: AssistField[]): HTMLElement | null;
}

function isField(from: AssistField | Element): from is AssistField {
  return !(from instanceof Element);
}

function usableStop(stop: NextStop | undefined): stop is NextStop {
  if (!stop) return false;
  if (stop.kind === "assist") return stop.field.el.isConnected && isRendered(stop.field.anchorEl);
  if (stop.kind === "frame") return stop.iframe.isConnected && isRendered(stop.iframe);
  return true;
}

/**
 * Where a field (or a plain control) that is no longer part of the scan sat: the index of the first stop after it (dir 1) / the last
 * stop before it (dir -1). A widget may hide its input once a value is chosen (react-select without classNamePrefix), so the rescan after the fill no longer
 * lists the field the person is still standing on. Null when the field is gone from the page as well.
 */
function locate(stops: StopRef[], from: AssistField | Element, dir: 1 | -1): number | null {
  const anchor = isField(from) ? (from.el.isConnected ? from.el : from.anchorEl.isConnected ? from.anchorEl : null) : from.isConnected ? from : null;
  if (!anchor) return null;
  if (dir === 1) {
    const i = stops.findIndex((s) => orderOf(stopElement(s), anchor) > 0);
    return i < 0 ? stops.length : i;
  }
  for (let i = stops.length - 1; i >= 0; i--) if (orderOf(stopElement(stops[i]), anchor) < 0) return i;
  return -1;
}

/**
 * The next tab stop after `from`: an assist field, a known child frame, the parent frame, or "let the browser do it" (a control the
 * assistant must not skip lies in between, or nothing follows). `from` is an assist field or, for a key pressed on a control that is
 * none (Safari's walk), the element itself. `fallback` is what was planned BEFORE the field changed the page: it answers only when
 * `from` can no longer be placed in the scan (its element left the page).
 */
export function nextStop(env: TraversalEnv, from: AssistField | Element, dir: 1 | -1, fallback?: NextStop): NextStop {
  const found = isField(from) ? env.stops.findIndex((s) => s.kind === "assist" && s.field.key === from.key) : -1;
  let begin: number;
  if (found >= 0) {
    begin = found + dir;
  } else {
    const at = locate(env.stops, from, dir);
    if (at === null) return usableStop(fallback) ? fallback : { kind: "native" };
    begin = at;
  }
  const skipped: AssistField[] = [];
  for (let i = begin; i >= 0 && i < env.stops.length; i += dir) {
    const stop = env.stops[i];
    if (stop.kind === "assist") {
      const f = stop.field;
      if (!f.el.isConnected || !isRendered(f.anchorEl)) continue;
      if (dir === 1 && env.skipFilled && env.hasValue(f)) {
        skipped.push(f);
        continue;
      }
      const between = env.tabStopBetween(from, f, dir, skipped);
      return between ? { kind: "native", el: between } : { kind: "assist", field: f };
    }
    if (!isRendered(stop.el)) continue;
    const between = env.tabStopBetween(from, stop.el, dir, skipped);
    if (between) return { kind: "native", el: between };
    if (stop.kind === "native") return { kind: "native", el: stop.el as HTMLElement };
    const frameKey = env.frameKeyFor(stop.el);
    return frameKey ? { kind: "frame", iframe: stop.el, frameKey } : { kind: "native", el: stop.el };
  }
  // no assist stop is left in this document: a control after the last field (Save and Continue) is the browser's to reach
  const rest = env.tabStopBetween(from, null, dir, skipped);
  if (rest) return { kind: "native", el: rest };
  if (env.isChild && (dir === 1 ? env.hint?.next : env.hint?.prev)) return { kind: "parent" };
  return { kind: "end" };
}

/**
 * No assist field (and no frame that may hold one) follows `field` in this document: a Tab that does not land on another question is the
 * end of the form (FR-03). Already-filled fields the walk passes over on purpose do not count as "following".
 */
export function isLastAssistField(env: TraversalEnv, field: AssistField): boolean {
  const at = env.stops.findIndex((s) => s.kind === "assist" && s.field.key === field.key);
  if (at < 0) return false;
  for (let i = at + 1; i < env.stops.length; i++) {
    const stop = env.stops[i];
    if (stop.kind === "assist") {
      if (!stop.field.el.isConnected || !isRendered(stop.field.anchorEl)) continue;
      if (env.skipFilled && env.hasValue(stop.field)) continue;
      return false;
    }
    if (stop.kind === "frame" && isRendered(stop.el)) return false;
  }
  return true;
}

/** The element inside a field that should take focus (a radio group focuses its selected radio, else the first). */
export function focusTarget(field: AssistField): HTMLElement {
  if (field.kind === "radio" || field.kind === "checkboxes") {
    const on = field.members.find((m) => m.enabled && isOn(m.el, m.checked));
    return (on ?? field.members.find((m) => m.enabled) ?? field.members[0])?.el ?? field.el;
  }
  return field.focusEl;
}

export function fieldHasFocus(field: AssistField): boolean {
  const active = deepActiveElement();
  if (!active) return false;
  if (active === field.el || field.members.some((m) => m.el === active || m.proxy === active)) return true;
  return field.links.proxy === active || field.links.trigger === active || field.links.search === active;
}

/**
 * Elements that were given `tabindex="-1"` so that the assistant could stand on them (the visible drop area of a file input that cannot
 * take focus), with the attribute they had before (null = none). Handed back by `releaseFocusProxies` when the assistant is switched off.
 */
const FOCUS_PROXIES = new Map<HTMLElement, string | null>();
const MAX_FOCUS_PROXIES = 60;

function tryFocus(el: HTMLElement | null | undefined): boolean {
  if (!el) return false;
  try {
    el.focus({ preventScroll: true });
  } catch {
    return false;
  }
  return deepActiveElement() === el;
}

/**
 * Can this control take keyboard focus right now? No for `display: none`, `visibility: hidden` and inert content (Dropzone.js and
 * react-dropzone keep their real file input like that): the person reaches such a control through what they SEE of it.
 */
export function focusBlocked(el: HTMLElement): boolean {
  try {
    if (!el.isConnected || !el.getClientRects().length) return true;
    if (getComputedStyle(el).visibility !== "visible") return true;
    return !!closestDeep(el, "[inert]");
  } catch {
    return false;
  }
}

/** Focus a visible stand-in of the field (its drop area, its styled toggle): made focusable with `tabindex=-1` when it is not already. */
function focusProxy(el: HTMLElement | null | undefined): boolean {
  if (!el || !el.isConnected || inUiHost(el) || !isRendered(el)) return false;
  if (tryFocus(el)) return true;
  if (el.hasAttribute("tabindex")) return false; // focusable by its own rules and still refused: the page decides
  if (FOCUS_PROXIES.size >= MAX_FOCUS_PROXIES) releaseFocusProxies(1);
  el.setAttribute("tabindex", "-1");
  if (tryFocus(el)) {
    FOCUS_PROXIES.set(el, null);
    return true;
  }
  el.removeAttribute("tabindex");
  return false;
}

/** Take the `tabindex` attributes back (the page is exactly as it was when the assistant is off). `count`: only the oldest ones. */
export function releaseFocusProxies(count = Infinity): void {
  let left = count;
  for (const [el, original] of [...FOCUS_PROXIES]) {
    if (left-- <= 0) break;
    FOCUS_PROXIES.delete(el);
    try {
      if (el.getAttribute("tabindex") !== "-1") continue; // the page changed it meanwhile: it is the page's now
      if (original === null) el.removeAttribute("tabindex");
      else el.setAttribute("tabindex", original);
    } catch {
      /* a detached or hostile element */
    }
  }
}

/** A field nearer than this to the top of the window is brought into the middle of it. */
const FOCUS_MARGIN_PX = 90;
/** The room a chip needs under a field: its height and a margin. A field nearer than this to the bottom of the window is brought up. */
export const CHIP_ROOM_BELOW_PX = 170;

/** Moves focus to a field and scrolls it into view only when it is near an edge. True when focus really moved there. */
export function focusAssistField(field: AssistField): boolean {
  const target = focusTarget(field);
  if (!tryFocus(target) && !tryFocus(field.links.proxy) && !tryFocus(field.anchorEl)) {
    // The control itself cannot take focus (a file input that is visibility:hidden or display:none inside a drop area): enter the field
    // through what the person sees of it - the proxy of the member that would have been focused, else the field's own proxy.
    const member = field.members.find((m) => m.el === target);
    if (!focusProxy(member?.proxy) && !focusProxy(field.links.proxy)) return false;
  }
  try {
    // what the person sees: a hidden file input is scrolled to through its visible button or label
    // UXR-04: a field inside a dialog, a drawer or a scrolling form pane is scrolled into ITS container first (the browser's own Tab would
    // have), whatever the viewport says: the field may be well inside the viewport and still cut off by the box around it
    scrollWithinContainers(anchorElement(field), () => anchorRect(field));
    const r = anchorRect(field);
    // UXR-12: a field in the lower part of the window is brought up until a chip fits under it (the chip then need not lie on the value of the
    // field above or on the field's own label to stay off the next field). A window too short to give both leaves it at the centre.
    if (r.y < FOCUS_MARGIN_PX || r.y + r.h > window.innerHeight - CHIP_ROOM_BELOW_PX) {
      anchorElement(field).scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" as ScrollBehavior });
    }
  } catch {
    /* scrolling is a nicety */
  }
  return true;
}

/** Is the caret of a contenteditable inside a list item or a table cell (where Tab belongs to the editor)? */
export function caretInStructure(editor: HTMLElement): boolean {
  try {
    const root = editor.getRootNode() as Document | ShadowRoot;
    const selection = (root as unknown as { getSelection?: () => Selection | null }).getSelection?.() ?? window.getSelection();
    const node = selection?.anchorNode;
    const el = node instanceof Element ? node : node?.parentElement;
    if (!el || !editor.contains(el)) return false;
    return !!el.closest('li,td,th,[role="listitem"],[role="gridcell"],[role="cell"]');
  } catch {
    return false;
  }
}

/** The next stop as it is before the field changes the page (only choice widgets can drop out of the scan: a text box stays). */
function planFor(host: KeyboardHost, field: AssistField): NextStop | undefined {
  return field.kind === "text" || field.kind === "textarea" || field.kind === "editable" ? undefined : nextStop(host.traversal(), field, 1);
}

/** Code editors (CodeMirror, Monaco, Ace ...) indent with Tab: the key is theirs, whatever the assistant plans. */
const CODE_EDITOR = ".CodeMirror,.cm-editor,.monaco-editor,.ace_editor,.prism-editor-wrapper,.react-simple-code-editor,[data-language]";

/** Native date/time inputs have a Tab stop per segment (day, month, year): without an insertion the key belongs to them. */
export function isSegmentedInput(field: AssistField): boolean {
  return segmentedElement(field.el);
}

function segmentedElement(el: Element): boolean {
  return el instanceof HTMLInputElement && ["date", "time", "datetime-local", "month", "week"].includes(el.type);
}

/**
 * Does the browser's own Tab skip this field? Only meaningful in Safari's model (default settings: text fields only), where a text
 * field, a text area or an editor is where the browser lands by itself and a checkbox, a radio, a menu, a file input or a button-like
 * widget is not.
 */
function skippedByNativeTab(field: AssistField): boolean {
  return !isTabStop(focusTarget(field));
}

/** Does a widget own Tab here (so the assistant must not even plan an advance)? */
export function tabBelongsToWidget(target: Element): boolean {
  return !!closestDeep(target, CODE_EDITOR);
}

/** Nothing focused (or the page body): a widget that blurred its input after a selection (react-select on a "touch" device) left focus nowhere. */
function focusLost(): boolean {
  const active = deepActiveElement();
  return !active || active === document.body || active === document.documentElement;
}

export interface KeyboardHost {
  active(): boolean;
  paused(): boolean;
  /** an async fill (combobox / file) is running: key presses are swallowed so focus cannot leave under it */
  busy(): boolean;
  /** this frame may take over keys: runtime alive, connection usable, page not blocked, suggestions exist */
  intercepting(): boolean;
  autoAdvance(): boolean;
  fieldFor(el: Element): AssistField | null;
  popupOpen(field: AssistField, target: Element): boolean;
  /**
   * A popup the PAGE opened next to the field is on screen (the calendar of a date picker, a list under an input; `popups.ts`). Unlike
   * `popupOpen` the control does not say it is mid-selection: the key's second half belongs to the page, which closes its popup on a Tab it
   * sees. So the answer is still inserted, but the Tab is not swallowed.
   */
  popupNearby(field: AssistField): boolean;
  /** a control that is no assist field has a popup open / a widget of its own that handles Tab (Safari's walk leaves it alone) */
  plainWidgetOwnsTab(target: Element): boolean;
  /** synchronous cache decision for Tab */
  plan(field: AssistField): { insert: boolean; async: boolean };
  insertTab(field: AssistField): FillOutcome | Promise<FillOutcome>;
  /** called after an insertion changed the page, before the next stop is computed (conditional questions may have appeared) */
  afterInsert(field: AssistField): void;
  /** a chip with insert "explicit" is showing for this field and the value is insertable */
  explicitReady(field: AssistField): boolean;
  insertExplicit(field: AssistField): FillOutcome | Promise<FillOutcome>;
  chipShowing(field: AssistField): boolean;
  dismiss(field: AssistField): void;
  canRegenerate(field: AssistField): boolean;
  regenerate(field: AssistField): void;
  hasUndo(): boolean;
  undo(): void;
  focusPanel(): boolean;
  /** UXR-03: a chord of the numbered chip actions matched: run action `n` of the chip on screen. True ONLY when the chip really ran it. */
  chipAction(n: number): boolean;
  /** UXR-06: the answers of the last "Fill verified" can still be taken back as one / take them back */
  hasBatchUndo(): boolean;
  undoBatch(): void;
  /** UX-02: focus is inside the panel (Esc / Alt+Shift+M): give it back to the last form field the person was on. True when focus moved. */
  leavePanel(): boolean;
  /** FR-03: Tab from `field` found no further question in this document (it never blocks the key): the end-of-form review may show. */
  endOfForm(field: AssistField): void;
  traversal(): TraversalEnv;
  /** focus a field, hand off to a child frame, or to the parent. True when focus moved. */
  go(stop: NextStop, dir: 1 | -1): boolean;
  /** focus a control the browser's Tab would have reached (an asynchronous insertion swallowed the key, so the browser cannot). */
  focusNative(el: HTMLElement): boolean;
  /**
   * An asynchronous insertion starts (keys are swallowed while it runs) / ends. `endBusy` says whether its result still counts: a fill
   * that outlived the limit has been given up on (the keyboard was handed back, the field marked), so nothing may move focus for it later.
   */
  beginBusy(): number;
  endBusy(id: number): boolean;
}

export class KeyboardController {
  private installed = false;
  /** Tab events this controller left to a widget on purpose (popup open, code editor ...): the late phase must not take them over */
  private declined = new WeakSet<Event>();
  private readonly onKeyDown = (e: KeyboardEvent) => {
    try {
      this.handle(e);
    } catch {
      /* a bug here must never trap the user's keyboard: fall through to native behaviour */
    }
  };
  /**
   * The page gets the first word on a Tab the assistant does not insert for: a widget that owns Tab (a tag input, an autocomplete
   * that picks its highlighted option) calls preventDefault on its own, and then the assistant stays out of it. Still synchronous:
   * this runs inside the same key event, before the browser moves focus.
   */
  private readonly onKeyDownLate = (e: KeyboardEvent) => {
    try {
      this.handleLate(e);
    } catch {
      /* native behaviour */
    }
  };

  constructor(private readonly host: KeyboardHost) {}

  install(): void {
    if (this.installed) return;
    this.installed = true;
    window.addEventListener("keydown", this.onKeyDown, true);
    window.addEventListener("keydown", this.onKeyDownLate, false);
  }

  uninstall(): void {
    if (!this.installed) return;
    this.installed = false;
    window.removeEventListener("keydown", this.onKeyDown, true);
    window.removeEventListener("keydown", this.onKeyDownLate, false);
  }

  private swallow(e: KeyboardEvent): void {
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  private handle(e: KeyboardEvent): void {
    if (!e.isTrusted || e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    if (!this.host.active()) return;
    const target = composedTarget(e);
    if (!target) return;
    // the panel shortcut works whatever the pause state is, and it is a switch: from a form field it goes into the panel, from inside the
    // panel it goes back to the field the person was on (UX-02: the panel is the last thing of the page, a way back must exist). The key
    // the person pressed is the one printed on it (Alt+Shift+M on an AZERTY keyboard is the key that prints M), the physical key only
    // where the key prints no ASCII character (a Mac's Option+Shift+M prints "Â").
    if (altShift(e) && altLetter(e, "m")) {
      if (inUiHost(target) ? this.host.leavePanel() : this.host.focusPanel()) this.swallow(e);
      return;
    }
    // UXR-06: the keyboard way to "Undo all" (the toast's button is out of the Tab order by design): only while there is a batch to take back
    if (altShift(e) && altLetter(e, "z")) {
      if (!e.repeat && this.host.hasBatchUndo()) {
        this.swallow(e);
        this.host.undoBatch();
      }
      return;
    }
    if (inUiHost(target)) return;
    // UXR-03: the numbered buttons of the chip. A chord that types no character; swallowed only when the chip ran the action.
    const chipNumber = chipActionNumber(e, isMacPlatform());
    if (chipNumber) {
      if (!e.repeat && this.host.chipAction(chipNumber)) this.swallow(e);
      return;
    }
    if (this.host.paused() || !this.host.intercepting()) return;
    if (e.key === "Tab") return this.onTab(e, target);
    if (e.key === "Enter") return this.onEnter(e, target);
    if (e.key === "Escape") return this.onEscape(e, target);
    if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      if (altLetter(e, "r")) return this.onRegenerate(e, target);
      if (altLetter(e, "z") && this.host.hasUndo()) {
        this.swallow(e);
        this.host.undo();
      }
    }
  }

  /** Where the person's Tab goes once an insertion (if any) is done. */
  private advance(e: KeyboardEvent, field: AssistField, planned?: NextStop): void {
    const env = this.host.traversal();
    const stop = nextStop(env, field, 1, planned);
    if (stop.kind === "native" || stop.kind === "end") {
      // the browser's own Tab continues; when no question is left, this Tab ends the form (the review never blocks it)
      if (isLastAssistField(env, field)) this.host.endOfForm(field);
      return;
    }
    if (this.host.go(stop, 1)) this.swallow(e);
  }

  /** After an asynchronous insertion the key is long gone: reach what the browser's Tab would have reached. */
  private advanceLater(field: AssistField, planned?: NextStop): void {
    const env = this.host.traversal();
    const stop = nextStop(env, field, 1, planned);
    switch (stop.kind) {
      case "native":
        if (isLastAssistField(env, field)) this.host.endOfForm(field);
        if (stop.el) this.host.focusNative(stop.el);
        return;
      case "end":
        // nothing follows in this frame: the parent knows what comes after the <iframe> (a link, a button, the next field)
        if (env.isChild) this.host.go({ kind: "parent" }, 1);
        else this.host.endOfForm(field);
        return;
      default:
        this.host.go(stop, 1);
    }
  }

  private onTab(e: KeyboardEvent, target: Element): void {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const host = this.host;
    const field = host.fieldFor(target);
    if (!field) return; // not ours: the late phase may still walk (Safari's model), after the page had its say
    if (host.popupOpen(field, target) || tabBelongsToWidget(target)) {
      this.declined.add(e);
      return;
    }
    // in a rich-text editor Tab indents list items and moves between table cells: that is the editor's job, not "next field"
    if (!e.shiftKey && field.kind === "editable" && caretInStructure(field.el)) {
      this.declined.add(e);
      return;
    }
    if (host.busy()) {
      this.swallow(e);
      return;
    }
    if (e.shiftKey) return; // walking back never inserts: decided after the page had its say
    const plan = host.plan(field);
    if (!plan.insert) return; // idem
    const advance = host.autoAdvance();
    if (!plan.async && host.popupNearby(field)) {
      // A calendar (or a list) the page opened for this field is on screen: the widget closes it on the Tab it sees, and a Tab swallowed here
      // would leave it open over the NEXT field. The answer goes in now; the key is left to the page and the browser (focus follows the
      // browser's own order). A failed fill, or "Tab only inserts", keeps the person here as it always did.
      this.declined.add(e);
      const done = host.insertTab(field) as FillOutcome;
      // a field nobody can see (the calendar of another question lies over it) is left alone and the key goes on: it never traps the keyboard
      if (!done.ok && done.failure === "hidden") return;
      if (!done.ok || !advance) {
        this.swallow(e);
        return;
      }
      if (done.changed) host.afterInsert(field);
      return;
    }
    // where the field sits NOW: a widget may hide its input after the value is chosen, and the rescan then no longer lists it
    const planned = planFor(host, field);
    if (plan.async) {
      // custom comboboxes / files / choices finish later: keep focus here, swallow the key, advance when done
      this.swallow(e);
      const id = host.beginBusy();
      spawn(Promise.resolve(host.insertTab(field)).then(
        (result) => {
          if (!host.endBusy(id) || !result.ok || !advance) return;
          if (!fieldHasFocus(field) && !focusLost()) return; // the user clicked elsewhere meanwhile
          if (result.changed) host.afterInsert(field);
          this.advanceLater(field, planned);
        },
        () => void host.endBusy(id),
      ));
      return;
    }
    const result = host.insertTab(field) as FillOutcome;
    if (!result.ok || !advance) {
      // failed fill: stay so the user sees the reason; or auto-advance is off: Tab only inserts
      this.swallow(e);
      return;
    }
    if (result.changed) host.afterInsert(field);
    this.advance(e, field, planned);
  }

  /** Tab / Shift+Tab with nothing to insert: own traversal between assist fields, unless the page or the browser has a better claim. */
  private handleLate(e: KeyboardEvent): void {
    if (e.key === "Escape") return this.escapeFromPanel(e);
    if (e.key !== "Tab" || !e.isTrusted || e.defaultPrevented || e.isComposing || e.keyCode === 229 || this.declined.has(e)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const host = this.host;
    if (!host.active() || host.paused() || !host.intercepting() || host.busy()) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = host.fieldFor(target);
    if (!field) {
      this.walkFromPlainControl(e, target);
      return;
    }
    if (host.popupOpen(field, target) || tabBelongsToWidget(target) || host.popupNearby(field)) return;
    // the page's own handler moved focus (a custom focus order): it has handled this key
    if (!fieldHasFocus(field) && deepActiveElement() !== target) return;
    // a date/time input has a Tab stop per segment; only an insertion (done in the first phase) lets the assistant move on
    if (isSegmentedInput(field)) return;
    if (!e.shiftKey && field.kind === "editable" && caretInStructure(field.el)) return;
    const dir: 1 | -1 = e.shiftKey ? -1 : 1;
    const env = host.traversal();
    const stop = nextStop(env, field, dir);
    if (stop.kind === "native" || stop.kind === "end") {
      if (dir === 1 && isLastAssistField(env, field)) host.endOfForm(field);
      return;
    }
    if (host.go(stop, dir)) this.swallow(e);
  }

  /**
   * Esc with the focus inside the panel gives the focus back to the form field the person was on (UX-02). It runs in the bubble phase: a
   * control of the panel that uses Esc itself (the research form, a menu) stops or cancels the event and keeps it.
   */
  private escapeFromPanel(e: KeyboardEvent): void {
    if (!e.isTrusted || e.defaultPrevented || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (!this.host.active()) return;
    const target = composedTarget(e);
    if (!target || !inUiHost(target)) return;
    if (this.host.leavePanel()) this.swallow(e);
  }

  /**
   * Safari only (its own Tab stops on text fields and skips checkboxes, radios and menus): the person is on a control that is no assist
   * field (a read-only box, a password field, a link they clicked) and the next question is one the browser's Tab would jump over. The
   * assistant walks there itself, exactly as it does from one question to the next. Chrome's Tab reaches every control: nothing to do.
   */
  private walkFromPlainControl(e: KeyboardEvent, target: Element): void {
    if (!safariModel()) return;
    const host = this.host;
    if (target === document.body || target === document.documentElement || !(target instanceof HTMLElement)) return;
    // controls that keep Tab for themselves: editors, native date/time segments, an open popup, a page widget that owns the key
    if (target.isContentEditable || segmentedElement(target) || tabBelongsToWidget(target) || host.plainWidgetOwnsTab(target)) return;
    const dir: 1 | -1 = e.shiftKey ? -1 : 1;
    const stop = nextStop(host.traversal(), target, dir);
    if (stop.kind !== "assist" || !skippedByNativeTab(stop.field)) return; // the browser gets there by itself (or has a stop of its own first)
    if (host.go(stop, dir)) this.swallow(e);
  }

  private onEnter(e: KeyboardEvent, target: Element): void {
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    const host = this.host;
    const field = host.fieldFor(target);
    if (!field || field.kind === "textarea" || field.kind === "editable") return;
    if (host.popupOpen(field, target) || !host.explicitReady(field)) return;
    this.swallow(e);
    if (host.busy()) return;
    const advance = host.autoAdvance();
    const planned = planFor(host, field);
    const finish = (result: FillOutcome) => {
      if (!result.ok || !advance || (!fieldHasFocus(field) && !focusLost())) return;
      if (result.changed) host.afterInsert(field);
      // Enter only ever goes on to another question: it never carries focus onto a button, where the next Enter would press it
      // (a Submit button among them): "submission is always the user's own click"
      const stop = nextStop(host.traversal(), field, 1, planned);
      if (stop.kind === "assist" || stop.kind === "frame" || stop.kind === "parent") host.go(stop, 1);
    };
    const result = host.insertExplicit(field);
    if (result instanceof Promise) {
      const id = host.beginBusy();
      spawn(result.then((r) => { if (host.endBusy(id)) finish(r); }, () => void host.endBusy(id)));
    } else {
      finish(result);
    }
  }

  private onEscape(e: KeyboardEvent, target: Element): void {
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    const field = this.host.fieldFor(target);
    if (!field || this.host.popupOpen(field, target) || !this.host.chipShowing(field)) return;
    this.swallow(e);
    this.host.dismiss(field);
  }

  private onRegenerate(e: KeyboardEvent, target: Element): void {
    const field = this.host.fieldFor(target);
    if (!field || !this.host.canRegenerate(field)) return;
    this.swallow(e);
    this.host.regenerate(field);
  }
}
