// The assistant panel (TOP frame only): job header, research strip, form progress, bulk fill, calm connection states.
// It sits at the end of the document in DOM order, so Tab reaches it after the form; it never takes focus on its own.

import type { ConnectionPhase, ConnectionState } from "../../shared/messages";
import type { ResearchSource } from "../../shared/protocol";
import { enterKeyName, formatClock, formatDay, formatMoney, formatNumber, t, tn, type StringKey } from "../i18n";
import type { PanelView, ProgressItem, ResearchView, UIAction } from "../ui-api";
import { activationShortcut, altLabel, altShiftKey, chipKeyLabel, panelShortcut, shiftLabel, type UiCtx } from "./ctx";
import { captureFocus, h, hostOf, icon, markIcon, plainLabel, restoreFocus, safeHref, truncate, type Child, type IconName } from "./dom";
import { currentPrefs, loadPrefs, NOTE_TTL_MS, savePrefs, type Dock, type NoteKind } from "./prefs";
import { rove } from "./roving";

export type { Dock } from "./prefs";
type FormKind = "company" | "site";

/** The first connection check is still running: calm "Connecting to Meridian…", never an error and never announced. */
function isConnecting(phase: ConnectionPhase | undefined): boolean {
  return phase === "connecting";
}

/** A TCP port the worker reported, or null for anything that is not one (the number is shown to the user, so it is checked). */
function portOf(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 65535 ? value : null;
}

/**
 * Two or more programs on this Mac answer like Meridian while this browser holds no credential that could tell them apart
 * (phase `unpaired` or `revoked`): the worker refuses to pair with any of them (`pair:start` fails with `ambiguous`) and the
 * panel asks the user to close the extra one instead of offering a Connect that cannot work. The ports it lists, or null.
 */
export function ambiguousPorts(connection: ConnectionState): number[] | null {
  if (connection.phase !== "unpaired" && connection.phase !== "revoked") return null;
  const raw: unknown = connection.meridianPorts;
  if (!Array.isArray(raw)) return null;
  const ports = [...new Set(raw.map(portOf).filter((port): port is number => port !== null))].slice(0, 8);
  return ports.length > 1 ? ports : null;
}

/** The sentence of the duplicate banner: what became of the application (when the logic knows), with the day it was recorded when known. */
const DUPLICATE_HINTS: Record<"applied" | "rejected" | "withdrawn" | "ghosted" | "interview" | "offer" | "preparing", { since: StringKey; plain: StringKey }> = {
  applied: { since: "dup.applied.hint.since", plain: "dup.applied.hint" },
  rejected: { since: "dup.rejected.hint.since", plain: "dup.rejected.hint" },
  withdrawn: { since: "dup.withdrawn.hint.since", plain: "dup.withdrawn.hint" },
  ghosted: { since: "dup.ghosted.hint.since", plain: "dup.ghosted.hint" },
  interview: { since: "dup.interview.hint.since", plain: "dup.interview.hint" },
  offer: { since: "dup.offer.hint.since", plain: "dup.offer.hint" },
  preparing: { since: "dup.preparing.hint.since", plain: "dup.preparing.hint" },
};

export function duplicateHint(dup: NonNullable<PanelView["duplicate"]>): string {
  const date = formatDay(dup.since);
  const kind = dup.state !== "applied" ? "preparing" : dup.status && dup.status in DUPLICATE_HINTS ? dup.status : "applied";
  return date ? t(DUPLICATE_HINTS[kind].since, { date }) : t(DUPLICATE_HINTS[kind].plain);
}

/** How long a pressed "Try again" says "Checking…": the answer may be the same as before, and a button must never look dead. */
const RETRY_FEEDBACK_MS = 1200;
/** The end-of-form review lists this many questions; the rest is a count. */
const REVIEW_ITEMS = 5;
/** The "Fill verified" confirmation lists this many answers (the logic may send fewer than its count); the rest is a count. */
const FILL_PREVIEW_ITEMS = 12;
/** When the control that had the keyboard is gone, these are where it goes next (the first one that is there and enabled). */
const FOCUS_NEXT: Record<string, string[]> = {
  "fill-confirm": ["panel-fill-verified", "panel-undo-all"],
  "fill-cancel": ["panel-fill-verified"],
  "panel-fill-verified": ["panel-undo-all", "fill-confirm"],
};
const SOURCE_ICON: Record<ResearchSource["status"], IconName> = {
  ok: "check", failed: "close", blocked: "lock", skipped: "dash", unverified: "info",
};
const ITEM_ICON: Record<ProgressItem["status"], IconName | null> = {
  pending: null, inserted: "check", edited: "pencil", filled: "check", needs_you: "info", locked: "lock", skipped: "dash",
};

export interface PanelController {
  set(view: PanelView): void;
  /** Remember for good that a note was seen (the first-use card after the first insertion): the same entry as its "Got it". */
  dismissNote(kind: NoteKind): void;
  refresh(): void;
  /** Move keyboard focus into the panel (pill when collapsed). Returns false when there is nothing to focus. */
  focus(): boolean;
  dock(): Dock;
  destroy(): void;
}

/** Hostname for the "official website" form; null when the text is not a plausible public site address. */
export function normaliseDomain(raw: string): string | null {
  const text = raw.trim().toLowerCase();
  if (!text || text.length > 253 || /\s/.test(text)) return null;
  let host: string;
  try {
    host = new URL(/^[a-z][a-z0-9+.-]*:\/\//.test(text) ? text : `https://${text}`).hostname;
  } catch {
    return null;
  }
  host = host.replace(/\.$/, "");
  if (host.includes(":") || /^\d+(\.\d+){3}$/.test(host)) return null;
  return /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$/.test(host) ? host : null;
}

function expiryMs(expiresAt: number): number {
  return expiresAt < 1e12 ? expiresAt * 1000 : expiresAt;
}

function spaced(code: string): string {
  const digits = code.replace(/\s+/g, "");
  return digits.length === 6 ? `${digits.slice(0, 3)} ${digits.slice(3)}` : digits;
}

export function createPanel(ctx: UiCtx, dockEl: HTMLElement, slot: HTMLElement): PanelController {
  const section = h("section", { class: "panel-host", role: "region", "aria-label": t("panel.region"), testid: "panel" });
  slot.append(section);

  const saved = currentPrefs();
  const ps = {
    moreOpen: false, sources: false, form: null as FormKind | null, formValue: "", formError: false,
    dock: saved.dock as Dock, dismissed: { ...saved.dismissed } as Partial<Record<NoteKind, number>>, lastCurrent: "", listScroll: 0, bodyScroll: 0, dockTouched: false,
    retryUntil: 0,
    // the in-panel key cheat sheet is open; the duplicate banner and the end-of-form review were answered (the logic clears them
    // too, this only makes the click take effect at once); research needs the person said "not now" to, by kind and company
    help: false, dupHidden: false, reviewHidden: false, needsDismissed: new Set<string>(),
    // "automatic drafts are paused on this page" was closed (it belongs to this page only: nothing about it is remembered)
    pausedHidden: false,
    // the cards that can be folded to their title, and the control each roving group (one Tab stop) last had
    reviewOpen: true, firstOpen: true, roving: {} as Record<string, string>,
  };
  let view: PanelView | null = null;
  let lastSig = "";
  let cancelTick: (() => void) | null = null;
  let expired = false;
  let uid = 0;
  let alive = true;

  // A polite status line for connection changes: keyboard and screen-reader users get no other signal that, say, the
  // pairing code appeared after they pressed Connect. Silent for the first healthy render and for "connecting".
  const announcer = h("div", { class: "sr", role: "status", "aria-live": "polite", "aria-atomic": "true", testid: "panel-announce" });
  slot.append(announcer);
  let announced = "";

  function announceConnection(prev: PanelView | null, next: PanelView, again = false): void {
    const phase = next.connection.phase;
    const before = prev?.connection.phase;
    const pairingCode = phase === "pairing" ? next.connection.pairing?.code ?? "" : "";
    const ports = ambiguousPorts(next.connection);
    const key = `${phase}|${pairingCode}|${ports ? ports.join(",") : ""}`;
    if ((key === announced && !again) || next.orphaned) return;
    announced = key;
    if (isConnecting(phase) || (phase === "ready" && (prev === null || isConnecting(before)))) return;
    let text: string;
    switch (phase) {
      case "ready": text = t("conn.ready"); break;
      case "pairing": {
        const digits = pairingCode.replace(/\D+/g, "").split("").join(" ");
        text = digits ? `${t("calm.pairing")}. ${t("calm.pairing.codeLabel", { digits })}` : t("calm.pairing.expired");
        break;
      }
      case "proof_failed": text = `${t("calm.proof_failed")} ${t("calm.proof_failed.hint")}`; break;
      default: text = ports ? `${t("calm.ambiguous")} ${t("calm.ambiguous.hint", { ports: ports.join(", ") })}` : t(`calm.${phase}` as StringKey);
    }
    announcer.textContent = "";
    announcer.textContent = text;
  }

  /** The duplicate warning and the end-of-form review appear without a click: a screen reader hears that they are there. */
  function announceNotices(prev: PanelView | null, next: PanelView): void {
    if (next.orphaned || next.connection.phase !== "ready") return;
    const dup = next.duplicate;
    if (dup && dup.state !== prev?.duplicate?.state) {
      ctx.say(`${dup.state === "applied" ? t("dup.applied.title") : t("dup.preparing.title")}. ${duplicateHint(dup)}`);
    }
    const review = next.review;
    if (review && review.missingRequired > 0 && review.missingRequired !== (prev?.review?.missingRequired ?? 0)) ctx.say(tn("review.title", review.missingRequired));
  }

  // ---- tiny remembered look (dock side, dismissed notes): kept by the worker, never by the page or by this script ----
  function persist(): void {
    savePrefs({ dock: ps.dock, dismissed: ps.dismissed as Record<string, number> });
  }
  function load(): void {
    void loadPrefs(() => ps.dockTouched).then((merged) => {
      if (!merged || !alive) return;
      ps.dock = merged.dock;
      ps.dismissed = merged.dismissed as Partial<Record<NoteKind, number>>;
      applyDock();
      if (view) render(true);
    }).catch(() => undefined);
  }
  function applyDock(): void {
    dockEl.classList.toggle("dock-left", ps.dock === "left");
    dockEl.classList.toggle("dock-right", ps.dock === "right");
  }
  function noteDismissed(kind: NoteKind): boolean {
    const at = ps.dismissed[kind];
    return typeof at === "number" && Date.now() - at < NOTE_TTL_MS[kind];
  }

  // ---- building blocks ------------------------------------------------------------------------------------------
  function btn(label: string, testid: string, cls: string, action: UIAction | (() => void), opts: { icon?: IconName; fid?: string; disabled?: boolean; aria?: string } = {}): HTMLButtonElement {
    const el = h("button", {
      type: "button", class: `btn ${cls}`, testid, "data-fid": opts.fid ?? testid, disabled: opts.disabled || null, "aria-label": opts.aria,
    }, opts.icon ? icon(opts.icon, 14) : null, label);
    ctx.onTrustedClick(el, () => { if (typeof action === "function") action(); else ctx.emit(action); });
    return el;
  }

  /** The keyboard way to a button, for the screen reader (the cheat sheet prints it). */
  function withShortcut(el: HTMLButtonElement, shortcut: string): HTMLButtonElement {
    el.setAttribute("aria-keyshortcuts", shortcut);
    return el;
  }

  function iconBtn(name: IconName, label: string, testid: string, onClick: () => void, extra: Record<string, string | boolean> = {}): HTMLButtonElement {
    const el = h("button", { type: "button", class: "ibtn", "aria-label": label, title: label, testid, "data-fid": testid, ...extra }, icon(name, 16));
    ctx.onTrustedClick(el, onClick);
    return el;
  }

  function switchRow(label: string, hint: string | null, checked: boolean, testid: string, onToggle: () => void): HTMLElement {
    const id = `sw-${++uid}`;
    const sw = h("button", { type: "button", role: "switch", class: "switch", "aria-checked": String(checked), "aria-labelledby": id, testid, "data-fid": testid }, h("span", { class: "knob" }));
    ctx.onTrustedClick(sw, onToggle);
    return h("div", { class: "srow" }, h("div", { class: "stext" }, h("span", { class: "slabel", id }, label), hint ? h("span", { class: "shint" }, hint) : null), sw);
  }

  function calm(opts: { tone: "info" | "ok" | "warn"; icon: IconName | "spinner"; title: string; hint?: string; primary?: HTMLElement | null; secondary?: HTMLElement | null; testid: string; extra?: Child }): HTMLElement {
    return h("div", { class: `calm tone-${opts.tone}`, testid: opts.testid, role: "group", "aria-busy": opts.icon === "spinner" ? "true" : null },
      h("div", { class: "calm-head" }, h("span", { class: "calm-icon", "aria-hidden": "true" }, opts.icon === "spinner" ? h("span", { class: "spinner", testid: "spinner" }) : icon(opts.icon, 18)),
        h("div", { class: "calm-text" }, h("div", { class: "calm-title" }, opts.title), opts.hint ? h("div", { class: "calm-hint" }, opts.hint) : null)),
      opts.extra ?? null,
      opts.primary || opts.secondary ? h("div", { class: "calm-actions" }, opts.primary ?? null, opts.secondary ?? null) : null);
  }

  // ---- header -----------------------------------------------------------------------------------------------------
  function jobLine(v: PanelView): string {
    const company = v.job?.company.trim() ?? "";
    const title = v.job?.title.trim() ?? "";
    if (company && title) return t("panel.titleSeparator", { company, title });
    return company || title || t("panel.untitled");
  }

  /**
   * What the header says about the connection. An orphaned script (the extension was reloaded or updated) is not a connection
   * problem, whatever phase the logic layer parked it in, and the ambiguous case is an unpaired or revoked browser with a
   * reason of its own.
   */
  function statusOf(v: PanelView): ConnectionPhase | "ambiguous" | "orphaned" {
    if (v.orphaned) return "orphaned";
    return ambiguousPorts(v.connection) ? "ambiguous" : v.connection.phase;
  }

  function connLabel(status: ConnectionPhase | "ambiguous" | "orphaned"): string {
    return t(`conn.${status}` as StringKey);
  }

  function subline(v: PanelView): string {
    if (v.orphaned || v.connection.phase !== "ready") return connLabel(statusOf(v));
    switch (v.job?.tracked) {
      case "applied": return t("panel.job.applied");
      case "preparing": return t("panel.job.preparing");
      case "prospective": return t("panel.job.prospective");
      default: return v.job?.location.trim() ?? "";
    }
  }

  function header(v: PanelView): HTMLElement {
    const phase = v.connection.phase;
    const status = statusOf(v);
    const dot = h("span", { class: `conn conn-${status}`, role: "img", "aria-label": connLabel(status), title: connLabel(status), testid: "panel-conn", "data-phase": phase });
    const full = jobLine(v);
    const sub = subline(v);
    const head = h("div", { class: "p-head" },
      h("span", { class: "p-mark" }, markIcon(22)),
      h("div", { class: "p-title" },
        h("div", { class: "p-job", dir: "auto", title: full, testid: "panel-job" }, full),
        sub ? h("div", { class: `p-sub${v.job?.tracked === "applied" && phase === "ready" ? " strong" : ""}`, dir: "auto", testid: "panel-sub" }, sub) : null),
      dot,
      iconBtn("minus", t("panel.collapse"), "panel-collapse", () => ctx.emit({ type: "toggle-collapse" }), { "data-fid": "toggle" }),
      iconBtn("more", t("panel.more"), "panel-more", () => { ps.moreOpen = !ps.moreOpen; render(true); }, { "aria-expanded": String(ps.moreOpen), "aria-controls": "p-more" }));
    return head;
  }

  function morePanel(v: PanelView): HTMLElement {
    const box = h("div", { class: "p-more", id: "p-more", testid: "panel-more-box" });
    const dockRow = h("div", { class: "dock-row", role: "group", "aria-label": t("more.dock") });
    for (const side of ["left", "right"] as const) {
      const label = side === "left" ? t("more.dock.left") : t("more.dock.right");
      const b = h("button", { type: "button", class: "btn ghost small", "aria-pressed": String(ps.dock === side), testid: `panel-dock-${side}`, "data-fid": `dock-${side}` },
        icon(side === "left" ? "dockLeft" : "dockRight", 14), label);
      ctx.onTrustedClick(b, () => { ps.dock = side; ps.dockTouched = true; applyDock(); persist(); render(true); });
      dockRow.append(b);
    }
    box.append(dockRow);
    if (v.site) {
      const host = v.site.host.trim();
      // The person asked for "start automatically" and the browser has not granted the site yet: the question is open in a small window.
      // A switch that says Off would be stale, so the menu says what to do instead until the access is there.
      const waiting = v.site.alwaysOnNeedsPermission === true && !v.site.alwaysOn;
      box.append(
        waiting
          ? h("div", { class: "srow perm", role: "status", testid: "panel-always-on-permission" },
            h("span", { class: "calm-icon", "aria-hidden": "true" }, icon("info", 16)), h("span", { class: "shint" }, t("more.alwaysOn.permission")))
          : switchRow(host ? t("more.alwaysOn", { site: host }) : t("more.alwaysOn.generic"), null, v.site.alwaysOn, "panel-always-on", () => ctx.emit({ type: "always-on-site" })),
      );
      // The switch was turned OFF and a rule the person did not set here still starts the assistant: one calm line says which, and where it goes
      const note = !waiting && v.site.alwaysOn ? v.site.alwaysOnNote : undefined;
      if (note) {
        const rule = (note.rule ?? "").trim();
        const text = rule ? t("more.alwaysOn.rule", { rule }) : t("more.alwaysOn.rule.generic");
        box.append(h("div", { class: "srow perm", role: "status", testid: "panel-always-on-rule", "data-code": note.code },
          h("span", { class: "calm-icon", "aria-hidden": "true" }, icon("info", 16)), h("span", { class: "shint", dir: "auto" }, text)));
      }
      box.append(switchRow(host ? t("more.hideHint", { site: host }) : t("more.hideHint.generic"), null, v.site.hintHidden, "panel-hide-hint", () => ctx.emit({ type: "hide-hint-site" })));
    }
    // Recording a posting never depends on a thank-you page being recognised: the person can always say so here (CPL-10).
    const tracked = v.job?.tracked;
    if (v.job && !v.orphaned && v.connection.phase === "ready" && tracked !== "applied") {
      const track = (action: UIAction): (() => void) => () => { ps.moreOpen = false; ctx.emit(action); render(true, "panel-more"); };
      if (tracked === "none") box.append(btn(t("more.track.save"), "panel-track-save", "ghost small menu", track({ type: "track-save" }), { icon: "list" }));
      box.append(btn(t("more.track.applied"), "panel-track-applied", "ghost small menu", track({ type: "track-applied" }), { icon: "check" }));
    }
    box.append(btn(t("more.deactivate"), "panel-deactivate", "ghost small block", { type: "deactivate" }));
    return box;
  }

  // ---- calm connection states -------------------------------------------------------------------------------------
  function pairingCard(v: PanelView): HTMLElement {
    const pairing = v.connection.pairing;
    const digits = pairing ? pairing.code.replace(/\D+/g, "") : "";
    const end = pairing ? expiryMs(pairing.expiresAt) : 0;
    const left = pairing ? Math.max(0, Math.ceil((end - Date.now()) / 1000)) : 0;
    expired = !!pairing && left <= 0;
    if (!pairing || expired) {
      return calm({
        tone: "warn", icon: "key", title: t("calm.pairing.expired"), testid: "calm-pairing-expired",
        primary: btn(t("calm.pairing.new"), "calm-new-code", "primary", { type: "connect" }),
        secondary: btn(t("calm.cancel"), "calm-cancel", "ghost", { type: "cancel-pairing" }),
      });
    }
    const code = h("div", { class: "code", testid: "pairing-code", role: "img", "aria-label": t("calm.pairing.codeLabel", { digits: digits.split("").join(" ") }) },
      h("span", { class: "digits", "aria-hidden": "true" }, spaced(digits)));
    const timer = h("div", { class: "countdown", testid: "pairing-countdown" }, t("calm.pairing.expires", { time: formatClock(left) }));
    // The first pairing has nothing to check the listener against: the user is the check. So, under the code, say that Meridian
    // itself must open the window asking for it, which port answered, and what to do when no window opens (the options page
    // says the same). The code comes first so that it stays on screen in a very short frame.
    const port = portOf(v.connection.port);
    const notes = h("div", { class: "pair-notes" },
      h("div", { class: "calm-hint", testid: "pairing-window" }, t("calm.pairing.hint")),
      h("div", { class: "calm-hint", testid: "pairing-help" },
        port !== null ? h("span", { testid: "pairing-port" }, t("calm.pairing.port", { port })) : null,
        port !== null ? " " : null,
        h("span", { testid: "pairing-nowindow" }, t("calm.pairing.nowindow"))));
    return calm({
      tone: "info", icon: "key", title: t("calm.pairing"), testid: "calm-pairing",
      extra: h("div", { class: "pair-extra" }, h("div", { class: "code-box" }, code, timer), notes),
      primary: btn(t("calm.openMeridian"), "calm-open-meridian", "ghost", { type: "open-meridian" }),
      secondary: btn(t("calm.cancel"), "calm-cancel", "ghost", { type: "cancel-pairing" }),
    });
  }

  /** "Try again" that visibly does something even when the answer is the same as before (see RETRY_FEEDBACK_MS). */
  function retryButton(label: string, cls: string): HTMLButtonElement {
    const busy = Date.now() < ps.retryUntil;
    const el = btn(busy ? t("calm.retrying") : label, "calm-retry", cls, () => {
      ctx.emit({ type: "retry-connection" });
      const before = announced;
      ps.retryUntil = Date.now() + RETRY_FEEDBACK_MS;
      announcer.textContent = "";
      announcer.textContent = t("calm.retrying");
      render(true);
      ctx.later(() => {
        ps.retryUntil = 0;
        if (!alive || !view) return;
        render(true);
        // the answer is the same problem: say so again instead of staying silent (a changed answer was announced when it came)
        if (announced === before) announceConnection(view, view, true);
      }, RETRY_FEEDBACK_MS);
    });
    if (busy) el.setAttribute("aria-busy", "true");
    return el;
  }

  function ambiguousCard(ports: number[]): HTMLElement {
    return calm({
      tone: "warn", icon: "alert", title: t("calm.ambiguous"), hint: t("calm.ambiguous.hint", { ports: ports.join(", ") }), testid: "calm-ambiguous",
      primary: retryButton(t("calm.retry"), "primary"),
    });
  }

  function connectionCard(v: PanelView): HTMLElement | null {
    const phase = v.connection.phase;
    if (isConnecting(phase)) return calm({ tone: "info", icon: "spinner", title: t("calm.connecting"), testid: "calm-connecting" });
    const ambiguous = ambiguousPorts(v.connection);
    if (ambiguous) return ambiguousCard(ambiguous);
    switch (phase) {
      case "ready": return null;
      case "no_bridge":
        return calm({
          tone: "info", icon: "plug", title: t("calm.no_bridge"), hint: t("calm.no_bridge.hint"), testid: "calm-no_bridge",
          primary: btn(t("calm.openMeridian"), "calm-open-meridian", "primary", { type: "open-meridian" }),
          secondary: retryButton(t("calm.retryNow"), "ghost"),
        });
      case "unpaired": {
        const port = portOf(v.connection.port);
        return calm({
          tone: "info", icon: "plug", title: t("calm.unpaired"), hint: port !== null ? t("calm.unpaired.hint.port", { port }) : t("calm.unpaired.hint"), testid: "calm-unpaired",
          primary: btn(t("calm.connect"), "calm-connect", "primary", { type: "connect" }),
        });
      }
      case "pairing": return pairingCard(v);
      case "proof_failed":
        return calm({
          tone: "warn", icon: "alert", title: t("calm.proof_failed"), hint: t("calm.proof_failed.hint"), testid: "calm-proof_failed",
          primary: retryButton(t("calm.retry"), "primary"),
        });
      case "revoked":
        return calm({
          tone: "info", icon: "plug", title: t("calm.revoked"), hint: t("calm.revoked.hint"), testid: "calm-revoked",
          primary: btn(t("calm.connectAgain"), "calm-connect", "primary", { type: "connect" }),
        });
      case "disabled":
        return calm({
          tone: "info", icon: "info", title: t("calm.disabled"), hint: t("calm.disabled.hint"), testid: "calm-disabled",
          primary: btn(t("calm.openMeridian"), "calm-open-meridian", "primary", { type: "open-meridian" }),
        });
      case "restoring":
        return calm({ tone: "info", icon: "refresh", title: t("calm.restoring"), hint: t("calm.restoring.hint"), testid: "calm-restoring" });
      default:
        return calm({
          tone: "info", icon: "info", title: t("calm.error"), hint: t("calm.error.hint"), testid: "calm-error",
          primary: retryButton(t("calm.retry"), "primary"),
        });
    }
  }

  function noteCard(kind: NoteKind, text: string, hint: string | null, action: HTMLElement | null, testid: string): HTMLElement {
    const close = iconBtn("close", t("note.dismiss"), `${testid}-dismiss`, () => { ps.dismissed[kind] = Date.now(); persist(); render(true); });
    close.classList.add("small");
    return h("div", { class: "note-card", testid, role: "group" },
      h("span", { class: "calm-icon", "aria-hidden": "true" }, icon("info", 16)),
      h("div", { class: "note-text" }, h("div", { class: "note-title" }, text), hint ? h("div", { class: "calm-hint" }, hint) : null, action),
      close);
  }

  // ---- duplicate application, end-of-form review, first use, key cheat sheet -------------------------------------------------
  /** "You already applied": the one thing on the panel that must not be missed (CPL-13). Continue is the person's call, never the default. */
  function duplicateBanner(v: PanelView): HTMLElement | null {
    const dup = v.duplicate;
    if (!dup || ps.dupHidden) return null;
    const applied = dup.state === "applied";
    const title = applied ? t("dup.applied.title") : t("dup.preparing.title");
    const hint = duplicateHint(dup);
    const proceed = btn(t("dup.continue"), "dup-continue", "primary", () => { ps.dupHidden = true; ctx.emit({ type: "duplicate-continue" }); render(true); });
    return h("div", { class: `dup ${applied ? "tone-inferred" : "tone-info"}`, role: "group", "aria-label": title, testid: "panel-duplicate", "data-state": dup.state },
      h("div", { class: "calm-head" },
        h("span", { class: "calm-icon", "aria-hidden": "true" }, icon(applied ? "alert" : "info", 18)),
        h("div", { class: "calm-text" }, h("div", { class: "calm-title", testid: "dup-title" }, title), h("div", { class: "calm-hint", testid: "dup-hint" }, hint))),
      h("div", { class: "calm-actions" }, proceed, btn(t("dup.close"), "dup-close", "ghost", { type: "deactivate" })));
  }

  /** A card's title as the control that folds the card: a button with a chevron (aria-expanded), so the fold is reachable and named. */
  function foldTitle(text: string, open: boolean, controls: string, testid: string, textTestid: string, onToggle: () => void): HTMLButtonElement {
    const toggle = h("button", { type: "button", class: `fold${open ? " open" : ""}`, "aria-expanded": String(open), "aria-controls": controls, testid, "data-fid": testid },
      h("span", { class: "fold-t", dir: "auto", testid: textTestid }, text), icon("chevron", 12));
    ctx.onTrustedClick(toggle, onToggle);
    return toggle;
  }

  /** "N required questions are still empty": a reminder with a way to each of them, never a gate (FR-03). */
  function reviewCard(v: PanelView): HTMLElement | null {
    const review = v.review;
    if (!review || ps.reviewHidden || review.missingRequired <= 0) return null;
    const shown = review.items.slice(0, REVIEW_ITEMS);
    const rest = review.missingRequired - shown.length;
    const close = iconBtn("close", t("review.dismiss"), "review-dismiss", () => { ps.reviewHidden = true; ctx.emit({ type: "review-dismiss" }); render(true); });
    close.classList.add("small");
    const list = h("div", { class: "review-list", role: "group", "aria-label": t("review.region"), testid: "review-list" });
    const rows: HTMLElement[] = [];
    for (const item of shown) {
      const label = plainLabel(item.label);
      // the whole question is the row's name (a screen reader) and its tooltip (a mouse); the row shows two lines of it
      const row = h("button", { type: "button", class: "p-item st-needs_you", "data-fid": `review:${item.key}`, "data-key": item.key, testid: "review-item", "aria-label": t("progress.goto", { label }) },
        h("span", { class: "p-status", "aria-hidden": "true" }, icon("info", 13)), h("span", { class: "p-label wrap", dir: "auto", title: label }, label));
      ctx.onTrustedClick(row, () => ctx.emit({ type: "focus-item", key: item.key }));
      list.append(row);
      rows.push(row);
    }
    rove(list, rows, { axis: "vertical", active: ps.roving.review, onActive: (fid) => { ps.roving.review = fid; } });
    if (rest > 0) list.append(h("div", { class: "muted small-text review-more", testid: "review-more" }, t("review.more", { n: formatNumber(rest) })));
    const open = ps.reviewOpen;
    const fold = foldTitle(tn("review.title", review.missingRequired), open, "p-review", "review-fold", "review-title", () => { ps.reviewOpen = !ps.reviewOpen; render(true, "review-fold"); });
    return h("div", { class: "note-card review", role: "group", "aria-label": t("review.region"), testid: "panel-review" },
      h("div", { class: "nc-head" }, h("span", { class: "calm-icon", "aria-hidden": "true" }, icon("list", 16)), fold, close),
      h("div", { class: `nc-body${open ? "" : " is-hidden"}`, id: "p-review", testid: "review-body" }, h("div", { class: "calm-hint", testid: "review-hint" }, t("review.hint")), list));
  }

  /** Three steps and the key to start with, until the person has used the assistant once or says "Got it" (CPL-18). */
  function firstUseCard(v: PanelView): HTMLElement | null {
    if (!v.firstUse || v.noForm || noteDismissed("first_use")) return null;
    const steps = ["first.step1", "first.step2", "first.step3"] as const;
    const list = h("ul", { class: "steps", testid: "first-steps" },
      ...steps.map((key, index) => h("li", null, h("span", { class: "step-n", "aria-hidden": "true" }, formatNumber(index + 1)), h("span", { class: "step-t" }, t(key)))));
    const shortcut = activationShortcut(ctx.mac);
    const keys = h("button", { type: "button", class: "link", testid: "first-keys", "data-fid": "first-keys" }, t("first.keys"));
    ctx.onTrustedClick(keys, () => toggleHelp(true));
    const open = ps.firstOpen;
    const fold = foldTitle(t("first.title"), open, "p-first", "first-fold", "first-title", () => { ps.firstOpen = !ps.firstOpen; render(true, "first-fold"); });
    return h("div", { class: "note-card first", role: "group", "aria-label": t("first.title"), testid: "panel-first-use" },
      h("div", { class: "nc-head" }, h("span", { class: "calm-icon", "aria-hidden": "true" }, icon("info", 16)), fold),
      h("div", { class: `nc-body${open ? "" : " is-hidden"}`, id: "p-first", testid: "first-body" }, list,
        shortcut ? h("div", { class: "calm-hint", testid: "first-shortcut" }, t("first.shortcut", { keys: shortcut })) : null,
        h("div", { class: "calm-actions" }, btn(t("first.dismiss"), "first-dismiss", "ghost small", () => { ps.dismissed.first_use = Date.now(); persist(); render(true); }), keys)));
  }

  function toggleHelp(open: boolean): void {
    if (ps.help === open) return;
    ps.help = open;
    ps.bodyScroll = 0; // the sheet and the panel are different pages: neither inherits the other's scroll
    if (open) ctx.emit({ type: "keys-help" });
    render(true, open ? "help-back" : "panel-help");
  }

  /** Every key the assistant answers to, in the panel itself: reading it takes no one away from the form (UX-12, UX-10). */
  function helpSheet(): HTMLElement {
    const alt = altLabel(ctx.mac);
    const shift = shiftLabel(ctx.mac, t("key.shift"));
    const rows: [string, string][] = [
      ["Tab", t("help.row.tab")], [`${shift}Tab`, t("help.row.shiftTab")], [enterKeyName(), t("help.row.enter")], ["Esc", t("help.row.esc")],
      [`${alt}R`, t("help.row.rewrite")], [`${alt}Z`, t("help.row.undo")], [`${chipKeyLabel(ctx.mac, 1)} – ${chipKeyLabel(ctx.mac, 7)}`, t("help.row.chip")],
      [panelShortcut(ctx.mac, t("key.shift")), t("help.row.panel")], [altShiftKey(ctx.mac, t("key.shift"), "Z"), t("help.row.undoAll")],
      ["← ↑ ↓ →", t("help.row.arrows")],
    ];
    const shortcut = activationShortcut(ctx.mac);
    if (shortcut) rows.push([shortcut, t("help.row.toggle")]);
    const list = h("ul", { class: "keys", testid: "help-keys" },
      ...rows.map(([combo, text]) => h("li", { testid: "help-row" }, h("kbd", null, combo), h("span", { class: "key-t", dir: "auto" }, text))));
    // The sheet is the quick answer; the options page holds the long one (what each colour means, what is never filled).
    const full = h("button", { type: "button", class: "link", testid: "help-full", "data-fid": "help-full" }, t("help.full"), icon("external", 12));
    ctx.onTrustedClick(full, () => ctx.emit({ type: "open-options", anchor: "help" }));
    const sheet = h("div", { class: "help", id: "p-help", role: "group", "aria-label": t("help.region"), testid: "help-sheet" },
      h("div", { class: "calm-title", testid: "help-title" }, t("help.title")), list,
      h("div", { class: "calm-actions" }, btn(t("help.back"), "help-back", "ghost small", () => toggleHelp(false)), full));
    // Esc leaves the sheet, as it leaves the research form: a keyboard user is never stuck in it
    sheet.addEventListener("keydown", (ev) => {
      if (ev.isTrusted && ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); toggleHelp(false); }
    });
    return sheet;
  }

  // ---- research strip ---------------------------------------------------------------------------------------------
  function researchHeadline(r: ResearchView): string {
    const company = r.company.trim();
    const params = { company };
    switch (r.state) {
      case "queued": return company ? t("research.queued", params) : t("research.queued.noCompany");
      case "running": return company ? t("research.running", params) : t("research.running.noCompany");
      case "done":
        if (r.quality === "none") return company ? t("research.done.full", params) : t("research.done.minimal");
        if (r.quality === "company_only" && !company) return t("research.done.minimal");
        if (r.quality === "full" && !company) return t("research.done.job_only");
        return t(`research.done.${r.quality}` as StringKey, params);
      case "partial": return company ? t("research.partial", params) : t("research.partial.noCompany");
      case "failed": return company ? t("research.failed", params) : t("research.failed.noCompany");
      case "needs_input":
        if (r.needsCompany || (!company && !r.needsSite)) return t("research.needs.company");
        return company ? t("research.needs.site", params) : t("research.needs.site.noCompany");
      case "cancelled": return t("research.cancelled");
      default: return r.headline;
    }
  }

  function sourceList(r: ResearchView): HTMLElement {
    const list = h("ul", { class: "r-list", testid: "research-sources" });
    if (r.sources.length === 0) return h("div", { class: "muted small-text" }, t("research.noSources"));
    for (const src of r.sources) {
      const status = (src.status in SOURCE_ICON ? src.status : "skipped") as ResearchSource["status"];
      const href = safeHref(src.url);
      const label = truncate(src.label || hostOf(src.url) || src.url, 48);
      const stateText = t(`research.status.${status}` as StringKey);
      const body: Child[] = [
        h("span", { class: `r-ic st-${status}`, title: stateText }, icon(SOURCE_ICON[status], 13), h("span", { class: "sr" }, stateText)),
      ];
      if (href && status === "ok") {
        const link = h("a", { class: "r-link", href, target: "_blank", rel: "noopener noreferrer", testid: "research-source", "data-fid": `src-${hostOf(src.url)}-${label}` }, h("span", { class: "r-label", dir: "auto" }, label), icon("external", 12));
        link.addEventListener("click", (ev) => { if (!ev.isTrusted) ev.preventDefault(); });
        body.push(link);
      } else {
        body.push(h("span", { class: "r-link plain", testid: "research-source", title: stateText }, h("span", { class: "r-label", dir: "auto" }, label)));
      }
      list.append(h("li", { class: `r-item st-${status}` }, ...body));
    }
    return list;
  }

  /** What "not now" is remembered against: the question and the company it was asked about (a new company is a new question). */
  function needKeyOf(kind: FormKind, r: ResearchView): string {
    return `${kind}:${r.company.trim().toLowerCase()}`;
  }

  function researchForm(kind: FormKind, needKey: string): HTMLElement {
    const label = kind === "company" ? t("research.form.company") : t("research.form.site");
    const input = h("input", {
      type: "text", class: "input", "aria-label": label, placeholder: label, maxlength: kind === "company" ? 120 : 253, autocomplete: "off",
      autocapitalize: "off", spellcheck: "false", "data-fid": "form-input", "data-lpignore": "true", "data-1p-ignore": "true", testid: `research-input-${kind}`,
      "aria-invalid": ps.formError ? "true" : null,
    });
    input.value = ps.formValue;
    const go = btn(t("research.form.go"), `research-go-${kind}`, "primary small", () => submit(kind), { fid: "form-go" });
    const opener = kind === "company" ? "research-not-this" : "research-add-site";
    // Cancel, Esc and sending all close the form for good: a form the person has answered or declined never comes back by itself
    const close = (): void => { ps.form = null; ps.formValue = ""; ps.formError = false; ps.needsDismissed.add(needKey); render(true, opener); };
    const cancel = btn(t("research.form.cancel"), `research-cancel-${kind}`, "ghost small", close, { fid: "form-cancel" });
    const submit = (k: FormKind): void => {
      const raw = ps.formValue.trim();
      if (k === "company") {
        if (!raw) return;
        ctx.emit({ type: "research-company", company: raw });
      } else {
        const domain = normaliseDomain(raw);
        if (!domain) { ps.formError = true; render(true); return; }
        ctx.emit({ type: "research-site", domain });
      }
      close();
    };
    input.addEventListener("input", () => { ps.formValue = input.value; if (ps.formError) { ps.formError = false; input.removeAttribute("aria-invalid"); wrapper.querySelector(".err")?.remove(); } });
    // What is typed into our field must not reach page shortcuts or be readable by page listeners in the bubble phase
    // (InputEvent.data is composed and would otherwise cross the shadow boundary).
    for (const type of ["beforeinput", "input", "compositionstart", "compositionupdate", "compositionend", "paste", "cut", "copy"] as const) {
      input.addEventListener(type, (ev) => ev.stopPropagation());
    }
    for (const type of ["keydown", "keyup", "keypress"] as const) {
      input.addEventListener(type, (ev) => {
        ev.stopPropagation();
        if (type === "keydown" && ev.isTrusted) {
          if (ev.key === "Enter") { ev.preventDefault(); submit(kind); }
          else if (ev.key === "Escape") { ev.preventDefault(); close(); }
        }
      });
    }
    const wrapper = h("div", { class: "p-form", testid: `research-form-${kind}` }, input, h("div", { class: "form-actions" }, go, cancel));
    // Esc on the Research or Cancel button closes the form as well as Esc in the field (the field handles its own)
    wrapper.addEventListener("keydown", (ev) => {
      if (ev.isTrusted && ev.key === "Escape" && !ev.defaultPrevented && !ev.isComposing) { ev.preventDefault(); ev.stopPropagation(); close(); }
    });
    if (ps.formError) wrapper.append(h("div", { class: "err", role: "alert", testid: "research-form-error" }, t("research.form.siteInvalid")));
    return wrapper;
  }

  function researchStrip(r: ResearchView): HTMLElement | null {
    if (r.state === "none" && !r.company) return null;
    const running = r.state === "queued" || r.state === "running";
    // The form opens by itself only when the research cannot go on without an answer (state needs_input), and only until the person
    // has dismissed it. A website that merely could not be confirmed is a sentence and a link, never a form that appears unprompted.
    const wanted: FormKind | null = r.state === "needs_input" ? (r.needsCompany ? "company" : r.needsSite ? "site" : null) : null;
    const auto = wanted !== null && !ps.needsDismissed.has(needKeyOf(wanted, r)) ? wanted : null;
    const form: FormKind | null = ps.form ?? auto;
    const okCount = r.sources.filter((s) => s.status === "ok").length;
    const strip = h("div", { class: `p-research st-${r.state}`, testid: "panel-research", "data-state": r.state, role: "group", "aria-label": t("research.region") });
    const status = h("div", { class: "r-head", testid: "research-headline" },
      running ? h("span", { class: "pulse", "aria-hidden": "true" }) : h("span", { class: "r-lead", "aria-hidden": "true" }, icon(r.state === "failed" ? "alert" : r.state === "needs_input" ? "search" : "check", 14)),
      h("span", { class: "r-text" }, researchHeadline(r)));
    strip.append(status);
    if (r.needsSite && r.state !== "needs_input" && !form) {
      const company = r.company.trim();
      strip.append(h("div", { class: "r-need", testid: "research-need" }, company ? t("research.needs.site", { company }) : t("research.needs.site.noCompany")));
    }
    const facts: Child[] = [];
    if (okCount > 0) facts.push(h("span", { class: "tag", testid: "research-count" }, tn("research.sources", okCount)));
    if (r.quality !== "none" && r.quality !== "full" && !running) facts.push(h("span", { class: "tag soft", testid: "research-quality" }, t(`research.quality.${r.quality}` as StringKey)));
    if (r.site) facts.push(h("span", { class: "tag soft", dir: "auto", testid: "research-site", title: t("research.site", { site: r.site }), "aria-label": t("research.site", { site: r.site }) }, truncate(r.site, 40)));
    if (facts.length > 0) strip.append(h("div", { class: "r-facts" }, ...facts));
    const actions = h("div", { class: "r-actions", role: "toolbar", "aria-label": t("research.actions"), testid: "research-actions" });
    if (r.sources.length > 0) {
      const toggle = h("button", { type: "button", class: "link", "aria-expanded": String(ps.sources), "aria-controls": "p-sources", testid: "research-toggle", "data-fid": "research-toggle" },
        icon("chevron", 12), ps.sources ? t("research.hide") : t("research.show"));
      toggle.classList.toggle("open", ps.sources);
      ctx.onTrustedClick(toggle, () => { ps.sources = !ps.sources; render(true); });
      actions.append(toggle);
    }
    if (!form) {
      const notThis = h("button", { type: "button", class: "link", testid: "research-not-this", "data-fid": "research-not-this" }, t("research.notThis"));
      ctx.onTrustedClick(notThis, () => { ps.form = "company"; ps.formValue = ""; render(true, "form-input"); });
      actions.append(notThis);
      const addSite = h("button", { type: "button", class: "link", testid: "research-add-site", "data-fid": "research-add-site" }, t("research.addSite"));
      ctx.onTrustedClick(addSite, () => { ps.form = "site"; ps.formValue = ""; render(true, "form-input"); });
      actions.append(addSite);
    }
    const canRefresh = !running && r.state !== "none";
    const again = h("button", { type: "button", class: "link", testid: "research-refresh", "data-fid": "research-refresh" }, icon("refresh", 12), t("research.refresh"));
    ctx.onTrustedClick(again, () => ctx.emit({ type: "research-refresh" }));
    // "Research again" lives with the sources when there are some; otherwise it is offered right in the action row
    if (canRefresh && !form && r.sources.length === 0) actions.append(again);
    rove(actions, [...actions.querySelectorAll<HTMLElement>("button")], { axis: "both", active: ps.roving.research, onActive: (fid) => { ps.roving.research = fid; } });
    strip.append(actions);
    if (ps.sources && r.sources.length > 0) {
      strip.append(h("div", { id: "p-sources", class: "r-sources" }, sourceList(r), h("div", { class: "muted small-text", testid: "research-privacy" }, t("research.privacy")), canRefresh ? again : null));
    }
    if (form) strip.append(researchForm(form, needKeyOf(form, r)));
    return strip;
  }

  // ---- progress -------------------------------------------------------------------------------------------------
  function progressItem(item: ProgressItem): HTMLElement {
    const statusText = t(`progress.status.${item.status}` as StringKey);
    const ic = ITEM_ICON[item.status];
    const tone = item.status === "inserted" ? (item.tone ?? "verified") : "";
    const label = plainLabel(item.label);
    const el = h("button", {
      type: "button", class: `p-item st-${item.status}${tone ? ` tone-${tone}` : ""}${item.current ? " current" : ""}`,
      "data-fid": `item:${item.key}`, "data-key": item.key, "aria-current": item.current ? "true" : null, testid: "progress-item",
      "aria-label": `${t("progress.goto", { label })}. ${statusText}${item.required ? `, ${t("progress.required")}` : ""}`,
    },
    h("span", { class: "p-status", "aria-hidden": "true" }, ic ? icon(ic, 13) : null),
    // the row shows one line of the question; the tooltip (the row's own name is the aria-label above) shows all of it
    h("span", { class: "p-label", dir: "auto", title: label }, label),
    item.required ? h("span", { class: "req", "aria-hidden": "true", title: t("progress.required") }, "*") : null);
    ctx.onTrustedClick(el, () => ctx.emit({ type: "focus-item", key: item.key }));
    return el;
  }

  function progress(v: PanelView): HTMLElement | null {
    const { total, done, items } = v.progress;
    if (total <= 0 && items.length === 0) return null;
    const inserted = items.filter((i) => i.status === "inserted").length;
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const wrap = h("div", { class: "p-progress", testid: "panel-progress" });
    wrap.append(h("div", { class: "p-sum" },
      h("span", { class: "sum-main", testid: "progress-summary" }, t("progress.summary", { done: formatNumber(done), total: formatNumber(total) })),
      h("span", { class: "sum-sub", testid: "progress-inserted" }, tn("progress.inserted", inserted))));
    const bar = h("div", { class: "bar", role: "progressbar", "aria-label": t("progress.label"), "aria-valuemin": 0, "aria-valuemax": total, "aria-valuenow": done, testid: "progress-bar" },
      h("span", { class: "bar-fill" }));
    (bar.firstElementChild as HTMLElement).style.setProperty("width", `${pct}%`);
    wrap.append(bar);
    const list = h("div", { class: "p-list", role: "group", "aria-label": t("progress.fields"), testid: "progress-list", tabindex: -1 });
    let currentSection: string | null = null;
    const rows: HTMLElement[] = [];
    for (const item of items) {
      const sec = item.section.trim() || t("progress.section.other");
      if (sec !== currentSection) {
        currentSection = sec;
        list.append(h("div", { class: "p-sec", dir: "auto", testid: "progress-section" }, truncate(sec, 48)));
      }
      const row = progressItem(item);
      rows.push(row);
      list.append(row);
    }
    // The whole list is ONE stop of the Tab order (arrows, Home and End move inside it). Tab lands on the row of the field the person
    // is on, or on the row they last moved to while they are still inside the list.
    const here = items.find((item) => item.current);
    rove(list, rows, { axis: "vertical", active: ps.roving.progress ?? (here ? `item:${here.key}` : undefined), onActive: (fid) => { ps.roving.progress = fid; } });
    list.addEventListener("focusout", (ev) => {
      if (!(ev.relatedTarget instanceof Node) || !list.contains(ev.relatedTarget)) delete ps.roving.progress;
    });
    wrap.append(list);
    return wrap;
  }

  /** "Fill these N answers?": every field Fill verified will fill and the value that goes in, before anything is written (SEC-01). */
  function fillPreviewBlock(preview: NonNullable<PanelView["fillPreview"]>): HTMLElement {
    const items = preview.items.slice(0, FILL_PREVIEW_ITEMS);
    const list = h("ul", { class: "fp-list", tabindex: -1, testid: "fill-preview-list" },
      ...items.map((item) => {
        const label = plainLabel(item.label);
        const value = item.value.replace(/\s+/g, " ").trim();
        return h("li", { class: "fp-item", testid: "fill-preview-item" },
          h("span", { class: "fp-label", dir: "auto", title: label }, truncate(label, 60)), h("span", { class: "fp-value", dir: "auto", title: value }, truncate(value, 90)));
      }));
    const rest = Math.max(0, preview.count - items.length);
    const confirm = btn(t("act.fillConfirm"), "fill-confirm", "primary", { type: "fill-confirm" }, { icon: "check" });
    const cancel = btn(t("calm.cancel"), "fill-cancel", "ghost", { type: "fill-cancel" });
    const block = h("div", { class: "fill-preview", role: "group", "aria-label": t("fill.preview.region"), testid: "panel-fill-preview" },
      h("div", { class: "calm-title", testid: "fill-preview-title" }, tn("fill.preview.title", preview.count)), list,
      rest > 0 ? h("div", { class: "muted small-text", testid: "fill-preview-more" }, t("fill.preview.more", { n: formatNumber(rest) })) : null,
      h("div", { class: "calm-actions" }, confirm, cancel));
    // Esc closes the question, as everywhere else in the panel: nothing was written
    block.addEventListener("keydown", (ev) => {
      if (ev.isTrusted && ev.key === "Escape" && !ev.isComposing) { ev.preventDefault(); ev.stopPropagation(); ctx.emit({ type: "fill-cancel" }); }
    });
    return block;
  }

  function actions(v: PanelView): HTMLElement {
    const box = h("div", { class: "p-actions", testid: "panel-actions" });
    const n = v.fillableCount;
    if (v.fillPreview && v.fillPreview.count > 0 && v.fillPreview.items.length > 0) {
      box.classList.add("asking");
      box.append(fillPreviewBlock(v.fillPreview));
    } else {
      const fill = btn(t("act.fillVerified", { n: formatNumber(n) }), "panel-fill-verified", "primary block", { type: "fill-verified" }, { disabled: n <= 0, icon: "check" });
      box.append(fill, h("div", { class: "shint", testid: "panel-fill-hint" }, n > 0 ? t("act.fillVerified.hint") : t("act.fillVerified.none")));
    }
    // "Fill verified" put several answers in at once: they can all be taken back for a few seconds. Below the button, so that the
    // button the person just pressed does not move under the pointer when this appears.
    const batch = v.batchUndo?.count ?? 0;
    if (batch > 0) {
      box.append(h("div", { class: "p-batch", role: "group", testid: "panel-batch-undo" },
        h("span", { testid: "batch-count" }, tn("toast.filled.count", batch)),
        withShortcut(btn(t("act.undoAll"), "panel-undo-all", "ghost small", { type: "undo-all" }, { icon: "undo" }), "Alt+Shift+Z")));
    }
    if (v.pendingDrafts > 0) {
      box.append(h("div", { class: "p-drafts", testid: "panel-drafts" }, h("span", { class: "pulse", "aria-hidden": "true" }), tn("act.drafts", v.pendingDrafts)));
    }
    box.append(switchRow(t("act.keys.label"), v.keysPaused ? t("act.keys.hint.off") : null, !v.keysPaused, "panel-keys", () => ctx.emit({ type: "toggle-keys" })));
    return box;
  }

  function footer(v: PanelView): HTMLElement {
    const foot = h("div", { class: "p-foot", testid: "panel-footer" });
    const links = h("nav", { class: "links", "aria-label": t("footer.nav") });
    const settings = h("button", { type: "button", class: "link", testid: "panel-settings", "data-fid": "panel-settings" }, t("footer.settings"));
    ctx.onTrustedClick(settings, () => ctx.emit({ type: "open-options", anchor: "behaviour" }));
    // Help is the key cheat sheet, inside the panel: nothing opens, nobody leaves the form (UX-12)
    const help = h("button", { type: "button", class: "link", "aria-expanded": String(ps.help), "aria-controls": "p-help", testid: "panel-help", "data-fid": "panel-help" }, t("footer.help"));
    ctx.onTrustedClick(help, () => { toggleHelp(!ps.help); });
    links.append(settings, help);
    rove(links, [settings, help], { axis: "horizontal", active: ps.roving.footer, onActive: (fid) => { ps.roving.footer = fid; } });
    foot.append(links);
    const spent = v.generation.spentUsd;
    if (typeof spent === "number" && spent > 0) {
      const limit = v.generation.limitUsd;
      const text = typeof limit === "number" && limit > 0 ? t("footer.spendOf", { spent: formatMoney(spent), limit: formatMoney(limit) }) : t("footer.spend", { spent: formatMoney(spent) });
      foot.append(h("span", { class: "spend", testid: "panel-spend" }, text));
    }
    return foot;
  }

  // ---- whole panel ------------------------------------------------------------------------------------------------
  function pill(v: PanelView): HTMLElement {
    const phase = v.connection.phase;
    const { done, total } = v.progress;
    const showCount = phase === "ready" && total > 0;
    const base = showCount ? t("panel.pill", { done: formatNumber(done), total: formatNumber(total) }) : t("panel.pillNoForm");
    // the duplicate warning must survive a collapsed panel: the pill says so (an amber dot and its name)
    const dup = v.duplicate && !ps.dupHidden && phase === "ready" && !v.orphaned ? v.duplicate.state : null;
    const label = dup ? `${base}. ${t(dup === "applied" ? "dup.pill.applied" : "dup.pill.preparing")}` : base;
    const el = h("button", { type: "button", class: "pill-btn", "aria-expanded": "false", "aria-label": label, title: t("panel.expand"), testid: "panel-pill", "data-fid": "toggle", "data-phase": phase, "data-dup": dup },
      markIcon(20), showCount ? h("span", { class: "pill-count" }, `${formatNumber(done)}/${formatNumber(total)}`) : null,
      dup ? h("span", { class: "pill-dup", "aria-hidden": "true", testid: "pill-dup" }, icon("alert", 12)) : null,
      h("span", { class: `conn conn-${statusOf(v)}`, "aria-hidden": "true" }));
    ctx.onTrustedClick(el, () => ctx.emit({ type: "toggle-collapse" }));
    return el;
  }

  function body(v: PanelView): HTMLElement {
    const box = h("div", { class: "p-body", testid: "panel-body", tabindex: -1 });
    if (v.orphaned) {
      box.append(calm({
        tone: "warn", icon: "refresh", title: t("calm.orphaned"), hint: t("calm.orphaned.hint"), testid: "calm-orphaned",
        primary: btn(t("calm.reload"), "calm-reload", "primary", () => { try { location.reload(); } catch { /* blocked */ } }),
      }));
      return box;
    }
    const card = connectionCard(v);
    if (card) { box.append(card); return box; }
    if (ps.help) { box.append(helpSheet()); return box; }
    // the duplicate warning is never folded away or scrolled out of sight; every other card shares one capped area, so that a pile of
    // them never pushes the form's list and Fill verified off the panel
    const dup = duplicateBanner(v);
    if (dup) box.append(dup);
    const notices = h("div", { class: "p-notices", tabindex: -1, testid: "panel-notices" });
    for (const notice of [reviewCard(v), firstUseCard(v)]) if (notice) notices.append(notice);
    if (v.connection.extensionOutdated) {
      notices.append(calm({
        tone: "info", icon: "refresh", title: t("calm.update"), testid: "calm-update",
        primary: btn(t("calm.update.open"), "calm-update-open", "ghost small", { type: "open-options", anchor: "connection" }),
      }));
    }
    if (v.profileEmpty) {
      notices.append(calm({
        tone: "info", icon: "info", title: t("calm.profileEmpty"), hint: t("calm.profileEmpty.hint"), testid: "calm-profile-empty",
        primary: btn(t("calm.openMeridian"), "calm-open-meridian", "primary small", { type: "open-meridian" }),
      }));
    }
    // the writing-model note belongs to a model KNOWN to be missing: until Meridian has said, no one sees it flash (`known` false)
    if (!v.generation.available && v.generation.known !== false && !noteDismissed("drafts_unavailable") && !v.generation.budgetExhausted) {
      notices.append(noteCard("drafts_unavailable", t("note.drafts"), null, btn(t("calm.openMeridian"), "note-open-meridian", "ghost small", { type: "open-meridian" }), "note-drafts"));
    } else if (v.generation.budgetExhausted && !noteDismissed("budget")) {
      notices.append(noteCard("budget", t("note.budget"), null, btn(t("calm.openMeridian"), "note-open-meridian", "ghost small", { type: "open-meridian" }), "note-budget"));
    }
    // Meridian used up the drafts it writes on its own for this page: long answers are still one click away ("Draft with AI"), and the
    // person is told why no draft appeared by itself. Only about this page, so closing it is remembered for this page alone.
    if (v.generation.draftsPaused && !ps.pausedHidden && v.generation.available && !v.generation.budgetExhausted) {
      const close = iconBtn("close", t("note.dismiss"), "note-drafts-paused-dismiss", () => { ps.pausedHidden = true; render(true); });
      close.classList.add("small");
      notices.append(h("div", { class: "note-card", testid: "note-drafts-paused", role: "group" },
        h("span", { class: "calm-icon", "aria-hidden": "true" }, icon("info", 16)),
        h("div", { class: "note-text" }, h("div", { class: "note-title", dir: "auto" }, t("note.draftsPaused"))), close));
    }
    if (v.permissions.crossOriginFrames > 0 && !v.permissions.allSites && !noteDismissed("cross_origin")) {
      notices.append(noteCard("cross_origin", t("note.crossOrigin"), t("note.crossOrigin.hint"),
        btn(t("note.allowAll"), "note-allow-all", "ghost small", { type: "open-options", anchor: "permissions" }), "note-cross-origin"));
    }
    if (notices.childElementCount > 0) box.append(notices);
    const research = researchStrip(v.research);
    if (research) box.append(research);
    if (v.noForm) {
      box.append(calm({ tone: "info", icon: "search", title: t("calm.noForm"), hint: t("calm.noForm.hint"), testid: "calm-no-form" }));
      return box;
    }
    const prog = progress(v);
    if (prog) box.append(prog);
    box.append(actions(v));
    return box;
  }

  function signature(v: PanelView): string {
    return JSON.stringify([
      v, ps.moreOpen, ps.sources, ps.form, ps.formError, ps.dock, ps.dismissed, expired, ctx.mac,
      ps.help, ps.dupHidden, ps.reviewHidden, ps.pausedHidden, ps.reviewOpen, ps.firstOpen, [...ps.needsDismissed],
      v.connection.pairing ? Math.ceil((expiryMs(v.connection.pairing.expiresAt) - Date.now()) / 1000) <= 0 : null,
    ]);
  }

  function render(force: boolean, focusFid?: string): void {
    if (!view) return;
    const v = view;
    const sig = signature(v);
    if (!force && sig === lastSig) return;
    lastSig = sig;
    const list = section.querySelector<HTMLElement>(".p-list");
    if (list) ps.listScroll = list.scrollTop;
    const oldBody = section.querySelector<HTMLElement>(".p-body");
    if (oldBody) ps.bodyScroll = oldBody.scrollTop;
    const snap = captureFocus(ctx.root);
    section.setAttribute("aria-label", t("panel.region"));
    section.replaceChildren();
    cancelTick?.(); cancelTick = null;
    if (v.collapsed) {
      section.className = "panel-host is-pill";
      section.append(pill(v));
    } else {
      section.className = "panel-host";
      // with the menu open the body has little room left: Fill verified stops being pinned, it would take the whole of it
      const panel = h("div", { class: `panel${ps.moreOpen ? " has-menu" : ""}`, testid: "panel-card", "data-phase": v.connection.phase }, header(v));
      if (ps.moreOpen) panel.append(morePanel(v));
      panel.append(body(v));
      if (!v.orphaned && v.connection.phase === "ready") panel.append(footer(v));
      section.append(panel);
      const newBody = section.querySelector<HTMLElement>(".p-body");
      if (newBody && ps.bodyScroll > 0) newBody.scrollTop = ps.bodyScroll; // a re-render must not throw a scrolled panel back to its top
      const listEl = section.querySelector<HTMLElement>(".p-list");
      if (listEl) {
        const current = listEl.querySelector<HTMLElement>(".p-item.current");
        const currentKey = current?.getAttribute("data-key") ?? "";
        listEl.scrollTop = ps.listScroll;
        if (current && currentKey !== ps.lastCurrent) {
          const base = listEl.getBoundingClientRect().top - listEl.scrollTop;
          const topOf = (node: Element): number => node.getBoundingClientRect().top - base;
          const top = topOf(current);
          if (top < listEl.scrollTop || top + current.offsetHeight > listEl.scrollTop + listEl.clientHeight) {
            const wanted = Math.max(0, top - Math.round(listEl.clientHeight / 2 - current.offsetHeight / 2));
            // snap to a row boundary so no row is cut off at the top edge
            const snap = [...listEl.children].find((child) => topOf(child) >= wanted - 1);
            listEl.scrollTop = snap ? topOf(snap) : wanted;
          }
        }
        ps.lastCurrent = currentKey;
      }
    }
    if (focusFid) {
      const target = [...ctx.root.querySelectorAll<HTMLElement>("[data-fid]")].find((n) => n.getAttribute("data-fid") === focusFid);
      try { target?.focus({ preventScroll: true }); } catch { /* not focusable */ }
      // a question that opened below the fold (a short window): the answer that has the keyboard is on screen
      if (focusFid === "fill-confirm") target?.scrollIntoView({ block: "nearest" });
    } else if (!restoreFocus(ctx.root, snap) && snap) {
      // the control that had focus is gone or switched off (pressing Connect replaces it with the code card, Fill verified ends up disabled
      // when nothing is left to fill): keep the keyboard in the panel, on the control that carries on from it
      const wanted = (FOCUS_NEXT[snap.fid] ?? []).map((fid) => [...ctx.root.querySelectorAll<HTMLElement>("[data-fid]")].find((n) => n.getAttribute("data-fid") === fid))
        .find((node) => node && !(node as HTMLButtonElement).disabled);
      const next = wanted ?? section.querySelector<HTMLElement>(".p-body .btn:not([disabled]), .p-body .link, .pill-btn, .ibtn");
      try { next?.focus({ preventScroll: true }); } catch { /* not focusable */ }
    }
    startTick(v);
  }

  function startTick(v: PanelView): void {
    const pairing = v.connection.phase === "ready" ? null : v.connection.phase === "pairing" ? v.connection.pairing : null;
    if (!pairing || v.collapsed || v.orphaned) return;
    const tick = (): void => {
      const left = Math.max(0, Math.ceil((expiryMs(pairing.expiresAt) - Date.now()) / 1000));
      const node = section.querySelector(".countdown");
      if (left <= 0) { render(false); return; }
      if (node) node.textContent = t("calm.pairing.expires", { time: formatClock(left) });
      cancelTick = ctx.later(tick, 1000);
    };
    cancelTick = ctx.later(tick, 1000);
  }

  // Esc closes the open menu first and leaves the rest to the UI root, which tells the logic to give the keyboard back to the form
  section.addEventListener("keydown", (ev) => {
    if (!ev.isTrusted || ev.key !== "Escape" || ev.defaultPrevented || ev.isComposing || ev.ctrlKey || ev.altKey || ev.metaKey || ev.shiftKey) return;
    if (!ps.moreOpen) return;
    ev.preventDefault();
    ev.stopPropagation();
    ps.moreOpen = false;
    render(true, "panel-more");
  });

  load();
  applyDock();

  return {
    set(next: PanelView): void {
      const prev = view;
      view = next;
      // an answered banner or review comes back only as a new one: the logic dropping it re-arms it
      if (!next.duplicate) ps.dupHidden = false;
      if (!next.generation.draftsPaused) ps.pausedHidden = false;
      if (!next.review) ps.reviewHidden = false;
      if (next.collapsed || next.orphaned) ps.help = false; // the cheat sheet is for now: a panel put away comes back as the panel
      announceConnection(prev, next);
      announceNotices(prev, next);
      // the question "fill these answers?" opened while the keyboard was in the panel (the person pressed Fill verified): it goes to the answer
      const opened = !prev?.fillPreview && !!next.fillPreview && !!captureFocus(ctx.root);
      render(false, opened ? "fill-confirm" : undefined);
    },
    dismissNote(kind: NoteKind): void {
      ps.dismissed[kind] = Date.now();
      persist();
      if (view) render(true);
    },
    refresh(): void {
      if (view) render(true);
    },
    focus(): boolean {
      const first = section.querySelector<HTMLElement>(".pill-btn, .ibtn, .btn:not([disabled])");
      if (!first) return false;
      try { first.focus({ preventScroll: true }); } catch { return false; }
      return true;
    },
    dock: () => ps.dock,
    destroy(): void {
      alive = false;
      cancelTick?.(); cancelTick = null;
      section.remove();
      announcer.remove();
    },
  };
}
