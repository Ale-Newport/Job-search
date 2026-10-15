// Wire contract of the Meridian bridge, protocol 1. Mirror of backend/jobagent/extension/schemas.py.
// Change both files together and update docs/EXTENSION_DESIGN.md.

export const PROTOCOL = 1;
export const BRIDGE_PORTS = [47615, 47616, 47617, 47618, 47619, 47620, 47621, 47622, 47623, 47624] as const;

export type Browser = "chrome" | "safari";
export type Language = "auto" | "en" | "es";
export type SaveCorrections = "ask" | "always" | "never";

export type SuggestionState =
  | "SKIP"
  | "NOT_APPLICABLE"
  | "SENSITIVE_BLANK"
  | "CONSENT"
  | "GENERATING"
  | "GENERATED_DRAFT"
  | "VERIFIED"
  | "INFERRED"
  | "NEEDS_YOU";
export type InsertTier = "tab" | "explicit" | "never";
export type FillOperation =
  | "TYPE_TEXT"
  | "SELECT_OPTION"
  | "CHECK_RADIO"
  | "SET_CHECKBOX"
  | "SET_CHECKBOXES"
  | "OPEN_AND_SELECT"
  | "ATTACH_FILE";
export type GenerationStatus = "QUEUED" | "GENERATING" | "REGENERATING" | "VALIDATING" | "DONE" | "FAILED" | "CANCELLED";
export type Outcome = "accepted" | "edited" | "rejected" | "skipped" | "manual";
export type Remember = "none" | "application" | "reuse";
export type ResearchState = "queued" | "running" | "needs_input" | "done" | "partial" | "failed" | "cancelled";
export type TrackedState = "none" | "prospective" | "preparing" | "applied";
export type ErrorCode =
  | "unauthorized" | "origin" | "host" | "restoring" | "invalid" | "too_large" | "rate_limited"
  | "not_found" | "expired" | "disabled" | "unavailable" | "conflict" | "internal";

export interface ErrorBody { detail: string; code: ErrorCode }

// ---- pairing / hello -------------------------------------------------------------------------------------------
export interface PairStartRequest { client_name: string; extension_version: string; locale: string }
export interface PairStartResponse { pairing_id: string; code: string; expires_in: number; poll_after_ms: number }
export interface PairStatusResponse {
  state: "pending" | "approved" | "denied" | "expired" | "claimed";
  token?: string | null;
  client_id?: string | null;
}
export interface HelloResponse {
  app: "Meridian";
  bridge: number;
  app_version: string;
  latest_extension_version: string;
  port: number;
  proof?: string | null;
  /**
   * Additive. `revoked` is true only with a `revoked_proof`: HMAC-SHA256(key = SHA-256(secret), "meridian-bridge-v1-revoked|" + nonce
   * + "|" + port), base64url. It is what a real Meridian says to a client it revoked; silence (no proof at all) never means revoked.
   */
  revoked?: boolean;
  revoked_proof?: string | null;
  /** Additive. SHA-256 tree digest of the extension bundled with the app ("" when none), to notice a rebuild with the same version. */
  latest_extension_digest?: string;
}

// ---- settings / status -------------------------------------------------------------------------------------------
export interface ExtensionSettings {
  enabled: boolean;
  language: Language;
  auto_advance: boolean;
  skip_filled: boolean;
  tab_inserts_protected: boolean;
  save_corrections: SaveCorrections;
  background_research: boolean;
  background_drafts: boolean;
  /** Laya and the local writing model may pick among offered options and write a known value as the field asks (all on this Mac) */
  local_assist: boolean;
  web_lookup: boolean;
  hint_on_ats: boolean;
  draft_budget_usd: number | null;
  always_on_sites: string[];
}
export interface GenerationAvailability {
  available: boolean;
  tested: boolean;
  provider?: string;
  local?: boolean;
  message?: string;
  monthly_limit_usd?: number | null;
  spent_usd?: number;
}
export interface StatusResponse {
  protocol: number;
  app_version: string;
  latest_extension_version: string;
  language: Language;
  /**
   * `has_cv` = an APPROVED CV exists. Additive (CTRL2/CPL-06): `unapproved_cvs` = imported CV versions that are still waiting for approval
   * (absent = the server does not say). Together they let the panel explain why a CV upload was left alone.
   */
  profile: { verified_facts: number; has_cv: boolean; display_name: string | null; unapproved_cvs?: number };
  generation: GenerationAvailability;
  settings: ExtensionSettings;
}

// ---- page / sessions ---------------------------------------------------------------------------------------------
export interface PageLink { href: string; text: string }
export interface PageReport {
  url: string;
  title: string;
  h1: string;
  lang: string;
  company_hint: string | null;
  meta: Record<string, string>;
  canonical: string | null;
  jsonld: string[];
  description_text: string;
  /** Where the page itself says the job is ("London, UK"). Optional; read only when structured data has no location. */
  location_hint?: string | null;
  links: PageLink[];
  user: Record<string, string | null>;
}
export interface SessionCreateRequest { page: PageReport; tab_key: string; locale: string }

export interface ResearchSource {
  label: string;
  url: string;
  kind: string;
  origin: string;
  status: "ok" | "failed" | "blocked" | "skipped" | "unverified";
  chars: number;
  retrieved_at: string | null;
  cached: boolean;
  error: string | null;
}
export interface ResearchStage {
  id: "identify" | "role" | "site" | "pages";
  label: string;
  status: "pending" | "running" | "done" | "skipped" | "failed";
  detail: string;
  ms: number | null;
}
/** Server-side `ResearchManager.snapshot()`; see docs/EXTENSION_DESIGN.md section 5.4. */
export interface ResearchSnapshot {
  run_id: string;
  state: ResearchState;
  version: number;
  ready_for_answers: boolean;
  quality: "full" | "job_only" | "company_only" | "minimal";
  stale: boolean;
  headline: string;
  needs: null | { field: "company" | "site"; suggestions: string[] };
  company: { name: string; legal_name: string | null; key: string; company_id: string | null; name_source: string; confidence: number } | null;
  site: { url: string; domain: string; origin: string; verified: boolean; evidence: string } | null;
  role: {
    title: string; job_id: string | null; posting_url: string; location: string; employment_type: string | null;
    /** The country the job is in as English name ("United Kingdom"), "" when unknown or several. Always read independently of the description text. */
    country?: string;
    countries?: string[];
    remote?: boolean;
    department: string | null; skills: string[]; description_chars: number;
    description_state: "JSONLD" | "PAGE" | "DB" | "ATS_API" | "FETCHED_POSTING" | "NOT_FOUND";
    sections: Record<string, string>;
  } | null;
  brief: string;
  brief_source: string | null;
  stages: ResearchStage[];
  sources: ResearchSource[];
  notes: string[];
  updated_at: string;
}
export interface JobSummary {
  company: string;
  title: string;
  location: string;
  job_id: string | null;
  application_id: string | null;
  tracked: TrackedState;
  /** Additive (CTRL2/CPL-13): when the application was recorded as sent (ISO date or timestamp), for the duplicate banner. Absent = unknown. */
  tracked_at?: string | null;
  /**
   * Additive (ASSIST3/UXR-08, mirrored by CTRL3): the application's own status (`PREPARING`, `APPLIED`, `INTERVIEW`, `REJECTED`, `WITHDRAWN`,
   * `GHOSTED` ...) when an application exists, so the duplicate banner says what really happened to it. `since`: when it was SENT (ISO date or
   * timestamp), only for a sent application; `tracked_at` carries the same value.
   */
  status?: string | null;
  since?: string | null;
}
export interface SessionCreateResponse {
  session_id: string;
  protocol: number;
  knowledge_version: string;
  job: JobSummary;
  research: ResearchSnapshot | null;
  generation: GenerationAvailability;
  settings: ExtensionSettings;
  cursor: number;
}

// ---- elements / suggestions --------------------------------------------------------------------------------------
export interface OptionIn { label: string; value: string; disabled?: boolean }
/** Pruned element of automation/observation.js. Text controls never carry what the user typed (has_value only). */
export interface ElementIn {
  node_id: number;
  role: string;
  tag: string;
  type: string;
  explicit_type?: string | null;
  label: string;
  context: string;
  description: string;
  placeholder: string;
  section_id: string;
  section_label: string;
  form_id: string | number;
  group: string;
  field_path: string;
  name: string;
  id: string;
  autocomplete: string;
  required: boolean;
  enabled: boolean;
  readonly: boolean;
  multiple: boolean;
  checked: boolean;
  expanded: boolean;
  has_value: boolean;
  value: string;
  selected_text: string;
  selected_values: string[];
  max_length: number | null;
  min_length: number | null;
  pattern: string;
  min: string | null;
  max: string | null;
  step: string | null;
  accept: string;
  operations: string[];
  options: OptionIn[];
  phone_country_code: string | null;
  phone_national_only: boolean;
  auxiliary_control: boolean;
  manual_key: string;
  /**
   * The question as the person sees it (the visible label's text) when the control's accessible name (`label`) came from
   * somewhere else (aria-label, a hidden span). The server answers what the person reads; two names that disagree are never
   * offered as verified. Optional: absent or equal to `label` means they are the same.
   */
  visible_label?: string;
  /**
   * Index of the control in document order among the controls of the scan this request comes from (comparable within ONE
   * request). A partial request (a conditional follow-up and its parent) lists controls in any order; this tells the server
   * which question is "above" which. Optional: without it the observer's node numbering is used.
   */
  position?: number | null;
}
export interface SuggestRequest {
  document_id: string;
  frame_url: string;
  frame_key: string;
  elements: ElementIn[];
  focus_node_id?: number | null;
  mode: "scan" | "refresh" | "focus";
  challenge: boolean;
  mfa: boolean;
  login: boolean;
}
export interface SuggestionValue {
  display: string;
  operation: FillOperation;
  text?: string | null;
  form_value?: unknown;
  option?: { label: string; value: string } | null;
  labels?: string[] | null;
  label?: string | null;
  search_text?: string | null;
  checked?: boolean | null;
  /** `name` (additive, ASSIST2/SAFE-09): what the document is CALLED in Meridian (a CV written for one job says which): show it, not just the filename. */
  document?: {
    id: string; filename: string; mime: string; size: number; sha256: string; name?: string;
    /**
     * Additive (ASSIST3/CPL-20): the OTHER documents of the same kind this page may be offered and this field would take (at most 6, the
     * default excluded, best first). A click-only "Use another CV" menu swaps one in (its content is `GET /documents/{id}/content`);
     * Tab always inserts the default. Absent or empty = no choice to offer.
     */
    alternatives?: { id: string; filename: string; version: number }[];
  } | null;
}
export interface WhyFact { id: string; concept: string; entity: string; verified: boolean }
export interface Why {
  /** Stable key the UI localises; `summary` is the English fallback. Besides the design's vocabulary the server
   * also sends `skip` (state SKIP; `rules[0]` is the reason: password, hidden, file, readonly, disabled, challenge,
   * mfa, login). Unknown codes must fall back to `summary`. */
  code: string;
  summary: string;
  intent?: string | null;
  rules?: string[];
  facts?: WhyFact[];
  sensitivity?: string | null;
  sources?: { label: string; url: string }[];
}
export interface NeedAction { type: "profile" | "answer_here" | "draft_with_ai" | "open_meridian"; key?: string }
export interface Needs {
  root_cause: string;
  code: string;
  message: string;
  actions: NeedAction[];
  candidate_value?: string | null;
  missing?: string[];
}
export interface GenerationInfo {
  id: string;
  status: GenerationStatus;
  attempt?: number;
  /** "local": a model on this Mac is finishing the control (picking an option, writing a known value as asked), not a draft */
  kind?: "local";
  cache_hit?: boolean;
  word_count?: number;
  provider?: string;
  local?: boolean;
  cost?: number;
  requires_review?: boolean;
  quality_passed?: boolean;
  company_claims?: { text: string; source_urls: string[] }[];
  claims?: { text: string; fact_ids: string[] }[];
}
export interface Suggestion {
  node_id: number;
  frame_key: string;
  question: string;
  question_id: string;
  request_id: string;
  knowledge_version: string;
  state: SuggestionState;
  insert: InsertTier;
  suggestion: SuggestionValue | null;
  confidence: { value: number; level: string } | null;
  why: Why | null;
  flags: {
    protected?: boolean; legal?: boolean; leave_blank?: boolean; requires_review?: boolean;
    /** `{applies: boolean | null, parent?}` for a conditional question (null = waiting for the parent's answer). */
    conditional?: unknown;
    /** The question may be remembered for future applications (Profile-mappable factual answer): offer "Reuse". */
    reusable?: boolean;
    /** node_ids of every control of a radio/checkbox group; the suggestion's `node_id` is the first one. */
    members?: number[];
    /** Additive: a model on this Mac read the option into the profile (no fact says it): amber, never taken by Tab, Enter or a click takes it. */
    read_in?: boolean;
    /** Additive (ASSIST2/SAFE-14): `otp` | `card` | `bank` | `id` | `security_answer` | `password` | `hidden` | `file`: whatever is typed here is
     *  NEVER saved (the server refuses it with `code: never_saved_<kind>`): offer no "save this answer?" prompt, whatever the save mode. */
    never_saved?: string;
  };
  needs: Needs | null;
  generation: GenerationInfo | null;
  /**
   * `knowledge_version` (additive, CTRL2/CPL-09): the Profile/knowledge version the draft was WRITTEN under. A draft whose version differs
   * from the page's current one is marked "profile changed" and is not inserted by Tab (an explicit rewrite is offered). Absent = unknown:
   * the client never guesses staleness from a missing value.
   */
  based_on: {
    job_description: boolean; company_pages: number; research_state: string; knowledge_version?: string;
    /** Additive (ASSIST2/CPL-09): the research bundle (hash of the pages read) the draft was written from. */
    research_bundle?: string;
    /** Additive (ASSIST2/SAFE-11, CPL-09): the draft no longer matches what it was written from. `research_changed`: research learned
     *  more since (still offered: say "based on older research" and offer a rewrite); `facts_changed` / `company_rejected` come with
     *  `state: NEEDS_YOU` and `needs.code: "draft_outdated"` (not offered; "write a new draft" is the action). */
    stale?: boolean;
    reason?: "research_changed" | "facts_changed" | "company_rejected" | null;
  } | null;
}
export interface SuggestResponse {
  knowledge_version: string;
  results: Suggestion[];
  skipped: { node_id: number; reason: string }[];
  /** The session's event cursor when the request STARTED: every event emitted while it ran is numbered after it and is
   *  delivered by a poll from it (the same state may arrive twice, never zero times). The results are never older than an
   *  event emitted before they were sent; a suggestion's `generation.id` is its draft's identity and `generation.status`
   *  only moves forward, so a result must never replace a finished draft (DONE/FAILED) of the same `generation.id` with an
   *  earlier status (the poll's events and this response are two requests and may reach the page in either order). */
  cursor: number;
}
export interface PollRequest { cursor: number; wait_ms: number }
export type PollEvent =
  | { type: "suggestion"; suggestion: Suggestion }
  | { type: "research"; research: ResearchSnapshot }
  | { type: "knowledge"; knowledge_version: string }
  | { type: "generation_budget"; message: string; code: string }
  /**
   * Additive (ASSIST2/SEC-06): this page used up the drafts Meridian writes ON ITS OWN (`code: "AUTO_DRAFT_LIMIT"`). Unlike
   * `generation_budget` it does NOT make writing unavailable: a draft the user asks for ("Draft with AI") still works.
   */
  | { type: "drafts_paused"; message: string; code: string }
  | { type: "tracked"; tracked: TrackedState; status?: string | null; since?: string | null }; // `status` / `since`: additive (ASSIST3/UXR-08), as in JobSummary
export interface PollResponse { cursor: number; events: PollEvent[] }

// ---- ONE poll for every page of a browser: POST /v1/poll -------------------------------------------------------------
// A browser allows only ~6 connections per host, so a long-poll per page starves the pages opened last. The worker holds ONE
// poll for all of its pages instead. At most 16 pages (each once; 17 is refused with 422), wait_ms 0..25000. The server
// answers as soon as ANY listed page has events, or empty after wait_ms. A newer poll of a page ends an older one that
// listed it, EXCEPT a quick check (wait_ms <= 250): it answers at once and never takes a page over.
export interface PollTarget { session_id: string; cursor: number }
export interface PollManyRequest { sessions: PollTarget[]; wait_ms: number }
/**
 * Per page, in request order:
 *  - ok: `events` after `cursor` (a client behind dropped events, or ahead of a rebuilt page, gets a `knowledge` event: re-scan);
 *        `cursor` is the one to ask with next
 *  - expired: this browser's page whose server state is gone (Meridian restarted): open it again with POST /sessions
 *  - unknown: not a page of this browser (a stranger's page looks exactly like one that never existed)
 *  - released: the page was closed or navigated away: stop polling it
 *  - superseded: a newer poll of this browser took the page over (cursor is the one it was asked with)
 */
export type PollStatus = "ok" | "expired" | "unknown" | "released" | "superseded";
export interface PollManyEntry { session_id: string; status: PollStatus; cursor: number; events: PollEvent[] }
export interface PollManyResponse { sessions: PollManyEntry[] }
export interface RegenerateRequest {
  question_id: string;
  node_id?: number | null;
  frame_key: string;
  instruction: "again" | "shorter" | "longer" | "different" | string;
  element?: ElementIn | null;
  current_text?: string | null;
}

/** Body of POST /v1/sessions/{sid}/research/company: "this is not the company, it is <company>". */
export interface ResearchCompanyRequest { company: string }
/** Body of POST /v1/sessions/{sid}/research/site: pin the official website (a bare domain or a URL). */
export interface ResearchSiteRequest { domain: string }

// ---- feedback / tracking / documents -----------------------------------------------------------------------------
export interface FeedbackRequest {
  question_id: string;
  frame_key: string;
  outcome: Outcome;
  suggestion_state?: SuggestionState | null;
  request_id?: string | null;
  final_value?: string | null;
  final_values?: string[] | null;
  remember: Remember;
  element?: ElementIn | null;
}
export interface FeedbackResponse {
  saved: boolean;
  application_id?: string | null;
  scope?: string | null;
  promoted_to_profile: boolean;
  message?: string | null;
  /**
   * Additive (ASSIST2/SAFE-12/SAFE-14): present only on a refusal with a precise reason: `never_saved_{password,hidden,file,otp,card,
   * bank,id,security_answer}` (a secret or identifier is never saved, whatever is sent), `other_party_label` (another person's details
   * under a bare label), `ambiguous_label` (two questions of the page share the label) or `invalid_value` (not what the Profile field holds).
   */
  code?: string | null;
}
export interface TrackRequest {
  /**
   * `save` (additive, CTRL2/CPL-10, served since ASSIST3/UXR-01): "Save to tracker" from the panel menu: keep the posting in Meridian's
   * tracker WITHOUT saying it was sent (creates the job and a PREPARING application, never records a submission). The answer is
   * `state: "saved"` with `tracked: "preparing"`; for an application that was ALREADY sent nothing changes and the answer is
   * `state: "already_recorded"` with `tracked: "applied"` (say so; do not say "saved").
   */
  event: "submit_clicked" | "confirmation_seen" | "record" | "save";
  evidence: string;
  applied_at?: string | null;
  notes: string;
}
export interface TrackResponse {
  state: "noted" | "recorded" | "already_recorded" | "saved";
  job_id?: string | null;
  application_id?: string | null;
  tracked: TrackedState;
}
export interface DocumentInfo {
  id: string; name: string; filename: string; mime: string; size: number; kind: string; version: number; default: boolean;
  /** Additive (ASSIST2/SAFE-09): `base` = one of the user's own CVs; `job` = the CV Meridian wrote for ONE posting (listed only with `?session_id=` of that page). */
  scope?: "base" | "job";
}
export interface DocumentContent {
  id: string; filename: string; mime: string; size: number; sha256: string; data_b64: string;
}
