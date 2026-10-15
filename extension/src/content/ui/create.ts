// createUI(): the visual layer of the assistant (field chip, panel, toasts, prompts) in one closed shadow root.
//
// Privacy and isolation rules enforced here:
//  - the host is `all: initial !important`, top-layer when possible and never part of the page's layout;
//  - the shadow root is CLOSED in production builds (open only for __SHADOW__ === "open" test builds);
//  - every string is rendered through text nodes, every click handler requires `isTrusted`;
//  - nothing inside the chip is focusable by Tab; the panel and prompts sit at the end of the document.

import type { Language } from "../../shared/protocol";
import { htmlLang, setLocale, t } from "../i18n";
import { UI_HOST_ATTRIBUTE, type AnnounceView, type AssistUI, type ChipView, type FieldMark, type PanelView, type SaveAskView, type ToastView, type TrackAskView, type UIAction } from "../ui-api";
import { CHIP_KEY_COUNT, chipSummary, createChip, isReady, type ChipController } from "./chip";
import { chipKeyNumber, detectMac, pageIsRtl, type Rect, type UiCtx } from "./ctx";
import { VisibilityContext, withHitTesting } from "../visibility";
import { h, plainLabel, truncate } from "./dom";
import { createMarks, type MarkController } from "./marks";
import { createFlash, createPrompts, createToasts, type FlashController, type PromptController, type ToastController } from "./notices";
import { createPanel, type PanelController } from "./panel";
import { rememberNoteDismissed } from "./prefs";
import { STYLES } from "./styles";

const HOST_STYLE: [string, string][] = [
  ["all", "initial"],
  ["display", "block"],
  ["position", "fixed"],
  ["inset", "0 auto auto 0"],
  ["width", "0"],
  ["height", "0"],
  ["overflow", "visible"],
  ["z-index", "2147483647"],
  ["pointer-events", "none"],
  ["contain", "none"],
  ["visibility", "visible"],
  ["opacity", "1"],
];

type Host = HTMLElement & { showPopover?: () => void; hidePopover?: () => void };

const PROBE_PX = 100;
/** The same sentence is spoken once inside this window (a Tab insertion is announced by the logic and, as a fallback, by the flash). */
const SAY_AGAIN_MS = 1500;
/** An insertion the logic announced itself this recently is not announced a second time from its flash. */
const EXPLICIT_WINDOW_MS = 700;
/** The flash of an insertion belongs to the chip on screen when it sits on the same field (a pixel or two of rounding aside). */
const SAME_BOX_PX = 3;
const FALLBACK_SAY_MS = 150;
const FILLED_TOAST_MS = 800;

export function createUI(): AssistUI {
  const handlers = new Set<(action: UIAction) => void>();
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let host: Host | null = null;
  let root: ShadowRoot | null = null;
  let ui: HTMLElement | null = null;
  let chip: ChipController | null = null;
  let panel: PanelController | null = null;
  let prompts: PromptController | null = null;
  let toasts: ToastController | null = null;
  let flash: FlashController | null = null;
  let marks: MarkController | null = null;
  let observer: MutationObserver | null = null;
  let dialogObserver: MutationObserver | null = null;
  let probe: HTMLElement | null = null;
  let hostZoom = 1;
  let mounted = false;
  let lastPanel: PanelView | null = null;
  let lastChip: ChipView | null = null;
  let lastSave: SaveAskView | null = null;
  let lastTrack: TrackAskView | null = null;
  let lastMarks: FieldMark[] = [];
  let unlisten: (() => void) | null = null;
  let speaker: HTMLElement | null = null;
  let lastSaid = { text: "", at: 0 };
  let lastFilledToastAt = -Infinity;
  /** When the logic last announced an insertion by itself (`announce`): the flash that goes with it must not say it again. */
  let lastExplicitAt = -Infinity;

  const api: AssistUI = {
    // what this UI renders and runs, so a logic newer or older than it never strands the person
    capabilities: { fillPreview: true, chipActions: true },
    mount(options): void {
      if (mounted) {
        if (options.panel && !panel && ui) attachPanel();
        return;
      }
      build(options.panel);
    },
    unmount(): void {
      teardown();
    },
    setPanel(view: PanelView): void {
      lastPanel = view;
      if (panel) placeHost();
      panel?.set(view);
    },
    showChip(view: ChipView): void {
      lastChip = view;
      if (!chip) return;
      placeHost();
      chip.show(view);
    },
    updateChipAnchor(anchor: ChipView["anchor"]): void {
      if (lastChip) lastChip = { ...lastChip, anchor };
      chip?.moveTo(anchor);
    },
    hideChip(): void {
      lastChip = null;
      chip?.hide();
    },
    flash(anchor: ChipView["anchor"], tone: ChipView["tone"]): void {
      flash?.flash(anchor, tone);
      // The flash is raised for every insertion. Whatever the logic did not announce itself, say it for the field the chip was showing for
      // (a bulk fill, a field the chip has already left: the toast or nothing). The insertion also ends any typing there: the pill that
      // stood for the person's own text gives way to the full chip, with its Undo.
      const view = chip?.current();
      if (view && sameBox(view.anchor, anchor)) chip?.inserted();
      announceFromFlash(anchor, performance.now());
    },
    setMarks(next: FieldMark[]): void {
      lastMarks = Array.isArray(next) ? next : [];
      marks?.set(lastMarks);
    },
    toast(view: ToastView): void {
      if (view.kind === "filled") lastFilledToastAt = performance.now();
      toasts?.show(view);
    },
    announce(view: AnnounceView): void {
      if (view.kind !== "inserted") return;
      lastExplicitAt = performance.now();
      sayInserted(view.label, view.value);
    },
    chipAction(n: number): boolean {
      return Number.isInteger(n) && n >= 1 && n <= CHIP_KEY_COUNT && (chip?.keyAction(n) ?? false);
    },
    dismissNote(kind: "first_use"): void {
      if (panel) panel.dismissNote(kind);
      else rememberNoteDismissed(kind);
    },
    askSave(view: SaveAskView | null): void {
      lastSave = view;
      prompts?.askSave(view);
    },
    askTrack(view: TrackAskView | null): void {
      lastTrack = view;
      prompts?.askTrack(view);
    },
    onAction(handler: (action: UIAction) => void): () => void {
      handlers.add(handler);
      return () => { handlers.delete(handler); };
    },
    setLanguage(language: Language): void {
      setLocale(language);
      if (ui) ui.setAttribute("lang", htmlLang());
      chip?.refresh();
      marks?.refresh();
      panel?.refresh();
      prompts?.refresh();
      toasts?.refresh();
    },
    focusPanel(): boolean {
      if (prompts?.focus()) return true;
      return panel?.focus() ?? false;
    },
    ensureOnTop,
  };

  const ctx = (): UiCtx => ({
    root: root as ShadowRoot,
    emit(action: UIAction): void {
      for (const handler of [...handlers]) {
        try { handler(action); } catch { /* a failing subscriber must not break the UI */ }
      }
    },
    onTrustedClick(el: Element, handler: (ev: MouseEvent) => void): void {
      el.addEventListener("click", (ev) => {
        if (!(ev as MouseEvent).isTrusted) { ev.preventDefault(); ev.stopPropagation(); return; }
        handler(ev as MouseEvent);
      });
    },
    later(fn: () => void, ms: number): () => void {
      const id = setTimeout(() => { timers.delete(id); fn(); }, ms);
      timers.add(id);
      return () => { clearTimeout(id); timers.delete(id); };
    },
    reducedMotion(): boolean {
      try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
    },
    rtl: pageIsRtl,
    mac: detectMac(),
    say,
    obstacles,
  });

  /** A sentence for screen readers only: a polite live region that draws nothing. */
  function say(text: string): void {
    const line = text.trim();
    if (!speaker || !line) return;
    const now = performance.now();
    if (line === lastSaid.text && now - lastSaid.at < SAY_AGAIN_MS) return;
    lastSaid = { text: line, at: now };
    speaker.textContent = "";
    speaker.textContent = line;
  }

  function sayInserted(label: string, value: string | undefined): void {
    const name = plainLabel(label);
    if (!name) return;
    const shown = (value ?? "").replace(/\s+/g, " ").trim();
    say(shown ? t("announce.inserted", { label: truncate(name, 80), value: truncate(shown, 60) }) : t("announce.inserted.novalue", { label: truncate(name, 80) }));
  }

  function sameBox(a: Rect, b: Rect): boolean {
    return Math.abs(a.x - b.x) <= SAME_BOX_PX && Math.abs(a.y - b.y) <= SAME_BOX_PX && Math.abs(a.w - b.w) <= SAME_BOX_PX && Math.abs(a.h - b.h) <= SAME_BOX_PX;
  }

  function announceFromFlash(anchor: Rect, flashedAt: number): void {
    const view = chip?.current();
    if (!view || !isReady(view) || !sameBox(view.anchor, anchor)) return;
    const summary = chipSummary(view);
    context?.later(() => {
      // the logic's own announcement of this insertion (it may come just before or just after the flash) or the toast already said it
      if (lastExplicitAt >= flashedAt - EXPLICIT_WINDOW_MS || performance.now() - lastFilledToastAt < FILLED_TOAST_MS) return;
      sayInserted(summary.label, summary.value);
    }, FALLBACK_SAY_MS);
  }

  /** The panel, its pill and the open prompts: the chip keeps off them (UX-05). Measured when asked, never cached. */
  function obstacles(): Rect[] {
    const out: Rect[] = [];
    if (!ui) return out;
    for (const node of ui.querySelectorAll<HTMLElement>(".panel, .pill-btn, .card")) {
      const r = node.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) out.push({ x: r.x, y: r.y, w: r.width, h: r.height });
    }
    return out;
  }

  let context: UiCtx | null = null;

  function removeStaleHosts(): void {
    // an orphaned copy of this script (extension reloaded) may have left its host behind
    document.querySelectorAll(`[${UI_HOST_ATTRIBUTE}]`).forEach((node) => node.remove());
  }

  function attachStyles(shadow: ShadowRoot): void {
    try {
      if ("adoptedStyleSheets" in shadow && typeof CSSStyleSheet !== "undefined") {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(STYLES);
        shadow.adoptedStyleSheets = [sheet];
        return;
      }
    } catch {
      /* fall through to a <style> element */
    }
    shadow.append(h("style", null, STYLES));
  }

  /** The focused element, looking through the open shadow roots of components that wrap form fields. */
  function deepActive(): Element | null {
    let el: Element | null = document.activeElement;
    for (let depth = 0; depth < 16 && el?.shadowRoot?.activeElement; depth++) el = el.shadowRoot.activeElement;
    return el;
  }

  function isModal(node: Node): boolean {
    try {
      return node instanceof HTMLDialogElement && node.matches(":modal");
    } catch {
      return false; // :modal unsupported
    }
  }

  /** The open modal <dialog> around the focus, including one that lives inside another component's shadow root. */
  function modalAroundFocus(): Element | null {
    let node: Node | null = deepActive();
    for (let depth = 0; node && depth < 64; depth++) {
      if (isModal(node)) return node as Element;
      node = node.parentNode ?? (node instanceof ShadowRoot ? node.host : null);
    }
    return null;
  }

  function hostParent(): Element {
    // A modal dialog makes everything outside it inert, so the UI has to live inside it while it is open.
    const around = modalAroundFocus();
    if (around) return around;
    try {
      const modals = document.querySelectorAll("dialog:modal");
      if (modals.length > 0) return modals[modals.length - 1];
    } catch {
      /* :modal unsupported */
    }
    return document.documentElement;
  }

  function promote(el: Host): void {
    // The popover top layer sits above any modal <dialog> and every z-index on the page.
    try {
      if (typeof el.showPopover === "function") {
        el.setAttribute("popover", "manual");
        el.showPopover();
      }
    } catch {
      /* already open or unsupported: the max z-index still applies */
    }
  }

  /** What the person looks at: the chip, the panel and its pill, the open prompts. Only those that are drawn (they have a box). */
  const SURFACES = ".chip, .panel, .pill-btn, .card";

  function surfaceBoxes(selector: string): { node: HTMLElement; rect: DOMRect }[] {
    const out: { node: HTMLElement; rect: DOMRect }[] = [];
    if (!ui) return out;
    for (const node of ui.querySelectorAll<HTMLElement>(selector)) {
      const rect = node.getBoundingClientRect();
      if (rect.width > 1 && rect.height > 1) out.push({ node, rect });
    }
    return out;
  }

  /** The page may have rewritten the host's inline style (a script can: `host.style.cssText = ...`): put the rules back. */
  function reassertHost(): void {
    if (!host) return;
    for (const [prop, value] of HOST_STYLE) {
      if (host.style.getPropertyValue(prop) !== value || host.style.getPropertyPriority(prop) !== "important") host.style.setProperty(prop, value, "important");
    }
    host.hidden = false;
  }

  /** Is every point of every surface hit by the host before anything of the page that paints? Pointer-events are forced on for the test. */
  function surfacesOnTop(boxes: { node: HTMLElement; rect: DOMRect }[]): boolean {
    if (!host || !root) return false;
    const tree = host.getRootNode() as Document | ShadowRoot;
    if (typeof tree.elementsFromPoint !== "function") return true;
    const judge = new VisibilityContext();
    const trees: (Document | ShadowRoot)[] = [document, tree, root];
    return withHitTesting(trees, (forced) => {
      if (!forced) return true; // no way to test through a layer that ignores the pointer: the host is at least the last thing in the top layer
      for (const { rect } of boxes) {
        const inset = Math.max(1, Math.min(6, rect.width / 4, rect.height / 4));
        const points: [number, number][] = [
          [rect.x + rect.width / 2, rect.y + rect.height / 2], [rect.x + inset, rect.y + inset], [rect.right - inset - 0.01, rect.bottom - inset - 0.01],
        ];
        for (const [x, y] of points) {
          if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) continue; // off the page: a person sees nothing there either
          let stack: Element[] = [];
          try {
            stack = tree.elementsFromPoint(x, y);
          } catch {
            continue;
          }
          for (const top of stack) {
            if (top === host) break; // reached the assistant before anything of the page that paints
            if (judge.paints(top)) return false;
          }
        }
      }
      return true;
    }, true); // a write is about to follow: the strong rule
  }

  /** SEC-01: nothing is written while the page has put something that paints above the chip or the panel. */
  function ensureOnTop(): boolean {
    try {
      if (!mounted || !host || !root) return false;
      const wanted = !!lastChip || (!!lastPanel && !!panel);
      for (let attempt = 0; attempt < 2; attempt++) {
        reassertHost();
        if (!host.isConnected || host.parentNode !== hostParent()) placeHost();
        const boxes = surfaceBoxes(SURFACES);
        if (!boxes.length) {
          if (!wanted) return true;
        } else if (surfacesOnTop(boxes)) {
          return true;
        }
        // another layer of the page was opened after the assistant's: leave the top layer and enter it again, which puts the host last
        try { host.hidePopover?.(); } catch { /* not open */ }
        const parent = host.parentNode;
        if (typeof host.showPopover !== "function" && parent) parent.append(host); // no top layer: be the last child (the z-index is already the highest)
        promote(host);
      }
      return false;
    } catch {
      return false;
    }
  }

  function placeHost(): void {
    if (!host) return;
    const parent = hostParent();
    if (host.parentNode !== parent || !host.isConnected) {
      parent.append(host);
      promote(host);
    }
    fitScale();
  }

  /**
   * A page that zooms its root element (`html { zoom: 1.25 }`) would scale the assistant with it, and every position the
   * logic layer measures (getBoundingClientRect, in viewport pixels) would then be off. Measure a probe of known width and
   * counter-zoom the host so one pixel inside the UI is one pixel of the viewport again. Transforms on ancestors need no
   * such treatment: the host sits in the top layer, which they do not reach.
   */
  function fitScale(): void {
    if (!host || !probe) return;
    const measured = probe.getBoundingClientRect().width / PROBE_PX;
    if (!(measured > 0.2 && measured < 5) || Math.abs(measured - 1) < 0.005) return;
    hostZoom /= measured;
    host.style.setProperty("zoom", String(hostZoom), "important");
  }

  function build(withPanel: boolean): void {
    removeStaleHosts();
    host = document.createElement("div") as Host;
    host.setAttribute(UI_HOST_ATTRIBUTE, "");
    for (const [prop, value] of HOST_STYLE) host.style.setProperty(prop, value, "important");
    root = host.attachShadow({ mode: __SHADOW__ });
    attachStyles(root);
    context = ctx();
    ui = h("div", { class: "ui", lang: htmlLang(), dir: "ltr", testid: "ui" });
    probe = h("div", { class: "scale-probe", "aria-hidden": "true" });
    ui.append(probe);
    root.append(ui);

    const dock = h("div", { class: "dock dock-right", testid: "dock" });
    const promptBox = h("div", { class: "prompts", testid: "prompts" });
    const slot = h("div", { class: "panel-slot" });
    dock.append(promptBox, slot);
    const chipLayer = h("div", { class: "chip-layer", testid: "chip-layer" });
    const flashLayer = h("div", { class: "flash-layer", "aria-hidden": "true" });
    const marksLayer = h("div", { class: "marks-layer", role: "group", testid: "marks-layer" });
    const toastLayer = h("div", { class: "toasts", testid: "toast-layer" });
    speaker = h("div", { class: "sr", role: "status", "aria-live": "polite", "aria-atomic": "true", testid: "announce" });
    ui.append(dock, marksLayer, flashLayer, chipLayer, toastLayer, speaker);

    chip = createChip(context, chipLayer);
    flash = createFlash(context, flashLayer);
    marks = createMarks(context, marksLayer);
    toasts = createToasts(context, toastLayer);
    prompts = createPrompts(context, promptBox);
    if (withPanel) panel = createPanel(context, dock, slot);

    document.documentElement.append(host);
    promote(host);
    mounted = true;
    fitScale();
    if (__E2E__) api.root = root;

    // Clicking a button of the UI must not take focus away from the field the person is filling in: the next Tab would
    // otherwise start from the end of the page. Text inputs and the pairing code (selectable) keep their default.
    ui.addEventListener("mousedown", (ev) => {
      const target = ev.target;
      if (target instanceof Element && target.closest("input, textarea, .code")) return;
      ev.preventDefault();
    });

    const reflow = (): void => { fitScale(); chip?.reflow(); marks?.reflow(); };
    window.addEventListener("resize", reflow, { passive: true });
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", reflow);
    // the page may rebuild <html> children (SPA shells, hydration): keep the UI attached
    observer = new MutationObserver(() => {
      if (host && !host.isConnected && mounted) {
        placeHost();
      }
    });
    observer.observe(document.documentElement, { childList: true });
    // A modal <dialog> opened after the UI was mounted would leave it inert (and one that closes would hide it with it):
    // follow the dialog's `open` attribute, and the focus moving into a dialog that lives in a page component's shadow root.
    dialogObserver = new MutationObserver(() => { if (mounted) placeHost(); });
    dialogObserver.observe(document, { attributes: true, attributeFilter: ["open"], subtree: true });
    const onFocus = (): void => { if (mounted) placeHost(); };
    document.addEventListener("focusin", onFocus, true);
    // While the person types into the field the chip is for, it shrinks to a one-line pill (UX-01). Only trusted typing counts: the
    // logic's own insertions dispatch synthetic input events. Whether the field is empty again is the one thing read of it.
    const onInput = (ev: Event): void => {
      if (!ev.isTrusted || !(ev instanceof InputEvent) || ev.target === host) return;
      const field = ev.target;
      const cleared = (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) && field.value === "";
      chip?.setTyping(!cleared);
    };
    document.addEventListener("input", onInput, true);
    // The chip's buttons carry numbers; the chip is never focusable, so a chord is the keyboard path to them (UX-10): Control+Option+digit on a
    // Mac, Alt+Shift+digit elsewhere. Neither types a character (Option+digit does on a Mac: it must never be swallowed). The logic matches the
    // same chord and asks `chipAction`; whichever hears the key first runs the action, and once it has run nobody else sees the key.
    const onKey = (ev: KeyboardEvent): void => {
      if (!ev.isTrusted || ev.isComposing || ev.repeat || !chip || ev.target === host) return;
      const prefix = chip.current()?.keyHints?.chipActionPrefix;
      const n = chipKeyNumber(ev, prefix ? prefix === "mac" : (context?.mac ?? false), CHIP_KEY_COUNT);
      if (n < 1 || !chip.keyAction(n)) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    };
    // Esc inside the panel or a prompt (what handles it locally keeps it): the keyboard goes back to the form field the person was on
    dock.addEventListener("keydown", (ev) => {
      if (!ev.isTrusted || ev.key !== "Escape" || ev.defaultPrevented || ev.isComposing || ev.ctrlKey || ev.altKey || ev.metaKey || ev.shiftKey) return;
      ev.preventDefault();
      ev.stopPropagation();
      context?.emit({ type: "panel-escape" });
    });
    window.addEventListener("keydown", onKey, true);
    unlisten = () => {
      window.removeEventListener("resize", reflow);
      viewport?.removeEventListener("resize", reflow);
      document.removeEventListener("focusin", onFocus, true);
      document.removeEventListener("input", onInput, true);
      window.removeEventListener("keydown", onKey, true);
    };

    if (lastPanel && panel) panel.set(lastPanel);
    if (lastChip) chip.show(lastChip);
    if (lastMarks.length > 0) marks.set(lastMarks);
    if (lastSave) prompts.askSave(lastSave);
    if (lastTrack) prompts.askTrack(lastTrack);
  }

  function attachPanel(): void {
    if (!context || !ui) return;
    const dock = ui.querySelector<HTMLElement>(".dock");
    const slot = ui.querySelector<HTMLElement>(".panel-slot");
    if (!dock || !slot) return;
    panel = createPanel(context, dock, slot);
    if (lastPanel) panel.set(lastPanel);
  }

  function teardown(): void {
    observer?.disconnect(); observer = null;
    dialogObserver?.disconnect(); dialogObserver = null;
    unlisten?.(); unlisten = null;
    chip?.destroy(); panel?.destroy(); prompts?.destroy(); toasts?.destroy(); flash?.destroy(); marks?.destroy();
    chip = null; panel = null; prompts = null; toasts = null; flash = null; marks = null;
    for (const id of timers) clearTimeout(id);
    timers.clear();
    host?.remove();
    host = null; root = null; ui = null; context = null; probe = null; hostZoom = 1; speaker = null;
    lastPanel = null; lastChip = null; lastSave = null; lastTrack = null; lastMarks = [];
    lastSaid = { text: "", at: 0 }; lastFilledToastAt = -Infinity; lastExplicitAt = -Infinity;
    mounted = false;
    if (__E2E__) delete api.root;
  }

  return api;
}
