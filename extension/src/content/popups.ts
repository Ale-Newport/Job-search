// Popups of the page that are open next to a field: a calendar a date picker raised, a list of suggestions, a menu.
//
// Why this exists. A date picker opens its calendar when its input takes the focus and closes it on the Tab key the widget itself reads. The
// assistant used to swallow that Tab (it inserts the answer and walks to the next field on its own), so the widget never saw it and the calendar
// stayed open over the NEXT field; the next write then went into a field under a calendar. Two rules follow, both built on `openPopupNear`:
//   - the key belongs to the page while such a popup is open (keyboard.ts: the answer is still inserted, the Tab is not swallowed, so the widget
//     closes its popup the way it always does and the browser moves on);
//   - a field that such a popup covers is never written (scan.ts `stillVisible`: the exception for the popup of a neighbouring question does
//     not apply to a popup the page itself calls a dialog, a listbox, a grid or a menu).
//
// A popup is recognised by what the page SAYS it is (ARIA: dialog, listbox, grid, menu, a native <dialog open>, or what an open
// `aria-expanded` control points to) and by where it is: rendered, transient (positioned out of the flow of the form: a popper, a portal),
// within `POPUP_NEAR_PX` of the field, and neither around the field (the application inside a modal dialog) nor holding another question.

import { inUiHost, isRendered } from "./util";
import type { Rect } from "./util";

/** What the page itself calls a popup. */
export const POPUP_SELECTOR = '[role="dialog"],[role="listbox"],[role="grid"],[role="menu"],dialog[open]';
/** A popup within this many pixels of the field (on both axes) is "near" it: a calendar sits right under its input, a portal list a little further. */
export const POPUP_NEAR_PX = 300;
/** A popup spanning this share of the window is a banner or a drawer of the page, not something that belongs to a field. */
const BANNER_SHARE = 0.9;
/** In a window narrower than this a popup naturally spans most of the width: the "bar across the window" rule needs room to mean anything. */
const WIDE_WINDOW_PX = 600;
const MAX_CANDIDATES = 60;

function visibleBox(el: Element): Rect | null {
  try {
    if (!isRendered(el) || inUiHost(el)) return null;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return null;
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  } catch {
    return null;
  }
}

/** Hidden from everybody (hidden, inert, aria-hidden, no paint): such a popup is closed, whatever markup it keeps in the page. */
function hiddenFromPeople(el: Element): boolean {
  try {
    if (el.closest('[hidden],[inert],[aria-hidden="true"]')) return true;
    const style = getComputedStyle(el);
    return style.visibility === "hidden" || style.visibility === "collapse" || Number(style.opacity) === 0;
  } catch {
    return true;
  }
}

function gapBetween(a: Rect, b: Rect): { dx: number; dy: number } {
  return { dx: Math.max(0, Math.max(a.x, b.x) - Math.min(a.x + a.w, b.x + b.w)), dy: Math.max(0, Math.max(a.y, b.y) - Math.min(a.y + a.h, b.y + b.h)) };
}

/**
 * How is this element positioned out of the flow of the form (a popper, a portal): "fixed", "absolute", or null when it lives in the page's own
 * flow? Looked at from the element up to the first ancestor that holds the field too: whatever sits in the flow beside the field (a table of
 * the form, a list it shows inline) is content, not a popup.
 */
function placedOutOfFlow(el: Element, field: Element): "fixed" | "absolute" | null {
  let found: "fixed" | "absolute" | null = null;
  let cur: Element | null = el;
  for (let depth = 0; cur && depth < 40 && cur !== document.documentElement; depth++) {
    if (cur.contains(field)) return found; // reached the field's own container: what was seen on the way is the answer
    try {
      const position = getComputedStyle(cur).position;
      if (position === "fixed") return "fixed";
      if (position === "absolute") found = "absolute";
    } catch {
      return null;
    }
    const parent: Element | null = cur.parentElement;
    cur = parent ?? ((cur.getRootNode() as ShadowRoot).host ?? null);
  }
  return found;
}

/** The popups an open `aria-expanded` control points to (`aria-controls` / `aria-owns`), looked up in the control's own tree. */
function controlledBy(trigger: Element): Element[] {
  const ids = `${trigger.getAttribute("aria-controls") ?? ""} ${trigger.getAttribute("aria-owns") ?? ""}`.split(/\s+/).filter(Boolean);
  if (!ids.length) return [];
  const root = trigger.getRootNode() as Document | ShadowRoot;
  const out: Element[] = [];
  for (const id of ids.slice(0, 4)) {
    const hit = (root as Document).getElementById?.(id) ?? document.getElementById(id);
    if (hit) out.push(hit);
  }
  return out;
}

export interface PopupField {
  /** the control itself and the elements that stand for it (its visible proxy, its trigger): none of them is a popup of ITS OWN */
  elements: readonly (Element | null | undefined)[];
  /** what the person sees of the field, this frame's viewport coordinates */
  rect: Rect;
  /** the other questions of the page (a popup that holds one of them is a form region, not a popup) */
  others?: readonly Element[];
}

/**
 * The open popup next to a field, or null. `roots` are the open shadow roots of the page (a popup may live in one).
 * Never throws: a page that makes the check fail has no popup for this purpose.
 */
export function openPopupNear(field: PopupField, roots: readonly ParentNode[] = []): Element | null {
  try {
    const own = field.elements.filter((e): e is Element => !!e);
    const anchor = own[0];
    if (!anchor || !(field.rect.w > 0 && field.rect.h > 0)) return null;
    const candidates = new Set<Element>();
    for (const scope of [document, ...roots]) {
      let found: NodeListOf<Element>;
      try {
        found = scope.querySelectorAll(`${POPUP_SELECTOR},[aria-expanded="true"]`);
      } catch {
        continue;
      }
      for (const el of found) {
        if (candidates.size >= MAX_CANDIDATES) break;
        if (el.getAttribute("aria-expanded") === "true" && !el.matches(POPUP_SELECTOR)) {
          if (own.some((o) => o === el || el.contains(o) || o.contains(el))) continue; // the field's own widget: the combobox rules handle it
          for (const popup of controlledBy(el)) candidates.add(popup);
        } else {
          candidates.add(el);
        }
      }
    }
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    for (const el of candidates) {
      if (own.some((o) => o === el || el.contains(o))) continue; // the dialog the form is in, the field's own list
      if (field.others?.some((o) => el.contains(o))) continue; // it holds other questions: a region of the form
      const box = visibleBox(el);
      if (!box || hiddenFromPeople(el)) continue;
      if (box.w >= vw * BANNER_SHARE && box.h >= vh * BANNER_SHARE) continue; // covers the window: a modal of the page, not a popup of a field
      const { dx, dy } = gapBetween(box, field.rect);
      if (dx > POPUP_NEAR_PX || dy > POPUP_NEAR_PX) continue;
      const placed = placedOutOfFlow(el, anchor);
      if (!placed) continue;
      // a bar across the window that stays where it is (a cookie notice, a chat bar) is the page's, not the field's
      if (placed === "fixed" && vw >= WIDE_WINDOW_PX && box.w >= vw * BANNER_SHARE) continue;
      return el;
    }
  } catch {
    /* no popup */
  }
  return null;
}

/**
 * Is `el` (the thing painted over a control) inside an open popup the page calls a popup (and that popup does not hold `control` itself: the
 * dialog an application form is in is no popup of the field)? Then it is that popup that covers the control.
 */
export function insideOpenPopup(el: Element | null | undefined, control: Element): boolean {
  try {
    let cur: Element | null | undefined = el;
    for (let guard = 0; cur && guard < 80; guard++) {
      const hit: Element | null = cur.closest(POPUP_SELECTOR);
      if (hit) {
        if (!hit.contains(control) && !hiddenFromPeople(hit) && !!visibleBox(hit)) return true;
        cur = hit.parentElement ?? (hit.getRootNode() as ShadowRoot).host ?? null; // an outer popup may still be the one
        continue;
      }
      const root = cur.getRootNode();
      cur = root && root.nodeType === 11 ? (root as ShadowRoot).host : null;
    }
  } catch {
    /* not a popup */
  }
  return false;
}
