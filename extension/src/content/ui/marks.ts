// Persistent markers: a small badge on the corner of a field that holds an inserted INFERRED ("check this") or drafted
// answer, kept until the logic layer stops listing it (the user touched the field).
//
// The logic layer re-sends the complete set with fresh anchors on every scroll/resize frame; nodes are reused by key, so
// that is a handful of style writes. The badge sits on the field's end-side top corner, where it covers the border and a
// few pixels of padding but not the text (the same spot the brief insertion flash uses, so the flash settles into it).
// It is decoration with a name: not focusable, never in the tab order, and click-through.

import { t } from "../i18n";
import type { FieldMark } from "../ui-api";
import { viewport, type Rect, type UiCtx } from "./ctx";
import { TONE_ICON } from "./chip";
import { clamp, h, icon, plainLabel, truncate } from "./dom";

export const MARK_SIZE = 20;
const OVERHANG = 8; // how far the badge reaches past the field's end edge
const INSIDE = 7; // how far it reaches into the field from the top edge
const EDGE = 2; // never closer than this to the viewport edge
const MAX_MARKS = 60;

export type MarkPlacement = "corner" | "side" | "below" | "inset";

/** Where the badge goes for a field. Pure geometry: the tests check it against real layout. */
export function markPlacement(anchor: Rect, vp: { w: number; h: number }, rtl: boolean): { x: number; y: number; placement: MarkPlacement } {
  const maxX = vp.w - MARK_SIZE - EDGE;
  const maxY = vp.h - MARK_SIZE - EDGE;
  const endX = (gap: number): number => (rtl ? anchor.x - MARK_SIZE - gap : anchor.x + anchor.w + gap);
  // very small fields (a checkbox): the badge goes beside the control instead of on top of it
  if (anchor.h < 24 || anchor.w < 24) {
    const x = endX(4);
    if (x >= EDGE && x <= maxX) return { x, y: clamp(anchor.y + anchor.h / 2 - MARK_SIZE / 2, EDGE, maxY), placement: "side" };
  }
  const cornerX = rtl ? anchor.x - OVERHANG : anchor.x + anchor.w + OVERHANG - MARK_SIZE;
  const x = clamp(cornerX, EDGE, maxX);
  const top = anchor.y + INSIDE - MARK_SIZE;
  if (top >= EDGE) return { x, y: Math.min(top, maxY), placement: "corner" };
  // no room above the field (it touches the top of the viewport): straddle the bottom edge instead
  const bottom = anchor.y + anchor.h - INSIDE;
  if (bottom + MARK_SIZE <= vp.h - EDGE) return { x, y: bottom, placement: "below" };
  return { x, y: clamp(anchor.y + EDGE, EDGE, maxY), placement: "inset" };
}

function toneOf(mark: FieldMark): FieldMark["tone"] {
  return Object.prototype.hasOwnProperty.call(TONE_ICON, mark.tone) ? mark.tone : "neutral";
}

function nameFor(mark: FieldMark): string {
  const label = plainLabel(mark.label);
  const tone = toneOf(mark);
  const base = tone === "inferred" ? "inferred" : tone === "draft" ? "draft" : "generic";
  const shown = truncate(label, 60);
  if (base === "inferred") return label ? t("mark.inferred.label", { label: shown }) : t("mark.inferred");
  if (base === "draft") return label ? t("mark.draft.label", { label: shown }) : t("mark.draft");
  return label ? t("mark.generic.label", { label: shown }) : t("mark.generic");
}

export interface MarkController {
  set(marks: FieldMark[]): void;
  /** Re-label after a language change. */
  refresh(): void;
  /** Re-place after a viewport change. */
  reflow(): void;
  destroy(): void;
}

export function createMarks(ctx: UiCtx, layer: HTMLElement): MarkController {
  const nodes = new Map<string, { el: HTMLElement; tone: string; name: string; x: number; y: number; hidden: boolean; placement: string }>();
  let current: FieldMark[] = [];
  let layerName = "";

  function paint(mark: FieldMark, vp: { w: number; h: number }, rtl: boolean): void {
    const a = mark.anchor;
    const offscreen = !(a.w > 0 && a.h > 0) || a.y + a.h < 0 || a.y > vp.h || a.x + a.w < 0 || a.x > vp.w;
    let entry = nodes.get(mark.key);
    if (!entry) {
      const el = h("div", { class: "field-mark", role: "img", testid: "mark", "data-key": mark.key });
      if (!ctx.reducedMotion()) {
        el.classList.add("enter");
        el.addEventListener("animationend", () => el.classList.remove("enter"), { once: true });
      }
      layer.append(el);
      entry = { el, tone: "", name: "", x: NaN, y: NaN, hidden: false, placement: "" };
      nodes.set(mark.key, entry);
    }
    const tone = toneOf(mark);
    if (entry.tone !== tone) {
      entry.tone = tone;
      entry.el.className = `field-mark tone-${tone}${entry.el.classList.contains("enter") ? " enter" : ""}`;
      entry.el.setAttribute("data-tone", tone);
      entry.el.replaceChildren(icon(TONE_ICON[tone], 12));
    }
    const name = nameFor(mark);
    if (entry.name !== name) {
      entry.name = name;
      entry.el.setAttribute("aria-label", name);
    }
    if (offscreen !== entry.hidden) {
      entry.hidden = offscreen;
      entry.el.style.visibility = offscreen ? "hidden" : "visible";
    }
    if (offscreen) return;
    const spot = markPlacement(a, vp, rtl);
    const x = Math.round(spot.x);
    const y = Math.round(spot.y);
    if (x !== entry.x) { entry.x = x; entry.el.style.left = `${x}px`; }
    if (y !== entry.y) { entry.y = y; entry.el.style.top = `${y}px`; }
    if (spot.placement !== entry.placement) { entry.placement = spot.placement; entry.el.setAttribute("data-placement", spot.placement); }
  }

  function sync(): void {
    const wanted = new Set(current.map((m) => m.key));
    for (const [key, entry] of nodes) {
      if (!wanted.has(key)) { entry.el.remove(); nodes.delete(key); }
    }
    if (current.length > 0) {
      const vp = viewport();
      const rtl = ctx.rtl();
      for (const mark of current) paint(mark, vp, rtl);
    }
    layer.classList.toggle("is-hidden", nodes.size === 0);
    const name = nodes.size > 0 ? t("mark.region") : "";
    if (name && name !== layerName) { layerName = name; layer.setAttribute("aria-label", name); }
  }

  layer.classList.add("is-hidden");

  return {
    set(marks: FieldMark[]): void {
      const seen = new Set<string>();
      current = [];
      for (const mark of marks) {
        if (!mark || typeof mark.key !== "string" || seen.has(mark.key) || !mark.anchor) continue;
        seen.add(mark.key);
        current.push(mark);
        if (current.length >= MAX_MARKS) break;
      }
      sync();
    },
    refresh(): void {
      for (const entry of nodes.values()) entry.name = "";
      layerName = "";
      sync();
    },
    reflow(): void {
      sync();
    },
    destroy(): void {
      for (const entry of nodes.values()) entry.el.remove();
      nodes.clear();
      current = [];
    },
  };
}
