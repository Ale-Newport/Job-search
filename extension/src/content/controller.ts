// Controller: ties scan, session, keyboard, fill, frames and submit-awareness to the visual layer. It owns the
// view models (ChipView / PanelView), the insertion bookkeeping (undo, marks, progress) and every UIAction.
// All page writes go through fill.ts; all bridge traffic through session.ts / sendBg.

import type { ConnectionState, CtxInfo, FieldDigest, StoredPrefs, ToContent } from "../shared/messages";
import type {
  FeedbackResponse, GenerationAvailability, GenerationStatus, JobSummary, PollEvent, RegenerateRequest, ResearchSnapshot, Suggestion,
} from "../shared/protocol";
import type { AssistUI, ChipView, DuplicateStatus, FieldMark, PanelView, ProgressItem, ToastView, TrackAskView, UIAction } from "./ui-api";
import { anchorElement, anchorRect, isObscured, memberRect, optionAreas } from "./anchors";
import { hostMatchesRules } from "../shared/hosts";
import { FrameCoordinator, type FramesHost } from "./frames";
import { fillField, isAsyncOperation, matches, readField, relationOf, type FillOutcome, type ValueReading } from "./fill";
import {
  focusAssistField, focusBlocked, focusTarget, KeyboardController, nextStop, releaseFocusProxies, type KeyboardHost, type NextStop, type TraversalEnv,
} from "./keyboard";
import { edgeTabStop, tabStopBetween, type TabScope } from "./tabstops";
import { buildPageReport, currentJobIdentity, jobIdentity } from "./page-report";
import { openPopupNear } from "./popups";
import { RootRegistry } from "./roots";
import { Scanner, type ScanDiff, type ScanResult } from "./scan";
import { frameIsHumanVisible } from "./visibility";
import { isFailure, SessionClient, type SuggestBatch, type SuggestContext } from "./session";
import {
  activeFailure, DEFAULT_SETTINGS, deriveStatus, digestTone, DRAFT_DWELL_MS, duplicateStatus, evaluate, INSERTABLE_STATES, looksLikeConsent, markFailed, Store,
  suggestedLabels, suggestedText, toneForState, UNDO_WINDOW_MS, type AssistField, type DraftBasis, type Eligibility, type InsertRecord,
  type SuggestionEntry, type UndoRecord,
} from "./state";
import { LearningLoop, SubmitWatch } from "./submit-watch";
import {
  after, chipActionPrefix, composedTarget, cut, deepActiveElement, every, guard, inUiHost, isOrphaned, isRendered, normalizeText, onFrame, onOrphaned,
  questionElement, rectOf, ruleOf, sendBg, sleep, soon, spawn, squash, swallowedErrors, type Rect,
} from "./util";

/** UXR-10: how long a dismissed first-use card stays dismissed (the same 400 days as the UI's own "Got it", ui/prefs.ts). */
const FIRST_USE_TTL_MS = 400 * 24 * 3600_000;
/** After a failed (or still running) first connection: look again soon, then back off to every 10 s (never faster than the worker's own hint). */
export const RETRY_DELAYS_MS = [1500, 3000, 6000, 10000];
/** The cross-origin frame note needs a frame that stayed silent this long (its content script may simply still be starting). */
const FRAME_NOTE_AFTER_MS = 3000;
/** The UI mounted only to show a prompt on a quiet page goes away again this long after the prompt closed (toasts need it a moment). */
const QUIET_UNMOUNT_MS = 6000;
const MAX_NOTIFIED = 200;
/** CPL-20: the chip's "Use another CV" menu lists at most this many documents (the server sends at most as many). */
const MAX_ALTERNATIVES = 6;
/** How often the assistant checks that the document it attached itself to is still the page's document. */
const DOCUMENT_WATCH_MS = 1000;
const DOCUMENT_RESTART_GAP_MS = 4000;
/** Connection phases in which this browser is NOT (or no longer) trusted with Meridian's answers. */
const REVOKING_PHASES: ReadonlySet<ConnectionState["phase"]> = new Set<ConnectionState["phase"]>(["unpaired", "revoked", "proof_failed", "disabled"]);
/** An asynchronous fill swallows Tab while it runs; it never does for longer than this (the keyboard is never held hostage). */
const BUSY_MAX_MS = 15000;
/**
 * E2E builds only: tests pin a limit instead of depending on how fast the machine is (or waiting 15 s). Referenced only behind
 * `__E2E__`, so production bundles contain neither this function nor the names it reads.
 */
function e2eOverride(name: string): number | null {
  const value = Number((globalThis as unknown as Record<string, unknown>)[name]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function alwaysOnPollMs(): number {
  return (__E2E__ ? e2eOverride("__MERIDIAN_ALWAYS_ON_POLL_MS") : null) ?? 2500;
}

function busyLimitMs(): number {
  return (__E2E__ ? e2eOverride("__MERIDIAN_BUSY_MAX_MS") : null) ?? BUSY_MAX_MS;
}

function syncScanLimitMs(): number {
  return (__E2E__ ? e2eOverride("__MERIDIAN_SYNC_SCAN_MAX_MS") : null) ?? SYNC_SCAN_MAX_MS;
}
/** Above this a scan is not run synchronously inside a key press (see `afterInsert`). */
const SYNC_SCAN_MAX_MS = 100;
/** The visibility verdict that decides whether the chip stands next to a field is good for this long (a render per keystroke must not recalculate the page's styles each time). */
const CHIP_VISIBLE_MS = 400;
/** An `input` event counts as the person's own when a trusted edit-starting event (a key, a pointer ...) reached the same field this recently (a dictation after a click is theirs). */
const ORIGIN_MS = 20_000;
const MAX_ORIGINS = 200;
/** Controls whose value a person sets with a picker or a click, never by typing text: a script cannot edit them with `insertText`, and the picker's own clicks are not seen by the page. */
const PICKED_TYPES = new Set(["radio", "checkbox", "file", "date", "time", "datetime-local", "month", "week", "color", "range", "image", "submit", "button", "reset"]);
/** Height of the area below a field a chip typically occupies; room it needs above a field to sit there instead. */
const CHIP_BAND_PX = 170;
const MIN_ROOM_ABOVE_PX = 150;
/** The chip is about 350 px wide: room needed to the right of a field (chip + gap + a margin) for it to sit beside the field (UX-01). */
const CHIP_BESIDE_PX = 372;
/** UX-05: below this viewport (a window at 200% zoom, a phone) the panel starts as a pill: expanded it would cover most of the form. */
const PANEL_MIN_WIDTH_PX = 700;
const PANEL_MIN_HEIGHT_PX = 600;
/** UX-03: "Fill verified" can be taken back as one for this long. */
const BATCH_UNDO_MS = 10000;
/** UX-03 / FR-03: the review lists at most this many fields (the count is always the full number). */
const REVIEW_ITEMS_MAX = 12;
/** How long a draft's company stays "rejected": drafts whose job began before the person's correction (see `rejectedGenerations`). */
const MAX_REJECTED = 200;
/** Draft progress, as the server orders it (the events and a response are separate requests and may arrive in either order). */
const DRAFT_RANK: Record<GenerationStatus, number> = { QUEUED: 0, GENERATING: 1, REGENERATING: 2, VALIDATING: 3, DONE: 4, FAILED: 4, CANCELLED: 4 };
const RESEARCH_FINAL: ReadonlySet<string> = new Set(["done", "partial", "failed", "cancelled"]);
const COVER_LETTER = /cover ?letter|carta de presentaci[oó]n|letter of (?:motivation|intent)|motivation letter|carta de motivaci[oó]n/i;
const CV_FIELD = /\b(?:resume|r[eé]sum[eé]|cv|curriculum|curr[ií]culum|hoja de vida)\b/i;

function smallViewport(): boolean {
  return window.innerWidth < PANEL_MIN_WIDTH_PX || window.innerHeight < PANEL_MIN_HEIGHT_PX;
}

/** One line: whitespace runs collapsed (what the confirmation row shows of a long answer). */
function squash0(text: string): string {
  return String(text ?? "").replace(/\s+/g, " ").trim();
}

function randomKey(): string {
  try {
    const bytes = new Uint8Array(8);
    crypto.getRandomValues(bytes);
    return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return Math.random().toString(16).slice(2, 18);
  }
}

/** E2E builds only: the last exception swallowed while drawing the panel (a silent stale panel is the worst kind of bug). */
let renderErrorForTests = "";

function tabKey(): string {
  try {
    let key = sessionStorage.getItem("__meridian_assist_tab");
    if (!key) {
      key = randomKey();
      sessionStorage.setItem("__meridian_assist_tab", key);
    }
    return key;
  } catch {
    return randomKey();
  }
}

/** What "Fill verified" inserted, so that all of it (or one field of it, the one the person is on) can be taken back (UX-03). */
interface BulkUndo {
  at: number;
  items: { key: string; label: string; undo: () => boolean }[];
}

/** The posting is already in Meridian's tracker (CPL-13): what the panel says about it and whether the person has said "continue". */
interface DuplicateState {
  state: "applied" | "preparing";
  since?: string;
  /** what became of the application (UXR-08): the banner says "rejected" / "withdrawn" instead of "applied" */
  status?: DuplicateStatus;
  acknowledged: boolean;
}

/** UXR-08: "Fill verified" asked for confirmation: the keys of the fields the row listed (nothing else is filled when the person confirms). */
interface FillPreviewState {
  keys: string[];
}

/** The longest value the confirmation row of "Fill verified" shows for one field (the field itself may hold more). */
const PREVIEW_VALUE_MAX = 200;
/** The confirmation row lists at most this many fields (the count is always the full number). */
const PREVIEW_ITEMS_MAX = 30;

/**
 * Is `incoming` an EARLIER point of the same draft's progress than `held`? The attempt counts first (a revision starts a new attempt);
 * within one attempt the status only moves forward. Without an attempt on either side only the status can tell.
 */
function behindDraft(incoming: NonNullable<Suggestion["generation"]>, held: NonNullable<Suggestion["generation"]>): boolean {
  if (incoming.attempt !== undefined && held.attempt !== undefined && incoming.attempt !== held.attempt) return incoming.attempt < held.attempt;
  return (DRAFT_RANK[incoming.status] ?? 0) < (DRAFT_RANK[held.status] ?? 0);
}

function skipSuggestion(field: AssistField, version: string, reason: string): Suggestion {
  return {
    node_id: field.nodeId, frame_key: field.frameKey, question: field.label, question_id: "", request_id: `skip:${reason}`,
    knowledge_version: version, state: "SKIP", insert: "never", suggestion: null, confidence: null, why: null, flags: {}, needs: null,
    generation: null, based_on: null,
  };
}

export class Controller {
  readonly store = new Store();
  private ui!: AssistUI;
  private scanner!: Scanner;
  private session!: SessionClient;
  private frames!: FrameCoordinator;
  private keyboard!: KeyboardController;
  private submit!: SubmitWatch;
  private learning!: LearningLoop;
  private disposers: (() => void)[] = [];

  active = false;
  private activating: Promise<void> | null = null;
  private mounted = false;
  /** automatic start: stay invisible until an application form shows up */
  private quiet = false;
  private orphaned = false;
  private prefs: StoredPrefs | null = null;
  private connection: ConnectionState = { phase: "no_bridge" };
  private everReady = false;
  private job: JobSummary | null = null;
  private research: ResearchSnapshot | null = null;
  /** null until Meridian has said whether a writing model is set up (the session reply or the status): never guess "no" (UX-08) */
  private generation: GenerationAvailability | null = null;
  private budgetExhausted = false;
  private profileFacts: number | null = null;
  /** CPL-06: does an APPROVED CV exist, and how many imported CVs still wait for approval (null = Meridian has not said) */
  private hasCv: boolean | null = null;
  private unapprovedCvs: number | null = null;
  /** CPL-13: top frame: the posting is already recorded; children: whether the top frame blocks Tab (it broadcasts the state) */
  private duplicate: DuplicateState | null = null;
  private duplicateFromTop = false;
  /** SEC-01: this (child) frame's <iframe> cannot be seen by a person: nothing here is offered, inserted or counted */
  private frameHidden = false;
  private jobKey = "";
  private opening = false;
  private openAttempts = 0;
  private current: AssistField | null = null;
  private chipKey: string | null = null;
  private chipTimer: ReturnType<typeof setTimeout> | null = null;
  /** when a trusted key / pointer / paste / drop / composition last reached each field (LOW-1: `onOrigin`) */
  private origins = new Map<string, number>();
  /** the last visibility verdict of the chip's field (see `chipVisible`) */
  private chipSeen: { key: string; at: number; ok: boolean } | null = null;
  private undoRecord: UndoRecord | null = null;
  private undoTimer: ReturnType<typeof setTimeout> | null = null;
  private keysPaused = false;
  /** an asynchronous fill started by a key press is running (Tab is swallowed until it finishes) */
  private keyBusy = false;
  private keyBusyAt = 0;
  /** every asynchronous fill started by a key gets a number; a fill that was given up on can no longer move focus */
  private busyGen = 0;
  private fillingField: AssistField | null = null;
  /** "Fill verified" is running */
  private bulkBusy = false;
  private bulkUndo: BulkUndo | null = null;
  private bulkUndoTimer: ReturnType<typeof setTimeout> | null = null;
  private collapsed = false;
  /** the person chose (expand / collapse): the viewport no longer decides (UX-05) */
  private collapseTouched = false;
  /** the form field the person was on last (the panel sends them back to it: Esc / Alt+Shift+M, UX-02) */
  private lastField: AssistField | null = null;
  private lastChildKey: string | null = null;
  private lastWasChild = false;
  /** FR-03: Tab reached the end of the form; the panel lists the required questions that are still empty (live, until dismissed) */
  private reviewOpen = false;
  /** CPL-18: the person has not touched a field or inserted anything yet on this page: the panel shows its first-use card */
  private firstUseDone = false;
  /** UXR-10: the person has been shown the three steps for good (a "Got it" or a first insertion, remembered by the worker): the card never comes back */
  private firstUseSeen = false;
  /** UXR-10: the worker has said whether the card was dismissed before (until then it is not shown: a card that flashes and goes is worse than none) */
  private firstUseKnown = false;
  /** UXR-07: top frame: was the panel on screen the last time the children were told? Child frame: can Alt+Shift+M be relayed to a panel? */
  private panelAnnounced: boolean | null = null;
  private topPanelAvailable = false;
  /** SEC-01: "Fill verified" is waiting for the person's confirmation of the fields listed in the panel */
  private fillPreview: FillPreviewState | null = null;
  /** UX-13: the person asked for "start automatically on this site" and the browser has not granted the access yet */
  private alwaysOnPending = false;
  /** UX-13: the person switched it OFF and a rule they did not set here still starts the assistant (one calm line names it) */
  private alwaysOnNote: { rule: string | null; text: string } | null = null;
  /** SEC-06: Meridian used up the drafts it writes on its own for this page (`drafts_paused`); the panel says so, "Draft with AI" still works */
  private draftsPaused = false;
  private focusSyncQueued = false;
  /** FR-02: the first empty field is brought into view once, on the first scan of an assistant the person started themselves */
  private guideArmed = false;
  private startScroll = 0;
  /** CPL-09: draft jobs (`generation.id`) that were already running when the person said "Not this company?" */
  private rejectedGenerations = new Set<string>();
  private childDigests = new Map<string, FieldDigest[]>();
  private childFocusKey: string | null = null;
  private trackEvidence = "";
  private flushQueued = false;
  private firstRequestDone = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retryCount = 0;
  private helloTimer: ReturnType<typeof setInterval> | null = null;
  private pairingTimer: ReturnType<typeof setTimeout> | null = null;
  private urlTimer: ReturnType<typeof setTimeout> | null = null;
  private digestTimer: ReturnType<typeof setTimeout> | null = null;
  private framesInjectTimer: ReturnType<typeof setTimeout> | null = null;
  private renderSoonTimer: ReturnType<typeof setTimeout> | null = null;
  private frameNoteTimer: ReturnType<typeof setTimeout> | null = null;
  private quietUnmountTimer: ReturnType<typeof setTimeout> | null = null;
  private openTimer: ReturnType<typeof setTimeout> | null = null;
  /** the <html> element this run of the assistant attached itself to: a page that replaces its document takes everything we hung on it */
  private documentRoot: Element | null = null;
  private watchTimer: ReturnType<typeof setInterval> | null = null;
  private restartedAt = 0;
  private resizeWatch: ResizeObserver | null = null;
  private roots = new RootRegistry();
  /** events already handled (a focus change inside a shadow tree reaches both the root and the window listener) */
  private handled = new WeakSet<Event>();
  private pageVisible = typeof document === "undefined" || document.visibilityState !== "hidden";
  /** a connection retry came due while the page was hidden: do it the moment the page is shown again */
  private retryDue = false;
  private frameSeenAt = new WeakMap<HTMLIFrameElement, number>();
  private anchorFrame = 0;
  private notifiedDrafts = new Set<string>();
  /** > 0 while WE write to the page: the browser reports activation-driven change events as trusted, they are not the user */
  private filling = 0;

  constructor(private readonly makeUI: () => AssistUI) {
    onOrphaned(() => this.handleOrphaned());
  }

  // ---- activation ----------------------------------------------------------------------------------------------------------

  activate(auto = false): Promise<void> {
    if (this.active) {
      if (!auto && this.quiet) this.reveal();
      this.scanner.request("reactivate", 0);
      return Promise.resolve();
    }
    if (!this.activating) this.activating = this.start(auto).finally(() => { this.activating = null; });
    return this.activating;
  }

  /** Is the panel on screen right now? A quiet automatic start, an unmounted UI and a stopped assistant all mean "no". */
  panelVisible(): boolean {
    return this.active && this.mounted && !this.quiet && this.store.isTop && !this.orphaned;
  }

  /**
   * The toolbar button on a page that already runs the content script (design 7.2, `activate{toggle:true}`): a visible panel
   * switches the assistant off (and tells the worker); a hidden panel, an unmounted UI or a quiet page shows it and stays on.
   */
  private async toggle(): Promise<void> {
    if (this.orphaned) return;
    if (this.activating) {
      await this.activating; // a second click while the first is still starting is the same wish, not the opposite
      return;
    }
    if (window !== window.top) {
      // frames follow the top frame (it relays "deactivate"): they only need to be running
      if (!this.active) await this.activate(false);
      return;
    }
    if (this.panelVisible()) {
      this.deactivate(true);
      return;
    }
    await this.activate(false);
  }

  /** The user asked for the panel after an automatic, invisible start: show it now, and only now let the page reach the bridge. */
  private reveal(): void {
    this.quiet = false;
    this.guideArmed = this.store.isTop;
    this.mount(true);
    this.scheduleRender(true);
    if (this.store.isTop) void sendBg<"activation">({ t: "activation", active: true });
    this.ensureSession();
  }

  /** Top frame: a usable connection opens the session; an unusable one keeps retrying. A quiet page does neither (nothing leaves it). */
  private ensureSession(): void {
    if (!this.store.isTop || this.quiet || !this.active || this.orphaned) return;
    if (this.connectionUsable()) {
      if (!this.session.sessionId) spawn(this.openSession());
    } else {
      this.scheduleRetry();
    }
  }

  private async start(auto: boolean): Promise<void> {
    if (isOrphaned()) return;
    // the worker may be waking up: a failed first answer is retried before giving up
    let res = await sendBg<"ctx">({ t: "ctx" });
    for (let attempt = 0; !res.ok && res.error.code !== "orphaned" && attempt < 3; attempt++) {
      await sleep(400 * (attempt + 1));
      res = await sendBg<"ctx">({ t: "ctx" });
    }
    if (!res.ok) {
      if (res.error.code === "orphaned") this.handleOrphaned();
      return;
    }
    const ctx: CtxInfo = res.data;
    this.prefs = ctx.prefs;
    const isTop = ctx.isTop && window === window.top;
    this.store.isTop = isTop;
    this.store.frameId = ctx.frameId;
    this.store.frameKey = isTop ? "top" : `f${ctx.frameId}`;
    this.store.settings = { ...DEFAULT_SETTINGS, ...(ctx.settings ?? ctx.prefs?.settings ?? {}) };
    this.connection = this.store.settings.enabled ? ctx.connection : { ...ctx.connection, phase: "disabled" };
    this.everReady = this.connection.phase === "ready";
    this.pageVisible = document.visibilityState !== "hidden";
    this.retryCount = 0;
    this.build();
    this.active = true;
    this.documentRoot = document.documentElement;
    this.watchTimer = every(() => this.watchDocument(), DOCUMENT_WATCH_MS);
    this.quiet = auto;
    // a narrow or short viewport (a window at 200% zoom, a phone) starts with the panel as a pill: expanded it covers the form (UX-05)
    this.collapsed = isTop && smallViewport();
    this.collapseTouched = false;
    this.guideArmed = isTop && !auto;
    this.startScroll = window.scrollY;
    this.mount(!auto);
    this.keyboard.install();
    this.listen();
    this.scanner.start();
    this.submit.install(ctx.pending);
    if (isTop) {
      void sendBg<"activation">({ t: "activation", active: true });
      if (!this.pageVisible) void sendBg<"visibility">({ t: "visibility", visible: false });
      this.ensureSession();
      spawn(this.loadFirstUse());
    } else {
      this.startHello();
    }
    this.scheduleRender(true);
  }

  /** UXR-10: has the person already seen the three steps of the first-use card (a "Got it" or a first insertion, on any earlier page)? */
  private async loadFirstUse(): Promise<void> {
    const res = await sendBg<"ui:get">({ t: "ui:get" });
    if (!this.active || this.orphaned) return;
    const at = res.ok ? res.data?.dismissed?.first_use : undefined;
    this.firstUseSeen = typeof at === "number" && Number.isFinite(at) && Date.now() - at < FIRST_USE_TTL_MS;
    this.firstUseKnown = true;
    this.scheduleRender();
  }

  /**
   * UXR-10: the first answer went in: the three steps have done their job, for this page and for every later one. The UI keeps the same
   * entry for its "Got it" button; a UI without `dismissNote` gets the entry written through the worker (`ui:set`), keeping the rest of the
   * remembered look as the worker holds it.
   */
  private noteFirstInsertion(): void {
    this.firstUseDone = true;
    if (this.firstUseSeen) return;
    this.firstUseSeen = true;
    try {
      if (this.ui.dismissNote) {
        this.ui.dismissNote("first_use");
        return;
      }
    } catch {
      /* write it ourselves */
    }
    spawn((async () => {
      const got = await sendBg<"ui:get">({ t: "ui:get" });
      const saved = got.ok && got.data ? got.data : { dock: "right" as const, dismissed: {} };
      await sendBg<"ui:set">({ t: "ui:set", value: { dock: saved.dock === "left" ? "left" : "right", dismissed: { ...saved.dismissed, first_use: Date.now() } } });
    })());
  }

  private build(): void {
    const store = this.store;
    this.roots = new RootRegistry();
    this.ui = this.makeUI();
    this.disposers.push(this.ui.onAction((a) => this.onAction(a)));
    this.session = new SessionClient({
      frameKey: store.frameKey,
      onBatch: (b) => this.onBatch(b),
      onEvent: (e) => this.onEvent(e),
      onConnection: (c) => this.setConnection(c),
      onExpired: () => this.onSessionExpired(),
      onOrphaned: () => this.handleOrphaned(),
      onRequestFailed: (fields) => {
        for (const f of fields) store.state(f.key).requestedDigest = null;
      },
    });
    this.frames = new FrameCoordinator(this.framesHost(), store.frameId);
    this.scanner = new Scanner({
      frameKey: store.frameKey,
      isActive: () => this.active && !this.orphaned,
      onResult: (r, d) => this.onScan(r, d),
      onUrlChange: () => this.onUrlChange(),
      onFramesChanged: () => this.onFramesChanged(),
      roots: this.roots,
    });
    this.keyboard = new KeyboardController(this.keyboardHost());
    this.submit = new SubmitWatch({
      active: () => this.active && !this.orphaned,
      isTop: store.isTop,
      sessionId: () => store.sessionId,
      job: () => (this.job ? { company: this.job.company, title: this.job.title } : null),
      hasFields: () => store.list().length > 0,
      formHasFields: (form) => !!form && store.list().some((f) => form.contains(f.el)),
      showTrack: (view) => this.showTrack(view),
      relayConfirmation: (evidence) => this.frames.sendConfirmation(evidence),
      roots: this.roots,
      identity: () => currentJobIdentity(),
      // a confirmation page has an address of its own; "another job" is recognisable by an application form of its own
      sameApplication: (identity) => !identity || currentJobIdentity() === identity || store.list().length === 0,
    });
    this.learning = new LearningLoop({
      frameKey: store.frameKey,
      settings: () => store.settings,
      readField,
      stateOf: (f) => store.peek(f.key),
      sessionId: () => store.sessionId,
      askSave: (view) => this.ui.askSave(view),
      feedback: (req) => this.session.feedback(req),
      saved: (resp: FeedbackResponse, remember, label) => this.toast("saved", "ok", { scope: remember, label }, resp.saved),
      isFailure,
    });
  }

  private mount(force: boolean): void {
    if (this.mounted) return;
    if (!force && !this.store.list().length) return;
    this.mounted = true;
    this.ui.mount({ panel: this.store.isTop });
    this.ui.setLanguage(this.store.settings.language);
  }

  deactivate(fromPanel = false): void {
    if (!this.active) return;
    this.active = false;
    this.keyboard.uninstall();
    releaseFocusProxies();
    this.scanner.stop();
    this.submit.uninstall();
    this.learning.clear();
    this.origins.clear();
    this.frames.dispose();
    for (const d of this.disposers.splice(0)) d();
    this.roots.stop();
    this.resizeWatch?.disconnect();
    this.resizeWatch = null;
    for (const t of [
      this.retryTimer, this.helloTimer, this.pairingTimer, this.urlTimer, this.digestTimer, this.chipTimer, this.undoTimer,
      this.framesInjectTimer, this.renderSoonTimer, this.frameNoteTimer, this.quietUnmountTimer, this.openTimer, this.watchTimer, this.bulkUndoTimer,
    ]) {
      if (t) { clearTimeout(t as ReturnType<typeof setTimeout>); clearInterval(t as ReturnType<typeof setInterval>); }
    }
    this.retryTimer = this.helloTimer = this.pairingTimer = this.urlTimer = this.digestTimer = this.chipTimer = this.undoTimer = null;
    this.framesInjectTimer = this.renderSoonTimer = this.frameNoteTimer = this.quietUnmountTimer = this.openTimer = this.watchTimer = null;
    this.bulkUndoTimer = null;
    // a later activation is a new visit: what this one knew about the posting, the person's choices and the first-use card start over
    this.bulkUndo = null;
    this.duplicate = null;
    this.duplicateFromTop = false;
    this.frameHidden = false;
    this.reviewOpen = false;
    this.firstUseDone = false;
    this.firstUseSeen = false;
    this.firstUseKnown = false;
    this.panelAnnounced = null;
    this.topPanelAvailable = false;
    this.fillPreview = null;
    this.alwaysOnPending = false;
    this.alwaysOnNote = null;
    this.draftsPaused = false;
    this.guideArmed = false;
    this.lastField = null;
    this.lastChildKey = null;
    this.lastWasChild = false;
    this.rejectedGenerations.clear();
    this.generation = null;
    this.jobKey = "";
    this.documentRoot = null;
    this.openAttempts = 0;
    this.opening = false;
    if (this.anchorFrame) cancelAnimationFrame(this.anchorFrame);
    this.anchorFrame = 0;
    this.retryDue = false;
    this.keysPaused = false;
    this.childDigests.clear();
    this.childFocusKey = null;
    this.notifiedDrafts.clear();
    this.trackEvidence = "";
    if (this.store.isTop) {
      if (fromPanel) this.frames.deactivateChildren();
      spawn(this.session.release());
      void sendBg<"activation">({ t: "activation", active: false });
    } else {
      this.session.reset();
    }
    this.current = null;
    this.chipKey = null;
    this.ui.hideChip();
    this.ui.setMarks?.([]);
    this.ui.unmount();
    this.mounted = false;
    this.store.sessionId = null;
    this.store.setFields([]);
    this.store.clearSuggestions();
    this.undoRecord = null;
    this.firstRequestDone = false;
  }

  /**
   * The page replaced its own document (`document.open()` + `write`, a swapped <html>): the listeners, the observers and the UI the
   * assistant hung on the old one are gone with it, and what it knows about the page is the old page. Start over on the new one.
   */
  private watchDocument(): void {
    if (!this.active || this.orphaned || this.activating) return;
    this.syncFocus(); // the slow safety net of UXR-05: whatever events an engine skipped, the field the person is on is shown within a second
    const root = document.documentElement;
    if (!root || root === this.documentRoot) return;
    // a page that swaps its <html> over and over must not make the assistant open a session every second
    if (Date.now() - this.restartedAt < DOCUMENT_RESTART_GAP_MS) return;
    this.restartedAt = Date.now();
    const quiet = this.quiet;
    spawn((async () => {
      this.deactivate(false);
      await this.activate(quiet);
    })());
  }

  /** The runtime is gone (extension reloaded/updated): stop touching the page, tell the user to reload. */
  private handleOrphaned(): void {
    if (this.orphaned) return;
    this.orphaned = true;
    if (this.watchTimer) clearInterval(this.watchTimer);
    this.watchTimer = null;
    try {
      this.keyboard?.uninstall();
      releaseFocusProxies();
      this.scanner?.stop();
      this.submit?.uninstall();
      this.frames?.dispose();
      this.session?.stopPolling();
      for (const d of this.disposers.splice(0)) d();
      this.roots.stop();
      this.resizeWatch?.disconnect();
      this.resizeWatch = null;
      this.ui?.hideChip();
      this.ui?.setMarks?.([]);
      if (!this.store.isTop) this.ui?.unmount();
      else this.renderPanelNow();
    } catch {
      /* nothing else can fail visibly */
    }
  }

  // ---- messages from the worker ---------------------------------------------------------------------------------------------

  onWorkerMessage(msg: ToContent): void {
    switch (msg.t) {
      case "activate":
        if (msg.toggle) spawn(this.toggle());
        else spawn(this.activate(!!msg.auto));
        return;
      case "deactivate":
        // a deactivation that arrives while the assistant is still starting must not be lost
        if (this.activating) spawn(this.activating.then(() => this.deactivate(false)));
        else this.deactivate(false);
        return;
      case "conn:changed": if (this.active) this.setConnection(msg.connection); return;
      case "frame": if (this.active) this.frames.handle(msg.payload, msg.fromFrameId); return;
    }
  }

  // ---- listeners -------------------------------------------------------------------------------------------------------------

  private listen(): void {
    const on = (target: EventTarget, type: string, handler: (e: any) => void, opts: AddEventListenerOptions | boolean = true) => {
      const fn = guard(handler); // a node that throws must never end up as an error of the page
      target.addEventListener(type, fn, opts);
      this.disposers.push(() => target.removeEventListener(type, fn, opts));
    };
    // every window-level listener also learns which open shadow roots the event travelled through
    on(window, "focusin", (e: FocusEvent) => { this.roots.addFromPath(e); this.onFocusIn(e); });
    on(window, "focusout", (e: FocusEvent) => this.onFocusOut(e));
    // UXR-05: Safari's engine raises NO `focusin` when a native date / time input takes the focus (it raises `focus`): the field the person is
    // on is looked up after any focus change and after a Tab, whichever events the engine chose to send
    on(window, "focus", (e: FocusEvent) => this.onFocusCapture(e));
    on(window, "keyup", (e: KeyboardEvent) => { if (e.key === "Tab") this.syncFocusSoon(); });
    on(window, "click", (e: MouseEvent) => { this.roots.addFromPath(e); this.onClick(e); });
    on(document, "input", (e: Event) => this.onUserInput(e));
    on(document, "change", (e: Event) => this.onUserInput(e));
    // Focus moving between two controls of ONE shadow tree never reaches the window (the host retargets to itself on both
    // sides and the dispatch skips it), and `change` is not composed: every open root gets its own listeners.
    this.disposers.push(this.roots.bind((root) => {
      const focusin = guard((e: Event) => this.onFocusIn(e as FocusEvent));
      const focusout = guard((e: Event) => this.onFocusOut(e as FocusEvent));
      const change = guard((e: Event) => this.onUserInput(e));
      root.addEventListener("focusin", focusin, true);
      root.addEventListener("focusout", focusout, true);
      root.addEventListener("change", change, true);
      return () => {
        root.removeEventListener("focusin", focusin, true);
        root.removeEventListener("focusout", focusout, true);
        root.removeEventListener("change", change, true);
      };
    }));
    on(window, "scroll", () => this.scheduleAnchors(), { capture: true, passive: true });
    on(window, "resize", () => { this.scheduleAnchors(); this.syncCollapse(); }, { capture: true, passive: true });
    // SEC-05: only gestures the browser itself raised (a key, a click, an input) make a value the person's correction
    on(window, "keydown", (e: KeyboardEvent) => { this.onOrigin(e); this.onUserGesture(e); });
    on(window, "click", (e: MouseEvent) => this.onUserGesture(e));
    // LOW-1: `document.execCommand("insertText")` raises `beforeinput` / `input` with `isTrusted` true, so an `input` event alone proves nothing: a
    // value only counts as the person's own when one of these (which a script cannot raise as trusted) came to the same field just before it
    for (const type of ["pointerdown", "paste", "drop", "compositionend"]) on(window, type, (e: Event) => this.onOrigin(e));
    on(window, "pagehide", (e: PageTransitionEvent) => {
      if (e.persisted || !this.store.isTop) return;
      if (!this.submit.isPending()) spawn(this.session.release());
    }, false);
    on(document, "visibilitychange", () => this.onVisibility(), false);
    // layout shifts without a scroll or a window resize (accordions, late images, growing textareas) move the chip's field
    try {
      this.resizeWatch = new ResizeObserver(guard(() => this.scheduleAnchors()));
      this.resizeWatch.observe(document.documentElement);
    } catch {
      this.resizeWatch = null;
    }
  }

  /** The page was hidden or shown: tell the worker (it polls only for visible pages), pause/resume events, retry at once when due. */
  private onVisibility(): void {
    if (!this.active || this.orphaned) return;
    const visible = document.visibilityState !== "hidden";
    if (visible === this.pageVisible) return;
    this.pageVisible = visible;
    if (this.store.isTop) void sendBg<"visibility">({ t: "visibility", visible });
    this.session.setVisible(visible);
    if (!visible) return;
    if (this.retryDue) {
      this.retryDue = false;
      spawn(this.refreshConnection());
    }
    this.renderChip(); // the draft dwell clock only runs while the chip can be seen
    this.scheduleAnchors();
    this.scheduleRender();
  }

  /** True the first time an event is seen (a focus change inside a shadow tree reaches the root listener AND, sometimes, the window). */
  private firstSight(e: Event): boolean {
    if (this.handled.has(e)) return false;
    this.handled.add(e);
    return true;
  }

  private onFocusIn(e: FocusEvent): void {
    if (!this.active || this.orphaned || !this.firstSight(e)) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = this.scanner.fieldFor(target);
    if (!field) {
      if (this.scanner.isControl(target) && !this.scanner.isKnownControl(target)) this.scanner.request("focus-unknown", 40);
      this.leaveField();
      return;
    }
    this.enterField(field);
  }

  /** A `focus` event (it does not bubble: seen in the capture phase): the engine may have skipped `focusin` for this control (UXR-05). */
  private onFocusCapture(e: FocusEvent): void {
    if (!this.active || this.orphaned) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    this.syncFocusSoon();
  }

  /** Looked up one task later: an engine that does send `focusin` has entered the field by then and nothing is left to do. */
  private syncFocusSoon(): void {
    if (this.focusSyncQueued) return;
    this.focusSyncQueued = true;
    after(() => {
      this.focusSyncQueued = false;
      this.syncFocus();
    }, 0);
  }

  /** The assist field the keyboard focus is on right now is the one the assistant shows (an engine that raised no `focusin` leaves the old one). */
  private syncFocus(): void {
    if (!this.active || this.orphaned || this.frameHidden) return;
    const active = deepActiveElement();
    if (!active || inUiHost(active)) return;
    const field = this.scanner.fieldFor(active);
    if (field && this.current?.key !== field.key) this.enterField(field);
  }

  private onFocusOut(e: FocusEvent): void {
    if (!this.active || this.orphaned || !this.firstSight(e)) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = this.scanner.fieldFor(target);
    if (field) {
      const next = e.relatedTarget instanceof Element ? this.scanner.fieldFor(e.relatedTarget) : null;
      if (next?.key !== field.key) this.learning.onFocusOut(field);
    }
    after(() => {
      if (!this.active) return;
      const now = this.scanner.fieldFor(deepActiveElement());
      if (!now) this.leaveField();
    }, 0);
  }

  private onClick(e: MouseEvent): void {
    if (!this.active || !e.isTrusted) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = this.scanner.fieldFor(target);
    if (!field) return;
    if (this.current?.key === field.key) {
      const fs = this.store.state(field.key);
      fs.dismissed = false;
      fs.held = false;
      this.renderChip();
    } else if (fieldIsFocused(field)) {
      this.enterField(field);
    } else if (focusBlocked(focusTarget(field))) {
      // A control that can never take focus (the file input behind a drop area) is entered through what the person clicked: once the page's
      // own click handler has run, the visible stand-in takes the focus, which brings the chip and makes Tab / Enter work from there.
      after(() => {
        if (!this.active || this.orphaned || !field.el.isConnected) return;
        const active = deepActiveElement();
        if (active && active !== document.body && active !== document.documentElement) return; // the page (or the person) put focus somewhere else
        this.focusField(field);
      }, 0);
    }
  }

  /**
   * A trusted key press or click on a field (or, for a custom combobox, anywhere while the field has the focus: its options live in a
   * popup): evidence that what the field holds when the person leaves it is theirs (SEC-05). A script cannot produce a trusted event.
   */
  private onUserGesture(e: Event): void {
    if (!this.active || this.orphaned || !e.isTrusted || this.filling > 0) return;
    if (e instanceof KeyboardEvent && ["Tab", "Shift", "Control", "Alt", "Meta", "CapsLock", "Escape", "AltGraph"].includes(e.key)) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = this.scanner.fieldFor(target) ?? (this.current?.kind === "combobox" ? this.current : null);
    if (field) this.learning.noteGesture(field);
  }

  /** A trusted event that starts an edit (a key, a pointer, a paste, a drop, the end of a composition) happened on a field: see `onUserInput`. */
  private onOrigin(e: Event): void {
    if (!this.active || this.orphaned || !e.isTrusted) return;
    if (e instanceof KeyboardEvent && ["Tab", "Shift", "Control", "Alt", "Meta", "CapsLock", "Escape", "AltGraph"].includes(e.key)) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = this.scanner.fieldFor(target) ?? (this.current?.kind === "combobox" ? this.current : null);
    if (!field) return;
    this.origins.set(field.key, Date.now());
    while (this.origins.size > MAX_ORIGINS) this.origins.delete(this.origins.keys().next().value as string);
  }

  /** Did a trusted key, pointer, paste, drop or composition reach this field in the last moments? */
  private hasOrigin(field: AssistField): boolean {
    const at = this.origins.get(field.key);
    return at !== undefined && Date.now() - at < ORIGIN_MS;
  }

  /** The window got narrower or shorter (zoom, a resized window): a panel nobody opened or closed by hand follows the viewport (UX-05). */
  private syncCollapse(): void {
    if (!this.active || !this.store.isTop || this.collapseTouched || this.orphaned) return;
    const want = smallViewport();
    if (want === this.collapsed) return;
    this.collapsed = want;
    this.scheduleRender();
  }

  /** A trusted edit: the user touches the field, so markers go away and conflict state is recomputed. */
  private onUserInput(e: Event): void {
    if (!this.active || !e.isTrusted || this.filling > 0 || !this.firstSight(e)) return;
    const target = composedTarget(e);
    if (!target || inUiHost(target)) return;
    const field = this.scanner.fieldFor(target);
    if (!field) return;
    // A text box raises `change` (a trusted one) when it LOSES the focus with a value that differs from the one it got the focus with: that
    // says nothing about who set the value, a script may have set it a moment before. The edits themselves are `input` events. A select, a
    // radio or a checkbox raises `change` at the person's click.
    const commitsOnBlur = target instanceof HTMLTextAreaElement
      || (target instanceof HTMLInputElement && target.type !== "radio" && target.type !== "checkbox" && target.type !== "file");
    // typing into a text control is a gesture only when a trusted key / pointer / paste / drop / composition came first (LOW-1): a script's
    // `execCommand("insertText")` makes `input` events with `isTrusted` true. A select, a radio, a checkbox cannot be changed that way.
    const textual = target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement
      ? !PICKED_TYPES.has(target.type) : (target as HTMLElement).isContentEditable === true;
    if ((e.type !== "change" || !commitsOnBlur) && (!textual || this.hasOrigin(field))) this.learning.noteGesture(field);
    const fs = this.store.state(field.key);
    fs.userEdited = true;
    // what Undo would restore is stale once the person changed the field themselves: Alt+Z must never wipe their own typing
    if (this.undoRecord?.key === field.key) {
      this.undoRecord = null;
      if (this.undoTimer) clearTimeout(this.undoTimer);
      this.undoTimer = null;
    }
    if (fs.marked) {
      fs.marked = false;
      this.scheduleAnchors();
    }
    if (fs.failed) {
      fs.failed = null;
      fs.failedFor = null;
    }
    if (this.current?.key === field.key) this.scheduleChip(120);
    this.scheduleDigests();
    this.scheduleRenderSoon(150);
  }

  // ---- focus / chip -----------------------------------------------------------------------------------------------------------

  private enterField(field: AssistField): void {
    const changed = this.current?.key !== field.key;
    this.current = field;
    this.lastField = field;
    this.lastWasChild = false;
    this.firstUseDone = true; // the person found the first field by themselves: the first-use card has done its job
    const fs = this.store.state(field.key);
    fs.dismissed = false;
    fs.held = false;
    if (changed) {
      this.learning.onFocusIn(field);
      this.frames.sendFocused(field.key);
      this.childFocusKey = null;
    }
    if (!this.store.entryFor(field)) {
      this.requestFields([field], "focus", { force: !fs.requestedAt || Date.now() - fs.requestedAt > 4000, focus: field });
      this.session.prioritize(field);
    }
    this.renderChip();
    this.scheduleRender();
  }

  private leaveField(): void {
    if (!this.current) return;
    this.current = null;
    this.chipKey = null;
    this.ui.hideChip();
    this.frames.sendFocused(null);
    this.scheduleRender();
  }

  private pageBlocked(): boolean {
    const f = this.store.flags;
    return f.challenge || f.mfa || f.login;
  }

  /** An insertion of this field can be taken back now (a Tab / Enter / click insertion within 8 s, or a "Fill verified" one within 10 s). */
  private undoableFor(key: string, now = Date.now()): boolean {
    if (this.undoRecord && this.undoRecord.key === key && now - this.undoRecord.at < UNDO_WINDOW_MS) return true;
    const batch = this.bulkUndo;
    return !!batch && now - batch.at < BATCH_UNDO_MS && batch.items.some((i) => i.key === key);
  }

  private connectionUsable(): boolean {
    return this.connection.phase === "ready" && this.store.settings.enabled && !this.orphaned;
  }

  /** Meridian said a writing model is set up and there is budget left (false while it has not said anything yet: UX-08). */
  private canGenerate(): boolean {
    return this.generation?.available === true && !this.budgetExhausted;
  }

  /** CPL-13: the posting is already recorded as sent and the person has not said "continue": Tab inserts nothing. */
  private duplicateBlocks(): boolean {
    if (!this.store.isTop) return this.duplicateFromTop;
    return this.duplicate?.state === "applied" && !this.duplicate.acknowledged;
  }

  private eligibility(field: AssistField, via: "tab" | "explicit", replace = false): Eligibility {
    const entry = this.store.entryFor(field);
    const sv = entry?.suggestion.suggestion ?? null;
    const insertable = !!entry && INSERTABLE_STATES.has(entry.suggestion.state) && !!sv;
    return evaluate({
      field, entry, stale: this.store.isStale(field), fs: this.store.peek(field.key), settings: this.store.settings, now: Date.now(),
      relation: insertable ? relationOf(field, sv) : "empty", pageBlocked: this.pageBlocked(), connected: this.connectionUsable(), via, replace,
      duplicateBlock: this.duplicateBlocks(), staleDraft: !!entry && this.staleBlocks(field, entry), frameHidden: this.frameHidden,
    });
  }

  // ---- stale drafts (CPL-09) -----------------------------------------------------------------------------------------------------

  /** What a draft was written from, captured when it first arrives (the server's `research_state` plus the run this page knew). */
  private draftBasis(s: Suggestion): DraftBasis {
    return { runId: this.research?.run_id ?? "", state: s.based_on?.research_state ?? this.research?.state ?? "none" };
  }

  /** Has the page's research moved on since the draft was written: a new run (website pinned, researched again), or it finished since? */
  private researchMoved(basis: DraftBasis): boolean {
    const now = this.research;
    if (!now) return false;
    if (basis.runId && now.run_id !== basis.runId) return true;
    return !RESEARCH_FINAL.has(basis.state) && RESEARCH_FINAL.has(now.state);
  }

  /**
   * Why a draft is not what Meridian would write now, or null. `company-rejected`: the person said the researched company was wrong;
   * `profile`: the server says it was written under another Profile version; `research`: newer research exists. Only a server
   * statement or the person's own correction ever marks a draft: a missing `based_on` value is never read as "stale".
   */
  private staleOf(field: AssistField, entry: SuggestionEntry): ChipView["staleReason"] | null {
    const s = entry.suggestion;
    if (s.state !== "GENERATED_DRAFT") return null;
    const memo = this.store.peek(field.key)?.draft;
    if (memo?.companyRejected) return "company-rejected";
    const written = s.based_on?.knowledge_version;
    if (written && this.store.knowledgeVersion && written !== this.store.knowledgeVersion) return "profile";
    if (memo && this.researchMoved(memo.basis)) return "research";
    return null;
  }

  /** A draft written for another company or another Profile is never inserted (Tab, Enter or a click): only an explicit rewrite. */
  private staleBlocks(field: AssistField, entry: SuggestionEntry): boolean {
    const reason = this.staleOf(field, entry);
    return reason === "company-rejected" || reason === "profile";
  }

  /** The person said "Not this company?": every draft written (or being written) for the company they rejected is out. */
  private rejectDrafts(): void {
    for (const f of this.store.list()) {
      const fs = this.store.peek(f.key);
      if (!fs) continue;
      if (fs.draft) fs.draft.companyRejected = true;
      const id = fs.entry?.suggestion.generation?.id;
      if (id) this.rejectedGenerations.add(id);
    }
    while (this.rejectedGenerations.size > MAX_REJECTED) this.rejectedGenerations.delete(this.rejectedGenerations.values().next().value as string);
    this.renderChip();
    this.scheduleDigests();
    this.scheduleRender();
  }

  // ---- the chip --------------------------------------------------------------------------------------------------------------------

  /**
   * CPL-06: why a file field is left alone, in one calm sentence. Only an upload the server skipped for being a file counts (a password,
   * a hidden or a read-only field stays silent): a CV field when no CV can be attached, a cover letter (never attached), any other upload.
   */
  private skipReason(field: AssistField, s: Suggestion): NonNullable<ChipView["skipReason"]> | null {
    if (field.kind !== "file" || s.state !== "SKIP" || !s.why || s.why.code !== "skip") return null;
    const rule = s.why.rules?.[0];
    if (rule && rule !== "file") return null;
    const named = `${field.label} ${field.element.name} ${field.element.id}`;
    if (COVER_LETTER.test(named)) {
      return { code: "file_cover_letter", text: "Meridian never attaches a cover letter. Upload yours yourself." };
    }
    if (CV_FIELD.test(named) && this.hasCv !== true) {
      if ((this.unapprovedCvs ?? 0) > 0) return { code: "file_unapproved", text: "Approve your CV in Meridian > Documents so I can attach it." };
      return { code: "file_none", text: "No approved CV yet. Import one in Meridian > Documents and approve it, then I can attach it." };
    }
    return { code: "generic", text: s.why.summary || "Meridian only attaches your approved CV to a CV upload." };
  }

  private skipChip(field: AssistField, entry: SuggestionEntry): ChipView | null {
    const s = entry.suggestion;
    const skip = this.skipReason(field, s);
    if (!skip) return null;
    const anchor = this.anchorOf(field);
    const around = this.avoidRects(field);
    const label = field.label;
    return {
      key: field.key, fieldLabel: label, questionText: label, state: "SKIP", insert: "never", tone: "neutral", operation: null, value: "",
      whyCode: "skip", whySummary: s.why?.summary ?? "", sources: [], reasonCode: skip.code, reasonText: skip.text, canRegenerate: false,
      canDraft: false, review: false, conflictsWithCurrent: false, undoable: false, dwellRemainingMs: 0, keys: ["esc"], anchor,
      placement: preferredPlacement(anchor, around.next, window.innerWidth), avoid: around.all, avoidNext: around.next.length, skipReason: skip,
    };
  }

  /** UXR-08: the one sentence a chip says while the "already recorded" notice is unanswered, worded for what became of the application. */
  private duplicateReason(): string {
    switch (this.duplicate?.status) {
      case "rejected": return "Meridian has this application as rejected. Choose Continue in the panel to insert answers anyway.";
      case "withdrawn": return "You withdrew this application. Choose Continue in the panel to insert answers anyway.";
      case "ghosted": return "You applied to this job and no answer came. Choose Continue in the panel to insert answers anyway.";
      default: return "You already applied to this job. Choose Continue in the panel to insert answers.";
    }
  }

  private buildChip(field: AssistField): ChipView | null {
    const entry = this.store.entryFor(field);
    const fs = this.store.state(field.key);
    if (!entry || this.pageBlocked() || fs.dismissed || this.frameHidden) return null;
    const s = entry.suggestion;
    if (s.state === "SKIP") return this.skipChip(field, entry);
    const sv = s.suggestion;
    const insertable = INSERTABLE_STATES.has(s.state) && !!sv;
    const relation = insertable ? relationOf(field, sv) : "empty";
    const tab = this.eligibility(field, "tab");
    const explicit = s.insert === "explicit" ? this.eligibility(field, "explicit") : null;
    const now = Date.now();
    let dwell = 0;
    if (s.state === "GENERATED_DRAFT") dwell = entry.shownAt === null ? DRAFT_DWELL_MS : Math.max(0, DRAFT_DWELL_MS - (now - entry.shownAt));
    const staleReason = this.staleOf(field, entry) ?? undefined;
    const staleBlocksInsert = staleReason === "company-rejected" || staleReason === "profile";
    // UXR-08: the posting is already recorded as sent and the person has not said "Continue": nothing inserts, and the chip says so itself
    const duplicateBlocked = insertable && relation !== "equal" && !tab.ok && tab.reason === "duplicate"; // (a field that already holds the answer needs no warning)
    const keys: ChipView["keys"] = [];
    if (tab.ok || (!tab.ok && tab.reason === "dwell")) keys.push("tab");
    if (explicit?.ok) keys.push("enter");
    keys.push("esc");
    const canRegenerate = s.state === "GENERATED_DRAFT" && this.canGenerate();
    if (canRegenerate) keys.push("regenerate");
    const prev = nextStop(this.traversal(false), field, -1);
    if (prev.kind === "assist" || prev.kind === "frame" || prev.kind === "parent") keys.push("shift-tab");
    const failed = activeFailure(fs, entry);
    const choice = field.kind === "select" || field.kind === "radio" || field.kind === "checkbox" || field.kind === "checkboxes" || field.kind === "combobox";
    const tone: ChipView["tone"] = failed ? "error" : toneForState(s.state);
    let reasonCode: string | undefined;
    let reasonText: string | undefined;
    if (failed) {
      reasonCode = "fill_failed";
      reasonText = "I could not fill this one safely; check it.";
    } else if (s.needs && !insertable) {
      reasonCode = s.needs.code;
      reasonText = s.needs.message;
    } else if (s.state === "CONSENT" || s.state === "SENSITIVE_BLANK" || s.state === "NOT_APPLICABLE") {
      reasonCode = s.state.toLowerCase();
      reasonText = s.why?.summary;
    } else if (staleBlocksInsert) {
      reasonCode = staleReason === "company-rejected" ? "stale_company" : "stale_profile";
      reasonText = staleReason === "company-rejected"
        ? "You said this is not the company, so this draft is not inserted. Rewrite it with the new research."
        : "Your Profile changed since this draft was written, so it is not inserted. Rewrite it.";
    } else if (duplicateBlocked) {
      reasonCode = "duplicate";
      reasonText = this.duplicateReason();
    } else if (insertable && s.insert === "explicit") {
      reasonCode = "protected";
      reasonText = "Protected answer: press Enter to insert it.";
    }
    const value = sv?.display ?? s.needs?.candidate_value ?? "";
    const optionAnchors = choice && sv ? this.optionAnchors(field, sv) : undefined;
    const anchor = this.anchorOf(field);
    const around = this.avoidRects(field);
    const undoable = this.undoableFor(field.key, now);
    const label = field.label;
    const view: ChipView = {
      key: field.key,
      fieldLabel: label,
      questionText: label,
      state: s.state,
      insert: staleBlocksInsert || duplicateBlocked ? "never" : s.insert,
      tone,
      operation: sv?.operation ?? null,
      value,
      optionLabel: choice && sv ? (suggestedLabels(sv).join(", ") || undefined) : undefined,
      whyCode: s.why?.code ?? s.needs?.code ?? "",
      whySummary: s.why?.summary ?? s.needs?.message ?? "",
      sources: (s.why?.sources ?? []).slice(0, 6),
      reasonCode,
      reasonText,
      canRegenerate,
      canDraft: s.state === "NEEDS_YOU" && !!s.needs?.actions?.some((a) => a.type === "draft_with_ai") && this.canGenerate(),
      review: s.state === "INFERRED" || s.state === "GENERATED_DRAFT" || !!s.flags?.requires_review,
      generation: s.generation ? { status: s.generation.status, attempt: s.generation.attempt, wordCount: s.generation.word_count } : undefined,
      conflictsWithCurrent: insertable && relation === "conflict",
      undoable,
      dwellRemainingMs: dwell,
      keys,
      anchor,
      placement: preferredPlacement(anchor, around.next, window.innerWidth),
      optionAnchor: optionAnchors?.[0],
      optionAnchors,
      avoid: around.all,
      avoidNext: around.next.length,
      keyHints: { chipActionPrefix: chipActionPrefix() },
    };
    // CPL-20: a file field the page may give another approved document: a click-only menu on the chip (Tab always inserts the one on offer)
    const others = insertable && field.kind === "file" && sv?.operation === "ATTACH_FILE" ? this.documentAlternatives(sv) : [];
    if (others.length) {
      view.alternatives = others;
      view.documentKind = COVER_LETTER.test(`${field.label} ${field.element.name} ${field.element.id}`) ? "cover_letter" : "cv";
    }
    if (duplicateBlocked) view.duplicateBlocksInsert = true;
    if (staleReason) {
      view.staleReason = staleReason;
      if (staleBlocksInsert) view.staleBlocksInsert = true;
    }
    // UX-09: the field already holds the suggested value (Fill verified, a hand-typed answer, an insertion older than the undo window)
    if (insertable && relation === "equal" && !undoable) view.alreadyInserted = true;
    return view;
  }

  /**
   * What the chip must stay off: the NEXT two assist fields (the person is about to go there), and what the person needs in order to read
   * the question and check the answer just given: the PREVIOUS assist field (its value was just inserted) and the focused field's own label
   * (UX-01). `next` alone decides above/below; the UI weighs `all`.
   */
  private avoidRects(field: AssistField): { next: Rect[]; all: Rect[] } {
    const stops = this.scanner.stops();
    const index = stops.findIndex((s) => s.kind === "assist" && s.field.key === field.key);
    if (index < 0) return { next: [], all: [] };
    const next: Rect[] = [];
    for (let i = index + 1; i < stops.length && next.length < 2; i++) {
      const stop = stops[i];
      if (stop.kind !== "assist" || !stop.field.el.isConnected || !isRendered(stop.field.anchorEl)) continue;
      const r = this.anchorOf(stop.field);
      if (r.w > 0 && r.h > 0) next.push(r);
    }
    // UXR-02: the options of a radio / checkbox group the person is on, each as they read it (the control AND its printed label): the
    // chip never sits on the words of an option, the recommended one included
    const around: Rect[] = optionAreas(field);
    for (let i = index - 1; i >= 0; i--) {
      const stop = stops[i];
      if (stop.kind !== "assist" || !stop.field.el.isConnected || !isRendered(stop.field.anchorEl)) continue;
      // the previous field's value area (what was just inserted there must stay readable); for a group, each of its options
      const options = optionAreas(stop.field);
      if (options.length) {
        around.push(...options);
      } else {
        const r = this.anchorOf(stop.field);
        if (r.w > 0 && r.h > 0) around.push(r);
      }
      break;
    }
    const question = rectOf(questionElement(field));
    if (question.w > 0 && question.h > 0) around.push(question);
    return { next, all: [...next, ...around] };
  }

  /** What the person sees of the field: a visually hidden control (file input, native checkbox behind a custom one) is anchored to its visible proxy or label. */
  private anchorOf(field: AssistField): Rect {
    return anchorRect(field);
  }

  /**
   * Where each RECOMMENDED option of a radio / checkbox group is on screen, so the UI can outline it (CPL-05). A checkbox group
   * recommends several ("Tick English, Spanish"): one rectangle each. A member is matched by its label, else (a radio) by its value.
   */
  private optionAnchors(field: AssistField, sv: NonNullable<Suggestion["suggestion"]>): Rect[] | undefined {
    if (!field.members.length) return undefined;
    const wanted = new Set(suggestedLabels(sv).map(normalizeText).filter(Boolean));
    let picked = field.members.filter((m) => wanted.has(normalizeText(m.label)));
    if (!picked.length && sv.option?.value) picked = field.members.filter((m) => m.value === sv.option?.value);
    const rects = picked.map((m) => memberRect(m)).filter((r): r is Rect => !!r);
    return rects.length ? rects : undefined;
  }

  /** The other documents Meridian says this file field may take: well-formed entries only, at most 6, never the one on offer. */
  private documentAlternatives(sv: NonNullable<Suggestion["suggestion"]>): NonNullable<ChipView["alternatives"]> {
    const doc = sv.document;
    const raw = Array.isArray(doc?.alternatives) ? doc.alternatives : [];
    const seen = new Set<string>(doc?.id ? [doc.id] : []);
    const out: NonNullable<ChipView["alternatives"]> = [];
    for (const alt of raw) {
      if (!alt || typeof alt.id !== "string" || !alt.id || typeof alt.filename !== "string" || !alt.filename.trim() || seen.has(alt.id)) continue;
      seen.add(alt.id);
      out.push({ id: alt.id, filename: alt.filename, version: Number.isFinite(alt.version) && alt.version > 0 ? Math.floor(alt.version) : 0 });
      if (out.length >= MAX_ALTERNATIVES) break;
    }
    return out;
  }

  private renderChip(): void {
    const field = this.current;
    if (this.chipTimer) clearTimeout(this.chipTimer);
    this.chipTimer = null;
    if (!field || !this.active) return;
    if (!field.el.isConnected) {
      this.leaveField();
      return;
    }
    if (!this.connectionUsable() && !this.store.entryFor(field)) {
      this.chipKey = null;
      this.ui.hideChip();
      return;
    }
    this.mount(true);
    // SEC-01: the chip only stands next to a field a person can see (a cover that came after the scan would otherwise leave it offering to write
    // into something invisible); the write asks again
    if (!this.chipVisible(field)) {
      this.chipKey = null;
      this.ui.hideChip();
      this.scanner.request("visibility", 0);
      return;
    }
    const view = this.buildChip(field);
    if (!view) {
      this.chipKey = null;
      this.ui.hideChip();
      return;
    }
    this.chipKey = field.key;
    this.ui.showChip(view);
    const entry = this.store.entryFor(field);
    if (entry && entry.suggestion.state === "GENERATED_DRAFT" && entry.shownAt === null && document.visibilityState !== "hidden") {
      entry.shownAt = Date.now();
      this.chipTimer = after(() => this.renderChip(), DRAFT_DWELL_MS + 40);
    } else if (view.dwellRemainingMs > 0) {
      this.chipTimer = after(() => this.renderChip(), view.dwellRemainingMs + 40);
    }
  }

  /** The fresh visibility check of the chip's field, remembered for a moment: a render per keystroke must not cost a style recalculation each. */
  private chipVisible(field: AssistField): boolean {
    const now = Date.now();
    const known = this.chipSeen;
    if (known && known.key === field.key && now - known.at < CHIP_VISIBLE_MS) return known.ok;
    const ok = this.scanner.stillVisible(field, false, true);
    this.chipSeen = { key: field.key, at: now, ok };
    return ok;
  }

  private scheduleChip(ms: number): void {
    if (this.chipTimer) clearTimeout(this.chipTimer);
    this.chipTimer = after(() => this.renderChip(), ms);
  }

  private scheduleAnchors(): void {
    if (this.anchorFrame) return;
    this.anchorFrame = onFrame(() => {
      this.anchorFrame = 0;
      if (!this.active) return;
      // Everything is MEASURED first and DRAWN after: the hit tests behind the markers (`elementsFromPoint`) and the rectangles of the
      // chip's neighbours must not run on a page the chip's own writes have just made dirty (each such read lays the page out again).
      const field = this.current;
      const following = !!field && this.chipKey === field.key;
      // the neighbours move with the field: a fresh view keeps `avoid` in step with `anchor` (else the side could flip while scrolling)
      const chip = following && field.el.isConnected ? this.buildChip(field) : null;
      const anchor = following && !chip ? this.anchorOf(field) : null;
      const marks = this.collectMarks();
      if (chip) this.ui.showChip(chip);
      else if (anchor) this.ui.updateChipAnchor(anchor);
      this.ui.setMarks?.(marks);
    });
  }

  private renderMarks(): void {
    this.ui.setMarks?.(this.collectMarks());
  }

  /** Where each persistent marker belongs right now (reads layout; draws nothing). */
  private collectMarks(): FieldMark[] {
    const marks: FieldMark[] = [];
    if (!this.ui.setMarks) return marks;
    const fields = this.store.list();
    for (const f of fields) {
      const fs = this.store.peek(f.key);
      if (!fs?.marked || !fs.inserted) continue;
      const anchor = this.anchorOf(f);
      // a marker never floats over unrelated UI: a field scrolled out of an overflow container, or covered by a modal/menu, gets none
      if (anchor.w > 0 && !isObscured(f, anchor, fields)) marks.push({ key: f.key, anchor, tone: fs.inserted.tone, label: f.label });
    }
    return marks;
  }

  // ---- scan results ------------------------------------------------------------------------------------------------------------

  /** The page replaced a control by an equivalent element (blur validation, framework re-mount): its state follows it. */
  private rekey(from: string, to: AssistField): void {
    const state = this.store.peek(from);
    if (!state || this.store.peek(to.key)) return;
    this.store.moveState(from, to.key);
    this.learning.rekey(from, to.key);
    if (this.undoRecord?.key === from) this.undoRecord = null; // its undo closure holds the detached element
    if (this.chipKey === from) this.chipKey = null;
    const moved = this.store.state(to.key);
    // an insertion only counts when the new element really holds it (a re-mount may have lost the value)
    if (moved.inserted && !this.stillHolds(to, moved.inserted, readField(to))) {
      moved.inserted = null;
      moved.marked = false;
    }
  }

  private onScan(result: ScanResult, diff: ScanDiff): void {
    const store = this.store;
    const previousFocus = this.current;
    store.documentId = result.documentId;
    store.flags = result.flags;
    for (const pair of diff.renamed) this.rekey(pair.from, pair.to);
    store.setFields(result.fields);
    // the control the person is on may have a new digest (its options arrived, its label changed): the chip must follow THAT version of
    // the field, or the answer that comes back for it would be judged stale against the old one and never shown
    if (this.current) {
      const fresh = store.get(this.current.key);
      if (fresh) this.current = fresh;
    }
    if (diff.reset) {
      store.clearSuggestions();
      this.firstRequestDone = false;
    }
    if (result.fields.length) {
      const wasQuiet = this.quiet;
      this.quiet = false;
      this.mount(true);
      if (wasQuiet) this.ensureSession(); // the first form on a quiet page: only now does anything leave the page
    }
    if (previousFocus && !store.get(previousFocus.key)) this.leaveField();
    this.guideFirstField();
    if (this.session.sessionId && this.connectionUsable() && !this.pageBlocked()) {
      const refresh = new Map<string, AssistField>();
      for (const f of [...diff.added, ...diff.changed]) refresh.set(f.key, f);
      let force = false;
      if (diff.selectionChanged.length) {
        // a choice control changed: conditional questions depend on it, and the server must learn the new selection
        force = true;
        for (const f of diff.selectionChanged) refresh.set(f.key, f);
        for (const f of result.fields) {
          const entry = store.peek(f.key)?.entry;
          if (entry && (entry.suggestion.state === "NOT_APPLICABLE" || entry.suggestion.flags?.conditional)) refresh.set(f.key, f);
        }
      }
      if (!this.firstRequestDone) {
        this.firstRequestDone = true;
        this.requestFields(this.orderFromFocus(result.fields), "scan");
      } else if (refresh.size) {
        // a follow-up question is only answered correctly next to the control that decides it: the one above travels with it
        this.requestFields([...refresh.values()], "refresh", { force, parents: this.parentsOf([...diff.added, ...diff.changed], result.fields) });
      }
    }
    // a control that was already focused when the assistant started (or appeared focused) gets its chip
    if (!this.current) {
      const f = this.scanner.fieldFor(deepActiveElement());
      if (f) this.enterField(f);
    } else {
      this.renderChip();
    }
    if (this.store.isTop) {
      this.frames.prune();
      this.frames.publishHints();
    }
    this.scheduleDigests();
    this.scheduleRender();
    this.renderMarks();
  }

  /**
   * FR-02: an assistant the person started themselves on a page with a form brings the first empty question into view, once, when it lies
   * BELOW what they see (a long posting with the form at its end). It never moves focus (no chip appears, nothing is inserted), never scrolls
   * back up past a person who is already in the form, and leaves the page alone when they scrolled or focused a field meanwhile.
   */
  private guideFirstField(): void {
    if (!this.guideArmed || !this.store.isTop || this.quiet || this.orphaned) return;
    if (this.current || Math.abs(window.scrollY - this.startScroll) > 40) {
      this.guideArmed = false;
      return;
    }
    const first = this.store.list().find((f) => f.el.isConnected && isRendered(f.anchorEl) && readField(f).empty);
    if (!first) return;
    this.guideArmed = false;
    try {
      const r = anchorRect(first);
      if (r.w > 0 && r.y + r.h > window.innerHeight - 60) {
        anchorElement(first).scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" as ScrollBehavior });
      }
    } catch {
      /* scrolling is a courtesy */
    }
  }

  private orderFromFocus(fields: AssistField[]): AssistField[] {
    const active = this.scanner.fieldFor(deepActiveElement());
    if (!active) return fields;
    const idx = fields.findIndex((f) => f.key === active.key);
    return idx <= 0 ? fields : [...fields.slice(idx), ...fields.slice(0, idx)];
  }

  private suggestContext(): SuggestContext {
    return { documentId: this.scanner.documentIdentity(), frameUrl: location.href, flags: this.store.flags };
  }

  /** For each new/changed field: the nearest choice control above it that has an answer (the likeliest parent of a conditional question). */
  private parentsOf(fields: AssistField[], all: AssistField[]): AssistField[] {
    const parents = new Map<string, AssistField>();
    for (const f of fields) {
      const at = all.findIndex((x) => x.key === f.key);
      for (let i = at - 1; i >= 0; i--) {
        const p = all[i];
        if (p.kind !== "select" && p.kind !== "radio" && p.kind !== "checkboxes" && p.kind !== "checkbox" && p.kind !== "combobox") continue;
        if (!p.el.isConnected || readField(p).empty) continue;
        parents.set(p.key, p);
        break;
      }
    }
    return [...parents.values()];
  }

  private requestFields(
    fields: AssistField[], mode: "scan" | "refresh" | "focus", opts: { force?: boolean; focus?: AssistField; parents?: AssistField[] } = {},
  ): void {
    if (!this.session.sessionId || !this.connectionUsable() || this.pageBlocked()) return;
    const now = Date.now();
    const need = fields.filter((f) => {
      const fs = this.store.state(f.key);
      if (opts.force) return true;
      if (this.store.entryFor(f)) return false;
      return !(fs.requestedDigest === f.digest && now - fs.requestedAt < 15000);
    });
    if (!need.length) return;
    for (const f of need) {
      const fs = this.store.state(f.key);
      fs.requestedDigest = f.digest;
      fs.requestedAt = now;
    }
    // the parents ride along with the follow-up (even when their own answer is cached: the server needs their CURRENT selection beside it)
    const along = (opts.parents ?? []).filter((p) => !need.some((f) => f.key === p.key));
    this.session.request([...need, ...along], mode, this.suggestContext(), opts.focus?.nodeId ?? null);
  }

  // ---- suggestions in -----------------------------------------------------------------------------------------------------------

  /**
   * Takes an answer for `field` into the cache. False when it is OLD NEWS: the events and a response are separate requests and may reach the
   * page in either order, so an answer for the very draft the page already holds (same `generation.id`) that stands at an earlier point
   * of that draft's progress must never replace it (a finished draft would go back to "writing a draft" for good).
   */
  private ingest(field: AssistField, s: Suggestion, digest: string): boolean {
    const fs = this.store.state(field.key);
    const previous = fs.entry;
    const held = previous?.suggestion.generation;
    if (held && s.generation && held.id === s.generation.id && behindDraft(s.generation, held)) return false;
    const sameDraft = !!previous && previous.suggestion.state === "GENERATED_DRAFT" && s.state === "GENERATED_DRAFT"
      && previous.suggestion.suggestion?.display === s.suggestion?.display;
    fs.entry = {
      suggestion: s,
      digest,
      knowledgeVersion: s.knowledge_version || this.store.knowledgeVersion,
      receivedAt: Date.now(),
      shownAt: sameDraft ? previous!.shownAt : null,
    };
    fs.skippedReason = null;
    if (s.state === "GENERATED_DRAFT") {
      // what the draft was written from stays with the DRAFT: it comes back unchanged from the server after every knowledge change
      const text = s.suggestion?.display ?? "";
      const id = s.generation?.id ?? "";
      if (!fs.draft || fs.draft.generationId !== id || fs.draft.text !== text) {
        fs.draft = { generationId: id, text, basis: this.draftBasis(s), companyRejected: !!id && this.rejectedGenerations.has(id) };
      }
    }
    this.learning.noteSuggestion(field, s);
    if (s.state === "GENERATED_DRAFT" && !sameDraft && this.current?.key !== field.key && !this.notifiedDrafts.has(s.request_id)) {
      this.notifiedDrafts.add(s.request_id);
      if (this.notifiedDrafts.size > MAX_NOTIFIED) this.notifiedDrafts.delete(this.notifiedDrafts.values().next().value as string);
      this.toast("draft_ready", "info", { label: field.label });
    }
    return true;
  }

  private onBatch(batch: SuggestBatch): void {
    const { fields, response } = batch;
    if (!this.store.knowledgeVersion && response.knowledge_version) {
      this.store.knowledgeVersion = response.knowledge_version;
      this.session.setKnowledge(response.knowledge_version);
    }
    const byNode = new Map(fields.map((f) => [f.nodeId, f]));
    const touched = new Set<string>();
    const redo: AssistField[] = [];
    for (const s of response.results ?? []) {
      const sent = byNode.get(s.node_id);
      if (!sent) continue;
      const field = this.store.get(sent.key) ?? sent;
      const kv = s.knowledge_version;
      if (kv && this.store.knowledgeVersion && kv !== this.store.knowledgeVersion) {
        if (batch.sentKv !== this.store.knowledgeVersion) {
          // answered under knowledge that has since changed: never keep it, ask again
          this.store.state(sent.key).requestedDigest = null;
          redo.push(this.store.get(sent.key) ?? sent);
          continue;
        }
        this.adoptKnowledge(kv); // the server is ahead of us (a knowledge event was missed)
      }
      this.ingest(field, s, sent.digest);
      touched.add(field.key);
    }
    for (const skipped of response.skipped ?? []) {
      const sent = byNode.get(skipped.node_id);
      if (!sent) continue;
      const fs = this.store.state(sent.key);
      fs.skippedReason = skipped.reason || "skipped";
      const skip = skipSuggestion(sent, this.store.knowledgeVersion, fs.skippedReason);
      fs.entry = { suggestion: skip, digest: sent.digest, knowledgeVersion: this.store.knowledgeVersion, receivedAt: Date.now(), shownAt: null };
      this.learning.noteSuggestion(this.store.get(sent.key) ?? sent, skip);
      touched.add(sent.key);
    }
    if (redo.length) this.requestFields(redo, "refresh", { force: true });
    this.afterSuggestions(touched);
  }

  /** The profile/knowledge behind the suggestions changed: nothing computed before it may be inserted any more. */
  private adoptKnowledge(version: string, rebroadcast = false): void {
    if (!version || version === this.store.knowledgeVersion) return;
    this.store.knowledgeVersion = version;
    this.session.setKnowledge(version);
    this.store.clearSuggestions();
    if (rebroadcast && this.store.isTop) this.frames.routeEvent({ type: "knowledge", knowledge_version: version });
    this.requestFields(this.orderFromFocus(this.store.list()), "scan", { force: true });
    this.renderChip();
    this.scheduleDigests();
    this.scheduleRender();
  }

  private afterSuggestions(keys: Set<string>): void {
    if (this.current && keys.has(this.current.key)) this.renderChip();
    this.scheduleDigests();
    this.scheduleRender();
  }

  private onEvent(event: PollEvent): void {
    switch (event.type) {
      case "suggestion": {
        if (this.store.isTop && this.frames.routeEvent(event)) return;
        const s = event.suggestion;
        const field = this.store.get(`${this.store.frameKey}:${s.node_id}`);
        if (!field) return;
        // events follow the cursor order: a result computed under older knowledge than the one we hold is obsolete
        if (s.knowledge_version && this.store.knowledgeVersion && s.knowledge_version !== this.store.knowledgeVersion) return;
        const fs = this.store.state(field.key);
        this.ingest(field, s, fs.requestedDigest ?? field.digest);
        this.afterSuggestions(new Set([field.key]));
        return;
      }
      case "research":
        this.research = event.research;
        this.renderChip(); // a draft may now be "based on older research" (CPL-09)
        this.scheduleRender();
        return;
      case "knowledge":
        this.adoptKnowledge(event.knowledge_version, this.store.isTop);
        return;
      case "generation_budget":
        this.budgetExhausted = true;
        this.generation = { tested: false, ...this.generation, available: false, message: event.message };
        this.scheduleRender();
        return;
      case "drafts_paused":
        // Meridian stopped writing drafts on its own for this page (a limit per page, not the budget): "Draft with AI" still works, and the
        // panel says one calm sentence about it. The flag is the page's alone (a new session starts the count again).
        this.draftsPaused = true;
        this.scheduleRender();
        return;
      case "tracked":
        if (this.job) this.job = { ...this.job, tracked: event.tracked, status: event.status ?? this.job.status, since: event.since ?? this.job.since };
        // a posting that turns out to be recorded as sent (another tab, the app) escalates the notice; one the person records HERE does not (track-record)
        if (this.duplicate && event.tracked === "applied" && this.duplicate.state !== "applied") {
          this.duplicate = {
            ...this.duplicate, state: "applied", acknowledged: false, since: event.since ?? this.duplicate.since, status: duplicateStatus(event.status),
          };
          this.broadcastDuplicate();
        } else if (this.duplicate && event.tracked === "applied" && (event.status || event.since)) {
          // what became of it (rejected, withdrawn ...) or the sending date arrived later: the banner follows
          this.duplicate = {
            ...this.duplicate, since: event.since ?? this.duplicate.since, status: event.status ? duplicateStatus(event.status) : this.duplicate.status,
          };
        }
        this.scheduleRender();
        return;
    }
  }

  // ---- session (top frame) -------------------------------------------------------------------------------------------------------

  private async openSession(): Promise<void> {
    if (!this.store.isTop || this.opening || !this.active || this.quiet || this.session.sessionId || !this.connectionUsable()) return;
    this.opening = true;
    const client = this.session;
    const previousJob = this.jobKey;
    try {
      const report = buildPageReport(this.scanner.lastSnapshot() ?? this.scanner.peek());
      const res = await client.open(report, tabKey());
      if (!this.active || client !== this.session) {
        // switched off (or restarted) while the session was being opened: never leave it behind, polling for nobody
        if (!isFailure(res)) spawn(client.release());
        return;
      }
      if (isFailure(res)) {
        if (res.code === "rate_limited" || res.code === "transport" || res.code === "no_response" || res.code === "internal") {
          if (this.openAttempts++ < 4) {
            if (this.openTimer) clearTimeout(this.openTimer);
            this.openTimer = after(() => { this.openTimer = null; spawn(this.openSession()); }, 2500 * this.openAttempts);
          }
        }
        this.scheduleRender();
        return;
      }
      this.openAttempts = 0;
      this.jobKey = jobIdentity(report);
      this.store.sessionId = res.session_id;
      this.store.knowledgeVersion = res.knowledge_version;
      this.store.settings = { ...this.store.settings, ...res.settings };
      this.job = res.job;
      this.research = res.research;
      this.generation = res.generation;
      this.budgetExhausted = false;
      this.draftsPaused = false; // a session is a fresh count of the drafts written on their own
      this.noteTracked(res.job, previousJob !== "" && previousJob === this.jobKey);
      this.ui.setLanguage(this.store.settings.language);
      this.frames.broadcastSession(res.session_id, res.knowledge_version);
      this.frames.broadcastSettings(this.store.settings);
      this.session.startPolling();
      if (this.store.list().length) {
        this.firstRequestDone = true;
        this.requestFields(this.orderFromFocus(this.store.list()), "scan");
      }
      spawn(this.loadStatus());
      this.scheduleRender();
    } finally {
      if (client === this.session) this.opening = false;
    }
  }

  /**
   * CPL-13: a posting that is already in Meridian's tracker (recorded as sent, or being prepared) when the page's session opens gets a
   * notice at the top of the panel; while it is "sent" and the person has not chosen to continue, Tab inserts nothing. Opening the same
   * session again later (Meridian restarted) keeps the person's answer, and never raises a notice for what this very visit created.
   */
  private noteTracked(job: JobSummary, sameJob: boolean): void {
    const state = job.tracked === "applied" || job.tracked === "preparing" ? job.tracked : null;
    const before = this.duplicate;
    // UXR-08: when it was sent and what became of it, as Meridian says (`since` is the sending date, `tracked_at` the older name for it)
    const since = job.since ?? job.tracked_at ?? undefined;
    const status = state === "applied" ? duplicateStatus(job.status) : undefined;
    if (!state) {
      this.duplicate = null;
    } else if (before && sameJob) {
      this.duplicate = { ...before, state: state === "applied" ? "applied" : before.state, since: since ?? before.since, status: status ?? before.status };
    } else if (!before && sameJob) {
      this.duplicate = null; // this visit began with nothing recorded: what exists now is what the person did here
    } else {
      this.duplicate = { state, since, status, acknowledged: false };
    }
    this.broadcastDuplicate();
  }

  /** The frames of the page follow the top frame: while "already applied" is unanswered none of them inserts on Tab. */
  private broadcastDuplicate(): void {
    if (this.store.isTop) this.frames.broadcastDuplicate(this.duplicateBlocks());
    this.renderChip();
  }

  private async loadStatus(): Promise<void> {
    const res = await sendBg<"status">({ t: "status" });
    if (res.ok) {
      this.profileFacts = res.data.profile?.verified_facts ?? null;
      this.hasCv = typeof res.data.profile?.has_cv === "boolean" ? res.data.profile.has_cv : null;
      this.unapprovedCvs = typeof res.data.profile?.unapproved_cvs === "number" ? res.data.profile.unapproved_cvs : null;
      this.generation = res.data.generation ?? this.generation;
      this.renderChip(); // a file field's chip says more once it is known whether a CV can be attached
      this.scheduleRender();
    }
  }

  private onSessionExpired(): void {
    this.session.reset();
    this.store.sessionId = null;
    this.store.clearSuggestions();
    this.firstRequestDone = false;
    if (this.store.isTop) spawn(this.openSession());
    else this.sendHelloOnce();
  }

  private onUrlChange(): void {
    if (!this.store.isTop) return;
    // until we know whether this is another job, drafts written for the old page are not trusted
    this.store.dropNarratives();
    this.renderChip();
    this.checkJobIdentity(700, true);
  }

  /** A route change may or may not be a different job; titles/canonicals of SPAs update late, so look twice. */
  private checkJobIdentity(delay: number, again: boolean): void {
    if (this.urlTimer) clearTimeout(this.urlTimer);
    this.urlTimer = after(() => {
      if (!this.active || !this.session.sessionId) return;
      const report = buildPageReport(this.scanner.lastSnapshot());
      if (jobIdentity(report) === this.jobKey) {
        // the next step of the same application: refill what was dropped
        this.requestFields(this.store.list(), "refresh");
        if (again) this.checkJobIdentity(1800, false);
        return;
      }
      // suggestions (company-specific drafts!) of the previous job must never be inserted into the new one
      this.store.clearSuggestions();
      this.firstRequestDone = false;
      this.job = null;
      this.research = null;
      this.renderChip();
      spawn(this.session.release().then(() => {
        this.store.sessionId = null;
        spawn(this.openSession());
      }));
    }, delay);
  }

  private onFramesChanged(): void {
    if (!this.store.isTop) return;
    if (this.framesInjectTimer) clearTimeout(this.framesInjectTimer);
    this.framesInjectTimer = after(() => {
      void sendBg<"frames:inject">({ t: "frames:inject" });
      this.scheduleRender();
    }, 300);
  }

  // ---- connection ------------------------------------------------------------------------------------------------------------------

  private setConnection(next: ConnectionState): void {
    const was = this.connection.phase;
    if (!this.store.settings.enabled && next.phase === "ready") next = { ...next, phase: "disabled" };
    this.connection = next;
    if (REVOKING_PHASES.has(next.phase)) this.forgetAnswers();
    if (next.phase === "ready") {
      this.retryCount = 0;
      if (this.retryTimer) clearTimeout(this.retryTimer);
      this.retryTimer = null;
      if (was !== "ready") {
        if (this.everReady && was !== "pairing") this.toast("connection_back", "ok");
        this.everReady = true;
        this.onReady();
      }
    } else {
      // `connecting` is the worker still looking for Meridian: calm, never a "connection lost" event
      if (was === "ready" && this.everReady && next.phase !== "connecting") this.toast("connection_lost", "warn");
      this.scheduleRetry();
    }
    this.renderChip();
    this.scheduleRender();
  }

  /**
   * This browser is no longer connected to Meridian (never paired, revoked, disabled in the app, or the listener could not prove
   * it is Meridian): what Meridian answered earlier is not offered or inserted any more. A merely unreachable Meridian is different:
   * its prefetched answers may still be used.
   */
  private forgetAnswers(): void {
    this.store.clearSuggestions();
    this.firstRequestDone = false;
    if (this.current) {
      this.chipKey = null;
      this.ui.hideChip();
    }
  }

  private onReady(): void {
    if (this.store.isTop) {
      if (this.session.sessionId) {
        this.session.startPolling();
        this.requestFields(this.store.list(), "refresh");
      } else {
        spawn(this.openSession());
      }
    } else if (!this.session.sessionId) {
      this.sendHelloOnce();
    } else {
      this.requestFields(this.store.list(), "refresh");
    }
  }

  private scheduleRetry(): void {
    if (!this.store.isTop || this.retryTimer || !this.active || this.orphaned || this.quiet) return;
    const phase = this.connection.phase;
    // two programs that answer like Meridian (`unpaired` / `revoked` with two or more ports) clear the moment one of them quits: look again
    const ambiguous = (phase === "unpaired" || phase === "revoked") && (this.connection.meridianPorts?.length ?? 0) >= 2;
    if (!ambiguous && phase !== "no_bridge" && phase !== "error" && phase !== "restoring" && phase !== "proof_failed" && phase !== "connecting") return;
    // 1.5 s, 3 s, 6 s, then every 10 s; a shorter hint from the worker wins, a longer one never slows the first looks
    const step = RETRY_DELAYS_MS[Math.min(this.retryCount++, RETRY_DELAYS_MS.length - 1)];
    const hint = this.connection.retryAfterMs;
    const delay = typeof hint === "number" && hint > 0 ? Math.min(step, Math.max(250, hint)) : step;
    this.retryTimer = after(() => {
      this.retryTimer = null;
      if (!this.active || this.orphaned) return;
      if (!this.pageVisible) {
        this.retryDue = true; // nobody is looking: wait for the page to come back instead of probing in the background
        return;
      }
      spawn(this.refreshConnection());
    }, delay);
  }

  private async refreshConnection(): Promise<void> {
    const res = await sendBg<"conn:state">({ t: "conn:state", refresh: true });
    if (res.ok) this.setConnection(res.data);
    else if (res.error.connection) this.setConnection(res.error.connection);
    else this.scheduleRetry();
  }

  private async startPairing(): Promise<void> {
    const res = await sendBg<"pair:start">({ t: "pair:start" });
    if (!res.ok) {
      if (res.error.connection) this.setConnection(res.error.connection);
      else this.toast("error", "warn"); // the server's rate limit, an internal error: never a silent button
      return;
    }
    this.connection = { ...this.connection, phase: "pairing", pairing: { pairingId: res.data.pairingId, code: res.data.code, expiresAt: res.data.expiresAt } };
    this.scheduleRender();
    const poll = async (): Promise<void> => {
      if (!this.active || this.connection.phase !== "pairing") return;
      const r = await sendBg<"pair:poll">({ t: "pair:poll", pairingId: res.data.pairingId });
      if (!this.active) return;
      if (r.ok) {
        if (r.data.state === "pending") {
          this.pairingTimer = after(() => spawn(poll()), 1500);
          return;
        }
        this.setConnection(r.data.connection);
      } else if (r.error.connection) {
        this.setConnection(r.error.connection);
      }
    };
    this.pairingTimer = after(() => spawn(poll()), 1500);
  }

  // ---- child frames --------------------------------------------------------------------------------------------------------------

  private startHello(): void {
    this.sendHelloOnce();
    let tries = 0;
    this.helloTimer = every(() => {
      if (!this.active || this.orphaned || this.session.sessionId || ++tries > 12) {
        if (this.helloTimer) clearInterval(this.helloTimer);
        this.helloTimer = null;
        return;
      }
      this.sendHelloOnce();
    }, 1500);
  }

  private sendHelloOnce(): void {
    this.frames.sendHello();
  }

  private framesHost(): FramesHost {
    const self = this;
    return {
      get frameKey() { return self.store.frameKey; },
      get isTop() { return self.store.isTop; },
      currentSession: () => (self.store.sessionId ? { sessionId: self.store.sessionId, knowledgeVersion: self.store.knowledgeVersion } : null),
      currentSettings: () => self.store.settings,
      onChildHello: () => self.scheduleRender(),
      onChildDigests: (frameKey, items) => {
        if (items.length) self.childDigests.set(frameKey, items);
        else self.childDigests.delete(frameKey);
        // a page whose only form lives in a frame: the quiet (automatic) start of the top frame ends here, and its session serves the frame
        if (items.length && self.quiet && self.active && self.store.isTop) self.reveal();
        self.scheduleRender();
      },
      onChildFocus: (frameKey, key) => {
        self.childFocusKey = key;
        if (key) {
          self.current = null;
          self.chipKey = null;
          self.lastChildKey = key;
          self.lastWasChild = true;
          self.firstUseDone = true;
          self.ui.hideChip();
        }
        self.scheduleRender();
        void frameKey;
      },
      iframes: () => self.allIframes(),
      onChildEnd: () => self.endOfForm(),
      currentDuplicate: () => self.duplicateBlocks(),
      onDuplicate: (blocked) => {
        self.duplicateFromTop = blocked;
        self.renderChip();
        self.scheduleDigests();
      },
      onFrameVisible: (visible) => {
        const hidden = !visible;
        if (hidden === self.frameHidden) return;
        self.frameHidden = hidden;
        if (hidden) {
          self.current = null;
          self.chipKey = null;
          self.ui.hideChip();
          self.ui.setMarks?.([]);
        } else {
          self.renderChip();
        }
        self.scheduleDigests();
      },
      undoAll: () => self.undoAll(),
      onUndoAllRequest: () => {
        self.undoAll();
        self.frames.undoAllInChildren();
      },
      currentPanel: () => self.panelVisible(),
      onPanelState: (available) => { self.topPanelAvailable = available; },
      onChildFocusPanel: (frameKey) => {
        // the keyboard goes into the panel; Esc / Alt+Shift+M there bring it back to the field of that frame the person was on
        if (self.childFocusKey && self.childFocusKey.split(":")[0] === frameKey) {
          self.lastChildKey = self.childFocusKey;
          self.lastWasChild = true;
        }
        self.ui.focusPanel();
      },
      onConfirmation: (evidence) => self.submit.confirmedElsewhere(evidence),
      focusField: (field) => self.focusField(field),
      stops: () => self.scanner.stops(),
      tabStopBetween: (from, to, dir, skipped) => stopForPerson(self.tabScope(), from, to, dir, skipped ?? []),
      skipField: (field) => self.store.settings.skip_filled && !readField(field).empty,
      onSession: (sessionId, knowledgeVersion) => {
        if (self.helloTimer) clearInterval(self.helloTimer);
        self.helloTimer = null;
        const changed = self.store.sessionId !== sessionId;
        self.session.adopt(sessionId, knowledgeVersion);
        self.store.sessionId = sessionId;
        self.store.knowledgeVersion = knowledgeVersion;
        if (changed) {
          self.store.clearSuggestions();
          if (self.store.list().length) {
            self.firstRequestDone = true;
            self.requestFields(self.orderFromFocus(self.store.list()), "scan");
          }
        }
      },
      onSettings: (settings) => {
        self.store.settings = { ...self.store.settings, ...settings };
        self.ui.setLanguage(self.store.settings.language);
      },
      onEvent: (event) => self.onEvent(event),
      focusEdge: (which) => {
        const list = self.store.list().filter((f) => f.el.isConnected && isRendered(f.anchorEl));
        const candidates = which === "first" ? list : [...list].reverse();
        const pick = candidates.find((f) => which === "last" || !self.store.settings.skip_filled || readField(f).empty) ?? null;
        if (!pick) return false;
        // the browser's Tab enters a frame on its first control and Shift+Tab on its last: a Back button before the first assist field
        // or a "Save and Continue" after the last one is the edge, not something to jump over
        const skipped = which === "first" ? list.slice(0, list.indexOf(pick)) : [];
        const stop = edgeTabStop(self.tabScope(), which, pick, skipped);
        if (stop) {
          try {
            stop.focus({ preventScroll: false });
          } catch {
            /* fall back to the assist field */
          }
          if (deepActiveElement() === stop) return true;
        }
        return self.focusField(pick);
      },
      focusByKey: (key) => {
        const f = self.store.get(key);
        if (f) self.focusField(f);
      },
      fillVerified: (keys) => spawn(self.fillVerified(keys ? new Set(keys) : undefined)),
      deactivate: () => self.deactivate(false),
    };
  }

  // ---- digests / panel ---------------------------------------------------------------------------------------------------------------

  private stillHolds(field: AssistField, rec: InsertRecord, reading: ValueReading): boolean {
    if (reading.empty) return false;
    if (rec.labels.length) {
      const have = new Set(reading.labels.map(normalizeText));
      return rec.labels.length === have.size && rec.labels.every((l) => have.has(normalizeText(l)));
    }
    if (field.kind === "checkbox") return true;
    return squash(reading.text) === squash(rec.text);
  }

  private digestOf(field: AssistField): FieldDigest {
    const fs = this.store.peek(field.key);
    const entry = this.store.entryFor(field);
    const reading = readField(field);
    const state = entry?.suggestion.state ?? null;
    const fillable = this.isFillable(field);
    const status = this.pageBlocked() && reading.empty ? "skipped" : deriveStatus({
      state, empty: reading.empty, inserted: fs?.inserted ?? null, stillInserted: !!fs?.inserted && this.stillHolds(field, fs.inserted, reading),
      userEdited: !!fs?.userEdited, failed: activeFailure(fs, entry), skippedReason: fs?.skippedReason ?? null,
    });
    return {
      key: field.key,
      label: field.label,
      section: field.section,
      status,
      tone: digestTone(fs?.inserted?.state ?? state),
      required: field.required,
      fillable,
      generating: state === "GENERATING",
      ...(fillable ? { fillValue: this.fillValueOf(field) } : {}),
    };
  }

  /** What "Fill verified" would put in this field, as the confirmation row shows it (display form, one line, bounded). */
  private fillValueOf(field: AssistField): string {
    const sv = this.store.entryFor(field)?.suggestion.suggestion;
    return cut(squash0(sv?.display ?? suggestedText(sv)), PREVIEW_VALUE_MAX);
  }

  private isFillable(field: AssistField): boolean {
    const entry = this.store.entryFor(field);
    if (!entry) return false;
    const s = entry.suggestion;
    if (s.state !== "VERIFIED" || s.insert !== "tab" || s.flags?.protected || s.flags?.legal || looksLikeConsent(field)) return false;
    return this.eligibility(field, "tab").ok;
  }

  private scheduleDigests(): void {
    if (this.store.isTop || this.digestTimer) return;
    this.digestTimer = after(() => {
      this.digestTimer = null;
      if (!this.active) return;
      // a frame nobody can see offers the panel nothing: its fields are as hidden as the frame (SEC-01)
      this.frames.sendDigests(this.frameHidden ? [] : this.store.list().map((f) => this.digestOf(f)));
    }, 120);
  }

  private scheduleRender(now = false): void {
    if (!this.store.isTop) return;
    if (now) {
      this.renderPanelNow();
      return;
    }
    if (this.flushQueued) return;
    this.flushQueued = true;
    soon(() => {
      this.flushQueued = false;
      this.renderPanelNow();
    });
  }

  /** Typing produces an event per key: rebuild the panel at most every `ms`. */
  private scheduleRenderSoon(ms: number): void {
    if (this.renderSoonTimer) return;
    this.renderSoonTimer = after(() => {
      this.renderSoonTimer = null;
      this.scheduleRender();
    }, ms);
  }

  private researchView(): PanelView["research"] {
    const r = this.research;
    return {
      state: r ? r.state : "none",
      headline: r?.headline ?? "",
      quality: r?.quality ?? "none",
      needsCompany: r?.needs?.field === "company",
      needsSite: r?.needs?.field === "site",
      sources: (r?.sources ?? []).map((s) => ({ label: s.label, url: s.url, status: s.status })),
      company: r?.company?.name ?? this.job?.company ?? "",
      site: r?.site?.domain ?? null,
    };
  }

  private uncoveredFrames(): number {
    let count = 0;
    const now = Date.now();
    let dueAt = Infinity;
    for (const stop of this.scanner.stops()) {
      if (stop.kind !== "frame") continue;
      const first = this.frameSeenAt.get(stop.el) ?? now;
      this.frameSeenAt.set(stop.el, first);
      if (this.frames.frameKeyFor(stop.el)) continue;
      let crossOrigin = false;
      try {
        crossOrigin = !stop.el.contentDocument;
      } catch {
        crossOrigin = true;
      }
      if (!crossOrigin) continue;
      if (now - first > FRAME_NOTE_AFTER_MS) count++;
      else dueAt = Math.min(dueAt, first + FRAME_NOTE_AFTER_MS);
    }
    // the note needs a frame that stayed silent for FRAME_NOTE_AFTER_MS: nothing else would render the panel then, so look again
    // exactly when the youngest candidate becomes old enough (one timer, only while such a frame exists)
    if (dueAt !== Infinity) this.scheduleFrameNote(dueAt - now + 60);
    return count;
  }

  private scheduleFrameNote(ms: number): void {
    if (this.frameNoteTimer) clearTimeout(this.frameNoteTimer);
    this.frameNoteTimer = after(() => {
      this.frameNoteTimer = null;
      if (this.active) this.scheduleRender();
    }, Math.max(60, ms));
  }

  /** The panel's rows: this frame's fields and those of the frames a person can see, in the order of the page. */
  private gatherDigests(): { digests: FieldDigest[]; items: ProgressItem[] } {
    const items: ProgressItem[] = [];
    const currentKey = this.current?.key ?? this.childFocusKey;
    const push = (d: FieldDigest): void => {
      items.push({ key: d.key, label: d.label, section: d.section, status: d.status, tone: d.tone, required: d.required, current: d.key === currentKey });
    };
    const digests: FieldDigest[] = [];
    // the fields of a frame a person cannot see are not the person's to fill, count or walk to: their rows are not listed at all (SEC-01)
    const used = new Set<string>(this.frames.hiddenChildKeys());
    for (const stop of this.scanner.stops()) {
      if (stop.kind === "assist") {
        const d = this.digestOf(stop.field);
        digests.push(d);
        push(d);
      } else if (stop.kind === "frame") {
        const key = this.frames.frameKeyFor(stop.el);
        if (key) {
          used.add(key);
          // the fields of a frame a person cannot see (transparent, tiny, off the page, covered) are not the person's to fill (SEC-01)
          if (frameIsHumanVisible(stop.el)) for (const d of this.childDigests.get(key) ?? []) { digests.push(d); push(d); }
        }
      }
    }
    for (const [key, list] of this.childDigests) {
      if (used.has(key)) continue;
      for (const d of list) { digests.push(d); push(d); }
    }
    return { digests, items };
  }

  private buildPanel(): PanelView {
    const { digests, items } = this.gatherDigests();
    const countable = items.filter((i) => i.status !== "skipped");
    const uncovered = this.uncoveredFrames();
    const host = location.hostname;
    const prefs = this.prefs;
    // `connecting` (the worker is still looking for Meridian) goes to the panel as it is: it has its own calm card
    const connection: ConnectionState = this.orphaned ? { phase: "error", detail: "orphaned" } : this.connection;
    const batch = this.bulkUndo && Date.now() - this.bulkUndo.at < BATCH_UNDO_MS ? this.bulkUndo : null;
    return {
      collapsed: this.collapsed,
      connection,
      generation: {
        // until Meridian has answered nobody knows whether a writing model is set up: no note (UX-08)
        available: this.generation === null ? true : this.generation.available && !this.budgetExhausted,
        known: this.generation !== null,
        message: this.generation?.message,
        budgetExhausted: this.budgetExhausted,
        spentUsd: this.generation?.spent_usd,
        limitUsd: this.generation?.monthly_limit_usd,
        ...(this.draftsPaused ? { draftsPaused: true } : {}),
      },
      job: this.job ? { company: this.job.company, title: this.job.title, location: this.job.location, tracked: this.job.tracked } : null,
      research: this.researchView(),
      progress: { total: countable.length, done: countable.filter((i) => i.status === "inserted" || i.status === "edited" || i.status === "filled").length, items },
      fillableCount: digests.filter((d) => d.fillable).length,
      keysPaused: this.keysPaused,
      noForm: items.length === 0,
      profileEmpty: this.profileFacts === 0,
      permissions: { allSites: uncovered === 0, crossOriginFrames: uncovered },
      pendingDrafts: digests.filter((d) => d.generating).length,
      site: prefs ? this.siteView(host, prefs) : undefined,
      orphaned: this.orphaned || undefined,
      duplicate: this.duplicate && !this.duplicate.acknowledged
        ? { state: this.duplicate.state, since: this.duplicate.since, status: this.duplicate.state === "applied" ? this.duplicate.status ?? "applied" : undefined }
        : null,
      firstUse: !this.firstUseDone && this.firstUseKnown && !this.firstUseSeen && items.length > 0 && !this.orphaned,
      review: this.reviewView(digests),
      batchUndo: batch ? { count: batch.items.length } : null,
      fillPreview: this.previewView(digests),
    };
  }

  /**
   * SEC-01: the confirmation row of "Fill verified": EVERY field the button will fill, with the value that goes in. It lists the keys the
   * person was shown (what appeared afterwards is not part of what they confirm) and drops the ones that stopped being fillable meanwhile.
   */
  private previewView(digests: FieldDigest[]): PanelView["fillPreview"] {
    const preview = this.fillPreview;
    if (!preview) return null;
    const keys = new Set(preview.keys);
    const rows = digests.filter((d) => d.fillable && keys.has(d.key));
    if (!rows.length) {
      this.fillPreview = null;
      return null;
    }
    return { count: rows.length, items: rows.slice(0, PREVIEW_ITEMS_MAX).map((d) => ({ key: d.key, label: d.label, value: d.fillValue ?? "" })) };
  }

  private siteView(host: string, prefs: StoredPrefs): NonNullable<PanelView["site"]> {
    const alwaysOn = this.alwaysOnHere();
    // the note about a rule that still applies is true only while one does
    const note = this.alwaysOnNote && alwaysOn ? { code: "rule" as const, rule: this.alwaysOnNote.rule, text: this.alwaysOnNote.text } : null;
    return {
      host, alwaysOn, hintHidden: prefs.hintOff.includes(host),
      ...(this.alwaysOnPending ? { alwaysOnNeedsPermission: true } : {}),
      ...(note ? { alwaysOnNote: note } : {}),
    };
  }

  /** The browser has the extension on this site by a rule of its own or one Meridian's own setting lists (`always_on_sites`). */
  private alwaysOnHere(): boolean {
    const prefs = this.prefs;
    if (!prefs) return false;
    const rules = [...prefs.alwaysOn, ...(this.store.settings.always_on_sites ?? []), ...(prefs.settings?.always_on_sites ?? [])];
    return hostMatchesRules(location.hostname.toLowerCase().replace(/\.$/, ""), rules);
  }

  /** FR-03: the required questions that are still empty, live while the review is open (Tab reached the end of the form). */
  private reviewView(digests: FieldDigest[]): PanelView["review"] {
    if (!this.reviewOpen) return null;
    const missing = digests.filter((d) => d.required && (d.status === "pending" || d.status === "needs_you" || d.status === "locked"));
    if (!missing.length) {
      this.reviewOpen = false;
      return null;
    }
    return { missingRequired: missing.length, items: missing.slice(0, REVIEW_ITEMS_MAX).map((d) => ({ key: d.key, label: d.label })) };
  }

  private renderPanelNow(): void {
    if (!this.store.isTop || !this.active) return;
    if (this.quiet && !this.orphaned && !this.store.list().length) {
      this.announcePanel();
      return;
    }
    try {
      this.mount(true);
      this.ui.setPanel(this.buildPanel());
    } catch (e) {
      /* the panel is informational: never let it break the page */
      if (__E2E__) renderErrorForTests = e instanceof Error ? `${e.message}\n${e.stack ?? ""}`.slice(0, 600) : String(e);
    }
    this.announcePanel();
  }

  // ---- inserting ----------------------------------------------------------------------------------------------------------------------

  private fillContext() {
    return {
      fetchDocument: async (id: string) => {
        const res = await sendBg<"document:content">({ t: "document:content", id });
        return res.ok ? res.data : null;
      },
      isOrphaned: () => isOrphaned(),
    };
  }

  /** Performs the suggestion cached for `field` and does the bookkeeping. Never throws. */
  private insertField(field: AssistField, via: InsertRecord["via"], context: ReturnType<Controller["fillContext"]> = this.fillContext()): FillOutcome | Promise<FillOutcome> {
    const entry = this.store.entryFor(field);
    const sv = entry?.suggestion.suggestion;
    if (!entry || !sv) return { ok: false, failure: "no_value", changed: false, text: "", labels: [], undo: null };
    // SEC-01: the assistant writes only into what a person can see and click, and it asks AGAIN at the moment of writing (a cover that
    // appeared after the scan, a field the page moved under a banner): Tab, Enter, Replace, a click on Insert and "Fill verified" all end here
    // SEC-01 (second gate): a file and every write the person is looking at (Tab, Enter, Replace, a click) must be ON SCREEN and hit-tested now; and the
    // assistant's own chip / panel, which is what the person decides on, must be on top of everything the page draws (a popover opened after it would
    // hide it): else nothing is written
    if (!this.writable(field, via)) {
      const out: FillOutcome = { ok: false, failure: "hidden", changed: false, text: "", labels: [], undo: null };
      return this.afterFill(field, entry, out, via);
    }
    this.filling++;
    let out: FillOutcome | Promise<FillOutcome>;
    try {
      out = fillField(field, sv, context);
    } finally {
      if (!(out! instanceof Promise)) this.filling--;
    }
    if (out instanceof Promise) {
      return out
        .catch((): FillOutcome => ({ ok: false, failure: "exception", changed: false, text: "", labels: [], undo: null }))
        .then((o) => {
          this.filling--;
          return this.afterFill(field, entry, o, via);
        });
    }
    return this.afterFill(field, entry, out, via);
  }

  /** May the assistant write into this field right now? (see `insertField`). */
  private writable(field: AssistField, via: InsertRecord["via"]): boolean {
    if (!this.scanner.stillVisible(field, field.kind === "file" || via !== "bulk")) return false;
    this.mount(true);
    try {
      return this.ui.ensureOnTop ? this.ui.ensureOnTop() : true;
    } catch {
      return false;
    }
  }

  private afterFill(field: AssistField, entry: SuggestionEntry, out: FillOutcome, via: InsertRecord["via"]): FillOutcome {
    const fs = this.store.state(field.key);
    const s = entry.suggestion;
    if (out.ok) {
      fs.failed = null;
      fs.failedFor = null;
      fs.dismissed = false;
      if (out.changed) {
        const tone = s.state === "GENERATED_DRAFT" ? "draft" : s.state === "INFERRED" ? "inferred" : "verified";
        fs.inserted = { text: out.text, labels: out.labels, state: s.state, tone, requestId: s.request_id, questionId: s.question_id, at: Date.now(), via };
        fs.userEdited = false;
        fs.feedbackSent = false;
        fs.marked = s.state === "INFERRED" || s.state === "GENERATED_DRAFT";
        if (out.undo && via === "bulk") {
          // "Fill verified": one batch, not 11 competing "last insertions" (UX-03)
          const batch = (this.bulkUndo ??= { at: 0, items: [] });
          batch.at = Date.now();
          batch.items.push({ key: field.key, label: field.label, undo: out.undo });
        } else if (out.undo) {
          this.undoRecord = { key: field.key, at: Date.now(), undo: out.undo };
          if (this.undoTimer) clearTimeout(this.undoTimer);
          this.undoTimer = after(() => {
            this.undoRecord = null;
            this.renderChip();
          }, UNDO_WINDOW_MS + 50);
        } else if (this.undoRecord?.key === field.key) {
          this.undoRecord = null;
        }
        this.ui.flash(this.anchorOf(field), tone);
        this.noteFirstInsertion();
        // an asynchronous page revert (masks, controlled inputs) is caught shortly after
        after(() => this.recheck(field, entry), 280);
      }
    } else {
      // a control nobody can see right now is not a failure of the answer: it stays offered, and writes again the moment a person can see it
      if (out.failure === "hidden") this.chipSeen = null; // the chip looks again
      else markFailed(fs, entry, out.failure ?? "verify");
      this.toast("error", "warn", { label: field.label });
    }
    if (this.current?.key === field.key) this.renderChip();
    this.scheduleDigests();
    this.scheduleRender();
    this.renderMarks();
    return out;
  }

  private recheck(field: AssistField, entry: SuggestionEntry): void {
    const sv = entry.suggestion.suggestion;
    const fs = this.store.peek(field.key);
    if (!this.active || !sv || !fs?.inserted || fs.inserted.requestId !== entry.suggestion.request_id || fs.userEdited) return;
    if (!field.el.isConnected) return;
    if (!matches(field, sv)) {
      markFailed(fs, entry, "verify");
      fs.inserted = null;
      fs.marked = false;
      this.toast("error", "warn", { label: field.label });
      if (this.current?.key === field.key) this.renderChip();
      this.scheduleDigests();
      this.scheduleRender();
      this.renderMarks();
    }
  }

  private undoLast(): void {
    // after "Fill verified" Alt+Z takes back only the field the person is ON: the last field of a batch can be far out of view
    if (this.current && this.undoBatchField(this.current.key)) return;
    const rec = this.undoRecord;
    if (!rec || Date.now() - rec.at > UNDO_WINDOW_MS) return;
    this.undoRecord = null;
    const field = this.store.get(rec.key);
    let ok = false;
    try {
      // a field the page replaced meanwhile has nothing to give back (the closure holds the detached element)
      ok = !!field && field.el.isConnected && rec.undo();
    } catch {
      ok = false;
    }
    if (field) {
      const fs = this.store.state(field.key);
      if (ok) {
        fs.inserted = null;
        fs.marked = false;
        fs.held = true; // the user took it back: the next Tab must not re-insert it (the chip stays so Accept still works)
        this.toast("undone", "info", { label: field.label });
      }
      if (this.current?.key === field.key) this.renderChip();
    }
    this.scheduleDigests();
    this.scheduleRender();
    this.renderMarks();
  }

  /** The "Undo" button of a chip: the batch's own field when it belongs to one, else the last single insertion. */
  private undoKey(key: string): void {
    if (this.undoBatchField(key)) return;
    this.undoLast();
  }

  /** Is `key` one of the answers of the last "Fill verified", still inside its window? */
  private batchItem(key: string): BulkUndo["items"][number] | null {
    const batch = this.bulkUndo;
    if (!batch || Date.now() - batch.at >= BATCH_UNDO_MS) return null;
    return batch.items.find((i) => i.key === key) ?? null;
  }

  /** Takes back ONE answer of the last "Fill verified". False when `key` has none. */
  private undoBatchField(key: string): boolean {
    const item = this.batchItem(key);
    if (!item || !this.bulkUndo) return false;
    this.bulkUndo.items = this.bulkUndo.items.filter((i) => i !== item);
    if (!this.bulkUndo.items.length) this.bulkUndo = null;
    const ok = this.revert(item);
    if (ok) this.toast("undone", "info", { label: item.label });
    this.afterUndo();
    return true;
  }

  /** The answers of the last "Fill verified" of THIS frame can still be taken back (the keyboard shortcut and the panel button ask). */
  private batchLive(): boolean {
    const batch = this.bulkUndo;
    return !!batch && batch.items.length > 0 && Date.now() - batch.at < BATCH_UNDO_MS;
  }

  /** UXR-06: "Undo all" in every frame: the top frame asks the others, a frame asks the top frame to (the person may be anywhere). */
  private undoAllEverywhere(): void {
    this.undoAll();
    if (this.store.isTop) this.frames.undoAllInChildren();
    else this.frames.sendUndoAllRequest();
  }

  /** "Undo all": every answer of the last "Fill verified" goes back, newest first. */
  private undoAll(): void {
    const batch = this.bulkUndo;
    if (!batch || Date.now() - batch.at >= BATCH_UNDO_MS) return;
    this.bulkUndo = null;
    let back = 0;
    for (const item of [...batch.items].reverse()) if (this.revert(item)) back++;
    if (back) this.toast("undone", "info", { count: back });
    this.afterUndo();
  }

  /** Runs one undo closure and settles what the field remembers; true when the field really went back. */
  private revert(item: BulkUndo["items"][number]): boolean {
    const field = this.store.get(item.key);
    let ok = false;
    try {
      ok = !!field && field.el.isConnected && item.undo();
    } catch {
      ok = false;
    }
    if (field && ok) {
      const fs = this.store.state(field.key);
      fs.inserted = null;
      fs.marked = false;
      fs.held = true;
    }
    return ok;
  }

  private afterUndo(): void {
    this.renderChip();
    this.scheduleDigests();
    this.scheduleRender();
    this.renderMarks();
  }

  // ---- bulk fill -------------------------------------------------------------------------------------------------------------------------

  /**
   * "Fill verified" was pressed. With a UI that can show the confirmation row (`capabilities.fillPreview`) nothing is written yet: the panel
   * lists every field the button will fill and the value that goes in, and `fill-confirm` fills exactly those (SEC-01). A UI that cannot
   * show it keeps the one-click behaviour (it would otherwise strand the person behind a button that never comes).
   */
  private startFill(): void {
    if (this.duplicateBlocks()) {
      this.toast("no_suggestion", "info"); // UXR-08: "I won't insert anything until you continue" holds for the bulk fill too
      return;
    }
    if (!this.ui.capabilities?.fillPreview) {
      spawn(this.fillVerified());
      return;
    }
    // what the confirmation lists is what a person can see NOW (the write asks again): a field of this frame under a layer is not offered
    const here = new Map(this.store.list().map((f) => [f.key, f] as const));
    const keys = this.gatherDigests().digests.filter((d) => d.fillable && (!here.has(d.key) || this.scanner.stillVisible(here.get(d.key) as AssistField))).map((d) => d.key);
    if (!keys.length) {
      this.fillPreview = null;
      this.toast("no_suggestion", "info");
      this.scheduleRender();
      return;
    }
    this.fillPreview = { keys };
    this.scheduleRender(true);
  }

  /** What "Fill verified" fills in THIS frame: fillable, still on screen for a person, and (after a confirmation) among the fields that were listed. */
  private fillTargets(only?: ReadonlySet<string>): AssistField[] {
    return this.store.list().filter((f) => (!only || only.has(f.key)) && this.isFillable(f) && this.scanner.stillVisible(f));
  }

  private async fillVerified(only?: ReadonlySet<string>): Promise<void> {
    if (this.bulkBusy || this.frameHidden) return;
    if (this.duplicateBlocks()) {
      this.toast("no_suggestion", "info");
      return;
    }
    this.bulkBusy = true;
    let inserted = 0;
    const before = deepActiveElement() as HTMLElement | null;
    // UXR-06: pressed from the keyboard inside the panel, the focus goes back to the form field the person was on (it must not drop to <body>)
    const fromPanel = !!before && inUiHost(before);
    try {
      const targets = this.fillTargets(only);
      if (targets.length) this.bulkUndo = null; // a new batch replaces the previous one
      for (const f of targets) {
        const out = await Promise.resolve(this.insertField(f, "bulk"));
        if (out.ok && out.changed) inserted++;
        await sleep(0);
      }
    } finally {
      this.bulkBusy = false;
      // custom comboboxes take focus while they are filled: give it back to where the user was
      try {
        if (before && before.isConnected && deepActiveElement() !== before) before.focus({ preventScroll: true });
      } catch {
        /* focus is a courtesy */
      }
    }
    if (this.store.isTop) this.frames.fillVerifiedInChildren(only ? [...only] : undefined);
    if (fromPanel && this.active && !this.orphaned) {
      try {
        if (this.store.isTop) this.returnFromPanel();
      } catch {
        /* focus is a courtesy */
      }
    }
    if (this.bulkUndo?.items.length) {
      if (this.bulkUndoTimer) clearTimeout(this.bulkUndoTimer);
      this.bulkUndoTimer = after(() => {
        this.bulkUndoTimer = null;
        this.bulkUndo = null;
        this.afterUndo();
      }, BATCH_UNDO_MS + 50);
    }
    if (inserted || this.store.isTop) this.toast(inserted ? "filled" : "no_suggestion", inserted ? "ok" : "info", { count: inserted }, true, inserted > 0);
    this.renderChip();
    this.scheduleRender();
  }

  // ---- keyboard host ---------------------------------------------------------------------------------------------------------------------

  /** Where the browser's own Tab can stop in this document: the page, plus every open shadow root. */
  private tabScope(): TabScope {
    return { roots: this.roots.list(), fields: this.store.list() };
  }

  /** `interrupts` false: a cheap view for chip hints (no search for controls the browser's Tab would reach in between). */
  private traversal(interrupts = true): TraversalEnv {
    const scope = interrupts ? this.tabScope() : null;
    return {
      stops: this.scanner.stops(),
      skipFilled: this.store.settings.skip_filled,
      hasValue: (f) => !readField(f).empty,
      frameKeyFor: (iframe) => (frameIsHumanVisible(iframe) ? this.frames.frameKeyFor(iframe) : null),
      isChild: !this.store.isTop,
      hint: this.frames.hint,
      tabStopBetween: (from, to, dir, skipped) => (scope ? stopForPerson(scope, from, to, dir, skipped) : null),
    };
  }

  /** `field` null: the control is no assist field (Safari's walk asks whether a widget of the page owns Tab there). */
  private popupOpen(field: AssistField | null, target: Element): boolean {
    const active = deepActiveElement() ?? target;
    const popupish = (el: Element | null | undefined): boolean =>
      !!el && el.getAttribute("aria-expanded") === "true" && (el.getAttribute("role") === "combobox" || el.hasAttribute("aria-haspopup"));
    if (popupish(active) || popupish(target) || popupish(field?.el) || popupish(field?.links.trigger)) return true;
    // a highlighted option (aria-activedescendant) means the page's own widget is mid-selection and owns Tab
    if ([active, target, field?.el].some((el) => !!el?.getAttribute?.("aria-activedescendant"))) return true;
    if (field?.kind === "combobox" && field.el.getAttribute("aria-expanded") === "true") return true;
    // an autocomplete that never maintains aria-expanded but whose list (aria-controls / aria-owns) is on screen is mid-selection too
    for (const el of [active, target, field?.el]) {
      if (el && controlledListOpen(el)) return true;
    }
    return !!target.closest?.('[role="listbox"],[role="menu"]');
  }

  /** A popup the page opened next to the field (a date picker's calendar, a list under the input) is on screen: see `popups.ts`. */
  private popupNearby(field: AssistField): boolean {
    const own: (Element | null)[] = [field.el, field.anchorEl, field.focusEl, field.links.proxy, field.links.trigger, field.links.search];
    for (const m of field.members) own.push(m.el, m.proxy);
    const others = this.store.list().filter((f) => f.key !== field.key).map((f) => f.el);
    return !!openPopupNear({ elements: own, rect: anchorRect(field), others }, this.roots.list());
  }

  private keyboardHost(): KeyboardHost {
    const self = this;
    return {
      active: () => self.active && !isOrphaned() && !self.orphaned,
      paused: () => self.keysPaused,
      busy: () => self.isBusy(),
      intercepting: () => !self.orphaned && self.active && self.store.settings.enabled && !self.pageBlocked() && !self.frameHidden
        && (self.connectionUsable() || self.store.list().some((f) => !!self.store.entryFor(f))),
      autoAdvance: () => self.store.settings.auto_advance,
      fieldFor: (el) => self.scanner.fieldFor(el),
      popupOpen: (field, target) => self.popupOpen(field, target),
      popupNearby: (field) => self.popupNearby(field),
      plainWidgetOwnsTab: (target) => self.popupOpen(null, target),
      plan: (field) => {
        const e = self.eligibility(field, "tab");
        if (!e.ok) {
          if (e.reason === "stale") self.requestFields([field], "focus", { force: true, focus: field });
          return { insert: false, async: false };
        }
        const sv = self.store.entryFor(field)?.suggestion.suggestion;
        return { insert: true, async: !!sv && isAsyncOperation(sv.operation) };
      },
      insertTab: (field) => {
        self.fillingField = field;
        return self.insertField(field, "tab");
      },
      afterInsert: (field) => {
        // a choice just changed: conditional questions render synchronously in most frameworks, so look right away
        // (the next stop must include them) instead of waiting for the debounced scan
        if (field.kind === "text" || field.kind === "textarea" || field.kind === "editable" || field.kind === "file") return;
        try {
          // a very large page costs more than a keystroke can afford (~150 ms at 15,000 nodes): let the debounced scan do it
          if (self.scanner.scanCost() > syncScanLimitMs()) self.scanner.request("insert", 60);
          else self.scanner.scanNow();
        } catch {
          /* the debounced scan will catch up */
        }
      },
      explicitReady: (field) => {
        if (self.chipKey !== field.key) return false;
        const entry = self.store.entryFor(field);
        return !!entry && entry.suggestion.insert === "explicit" && self.eligibility(field, "explicit").ok;
      },
      insertExplicit: (field) => {
        self.fillingField = field;
        return self.insertField(field, "enter");
      },
      chipShowing: (field) => self.chipKey === field.key,
      dismiss: (field) => self.dismissField(field),
      canRegenerate: (field) => {
        const entry = self.store.entryFor(field);
        return self.chipKey === field.key && entry?.suggestion.state === "GENERATED_DRAFT" && self.canGenerate();
      },
      regenerate: (field) => spawn(self.regenerate(field, "again")),
      hasUndo: () => (!!self.undoRecord && Date.now() - self.undoRecord.at < UNDO_WINDOW_MS) || (!!self.current && !!self.batchItem(self.current.key)),
      undo: () => self.undoLast(),
      focusPanel: () => self.focusPanelFromKey(),
      chipAction: (n) => {
        try {
          return self.ui.chipAction?.(n) === true;
        } catch {
          return false;
        }
      },
      hasBatchUndo: () => self.batchLive(),
      undoBatch: () => self.undoAllEverywhere(),
      leavePanel: () => self.returnFromPanel(),
      endOfForm: () => self.endOfForm(),
      traversal: () => self.traversal(),
      go: (stop, dir) => self.go(stop, dir),
      beginBusy: () => {
        self.keyBusy = true;
        self.keyBusyAt = Date.now();
        return ++self.busyGen;
      },
      endBusy: (id) => {
        if (id !== self.busyGen) return false; // given up on while it ran
        self.keyBusy = false;
        self.keyBusyAt = 0;
        return true;
      },
      focusNative: (el) => {
        try {
          el.focus({ preventScroll: false });
        } catch {
          return false;
        }
        return deepActiveElement() === el || document.activeElement === el;
      },
    };
  }

  /**
   * Moves the focus to a field and scrolls it into view, THEN draws its chip where the field is now (UXR-04): the focus raises the chip before
   * the scroll that brings the field into its dialog / drawer / pane has happened, and a chip drawn at the old place would float over whatever
   * is behind the box until the next animation frame.
   */
  private focusField(field: AssistField): boolean {
    const ok = focusAssistField(field);
    if (ok && this.active && !this.orphaned && this.current?.key === field.key) this.renderChip();
    return ok;
  }

  private go(stop: NextStop, dir: 1 | -1): boolean {
    switch (stop.kind) {
      case "assist": return this.focusField(stop.field);
      case "frame": return this.frames.handoffInto(stop.iframe, stop.frameKey, dir);
      case "parent": return this.frames.handoffOut(dir);
      default: return false;
    }
  }

  /** Keys are swallowed while an asynchronous fill (or "Fill verified") runs, but never for long: a fill that hangs is given up on. */
  private isBusy(): boolean {
    if (this.bulkBusy) return true;
    if (!this.keyBusy) return false;
    if (Date.now() - this.keyBusyAt < busyLimitMs()) return true;
    // The fill outlived the limit (a document that never arrives, a widget that never answers): hand the keyboard back, and do not
    // start the same fill again on the next Tab, or the person would be held at this field for ever.
    this.busyGen++;
    this.keyBusy = false;
    this.keyBusyAt = 0;
    const field = this.fillingField;
    const entry = field ? this.store.entryFor(field) : null;
    if (field && entry) {
      markFailed(this.store.state(field.key), entry, "timeout");
      this.toast("error", "warn", { label: field.label });
      if (this.current?.key === field.key) this.renderChip();
    }
    return false;
  }

  /** Every <iframe> of this document and of its open shadow roots, the page's own ones (SEC-01: the hidden ones are the ones that matter). */
  private allIframes(): HTMLIFrameElement[] {
    const out: HTMLIFrameElement[] = [];
    const take = (root: ParentNode): void => {
      try {
        for (const el of root.querySelectorAll("iframe")) if (!inUiHost(el)) out.push(el);
      } catch {
        /* a hostile root */
      }
    };
    take(document);
    for (const root of this.roots.list()) take(root);
    return out;
  }

  /**
   * Alt+Shift+M: into the panel. A form inside a frame has no panel of its own: the key is relayed to the top frame (UXR-07), and swallowed
   * only when there IS a panel there for the keyboard to enter (a key that went nowhere keeps typing what it types).
   */
  private focusPanelFromKey(): boolean {
    if (this.store.isTop) return this.ui.focusPanel();
    if (!this.topPanelAvailable) return false;
    this.frames.sendFocusPanel();
    return true;
  }

  /** Top frame: a panel the keyboard can enter is on screen (the frames are told when that changes, and when they say hello). */
  private announcePanel(): void {
    if (!this.store.isTop) return;
    const available = this.panelVisible();
    if (available === this.panelAnnounced) return;
    this.panelAnnounced = available;
    this.frames.broadcastPanel(available);
  }

  /** UX-02: focus goes back to the form field the person was on (Esc / Alt+Shift+M inside the panel); else to the first empty question. */
  private returnFromPanel(): boolean {
    if (this.lastWasChild && this.lastChildKey && this.frames.focusChildField(this.lastChildKey)) return true;
    const known = this.lastField ? this.store.get(this.lastField.key) : undefined;
    const target = known ?? this.store.list().find((f) => f.el.isConnected && isRendered(f.anchorEl) && readField(f).empty) ?? this.store.list()[0];
    return !!target && this.focusField(target);
  }

  /**
   * FR-03: Tab found no further question. Meridian never blocks the key (the browser's own Tab goes on to Back / Submit); the panel lists the
   * required questions that are still empty. A frame that holds the last question tells the top frame.
   */
  private endOfForm(): void {
    if (!this.store.isTop) {
      this.frames.sendEndOfForm();
      return;
    }
    this.reviewOpen = true;
    this.scheduleRender();
  }

  private dismissField(field: AssistField): void {
    this.store.state(field.key).dismissed = true;
    this.chipKey = null;
    this.ui.hideChip();
  }

  /** `fresh`: a rewrite from the CURRENT research and Profile, not a revision of the old text (the old text may name the wrong company). */
  private async regenerate(field: AssistField, instruction: RegenerateRequest["instruction"], explicitDraft = false, fresh = false): Promise<void> {
    const entry = this.store.entryFor(field);
    if (!entry || !this.session.sessionId) return;
    const s = entry.suggestion;
    const request: RegenerateRequest = {
      question_id: s.question_id,
      node_id: field.nodeId,
      frame_key: field.frameKey,
      instruction,
      element: explicitDraft ? field.element : null,
      // the draft being revised, never what the user typed
      current_text: explicitDraft || fresh ? null : cut(suggestedText(s.suggestion), 20000) || null,
    };
    this.toast("regenerating", "info", { label: field.label });
    const res = await this.session.regenerate(request);
    if (isFailure(res)) {
      this.toast("error", "warn", { label: field.label });
      return;
    }
    const fs = this.store.state(field.key);
    fs.failed = null;
    fs.failedFor = null;
    this.ingest(field, res, field.digest);
    this.afterSuggestions(new Set([field.key]));
  }

  // ---- UI actions ---------------------------------------------------------------------------------------------------------------------------

  private toast(kind: ToastView["kind"], tone: ToastView["tone"], params?: ToastView["params"], show = true, undoable = false): void {
    if (!show) return;
    try {
      this.ui.toast(undoable ? { kind, tone, params, undoable: true } : { kind, tone, params });
    } catch {
      /* toasts are decoration */
    }
  }

  private showTrack(view: TrackAskView | null): void {
    this.trackEvidence = view?.evidence ?? "";
    if (view) {
      // a quiet (automatic) start on the confirmation page: the UI is mounted just for this prompt, the panel stays unrendered
      if (this.quietUnmountTimer) clearTimeout(this.quietUnmountTimer);
      this.quietUnmountTimer = null;
      this.mount(true);
    }
    this.ui.askTrack(view);
    if (!view) this.settleQuietUi();
  }

  /** A page that stayed quiet (no form) must not keep an invisible UI around once its only prompt is over. */
  private settleQuietUi(): void {
    if (!this.quiet || !this.mounted) return;
    if (this.quietUnmountTimer) clearTimeout(this.quietUnmountTimer);
    this.quietUnmountTimer = after(() => {
      this.quietUnmountTimer = null;
      if (!this.active || !this.quiet || !this.mounted || this.store.list().length) return;
      this.ui.hideChip();
      this.ui.unmount();
      this.mounted = false;
    }, QUIET_UNMOUNT_MS);
  }

  private onAction(action: UIAction): void {
    try {
      this.handleAction(action);
    } catch {
      /* a UI click must never throw into the page */
    }
  }

  private handleAction(a: UIAction): void {
    if (this.learning.handleAction(a)) return;
    switch (a.type) {
      case "accept":
      case "replace": {
        const field = this.store.get(a.key);
        if (!field) return;
        const e = this.eligibility(field, "explicit", a.type === "replace");
        if (!e.ok) {
          // the field already holds the suggestion (UX-09): nothing to insert and nothing to complain about
          if (e.reason === "equal") this.renderChip();
          else this.toast("no_suggestion", "info", { label: field.label });
          return;
        }
        spawn(Promise.resolve(this.insertField(field, a.type === "replace" ? "replace" : "click")).then((out) => {
          if (out.ok && out.changed) this.toast("filled", "ok", { label: field.label });
        }));
        return;
      }
      case "regenerate": {
        const field = this.store.get(a.key);
        if (field) spawn(this.regenerate(field, a.instruction));
        return;
      }
      case "draft": {
        const field = this.store.get(a.key);
        if (field) spawn(this.regenerate(field, "again", true));
        return;
      }
      case "dismiss": {
        const field = this.store.get(a.key);
        if (field) this.dismissField(field);
        return;
      }
      case "undo": this.undoKey(a.key); return;
      case "undo-all": this.undoAllEverywhere(); return;
      case "fill-verified": this.startFill(); return;
      case "fill-confirm": {
        const preview = this.fillPreview;
        this.fillPreview = null;
        this.scheduleRender(true);
        if (preview) spawn(this.fillVerified(new Set(preview.keys)));
        return;
      }
      case "fill-cancel":
        this.fillPreview = null;
        this.scheduleRender();
        return;
      case "toggle-collapse":
        this.collapsed = !this.collapsed;
        this.collapseTouched = true; // the person decided: the size of the window no longer does (UX-05)
        this.scheduleRender();
        return;
      case "keys-help":
        // the cheat sheet lives in the panel: it must be open to be read
        this.collapsed = false;
        this.collapseTouched = true;
        this.scheduleRender();
        return;
      case "review-dismiss":
        this.reviewOpen = false;
        this.scheduleRender();
        return;
      case "panel-escape":
        // Esc inside the panel or a prompt that used it for nothing: the keyboard goes back to the form field the person was on (UX-02)
        this.returnFromPanel();
        return;
      case "duplicate-continue":
        if (this.duplicate) {
          this.duplicate = { ...this.duplicate, acknowledged: true };
          this.broadcastDuplicate();
          this.scheduleDigests();
          this.scheduleRender();
        }
        return;
      case "rewrite": {
        const field = this.store.get(a.key);
        if (field) spawn(this.rewrite(field));
        return;
      }
      case "use-cv": spawn(this.useDocument(a.id, a.key)); return;
      case "track-applied": this.openRecordCard(); return;
      case "track-save": spawn(this.saveToTracker()); return;
      case "toggle-keys":
        this.keysPaused = !this.keysPaused;
        this.toast(this.keysPaused ? "paused" : "resumed", "info");
        this.scheduleRender();
        return;
      case "focus-item": {
        const own = this.store.get(a.key);
        if (own) this.focusField(own);
        else this.frames.focusChildField(a.key);
        return;
      }
      case "connect": spawn(this.startPairing()); return;
      case "cancel-pairing": {
        const pairing = this.connection.pairing;
        if (this.pairingTimer) clearTimeout(this.pairingTimer);
        if (pairing) spawn(sendBg<"pair:cancel">({ t: "pair:cancel", pairingId: pairing.pairingId }).then(() => this.refreshConnection()));
        else spawn(this.refreshConnection());
        return;
      }
      case "retry-connection": spawn(this.refreshConnection()); return;
      case "open-meridian": void sendBg<"meridian:open">({ t: "meridian:open" }); return;
      case "open-options": void sendBg<"options:open">({ t: "options:open", anchor: a.anchor }); return;
      case "research-company":
        this.rejectDrafts(); // the person said it is not this company: what was written for it is out, whatever the new research finds
        spawn(this.researchAction("company", a.company));
        return;
      case "research-site": spawn(this.researchAction("site", a.domain)); return;
      case "research-refresh": spawn(this.researchAction("refresh")); return;
      case "save-setting": spawn(this.saveSetting(a.value)); return;
      case "track-record":
        spawn(this.submit.record(this.trackEvidence).then((res) => {
          if (!isFailure(res)) {
            if (this.job) this.job = { ...this.job, tracked: res.tracked };
            // the person recorded it HERE: that is not a duplicate of anything
            this.duplicate = { state: "applied", since: new Date().toISOString(), acknowledged: true };
            this.broadcastDuplicate();
            this.toast("saved", "ok", { scope: "application" });
            this.scheduleRender();
          } else {
            this.toast("error", "warn");
          }
        }));
        return;
      case "track-dismiss": this.submit.dismiss(); return;
      case "deactivate": this.deactivate(true); return;
      case "always-on-site": {
        const host = location.hostname;
        const enabled = !this.alwaysOnHere();
        this.alwaysOnNote = null; // a new try, a new answer
        spawn(sendBg<"site:always-on">({ t: "site:always-on", enabled }).then((res) => {
          if (!res.ok) {
            // UX-13: the browser has not given the extension this site yet (a small window asks): the switch stays OFF, the menu says why in one
            // calm line, and it goes away by itself once the access is granted
            if (res.error.code === "permission" && enabled) {
              this.alwaysOnPending = true;
              this.scheduleRender();
              spawn(this.watchAlwaysOn());
            } else if (res.error.code === "rule" && !enabled) {
              // Switching OFF removed this site's own rule, but another one (a parent site's, one set in Meridian) still starts the assistant
              // here: the switch keeps saying ON, and one calm line names that rule and where it is removed
              this.alwaysOnNote = { rule: ruleOf(res.error.detail), text: res.error.detail };
              if (this.prefs) this.prefs = { ...this.prefs, alwaysOn: this.prefs.alwaysOn.filter((h) => h !== host) };
              this.scheduleRender();
            } else if (res.error.code !== "orphaned") {
              this.toast("error", "warn"); // never a switch that does nothing and says nothing
            }
            return;
          }
          this.alwaysOnPending = false;
          if (!this.prefs) return;
          this.prefs = { ...this.prefs, alwaysOn: enabled ? [...new Set([...this.prefs.alwaysOn, host])] : this.prefs.alwaysOn.filter((h) => h !== host) };
          this.scheduleRender();
        }));
        return;
      }
      case "hide-hint-site": {
        const host = location.hostname;
        spawn(sendBg<"hint:hide">({ t: "hint:hide" }).then((res) => {
          if (!res.ok || !this.prefs) return;
          this.prefs = { ...this.prefs, hintOff: [...new Set([...this.prefs.hintOff, host])] };
          this.scheduleRender();
        }));
        return;
      }
      default: return;
    }
  }

  /**
   * UX-13: waits (a couple of minutes at most) for the person's answer in the permission window: the rule is stored by the worker's options
   * page when they allow the site, so the browser's own list is read again until it names this site.
   */
  private async watchAlwaysOn(): Promise<void> {
    for (let round = 0; round < 48 && this.alwaysOnPending && this.active && !this.orphaned; round++) {
      await sleep(alwaysOnPollMs());
      if (!this.alwaysOnPending || !this.active || this.orphaned) return;
      const res = await sendBg<"ctx">({ t: "ctx" });
      if (!res.ok || !this.alwaysOnPending) continue;
      this.prefs = res.data.prefs;
      if (this.alwaysOnHere()) {
        this.alwaysOnPending = false;
        this.scheduleRender();
        return;
      }
    }
    if (this.alwaysOnPending && this.active) {
      this.alwaysOnPending = false;
      this.scheduleRender();
    }
  }

  /** CPL-10 "I applied": the record card without any confirmation text (the person tells; nothing was detected). */
  private openRecordCard(): void {
    if (!this.store.isTop) return;
    const job = this.job;
    this.showTrack({ company: job?.company || location.hostname, title: job?.title || "", evidence: "" });
  }

  /** CPL-10 "Save to tracker": the posting goes into Meridian's tracker WITHOUT saying it was sent. */
  private async saveToTracker(): Promise<void> {
    // A posting Meridian already has as sent is never "saved" again: the menu does not offer it, and a press that gets here anyway is
    // answered without asking the server (UXR-01)
    if (this.job?.tracked === "applied") {
      this.noteAlreadyRecorded();
      return;
    }
    const res = await this.session.track({ event: "save", evidence: "", notes: "" });
    if (isFailure(res)) {
      this.toast("error", "warn");
      return;
    }
    // Meridian says the application was SENT (another tab, the app, an earlier visit): that is what the person is told. Saying "saved" of
    // something Meridian did not save would be a lie about their tracker.
    if (res.state === "already_recorded" || res.tracked === "applied") {
      if (this.job) this.job = { ...this.job, tracked: "applied" };
      this.noteAlreadyRecorded();
      return;
    }
    if (this.job) this.job = { ...this.job, tracked: res.tracked };
    this.toast("saved", "ok", { scope: "application" });
    this.scheduleRender();
  }

  /**
   * The posting is recorded as SENT in Meridian: say so, and put up the notice CPL-13 puts up for such a posting (with its date and what
   * became of it, as `JobSummary` carries them) unless the person has already answered it ("Continue anyway" stays answered).
   */
  private noteAlreadyRecorded(): void {
    const job = this.job;
    const since = job?.since ?? job?.tracked_at ?? undefined;
    const status = duplicateStatus(job?.status);
    if (!this.duplicate) {
      this.duplicate = { state: "applied", since, status, acknowledged: false };
      this.broadcastDuplicate();
    } else if (this.duplicate.state !== "applied") {
      this.duplicate = { ...this.duplicate, state: "applied", since: since ?? this.duplicate.since, status, acknowledged: false };
      this.broadcastDuplicate();
    }
    this.toast("already_recorded", "info");
    this.scheduleRender();
  }

  /**
   * CPL-20: the person chose ANOTHER document for a file field (the chip's click-only menu). Only a document Meridian offered for THIS field
   * (`alternatives`) can be chosen. It is fetched through the worker like the one on offer, checked against its own hash by the fill, and
   * replaces what the field holds. It then becomes what the field offers (the one that was on offer becomes an alternative), so Undo, "already
   * inserted" and the check after the write all talk about the document that is really in the field. Nothing is attached by a key: Tab inserts
   * the document on offer, always.
   */
  private async useDocument(id: string, key?: string): Promise<void> {
    const field = this.store.get(key ?? this.current?.key ?? "");
    const entry = field ? this.store.entryFor(field) : null;
    const sv = entry?.suggestion.suggestion;
    if (!field || !entry || !sv || field.kind !== "file" || sv.operation !== "ATTACH_FILE" || !sv.document || this.keyBusy || this.bulkBusy) return;
    const chosen = this.documentAlternatives(sv).find((d) => d.id === id);
    if (!chosen) return;
    // the same rules as a click on Replace (a notice about a sent application, a hidden frame ...); a field that already holds the document
    // on offer is exactly when the person wants another one
    const may = this.eligibility(field, "explicit", true);
    if (!may.ok && may.reason !== "equal") {
      this.toast("no_suggestion", "info", { label: field.label });
      return;
    }
    const run = ++this.busyGen;
    this.keyBusy = true; // Tab waits while the document is fetched and attached, as it does for any asynchronous fill
    this.keyBusyAt = Date.now();
    this.fillingField = field; // (a fill that outlives the limit is reported for THIS field)
    const previous = entry.suggestion;
    try {
      const context = this.fillContext();
      const content = await context.fetchDocument(chosen.id);
      if (!content || content.id !== chosen.id) {
        this.toast("error", "warn", { label: field.label });
        return;
      }
      if (this.store.entryFor(field) !== entry || !this.active || this.orphaned) return; // a newer answer arrived meanwhile: not this one's business
      const offered = sv.document;
      const rest = this.documentAlternatives(sv).filter((d) => d.id !== chosen.id);
      const swapped: Suggestion = {
        ...previous,
        suggestion: {
          ...sv,
          display: content.filename,
          document: {
            id: content.id, filename: content.filename, mime: content.mime, size: content.size, sha256: content.sha256,
            alternatives: [{ id: offered.id, filename: offered.filename, version: 0 }, ...rest].slice(0, MAX_ALTERNATIVES),
          },
        },
      };
      entry.suggestion = swapped;
      const out = await Promise.resolve(this.insertField(field, "replace", { ...context, fetchDocument: async (docId) => (docId === chosen.id ? content : context.fetchDocument(docId)) }));
      if (out.ok && out.changed) {
        this.toast("filled", "ok", { label: field.label });
      } else if (!out.ok) {
        // the field refused it: what was on offer stays on offer, and the failure belongs to the document that did not go in
        entry.suggestion = previous;
        const fs = this.store.peek(field.key);
        if (fs) { fs.failed = null; fs.failedFor = null; }
        if (this.current?.key === field.key) this.renderChip();
      }
    } finally {
      if (run === this.busyGen) {
        this.keyBusy = false;
        this.keyBusyAt = 0;
      }
      this.renderChip();
    }
  }

  /** CPL-09: write the draft again from the research and Profile as they are NOW (explicit: it costs money, so it is never automatic). */
  private async rewrite(field: AssistField): Promise<void> {
    if (!this.canGenerate()) {
      this.toast("error", "warn", { label: field.label });
      return;
    }
    await this.regenerate(field, "again", false, true);
  }

  private async researchAction(kind: "company" | "site" | "refresh", value = ""): Promise<void> {
    const res = await this.session.research(kind, value);
    if (isFailure(res)) {
      this.toast("error", "warn");
      return;
    }
    this.research = res;
    this.scheduleRender();
  }

  private async saveSetting(value: "always" | "never"): Promise<void> {
    this.learning.settingChosen(value);
    const res = await sendBg<"settings:patch">({ t: "settings:patch", patch: { save_corrections: value } });
    if (res.ok) {
      this.store.settings = { ...this.store.settings, ...res.data };
      this.frames.broadcastSettings(this.store.settings);
    }
  }
}

/** Test hook (E2E builds only; the whole block is dead code in production builds): a plain-data view of the internal state. */
if (__E2E__) {
  (Controller.prototype as unknown as { debugState: () => Record<string, unknown> }).debugState = function debugState(this: Controller) {
    const self = this as unknown as {
      store: Store; orphaned: boolean; active: boolean; connection: ConnectionState; keysPaused: boolean; keyBusy: boolean; bulkBusy: boolean;
    };
    const fields = self.store.list().map((f) => {
      const fs = self.store.peek(f.key);
      const entry = self.store.entryFor(f);
      return {
        key: f.key, kind: f.kind, label: f.label, digest: f.digest, state: entry?.suggestion.state ?? null, insert: entry?.suggestion.insert ?? null,
        inserted: fs?.inserted?.text ?? null, failed: activeFailure(fs, entry), dismissed: !!fs?.dismissed, hasEntry: !!entry,
      };
    });
    const inner = this as unknown as {
      quiet: boolean; mounted: boolean; pageVisible: boolean; roots: RootRegistry; notifiedDrafts: Set<string>; learning: LearningLoop;
      scanner: Scanner; session: SessionClient; retryCount: number; childDigests: Map<string, unknown>; disposers: unknown[]; job: JobSummary | null;
    };
    return {
      active: self.active, orphaned: self.orphaned, frameKey: self.store.frameKey, sessionId: self.store.sessionId, knowledge: self.store.knowledgeVersion,
      connection: self.connection.phase, paused: self.keysPaused, busy: self.keyBusy || self.bulkBusy, fields,
      job: inner.job ? { company: inner.job.company, title: inner.job.title } : null, renderError: renderErrorForTests, quiet: inner.quiet, mounted: inner.mounted, pageVisible: inner.pageVisible, retryCount: inner.retryCount, panelVisible: this.panelVisible(),
      current: (this as unknown as { current: AssistField | null }).current?.key ?? null,
      chipKey: (this as unknown as { chipKey: string | null }).chipKey,
      swallowed: swallowedErrors(),
      collapsed: (this as unknown as { collapsed: boolean }).collapsed,
      duplicate: (this as unknown as { duplicate: unknown }).duplicate,
      frameHidden: (this as unknown as { frameHidden: boolean }).frameHidden,
      batchUndo: (this as unknown as { bulkUndo: { items: unknown[] } | null }).bulkUndo?.items.length ?? 0,
      reviewOpen: (this as unknown as { reviewOpen: boolean }).reviewOpen,
      firstUseDone: (this as unknown as { firstUseDone: boolean }).firstUseDone,
      firstUseSeen: (this as unknown as { firstUseSeen: boolean }).firstUseSeen,
      topPanelAvailable: (this as unknown as { topPanelAvailable: boolean }).topPanelAvailable,
      fillPreview: (this as unknown as { fillPreview: { keys: string[] } | null }).fillPreview?.keys.length ?? 0,
      alwaysOnPending: (this as unknown as { alwaysOnPending: boolean }).alwaysOnPending,
      roots: inner.roots.size, scan: { ...(inner.scanner?.stats ?? { scans: 0, lastMs: 0, maxMs: 0, nodes: 0 }), cost: Math.round((inner.scanner?.scanCost() ?? 0) * 10) / 10 },
      polls: { ...(inner.session?.stats ?? { polls: 0, catchUps: 0 }) },
      retained: {
        notified: inner.notifiedDrafts.size, learning: inner.learning?.sizes() ?? null, children: inner.childDigests.size, disposers: inner.disposers.length,
      },
    };
  };
}

/**
 * The side of the field the chip prefers. The chip is ~100-150 px tall: when the next field starts within that band below the
 * current one, "below" would cover it, so ask for "above" (when there is room for it). The visual layer measures the real chip
 * and the `avoid` rectangles and may still decide otherwise; this is its tie-break and the answer for UIs that ignore `avoid`.
 */
export function preferredSide(anchor: Rect, avoid: Rect[]): "below" | "above" {
  const next = avoid[0];
  if (!next) return "below";
  const bottom = anchor.y + anchor.h;
  if (next.y + next.h <= anchor.y) return "below"; // the next field is above: below is clear
  if (next.y - bottom > CHIP_BAND_PX) return "below"; // far enough away
  return anchor.y >= MIN_ROOM_ABOVE_PX ? "above" : "below";
}

/**
 * `tabStopBetween` as a PERSON meets it: an <iframe> nobody can see (transparent, tiny, covered, off the page) is a stop of the browser's
 * own Tab, but it is not a place a person is going, so the assistant's walk does not stop at it and never counts it as "a control that must
 * not be skipped" (SEC-01). Anything else the browser's Tab would reach still is.
 */
export function stopForPerson(scope: TabScope, from: AssistField | Element, to: AssistField | Element | null, dir: 1 | -1, skipped: readonly AssistField[]): HTMLElement | null {
  let origin: AssistField | Element = from;
  for (let guardCount = 0; guardCount < 12; guardCount++) {
    const stop = tabStopBetween(scope, origin, to, dir, skipped);
    if (!stop) return null;
    if (stop instanceof HTMLIFrameElement && !frameIsHumanVisible(stop)) {
      origin = stop;
      continue;
    }
    return stop;
  }
  return null;
}

/**
 * Where the chip should sit relative to the field: BESIDE it (to its right, level with it) when the form column leaves room for the chip
 * there, so it covers neither the question above, the value just inserted nor the next field; else below / above as `preferredSide`
 * decides. The visual layer measures the real chip and may still decide otherwise (the panel, the viewport edge).
 */
export function preferredPlacement(anchor: Rect, next: Rect[], viewportWidth: number): "below" | "above" | "beside" {
  if (anchor.w > 0 && viewportWidth - (anchor.x + anchor.w) >= CHIP_BESIDE_PX) return "beside";
  return preferredSide(anchor, next);
}

/** Is the list this control points to (aria-controls / aria-owns) a visible listbox or menu with options in it? */
function controlledListOpen(el: Element): boolean {
  const ids = `${el.getAttribute("aria-controls") ?? ""} ${el.getAttribute("aria-owns") ?? ""}`.split(/\s+/).filter(Boolean);
  if (!ids.length) return false;
  const root = el.getRootNode() as Document | ShadowRoot;
  for (const id of ids) {
    const box = (root as Document).getElementById?.(id) ?? document.getElementById(id);
    if (!box || !isRendered(box) || inUiHost(box)) continue;
    const role = box.getAttribute("role");
    if ((role === "listbox" || role === "menu" || role === "grid" || role === "tree") && box.querySelector('[role="option"],[role="menuitem"],[role="row"],[role="treeitem"]')) return true;
  }
  return false;
}

function fieldIsFocused(field: AssistField): boolean {
  const active = deepActiveElement();
  return !!active && (active === field.el || field.members.some((m) => m.el === active));
}
