// The field chip: what Meridian recommends for the FOCUSED field, anchored next to it.
// Rendered in the closed shadow root of this frame. Nothing in here may join the form's tab order (every control is
// tabindex=-1) and mousedown is cancelled so a click never steals focus from the page field. The buttons that matter for a
// keyboard user carry their own number (Control+Option+1 ... 7 on a Mac, Alt+Shift+1 ... 7 elsewhere, printed on the button); create.ts routes the keys here (`keyAction`).

import type { SuggestionState } from "../../shared/protocol";
import { formatNumber, reasonText, t, tn, whyText, type StringKey } from "../i18n";
import type { ChipView } from "../ui-api";
import { altLabel, chipKeyAria, chipKeyLabel, overlapArea, viewport, type Rect, type UiCtx } from "./ctx";
import { clamp, countWords, h, hostOf, icon, plainLabel, safeHref, truncate, type IconName } from "./dom";
import { createOptionMarks } from "./options";

export type VisualTone = "verified" | "inferred" | "draft" | "locked" | "neutral" | "busy" | "error";
type Anchor = ChipView["anchor"];
type Side = "below" | "above" | "beside";

const READY_STATES: ReadonlySet<SuggestionState> = new Set<SuggestionState>(["VERIFIED", "INFERRED", "GENERATED_DRAFT"]);
const MARGIN = 8;
const GAP = 6;
const MIN_SPACE = 44;
const MAX_WIDTH = 380;
const MAX_WIDTH_WIDE = 440;
const MAX_WIDTH_PILL = 320;
const BESIDE_MIN_WIDTH = 260;
/** A long draft is made no narrower than this to stay clear of the panel. */
const RICH_MIN_WIDTH = 320;
/** The one-line pill a chip folds into when nothing else keeps it off the neighbours and the panel: the icon, a few words, the key. */
const COMPACT_MIN_WIDTH = 200;
/**
 * Covering an option of the focused choice group (or the outline of the recommended one) is worse than covering anything else: the person
 * has to READ those. It costs more than any area a neighbour could add, so a side that covers one only wins when every side does.
 */
const HARD_COST = 1e9;
/**
 * Covering a NEXT field (the person is about to go there: the controller names them, `ChipView.avoidNext`) by more than a sliver is worse than
 * covering anything else that is left to cover (the previous field, the question's own label, the panel): a side that leaves the next fields
 * alone always wins over one that does not, whatever the areas. The areas then only order the sides within each group. Only when EVERY side
 * covers a next field is the chip folded to its one-line pill (see `place`).
 */
const AHEAD_COST = 1e7;
/**
 * A full chip is folded to one line only when it covers at least this much of what must stay visible (px squared: about a 24 px square):
 * a sliver of the next field's margin is not worth taking the explanation and the keys away.
 */
const FOLD_MIN_COST = 600;
/**
 * Another side beats the preferred one only by covering at least this much less (half): the logic chose its side knowing which neighbour
 * matters most (the NEXT field, which the person is about to go to), the visual layer only knows the areas, so a near tie is the logic's.
 */
const CLEARLY_LESS = 0.5;
/** A rectangle covered by no more than this on one side (rounding of the page's own layout) is not covered. */
const COVER_TOLERANCE_PX = 1;
/** An avoid rectangle that shares this many pixels of height with the field is level with it: an option, or a neighbour in the same row. */
const LEVEL_PX = 2;
/** An obstacle whose start edge is within this of the chip's own start edge is one the chip is already on top of: narrowing cannot help. */
const MIN_KEEP_PX = 40;
/** Covering part of the panel costs this many times what covering the same area of a neighbouring field costs. */
const OBSTACLE_WEIGHT = 6;
const UNDO_MS = 8000;
const ANNOUNCE_DELAY_MS = 280;
const INLINE_SOURCES = 2;
const SKIP_CODES = ["file_unapproved", "file_cover_letter", "file_none", "generic"] as const;

/** The numbers of the chip's keyboard actions. The same key always means the same thing; the cheat sheet lists them. */
export const CHIP_KEY = { replace: 1, shorter: 2, longer: 3, different: 4, more: 5, source: 6, sources: 7 } as const;
export const CHIP_KEY_COUNT = 7;

export function isReady(view: ChipView): boolean {
  return READY_STATES.has(view.state) && view.value.trim() !== "";
}

/** A draft is on its way. A failed or cancelled generation is calm and neutral, never a pulsing "writing". */
export function isBusy(view: ChipView): boolean {
  if (view.state !== "GENERATING") return false;
  const status = view.generation?.status;
  return status !== "FAILED" && status !== "CANCELLED";
}

export function visualTone(view: ChipView): VisualTone {
  if (view.state === "GENERATING") return isBusy(view) ? "busy" : "neutral";
  if (view.tone === "error") return "error";
  switch (view.state) {
    case "CONSENT":
    case "SENSITIVE_BLANK":
      return "locked";
    case "NEEDS_YOU":
    case "NOT_APPLICABLE":
    case "SKIP":
      return "neutral";
    default:
      break;
  }
  if (!isReady(view)) return "neutral";
  if (view.insert === "explicit" && !view.readIn) return "locked";
  if (view.tone === "verified" || view.tone === "inferred" || view.tone === "draft") return view.tone;
  return view.state === "VERIFIED" ? "verified" : view.state === "INFERRED" ? "inferred" : "draft";
}

export const TONE_ICON: Record<VisualTone, IconName> = {
  verified: "check", inferred: "eye", draft: "sparkle", locked: "lock", neutral: "info", busy: "dot", error: "alert",
};

/** The quiet copy of a skipped file field: a label and one sentence, by `skipReason.code` (server English only for an unknown code). */
function skipCopy(view: ChipView): { label: string; reason: string } | null {
  const skip = view.skipReason;
  if (view.state !== "SKIP" || !skip) return null;
  const code = (SKIP_CODES as readonly string[]).includes(skip.code) ? skip.code : null;
  if (code) return { label: t(`chip.skip.label.${code}` as StringKey), reason: t(`chip.skip.reason.${code}` as StringKey) };
  return { label: t("chip.skip.label.generic"), reason: skip.text.trim() || t("chip.skip.reason.generic") };
}

function trustLabel(view: ChipView, tone: VisualTone): string {
  const skip = skipCopy(view);
  if (skip) return skip.label;
  switch (view.state) {
    case "CONSENT": return t("chip.trust.consent");
    case "SENSITIVE_BLANK": return t("chip.trust.sensitive");
    case "NOT_APPLICABLE": return t("chip.trust.notApplicable");
    case "GENERATING": return isBusy(view) ? t(view.generation?.kind === "local" ? "chip.trust.busyLocal" : "chip.trust.busy") : t("chip.trust.needs");
    default: break;
  }
  if (tone === "error") return t("chip.trust.error");
  if (tone === "locked") return t("chip.trust.protected");
  if (tone === "verified") return t("chip.trust.verified");
  if (tone === "inferred") return t("chip.trust.inferred");
  if (tone === "draft") return t("chip.trust.draft");
  return t("chip.trust.needs");
}

/** The calm one-sentence reason for a chip that has nothing to insert. Server English is only a last resort. */
function quietReason(view: ChipView): string {
  const skip = skipCopy(view);
  if (skip) return skip.reason;
  const byCode = reasonText(view.reasonCode);
  if (byCode) return byCode;
  if (view.state === "CONSENT") return t("reason.consent");
  if (view.state === "SENSITIVE_BLANK") return t("reason.sensitive_blank");
  if (view.state === "NOT_APPLICABLE") return t("reason.not_applicable");
  const byWhy = reasonText(view.whyCode) ?? whyText(view.whyCode);
  if (byWhy) return byWhy;
  return view.reasonText || view.whySummary || t("reason.generic");
}

function whyLine(view: ChipView): string {
  return whyText(view.whyCode) ?? view.whySummary;
}

function operationPrefix(view: ChipView): string | null {
  switch (view.operation) {
    case "ATTACH_FILE": return t("chip.op.attach");
    case "SELECT_OPTION":
    case "CHECK_RADIO":
    case "OPEN_AND_SELECT": return t("chip.op.select");
    case "SET_CHECKBOX":
    case "SET_CHECKBOXES": return t("chip.op.check");
    default: return null;
  }
}

function displayValue(view: ChipView): string {
  return (view.optionLabel && view.optionLabel.trim() !== "" ? view.optionLabel : view.value).trim();
}

/** What an insertion announcement says about a chip: the field's name and a short form of the value. */
export function chipSummary(view: ChipView): { label: string; value: string } {
  return { label: plainLabel(view.fieldLabel || view.questionText), value: truncate(displayValue(view).replace(/\s+/g, " "), 60) };
}

function isRich(view: ChipView): boolean {
  return view.state === "GENERATED_DRAFT" || view.value.length > 100 || view.value.includes("\n");
}

function staleKey(view: ChipView): StringKey | null {
  return view.staleReason ? (`chip.stale.${view.staleReason.replace("-", "_")}` as StringKey) : null;
}

/** The stale draft's Insert is gone (the logic says so, or the reason is one that always stops it). */
function staleBlocks(view: ChipView): boolean {
  return !!view.staleReason && (view.staleBlocksInsert === true || view.staleReason !== "research");
}

interface Hint { id: string; kbd: string; label: string; dim?: boolean }

function tabHintLabel(view: ChipView): string {
  if (isReady(view)) {
    if (view.conflictsWithCurrent) return t("key.tab.keep");
    return view.insert === "tab" ? t("key.tab.insert") : t("key.tab.next");
  }
  return isBusy(view) ? t("key.tab.drafting") : t("key.tab.next");
}

/** Is the chip's numbered chord the Mac one? The logic names it (`keyHints`); without that the platform decides. */
function macChord(view: ChipView, ctx: UiCtx): boolean {
  const prefix = view.keyHints?.chipActionPrefix;
  return prefix ? prefix === "mac" : ctx.mac;
}

function hintsFor(view: ChipView, ctx: UiCtx, dwell: boolean, undoOffered: boolean): Hint[] {
  const alt = altLabel(ctx.mac);
  const hints: Hint[] = [];
  for (const key of view.keys) {
    if (key === "tab") hints.push({ id: "tab", kbd: "⇥", label: tabHintLabel(view), dim: dwell });
    else if (key === "shift-tab") hints.push({ id: "shift-tab", kbd: "⇧⇥", label: t("key.shiftTab") });
    else if (key === "enter") hints.push({ id: "enter", kbd: "↵", label: t("key.enter.use") });
    else if (key === "esc") hints.push({ id: "esc", kbd: "Esc", label: t("key.esc") });
    else if (key === "regenerate" && view.canRegenerate) hints.push({ id: "regenerate", kbd: `${alt}R`, label: t("key.regenerate") });
  }
  if (undoOffered) hints.push({ id: "undo", kbd: `${alt}Z`, label: t("key.undo") });
  return hints;
}

/**
 * What a screen reader hears is decided per focus change, not per render: the same field announces again only when the
 * KIND of answer changes (a draft that was being written is now ready, a suggestion turned into "needs you", a draft went stale).
 * Typing into the field, the undo window opening or the dwell ending change the view without changing this, so they stay silent.
 */
function announceKind(view: ChipView, tone: VisualTone): string {
  if (view.state === "GENERATING") return isBusy(view) ? "busy" : "failed";
  if (!isReady(view) || tone === "error") return `blocked:${view.state}:${view.reasonCode ?? ""}:${view.skipReason?.code ?? ""}`;
  return `ready:${tone}:${view.staleReason ?? ""}:${view.duplicateBlocksInsert ? "duplicate" : ""}`;
}

function announceText(view: ChipView, tone: VisualTone): string {
  if (view.state === "GENERATING") {
    return isBusy(view) ? `${trustLabel(view, tone)}. ${t("chip.announce.busy")}`
      : t((view.generation?.kind === "local" ? "gen.local" : `gen.${view.generation?.status ?? "FAILED"}`) as StringKey);
  }
  if (!isReady(view) || tone === "error") return t("chip.announce.blocked", { reason: quietReason(view) });
  const lead = t("chip.announce.lead", { trust: trustLabel(view, tone), value: truncate(displayValue(view), 140) });
  const stale = staleKey(view);
  const staleTail = stale ? ` ${t("chip.announce.stale")} ${t(stale)}` : "";
  if (staleBlocks(view)) return `${lead}${staleTail}`;
  if (view.duplicateBlocksInsert) return `${lead} ${t("chip.announce.duplicate")}`;
  if (view.conflictsWithCurrent) return `${lead} ${t("chip.announce.conflict")}${staleTail}`;
  if (view.alreadyInserted) return `${lead} ${t("chip.announce.already")}${staleTail}`;
  if (view.state === "GENERATED_DRAFT") {
    const count = view.generation?.wordCount ?? countWords(view.value);
    return `${lead} ${tn("chip.announce.draft", count)}${staleTail}`;
  }
  return `${lead} ${view.insert === "explicit" ? t("chip.announce.enter") : t("chip.announce.tab")}${staleTail}`;
}

export interface ChipController {
  show(view: ChipView): void;
  moveTo(anchor: Anchor): void;
  hide(): void;
  /** Re-render the current view (language changed). */
  refresh(): void;
  /** Re-place after a viewport change. */
  reflow(): void;
  /** The person is typing into the field: shrink to a one-line pill until the field changes (UX-01). */
  setTyping(on: boolean): void;
  /** An insertion just landed in the field the chip is on: what was typed there is no longer a rival of the suggestion. */
  inserted(): void;
  /** Press the chip's keyboard action number `n` (the chord is Control+Option+n on a Mac, Alt+Shift+n elsewhere). True when that action is on screen and was run. */
  keyAction(n: number): boolean;
  /** The view on screen, or null. */
  current(): ChipView | null;
  destroy(): void;
}

interface Size { w: number; h: number }
interface Cand { box: Rect; maxW: number | null; cost: number }
interface Choice { side: Side; x: number; y: number; cost: number; overlay: boolean }

/** The recommended-option outlines count as options: a chip on top of one hides the very word the chip is recommending. */
function isChoiceOperation(view: ChipView): boolean {
  return view.operation === "CHECK_RADIO" || view.operation === "SET_CHECKBOX" || view.operation === "SET_CHECKBOXES"
    || (view.optionAnchors?.length ?? 0) > 0 || !!view.optionAnchor;
}

/** A chip that is only a value and a key can be folded to one line; a draft, an Undo, a reason or a stale note cannot. */
function canFold(view: ChipView, undoOffered: boolean): boolean {
  if (!isReady(view) || visualTone(view) === "error" || isRich(view) || view.state === "GENERATING") return false;
  if (view.staleReason || view.alreadyInserted || view.duplicateBlocksInsert || view.skipReason) return false;
  // the click-only menu of another document is a chip control: a pill has no room for it
  if (view.alternatives && view.alternatives.length > 0) return false;
  // a protected answer is the one where Tab does NOT insert: the note that says why and the Enter key it asks for stay on screen
  return view.insert !== "explicit" && !undoOffered;
}

export function createChip(ctx: UiCtx, layer: HTMLElement): ChipController {
  const live = h("div", { class: "sr", role: "status", "aria-live": "polite", "aria-atomic": "true", testid: "chip-announce" });
  layer.append(live);
  const options = createOptionMarks(ctx, layer);

  let view: ChipView | null = null;
  let el: HTMLElement | null = null;
  let anchor: Anchor = { x: 0, y: 0, w: 0, h: 0 };
  // neighbours to stay off; they travel with the anchor between two showChip calls (scrolling moves them together)
  let avoidNow: Anchor[] = [];
  let outlinesNow: Anchor[] = [];
  // how many of `avoidNow` the logic named (the outlines of the recommended options follow them)
  let avoidNamed = 0;
  // how many of the first of those are the NEXT fields (`ChipView.avoidNext`): covering them is the worst of the soft costs
  let aheadNow = 0;
  let lastKey = "";
  let lastSig = "";
  let lastAnnounce = "";
  let side: Side | null = null;
  // `compact`: the chip is folded to its one-line pill because nothing else kept it off what must stay visible (see place)
  const local = { expanded: false, sources: false, undoExpired: false, undoKey: "", dwellDone: false, typing: false, compact: false, docs: false };
  // how big the full chip is (measured whenever it is on screen): the folded pill asks whether the full chip would sit cleanly again
  let fullSize: Size | null = null;
  // the chord of the numbered actions of the view being built (the logic names it; else the platform decides)
  let chordMac = ctx.mac;
  // the keyboard actions of the chip on screen: number -> the control that carries it and what it does
  let keyed = new Map<number, { el: HTMLElement; run: () => void }>();
  let cancelDwell: (() => void) | null = null;
  let dwellActive = false;
  let cancelUndo: (() => void) | null = null;
  let cancelAnnounce: (() => void) | null = null;

  function signature(v: ChipView): string {
    return JSON.stringify([
      v.key, v.fieldLabel, v.questionText, v.state, v.insert, v.tone, v.operation, v.value, v.optionLabel, v.whyCode, v.whySummary, v.whyDetail,
      v.sources, v.reasonCode, v.reasonText, v.canRegenerate, v.canDraft, v.review, v.generation, v.conflictsWithCurrent,
      v.undoable && !local.undoExpired, v.dwellRemainingMs > 0, v.keys, v.staleReason, v.staleBlocksInsert, v.skipReason, v.alreadyInserted,
      v.duplicateBlocksInsert, v.keyHints?.chipActionPrefix, v.alternatives, v.documentKind, local.docs, local.expanded, local.sources, local.typing,
      local.compact, ctx.mac,
    ]);
  }

  function clearTimers(): void {
    cancelDwell?.(); cancelDwell = null; dwellActive = false;
    cancelUndo?.(); cancelUndo = null;
    cancelAnnounce?.(); cancelAnnounce = null;
  }

  /** A chip button. `key` is its keyboard action number (printed on it with the chord and announced as its shortcut). */
  function button(label: string, testid: string, cls: string, onClick: () => void, iconName?: IconName, key?: number): HTMLButtonElement {
    const btn = h("button", { type: "button", class: `btn ${cls}`, tabindex: -1, testid }, iconName ? icon(iconName, 14) : null, label);
    ctx.onTrustedClick(btn, onClick);
    if (key !== undefined) bindKey(key, btn, onClick);
    return btn;
  }

  function bindKey(key: number, control: HTMLElement, run: () => void): void {
    keyed.set(key, { el: control, run });
    control.setAttribute("aria-keyshortcuts", chipKeyAria(chordMac, key));
    control.append(h("kbd", { class: "k", "aria-hidden": "true", testid: `key-${key}` }, chipKeyLabel(chordMac, key)));
  }

  function sourcesRow(v: ChipView): HTMLElement | null {
    if (v.sources.length === 0) return null;
    const open = local.sources;
    const visible = open ? v.sources : v.sources.slice(0, INLINE_SOURCES);
    const row = h("div", { class: "row sources", testid: "chip-sources" }, h("span", { class: "muted" }, t("chip.basedOn")));
    let firstLink = true;
    for (const src of visible) {
      const href = safeHref(src.url);
      const label = truncate(src.label || hostOf(src.url) || src.url, 26);
      if (!href) {
        row.append(h("span", { class: "src plain", dir: "auto", testid: "chip-source" }, label));
        continue;
      }
      const link = h("a", {
        class: "src", dir: "auto", href, target: "_blank", rel: "noopener noreferrer", tabindex: -1, testid: "chip-source",
        "aria-label": t("chip.sourceLink", { label: src.label || hostOf(src.url) }),
      }, h("span", { class: "r-label" }, label), icon("external", 11));
      link.addEventListener("click", (ev) => { if (!ev.isTrusted) ev.preventDefault(); });
      if (firstLink) {
        firstLink = false;
        // the first source can be opened from the keyboard: a real window.open from a trusted key press, never a synthetic click
        bindKey(CHIP_KEY.source, link, () => { try { window.open(href, "_blank", "noopener,noreferrer"); } catch { /* blocked */ } });
      }
      row.append(link);
    }
    if (v.sources.length > INLINE_SOURCES) {
      const toggleSources = (): void => { local.sources = !local.sources; rerender(); };
      const more = button(open ? t("chip.lessSources") : t("chip.moreSources", { n: formatNumber(v.sources.length - INLINE_SOURCES) }),
        "chip-sources-toggle", "link", toggleSources, undefined, CHIP_KEY.sources);
      more.setAttribute("aria-expanded", String(open));
      row.append(more);
    }
    return row;
  }

  /**
   * "Use another CV": the OTHER documents Meridian would let this field take, as a menu that only a click opens and a click answers. It is a
   * set of chip buttons (never a Tab stop, no numbered key): the document on offer is the one Tab inserts, always. Choosing a row asks the
   * logic to attach that one instead (`use-cv`); the menu closes with the choice.
   */
  function documentMenu(v: ChipView): HTMLElement | null {
    const others = (v.alternatives ?? []).filter((d) => d && d.id && d.filename).slice(0, 6);
    if (!others.length) return null;
    const letter = v.documentKind === "cover_letter";
    const box = h("div", { class: "row docs", testid: "chip-docs", "data-open": String(local.docs) });
    const toggle = button(letter ? t("cv.use.letter") : t("cv.use"), "chip-docs-toggle", `link${local.docs ? " open" : ""}`, () => { local.docs = !local.docs; rerender(); }, "chevron");
    toggle.setAttribute("aria-expanded", String(local.docs));
    toggle.setAttribute("aria-controls", "chip-doc-list");
    box.append(toggle);
    if (local.docs) {
      const list = h("div", { class: "doc-list", id: "chip-doc-list", role: "group", "aria-label": t("cv.menu"), testid: "chip-doc-list" });
      for (const doc of others) {
        const name = truncate(doc.filename, 48);
        const row = button("", "chip-doc", "ghost small doc", () => {
          local.docs = false;
          ctx.emit({ type: "use-cv", id: doc.id, key: v.key });
          rerender();
        }, "refresh");
        row.setAttribute("aria-label", t("cv.option", { name: doc.filename }));
        row.setAttribute("data-id", doc.id);
        row.append(h("span", { class: "doc-name", dir: "auto" }, name));
        if (doc.version > 0) row.append(h("span", { class: "doc-ver" }, t("cv.version", { n: doc.version })));
        list.append(row);
      }
      box.append(list);
    }
    return box;
  }

  function questionLine(v: ChipView): HTMLElement | null {
    const text = plainLabel(v.questionText);
    return text ? h("div", { class: "question", dir: "auto", testid: "chip-question" }, truncate(text, 160)) : null;
  }

  /** The chip's accessible name: the field it is for, without the page's required mark. */
  function chipName(v: ChipView): string {
    const label = plainLabel(v.fieldLabel);
    return label ? t("chip.aria", { label: truncate(label, 80) }) : t("chip.ariaNoLabel");
  }

  /**
   * One line: the suggestion (or its trust word) without covering the neighbours. While the person types it keeps Replace; folded for
   * lack of room (`compact`) it also names the key that inserts it, because the line that said so is gone.
   */
  function buildPill(v: ChipView, tone: VisualTone, ready: boolean, compact: boolean): HTMLElement {
    const root = h("div", {
      class: `chip pill-mode tone-${tone}`, role: "group", testid: "chip", "data-pill": "true", "data-compact": String(compact), "aria-label": chipName(v),
      "data-state": v.state, "data-tone": tone, "data-insert": v.insert, "data-rich": "false", "data-quiet": String(!ready),
      "data-conflict": String(v.conflictsWithCurrent), "data-key": v.key,
    });
    const value = ready ? truncate(displayValue(v).replace(/\s+/g, " "), compact ? 40 : 36) : trustLabel(v, tone);
    // a verified answer needs no caption; an answer to CHECK says so before the value
    const caption = compact && ready && tone === "inferred" ? h("span", { class: "pill-trust" }, trustLabel(v, tone)) : null;
    root.append(
      h("span", { class: "qicon", "aria-hidden": "true" }, icon(TONE_ICON[tone], 14)),
      // the trust word is always in the pill, for the screen reader and for whatever looks for it, even where there is no room to draw it
      ...(ready ? [h("span", { class: "sr", testid: "chip-trust" }, trustLabel(v, tone))] : []),
      h("span", { class: "pill-text", dir: "auto", testid: "chip-pill-text" }, caption, h("span", { class: "pill-value" }, value)),
    );
    if (ready && v.conflictsWithCurrent && v.insert !== "never" && !staleBlocks(v)) {
      root.append(button(t("btn.replace"), "chip-replace", "ghost small", () => ctx.emit({ type: "replace", key: v.key }), undefined, CHIP_KEY.replace));
    } else if (compact && ready && v.insert !== "never") {
      // the key that inserts it, and a button for the pointer (the full chip's Insert is gone with the rest of it)
      root.append(h("span", { class: "hint pill-hint", "data-hint": "tab", testid: "hint-tab" }, h("kbd", { class: "glyph pill-key", "aria-hidden": "true", testid: "chip-pill-key" }, "⇥")),
        button(t("btn.insert"), "chip-accept", "primary small", () => ctx.emit({ type: "accept", key: v.key })));
    }
    return root;
  }

  function build(v: ChipView): { root: HTMLElement; valueEl: HTMLElement | null; toggle: HTMLButtonElement | null; meta: HTMLElement | null } {
    keyed = new Map();
    chordMac = macChord(v, ctx);
    const tone = visualTone(v);
    const skip = skipCopy(v);
    const ready = isReady(v) && tone !== "error";
    const generating = v.state === "GENERATING";
    const busy = isBusy(v);
    const quiet = !ready && !generating;
    const undoOffered = v.undoable && !local.undoExpired;
    if (!generating && (local.typing || (local.compact && canFold(v, undoOffered)))) {
      return { root: buildPill(v, tone, ready, !local.typing), valueEl: null, toggle: null, meta: null };
    }
    const rich = ready && isRich(v);
    const dwell = ready && v.dwellRemainingMs > 0 && !local.dwellDone;
    const root = h("div", {
      class: `chip tone-${tone}${quiet ? " quiet" : ""}${rich ? " rich" : ""}${skip ? " skip" : ""}${v.staleReason ? " stale-draft" : ""}`, role: "group", testid: "chip",
      "aria-label": chipName(v),
      "data-state": v.state, "data-tone": tone, "data-insert": v.insert, "data-rich": String(rich), "data-quiet": String(quiet),
      "data-conflict": String(v.conflictsWithCurrent), "data-key": v.key,
    });

    const dismiss = h("button", { type: "button", class: "x", tabindex: -1, "aria-label": t("btn.dismiss"), title: t("btn.dismiss"), testid: "chip-dismiss" }, icon("close", 14));
    ctx.onTrustedClick(dismiss, () => ctx.emit({ type: "dismiss", key: v.key }));

    // ---- quiet one-liner: NEEDS_YOU, consent, sensitive, not applicable, errors, a skipped file field
    if (quiet) {
      const badgeIcon = h("span", { class: "qicon", "aria-hidden": "true" }, icon(TONE_ICON[tone], 15));
      const text = h("div", { class: "qtext" },
        questionLine(v),
        h("span", { class: "qlabel", testid: "chip-label" }, trustLabel(v, tone)),
        h("span", { class: "qreason", dir: "auto", testid: "chip-reason" }, quietReason(v)));
      if (skip && (v.skipReason?.code === "file_unapproved" || v.skipReason?.code === "file_none")) {
        text.append(button(t("calm.openMeridian"), "chip-open-meridian", "ghost small", () => ctx.emit({ type: "open-meridian" })));
      }
      root.append(badgeIcon, text);
      if (v.canDraft) root.append(button(t("btn.draft"), "chip-draft", "primary", () => ctx.emit({ type: "draft", key: v.key }), "sparkle"));
      root.append(dismiss);
      return { root, valueEl: null, toggle: null, meta: null };
    }

    // ---- the question this suggestion answers, then the trust badge + why
    const question = questionLine(v);
    if (question) root.append(question);
    const badge = h("span", { class: "badge", testid: "chip-badge" },
      busy ? h("span", { class: "pulse", "aria-hidden": "true" }) : icon(TONE_ICON[tone], 14),
      h("span", { class: "badge-label", testid: "chip-trust" }, trustLabel(v, tone)));
    const head = h("div", { class: "row head" }, badge);
    if (undoOffered) head.append(h("span", { class: "pill ok", testid: "chip-inserted" }, icon("check", 12), t("chip.inserted")));
    if (ready) head.append(h("span", { class: "why", dir: "auto", testid: "chip-why" }, whyLine(v)));
    const metaRowRef: { el: HTMLElement | null } = { el: null };
    head.append(dismiss);
    root.append(head);

    // ---- body
    let valueEl: HTMLElement | null = null;
    let toggle: HTMLButtonElement | null = null;
    if (generating) {
      const status = v.generation?.status ?? "GENERATING";
      const key = (v.generation?.kind === "local" ? "gen.local" : `gen.${status}`) as StringKey;
      const attempt = v.generation?.attempt && v.generation.attempt > 1 ? ` ${t("gen.attempt", { n: v.generation.attempt })}` : "";
      root.append(h("div", { class: "value muted", testid: "chip-status" }, `${t(key)}${attempt}`));
    } else {
      const prefix = operationPrefix(v);
      valueEl = h("div", { class: `value${rich ? " preview" : ""}${local.expanded ? " open" : ""}`, dir: "auto", testid: "chip-value" },
        prefix ? h("span", { class: "op" }, prefix) : null, displayValue(v));
      if (rich && local.expanded) valueEl.setAttribute("tabindex", "-1");
      root.append(valueEl);
      if (rich) {
        const words = v.state === "GENERATED_DRAFT" ? (v.generation?.wordCount ?? countWords(v.value)) : 0;
        const meta = h("div", { class: "row meta muted", testid: "chip-meta" }, words > 0 ? h("span", { class: "words" }, tn("chip.words", words)) : null);
        toggle = button(local.expanded ? t("btn.showLess") : t("btn.showMore"), "chip-more", "link push", () => { local.expanded = !local.expanded; rerender(); }, undefined, CHIP_KEY.more);
        toggle.setAttribute("aria-expanded", String(local.expanded));
        toggle.classList.add("is-hidden");
        meta.append(toggle);
        if (words === 0) meta.classList.add("is-hidden");
        metaRowRef.el = meta;
        root.append(meta);
        if (v.whyDetail) root.append(h("div", { class: `detail muted${local.expanded ? " wrap" : ""}`, dir: "auto", testid: "chip-detail" }, truncate(v.whyDetail, 240)));
      }
      const stale = staleKey(v);
      if (stale) root.append(h("div", { class: "note warn", testid: "chip-stale", "data-reason": v.staleReason ?? "" }, icon("alert", 13), t(stale)));
      if (v.duplicateBlocksInsert) root.append(h("div", { class: "note warn", testid: "chip-duplicate" }, icon("alert", 13), t("reason.duplicate")));
      else if (v.conflictsWithCurrent) root.append(h("div", { class: "note warn", testid: "chip-conflict" }, icon("alert", 13), t("chip.conflict")));
      else if (v.alreadyInserted) root.append(h("div", { class: "note ok", testid: "chip-already" }, icon("check", 13), t("chip.alreadyInserted")));
      else if (v.insert === "explicit" && v.readIn) root.append(h("div", { class: "note", testid: "chip-read-in" }, icon("eye", 13), t("reason.readInReady")));
      else if (v.insert === "explicit") root.append(h("div", { class: "note", testid: "chip-protected" }, icon("lock", 13), t("reason.protectedReady")));
      else if (v.review && v.state === "VERIFIED") root.append(h("div", { class: "note", testid: "chip-review" }, icon("eye", 13), t("chip.review")));
    }

    // ---- sources + regenerate (drafts)
    if (ready) {
      const src = sourcesRow(v);
      if (src) root.append(src);
    }
    if (ready && v.canRegenerate && v.state === "GENERATED_DRAFT") {
      const group = h("div", { class: "row regen", role: "group", "aria-label": t("regen.label"), testid: "chip-regen" });
      const instructions: ["again" | "shorter" | "longer" | "different", StringKey, IconName | undefined, number | undefined][] = [
        ["again", "regen.again", "refresh", undefined], ["shorter", "regen.shorter", undefined, CHIP_KEY.shorter],
        ["longer", "regen.longer", undefined, CHIP_KEY.longer], ["different", "regen.different", undefined, CHIP_KEY.different],
      ];
      for (const [instruction, labelKey, ic, key] of instructions) {
        group.append(button(t(labelKey), `chip-regen-${instruction}`, "ghost small", () => ctx.emit({ type: "regenerate", key: v.key, instruction }), ic, key));
      }
      root.append(group);
    }

    // ---- another document (a file field with more than one approved document to offer): click only, never what Tab inserts
    if (ready && v.alternatives?.length && v.insert !== "never" && !staleBlocks(v) && !v.duplicateBlocksInsert) {
      const docs = documentMenu(v);
      if (docs) root.append(docs);
    }

    // ---- foot: key hints + primary action
    const foot = h("div", { class: "row foot" });
    const hintBox = h("div", { class: "hints", testid: "chip-hints" });
    for (const hint of hintsFor(v, ctx, dwell, undoOffered)) {
      hintBox.append(h("span", { class: `hint${hint.dim ? " dim" : ""}`, "data-hint": hint.id, testid: `hint-${hint.id}` }, h("kbd", { class: /^[⇥⇧↵]+$/.test(hint.kbd) ? "glyph" : null }, hint.kbd), hint.label));
    }
    foot.append(hintBox);
    const actions = h("div", { class: "actions" });
    const blocked = staleBlocks(v);
    if (undoOffered) actions.append(button(t("btn.undo"), "chip-undo", "ghost", () => ctx.emit({ type: "undo", key: v.key }), "undo"));
    if (ready && v.staleReason) {
      const label = v.staleReason === "profile" ? t("btn.rewriteProfile") : t("btn.rewriteNew");
      actions.append(button(label, "chip-rewrite", blocked ? "primary" : "ghost", () => ctx.emit({ type: "rewrite", key: v.key }), "refresh"));
    }
    if (ready && v.insert !== "never" && !undoOffered && !v.alreadyInserted && !blocked && !v.duplicateBlocksInsert) {
      if (v.conflictsWithCurrent) actions.append(button(t("btn.replace"), "chip-replace", "primary", () => ctx.emit({ type: "replace", key: v.key }), undefined, CHIP_KEY.replace));
      else if (v.insert === "explicit") actions.append(button(t("btn.use"), "chip-accept", "primary", () => ctx.emit({ type: "accept", key: v.key })));
      else actions.append(button(t("btn.insert"), "chip-accept", "primary", () => ctx.emit({ type: "accept", key: v.key })));
    } else if (!ready && !busy && v.canDraft) {
      actions.append(button(t("btn.draft"), "chip-draft", "primary", () => ctx.emit({ type: "draft", key: v.key }), "sparkle"));
    }
    if (actions.childElementCount > 0) foot.append(actions);
    if (foot.childElementCount > 0) root.append(foot);
    if (dwell) root.append(h("div", { class: "dwell", testid: "chip-dwell", "aria-hidden": "true" }, h("span", { class: "dwell-bar" })));
    return { root, valueEl, toggle, meta: metaRowRef.el };
  }

  function startDwell(v: ChipView): void {
    if (!el) return;
    const ms = v.dwellRemainingMs;
    const bar = el.querySelector<HTMLElement>(".dwell-bar");
    if (ms <= 0 || !bar) return;
    bar.style.setProperty("animation-duration", `${Math.round(ms)}ms`);
    dwellActive = true;
    cancelDwell?.();
    cancelDwell = ctx.later(() => {
      dwellActive = false;
      cancelDwell = null;
      local.dwellDone = true;
      el?.querySelectorAll(".hint.dim").forEach((node) => node.classList.remove("dim"));
      el?.querySelector(".dwell")?.classList.add("is-hidden");
    }, ms);
  }

  function rerender(): void {
    if (!view) return;
    render(view, true);
    lastSig = signature(view);
    place(true);
  }

  function render(v: ChipView, keepFade: boolean): void {
    const wasVisible = !!el && !el.classList.contains("is-hidden");
    const { root, valueEl, toggle, meta } = build(v);
    if (el) el.replaceWith(root);
    else layer.prepend(root);
    el = root;
    root.addEventListener("mousedown", (ev) => ev.preventDefault());
    if (!wasVisible && !keepFade) root.classList.add("enter");
    root.addEventListener("animationend", () => root.classList.remove("enter"), { once: true });
    // measure overflow of the collapsed preview once the chip has its final width
    root.style.maxWidth = `${maxWidthFor(root)}px`;
    if (valueEl && toggle && !local.expanded) {
      if (valueEl.scrollHeight > valueEl.clientHeight + 1) { toggle.classList.remove("is-hidden"); meta?.classList.remove("is-hidden"); }
    } else if (valueEl && toggle && local.expanded) {
      toggle.classList.remove("is-hidden");
      meta?.classList.remove("is-hidden");
      if (valueEl.scrollHeight > valueEl.clientHeight + 1) {
        // fade the cut-off last line until the reader reaches the end
        valueEl.classList.add("scrolls");
        valueEl.addEventListener("scroll", () => {
          valueEl.classList.toggle("scrolls", valueEl.scrollTop + valueEl.clientHeight < valueEl.scrollHeight - 2);
        }, { passive: true });
      }
    }
    if (v.dwellRemainingMs > 0 && isReady(v) && !local.dwellDone) {
      if (!dwellActive || v.key !== lastKey) startDwell(v);
      else root.querySelectorAll(".hint[data-hint=tab]").forEach((n) => n.classList.add("dim"));
    }
  }

  function maxWidthFor(chipEl: HTMLElement): number {
    const vp = viewport();
    const wide = chipEl.classList.contains("quiet") || chipEl.classList.contains("rich");
    const cap = chipEl.classList.contains("pill-mode") ? MAX_WIDTH_PILL : wide ? MAX_WIDTH_WIDE : MAX_WIDTH;
    return Math.max(160, Math.min(cap, vp.w - 2 * MARGIN));
  }

  /** Where each option outline goes: the rectangles the logic named, while a suggestion can still be inserted. */
  function optionRects(v: ChipView): Anchor[] {
    if (!isReady(v) || v.tone === "error" || v.undoable || v.alreadyInserted || staleBlocks(v)) return [];
    const list = v.optionAnchors && v.optionAnchors.length > 0 ? v.optionAnchors : v.optionAnchor ? [v.optionAnchor] : [];
    return list.filter((r) => r.w > 0 && r.h > 0);
  }

  /**
   * Put the chip where it covers the least of what must stay readable, folding it to one line when no place is clean enough.
   *
   * What must stay readable, worst to least bad: the options of the focused choice group and the outline of the recommended one (those
   * the person has to read to check the suggestion: never covered while any side leaves them alone), the panel and the open prompts
   * (six times a field), the neighbouring fields, the previous field's value and the focused field's own label. `attempt` is true when
   * the chip was just built or the window changed: only then may a full chip be folded, so that scrolling never makes it flicker.
   */
  function place(attempt = false): void {
    if (!el || !view) return;
    const vp = viewport();
    const offscreen = anchor.y + anchor.h < 0 || anchor.y > vp.h || anchor.x + anchor.w < 0 || anchor.x > vp.w;
    el.style.visibility = offscreen ? "hidden" : "visible";
    if (offscreen) return;
    const rtl = ctx.rtl();
    const obstacles = ctx.obstacles();
    const isLevel = (r: Rect): boolean => Math.min(r.y + r.h, anchor.y + anchor.h) - Math.max(r.y, anchor.y) > LEVEL_PX;
    const level = avoidNow.filter(isLevel);
    // What the chip must never lie on. For a radio / checkbox group: its options, on every side. For any other field, what is level with it is
    // a neighbour of the same row (or the label that wraps the control, which reaches over the whole row): beside the field is no place for the
    // chip then, but a chip above or below it merely keeps off those as far as it can.
    const group = isChoiceOperation(view);
    const hardFor = (s: Side): Rect[] => (group || s === "beside" ? [...level, ...outlinesNow] : outlinesNow);
    // a radio / checkbox group whose options the logic did not name (an outline is only the control): the labels beside the controls are
    // unknown, so not beside them
    const beside = !isChoiceOperation(view) || avoidNow.slice(0, avoidNamed).some(isLevel);
    const below = vp.h - MARGIN - (anchor.y + anchor.h + GAP);
    const above = anchor.y - GAP - MARGIN;
    const room = (s: "below" | "above"): number => (s === "below" ? below : above);
    // "beside" starts after the field. For a radio / checkbox group it starts after its printed options too (their labels run on past the
    // 13 px controls the field is made of); a neighbour in the same row is not a part of this field: beside it is simply not possible
    const edge = !group ? (rtl ? anchor.x : anchor.x + anchor.w)
      : rtl ? Math.min(anchor.x, ...level.map((r) => r.x)) : Math.max(anchor.x + anchor.w, ...level.map((r) => r.x + r.w));
    const besideSpace = beside ? (rtl ? edge - GAP - MARGIN : vp.w - MARGIN - (edge + GAP)) : 0;
    const covers = (a: Rect, b: Rect): boolean => Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) > COVER_TOLERANCE_PX && Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > COVER_TOLERANCE_PX;
    const cost = (box: Rect, s: Side): number => {
      let sum = 0;
      let ahead = 0;
      avoidNow.forEach((other, i) => {
        const area = overlapArea(box, other);
        sum += area;
        if (i < aheadNow) ahead += area;
      });
      let options = 0;
      for (const other of hardFor(s)) if (covers(box, other)) options += overlapArea(box, other);
      for (const other of obstacles) sum += OBSTACLE_WEIGHT * overlapArea(box, other);
      if (options > 0) return HARD_COST + options + sum;
      return ahead >= FOLD_MIN_COST ? AHEAD_COST + sum : sum;
    };
    // Where a chip of this size would sit on a side, or null when it does not fit there. It lines up with the start edge of the field
    // (left on a left-to-right page, right on a right-to-left one); "beside" is on its end side, level with the field.
    const boxFor = (s: "below" | "above", w: number, h: number): Rect | null => {
      if (h > room(s)) return null;
      return { x: clamp(rtl ? anchor.x + anchor.w - w : anchor.x, MARGIN, vp.w - w - MARGIN), y: s === "below" ? anchor.y + anchor.h + GAP : anchor.y - GAP - h, w, h };
    };
    // Beside the field: top-aligned first; or slid up or down so that its edge stops at a neighbour (the next field, the one above), as long as
    // it stays level with part of the field.
    const besideBoxes = (w: number, h: number): Rect[] => {
      if (w > besideSpace || h > vp.h - 2 * MARGIN) return [];
      const x = rtl ? edge - GAP - w : edge + GAP;
      const tops = [anchor.y, anchor.y + anchor.h - h];
      for (const other of avoidNow) tops.push(other.y - GAP - h, other.y + other.h + GAP);
      const seen = new Set<number>();
      const boxes: Rect[] = [];
      for (const top of tops) {
        const y = clamp(top, MARGIN, vp.h - h - MARGIN);
        if (seen.has(Math.round(y))) continue;
        seen.add(Math.round(y));
        if (y > anchor.y + anchor.h - 4 || y + h < anchor.y + 4) continue;
        boxes.push({ x, y, w, h });
      }
      return boxes;
    };
    const bestBox = (s: Side, w: number, h: number): { box: Rect; cost: number } | null => {
      const boxes = s === "beside" ? besideBoxes(w, h) : [boxFor(s, w, h)].filter((b): b is Rect => b !== null);
      let best: { box: Rect; cost: number } | null = null;
      for (const box of boxes) {
        const c = cost(box, s);
        if (!best || c + 1 < best.cost) best = { box, cost: c };
      }
      return best;
    };
    // How wide a chip in this box may be before it runs into the panel or a prompt that shares its rows (null: nothing in the way).
    const clearWidth = (box: Rect): number | null => {
      let wall: number | null = null;
      for (const o of obstacles) {
        if (o.y >= box.y + box.h || o.y + o.h <= box.y) continue;
        if (!rtl && o.x > box.x + MIN_KEEP_PX && (wall === null || o.x < wall)) wall = o.x;
        if (rtl && o.x + o.w < box.x + box.w - MIN_KEEP_PX && (wall === null || o.x + o.w > wall)) wall = o.x + o.w;
      }
      if (wall === null) return null;
      return rtl ? box.x + box.w - wall - GAP : wall - GAP - box.x;
    };
    // A side at the chip's own width and, when that covers the panel or does not reach beside the field, at a narrower one (a long
    // draft is never narrowed to fit beside a field: it goes above or below instead).
    const consider = (s: Side, size: Size, measure: ((maxW: number) => Size) | null, rich: boolean, minNarrow: number): Cand | null => {
      const natural = bestBox(s, size.w, size.h);
      let best: Cand | null = natural ? { box: natural.box, maxW: null, cost: natural.cost } : null;
      if (s === "beside" && rich && size.w > besideSpace) return best;
      const probe = natural?.box ?? (s === "beside" ? bestBox(s, Math.min(size.w, besideSpace), size.h)?.box ?? null : null);
      if (!measure || !probe || (best && best.cost === 0)) return best;
      const wall = clearWidth(probe);
      const limit = Math.min(s === "beside" ? besideSpace : Infinity, wall ?? Infinity);
      if (limit < size.w && limit >= minNarrow) {
        const narrower = measure(limit);
        const narrowed = bestBox(s, narrower.w, narrower.h);
        if (narrowed) {
          const candidate = { box: narrowed.box, maxW: Math.floor(limit), cost: narrowed.cost };
          if (!best || candidate.cost + 1 < best.cost) best = candidate;
        }
      }
      return best;
    };
    const candidatesFor = (size: Size, measure: ((maxW: number) => Size) | null, rich: boolean, minNarrow: number): Map<Side, Cand> => {
      const found = new Map<Side, Cand>();
      for (const s of ["below", "above", "beside"] as const) {
        const c = consider(s, size, measure, rich, minNarrow);
        if (c) found.set(s, c);
      }
      return found;
    };
    // the preferred side wins unless another fits AND covers clearly less of what must stay visible
    const pick = (found: Map<Side, Cand>): Side | undefined => {
      const preferred: Side = side ?? view!.placement;
      const order: Side[] = preferred === "beside" ? ["beside", "below", "above"] : preferred === "below" ? ["below", "above", "beside"] : ["above", "below", "beside"];
      let chosen: Side | undefined;
      for (const s of order) {
        const c = found.get(s);
        if (!c) continue;
        const held = chosen === undefined ? Infinity : (found.get(chosen)?.cost ?? Infinity);
        if (chosen === undefined || c.cost + 1 < held * (1 - CLEARLY_LESS)) chosen = s;
      }
      return chosen;
    };

    // ---- a folded pill: unfold when the full chip would sit cleanly again (the window grew, the field scrolled clear of the panel)
    if (local.compact && fullSize) {
      const found = candidatesFor(fullSize, null, false, BESIDE_MIN_WIDTH);
      const chosen = pick(found);
      if (chosen !== undefined && (found.get(chosen)?.cost ?? 1) === 0) {
        local.compact = false;
        render(view, true);
        lastSig = signature(view);
        place(false); // it was folded for lack of room a moment ago: this is the unfolding, not a new attempt to fold
        return;
      }
    }

    /** Place `node` (a full chip or a pill) as well as the room allows, leaving its size classes and limits set. */
    const settle = (node: HTMLElement): Choice => {
      const isPill = node.classList.contains("pill-mode");
      const naturalMax = maxWidthFor(node);
      node.style.maxWidth = `${naturalMax}px`;
      node.style.maxHeight = "";
      node.classList.remove("tight");
      let rect = node.getBoundingClientRect();
      const rich = node.classList.contains("rich");
      const measure = (maxW: number): Size => {
        node.style.maxWidth = `${Math.floor(maxW)}px`;
        const measured = node.getBoundingClientRect();
        node.style.maxWidth = `${naturalMax}px`;
        return { w: measured.width, h: measured.height };
      };
      if (!isPill) fullSize = { w: rect.width, h: rect.height };
      const found = candidatesFor({ w: rect.width, h: rect.height }, measure, rich, isPill ? COMPACT_MIN_WIDTH : rich ? RICH_MIN_WIDTH : BESIDE_MIN_WIDTH);
      const chosen = pick(found);
      if (chosen) {
        const picked = found.get(chosen) as Cand;
        if (picked.maxW !== null) node.style.maxWidth = `${picked.maxW}px`;
        return { side: chosen, x: picked.box.x, y: picked.box.y, cost: picked.cost, overlay: false };
      }
      // Neither side has room for the whole chip. Compact it; if that fits a side, done. A draft is worth reading, so when
      // only clipping would fit it the chip covers the field instead (a tiny viewport: 400% zoom, a short iframe).
      const fallback: Side = room("below") >= room("above") ? "below" : "above";
      const full = rect.height;
      node.classList.add("tight");
      const compact = node.getBoundingClientRect().height;
      const inside = vp.h - 2 * MARGIN;
      let overlay = false;
      if (compact <= room(fallback)) {
        /* fits once compacted */
      } else if (room(fallback) >= MIN_SPACE && !(rich && compact <= inside)) {
        node.style.maxHeight = `${Math.floor(room(fallback))}px`;
      } else {
        overlay = true; // as close to the field's edge as possible, but fully on screen
        if (full <= inside) node.classList.remove("tight");
        else if (compact > inside) node.style.maxHeight = `${Math.floor(inside)}px`;
      }
      rect = node.getBoundingClientRect();
      const x = clamp(rtl ? anchor.x + anchor.w - rect.width : anchor.x, MARGIN, vp.w - rect.width - MARGIN);
      let y = overlay ? anchor.y + anchor.h - rect.height - GAP : fallback === "below" ? anchor.y + anchor.h + GAP : anchor.y - GAP - rect.height;
      y = clamp(y, MARGIN, vp.h - rect.height - MARGIN);
      return { side: fallback, x, y, cost: Infinity, overlay };
    };
    const apply = (node: HTMLElement, choice: Choice): void => {
      side = choice.side;
      node.style.left = `${Math.round(choice.x)}px`;
      node.style.top = `${Math.round(choice.y)}px`;
      node.setAttribute("data-placement", choice.overlay ? "overlay" : choice.side);
    };

    let choice = settle(el);
    // ---- nothing clean for the full chip: try it as one line, and keep that when it covers clearly less
    if (attempt && !local.compact && !local.typing && choice.cost >= FOLD_MIN_COST && Number.isFinite(choice.cost) && canFold(view, view.undoable && !local.undoExpired)) {
      local.compact = true;
      render(view, true);
      lastSig = signature(view);
      const folded = settle(el as HTMLElement);
      if (folded.cost + 1 < choice.cost) {
        apply(el as HTMLElement, folded);
        return;
      }
      local.compact = false;
      render(view, true);
      lastSig = signature(view);
      choice = settle(el as HTMLElement);
    }
    apply(el, choice);
  }

  function announce(v: ChipView): void {
    cancelAnnounce?.();
    const tone = visualTone(v);
    const kind = `${v.key}|${announceKind(v, tone)}`;
    if (kind === lastAnnounce) return;
    cancelAnnounce = ctx.later(() => {
      lastAnnounce = kind;
      live.textContent = "";
      live.textContent = announceText(v, tone);
    }, ANNOUNCE_DELAY_MS);
  }

  function controlVisible(control: HTMLElement): boolean {
    if (!el || el.classList.contains("is-hidden") || !control.isConnected || !el.contains(control)) return false;
    if (getComputedStyle(el).visibility === "hidden") return false;
    return control.getClientRects().length > 0 && getComputedStyle(control).visibility !== "hidden";
  }

  return {
    show(next: ChipView): void {
      // a skipped field is silent unless the logic gave one calm sentence for it (an unapproved CV, a cover letter upload)
      if (next.state === "SKIP" && !next.skipReason) { this.hide(); return; }
      const keyChanged = next.key !== lastKey;
      if (keyChanged) {
        local.expanded = false; local.sources = false; local.docs = false; local.dwellDone = false; local.typing = false; local.compact = false; fullSize = null; side = null; clearTimers(); lastAnnounce = "";
      }
      if (next.dwellRemainingMs <= 0) local.dwellDone = false;
      // An insertion ends the typing: the answer the person typed was replaced (or matched), the field is the suggestion's again and the
      // Undo that comes with it must be on screen, not a one-line pill that never shows it.
      if ((next.undoable && !view?.undoable) || next.alreadyInserted) local.typing = false;
      if (next.undoable && (local.undoKey !== next.key || keyChanged)) {
        local.undoKey = next.key;
        local.undoExpired = false;
        cancelUndo?.();
        cancelUndo = ctx.later(() => { local.undoExpired = true; rerender(); }, UNDO_MS);
      } else if (!next.undoable) {
        local.undoKey = "";
        local.undoExpired = false;
        cancelUndo?.(); cancelUndo = null;
      }
      view = next;
      anchor = next.anchor;
      const outlines = optionRects(next);
      options.set(outlines);
      outlinesNow = outlines.map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h }));
      // the highlighted options are as worth keeping clear as the neighbouring fields
      const named = (next.avoid ?? []).slice(0, 12);
      avoidNamed = named.length;
      aheadNow = Math.max(0, Math.min(Math.floor(next.avoidNext ?? 0), named.length));
      avoidNow = [...named, ...outlines].map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h }));
      let sig = signature(next);
      const hidden = !el || el.classList.contains("is-hidden");
      const fresh = hidden || sig !== lastSig;
      if (fresh) {
        // a changed view is placed afresh: it may fit where the folded one did not, and the other way round
        if (!hidden && !keyChanged && !local.typing) local.compact = false;
        sig = signature(next);
        render(next, false);
        el?.classList.remove("is-hidden");
        lastSig = sig;
      }
      lastKey = next.key;
      place(fresh);
      announce(next);
    },
    moveTo(next: Anchor): void {
      const dx = next.x - anchor.x;
      const dy = next.y - anchor.y;
      if (dx !== 0 || dy !== 0) {
        avoidNow = avoidNow.map((r) => ({ ...r, x: r.x + dx, y: r.y + dy }));
        outlinesNow = outlinesNow.map((r) => ({ ...r, x: r.x + dx, y: r.y + dy }));
        options.shift(dx, dy);
      }
      anchor = next;
      place();
    },
    hide(): void {
      clearTimers();
      view = null; lastKey = ""; lastSig = ""; lastAnnounce = ""; side = null; avoidNow = []; outlinesNow = []; avoidNamed = 0; aheadNow = 0; fullSize = null;
      local.expanded = false; local.sources = false; local.docs = false; local.undoKey = ""; local.undoExpired = false; local.dwellDone = false; local.typing = false; local.compact = false;
      keyed = new Map();
      options.clear();
      el?.classList.add("is-hidden");
      live.textContent = "";
    },
    refresh(): void {
      if (view) { lastSig = ""; local.compact = false; render(view, true); place(true); }
    },
    reflow(): void {
      place(true);
    },
    setTyping(on: boolean): void {
      if (!view || local.typing === on) return;
      local.typing = on;
      rerender();
    },
    inserted(): void {
      if (!view || !local.typing) return;
      local.typing = false;
      rerender();
    },
    keyAction(n: number): boolean {
      const entry = keyed.get(n);
      if (!entry || !view || !controlVisible(entry.el)) return false;
      entry.run();
      return true;
    },
    current(): ChipView | null {
      // the anchor moves with the page between two views (moveTo), so the live one is what the field is at now
      return view ? { ...view, anchor } : null;
    },
    destroy(): void {
      clearTimers();
      options.destroy();
      el?.remove(); live.remove();
      el = null; view = null;
    },
  };
}
