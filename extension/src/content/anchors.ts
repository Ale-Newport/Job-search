// Where a field is on screen, for the things drawn next to it (chip, marks) and for scrolling it into view.
//
//  - `anchorRect`: the rectangle of what the PERSON sees. A visually hidden control (a file input inside a dropzone, a native
//    checkbox behind a custom switch) has a 0x0 box; its visible proxy, its label or the nearest visible wrapper stands in.
//  - `isObscured`: a persistent marker must not float over unrelated UI. A field scrolled out of an `overflow` container, or
//    covered by a modal, a menu or a sticky header, has its marker withheld until the field can be seen again.

import type { AssistField } from "./state";
import { composedContains } from "./tabstops";
import { inUiHost, isRendered, rectOf, unionRect, type Rect } from "./util";

function usable(rect: Rect): boolean {
  return rect.w > 0 && rect.h > 0;
}

function labelOf(el: Element): Element | null {
  try {
    const labelled = (el as HTMLInputElement).labels?.[0];
    if (labelled) return labelled;
    const id = el.getAttribute("id");
    if (id) {
      const root = el.getRootNode() as Document | ShadowRoot;
      const byFor = [...root.querySelectorAll("label[for]")].find((l) => l.getAttribute("for") === id);
      if (byFor) return byFor;
    }
    return el.closest("label");
  } catch {
    return null;
  }
}

interface Shown { el: Element | null; rect: Rect }

/** Big enough to be what a person sees as "the field" (a 1 px visually hidden input, a 4 px react-select search box are not). */
function substantial(rect: Rect): boolean {
  return rect.w >= 8 && rect.h >= 8;
}

/**
 * The first of the candidate elements that is really on screen; else the nearest visible ancestor of `base` (the dropzone around a hidden
 * file input, the control around a search box); else the biggest thing there is.
 */
function firstVisible(candidates: (Element | null | undefined)[], base: Element, label?: () => Element | null): Shown {
  let fallback: Shown | null = null;
  // the label is looked up last and only when nothing before it showed (it costs a query; a chip asks on every scroll frame)
  for (const el of [...candidates, ...(label ? [undefined] : [])]) {
    const candidate = el === undefined && label ? label() : el;
    if (!candidate || inUiHost(candidate) || !isRendered(candidate)) continue;
    const rect = rectOf(candidate);
    if (!usable(rect)) continue;
    if (substantial(rect)) return { el: candidate, rect };
    fallback ??= { el: candidate, rect };
  }
  let cur: Element | null = base.parentElement;
  for (let depth = 0; cur && depth < 6; depth++, cur = cur.parentElement) {
    const rect = rectOf(cur);
    if (substantial(rect)) return { el: cur, rect };
  }
  return fallback ?? { el: null, rect: { x: 0, y: 0, w: 0, h: 0 } };
}

function shownOf(field: AssistField): Shown {
  return firstVisible([field.anchorEl, field.links.proxy, field.el, field.links.trigger], field.el, () => labelOf(field.el));
}

/**
 * What the person sees of ONE option of a radio / checkbox group: the control (or the visible stand-in of a styled-away one) AND its printed
 * label, which is where the option's words are (UXR-02: a 13 px radio circle is not the option, "No" next to it is). A label that wraps other
 * controls of the group as well (one <label> around the whole list) is not this option's own and is left out.
 */
export function optionArea(field: AssistField, m: AssistField["members"][number]): Rect | null {
  const control = firstVisible([m.el, m.proxy], m.el, () => labelOf(m.el)).rect;
  const parts: Rect[] = [control];
  try {
    const label = labelOf(m.el);
    const shared = !!label && field.members.some((o) => o !== m && label.contains(o.el));
    if (label && !shared && !inUiHost(label) && isRendered(label)) parts.push(rectOf(label));
  } catch {
    /* the control alone */
  }
  const area = unionRect(parts.filter(usable));
  return usable(area) ? area : null;
}

/** One rectangle per option of a radio / checkbox group, as the person reads them (see `optionArea`). Empty for every other field. */
export function optionAreas(field: AssistField): Rect[] {
  return field.members.map((m) => optionArea(field, m)).filter((r): r is Rect => !!r);
}

/** The rectangle of the field as the person sees it (this frame's viewport coordinates). A choice group is all its options, labels included. */
export function anchorRect(field: AssistField): Rect {
  if (field.members.length) {
    const areas = optionAreas(field);
    if (areas.length) return unionRect(areas);
    return unionRect(field.members.map((m) => firstVisible([m.el, m.proxy], m.el, () => labelOf(m.el)).rect));
  }
  return shownOf(field).rect;
}

/** The element whose rectangle `anchorRect` reports (what to scroll into view); the field's own anchor when nothing is visible. */
export function anchorElement(field: AssistField): Element {
  if (field.members.length) {
    const first = field.members.map((m) => firstVisible([m.el, m.proxy], m.el, () => labelOf(m.el))).find((s) => s.el);
    if (first?.el) return first.el;
  }
  return shownOf(field).el ?? field.anchorEl;
}

/** Rectangle of one group member (radio/checkbox) or null when nothing of it can be seen. */
export function memberRect(m: AssistField["members"][number]): Rect | null {
  const r = firstVisible([m.el, m.proxy], m.el, () => labelOf(m.el)).rect;
  return usable(r) ? r : null;
}

const CLIPPING = /^(?:hidden|clip|scroll|auto|overlay)$/;

/** Is the point (a field's centre) cut off by an ancestor with `overflow` other than visible? */
function clippedByAncestor(start: Element, cx: number, cy: number): boolean {
  let cur: Element | null = start.parentElement ?? ((start.getRootNode() as ShadowRoot).host ?? null);
  for (let depth = 0; cur && depth < 60; depth++) {
    if (cur === document.documentElement || cur === document.body) break;
    try {
      const style = getComputedStyle(cur);
      // `overflow` does not apply to inline boxes (their client size is 0): they clip nothing
      const clips = (CLIPPING.test(style.overflowX) || CLIPPING.test(style.overflowY)) && style.display !== "inline" && style.display !== "contents";
      if (clips) {
        const box = cur.getBoundingClientRect();
        const left = box.left + (cur as HTMLElement).clientLeft;
        const top = box.top + (cur as HTMLElement).clientTop;
        const right = left + (cur as HTMLElement).clientWidth;
        const bottom = top + (cur as HTMLElement).clientHeight;
        if ((CLIPPING.test(style.overflowX) && (cx < left || cx > right)) || (CLIPPING.test(style.overflowY) && (cy < top || cy > bottom))) return true;
      }
    } catch {
      return false;
    }
    cur = cur.parentElement ?? ((cur.getRootNode() as ShadowRoot).host ?? null);
  }
  return false;
}

/** Elements that make up the field (what a hit on the field may be). */
function relatedSeeds(field: AssistField): Element[] {
  const list: (Element | null | undefined)[] = [
    field.el, field.anchorEl, field.links.proxy, field.links.trigger, field.links.search, field.links.clear,
    ...field.members.flatMap((m) => [m.el, m.proxy]),
  ];
  const seeds = list.filter((e): e is Element => !!e && e.isConnected);
  const label = labelOf(field.el);
  if (label) seeds.push(label);
  for (const m of field.members) {
    const l = labelOf(m.el);
    if (l) seeds.push(l);
  }
  return seeds;
}

/**
 * True when the field's centre cannot be seen: clipped by an ancestor's overflow (scrolled out of a modal body), or something
 * unrelated (a modal, a menu, a sticky header) is on top there. A centre outside the viewport is NOT obscured: the field is
 * merely scrolled away, which the visual layer already handles by positioning the marker off screen.
 */
export function isObscured(field: AssistField, rect: Rect, others: readonly AssistField[]): boolean {
  if (!usable(rect)) return true;
  const cx = rect.x + rect.w / 2;
  const cy = rect.y + rect.h / 2;
  // a centre outside the viewport is not drawn anyway: nothing to withhold, and no style or hit-test work for it
  if (cx < 0 || cy < 0 || cx >= window.innerWidth || cy >= window.innerHeight) return false;
  const base = field.members.length ? (field.members[0].el.isConnected ? field.members[0].el : field.el) : field.el;
  if (clippedByAncestor(base, cx, cy)) return true;
  try {
    const root = base.getRootNode() as Document | ShadowRoot;
    const stack = (root.elementsFromPoint ? root.elementsFromPoint(cx, cy) : document.elementsFromPoint(cx, cy)).filter((e) => !inUiHost(e));
    const top = stack[0];
    if (!top) return false; // nothing hit-testable there: not covered by anything
    const seeds = relatedSeeds(field);
    if (seeds.some((s) => composedContains(s, top))) return false; // the field, its label, a descendant
    // a wrapper of the field (its row, a styled control) is fine as long as it holds no OTHER question
    if (seeds.some((s) => composedContains(top, s)) && !others.some((o) => o.key !== field.key && o.el.isConnected && composedContains(top, o.el))) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

// ---- scrolling a field into view inside the boxes around it (UXR-04) ---------------------------------------------------------

const SCROLLERS = /^(?:auto|scroll|overlay)$/;

/**
 * Scroll every scrollable ANCESTOR of `anchor` (a dialog, a drawer, a form pane with its own scrollbar) so that the rectangle `rectOfField`
 * reports lies inside its visible part with `margin` px to spare: the browser's own Tab does this, the assistant's own walk must too, or the
 * focused field sits out of sight and the chip and the flash are drawn over whatever is behind the box. The page's viewport is left to the
 * caller (it has its own, wider rule). True when something moved. Never throws; never smooth (a smooth scroll would still be moving when the
 * chip is placed).
 */
export function scrollWithinContainers(anchor: Element, rectOfField: () => Rect, margin = 24): boolean {
  let moved = false;
  try {
    let cur: Element | null = anchor.parentElement ?? ((anchor.getRootNode() as ShadowRoot).host ?? null);
    for (let depth = 0; cur && depth < 60; depth++) {
      if (cur === document.documentElement || cur === document.body) break;
      const style = getComputedStyle(cur);
      const box = cur as HTMLElement;
      const y = SCROLLERS.test(style.overflowY) && box.scrollHeight > box.clientHeight + 1;
      const x = SCROLLERS.test(style.overflowX) && box.scrollWidth > box.clientWidth + 1;
      if (y || x) {
        const outer = cur.getBoundingClientRect();
        const left = outer.left + box.clientLeft;
        const top = outer.top + box.clientTop;
        const r = rectOfField();
        let dx = 0;
        let dy = 0;
        if (y && usable(r)) {
          const room = box.clientHeight - 2 * margin;
          if (r.h > room || r.y < top + margin) dy = r.y - (top + margin); // too tall for the box: its top, else up to the margin
          else if (r.y + r.h > top + box.clientHeight - margin) dy = r.y + r.h - (top + box.clientHeight - margin);
        }
        if (x && usable(r)) {
          const room = box.clientWidth - 2 * margin;
          if (r.w > room || r.x < left + margin) dx = r.x - (left + margin);
          else if (r.x + r.w > left + box.clientWidth - margin) dx = r.x + r.w - (left + box.clientWidth - margin);
        }
        if (Math.abs(dx) >= 1 || Math.abs(dy) >= 1) {
          box.scrollBy({ left: Math.round(dx), top: Math.round(dy), behavior: "instant" as ScrollBehavior });
          moved = true;
        }
      }
      cur = cur.parentElement ?? ((cur.getRootNode() as ShadowRoot).host ?? null);
    }
  } catch {
    /* scrolling is a nicety */
  }
  return moved;
}
