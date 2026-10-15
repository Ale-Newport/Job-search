// Submit awareness and the learning loop.
//
// SubmitWatch: classifies the user's own click on a final-submit control (EN/ES wording, form-associated), tells the
// bridge ("noted" only), then looks for visible confirmation text in this or the next document and offers - never
// performs - "Record this application?". Nothing here clicks, submits or assumes a submission succeeded.
//
// The submit that awaits its confirmation page is held by the WORKER, per tab (`pending:set` / `pending:clear`, read back through
// `ctx.pending`): it survives a navigation to another origin (an ATS posting to the employer's own thank-you page), which page
// sessionStorage does not, and a page cannot forge it. Only a worker that does not know the feature (no `pending` in its `ctx`)
// makes the page fall back to sessionStorage, as before.
//
// LearningLoop: when the user leaves a field whose final value differs from what Meridian inserted (or suggested),
// offers to save the correction according to settings.save_corrections. The typed value is sent ONLY by an explicit
// save (or the "always" setting for non-sensitive answers); never for password / SKIP / consent fields. A value counts as the
// person's only when a gesture of their own (a trusted key, click, input or change) produced it: a page script that sets a value,
// raises input / change and blurs the field is not a correction (SEC-05). "Reuse in future applications" is offered only for a
// question the server marked `flags.reusable` (SAFE-12).

import type { ExtensionSettings, FeedbackRequest, FeedbackResponse, Outcome, Remember, Suggestion, TrackRequest, TrackResponse } from "../shared/protocol";
import type { PendingSubmit } from "../shared/messages";
import type { SaveAskView, TrackAskView, UIAction } from "./ui-api";
import type { AssistField, FieldState, InsertRecord } from "./state";
import { looksLikeConsent } from "./state";
import type { ValueReading } from "./fill";
import { matches } from "./fill";
import type { Failure } from "./session";
import type { RootRegistry } from "./roots";
import { after, clip, composedTarget, cut, every, guard, inUiHost, normalizeText, sendBg, squash, tidy } from "./util";

// ---- submit awareness ----------------------------------------------------------------------------------------------------

const STRONG_FINAL = new RegExp(
  "\\b(?:submit|send|enviar|presentar|finali[sz]ar)\\s+(?:my\\s+|your\\s+|the\\s+|mi\\s+|tu\\s+|la\\s+)?(?:job\\s+)?" +
  "(?:application|candidatura|solicitud|aplicaci[oó]n|postulaci[oó]n)\\b|\\bcomplete (?:my )?application\\b|\\bconfirm (?:and )?submit\\b|\\bconfirmar y enviar\\b",
  "i",
);
const WEAK_FINAL = /^(?:submit|send|apply|finish|confirm|enviar|solicitar|finalizar|confirmar|postular(?:me)?|aplicar)\b/i;
const NOT_FINAL = new RegExp(
  "\\b(?:next|continue|back|previous|save|cancel|upload|attach|add|remove|delete|search|sign ?in|log ?in|login|register|create (?:an )?account|" +
  "another|siguiente|continuar|atr[aá]s|volver|guardar|cancelar|subir|adjuntar|a[nñ]adir|eliminar|buscar|iniciar sesi[oó]n|registr)",
  "i",
);

const CONFIRMATION: RegExp[] = [
  /thank(?:s| you)(?: so much)? for (?:applying|your application|submitting|your interest|your submission)/i,
  /(?:your )?application (?:has been |was |is )(?:successfully )?(?:submitted|received|sent|completed?)/i,
  /we(?:'|’)?ve received your application|we have received your application/i,
  /application (?:received|submitted|complete)\b/i,
  /successfully (?:applied|submitted)/i,
  /gracias por (?:tu|su) (?:candidatura|solicitud|inter[eé]s|postulaci[oó]n)|gracias por (?:postularte|aplicar|enviar)/i,
  /hemos recibido (?:tu|su) (?:candidatura|solicitud|postulaci[oó]n)/i,
  /(?:tu|su) (?:candidatura|solicitud|postulaci[oó]n) (?:ha sido|fue) (?:enviada|recibida|registrada)/i,
  /candidatura (?:enviada|recibida)|solicitud (?:enviada|recibida)/i,
  // more of the ways an ATS says it (CPL-10); each one needs the word that says the thing is DONE, never just "submission" / "solicitud"
  /\bsubmission (?:complete|completed|received|successful)\b|your submission (?:has been|was) (?:received|submitted|sent)/i,
  /(?:tu|su) solicitud se ha (?:enviado|recibido|registrado)|hemos recibido (?:tu|su) (?:cv|curr[ií]culum|inscripci[oó]n)/i,
  /inscripci[oó]n (?:realizada|completada|enviada|recibida)/i,
];

const PENDING_KEY = "__meridian_assist_pending_submit";
/**
 * The submit this DOCUMENT has already counted as a hop (`sid:at`). A hop is a navigation: the assistant being switched on and off in
 * the same document is not one, so it must not eat into the four documents a record may travel.
 */
let hopCountedFor = "";
function recordKey(record: { sid: string; at: number }): string {
  return `${record.sid}:${record.at}`;
}
/** A submit that was never confirmed is forgotten after this long (tab storage outlives navigations, not the user's attention). */
export const PENDING_TTL_MS = 10 * 60 * 1000;
/** How many documents may pass between the submit click and the confirmation text (a redirect chain, then the thank-you page). */
const MAX_HOPS = 4;
/** How long one document keeps looking for the confirmation text (a slow ATS may take its time to render the thank-you view). */
const WATCH_MS = 60_000;

export function controlLabel(el: Element): string {
  const input = el as HTMLInputElement;
  const raw = (el.tagName === "INPUT" ? input.value : "") || el.getAttribute("aria-label") || (el as HTMLElement).innerText || el.textContent || el.getAttribute("title") || "";
  return tidy(raw).slice(0, 80);
}

/** Is this control the final "send my application" action? (strong wording anywhere; weak wording only in a form with our fields) */
export function classifySubmit(control: Element, formHasFields: boolean): boolean {
  const label = controlLabel(control);
  if (!label || NOT_FINAL.test(label)) return false;
  if (STRONG_FINAL.test(label)) return true;
  if (!WEAK_FINAL.test(label)) return false;
  const isSubmitType = control.tagName === "BUTTON" ? (control as HTMLButtonElement).type === "submit" : (control as HTMLInputElement).type === "submit";
  // a bare "Apply" link/role=button on a posting page is not the final submit: it must be a real form submit control
  return formHasFields && (isSubmitType || !!(control as HTMLButtonElement).form);
}

/** A short excerpt of visible page text that says the application went through, or null. Never form values. */
export function confirmationEvidence(root: HTMLElement | null = document.body): string | null {
  if (!root) return null;
  let text = "";
  try {
    text = (root.innerText || "").slice(0, 30_000);
  } catch {
    return null;
  }
  for (const pattern of CONFIRMATION) {
    const m = pattern.exec(text);
    if (!m) continue;
    const start = Math.max(0, m.index - 20);
    return cut(tidy(text.slice(start, m.index + m[0].length + 80)), 160);
  }
  return null;
}

interface Pending {
  sessionId: string;
  at: number;
  label: string;
  company: string;
  title: string;
  /** job identity (page-report.jobIdentity) of the page where Submit was clicked */
  identity: string;
  confirmed: boolean;
}

/**
 * A pending-submit record that is still worth keeping (young enough, not too many documents ago), or null. Records come from the worker
 * (`ctx.pending`) or, for a worker that does not hold them, from sessionStorage (page scripts of the same origin can read that one).
 */
export function validatePending(raw: unknown, now = Date.now()): PendingSubmit | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Partial<PendingSubmit>;
  const at = Number(data.at);
  if (typeof data.sid !== "string" || !data.sid || !Number.isFinite(at) || at <= 0 || now - at > PENDING_TTL_MS || at > now + 60_000) return null;
  const hops = Math.max(0, Math.trunc(Number(data.hops) || 0));
  if (hops > MAX_HOPS) return null;
  return {
    sid: data.sid, at, label: String(data.label ?? "").slice(0, 80), company: String(data.company ?? "").slice(0, 200),
    title: String(data.title ?? "").slice(0, 300), identity: String(data.identity ?? "").slice(0, 600), hops,
  };
}

export function readStoredPending(now = Date.now()): PendingSubmit | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const data = validatePending(JSON.parse(raw), now);
    if (!data) sessionStorage.removeItem(PENDING_KEY);
    return data;
  } catch {
    return null;
  }
}

export interface SubmitHost {
  active(): boolean;
  isTop: boolean;
  sessionId(): string | null;
  job(): { company: string; title: string } | null;
  /** assist fields exist in this frame (so a "weak" submit label counts) */
  hasFields(): boolean;
  formHasFields(form: HTMLFormElement | null): boolean;
  /** top frame shows the prompt; children relay it */
  showTrack(view: TrackAskView | null): void;
  relayConfirmation(evidence: string): void;
  /** where the submit click happened (job identity of this page), stored with the pending submit */
  identity(): string;
  /** is this document still the same application as `identity`? False when it is recognisably another job (a form or a posting of its own). */
  sameApplication(identity: string): boolean;
  /** `submit` events are not composed: forms inside open shadow roots are listened to through the registry */
  roots?: RootRegistry;
}

export class SubmitWatch {
  private pending: Pending | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastClick = 0;
  private installed = false;
  private recordedOrDismissed = false;
  private unbindRoots: (() => void) | null = null;
  /** the worker keeps the pending submit (its `ctx` answered with a `pending` field, null or not) */
  private workerHolds = false;

  private readonly onClick = guard((e: MouseEvent) => {
    if (!e.isTrusted || !this.host.active()) return;
    const t = composedTarget(e);
    if (!t || inUiHost(t)) return;
    const control = t.closest('button,input[type="submit"],input[type="button"],[role="button"]');
    if (!control) return;
    const form = (control as HTMLButtonElement).form ?? control.closest("form");
    if (classifySubmit(control, this.host.formHasFields(form) || (!form && this.host.hasFields()))) this.noteSubmit(controlLabel(control));
  });

  private readonly onSubmit = guard((e: Event) => {
    if (!e.isTrusted || !this.host.active()) return;
    const submitter = (e as SubmitEvent).submitter as Element | null;
    if (!submitter || inUiHost(submitter)) return;
    const target = composedTarget(e);
    const form = target instanceof HTMLFormElement ? target : null;
    if (classifySubmit(submitter, this.host.formHasFields(form))) this.noteSubmit(controlLabel(submitter));
  });

  constructor(private readonly host: SubmitHost) {}

  /** `pending`: what the worker's `ctx` said (a record, null for "none"); undefined when the worker does not know the feature. */
  install(pending?: PendingSubmit | null): void {
    if (this.installed) return;
    this.installed = true;
    this.workerHolds = pending !== undefined;
    document.addEventListener("click", this.onClick, true);
    document.addEventListener("submit", this.onSubmit, true);
    this.unbindRoots = this.host.roots?.bind((root) => {
      root.addEventListener("submit", this.onSubmit, true);
      return () => root.removeEventListener("submit", this.onSubmit, true);
    }) ?? null;
    this.resume(pending);
  }

  uninstall(): void {
    if (!this.installed) return;
    this.installed = false;
    document.removeEventListener("click", this.onClick, true);
    document.removeEventListener("submit", this.onSubmit, true);
    this.unbindRoots?.();
    this.unbindRoots = null;
    this.stopWatching();
  }

  /** True while a submit is awaiting its confirmation (the session must not be released on navigation). */
  isPending(): boolean {
    return !!this.pending && !this.recordedOrDismissed && Date.now() - this.pending.at < PENDING_TTL_MS;
  }

  private noteSubmit(label: string): void {
    const now = Date.now();
    if (now - this.lastClick < 2000) return; // the click and the form submit of one gesture
    this.lastClick = now;
    const sessionId = this.host.sessionId();
    if (!sessionId) return;
    const job = this.host.job();
    const identity = this.host.identity();
    this.pending = { sessionId, at: now, label, company: job?.company ?? "", title: job?.title ?? "", identity, confirmed: false };
    this.recordedOrDismissed = false;
    const record: PendingSubmit = { sid: sessionId, at: now, label, company: this.pending.company, title: this.pending.title, identity, hops: 0 };
    hopCountedFor = recordKey(record); // the click happened in THIS document: switching the assistant off and on here is not a hop
    this.remember(record);
    void sendBg<"track">({ t: "track", sessionId, request: { event: "submit_clicked", evidence: label, notes: "" } });
    this.startWatching(confirmationEvidence() !== null);
  }

  /** Keep the record for the next document: with the worker (any origin) or, for a worker that cannot, in this tab's sessionStorage. */
  private remember(record: PendingSubmit): void {
    if (this.workerHolds) {
      void sendBg<"pending:set">({ t: "pending:set", record });
      return;
    }
    try {
      sessionStorage.setItem(PENDING_KEY, JSON.stringify(record));
    } catch {
      /* storage can be blocked: confirmation is then only recognised in this document */
    }
  }

  private forget(): void {
    if (this.workerHolds) void sendBg<"pending:clear">({ t: "pending:clear" });
    try {
      sessionStorage.removeItem(PENDING_KEY);
    } catch {
      /* nothing to clean */
    }
  }

  /** The next document of the same tab: pick up a pending submit left by the previous page (never one that is stale). */
  private resume(fromWorker: PendingSubmit | null | undefined): void {
    if (!this.host.isTop) return;
    const raw = fromWorker !== undefined ? fromWorker : readStoredPending();
    const data = fromWorker !== undefined ? validatePending(raw) : (raw as PendingSubmit | null);
    if (!data) {
      if (fromWorker) this.forget(); // the worker still holds a record that is too old / too many documents ago
      return;
    }
    this.pending = {
      sessionId: data.sid, at: data.at, label: data.label, company: data.company, title: data.title, identity: data.identity, confirmed: false,
    };
    this.recordedOrDismissed = false;
    // a document counts once, however often the assistant is switched on in it
    const key = recordKey(data);
    const counted = hopCountedFor === key;
    hopCountedFor = key;
    this.remember({ ...data, hops: counted ? data.hops : data.hops + 1 });
    this.startWatching(false);
  }

  private startWatching(alreadyShowing: boolean): void {
    this.stopWatching();
    const started = Date.now();
    let baseline = alreadyShowing;
    this.timer = every(() => {
      if (!this.pending || this.pending.confirmed || Date.now() - started > WATCH_MS) {
        this.stopWatching();
        return;
      }
      if (Date.now() - this.pending.at > PENDING_TTL_MS) {
        this.finish();
        return;
      }
      if (document.visibilityState === "hidden") return; // nobody is looking, and reading the page text forces layout
      const evidence = confirmationEvidence();
      if (!evidence) {
        baseline = false;
        return;
      }
      if (baseline) return; // that text was already on the page when the click happened
      this.confirmed(evidence);
    }, 700);
  }

  private stopWatching(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private confirmed(evidence: string): void {
    const p = this.pending;
    if (!p || p.confirmed) return;
    // never offer to record job A on the page of job B: another application form / posting means the user moved on
    if (p.identity && !this.host.sameApplication(p.identity)) {
      this.finish();
      return;
    }
    if (Date.now() - p.at > PENDING_TTL_MS) {
      this.finish();
      return;
    }
    p.confirmed = true;
    this.stopWatching();
    void sendBg<"track">({ t: "track", sessionId: p.sessionId, request: { event: "confirmation_seen", evidence, notes: "" } });
    if (this.host.isTop) {
      const job = this.host.job();
      this.host.showTrack({ company: p.company || job?.company || location.hostname, title: p.title || job?.title || "", evidence });
    } else {
      this.host.relayConfirmation(evidence);
    }
  }

  /** A child frame saw the confirmation text (relayed to the top frame, which owns the prompt). */
  confirmedElsewhere(evidence: string): void {
    if (!this.pending) {
      const sessionId = this.host.sessionId();
      if (!sessionId) return;
      const job = this.host.job();
      this.pending = { sessionId, at: Date.now(), label: "", company: job?.company ?? "", title: job?.title ?? "", identity: "", confirmed: false };
    }
    this.confirmed(evidence);
  }

  /** The user asked to record the application (never automatic). */
  async record(evidence: string): Promise<TrackResponse | Failure> {
    const p = this.pending;
    const sessionId = p?.sessionId ?? this.host.sessionId();
    if (!sessionId) return { code: "no_session", detail: "No session." };
    const request: TrackRequest = { event: "record", evidence: cut(evidence, 400), applied_at: new Date().toISOString(), notes: "" };
    const res = await sendBg<"track">({ t: "track", sessionId, request });
    this.finish();
    return res.ok ? res.data : { code: res.error.code, detail: res.error.detail };
  }

  dismiss(): void {
    this.finish();
  }

  private finish(): void {
    const p = this.pending;
    // the session of an EARLIER document (the submit navigated away) has no owner any more: let the bridge forget it
    if (p && p.sessionId && p.sessionId !== this.host.sessionId() && this.host.isTop) {
      void sendBg<"session:release">({ t: "session:release", sessionId: p.sessionId }, 5000);
    }
    this.recordedOrDismissed = true;
    this.pending = null;
    this.stopWatching();
    this.forget();
    this.host.showTrack(null);
  }
}

// ---- learning loop -----------------------------------------------------------------------------------------------------------

export interface LearnHost {
  frameKey: string;
  settings(): ExtensionSettings;
  readField(field: AssistField): ValueReading;
  stateOf(field: AssistField): FieldState | undefined;
  /** the bridge session this page belongs to now (a question id only means something inside the session that issued it) */
  sessionId(): string | null;
  askSave(view: SaveAskView | null): void;
  feedback(request: FeedbackRequest): Promise<FeedbackResponse | Failure>;
  saved(response: FeedbackResponse, remember: Remember, label: string): void;
  isFailure(value: unknown): value is Failure;
}

/** What the loop last knew Meridian suggested for a field, and in which session (a question id belongs to its session). */
interface Known { suggestion: Suggestion; sessionId: string | null }
interface Visit {
  /** what the field held when the person arrived (the baseline when nothing was inserted) */
  initial: string;
  /** the last suggestion seen for the field during the visit: the cache may be cleared meanwhile (a knowledge change), this is not */
  known: Known | null;
  /**
   * SEC-05: what the field said right after the person's last TRUSTED gesture on it (a key, a click, an input / change event the browser
   * itself raised), and when. A value that no gesture of the person produced (a script set it) never counts as a correction.
   */
  userSig: string | null;
  userAt: number;
}
/** Everything the decision needs about a field that was left, captured at the moment it was left. */
interface Left {
  field: AssistField;
  reading: ValueReading;
  signature: string;
  initial: string;
  inserted: InsertRecord | null;
  sessionId: string | null;
  at: number;
  /** the visit's trusted-gesture evidence (see `Visit`) */
  userSig: string | null;
  userAt: number;
}
interface SaveItem {
  key: string;
  field: AssistField;
  view: SaveAskView;
  request: Omit<FeedbackRequest, "remember">;
  sensitive: boolean;
  sig: string;
}

function readingSignature(r: ValueReading): string {
  return r.empty ? "" : `${r.text}␟${r.labels.join("␞")}`;
}

/**
 * Masks, controlled inputs and custom comboboxes settle the value a moment after the gesture that changed it: the value the person left
 * behind is compared with what the field said when it had settled, and a field left sooner than this after a gesture gets the benefit of it.
 */
const SETTLE_MS = 450;
const MAX_HANDLED = 300;
const MAX_VISITS = 100;
/** A field left before Meridian had answered for it waits this long for the answer (then the correction is not offered). */
const MAX_WAIT_MS = 60_000;
const MAX_WAITING = 20;
const NARRATIVE_INTENT = /(?:COVER|MOTIVATION|WHY|NARRATIVE|STRENGTH|WEAKNESS|ACHIEVEMENT|CHALLENGE|DESCRIBE|EXPERIENCE_DESC|ABOUT_YOU|AI_USE)/i;

export class LearningLoop {
  private visits = new Map<string, Visit>();
  private handled = new Map<string, string>();
  private queue: SaveItem[] = [];
  private current: SaveItem | null = null;
  /** fields the person left before any suggestion was known for them: decided as soon as one arrives */
  private waiting = new Map<string, Left>();

  constructor(private readonly host: LearnHost) {}

  onFocusIn(field: AssistField): void {
    if (this.visits.has(field.key)) return;
    this.visits.set(field.key, { initial: readingSignature(this.host.readField(field)), known: this.knownNow(field), userSig: null, userAt: 0 });
    // a field the page removes while it has the focus may never report a focusout: the oldest visits go first
    while (this.visits.size > MAX_VISITS) this.visits.delete(this.visits.keys().next().value as string);
  }

  /**
   * A suggestion for `field` entered the cache. The loop keeps its own copy for the visit in progress, so a correction typed in the
   * window where Meridian's knowledge changed (the cache is cleared and asked for again) is still judged against the right question;
   * a field that was left before any answer existed is decided now.
   */
  noteSuggestion(field: AssistField, suggestion: Suggestion): void {
    const known: Known = { suggestion, sessionId: this.host.sessionId() };
    const visit = this.visits.get(field.key);
    if (visit) visit.known = known;
    const left = this.waiting.get(field.key);
    if (!left) return;
    this.waiting.delete(field.key);
    if (Date.now() - left.at > MAX_WAIT_MS || left.sessionId !== known.sessionId) return;
    this.decide(left, suggestion);
  }

  /**
   * SEC-05: a trusted gesture (key, click, input / change raised by the browser) happened on `field` during the visit in progress. A page
   * script can set a value, raise `input` / `change` and blur the field without the person doing anything; only what follows a gesture of the
   * person's own is ever offered for saving.
   */
  noteGesture(field: AssistField): void {
    const visit = this.visits.get(field.key);
    if (!visit) return;
    const at = Date.now();
    visit.userAt = at;
    visit.userSig = readingSignature(this.host.readField(field));
    after(() => {
      if (visit.userAt === at && this.visits.get(field.key) === visit) visit.userSig = readingSignature(this.host.readField(field));
    }, SETTLE_MS);
  }

  /** The value in `left` is the one the person's own gesture produced (or a gesture was so recent that the page may still be settling it). */
  private trusted(left: Left): boolean {
    if (left.userSig === null) return false;
    return left.signature === left.userSig || left.at - left.userAt < SETTLE_MS;
  }

  private knownNow(field: AssistField): Known | null {
    const entry = this.host.stateOf(field)?.entry;
    return entry ? { suggestion: entry.suggestion, sessionId: this.host.sessionId() } : null;
  }

  onFocusOut(field: AssistField): void {
    const visit = this.visits.get(field.key);
    this.visits.delete(field.key);
    if (!visit) return;
    const fs = this.host.stateOf(field);
    if (!fs || field.element.type === "password" || fs.skippedReason || field.kind === "file") return;
    // what the cache says now, else what the visit saw last (the cache is emptied whenever Meridian's knowledge changes)
    const live = fs.entry?.suggestion ?? null;
    const sessionId = this.host.sessionId();
    const retained = visit.known && visit.known.sessionId === sessionId ? visit.known.suggestion : null;
    const suggestion = live ?? retained;
    if (suggestion && (suggestion.state === "SKIP" || suggestion.state === "CONSENT")) return;
    const reading = this.host.readField(field);
    if (reading.empty) return;
    const left: Left = {
      field, reading, signature: readingSignature(reading), initial: visit.initial, inserted: fs.inserted, sessionId, at: Date.now(),
      userSig: visit.userSig, userAt: visit.userAt,
    };
    if (suggestion) {
      this.decide(left, suggestion);
      return;
    }
    // nothing is known for this question yet (the answer is still on its way): decide when it arrives, from what the person left
    this.waiting.delete(field.key);
    this.waiting.set(field.key, left);
    while (this.waiting.size > MAX_WAITING) this.waiting.delete(this.waiting.keys().next().value as string);
  }

  /** The decision proper: independent of the suggestion cache (the inserted value / the empty baseline are what the field is compared with). */
  private decide(left: Left, suggestion: Suggestion): void {
    const { field, reading, signature, inserted } = left;
    const fs = this.host.stateOf(field);
    if (!fs || fs.skippedReason || suggestion.state === "SKIP" || suggestion.state === "CONSENT" || !suggestion.question_id) return;
    const base: Omit<FeedbackRequest, "outcome" | "remember"> = {
      question_id: suggestion.question_id, frame_key: field.frameKey, suggestion_state: suggestion.state, request_id: suggestion.request_id,
    };
    if (inserted) {
      const unchanged = squash(reading.text) === squash(inserted.text) || (inserted.labels.length > 0 && sameLabels(reading.labels, inserted.labels))
        || (suggestion.suggestion ? matches(field, suggestion.suggestion, reading) : false);
      if (unchanged) {
        if (!fs.feedbackSent) {
          fs.feedbackSent = true;
          void this.host.feedback({ ...base, outcome: "accepted", remember: "none" });
        }
        return;
      }
      if (this.trusted(left)) this.consider(field, suggestion, base, reading, signature, "edited");
      return;
    }
    if (signature === left.initial) return; // the user did not change anything
    if (suggestion.suggestion && matches(field, suggestion.suggestion, reading)) {
      if (!fs.feedbackSent) {
        fs.feedbackSent = true;
        void this.host.feedback({ ...base, outcome: "accepted", remember: "none" });
      }
      return;
    }
    if (this.trusted(left)) this.consider(field, suggestion, base, reading, signature, "manual");
  }

  private consider(
    field: AssistField, suggestion: Suggestion, base: Omit<FeedbackRequest, "outcome" | "remember">, reading: ValueReading, sig: string,
    outcome: Outcome,
  ): void {
    if (field.kind === "checkbox" && (looksLikeConsent(field) || !reading.checked)) return;
    if (this.handled.get(field.key) === sig) return;
    const settings = this.host.settings();
    if (settings.save_corrections === "never") return;
    // the server says this answer is NEVER saved (a code, a card, an ID number, a password ...: `flags.never_saved`): no prompt, whatever the save mode
    if (suggestion.flags?.never_saved) return;
    const sensitive = !!(suggestion.flags?.protected || suggestion.flags?.legal || suggestion.flags?.leave_blank
      || suggestion.state === "SENSITIVE_BLANK" || suggestion.insert === "explicit");
    const multi = field.kind === "checkboxes" || (field.kind === "select" && !!(field.el as HTMLSelectElement).multiple);
    const narrative = field.kind === "textarea" || field.kind === "editable" || (field.element.max_length ?? 0) > 500
      || suggestion.state === "GENERATED_DRAFT" || suggestion.state === "GENERATING" || NARRATIVE_INTENT.test(suggestion.why?.intent ?? "");
    const text = field.kind === "checkbox" ? "yes" : reading.labels.length && field.kind !== "text" && field.kind !== "textarea" && field.kind !== "editable"
      ? reading.labels.join(", ") : reading.text;
    const request: Omit<FeedbackRequest, "remember"> = {
      ...base, outcome, element: field.element,
      final_value: multi ? null : cut(text.trim(), 20000),
      final_values: multi ? reading.labels.slice(0, 100) : null,
    };
    // "Reuse in future applications" writes the answer into the Profile: only for a question the server itself marked as the person's own
    // reusable fact (`flags.reusable`, false for somebody else's details such as an emergency contact); a missing flag means no (SAFE-12)
    const reusable = suggestion.flags?.reusable === true;
    const view: SaveAskView = {
      key: field.key, fieldLabel: field.label, valuePreview: clip(text.trim(), 120),
      allowReuse: !(sensitive || narrative) && reusable, edited: outcome === "edited",
    };
    const item: SaveItem = { key: field.key, field, view, request, sensitive, sig };
    if (settings.save_corrections === "always" && !sensitive) {
      this.setHandled(field.key, sig);
      void this.send(item, "application");
      return;
    }
    this.enqueue(item);
  }

  private enqueue(item: SaveItem): void {
    this.queue = this.queue.filter((q) => q.key !== item.key);
    if (this.current?.key === item.key) this.current = null;
    this.queue.push(item);
    if (this.queue.length > 5) this.queue.shift();
    this.pump();
  }

  private pump(): void {
    if (this.current) return;
    this.current = this.queue.shift() ?? null;
    this.host.askSave(this.current ? this.current.view : null);
  }

  /** Returns true when the action belonged to the save prompt. */
  handleAction(action: UIAction): boolean {
    if (action.type === "save-answer" || action.type === "save-never") {
      const item = this.current;
      if (!item || item.key !== action.key) return true;
      this.current = null;
      this.setHandled(item.key, item.sig);
      if (action.type === "save-answer") {
        const remember: Remember = action.remember === "reuse" && item.view.allowReuse ? "reuse" : "application";
        void this.send(item, remember);
      }
      this.pump();
      return true;
    }
    return false;
  }

  /** "Always save" / "Never ask" chosen in the prompt: resolve the item on screen accordingly. */
  settingChosen(value: "always" | "never"): void {
    const item = this.current;
    if (!item) return;
    this.current = null;
    this.setHandled(item.key, item.sig);
    if (value === "always") void this.send(item, "application");
    this.queue = value === "never" ? [] : this.queue;
    this.pump();
  }

  private async send(item: SaveItem, remember: Remember): Promise<void> {
    const res = await this.host.feedback({ ...item.request, remember });
    if (!this.host.isFailure(res)) {
      const fs = this.host.stateOf(item.field);
      if (fs) fs.feedbackSent = true;
      this.host.saved(res, remember, item.view.fieldLabel);
    }
  }

  /** The page re-rendered a control: what we know about the old element belongs to the new one. */
  rekey(from: string, to: string): void {
    const visit = this.visits.get(from);
    if (visit) {
      this.visits.delete(from);
      this.visits.set(to, visit);
    }
    const left = this.waiting.get(from);
    if (left) {
      this.waiting.delete(from);
      this.waiting.set(to, left);
    }
    const handled = this.handled.get(from);
    if (handled !== undefined) {
      this.handled.delete(from);
      this.setHandled(to, handled);
    }
  }

  /** Bounded: a long single-page session creates new element ids all the time. */
  private setHandled(key: string, sig: string): void {
    this.handled.delete(key);
    this.handled.set(key, sig);
    while (this.handled.size > MAX_HANDLED) this.handled.delete(this.handled.keys().next().value as string);
  }

  /** Counts of what is retained (E2E diagnostics). */
  sizes(): { visits: number; handled: number; queue: number; waiting: number } {
    return { visits: this.visits.size, handled: this.handled.size, queue: this.queue.length, waiting: this.waiting.size };
  }

  clear(): void {
    this.visits.clear();
    this.handled.clear();
    this.waiting.clear();
    this.queue = [];
    this.current = null;
    this.host.askSave(null);
  }
}

function sameLabels(a: string[], b: string[]): boolean {
  const x = new Set(a.map(normalizeText));
  const y = new Set(b.map(normalizeText));
  if (x.size !== y.size) return false;
  for (const v of x) if (!y.has(v)) return false;
  return true;
}
