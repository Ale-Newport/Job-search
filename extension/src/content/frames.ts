// Cross-frame coordination through the worker's FramePayload relay. Every frame scans, caches and renders its own
// chips; the TOP frame owns the session, the poll loop and the panel. Frames never address each other directly: the
// worker relays {payload} and stamps it with the sender's frame id, which becomes the frame key `f<frameId>`.
//
//   child -> top : hello (url), fields (digests), focused / progress, focus-after-frame / focus-before-frame, confirmation,
//                  focus-panel (Alt+Shift+M pressed in a frame), undo-all-request (Alt+Shift+Z pressed in a frame)
//   top -> child : session, settings, event, frame-hints, focus-first / focus-last / focus-field, fill-verified, deactivate,
//                  handoff-ack (the answer to focus-after/before-frame: "I moved focus" / "there is nothing to move it to"),
//                  panel-state (a panel the keyboard can enter is on screen)
//
// A frame nobody can see (transparent, tiny, covered, off the page: visibility.ts `frameIsHumanVisible`) is never given the session: it asks
// Meridian nothing (no suggestions, no drafts paid for), is never walked or handed focus into, never filled and never listed (SEC-01).
//
// A hand-off out of a frame is acknowledged by the top frame instead of being timed: a timer cannot tell "nobody answered" from "focus
// legitimately came back" (a quick Shift+Tab) or "the relay is slow" (a loaded machine). The fallback below only concludes that the
// relay failed when no answer came for a long time AND this frame never lost the focus in between.

import type { FieldDigest, FramePayload } from "../shared/messages";
import type { ExtensionSettings, PollEvent } from "../shared/protocol";
import type { AssistField } from "./state";
import type { StopRef } from "./scan";
import { stopElement } from "./scan";
import { after, deepActiveElement, isRendered, sendBg } from "./util";
import { frameIsHumanVisible } from "./visibility";

/** How long a hand-off may stay unanswered before the frame concludes the relay failed (a slow worker on a loaded machine is not a failure). */
export const HANDOFF_FALLBACK_MS = 4000;

export interface FramesHost {
  readonly frameKey: string;
  readonly isTop: boolean;
  // ---- top frame
  currentSession(): { sessionId: string; knowledgeVersion: string } | null;
  currentSettings(): ExtensionSettings;
  /** a child frame announced itself (the cross-origin note may no longer apply) */
  onChildHello(frameKey: string): void;
  onChildDigests(frameKey: string, items: FieldDigest[]): void;
  onChildFocus(frameKey: string, key: string | null): void;
  onConfirmation(evidence: string): void;
  /** focus the assist field `field` of this frame (used when a child hands focus back) */
  focusField(field: AssistField): boolean;
  stops(): StopRef[];
  /**
   * The control the browser's Tab would stop on between two elements of this document (to null = the edge), walking in `dir`, ignoring
   * what belongs to the `skipped` fields (already-filled ones the walk passes over on purpose).
   */
  tabStopBetween(from: Element, to: Element | null, dir: 1 | -1, skipped?: AssistField[]): HTMLElement | null;
  /** `skip_filled` is on and this field already holds a value: own traversal passes over it. */
  skipField(field: AssistField): boolean;
  /**
   * EVERY <iframe> of this document (open shadow roots included), the ones a person cannot see as well: `stops()` lists only the frames
   * worth walking into, but a frame nobody can see still has a content script that answers, and it must be placed on its <iframe> to be
   * recognised as hidden (SEC-01).
   */
  iframes(): HTMLIFrameElement[];
  /** a child frame found the last question of the form (nothing follows it in the page): the end-of-form review may show */
  onChildEnd(): void;
  /** top frame: is the posting "already applied" and unanswered (no frame inserts on Tab)? */
  currentDuplicate(): boolean;
  // ---- child frame
  onSession(sessionId: string, knowledgeVersion: string): void;
  onSettings(settings: ExtensionSettings): void;
  /** the top frame's "already applied" state changed (it blocks Tab insertion in every frame until the person chooses to continue) */
  onDuplicate(blocked: boolean): void;
  /** the top frame says whether the <iframe> this frame lives in can be seen by a person (false: nothing here is offered or inserted) */
  onFrameVisible(visible: boolean): void;
  /** top frame asked for "Undo all": take back what this frame's "Fill verified" inserted */
  undoAll(): void;
  /** top frame: a child frame asks for "Undo all" (its keyboard shortcut was used): take back everything, here and in every frame */
  onUndoAllRequest(): void;
  /** top frame: is a panel the keyboard can enter on screen? */
  currentPanel(): boolean;
  /** child frame: the top frame says whether Alt+Shift+M can be relayed to a panel */
  onPanelState(available: boolean): void;
  /** top frame: Alt+Shift+M was pressed inside the frame `frameKey`: move the keyboard into the panel (Esc brings it back to that frame) */
  onChildFocusPanel(frameKey: string): void;
  onEvent(event: PollEvent): void;
  focusEdge(which: "first" | "last"): boolean;
  focusByKey(key: string): void;
  /** `keys`: fill only these fields (the ones the confirmation row of "Fill verified" listed); absent = every fillable field */
  fillVerified(keys?: string[]): void;
  deactivate(): void;
}

interface Child {
  frameKey: string;
  frameId: number;
  /**
   * A frame without an address of its own (srcdoc, about:blank) cannot be placed on its <iframe> by URL. While the document holds an
   * <iframe> of that kind that a person cannot see, such a frame is taken for that one (SEC-01): not offered, not filled, not counted.
   */
  suspect?: boolean;
  /** the directed "you are hidden" hint was sent to a suspect frame (once) */
  suspectTold?: boolean;
  /** the frame was not given the session because nobody can see it: it gets it the moment that changes (SEC-01) */
  held?: boolean;
  /** the URL the frame announced first: it equals the <iframe src>, later in-frame navigation must not break the mapping */
  url: string;
  items: FieldDigest[];
  seq: number;
  el?: HTMLIFrameElement;
}

export function frameIdOf(frameKey: string): number | null {
  if (!/^f\d+$/.test(frameKey)) return null;
  const id = Number(frameKey.slice(1));
  return Number.isSafeInteger(id) ? id : null;
}

function cleanUrl(url: string): string {
  return url.replace(/#.*$/, "");
}

function focusElement(el: HTMLElement | null | undefined): boolean {
  if (!el) return false;
  try {
    el.focus({ preventScroll: false });
  } catch {
    return false;
  }
  return deepActiveElement() === el || document.activeElement === el;
}

interface Handoff { dir: 1 | -1; timer: ReturnType<typeof setTimeout>; left: boolean }

export class FrameCoordinator {
  private children = new Map<string, Child>();
  private iframeMap = new Map<string, HTMLIFrameElement>();
  private hintCache: FramePayload & { k: "frame-hints" } | null = null;
  /** child frames: can focus leave through the parent? (null = unknown: stay native) */
  hint: { next: boolean; prev: boolean } | null = null;
  private seq = 0;
  /** the hand-off out of this (child) frame that has not been answered yet */
  private handoff: Handoff | null = null;
  private readonly onWindowBlur = () => {
    if (this.handoff) this.handoff.left = true;
  };

  constructor(private readonly host: FramesHost, private readonly frameId: number) {}

  // ---- sending ---------------------------------------------------------------------------------------------------------

  private send(to: "top" | "all" | { frameId: number }, payload: FramePayload): void {
    void sendBg<"frame:relay">({ t: "frame:relay", to, payload }, 8000);
  }

  sendHello(): void {
    if (!this.host.isTop) this.send("top", { k: "hello", frameKey: this.host.frameKey, url: location.href });
  }

  sendDigests(items: FieldDigest[]): void {
    if (!this.host.isTop) this.send("top", { k: "fields", frameKey: this.host.frameKey, items });
  }

  sendFocused(key: string | null): void {
    if (this.host.isTop) return;
    if (key) this.send("top", { k: "focused", frameKey: this.host.frameKey, key });
    else this.send("top", { k: "progress", frameKey: this.host.frameKey, currentKey: null });
  }

  sendConfirmation(evidence: string): void {
    if (!this.host.isTop) this.send("top", { k: "confirmation", evidence });
  }

  broadcastSession(sessionId: string, knowledgeVersion: string): void {
    if (!this.host.isTop) return;
    // Never to "all": a frame that has not said hello yet might be one nobody can see. A frame that says hello later gets the session then
    // (and says hello until it has one); a frame nobody can see gets none at all (it would ask Meridian about fields no person is looking
    // at, drafts included) until it becomes visible.
    const hidden = this.hiddenChildKeys();
    for (const child of this.children.values()) {
      if (hidden.has(child.frameKey)) child.held = true;
      else this.send({ frameId: child.frameId }, { k: "session", sessionId, knowledgeVersion });
    }
  }

  broadcastSettings(settings: ExtensionSettings): void {
    if (this.host.isTop) this.send("all", { k: "settings", settings });
  }

  /** Route a poll event to the frame that owns the suggestion, or to everybody for global events. */
  routeEvent(event: PollEvent): boolean {
    if (!this.host.isTop) return false;
    if (event.type === "suggestion") {
      const key = event.suggestion.frame_key || "top";
      if (key === "top") return false;
      const id = frameIdOf(key);
      if (id !== null) this.send({ frameId: id }, { k: "event", event });
      return true;
    }
    if (event.type === "knowledge") this.send("all", { k: "event", event });
    return false;
  }

  /**
   * "Fill verified" goes to every frame a person can see, one by one. A frame behind an invisible, tiny, off-page or covered <iframe> never
   * hears it (SEC-01); a frame the top frame cannot place on any <iframe> of its own (nested deeper, redirected) is not judged here.
   */
  fillVerifiedInChildren(keys?: string[]): void {
    if (!this.host.isTop) return;
    const hidden = this.hiddenChildKeys();
    for (const child of this.children.values()) {
      if (hidden.has(child.frameKey)) continue;
      if (!keys) {
        this.send({ frameId: child.frameId }, { k: "fill-verified" });
        continue;
      }
      // the confirmation row listed these fields: a frame fills exactly the ones of its own that were listed
      const own = keys.filter((key) => key.startsWith(`${child.frameKey}:`));
      if (own.length) this.send({ frameId: child.frameId }, { k: "fill-verified", keys: own });
    }
  }

  undoAllInChildren(): void {
    if (this.host.isTop && this.children.size) this.send("all", { k: "undo-all" });
  }

  /** Child frame: Alt+Shift+M was pressed here and the panel is in the top frame. */
  sendFocusPanel(): void {
    if (!this.host.isTop) this.send("top", { k: "focus-panel", frameKey: this.host.frameKey });
  }

  /** Child frame: Alt+Shift+Z was pressed here; the top frame takes everything back (its own frame, this one and the others). */
  sendUndoAllRequest(): void {
    if (!this.host.isTop) this.send("top", { k: "undo-all-request" });
  }

  /** Top frame: the panel appeared or went away: the frames learn whether Alt+Shift+M has anywhere to go. */
  broadcastPanel(available: boolean): void {
    if (this.host.isTop && this.children.size) this.send("all", { k: "panel-state", available });
  }

  broadcastDuplicate(blocked: boolean): void {
    if (this.host.isTop && this.children.size) this.send("all", { k: "duplicate", blocked });
  }

  sendEndOfForm(): void {
    if (!this.host.isTop) this.send("top", { k: "end-of-form" });
  }

  deactivateChildren(): void {
    if (this.host.isTop) this.send("all", { k: "deactivate" });
  }

  focusChildField(key: string): boolean {
    const frameKey = key.split(":")[0];
    const id = frameIdOf(frameKey);
    if (id === null) return false;
    if (this.hiddenChildKeys().has(frameKey)) return false; // nobody can see that frame: the keyboard is never sent into it (SEC-01)
    const iframe = this.iframeFor(frameKey);
    try {
      iframe?.focus({ preventScroll: false });
    } catch {
      /* the child focuses its own field right after */
    }
    this.send({ frameId: id }, { k: "focus-field", key });
    return true;
  }

  // ---- frame <-> element mapping (top frame) ---------------------------------------------------------------------------------

  childList(): Child[] {
    return [...this.children.values()];
  }

  allDigests(): FieldDigest[] {
    return [...this.children.values()].flatMap((c) => c.items);
  }

  private rebuildMap(): void {
    this.iframeMap.clear();
    const walkable = new Set(this.host.stops().filter((s): s is Extract<StopRef, { kind: "frame" }> => s.kind === "frame").map((s) => s.el));
    // every iframe takes part in the URL match (a hidden frame is placed on its element to be known as hidden) ...
    const iframes = [...new Set([...this.host.iframes(), ...walkable])];
    const used = new Set<HTMLIFrameElement>();
    const children = [...this.children.values()].sort((a, b) => a.seq - b.seq);
    const unmapped: Child[] = [];
    for (const child of children) {
      child.suspect = false;
      // a frame that was mapped before keeps its element while that element is still part of the page
      const remembered = child.el && child.el.isConnected && iframes.includes(child.el) && !used.has(child.el) ? child.el : undefined;
      const match = remembered ?? iframes.find((f) => !used.has(f) && !!f.src && cleanUrl(f.src) === cleanUrl(child.url));
      if (match) {
        used.add(match);
        child.el = match;
        this.iframeMap.set(child.frameKey, match);
      } else {
        unmapped.push(child);
      }
    }
    // ... but only the frames worth walking into are paired by elimination: a frame that redirected away from its src takes the only
    // leftover <iframe> (an unrelated hidden frame beside it must not stop that)
    const free = [...walkable].filter((f) => !used.has(f));
    let placed: Child | null = null;
    if (unmapped.length === 1 && free.length === 1) {
      placed = unmapped[0];
      placed.el = free[0];
      this.iframeMap.set(placed.frameKey, free[0]);
    }
    // a frame that is still nowhere and has no address (srcdoc / about:blank): is a frame nobody can see waiting for it?
    for (const child of unmapped) {
      if (child === placed || !/^about:/i.test(child.url)) continue;
      child.suspect = iframes.some((f) => !used.has(f) && (!f.getAttribute("src") || f.hasAttribute("srcdoc")) && !frameIsHumanVisible(f));
    }
  }

  /** The frames (keys) a person cannot see: their <iframe> is invisible, tiny, covered or off the page, or they are taken for such a frame. */
  hiddenChildKeys(): Set<string> {
    const keys = new Set<string>();
    if (!this.host.isTop) return keys;
    this.rebuildMap();
    for (const child of this.children.values()) {
      if (child.suspect || (child.el && !frameIsHumanVisible(child.el))) keys.add(child.frameKey);
    }
    return keys;
  }

  /** Is the frame `frameKey` one a person cannot see (see `hiddenChildKeys`)? */
  private isHidden(frameKey: string): boolean {
    return this.hiddenChildKeys().has(frameKey);
  }

  /** The same for the frame a relayed message came from (a message that names no frame key). */
  private isHiddenSender(frameId: number): boolean {
    const child = [...this.children.values()].find((c) => c.frameId === frameId);
    return !!child && this.isHidden(child.frameKey);
  }

  /** Forget frames whose <iframe> left the page (their digests would otherwise stay in the panel forever). */
  prune(): void {
    if (!this.host.isTop) return;
    this.rebuildMap();
    for (const child of [...this.children.values()]) {
      if (child.el && !child.el.isConnected) {
        this.children.delete(child.frameKey);
        this.host.onChildDigests(child.frameKey, []);
      }
    }
  }

  iframeFor(frameKey: string): HTMLIFrameElement | null {
    this.rebuildMap();
    return this.iframeMap.get(frameKey) ?? null;
  }

  frameKeyFor(iframe: HTMLIFrameElement): string | null {
    this.rebuildMap();
    for (const [key, el] of this.iframeMap) if (el === iframe) return key;
    return null;
  }

  /** Top -> child: park focus on the <iframe> and ask the child to take it. */
  handoffInto(iframe: HTMLIFrameElement, frameKey: string, dir: 1 | -1): boolean {
    const id = frameIdOf(frameKey);
    if (id === null || !frameIsHumanVisible(iframe) || this.hiddenChildKeys().has(frameKey)) return false; // SEC-01: never into a frame nobody can see
    try {
      iframe.focus({ preventScroll: true });
    } catch {
      return false;
    }
    this.send({ frameId: id }, { k: dir === 1 ? "focus-first" : "focus-last", frameKey });
    return true;
  }

  /** Child -> top at the end/start of its fields. The answer is a `handoff-ack` (see the header). */
  handoffOut(dir: 1 | -1): boolean {
    if (this.host.isTop) return false;
    this.send("top", { k: dir === 1 ? "focus-after-frame" : "focus-before-frame", frameKey: this.host.frameKey });
    this.armHandoff(dir);
    return true;
  }

  private armHandoff(dir: 1 | -1): void {
    this.cancelHandoff();
    const state: Handoff = { dir, left: false, timer: after(() => this.handoffTimedOut(state), HANDOFF_FALLBACK_MS) };
    this.handoff = state;
    window.addEventListener("blur", this.onWindowBlur);
  }

  private cancelHandoff(): void {
    if (this.handoff) clearTimeout(this.handoff.timer);
    this.handoff = null;
    window.removeEventListener("blur", this.onWindowBlur);
  }

  /** No answer for a long time. Only a frame that still holds the focus it never gave up has evidence that nobody moved it. */
  private handoffTimedOut(state: Handoff): void {
    if (this.handoff !== state) return;
    this.cancelHandoff();
    if (!state.left && document.hasFocus() && this.hint) this.hint = { ...this.hint, [state.dir === 1 ? "next" : "prev"]: false };
  }

  private acknowledged(dir: 1 | -1, moved: boolean): void {
    if (this.handoff && this.handoff.dir === dir) this.cancelHandoff();
    // the parent has nothing to take the focus: from here on the browser's own Tab leaves the frame
    if (!moved && this.hint) this.hint = { ...this.hint, [dir === 1 ? "next" : "prev"]: false };
  }

  /** Stop listening and waiting (the assistant was switched off). */
  dispose(): void {
    this.cancelHandoff();
  }

  /** Top frame: after an iframe was left, which field/element takes focus next? Always answers the child (`handoff-ack`). */
  private continueAfter(frameKey: string, dir: 1 | -1): void {
    const child = this.children.get(frameKey);
    const answer = (moved: boolean): void => {
      if (child) this.send({ frameId: child.frameId }, { k: "handoff-ack", frameKey, dir, moved });
    };
    const iframe = this.iframeFor(frameKey);
    if (!iframe) {
      answer(false);
      return;
    }
    const stops = this.host.stops();
    const idx = stops.findIndex((s) => s.kind === "frame" && s.el === iframe);
    const skipped: AssistField[] = [];
    if (idx >= 0) {
      for (let i = idx + dir; i >= 0 && i < stops.length; i += dir) {
        const stop = stops[i];
        if (stop.kind === "assist") {
          if (!stop.field.el.isConnected || !isRendered(stop.field.anchorEl)) continue;
          if (dir === 1 && this.host.skipField(stop.field)) {
            skipped.push(stop.field);
            continue;
          }
          // a control between the frame and that field is where the browser's own Tab would go: never skip it
          const between = this.host.tabStopBetween(iframe, stop.field.el, dir, skipped);
          if (between) {
            answer(focusElement(between));
            return;
          }
          if (this.host.focusField(stop.field)) {
            answer(true);
            return;
          }
          continue;
        }
        if (stop.kind === "frame") {
          // a frame nobody can see is never handed the focus: for the assistant it is a plain tab stop of the browser (SEC-01)
          const key = frameIsHumanVisible(stop.el) ? this.frameKeyFor(stop.el) : null;
          const between = this.host.tabStopBetween(iframe, stop.el, dir, skipped);
          if (between) {
            answer(focusElement(between));
            return;
          }
          if (key && this.handoffInto(stop.el, key, dir)) {
            answer(true);
            return;
          }
        }
        break; // a native stop: emulate Tab below
      }
    }
    answer(focusElement(this.host.tabStopBetween(iframe, null, dir, skipped)));
  }

  /**
   * Top frame: tell every child whether focus can leave it (the parent owns the next Tab: a further assist stop with nothing natively
   * tabbable before it) and whether a person can see the <iframe> it lives in (SEC-01). A frame nobody can see is no stop of its own, so it
   * is listed after the others with no way out; a frame the top frame can only suspect to be hidden is told so directly.
   */
  publishHints(): void {
    if (!this.host.isTop) return;
    const stops = this.host.stops();
    const frames = stops.map((s, i) => ({ s, i })).filter((x) => x.s.kind === "frame");
    const hints: { src: string; next: boolean; prev: boolean; visible?: boolean }[] = frames.map(({ s, i }) => {
      const el = stopElement(s) as HTMLIFrameElement;
      const has = (from: number, dir: 1 | -1): boolean => {
        for (let k = from + dir; k >= 0 && k < stops.length; k += dir) {
          const stop = stops[k];
          if (stop.kind !== "assist" && stop.kind !== "frame") continue;
          // a link or button between the frame and that stop is the browser's: the child lets its own Tab leave the frame
          return !this.host.tabStopBetween(el, stopElement(stop), dir);
        }
        return false;
      };
      return { src: cleanUrl(el.src || ""), next: has(i, 1), prev: has(i, -1), visible: frameIsHumanVisible(el) };
    });
    const listed = new Set(frames.map(({ s }) => stopElement(s)));
    for (const el of this.host.iframes()) {
      if (!listed.has(el)) hints.push({ src: cleanUrl(el.src || ""), next: false, prev: false, visible: frameIsHumanVisible(el) });
    }
    const payload: FramePayload = { k: "frame-hints", hints };
    const signature = JSON.stringify(hints);
    if (!this.hintCache || JSON.stringify((this.hintCache as { hints: unknown }).hints) !== signature) {
      this.hintCache = payload as FramePayload & { k: "frame-hints" };
      if (hints.length) this.send("all", payload);
    }
    const hiddenNow = this.hiddenChildKeys(); // places the children and finds the suspects
    const session = this.host.currentSession();
    for (const child of this.children.values()) {
      // a frame that was held back because nobody could see it, and now can, joins the session
      if (child.held && !hiddenNow.has(child.frameKey) && session) {
        child.held = false;
        this.send({ frameId: child.frameId }, { k: "session", sessionId: session.sessionId, knowledgeVersion: session.knowledgeVersion });
      }
      if (child.suspect && !child.suspectTold) {
        child.suspectTold = true;
        this.send({ frameId: child.frameId }, { k: "frame-hints", hints: [{ src: cleanUrl(child.url), next: false, prev: false, visible: false }] });
      } else if (!child.suspect) {
        child.suspectTold = false;
      }
    }
  }

  // ---- receiving ---------------------------------------------------------------------------------------------------------

  handle(payload: FramePayload, fromFrameId: number): void {
    if (fromFrameId === this.frameId && payload.k !== "session") return; // own broadcast echoed back
    try {
      this.dispatch(payload, fromFrameId);
    } catch {
      /* a malformed relay payload must never break the page */
    }
  }

  private dispatch(payload: FramePayload, fromFrameId: number): void {
    const host = this.host;
    switch (payload.k) {
      // ---- top frame receives ----
      case "hello": {
        if (!host.isTop) return;
        const key = payload.frameKey;
        const child: Child = this.children.get(key) ?? { frameKey: key, frameId: fromFrameId, url: payload.url, items: [], seq: ++this.seq };
        child.frameId = fromFrameId;
        this.children.set(key, child);
        const session = host.currentSession();
        // a frame nobody can see is not given the session (see the header); it gets it when it becomes visible (`publishHints`)
        const hidden = this.hiddenChildKeys().has(key);
        child.held = hidden;
        if (session && !hidden) this.send({ frameId: fromFrameId }, { k: "session", sessionId: session.sessionId, knowledgeVersion: session.knowledgeVersion });
        this.send({ frameId: fromFrameId }, { k: "settings", settings: host.currentSettings() });
        if (host.currentDuplicate()) this.send({ frameId: fromFrameId }, { k: "duplicate", blocked: true });
        this.send({ frameId: fromFrameId }, { k: "panel-state", available: host.currentPanel() });
        this.hintCache = null;
        this.publishHints();
        host.onChildHello(key);
        return;
      }
      case "fields": {
        if (!host.isTop) return;
        const child = this.children.get(payload.frameKey) ?? { frameKey: payload.frameKey, frameId: fromFrameId, url: "", items: [], seq: ++this.seq };
        child.items = payload.items;
        this.children.set(payload.frameKey, child);
        // a frame nobody can see offers the panel nothing (and cannot wake an assistant that is waiting for a form): its fields are as hidden as it is
        host.onChildDigests(payload.frameKey, this.isHidden(payload.frameKey) ? [] : payload.items);
        return;
      }
      case "focused":
        if (host.isTop && !this.isHidden(payload.frameKey)) host.onChildFocus(payload.frameKey, payload.key);
        return;
      case "progress":
        if (host.isTop && !this.isHidden(payload.frameKey)) host.onChildFocus(payload.frameKey, payload.currentKey);
        return;
      case "focus-after-frame":
        if (host.isTop) this.continueAfter(payload.frameKey, 1);
        return;
      case "focus-before-frame":
        if (host.isTop) this.continueAfter(payload.frameKey, -1);
        return;
      case "confirmation":
        if (host.isTop && !this.isHiddenSender(fromFrameId)) host.onConfirmation(payload.evidence);
        return;
      case "end-of-form":
        if (host.isTop && !this.isHiddenSender(fromFrameId)) host.onChildEnd();
        return;
      case "focus-panel":
        if (host.isTop) host.onChildFocusPanel(payload.frameKey);
        return;
      case "undo-all-request":
        if (host.isTop) host.onUndoAllRequest();
        return;
      // ---- child frames receive ----
      case "session":
        if (!host.isTop) host.onSession(payload.sessionId, payload.knowledgeVersion);
        return;
      case "settings":
        if (!host.isTop) host.onSettings(payload.settings);
        return;
      case "duplicate":
        if (!host.isTop) host.onDuplicate(payload.blocked);
        return;
      case "undo-all":
        if (!host.isTop) host.undoAll();
        return;
      case "panel-state":
        if (!host.isTop) host.onPanelState(payload.available);
        return;
      case "event":
        if (!host.isTop) host.onEvent(payload.event);
        return;
      case "focus-first":
      case "focus-last": {
        if (host.isTop || payload.frameKey !== host.frameKey) return;
        const which = payload.k === "focus-first" ? "first" : "last";
        if (!host.focusEdge(which)) this.handoffOut(which === "first" ? 1 : -1);
        return;
      }
      case "focus-field":
        if (!host.isTop) host.focusByKey(payload.key);
        return;
      case "fill-verified":
        if (!host.isTop) host.fillVerified(payload.keys);
        return;
      case "deactivate":
        if (!host.isTop) host.deactivate();
        return;
      case "handoff-ack":
        if (!host.isTop && payload.frameKey === host.frameKey) this.acknowledged(payload.dir, payload.moved);
        return;
      case "frame-hints": {
        if (host.isTop) return;
        const mine = cleanUrl(location.href);
        const exact = payload.hints.filter((h) => h.src === mine);
        const chosen = exact.length ? exact[0] : payload.hints.length === 1 ? payload.hints[0] : null;
        this.hint = chosen ? { next: chosen.next, prev: chosen.prev } : null;
        // the top frame judged the <iframe> this frame lives in (SEC-01); no hint for this frame means it could not tell: unchanged
        if (chosen && typeof chosen.visible === "boolean") host.onFrameVisible(chosen.visible);
        return;
      }
      default:
        return;
    }
  }
}
