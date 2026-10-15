// The recommended option of a choice control: a 2 px mint outline and a small check on the radio / checkbox rectangle, drawn while
// the chip is showing. It is decoration with a purpose (the person maps "Choose No" to the right control at a glance) and nothing
// else: aria-hidden, click-through, never focusable. It goes away with the chip (blur, Esc, insertion) and follows it on scroll.

import { viewport, type Rect, type UiCtx } from "./ctx";
import { h, icon } from "./dom";

const PAD = 3;
const MAX_OUTLINES = 12;

export interface OptionMarks {
  /** Replace the outlines with one per rectangle (a rectangle with no area, or off screen, draws nothing). */
  set(rects: Rect[]): void;
  /** The field moved without a new view (a scroll frame): the outlines move with it. */
  shift(dx: number, dy: number): void;
  clear(): void;
  destroy(): void;
}

export function createOptionMarks(ctx: UiCtx, layer: HTMLElement): OptionMarks {
  const box = h("div", { class: "opt-layer", "aria-hidden": "true", testid: "option-layer" });
  layer.append(box);
  let rects: Rect[] = [];
  let nodes: HTMLElement[] = [];

  function paint(): void {
    const vp = viewport();
    const wanted = rects.filter((r) => r.w > 0 && r.h > 0 && r.y + r.h > 0 && r.y < vp.h && r.x + r.w > 0 && r.x < vp.w).slice(0, MAX_OUTLINES);
    while (nodes.length > wanted.length) nodes.pop()?.remove();
    wanted.forEach((r, i) => {
      let node = nodes[i];
      if (!node) {
        node = h("div", { class: `opt-mark${ctx.rtl() ? " rtl" : ""}`, "aria-hidden": "true", testid: "option-mark" }, h("span", { class: "opt-check" }, icon("check", 11)));
        nodes.push(node);
        box.append(node);
      }
      node.style.left = `${Math.round(r.x - PAD)}px`;
      node.style.top = `${Math.round(r.y - PAD)}px`;
      node.style.width = `${Math.round(r.w + PAD * 2)}px`;
      node.style.height = `${Math.round(r.h + PAD * 2)}px`;
    });
    box.classList.toggle("is-hidden", nodes.length === 0);
  }

  box.classList.add("is-hidden");

  return {
    set(next: Rect[]): void {
      rects = next.map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h }));
      paint();
    },
    shift(dx: number, dy: number): void {
      if (rects.length === 0 || (dx === 0 && dy === 0)) return;
      rects = rects.map((r) => ({ ...r, x: r.x + dx, y: r.y + dy }));
      paint();
    },
    clear(): void {
      rects = [];
      paint();
    },
    destroy(): void {
      rects = [];
      nodes = [];
      box.remove();
    },
  };
}
