// Roving tabindex: a list or a row of controls that is ONE stop of the Tab order. Tab enters it at the active control and leaves it
// at the other end; the arrow keys, Home and End move between its members. Without this, a form of forty fields made the panel forty
// Tab stops long (UXR-13).
//
// The members are plain buttons, so Enter and Space still press them: a progress row goes to its field, a link opens its form.

export interface RovingOptions {
  /** Which arrow keys move: a column (up/down), a row (left/right) or either (a wrapped row of links). */
  axis: "vertical" | "horizontal" | "both";
  /** The member (its `data-fid`) that Tab should land on: the one the person was last on, else the first. */
  active?: string;
  /** Called when a member becomes the active one (focus arrived there by any means), so a re-render can keep it. */
  onActive?: (fid: string) => void;
}

const STEP_KEYS: Record<RovingOptions["axis"], Record<string, 1 | -1>> = {
  vertical: { ArrowDown: 1, ArrowUp: -1 },
  horizontal: { ArrowRight: 1, ArrowLeft: -1 },
  both: { ArrowDown: 1, ArrowUp: -1, ArrowRight: 1, ArrowLeft: -1 },
};

function usable(el: HTMLElement): boolean {
  return el.isConnected && !(el as HTMLButtonElement).disabled && el.getClientRects().length > 0;
}

/**
 * Make `members` one tab stop. Safe to call again after the members were rebuilt; nothing is left behind on the old nodes.
 * `members` must be descendants of `container` (the key handler lives there).
 */
export function rove(container: HTMLElement, members: HTMLElement[], options: RovingOptions): void {
  const list = members.filter((el) => !(el as HTMLButtonElement).disabled);
  if (list.length === 0) return;
  const wanted = options.active !== undefined ? list.find((el) => el.getAttribute("data-fid") === options.active) : undefined;
  let active = wanted ?? list[0];
  const mark = (): void => {
    for (const el of list) el.setAttribute("tabindex", el === active ? "0" : "-1");
  };
  mark();
  for (const el of list) {
    el.addEventListener("focus", () => {
      if (active === el) return;
      active = el;
      mark();
      const fid = el.getAttribute("data-fid");
      if (fid) options.onActive?.(fid);
    });
  }
  const keys = STEP_KEYS[options.axis];
  container.addEventListener("keydown", (ev) => {
    if (!ev.isTrusted || ev.ctrlKey || ev.metaKey || ev.altKey || ev.shiftKey || ev.isComposing) return;
    const from = list.findIndex((el) => el === ev.target || el.contains(ev.target as Node));
    if (from < 0) return;
    let to = from;
    if (ev.key === "Home") to = 0;
    else if (ev.key === "End") to = list.length - 1;
    else if (keys[ev.key] !== undefined) {
      // the nearest member that is really there (a collapsed section can leave some out of the layout)
      const step = keys[ev.key];
      for (let at = from + step; at >= 0 && at < list.length; at += step) {
        if (usable(list[at])) { to = at; break; }
      }
    } else return;
    ev.preventDefault();
    ev.stopPropagation();
    if (to !== from) list[to].focus();
  });
}
