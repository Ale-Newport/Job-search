// Extensible API records retain provider-specific provenance without losing known fields.
export type Data = Record<string, any>;
export interface Entity extends Data {
  id: string;
}
export interface Job extends Entity {
  title: string;
  company: string;
  location: string;
  url: string;
  application_url?: string;
  description?: string;
  source?: string;
  ats?: string;
  match_score?: number;
  priority_score?: number;
  status: string;
  skills?: string[];
  remote?: boolean;
  salary_min?: number;
  salary_max?: number;
  currency?: string;
  created_at?: string;
  /** Tracker rows only: the browser assistant has been used on this posting (GET /tracker). */
  assistant?: ExtensionAssist | null;
}
export interface Fact extends Entity {
  category: string;
  key: string;
  value: any;
  source?: string;
  verification_status: string;
  locked: boolean;
  notes?: string;
}
/** Who saved an answer: "extension" when it came from the browser assistant, null for everything else (Meridian, Auto Apply). */
export type AnswerOrigin = "extension" | null;
/** One entry of GET /applications/{id} `answers` or `answer_history`. */
export interface ApplicationAnswer extends Data {
  id: string;
  question: string;
  answer: string;
  origin?: AnswerOrigin;
}
/** One row of GET /application-profile `answers`: an answer remembered from an application (the Profile memory list). */
export interface ApplicationMemory extends Data {
  id: string;
  question: string;
  answer: string;
  action?: string;
  reusable?: boolean | number;
  superseded?: boolean;
  scope?: string | null;
  /** "extension" while the browser assistant is the last to have written this memory; an edit in Meridian clears it. */
  origin?: AnswerOrigin;
}
export interface Application extends Entity {
  job_id: string;
  status: string;
  title?: string;
  company?: string;
  job?: Job;
  notes?: string;
  mode?: string;
  /** Always present on GET/PATCH /applications/{id}; null until the browser assistant has been used on the posting. */
  assistant?: ExtensionAssist | null;
  events?: Data[];
  answers?: ApplicationAnswer[];
  answer_history?: ApplicationAnswer[];
  documents?: Data[];
  emails?: Data[];
  runs?: Data[];
  tasks?: Data[];
}
export interface AnswerLLMStatus extends Data {
  configured: boolean;
  provider: string;
  model: string;
  key_present: boolean;
  setup_required: boolean;
  message: string;
  endpoint?: string;
  local?: {
    available: boolean;
    models: Array<string | { id: string; label?: string }>;
    error?: string;
  };
  usage?: Data;
}
export interface AnswerDiagnostic extends Data {
  question?: string;
  exact_question?: string;
  question_id?: string;
  answer?: unknown;
  intent?: string;
  state?: string;
  status?: string;
  facts_used?: string[];
  requested_format?: string;
  word_count?: number;
  quality?: {
    passed?: boolean;
    scores?: Data;
    issues?: unknown[];
    review?: unknown;
  };
  provider?: string;
  model?: string;
  selected_roles?: string[];
  company_claims?: Array<{ text: string; source_urls: string[] }>;
}
export interface Document extends Entity {
  name?: string;
  filename?: string;
  kind?: string;
  created_at?: string;
  versions?: Data[];
  latest_version_id?: string;
}
export interface List<T> {
  items: T[];
  total: number;
}
export type Page =
  | "dashboard"
  | "discover"
  | "jobs"
  | "applications"
  | "review"
  | "networking"
  | "companies"
  | "profile"
  | "documents"
  | "email"
  | "analytics"
  | "automation"
  | "activity"
  | "settings";
export interface Selection {
  kind: "job" | "application" | "email" | "run" | "document";
  id: string;
}
export interface AppContext {
  refresh: number;
  /** Primitive copy of GET /extension .state, so the ctx identity stays stable between status polls. */
  assistant: ExtensionOverallState | "unknown";
  navigate: (page: Page, target?: string) => void;
  target?: string;
  select: (selection: Selection | null) => void;
  act: (
    label: string,
    action: () => Promise<any>,
    success?: string,
  ) => Promise<any>;
  busy: string | null;
  toast: (text: string, error?: boolean) => void;
}

// ---------------------------------------------------------------------------
// Browser assistant (Chrome / Safari extension) management API, docs/EXTENSION_DESIGN.md section 6.
// Paths are relative to api(): api("/extension") is GET {base_url}/api/extension (app token, never the bridge).
// No response below may carry a bridge token, a token hash or a pairing secret.
// ---------------------------------------------------------------------------
export type ExtensionBrowser = "chrome" | "safari";
export type ExtensionLanguage = "auto" | "en" | "es";
export type ExtensionSaveCorrections = "ask" | "always" | "never";
/** Server-computed headline, highest priority first: disabled > bridge_error > pairing > outdated > ready > detected > not_installed. */
export type ExtensionOverallState =
  | "disabled" // master switch off: the bridge refuses every extension request
  | "bridge_error" // the loopback bridge could not bind (port conflict) or crashed
  | "pairing" // an unexpired pairing request waits for approval
  | "outdated" // an approved client runs an older build than this Meridian
  | "ready" // at least one approved client and nothing needs attention
  | "detected" // an UNPAIRED extension contacted the bridge in the last ~15 min
  | "not_installed"; // nothing has contacted the bridge
/** Per-browser row state. A pending pairing is expressed by pending_pairing.browser. */
export type ExtensionBrowserState = "not_installed" | "detected" | "connected" | "outdated";

export interface ExtensionBridge {
  state: "listening" | "port_in_use" | "stopped" | "error";
  /** Port the bridge is bound to now, or the one that failed to bind. */
  port: number | null;
  /** Ordered ports the extension probes (47615-47624); shown so a conflict is actionable. */
  candidate_ports: number[];
  /** Best-effort NAME of the process holding the port (never a command line). */
  conflict_process?: string | null;
  message?: string | null;
}
export interface ExtensionBrowserInfo {
  /** The browser application exists on this Mac (/Applications/Google Chrome.app, Safari.app). */
  browser_installed: boolean;
  /** Chrome: stable unpacked folder (null until prepared). Safari: always null (the appex lives inside Meridian.app). */
  extension_path: string | null;
  /** Version shipped with THIS Meridian build. */
  bundled_version: string;
  /** false when this build of Meridian does not contain the extension for the browser (e.g. no Safari appex in a dev run). Absent means true. */
  bundled?: boolean;
  state: ExtensionBrowserState;
  /** Last time an UNPAIRED extension of this browser contacted the bridge (it is "detected" for 15 minutes after). */
  seen_unpaired_at: string | null;
  seen_version: string | null;
  /** Safari only: the build is not notarised, so Safari may need Develop > Allow Unsigned Extensions before it lists the
   *  extension (unverified on a development-signed build; Meridian cannot read Safari's state either way). */
  needs_unsigned_toggle?: boolean;
}
export interface ExtensionClient {
  id: string;
  browser: ExtensionBrowser;
  /** e.g. "Chrome on this Mac". Never contains a profile e-mail. */
  label: string;
  version: string;
  paired_at: string;
  /** null until the first authenticated request. MV3 workers sleep, so idle is NOT an error. */
  last_seen_at: string | null;
  last_error: string | null;
  outdated: boolean;
  stats: { pages: number; suggestions: number; insertions: number };
}
export interface ExtensionPairing {
  id: string;
  browser: ExtensionBrowser;
  /** Six digits, no separators ("482913"); displayed as "482 913". */
  code: string;
  extension_version: string;
  requested_at: string;
  expires_at: string;
  /** false when the requesting extension id is not one of Meridian's own (EXTENSION_DESIGN 4.1): shown as a red warning. Absent means recognised. */
  recognized?: boolean;
}
/** Mirror of backend/jobagent/extension/schemas.py ExtensionSettings and extension/src/shared/protocol.ts. */
export interface ExtensionSettings {
  enabled: boolean;
  language: ExtensionLanguage;
  /** Tab inserts the suggestion AND moves to the next field. */
  auto_advance: boolean;
  /** Tab skips fields that already contain a value. */
  skip_filled: boolean;
  /** Allow Tab (not only Enter or a click) to insert confirmed sensitive / legal answers. */
  tab_inserts_protected: boolean;
  save_corrections: ExtensionSaveCorrections;
  background_research: boolean;
  background_drafts: boolean;
  /** Last-resort public web search for the official company site (company name only). */
  web_lookup: boolean;
  /** Small hint pill on known application systems. */
  hint_on_ats: boolean;
  /** Per-application cap for background drafts; null means only the shared monthly cap applies. */
  draft_budget_usd: number | null;
  /** Hostnames where the assistant starts without the toolbar click. */
  always_on_sites: string[];
}
/** Cheap, computed from settings + ai_usage:YYYY-MM. No network probe. */
export interface ExtensionDrafts {
  configured: boolean;
  provider: string;
  model: string;
  message: string;
  /** "2026-10" */
  month: string;
  spent_usd: number;
  calls: number;
  monthly_limit_usd: number | null;
  unlimited: boolean;
}
export interface ExtensionStatus {
  state: ExtensionOverallState;
  server_time: string;
  app_version: string;
  bridge: ExtensionBridge;
  browsers: Record<ExtensionBrowser, ExtensionBrowserInfo>;
  clients: ExtensionClient[];
  pending_pairing: ExtensionPairing | null;
  settings: ExtensionSettings;
  drafts: ExtensionDrafts;
}
/** PATCH /extension/settings. The server returns the new ExtensionStatus. */
export type ExtensionPatch = Partial<ExtensionSettings>;
/** POST /extension/install/{chrome|safari}. install: the whole job in one call (prepare, copy_path, reveal, open_browser_page;
 *  `update: true` only prepares and opens the page); the others are its steps, for the buttons that repeat one of them.
 *  prepare: copy bundled files to the stable folder and return its path; reveal: `open -R`; open_browser_page: chrome://extensions
 *  in Chrome or launch Safari; copy_path: pbcopy. */
export type ExtensionInstallAction = "prepare" | "reveal" | "open_browser_page" | "copy_path" | "install";
export interface ExtensionInstallRequest {
  action: ExtensionInstallAction;
  /** Only with action "install": refresh the folder and open the browser's page, nothing else. */
  update?: boolean;
}
/** One step of an "install" answer, in the order it ran. A skipped step is not a failure (Chrome is not installed). */
export interface ExtensionInstallStep {
  action: Exclude<ExtensionInstallAction, "install">;
  ok: boolean;
  message: string;
  skipped?: boolean;
}
export interface ExtensionInstallResult {
  ok: boolean;
  /** Plain sentence the UI shows in a toast; for "install" the first failure when there was one. */
  message: string;
  /** Present for prepare / reveal / copy_path and for Chrome's install. */
  path?: string;
  /** Present for "install" only. */
  steps?: ExtensionInstallStep[];
}
/** POST /extension/jobs/{job_id}/open: primes research and opens the exact application URL in the paired browser. */
export interface ExtensionOpenResult {
  ok: boolean;
  browser?: ExtensionBrowser;
  message?: string;
}
/** Assisted-fill summary attached to GET /tracker rows and to GET /applications/{id} as `assistant` (null when unused). */
export interface ExtensionAssist {
  browser: ExtensionBrowser;
  fields_inserted: number;
  last_used_at: string | null;
}
/** GET /extension/activity?limit&offset. Answer text and typed values are never part of an item. */
export interface ExtensionActivityItem {
  id: string;
  created_at: string;
  message: string;
  origin: "assistant";
  kind: "paired" | "revoked" | "page_assisted" | "inserted" | "draft_ready" | "skipped_sensitive" | "blocked";
  browser: ExtensionBrowser;
  host: string | null;
  company: string | null;
  title: string | null;
  status?: string;
}
