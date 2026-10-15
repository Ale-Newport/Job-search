// Transient and inline notices: toasts, the "save this answer?" prompt, the "record this application?" prompt and the
// brief outline-and-sparkle flash on a field after an insertion.

import { t, tn, type StringKey } from "../i18n";
import { UI_HOST_ATTRIBUTE, type ChipView, type SaveAskView, type ToastView, type TrackAskView, type UIAction } from "../ui-api";
import { panelShortcut, type UiCtx } from "./ctx";
import { h, icon, plainLabel, truncate, type IconName } from "./dom";

type Anchor = ChipView["anchor"];
const TOAST_MS = { ok: 3200, info: 3600, warn: 6500 } as const;
/** A toast with an Undo button stays as long as the answers can be taken back (the controller's batch window is 10 s). */
const UNDOABLE_TOAST_MS = 10000;
const MAX_TOASTS = 3;
const FLASH_MS = 900;
const MAX_FLASHES = 6;

// ---- toasts -----------------------------------------------------------------------------------------------------------
export function toastText(view: ToastView): string {
  const params = view.params ?? {};
  const label = typeof params.label === "string" ? plainLabel(params.label) : "";
  const count = typeof params.count === "number" ? params.count : 0;
  switch (view.kind) {
    case "filled":
      if (count > 1) return tn("toast.filled.count", count);
      return label ? t("toast.filled.label", { label: truncate(label, 60) }) : t("toast.filled");
    case "undone":
      if (count > 0 && !label) return tn("toast.undone.count", count);
      return label ? t("toast.undone.label", { label: truncate(label, 60) }) : t("toast.undone");
    case "draft_ready": return label ? t("toast.draft_ready.label", { label: truncate(label, 60) }) : t("toast.draft_ready");
    case "saved":
      if (params.scope === "application") return t("toast.saved.application");
      if (params.scope === "reuse") return t("toast.saved.reuse");
      return t("toast.saved");
    default: return t(`toast.${view.kind}` as StringKey);
  }
}

const TOAST_ICON: Record<ToastView["tone"], IconName> = { ok: "check", info: "info", warn: "alert" };

export interface ToastController {
  show(view: ToastView): void;
  /** Re-label the live regions after a language change. */
  refresh(): void;
  clear(): void;
  destroy(): void;
}

export function createToasts(ctx: UiCtx, layer: HTMLElement): ToastController {
  const polite = h("div", { class: "toast-region", role: "status", "aria-live": "polite", "aria-atomic": "false", "aria-label": t("toast.region"), testid: "toasts" });
  const alert = h("div", { class: "toast-region", role: "alert", testid: "toasts-alert" });
  layer.append(polite, alert);
  const live = new Map<HTMLElement, () => void>();

  function drop(el: HTMLElement): void {
    live.get(el)?.();
    live.delete(el);
    el.remove();
  }

  return {
    show(view: ToastView): void {
      const text = toastText(view);
      const region = view.kind === "error" ? alert : polite;
      const same = [...region.children].find((c) => c.getAttribute("data-text") === text) as HTMLElement | undefined;
      if (same) drop(same);
      const undoable = view.undoable === true && view.kind !== "error";
      const el = h("div", { class: `toast tone-${view.tone}${undoable ? " has-action" : ""}`, "data-kind": view.kind, "data-text": text, testid: "toast" },
        h("span", { class: "toast-ic", "aria-hidden": "true" }, icon(TOAST_ICON[view.tone], 15)), h("span", { class: "toast-text" }, text));
      if (undoable) {
        // not focusable (the toast layer is outside the form's tab order): the panel's Undo all is the keyboard way to the same action
        const undo = h("button", { type: "button", class: "btn ghost small", tabindex: -1, testid: "toast-undo" }, icon("undo", 13), t("act.undoAll"));
        ctx.onTrustedClick(undo, () => { ctx.emit({ type: "undo-all" }); drop(el); });
        el.append(undo);
      }
      while (region.childElementCount >= MAX_TOASTS) drop(region.firstElementChild as HTMLElement);
      region.append(el);
      live.set(el, ctx.later(() => drop(el), undoable ? UNDOABLE_TOAST_MS : TOAST_MS[view.tone]));
    },
    refresh(): void {
      polite.setAttribute("aria-label", t("toast.region"));
    },
    clear(): void {
      for (const el of [...live.keys()]) drop(el);
    },
    destroy(): void {
      for (const el of [...live.keys()]) drop(el);
      polite.remove(); alert.remove();
    },
  };
}

// ---- inline prompts ---------------------------------------------------------------------------------------------------
export interface PromptController {
  askSave(view: SaveAskView | null): void;
  askTrack(view: TrackAskView | null): void;
  refresh(): void;
  focus(): boolean;
  destroy(): void;
}

export function createPrompts(ctx: UiCtx, container: HTMLElement): PromptController {
  let save: SaveAskView | null = null;
  let track: TrackAskView | null = null;
  let saveMore = false;
  let saveEl: HTMLElement | null = null;
  let trackEl: HTMLElement | null = null;

  function btn(label: string, testid: string, cls: string, action: UIAction, after: () => void): HTMLButtonElement {
    const el = h("button", { type: "button", class: `btn ${cls}`, testid, "data-fid": testid }, label);
    ctx.onTrustedClick(el, () => { ctx.emit(action); after(); });
    return el;
  }

  function buildSave(view: SaveAskView): HTMLElement {
    const done = (): void => { save = null; saveMore = false; renderSave(); };
    const card = h("div", { class: "card ask", role: "group", "aria-label": t("save.group"), testid: "save-ask" });
    card.append(h("div", { class: "card-title", testid: "save-title" }, view.edited ? t("save.title.edited") : t("save.title")));
    card.append(h("div", { class: "ask-field" },
      h("div", { class: "ask-label", dir: "auto", testid: "save-field" }, truncate(plainLabel(view.fieldLabel), 80)),
      h("div", { class: "ask-value", dir: "auto", testid: "save-value" }, truncate(view.valuePreview, 160))));
    const row = h("div", { class: "card-actions" });
    row.append(btn(t("save.application"), "save-application", "primary", { type: "save-answer", key: view.key, remember: "application" }, done));
    if (view.allowReuse) row.append(btn(t("save.reuse"), "save-reuse", "ghost", { type: "save-answer", key: view.key, remember: "reuse" }, done));
    card.append(row);
    if (!view.allowReuse) card.append(h("div", { class: "card-note", testid: "save-only-here" }, icon("lock", 12), t("save.onlyHere")));
    const more = h("button", { type: "button", class: "link", "aria-expanded": String(saveMore), "aria-controls": "save-more", testid: "save-more", "data-fid": "save-more" }, icon("chevron", 12), t("save.more"));
    more.classList.toggle("open", saveMore);
    ctx.onTrustedClick(more, () => { saveMore = !saveMore; renderSave(true); });
    card.append(h("div", { class: "card-actions spread" }, btn(t("save.no"), "save-no", "ghost", { type: "save-never", key: view.key }, done), more));
    if (saveMore) {
      card.append(h("div", { class: "card-actions", id: "save-more" },
        btn(t("save.always"), "save-always", "ghost small", { type: "save-setting", value: "always" }, done),
        btn(t("save.never"), "save-never", "ghost small", { type: "save-setting", value: "never" }, done)));
    }
    card.append(h("div", { class: "card-note", testid: "save-local" }, t("save.local")));
    return card;
  }

  function renderSave(keepFocus = false): void {
    const active = keepFocus ? ctx.root.activeElement?.getAttribute("data-fid") : null;
    saveEl?.remove(); saveEl = null;
    if (!save) return;
    saveEl = buildSave(save);
    container.prepend(saveEl);
    if (active) [...saveEl.querySelectorAll<HTMLElement>("[data-fid]")].find((n) => n.getAttribute("data-fid") === active)?.focus({ preventScroll: true });
  }

  function buildTrack(view: TrackAskView): HTMLElement {
    const done = (): void => { track = null; renderTrack(); };
    const card = h("div", { class: "card ask", role: "group", "aria-label": t("track.group"), testid: "track-ask" });
    card.append(h("div", { class: "card-title", testid: "track-title" }, t("track.title")));
    const company = truncate(view.company.trim(), 60);
    const title = truncate(view.title.trim(), 80);
    const what = title && company ? t("track.body", { title, company }) : company || title;
    if (what) card.append(h("div", { class: "ask-value", dir: "auto", testid: "track-what" }, what));
    if (view.evidence.trim()) card.append(h("div", { class: "card-note quote", testid: "track-evidence" }, t("track.evidence", { text: truncate(view.evidence.trim(), 140) })));
    card.append(h("div", { class: "card-actions" },
      btn(t("track.record"), "track-record", "primary", { type: "track-record" }, done),
      btn(t("track.dismiss"), "track-dismiss", "ghost", { type: "track-dismiss" }, done)));
    card.append(h("div", { class: "card-note" }, t("track.note")));
    return card;
  }

  function renderTrack(): void {
    trackEl?.remove(); trackEl = null;
    if (!track) return;
    trackEl = buildTrack(track);
    container.append(trackEl);
  }

  return {
    askSave(view: SaveAskView | null): void {
      if (view && save && save.key !== view.key) saveMore = false;
      // the prompt appears next to the panel, not where the person is typing: a screen reader says that it is there and how to reach it
      const appeared = !!view && (!save || save.key !== view.key);
      save = view;
      renderSave(true);
      if (appeared) ctx.say(t("announce.save", { keys: panelShortcut(ctx.mac, t("key.shift")) }));
    },
    askTrack(view: TrackAskView | null): void {
      const appeared = !!view && !track;
      track = view;
      renderTrack();
      if (appeared) ctx.say(t("announce.track", { keys: panelShortcut(ctx.mac, t("key.shift")) }));
    },
    refresh(): void {
      renderSave(true); renderTrack();
    },
    focus(): boolean {
      const first = (saveEl ?? trackEl)?.querySelector<HTMLElement>("button");
      if (!first) return false;
      try { first.focus({ preventScroll: true }); } catch { return false; }
      return true;
    },
    destroy(): void {
      saveEl?.remove(); trackEl?.remove();
      saveEl = null; trackEl = null; save = null; track = null;
    },
  };
}

// ---- field flash ------------------------------------------------------------------------------------------------------
export interface FlashController {
  flash(anchor: Anchor, tone: ChipView["tone"]): void;
  destroy(): void;
}

export function createFlash(ctx: UiCtx, layer: HTMLElement): FlashController {
  // The outline is fixed to the viewport, so a page that scrolls while it shows (the logic moves focus to the next field right after an
  // insertion, and an asynchronous insertion lands later still) would leave it hanging over whatever slid under it. Each flash follows the
  // page element that was at its centre when it was drawn: that element moves exactly as the field does, whichever thing scrolls, the window
  // or a dialog, a drawer, a pane of the form (UX-14, UXR-09). Window scroll is the fallback when nothing could be found there.
  interface Live { el: HTMLElement; box: Anchor; follow: Element | null; base: { x: number; y: number }; scrollX: number; scrollY: number }
  const PAD = 3;
  const live: Live[] = [];
  let listening = false;
  let frame = 0;

  /** The page element under a point of the viewport, looking through the open shadow roots of components (never into the assistant's own). */
  function elementAt(x: number, y: number): Element | null {
    if (!(x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight)) return null;
    let found: Element | null = document.elementFromPoint(x, y);
    for (let depth = 0; found?.shadowRoot && !found.hasAttribute(UI_HOST_ATTRIBUTE) && depth < 8; depth++) {
      const inner = found.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === found) break;
      found = inner;
    }
    return found && !found.hasAttribute(UI_HOST_ATTRIBUTE) ? found : null;
  }

  function put(entry: Live): void {
    let dx = -(window.scrollX - entry.scrollX);
    let dy = -(window.scrollY - entry.scrollY);
    if (entry.follow?.isConnected) {
      const now = entry.follow.getBoundingClientRect();
      dx = now.x - entry.base.x;
      dy = now.y - entry.base.y;
    }
    entry.el.style.left = `${Math.round(entry.box.x - PAD + dx)}px`;
    entry.el.style.top = `${Math.round(entry.box.y - PAD + dy)}px`;
  }
  function onScroll(): void {
    for (const entry of live) put(entry);
  }
  function tick(): void {
    frame = 0;
    if (live.length === 0) return;
    onScroll();
    frame = requestAnimationFrame(tick);
  }
  function listen(on: boolean): void {
    if (on === listening) return;
    listening = on;
    // capture: a scroll of an inner container does not bubble, but it does pass the window on the way down; the frame loop covers the
    // scrollers that live in a shadow tree (their scroll events never leave it)
    if (on) {
      window.addEventListener("scroll", onScroll, { passive: true, capture: true });
      frame = requestAnimationFrame(tick);
    } else {
      window.removeEventListener("scroll", onScroll, { capture: true });
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    }
  }
  function drop(entry: Live): void {
    entry.el.remove();
    const at = live.indexOf(entry);
    if (at >= 0) live.splice(at, 1);
    listen(live.length > 0);
  }

  return {
    flash(anchor: Anchor, tone: ChipView["tone"]): void {
      const el = h("div", { class: `flash tone-${tone}${ctx.rtl() ? " rtl" : ""}`, "aria-hidden": "true", testid: "flash" }, h("span", { class: "flash-spark" }, icon("sparkle", 12)));
      el.style.width = `${Math.round(anchor.w + PAD * 2)}px`;
      el.style.height = `${Math.round(anchor.h + PAD * 2)}px`;
      const follow = elementAt(anchor.x + anchor.w / 2, anchor.y + anchor.h / 2);
      const base = follow ? follow.getBoundingClientRect() : null;
      const entry: Live = {
        el, box: { x: anchor.x, y: anchor.y, w: anchor.w, h: anchor.h }, follow: base ? follow : null, base: base ? { x: base.x, y: base.y } : { x: 0, y: 0 },
        scrollX: window.scrollX, scrollY: window.scrollY,
      };
      put(entry);
      while (live.length >= MAX_FLASHES) { const oldest = live[0]; if (oldest) drop(oldest); else break; }
      layer.append(el);
      live.push(entry);
      listen(true);
      ctx.later(() => drop(entry), FLASH_MS);
    },
    destroy(): void {
      for (const entry of [...live]) drop(entry);
      listen(false);
    },
  };
}
