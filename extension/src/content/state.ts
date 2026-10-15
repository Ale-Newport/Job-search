// Content-side state: the assist-field model, suggestion cache and the PURE eligibility rules of design 7.4.
// No DOM access here: the Tab handler decides synchronously from this cache, and every rule is unit-testable.

import type { ElementIn, ExtensionSettings, FillOperation, InsertTier, Suggestion, SuggestionState, SuggestionValue } from "../shared/protocol";
import type { FieldDigest } from "../shared/messages";
import type { DuplicateStatus, Tone } from "./ui-api";
import { normalizeText } from "./util";

/** A Tab-inserted GENERATED_DRAFT must have been on screen at least this long (design 7.4). */
export const DRAFT_DWELL_MS = 500;
/** How long an insertion can be undone. */
export const UNDO_WINDOW_MS = 8000;

export const DEFAULT_SETTINGS: ExtensionSettings = {
  enabled: true,
  language: "auto",
  auto_advance: true,
  skip_filled: true,
  tab_inserts_protected: false,
  save_corrections: "ask",
  background_research: true,
  background_drafts: true,
  web_lookup: true,
  hint_on_ats: true,
  draft_budget_usd: null,
  always_on_sites: [],
};

// ---- the assist-field model ---------------------------------------------------------------------------------------------

export type FieldKind = "text" | "textarea" | "editable" | "select" | "combobox" | "radio" | "checkbox" | "checkboxes" | "file";

export interface GroupMember {
  nodeId: number;
  el: HTMLElement;
  /** visible element to click when the control itself is visually hidden (label proxy) */
  proxy: HTMLElement | null;
  label: string;
  value: string;
  checked: boolean;
  enabled: boolean;
}

export interface AssistField {
  /** `${frameKey}:${nodeId}` of the primary control */
  key: string;
  frameKey: string;
  nodeId: number;
  kind: FieldKind;
  /** primary control: the control itself, or the first member of a radio/checkbox group */
  el: HTMLElement;
  /** what receives focus when the assistant moves here */
  focusEl: HTMLElement;
  /** visible element the chip is anchored to */
  anchorEl: HTMLElement;
  /** radio / checkbox group members in DOM order (empty for every other kind) */
  members: GroupMember[];
  /** every observer node id that belongs to this field (members, proxies, triggers) */
  aliasIds: number[];
  /** pruned element exactly as sent to the bridge (never typed values) */
  element: ElementIn;
  /** hash of label/type/options/required/...: NOT of the value */
  digest: string;
  order: number;
  label: string;
  section: string;
  required: boolean;
  /** related elements fill needs (menu trigger, search input, clear button, visible proxy of a hidden control) */
  links: { trigger: HTMLElement | null; search: HTMLElement | null; proxy: HTMLElement | null; clear: HTMLElement | null; listboxIds: string[] };
}

export function isTextKind(kind: FieldKind): boolean {
  return kind === "text" || kind === "textarea" || kind === "editable";
}

export function isGroupKind(kind: FieldKind): boolean {
  return kind === "radio" || kind === "checkboxes";
}

/** Operations that make sense for a control kind. Anything else is refused (fail closed). */
export function operationFits(kind: FieldKind, operation: FillOperation, multipleSelect: boolean): boolean {
  switch (operation) {
    case "TYPE_TEXT": return isTextKind(kind);
    case "SELECT_OPTION": return kind === "select";
    case "CHECK_RADIO": return kind === "radio";
    case "SET_CHECKBOX": return kind === "checkbox";
    case "SET_CHECKBOXES": return kind === "checkboxes" || (kind === "select" && multipleSelect);
    case "OPEN_AND_SELECT": return kind === "combobox";
    case "ATTACH_FILE": return kind === "file";
    default: return false;
  }
}

// ---- consent guard ---------------------------------------------------------------------------------------------------------

const CONSENT_PATTERN = new RegExp(
  "\\b(?:i (?:agree|accept|consent|acknowledge|authori[sz]e|have read|understand and)|agree to|accept(?:s|ance)? (?:of )?(?:the )?(?:terms|privacy|policy)|" +
  "terms (?:and|&) conditions|terms of (?:use|service)|privacy (?:policy|notice|statement)|data (?:processing|protection)|gdpr|consent to|" +
  "consiento|acepto|he le[ií]do|t[eé]rminos|pol[ií]tica de privacidad|protecci[oó]n de datos|autorizo|doy mi consentimiento)\\b",
  "i",
);

/** Belt and braces: a consent-looking control is never filled by plain Tab, whatever the server said. */
export function looksLikeConsent(field: Pick<AssistField, "label" | "element">): boolean {
  const text = `${field.label} ${field.element.context ?? ""}`;
  return CONSENT_PATTERN.test(text);
}

// ---- per-field runtime state ------------------------------------------------------------------------------------------------

export interface SuggestionEntry {
  suggestion: Suggestion;
  /** element digest the suggestion was computed for */
  digest: string;
  knowledgeVersion: string;
  receivedAt: number;
  /** first moment a chip showing THIS request was on screen (drives the draft dwell rule) */
  shownAt: number | null;
}

export interface InsertRecord {
  /** what was inserted, as plain text (comparison form is derived when needed) */
  text: string;
  labels: string[];
  state: SuggestionState;
  tone: "verified" | "inferred" | "draft";
  requestId: string;
  questionId: string;
  at: number;
  via: "tab" | "enter" | "click" | "bulk" | "replace";
}

export interface UndoRecord {
  key: string;
  at: number;
  undo: () => boolean;
}

export interface FieldState {
  entry: SuggestionEntry | null;
  /** digest of the last request sent for this field (avoids duplicate in-flight requests) */
  requestedDigest: string | null;
  requestedAt: number;
  dismissed: boolean;
  /** Tab must not insert until the field is focused again (the user just undid an insertion) while the chip stays visible */
  held: boolean;
  inserted: InsertRecord | null;
  failed: string | null;
  /**
   * The suggestion (request id) and element digest the failure belongs to. A failure is about THAT answer for THAT control: when the
   * page's options arrive late and a fresh suggestion comes in, or the control changes, the field is fillable again (`activeFailure`).
   */
  failedFor: { requestId: string; digest: string } | null;
  /** the user typed / changed the field after we looked at it */
  userEdited: boolean;
  feedbackSent: boolean;
  /** the bridge answered "skipped" for this element */
  skippedReason: string | null;
  /** persistent marker (INFERRED / drafts) until the user touches the field */
  marked: boolean;
  /**
   * What the draft shown for this field was written from (CPL-09). It outlives the suggestion cache (a knowledge change empties the
   * cache and the same draft comes back from the server) and goes with the page's drafts (`dropNarratives`).
   */
  draft: DraftMemo | null;
}

/** The research a draft was written under, and whether the person said its company was wrong. */
export interface DraftBasis {
  /** the research run the page knew when the draft first arrived ("" = none) */
  runId: string;
  /** the server's `based_on.research_state` ("none" when the draft was written without research) */
  state: string;
}

export interface DraftMemo {
  /** `generation.id` of the draft job ("" when the server sent none) and its text: a different draft starts a new memo */
  generationId: string;
  text: string;
  basis: DraftBasis;
  /** the person corrected the company this draft was researched for ("Not this company?") */
  companyRejected: boolean;
}

export function newFieldState(): FieldState {
  return {
    entry: null,
    requestedDigest: null,
    requestedAt: 0,
    dismissed: false,
    held: false,
    inserted: null,
    failed: null,
    failedFor: null,
    userEdited: false,
    feedbackSent: false,
    skippedReason: null,
    marked: false,
    draft: null,
  };
}

/** The failure that still applies to this entry (same suggestion, same control), else null. */
export function activeFailure(fs: FieldState | undefined, entry: SuggestionEntry | null): string | null {
  if (!fs?.failed || !entry || !fs.failedFor) return null;
  return fs.failedFor.requestId === entry.suggestion.request_id && fs.failedFor.digest === entry.digest ? fs.failed : null;
}

/** Remember that filling `entry` failed (the failure lives exactly as long as that suggestion and that control). */
export function markFailed(fs: FieldState, entry: SuggestionEntry, reason: string): void {
  fs.failed = reason;
  fs.failedFor = { requestId: entry.suggestion.request_id, digest: entry.digest };
}

export interface PageFlags { challenge: boolean; mfa: boolean; login: boolean }

/** The store the controller, keyboard and session share. Plain data + tiny helpers. */
export class Store {
  frameKey = "top";
  frameId = 0;
  isTop = true;
  settings: ExtensionSettings = { ...DEFAULT_SETTINGS };
  sessionId: string | null = null;
  knowledgeVersion = "";
  documentId = "";
  flags: PageFlags = { challenge: false, mfa: false, login: false };
  private fieldMap = new Map<string, AssistField>();
  private ordered: AssistField[] = [];
  private states = new Map<string, FieldState>();

  setFields(fields: AssistField[]): void {
    this.fieldMap = new Map(fields.map((f) => [f.key, f]));
    this.ordered = fields;
    for (const key of [...this.states.keys()]) if (!this.fieldMap.has(key)) this.states.delete(key);
  }

  list(): AssistField[] {
    return this.ordered;
  }

  get(key: string): AssistField | undefined {
    return this.fieldMap.get(key);
  }

  state(key: string): FieldState {
    let st = this.states.get(key);
    if (!st) {
      st = newFieldState();
      this.states.set(key, st);
    }
    return st;
  }

  peek(key: string): FieldState | undefined {
    return this.states.get(key);
  }

  /** A control was replaced by an equivalent element: the state of `from` becomes the state of `to` (no-op when `to` already has one). */
  moveState(from: string, to: string): void {
    const state = this.states.get(from);
    if (!state || this.states.has(to)) return;
    this.states.delete(from);
    this.states.set(to, state);
  }

  /** The cached suggestion, only while it is fresh for the field's digest and the session's knowledge. */
  entryFor(field: AssistField): SuggestionEntry | null {
    const entry = this.states.get(field.key)?.entry;
    if (!entry) return null;
    if (entry.digest !== field.digest) return null;
    if (this.knowledgeVersion && entry.knowledgeVersion && entry.knowledgeVersion !== this.knowledgeVersion) return null;
    return entry;
  }

  /** True when there is an entry but it is no longer fresh. */
  isStale(field: AssistField): boolean {
    const entry = this.states.get(field.key)?.entry;
    return !!entry && this.entryFor(field) === null;
  }

  /** Company-specific text (drafts) must not outlive the page it was written for. */
  dropNarratives(): void {
    for (const st of this.states.values()) {
      const state = st.entry?.suggestion.state;
      if (state === "GENERATED_DRAFT" || state === "GENERATING") {
        st.entry = null;
        st.requestedDigest = null;
      }
      st.draft = null;
    }
  }

  clearSuggestions(): void {
    for (const st of this.states.values()) {
      st.entry = null;
      st.requestedDigest = null;
    }
  }
}

// ---- state -> presentation -------------------------------------------------------------------------------------------------

export const INSERTABLE_STATES: ReadonlySet<SuggestionState> = new Set<SuggestionState>(["VERIFIED", "INFERRED", "GENERATED_DRAFT"]);

export function toneForState(state: SuggestionState): Tone {
  switch (state) {
    case "VERIFIED": return "verified";
    case "INFERRED": return "inferred";
    case "GENERATED_DRAFT": return "draft";
    case "GENERATING": return "busy";
    case "CONSENT":
    case "SENSITIVE_BLANK": return "locked";
    default: return "neutral";
  }
}

export function digestTone(state: SuggestionState | null | undefined): FieldDigest["tone"] | undefined {
  if (state === "VERIFIED") return "verified";
  if (state === "INFERRED") return "inferred";
  if (state === "GENERATED_DRAFT") return "draft";
  return undefined;
}

export function suggestedText(value: SuggestionValue | null | undefined): string {
  if (!value) return "";
  return String(value.text ?? value.display ?? "");
}

/** Labels a choice suggestion refers to (option, single label, or the label list). */
export function suggestedLabels(value: SuggestionValue | null | undefined): string[] {
  if (!value) return [];
  if (value.labels?.length) return value.labels.map(String);
  if (value.option?.label) return [value.option.label];
  if (value.label) return [value.label];
  return value.display ? [value.display] : [];
}

export function isProtectedSuggestion(s: Suggestion): boolean {
  return s.insert === "explicit" || !!s.flags?.protected || !!s.flags?.legal;
}

// ---- eligibility (the rules of design 7.4, synchronous and DOM-free) --------------------------------------------------------

export type BlockReason =
  | "no_suggestion" | "stale" | "state" | "generating" | "tier" | "dismissed" | "dwell" | "conflict" | "equal"
  | "operation" | "consent_guard" | "page_blocked" | "failed" | "no_value" | "disconnected" | "duplicate" | "stale_draft" | "frame_hidden";

export type Eligibility =
  | { ok: true; tier: InsertTier }
  | { ok: false; reason: BlockReason; dwellMs?: number };

export interface EligibilityInput {
  field: AssistField;
  entry: SuggestionEntry | null;
  stale: boolean;
  fs: FieldState | undefined;
  settings: ExtensionSettings;
  now: number;
  /** relation between what is on the page now and the suggestion */
  relation: "empty" | "equal" | "conflict";
  pageBlocked: boolean;
  connected: boolean;
  /** insertion path: "tab" is the strict one; "explicit" = Enter / click on the chip */
  via: "tab" | "explicit";
  /** a Replace click overwrites a conflicting value */
  replace?: boolean;
  /** CPL-13 / UXR-08: this posting is already recorded as applied and the person has not said "continue": nothing inserts (Tab, Enter, a click) */
  duplicateBlock?: boolean;
  /** CPL-09: the draft was written for a company or a Profile that is no longer the page's (nothing inserts it, Tab or click) */
  staleDraft?: boolean;
  /** SEC-01: this frame's <iframe> cannot be seen by a person (invisible, tiny, covered, off screen): nothing is inserted */
  frameHidden?: boolean;
}

export function evaluate(input: EligibilityInput): Eligibility {
  const { field, entry, fs, settings, now, via } = input;
  if (input.pageBlocked) return { ok: false, reason: "page_blocked" };
  if (input.frameHidden) return { ok: false, reason: "frame_hidden" };
  if (!field.el.isConnected) return { ok: false, reason: "disconnected" };
  if (!entry) return { ok: false, reason: input.stale ? "stale" : "no_suggestion" };
  const s = entry.suggestion;
  if (s.state === "GENERATING") return { ok: false, reason: "generating" };
  if (!INSERTABLE_STATES.has(s.state)) return { ok: false, reason: "state" };
  if (!s.suggestion) return { ok: false, reason: "no_value" };
  if (activeFailure(fs, entry)) return { ok: false, reason: "failed" };
  if (!operationFits(field.kind, s.suggestion.operation, !!field.element.multiple)) return { ok: false, reason: "operation" };
  if (s.insert === "never") return { ok: false, reason: "tier" };
  if (input.staleDraft) return { ok: false, reason: "stale_draft" };
  // CPL-13 / UXR-08: "I won't insert anything until you continue" is TRUE: Tab, Enter and a click on Insert / Replace all stop here
  if (input.duplicateBlock) return { ok: false, reason: "duplicate" };
  if (via === "tab") {
    if (s.insert === "explicit" && !settings.tab_inserts_protected) return { ok: false, reason: "tier" };
    if (s.insert === "tab" && looksLikeConsent(field)) return { ok: false, reason: "consent_guard" };
    if (fs?.dismissed || fs?.held) return { ok: false, reason: "dismissed" };
    if (s.state === "GENERATED_DRAFT") {
      const shown = entry.shownAt;
      const visibleFor = shown === null ? 0 : now - shown;
      if (shown === null || visibleFor < DRAFT_DWELL_MS) {
        return { ok: false, reason: "dwell", dwellMs: shown === null ? DRAFT_DWELL_MS : DRAFT_DWELL_MS - visibleFor };
      }
    }
  }
  if (input.relation === "equal") return { ok: false, reason: "equal" };
  if (input.relation === "conflict" && !input.replace) return { ok: false, reason: "conflict" };
  return { ok: true, tier: s.insert };
}

// ---- progress status --------------------------------------------------------------------------------------------------------

export interface StatusInput {
  state: SuggestionState | null;
  empty: boolean;
  inserted: InsertRecord | null;
  stillInserted: boolean;
  userEdited: boolean;
  failed: string | null;
  skippedReason: string | null;
}

export function deriveStatus(i: StatusInput): FieldDigest["status"] {
  if (i.failed) return "needs_you";
  if (i.inserted) return i.stillInserted && !i.userEdited ? "inserted" : "edited";
  if (!i.empty) return i.userEdited ? "edited" : "filled";
  if (i.skippedReason || i.state === "SKIP" || i.state === "NOT_APPLICABLE") return "skipped";
  if (i.state === "CONSENT" || i.state === "SENSITIVE_BLANK") return "locked";
  if (i.state === "NEEDS_YOU") return "needs_you";
  return "pending";
}

/**
 * `JobSummary.status` (the application's own status in Meridian's tracker: APPLIED, CONFIRMED, RECRUITER_SCREEN, INTERVIEW, OFFER, REJECTED,
 * WITHDRAWN, GHOSTED ...) reduced to the cases that change what the duplicate banner says. Anything unknown, or no status at all, is "applied".
 */
export function duplicateStatus(raw: string | null | undefined): DuplicateStatus {
  const status = String(raw ?? "").trim().toLowerCase();
  if (status.startsWith("reject")) return "rejected";
  if (status.startsWith("withdr")) return "withdrawn";
  if (status.startsWith("ghost")) return "ghosted";
  if (status.startsWith("offer")) return "offer";
  if (/interview|recruiter_screen|assessment|technical/.test(status)) return "interview";
  return "applied";
}

/** Normalised comparison used by value equality checks. */
export function sameText(a: string, b: string): boolean {
  return normalizeText(a) === normalizeText(b);
}
