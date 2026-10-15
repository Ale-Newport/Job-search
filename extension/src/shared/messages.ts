// Messages between content scripts and the background service worker, plus the shapes of extension-local state.
// Content scripts NEVER talk to the bridge (mixed content + CORS + token exposure): everything goes through here.

import type {
  DocumentContent, DocumentInfo, ExtensionSettings, FeedbackRequest, FeedbackResponse, PageReport, PollEvent,
  PollResponse, RegenerateRequest, SessionCreateResponse, StatusResponse, SuggestRequest, SuggestResponse,
  TrackRequest, TrackResponse, ResearchSnapshot, Suggestion,
} from "./protocol";

// ---- connection --------------------------------------------------------------------------------------------------
export type ConnectionPhase =
  | "ready" // bridge found, proof verified, token valid
  | "no_bridge" // nothing answers on any candidate port: Meridian is not running
  | "unpaired" // bridge found, no credential yet
  | "pairing" // pairing in progress: show the code
  | "revoked" // Meridian itself said so (a revoked-proof only the holder of the secret can check) or refused the credential twice, between two proofs
  | "unknown_listener" // additive: a credential is stored but the listener gave no proof either way (not even "revoked"): could be Meridian after a reinstall or an impostor; pairing needs an explicit confirmation
  | "proof_failed" // a listener answered but could not prove it is Meridian: NEVER send the token
  | "disabled" // the app has the assistant switched off
  | "restoring" // backup restore in progress
  | "error"
  | "connecting"; // additive (C2): the first connection check is still running (slow machine); not a failure, `conn:changed` follows

export interface ConnectionState {
  phase: ConnectionPhase;
  port?: number;
  appVersion?: string;
  latestExtensionVersion?: string;
  extensionOutdated?: boolean; // bundled app ships a newer extension than the one running (newer version, or the same version rebuilt: different digest)
  clientName?: string;
  pairing?: { pairingId: string; code: string; expiresAt: number };
  detail?: string;
  /** Additive (workstream C): suggested delay before the next automatic retry while the phase is a failure phase. */
  retryAfterMs?: number;
  /**
   * Additive (C3): every bridge port that answered the hello like Meridian while this browser holds no credential that could
   * tell them apart (phase `unpaired`, `revoked` or `pairing`). Two or more means pairing is refused: the first pairing is
   * unauthenticated by nature, so the extension will not choose between two programs that both say they are Meridian.
   */
  meridianPorts?: number[];
}

/** Everything stored in chrome.storage.local (extension-private; web pages cannot read it). */
export interface StoredConnection {
  port: number; clientId: string; token: string; clientName: string; pairedAt: string;
  /** Additive: only on the FALLBACK credential, when a newer pairing replaced it (ISO time); it is dropped a month later. */
  replacedAt?: string;
}
export interface StoredPrefs {
  language: "auto" | "en" | "es";
  /** hostnames where the assistant starts without a toolbar click (needs the matching optional host permission) */
  alwaysOn: string[];
  /** hostnames where the ATS hint pill is hidden */
  hintOff: string[];
  /** last known settings from the app, used while the bridge is unreachable */
  settings: ExtensionSettings | null;
  /** user dismissed the welcome page */
  welcomed: boolean;
}

// ---- content -> background requests ------------------------------------------------------------------------------
export interface CtxInfo {
  tabId: number; frameId: number; isTop: boolean; settings: ExtensionSettings | null; prefs: StoredPrefs; connection: ConnectionState; e2e: boolean;
  /**
   * Additive (C3): the submit this TAB is waiting to see confirmed (kept by the worker, so it survives a navigation to another
   * origin, where page sessionStorage does not). Null/absent when there is none or it is older than 10 minutes.
   */
  pending?: PendingSubmit | null;
}

/**
 * Additive (C3): what the content script remembers about "the user clicked Submit and I am waiting for the confirmation page".
 * The worker stores one per tab (storage.session, trusted contexts only), for at most 10 minutes, and drops it with the tab.
 */
export interface PendingSubmit {
  sid: string; // the bridge session of the page where Submit was clicked
  at: number; // epoch ms of the click
  label: string; // the text of the clicked control
  company: string;
  title: string;
  identity: string; // job identity of that page (so another application on the next page is recognised)
  hops: number; // documents navigated since the click
}
export interface PairStarted { pairingId: string; code: string; expiresAt: number }

export type BgRequest =
  | { t: "ctx" }
  | { t: "conn:state"; refresh?: boolean }
  | { t: "pair:start"; confirmUnknown?: boolean } // confirmUnknown: the user agreed to pair with a listener that could not be recognised
  | { t: "pair:poll"; pairingId: string }
  | { t: "pair:cancel"; pairingId: string }
  | { t: "status" }
  | { t: "session:open"; report: PageReport; tabKey: string }
  | { t: "session:release"; sessionId: string }
  | { t: "suggest"; sessionId: string; request: SuggestRequest }
  | { t: "poll"; sessionId: string; cursor: number; waitMs: number }
  | { t: "regenerate"; sessionId: string; request: RegenerateRequest }
  | { t: "feedback"; sessionId: string; request: FeedbackRequest }
  | { t: "track"; sessionId: string; request: TrackRequest }
  | { t: "research:site"; sessionId: string; domain: string }
  | { t: "research:company"; sessionId: string; company: string }
  | { t: "research:refresh"; sessionId: string }
  | { t: "documents" }
  | { t: "document:content"; id: string }
  | { t: "prefs:patch"; patch: Partial<StoredPrefs> }
  | { t: "settings:patch"; patch: Partial<ExtensionSettings> }
  | { t: "meridian:open" }
  | { t: "options:open"; anchor?: "permissions" | "connection" | "behaviour" | "help" } // "help" is additive (wave 3): the keys and colours section
  | { t: "permissions:granted" } // asks the worker to re-check optional host permissions
  | { t: "frame:relay"; to: "top" | "all" | { frameId: number }; payload: FramePayload }
  | { t: "badge"; text: string; tone?: "ok" | "warn" | "off" }
  | { t: "hint"; fields: number } // from the small ATS hint script
  | { t: "activate-here" } // hint pill clicked: activate this tab
  // ---- additive (workstream C) ----
  | { t: "activation"; active: boolean } // content -> worker: assist mode switched on/off in this tab (keeps toolbar toggle + badge exact)
  | { t: "frames:inject" } // content (top frame) -> worker: new frames appeared; inject content.js where it is missing
  | { t: "site:always-on"; enabled: boolean } // toggle "start automatically on this site" (the host is taken from the sender)
  | { t: "hint:hide" } // hint pill: hide the hint on this site (the host is taken from the sender)
  | { t: "extension:reload" } // options page only: apply a downloaded update
  | { t: "disconnect" } // options page only: forget this browser's credential locally
  | { t: "diagnostics" } // options page only: connection facts without any secret
  | { t: "hint:config" } // hint script: what it may show on this page (no network, no credential)
  | { t: "ui:get" } // content: the panel's remembered look (dock side, dismissed notes); content scripts cannot read storage
  | { t: "ui:set"; value: UiPrefs }
  // ---- additive (wave 2) ----
  | { t: "visibility"; visible: boolean } // content (top frame) -> worker: the page became hidden/visible (the worker polls only for visible pages)
  // ---- additive (C3) ----
  | { t: "pending:set"; record: PendingSubmit } // content (any frame of an active tab): remember the submit awaiting confirmation, per tab
  | { t: "pending:clear" } // content: that submit was recorded, dismissed or abandoned

export type BgResponseData<T extends BgRequest["t"]> =
  T extends "ctx" ? CtxInfo
  : T extends "conn:state" ? ConnectionState
  : T extends "pair:start" ? PairStarted
  : T extends "pair:poll" ? { state: "pending" | "approved" | "denied" | "expired" | "claimed"; connection: ConnectionState }
  : T extends "status" ? StatusResponse
  : T extends "session:open" ? SessionCreateResponse
  : T extends "suggest" ? SuggestResponse
  : T extends "poll" ? PollResponse
  : T extends "regenerate" ? Suggestion
  : T extends "feedback" ? FeedbackResponse
  : T extends "track" ? TrackResponse
  : T extends "research:site" | "research:company" | "research:refresh" ? ResearchSnapshot
  : T extends "documents" ? DocumentInfo[]
  : T extends "document:content" ? DocumentContent
  : T extends "settings:patch" ? ExtensionSettings
  : T extends "diagnostics" ? Diagnostics
  : T extends "disconnect" ? ConnectionState & { remoteRevoked?: boolean } // remoteRevoked (C2): Meridian also dropped this browser from its list
  : T extends "hint:config" ? HintConfig
  : T extends "ui:get" ? UiPrefs
  : T extends "ui:set" ? UiPrefs
  : null;

/** What the ATS hint script needs to decide whether to show its pill. Computed by the worker; no storage access from content. */
export interface HintConfig { show: boolean; language: "en" | "es"; connected: boolean }

/** The in-page panel's remembered look. Stored by the worker (content scripts have no access to extension storage). */
export interface UiPrefs { dock: "left" | "right"; dismissed: Record<string, number> }

/** Everything the options page may show about the connection. Never contains a token, a secret or page text. */
export interface Diagnostics {
  extensionVersion: string;
  browser: "chrome" | "safari";
  protocol: number;
  phase: ConnectionPhase;
  port?: number;
  candidatePorts: number[];
  /** additive (C3): the ports whose hello looked like Meridian's while no credential could tell them apart (two or more refuse pairing) */
  meridianPorts?: number[];
  appVersion?: string;
  latestExtensionVersion?: string;
  extensionOutdated: boolean;
  paired: boolean;
  clientName?: string;
  pairedAt?: string;
  checkedAt: string | null;
  lastError: { code: string; at: string } | null;
  activeTabs: number;
  permissions: { allSites: boolean; origins: string[] };
  e2e: boolean;
}

export interface BgError {
  /** bridge error codes plus transport conditions */
  code: string;
  detail: string;
  status?: number;
  connection?: ConnectionState;
}
export type BgResponse<T = unknown> = { ok: true; data: T } | { ok: false; error: BgError };

// ---- background -> content (chrome.tabs.sendMessage) -------------------------------------------------------------
export type ToContent =
  | { t: "activate"; auto?: boolean; toggle?: boolean } // toggle (wave 2): toolbar click on a live page - visible panel: switch off; otherwise show it
  | { t: "deactivate" }
  | { t: "conn:changed"; connection: ConnectionState }
  | { t: "frame"; payload: FramePayload; fromFrameId: number };

/** Frame-to-frame payloads relayed by the worker. Chips render in their own frame; the panel lives in the top frame. */
export type FramePayload =
  | { k: "session"; sessionId: string; knowledgeVersion: string }
  | { k: "fields"; frameKey: string; items: FieldDigest[] }
  | { k: "focus-first"; frameKey: string } // top -> child: focus the first assistable field
  | { k: "focus-last"; frameKey: string }
  | { k: "focus-after-frame"; frameKey: string } // child -> top: continue after the <iframe>
  | { k: "focus-before-frame"; frameKey: string }
  | { k: "focused"; frameKey: string; key: string } // a field in a child frame took focus
  | { k: "progress"; frameKey: string; currentKey: string | null } // child -> top: which field is current
  // ---- additive (D1): everything below is relayed verbatim by the worker, never interpreted ----
  | { k: "hello"; frameKey: string; url: string } // child -> top: "I am here"; top answers with session + settings
  | { k: "event"; event: PollEvent } // top -> child(ren): poll event owned by that frame, or a broadcast (knowledge)
  | { k: "settings"; settings: ExtensionSettings } // top -> children: settings changed
  | { k: "focus-field"; key: string } // top -> child: focus this field (panel item clicked)
  | { k: "fill-verified"; keys?: string[] } // top -> children: fill every eligible verified field (CTRL3/SEC-01: `keys` = only these, the ones the confirmation row listed)
  | { k: "deactivate" } // top -> children: the panel menu switched the assistant off
  | { k: "frame-hints"; hints: { src: string; next: boolean; prev: boolean; visible?: boolean }[] } // top -> children: can focus leave this frame? (`visible`, additive CTRL2/SEC-01: can a person see the <iframe>? false = nothing in the frame is offered or inserted)
  | { k: "confirmation"; evidence: string } // child -> top: confirmation text seen in a child frame
  // ---- additive (D1b, wave 2b) ----
  | { k: "handoff-ack"; frameKey: string; dir: 1 | -1; moved: boolean } // top -> child: the hand-off out of the frame was handled (moved = focus now sits in the parent)
  // ---- additive (CTRL2, wave 3) ----
  | { k: "duplicate"; blocked: boolean } // top -> children: the posting is already recorded as applied and unanswered (CPL-13): Tab inserts nothing
  | { k: "undo-all" } // top -> children: "Undo all" of Fill verified (UX-03)
  | { k: "end-of-form" } // child -> top: Tab found no further question after this frame's last one (FR-03)
  // ---- additive (CTRL3, wave 4) ----
  | { k: "focus-panel"; frameKey: string } // child -> top: Alt+Shift+M pressed inside a frame: the top frame moves the keyboard into its panel (UXR-07)
  | { k: "panel-state"; available: boolean } // top -> children: a panel the keyboard can enter is on screen (the child swallows Alt+Shift+M only then)
  | { k: "undo-all-request" }; // child -> top: Alt+Shift+Z pressed inside a frame while its "Fill verified" batch can be taken back (UXR-06)

export interface FieldDigest {
  key: string; // `${frameKey}:${nodeId}`
  label: string;
  section: string;
  status: "pending" | "inserted" | "edited" | "filled" | "needs_you" | "locked" | "skipped";
  tone?: "verified" | "inferred" | "draft";
  required: boolean;
  /** additive (D1): "Fill verified" would insert this field now */
  fillable?: boolean;
  /** additive (D1): a long answer is being drafted for this field */
  generating?: boolean;
  /** additive (CTRL3/SEC-01): for a `fillable` field, the value "Fill verified" would insert (display form, at most 200 characters): the confirmation row lists it */
  fillValue?: string;
}
