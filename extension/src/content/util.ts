// Shared helpers of the content script: worker messaging that never throws, DOM helpers, hashing, text normalising.
// Nothing in here touches the bridge or page values; it is safe to import from every module.

import type { BgRequest, BgResponse, BgResponseData } from "../shared/messages";
import { isMacPlatform } from "../shared/platform";
import { UI_HOST_ATTRIBUTE } from "./ui-api";

/** Is this a Mac (Control+Option chords, Command shortcuts)? The one test the options page and the visual layer use to print the glyphs. */
export { isMacPlatform };

// ---- extension runtime (orphan-safe) ---------------------------------------------------------------------------------
// After an extension reload/update the content scripts of open tabs lose their runtime: every call throws
// "Extension context invalidated". We never let that reach the page; the controller turns it into a calm
// "Reload this page" state and stops intercepting keys.

const ORPHAN_PATTERN = /extension context invalidated|context invalidated|receiving end does not exist|message port closed before|extension has been (?:removed|reloaded|updated)/i;
let orphaned = false;
const orphanListeners = new Set<() => void>();

type RuntimeLike = {
  id?: string;
  sendMessage: (msg: unknown) => Promise<unknown>;
  onMessage: { addListener: (fn: (...args: any[]) => unknown) => void; removeListener?: (fn: (...args: any[]) => unknown) => void };
};

function runtime(): RuntimeLike | null {
  try {
    const g = globalThis as unknown as { chrome?: { runtime?: RuntimeLike }; browser?: { runtime?: RuntimeLike } };
    return g.chrome?.runtime ?? g.browser?.runtime ?? null;
  } catch {
    return null;
  }
}

export function isOrphaned(): boolean {
  if (orphaned) return true;
  let alive = false;
  try {
    alive = !!runtime()?.id;
  } catch {
    alive = false;
  }
  if (!alive) markOrphaned();
  return orphaned;
}

export function markOrphaned(): void {
  if (orphaned) return;
  orphaned = true;
  for (const cb of [...orphanListeners]) {
    try {
      cb();
    } catch {
      /* a listener must never break the others */
    }
  }
}

export function onOrphaned(cb: () => void): () => void {
  orphanListeners.add(cb);
  return () => orphanListeners.delete(cb);
}

/** Test hook (E2E only): lets the rig simulate an extension reload. */
export function resetOrphanedForTests(): void {
  orphaned = false;
}

export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  try {
    return String(e);
  } catch {
    return "unknown error";
  }
}

export function withTimeout<T>(promise: Promise<T>, ms: number, what = "request"): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${what} timed out`)), ms);
    promise.then(
      (v) => { clearTimeout(timer); resolve(v); },
      (e) => { clearTimeout(timer); reject(e); },
    );
  });
}

type ReqOf<T extends BgRequest["t"]> = Extract<BgRequest, { t: T }>;

function failure(code: string, detail: string): BgResponse<never> {
  return { ok: false, error: { code, detail } };
}

/** The ONLY way the content script talks to the worker. Never throws; transport failures become `{ok:false}`. */
export async function sendBg<T extends BgRequest["t"]>(req: ReqOf<T>, timeoutMs = 30000): Promise<BgResponse<BgResponseData<T>>> {
  if (isOrphaned()) return failure("orphaned", "The extension was reloaded; reload this page.");
  const api = runtime();
  if (!api) return failure("orphaned", "The extension runtime is unavailable.");
  try {
    const res = (await withTimeout(Promise.resolve(api.sendMessage(req)), timeoutMs, `message ${req.t}`)) as BgResponse<BgResponseData<T>> | undefined;
    if (!res || typeof res !== "object" || typeof (res as { ok?: unknown }).ok !== "boolean") {
      return failure("no_response", "The extension worker did not answer.");
    }
    return res;
  } catch (e) {
    const message = errorMessage(e);
    if (ORPHAN_PATTERN.test(message)) {
      markOrphaned();
      return failure("orphaned", "The extension was reloaded; reload this page.");
    }
    return failure("transport", message.slice(0, 200));
  }
}

/** Registers the single runtime.onMessage listener; false when the runtime is already gone. */
export function addRuntimeListener(fn: (message: unknown, sender: unknown, sendResponse: (r?: unknown) => void) => unknown): boolean {
  try {
    const api = runtime();
    if (!api) return false;
    api.onMessage.addListener(fn as (...args: any[]) => unknown);
    return true;
  } catch {
    markOrphaned();
    return false;
  }
}

// ---- never into the page ----------------------------------------------------------------------------------------------------
// Everything the assistant schedules (events, timers, frames, promises) runs through these: a page can hand it a node that throws
// from any getter, or take a node away in the middle of a handler. Whatever goes wrong here must never show up as an error of the
// PAGE (its error monitors, its console), and one failed callback must never stop the next one. E2E builds keep what was swallowed
// (messages only) so that the tests can see it.

const swallowed: string[] = [];

function note(error: unknown): void {
  if (!__E2E__) return;
  swallowed.push(errorMessage(error).slice(0, 160));
  if (swallowed.length > 20) swallowed.shift();
}

/** E2E builds: the errors the guards swallowed so far (the last 20). */
export function swallowedErrors(): string[] {
  return swallowed.slice();
}

/** `fn` as a callback that never throws and never leaves a rejected promise behind. */
export function guard<A extends unknown[]>(fn: (...args: A) => unknown): (...args: A) => void {
  return (...args: A): void => {
    try {
      const result = fn(...args);
      if (result && typeof (result as PromiseLike<unknown>).then === "function") (result as PromiseLike<unknown>).then(undefined, note);
    } catch (e) {
      note(e);
    }
  };
}

/** Fire-and-forget work: its failure is noted, never raised. */
export function spawn(work: PromiseLike<unknown> | void): void {
  if (work && typeof work.then === "function") work.then(undefined, note);
}

export const after = (fn: () => unknown, ms: number): ReturnType<typeof setTimeout> => setTimeout(guard(fn), ms);
export const every = (fn: () => unknown, ms: number): ReturnType<typeof setInterval> => setInterval(guard(fn), ms);
export const onFrame = (fn: () => unknown): number => requestAnimationFrame(guard(fn));
export const soon = (fn: () => unknown): void => queueMicrotask(guard(fn));

// ---- text ----------------------------------------------------------------------------------------------------------------

/** Lower-case, accent-free, punctuation-free comparison form ("Prefer not to say" == "prefer not to say."). */
export function normalizeText(value: string): string {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** Letters and digits only: tolerant of masks, spacing and capitalisation, still sensitive to lost content. */
export function squash(value: string): string {
  return normalizeText(value).replace(/\s+/g, "");
}

export function tokens(value: string): string[] {
  const n = normalizeText(value);
  return n ? n.split(" ") : [];
}

/** Hard cut without an ellipsis: for strings that go to the bridge (server-side maxima) or are compared later. */
export function cut(value: string, max: number): string {
  const text = String(value ?? "");
  return text.length <= max ? text : text.slice(0, max);
}

export function clip(value: string, max: number): string {
  const text = String(value ?? "");
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
}

/** Collapses whitespace; used for labels and evidence excerpts. */
export function tidy(value: string): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

// ---- keys ----------------------------------------------------------------------------------------------------------------------

/** The character a key press printed when it is ONE ASCII character ("" for "Dead", "Ω", "я", "Tab", undefined ...). */
function printedAscii(e: Pick<KeyboardEvent, "key">): string {
  const key = typeof e.key === "string" ? e.key : "";
  return key.length === 1 && key.charCodeAt(0) < 128 ? key : "";
}

/**
 * Does this key press mean Alt+`letter`? What the key prints decides whenever it is an ASCII character, so the letter shown in the chip's hint
 * works on any layout (QWERTZ, AZERTY, Dvorak, Colemak) and the physical key of another layout does not fire it by accident (the key that
 * prints "?" where a US keyboard has M is not Alt+M). A key that prints no ASCII character at all (Option+Z on a Mac prints "Ω", a Cyrillic
 * layout prints Cyrillic, a dead key prints "Dead") falls back to the physical key, which is where that letter sits on a US keyboard.
 */
export function altLetter(e: Pick<KeyboardEvent, "key" | "code">, letter: "r" | "z" | "m"): boolean {
  const printed = printedAscii(e);
  if (printed) return printed.toLowerCase() === letter;
  return e.code === `Key${letter.toUpperCase()}`;
}

/** Which chord reaches the numbered actions of the chip (UXR-03): chords that never type a character. */
export function chipActionPrefix(): "mac" | "other" {
  return isMacPlatform() ? "mac" : "other";
}

/**
 * The number (1..max) of the chip action a key press asks for, or 0. The chord is Control+Option on a Mac (Option+digit types characters
 * there, and so does Option+Shift) and Alt+Shift elsewhere (Alt+digit alone would be a browser/menu key, Ctrl+Alt is AltGr on many layouts).
 * Matching is by the physical digit row (`code`) and, failing that, by the digit the key printed (a numpad, a layout with digits elsewhere).
 */
export function chipActionNumber(e: Pick<KeyboardEvent, "key" | "code" | "altKey" | "ctrlKey" | "shiftKey" | "metaKey">, mac: boolean, max = 7): number {
  const chord = mac ? e.altKey && e.ctrlKey && !e.shiftKey && !e.metaKey : e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey;
  if (!chord) return 0;
  const byCode = /^Digit([1-9])$/.exec(typeof e.code === "string" ? e.code : "");
  const printed = printedAscii(e);
  const n = byCode ? Number(byCode[1]) : /^[1-9]$/.test(printed) ? Number(printed) : 0;
  return n >= 1 && n <= max ? n : 0;
}

/** Alt (Option) + Shift and no other modifier: the chord of Alt+Shift+M (panel) and Alt+Shift+Z (undo the whole "Fill verified"). */
export function altShift(e: Pick<KeyboardEvent, "altKey" | "shiftKey" | "ctrlKey" | "metaKey">): boolean {
  return e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey;
}

// ---- labels as the person reads them ---------------------------------------------------------------------------------------

/** Glyphs pages print to mark a required question: * and its look-alikes, and the footnote daggers some forms use for the same. */
const REQUIRED_GLYPHS = "*\u2217\u2731\u2732\u2733\u2734\u273a\u273b\u273c\u273d\uff0a\u204e\ufe61\u2020\u2021";
const LEADING_MARKS = new RegExp(`^[\\s${REQUIRED_GLYPHS}]+`);
const WORD_MARKS = new RegExp(`\\s*[${REQUIRED_GLYPHS}]+(?=\\s|[)\\]:.?!,;]|$)`, "g");
const REQUIRED_WORDS = /\s*[([](?:\*|required|obligatorio|obligatoria|requerido|requerida|campo obligatorio)[)\]]/gi;

/**
 * A field's label for the PERSON (chips, announcements, the panel's rows, "Go to ..."): the page's own text without its required-marker
 * glyphs ("First Name *" -> "First Name", "Full name✱" -> "Full name", "(required)"), which a screen reader reads out and the panel prints
 * again beside its own marker. What goes to Meridian is the page's text untouched.
 */
export function stripRequiredMarks(label: string): string {
  const text = String(label ?? "");
  const stripped = text.replace(REQUIRED_WORDS, "").replace(LEADING_MARKS, "").replace(WORD_MARKS, "");
  const out = tidy(stripped);
  return out || tidy(text); // a label that is nothing but marks keeps what the page printed
}

// ---- the question as the person reads it ---------------------------------------------------------------------------------

/**
 * The element that PRINTS the question of a field: its <label> (for a radio / checkbox group, the legend or the group's own label).
 * Null when the page prints none. The chip stays off it, so that the person can read what a suggestion answers (UX-01).
 */
export function questionElement(field: { el: Element; members: readonly unknown[] }): Element | null {
  try {
    const el = field.el;
    const root = el.getRootNode() as Document | ShadowRoot;
    const byId = (list: string | null): Element | null => {
      for (const id of (list ?? "").split(/\s+/).filter(Boolean)) {
        const found = root.getElementById?.(id) ?? document.getElementById(id);
        if (found) return found;
      }
      return null;
    };
    if (field.members.length) {
      const group = el.closest('fieldset,[role="radiogroup"],[role="group"]');
      return group?.querySelector("legend") ?? byId(group?.getAttribute("aria-labelledby") ?? null);
    }
    return byId(el.getAttribute("aria-labelledby")) ?? (el as HTMLInputElement).labels?.[0] ?? el.closest("label");
  } catch {
    return null;
  }
}

/** The rule the worker names in its "a rule still starts the assistant here" answer (`...because of the rule for <rule>. Remove it ...`), or null. */
export function ruleOf(detail: string): string | null {
  const hit = /\brule for (.+?)\.(?:\s|$)/i.exec(String(detail ?? ""));
  const rule = hit?.[1]?.trim() ?? "";
  return rule && rule.length <= 120 ? rule : null;
}

/** FNV-1a (32 bit), enough to detect "this label/options/type changed"; never a security primitive. */
export function hashText(value: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${h.toString(36)}${value.length.toString(36)}`;
}

// ---- timing ---------------------------------------------------------------------------------------------------------------

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface Debounced {
  (): void;
  cancel(): void;
  flush(): void;
  pending(): boolean;
}

/** Trailing debounce with a max wait so a constantly mutating page still gets scanned. */
export function debounce(fn: () => void, wait: number, maxWait = wait * 5): Debounced {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let first = 0;
  const run = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    first = 0;
    fn();
  };
  const call = (() => {
    const now = Date.now();
    if (!first) first = now;
    if (timer) clearTimeout(timer);
    const remaining = Math.max(0, Math.min(wait, first + maxWait - now));
    timer = setTimeout(run, remaining);
  }) as Debounced;
  call.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    first = 0;
  };
  call.flush = () => {
    if (timer) run();
  };
  call.pending = () => timer !== null;
  return call;
}

// ---- DOM -------------------------------------------------------------------------------------------------------------------

export interface Rect { x: number; y: number; w: number; h: number }

export function inUiHost(node: Node | null | undefined): boolean {
  let cur: Node | null | undefined = node;
  let guard = 0;
  while (cur && guard++ < 200) {
    if (cur.nodeType === 1 && (cur as Element).hasAttribute(UI_HOST_ATTRIBUTE)) return true;
    cur = cur.parentNode ?? (cur as ShadowRoot).host ?? null;
  }
  return false;
}

export function deepActiveElement(root: Document | ShadowRoot = document): Element | null {
  let el: Element | null = root.activeElement;
  let guard = 0;
  while (el && guard++ < 20) {
    const inner: Element | null | undefined = el.shadowRoot?.activeElement;
    if (!inner) break;
    el = inner;
  }
  return el;
}

/** The real event target, looking through open shadow roots. */
export function composedTarget(event: Event): Element | null {
  let target: EventTarget | null | undefined;
  try {
    target = event.composedPath?.()[0];
  } catch {
    target = undefined;
  }
  target = target ?? event.target;
  return target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
}

/** `closest` that also climbs out of open shadow roots (an `aria-hidden` / `inert` / `form` ancestor may live above a host). */
export function closestDeep(el: Element | null | undefined, selector: string): Element | null {
  let cur: Element | null | undefined = el;
  for (let guard = 0; cur && guard < 60; guard++) {
    let hit: Element | null = null;
    try {
      hit = cur.closest(selector);
    } catch {
      return null;
    }
    if (hit) return hit;
    const root = cur.getRootNode();
    cur = root && root.nodeType === 11 ? (root as ShadowRoot).host : null;
  }
  return null;
}

export function isRendered(el: Element): boolean {
  try {
    return el.isConnected && el.getClientRects().length > 0;
  } catch {
    return false;
  }
}

export function rectOf(el: Element | null | undefined): Rect {
  if (!el || inUiHost(el)) return { x: 0, y: 0, w: 0, h: 0 };
  try {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
  } catch {
    return { x: 0, y: 0, w: 0, h: 0 };
  }
}

export function unionRect(rects: Rect[]): Rect {
  const real = rects.filter((r) => r.w > 0 && r.h > 0);
  if (!real.length) return { x: 0, y: 0, w: 0, h: 0 };
  const x1 = Math.min(...real.map((r) => r.x));
  const y1 = Math.min(...real.map((r) => r.y));
  const x2 = Math.max(...real.map((r) => r.x + r.w));
  const y2 = Math.max(...real.map((r) => r.y + r.h));
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

function treePath(node: Node): Node[] {
  const path: Node[] = [];
  let cur: Node | null = node;
  let guard = 0;
  while (cur && guard++ < 500) {
    path.push(cur);
    cur = cur.parentNode ?? (cur as ShadowRoot).host ?? null;
  }
  return path.reverse();
}

/** Document order that also works across open shadow roots (a shadow tree sorts right after its host's light children). */
export function compareTreeOrder(a: Node, b: Node): number {
  if (a === b) return 0;
  const pa = treePath(a);
  const pb = treePath(b);
  let i = 0;
  while (i < pa.length && i < pb.length && pa[i] === pb[i]) i++;
  if (i >= pa.length) return -1; // a is an ancestor of b
  if (i >= pb.length) return 1;
  const x = pa[i];
  const y = pb[i];
  if (x.parentNode === y.parentNode && x.parentNode) {
    return x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  }
  // different trees below the same host: shadow content after light content
  const xShadow = x.nodeType === 11;
  const yShadow = y.nodeType === 11;
  if (xShadow !== yShadow) return xShadow ? 1 : -1;
  return 0;
}

/** True when the element is in the page (not the assistant UI) and is a plausible interactive node. */
export function isPageNode(node: Node | null | undefined): boolean {
  return !!node && node.isConnected && !inUiHost(node);
}

// ---- bytes / hashing (documents) ------------------------------------------------------------------------------------

export function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64.replace(/\s+/g, ""));
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
  0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
  0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

/** Plain SHA-256, used only when WebCrypto is unavailable (content scripts on insecure http pages have no crypto.subtle). */
function sha256Fallback(data: Uint8Array): string {
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const length = data.length;
  const padded = new Uint8Array(((length + 9 + 63) >> 6) << 6);
  padded.set(data);
  padded[length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor((length * 8) / 0x100000000));
  view.setUint32(padded.length - 4, (length * 8) >>> 0);
  const w = new Uint32Array(64);
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = ((w[i - 15] >>> 7) | (w[i - 15] << 25)) ^ ((w[i - 15] >>> 18) | (w[i - 15] << 14)) ^ (w[i - 15] >>> 3);
      const s1 = ((w[i - 2] >>> 17) | (w[i - 2] << 15)) ^ ((w[i - 2] >>> 19) | (w[i - 2] << 13)) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  }
  return [...h].map((x) => x.toString(16).padStart(8, "0")).join("");
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  try {
    const subtle = globalThis.crypto?.subtle;
    if (subtle) {
      const digest = await subtle.digest("SHA-256", bytes as unknown as BufferSource);
      return [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
    }
  } catch {
    /* fall through to the portable implementation */
  }
  return sha256Fallback(bytes);
}
