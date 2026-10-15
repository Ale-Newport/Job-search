// Shared plumbing handed to every visual component of one UI instance.

import type { UIAction } from "../ui-api";

export interface UiCtx {
  root: ShadowRoot;
  /** Deliver a user interaction to the logic layer. */
  emit(action: UIAction): void;
  /** Click handler that ignores synthetic (page-dispatched) events. */
  onTrustedClick(el: Element, handler: (ev: MouseEvent) => void): void;
  /** A timer owned by the UI instance; cancelled on unmount. Returns a cancel function. */
  later(fn: () => void, ms: number): () => void;
  reducedMotion(): boolean;
  /** The page reads right to left: chips align to the field's right edge, markers sit on its left corner. */
  rtl(): boolean;
  mac: boolean;
  /** A sentence for screen readers only (a polite live region, nothing is drawn). The same words twice in a row are said once. */
  say(text: string): void;
  /** Rectangles of the parts of this UI the chip must not sit on: the expanded panel (or its pill) and the open prompts. */
  obstacles(): Rect[];
}

export function detectMac(): boolean {
  try {
    const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
    const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || "";
    return /mac|iphone|ipad|ipod/i.test(platform);
  } catch {
    return false;
  }
}

/** Alt/Option modifier label for key hints: ⌥ on macOS, "Alt+" elsewhere. */
export function altLabel(mac: boolean): string {
  return mac ? "⌥" : "Alt+";
}

/** Shift modifier label for key hints: the glyph on macOS, the key's name ("Shift", "Mayús") plus "+" elsewhere. */
export function shiftLabel(mac: boolean, word = "Shift"): string {
  return mac ? "⇧" : `${word}+`;
}

/**
 * The browser's own shortcut for the toolbar button (manifest `commands._execute_action`): the DEFAULT, because the person may
 * have remapped it and a content script cannot ask. Null where there is none to show: Safari has no `commands`.
 */
export function activationShortcut(mac: boolean): string | null {
  if (__SAFARI__) return null;
  return mac ? "⌘⇧Y" : "Ctrl+Shift+Y";
}

/**
 * The chord that runs the chip's numbered actions. It must type nothing: Option+digit types a character on a Mac (¡ ™ £ ¢ ∞ § ¶, and
 * Option+3 is # on a British keyboard), so a Mac uses Control+Option, and everywhere else Alt+Shift, which no layout types.
 * The printed label, the ARIA shortcut and the key match all come from here.
 */
export function chipKeyLabel(mac: boolean, n: number): string {
  return mac ? `⌃⌥${n}` : `Alt+Shift+${n}`;
}

/** The same chord as `aria-keyshortcuts` writes it. */
export function chipKeyAria(mac: boolean, n: number): string {
  return mac ? `Control+Alt+${n}` : `Alt+Shift+${n}`;
}

/**
 * Which chip action (1 to `count`) a key press asks for, or 0. The physical digit key decides (`code`), never the character, because the
 * character depends on the layout. AltGr (reported as Control+Alt on Windows, or as the AltGraph state) is a way of typing characters, so
 * a press that carries it is never a chip key.
 */
export function chipKeyNumber(ev: Pick<KeyboardEvent, "altKey" | "ctrlKey" | "shiftKey" | "metaKey" | "code" | "getModifierState">, mac: boolean, count: number): number {
  if (ev.metaKey) return 0;
  const chord = mac ? ev.ctrlKey && ev.altKey && !ev.shiftKey : ev.altKey && ev.shiftKey && !ev.ctrlKey;
  if (!chord) return 0;
  try {
    if (ev.getModifierState("AltGraph")) return 0;
  } catch { /* an old engine without the state */ }
  const match = /^Digit([1-9])$/.exec(ev.code);
  const n = match ? Number(match[1]) : 0;
  return n >= 1 && n <= count ? n : 0;
}

/** The shortcut that moves the keyboard into the panel (Alt+Shift+M), as printed in key hints and announcements. */
export function panelShortcut(mac: boolean, shiftWord = "Shift"): string {
  return altShiftKey(mac, shiftWord, "M");
}

/** Alt (Option) + Shift + a letter, as printed: "⌥⇧M" on a Mac, "Alt+Shift+M" ("Alt+Mayús+M" in Spanish) elsewhere. */
export function altShiftKey(mac: boolean, shiftWord: string, letter: string): string {
  return `${altLabel(mac)}${shiftLabel(mac, shiftWord)}${letter}`;
}

export interface Viewport { w: number; h: number }

/** Layout viewport of THIS frame, excluding classic scrollbars. */
export function viewport(): Viewport {
  const doc = document.documentElement;
  const standards = document.compatMode === "CSS1Compat";
  const w = standards && doc.clientWidth > 0 ? doc.clientWidth : window.innerWidth;
  const h = standards && doc.clientHeight > 0 ? doc.clientHeight : window.innerHeight;
  return { w, h };
}

/** Reading direction of the page. Reading a computed style is cheap when nothing is dirty, so nothing is cached. */
export function pageIsRtl(): boolean {
  try {
    const el = document.body ?? document.documentElement;
    return getComputedStyle(el).direction === "rtl";
  } catch {
    return false;
  }
}

export interface Rect { x: number; y: number; w: number; h: number }

/** Area (px squared) where two rectangles overlap. */
export function overlapArea(a: Rect, b: Rect): number {
  const width = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? width * height : 0;
}
