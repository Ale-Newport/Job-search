// Interface between the assistant LOGIC (scan, keyboard, fill, session) and the VISUAL layer (panel + field chip).
// Logic passes semantic view models only; the UI layer owns every user-visible string (content/i18n.ts, en + es),
// styling, positioning and accessibility. Rendering lives in a CLOSED shadow root (open in __E2E__ builds).

import type { FillOperation, GenerationStatus, InsertTier, Language, ResearchSnapshot, SuggestionState, TrackedState } from "../shared/protocol";
import type { ConnectionState, FieldDigest } from "../shared/messages";

export type Tone = "verified" | "inferred" | "draft" | "neutral" | "locked" | "busy" | "error";

/** What is shown next to the focused field. Semantic: the UI turns codes into localized text. */
export interface ChipView {
  /** `${frameKey}:${nodeId}`; the UI re-renders in place when the key is unchanged. */
  key: string;
  fieldLabel: string;
  state: SuggestionState;
  insert: InsertTier;
  tone: Tone;
  operation: FillOperation | null;
  /** Full value to be inserted (display form). Long values are truncated by the UI with a "show more" affordance. */
  value: string;
  /** For choice controls: the option label being recommended. */
  optionLabel?: string;
  whyCode: string;
  whySummary: string; // English fallback
  whyDetail?: string; // e.g. "Based on your project X and Acme's mission" (already localized by logic only if from server)
  sources: { label: string; url: string }[];
  /** needs/blocked reason code from the server (needs.code) or 'protected'/'consent'/'policy'. */
  reasonCode?: string;
  reasonText?: string; // English fallback
  /** Can the user still act? */
  canRegenerate: boolean;
  canDraft: boolean; // "Draft with AI" for short fields on explicit request
  review: boolean; // amber "review before sending"
  /** Generation progress (state GENERATING) */
  generation?: { status: GenerationStatus; attempt?: number; wordCount?: number };
  /** The field already contains a different value: Tab will not overwrite it, offer an explicit Replace. */
  conflictsWithCurrent: boolean;
  /**
   * An inserted value that can be undone for a few seconds (8 s after a Tab / Enter / click insertion; 10 s after "Fill verified": its
   * `undo` action then takes back THAT field only, `undo-all` takes back the whole batch).
   */
  undoable: boolean;
  /** Tab inserts only after the draft has been visible long enough (see design 6.4). */
  dwellRemainingMs: number;
  /** keyboard hints to display; UI localizes the labels from `id`. */
  keys: ("tab" | "enter" | "esc" | "regenerate" | "shift-tab")[];
  /** Anchor geometry in THIS frame's viewport coordinates (every frame renders its own chip), kept fresh on scroll/resize. */
  anchor: { x: number; y: number; w: number; h: number };
  /**
   * Preferred placement relative to the anchor. UI flips automatically when there is no room. `beside` (additive, CTRL2/UX-01) is
   * "to the right of the field, level with it": the controller asks for it when the chip fits there, and the UI falls back to
   * below/above (whichever covers least) when it does not.
   */
  placement: "below" | "above" | "beside";
  /**
   * additive (D1): geometry of the recommended option (radio/checkbox member) so the UI may outline it.
   * CPL-05 (CTRL2): the rectangle of the FIRST recommended member; `optionAnchors` (additive) lists every recommended member
   * (a checkbox group recommends several), `optionAnchor` stays the first of them. The UI draws one outline per rectangle.
   */
  optionAnchor?: { x: number; y: number; w: number; h: number };
  optionAnchors?: { x: number; y: number; w: number; h: number }[];
  /** additive (CTRL2/UX-01): the QUESTION the suggestion answers, one line (the visible label, never a name-derived guess). Shown first. */
  questionText?: string;
  /**
   * additive (CTRL2/CPL-09): the draft was written for older or other context than the page has now. The chip shows a calm note
   * and a "Rewrite with new research" action (`UIAction.rewrite`); the controller never regenerates by itself (it costs money).
   * `research`: newer research exists (Tab still inserts, the note says so). `profile`: the Profile changed since it was written
   * and `company-rejected`: the user said the researched company was wrong. For the last two Tab insertion is DISABLED
   * (`insert` is `"never"` and `staleBlocksInsert` is true); only the explicit rewrite is offered.
   */
  staleReason?: "research" | "profile" | "company-rejected";
  /** additive (CTRL2/CPL-09): true when the staleness above stops Tab/Enter/Insert (the UI hides Insert and says why). */
  staleBlocksInsert?: boolean;
  /**
   * additive (CTRL2/CPL-06): a SKIP field that deserves one calm sentence (a file field the assistant will not touch). The chip is a
   * quiet explanatory one: no value, no Insert, no key hints other than Esc. `text` is the English fallback; the UI localises by `code`:
   * `file_unapproved` (a CV exists in Meridian but is not approved: "Approve your CV in Meridian > Documents so I can attach it"
   * with an Open Meridian action), `file_cover_letter` (the assistant never attaches a cover letter), `file_none` (no CV imported
   * yet), `generic`.
   */
  skipReason?: { code: "file_unapproved" | "file_cover_letter" | "file_none" | "generic"; text: string };
  /**
   * additive (CTRL2/UX-09): the field already holds the suggested value (inserted earlier, by Fill verified or typed by hand) and is
   * not undoable any more. The UI shows a quiet "Already inserted ✓" line and NO Insert button.
   */
  alreadyInserted?: boolean;
  /**
   * additive (D2-2): rectangles (this frame's viewport coordinates) of neighbouring fields the chip should not cover,
   * typically the NEXT assist field. The UI knows the chip's real size, so it picks the side (below/above) that covers
   * them least; `placement` stays the tie-break when both sides are equally clean.
   * CTRL2/UX-01: the list now ALSO holds, after the next fields (which come first, at most two), the PREVIOUS assist field (the value
   * just inserted there must stay readable) and the rectangle of the focused field's own printed label / legend (the question).
   * The PANEL's own rectangle is not in the list (the panel lives in the UI's own root, so the UI keeps the chip off it itself).
   * CTRL3/UXR-02: for a radio / checkbox group the list ALSO holds one rectangle per option of the FOCUSED group (the control and its
   * printed label together, so the 'beside' chip never lands on the option text the person has to read, the recommended one included),
   * and the previous group's options (their labels too).
   * UI3/UXR-02: how the visual layer reads the list. A rectangle that shares height with `anchor` (level with the field) is an option of
   * the focused group or a neighbour in the same row: the chip never lies on it while another place exists (the outline of the recommended
   * option counts too), and for a radio / checkbox group `beside` starts after the last of them. A choice group (`operation` CHECK_RADIO /
   * SET_CHECKBOX(ES), or `optionAnchor(s)`) that sends no such rectangle gets no `beside` at all: where its labels run is unknown. The rest of
   * the list (the next fields, the previous field, the label) is kept clear of as far as the room allows; when no side can, a short answer is
   * folded to a one-line pill (never a draft, an Undo, a reason or a stale note).
   */
  avoid?: { x: number; y: number; w: number; h: number }[];
  /**
   * additive (UXFIN/UXR-12): how many of the FIRST entries of `avoid` are the NEXT fields (0 to 2: the controller lists them first). The
   * visual layer treats covering one of them (by more than a sliver) as worse than covering anything else that is left to cover (the
   * previous field, the question's own label): the person is about to go there. A side that leaves them alone always wins over one that
   * does not; only when every side covers one is a short answer folded to its one-line pill. Absent = 0 (every rectangle counts the same).
   */
  avoidNext?: number;
  /**
   * additive (CTRL3/UXR-03): the chip's numbered actions (`Replace 1`, `Shorter 2` ...) are reached with a chord that types no character:
   * `mac` = Control+Option+digit, `other` = Alt+Shift+digit (the logic matches the keys, `AssistUI.chipAction`; the UI prints the glyphs).
   */
  keyHints?: { chipActionPrefix?: "mac" | "other" };
  /**
   * additive (CTRL3/UXR-08): this posting is already recorded as sent and the person has not said "Continue": NOTHING is inserted
   * (Tab, Enter, a click on Insert / Replace and "Fill verified" are all refused). `insert` is `"never"` and the chip says why
   * (`reasonCode: "duplicate"`, `reasonText`): the UI hides Insert and shows the reason on the chip itself.
   */
  duplicateBlocksInsert?: boolean;
  /**
   * additive (UXFIN/CPL-20): the OTHER documents Meridian would let this file field take (`suggestion.document.alternatives`: at most 6, the
   * one on offer excluded, best first). With at least one the chip carries a click-only "Use another CV" menu (a chip button, never a Tab stop,
   * never a numbered key: Tab always inserts the one on offer); choosing a row emits `{ type: "use-cv", id, key }`. Absent / empty = no choice.
   * `version` is 0 where it is not known (the document that was on offer before the person swapped it for another one).
   */
  alternatives?: { id: string; filename: string; version: number }[];
  /** additive (UXFIN/CPL-20): what the file field asks for, so the menu says "another CV" or "another cover letter" (default `cv`). */
  documentKind?: "cv" | "cover_letter";
}

/**
 * additive (D1): a subtle persistent marker on an inserted INFERRED / drafted field until the user touches it.
 * `label` (additive, D2-2) is the field's label; it only feeds the marker's accessible name.
 */
export interface FieldMark { key: string; anchor: ChipView["anchor"]; tone: Tone; label?: string }

export interface ProgressItem {
  key: string;
  label: string;
  section: string;
  status: FieldDigest["status"];
  tone?: "verified" | "inferred" | "draft";
  required: boolean;
  current: boolean;
}

export interface ResearchView {
  state: "none" | ResearchSnapshot["state"];
  headline: string; // English fallback from the server; UI prefers its own localized strings keyed by state/stages
  quality: ResearchSnapshot["quality"] | "none";
  needsCompany: boolean;
  needsSite: boolean;
  sources: { label: string; url: string; status: string }[];
  company: string;
  site: string | null;
}

export interface PanelView {
  /**
   * The panel is a pill. CTRL2/UX-05: it STARTS true when the viewport is narrower than 700 px or shorter than 600 px (a window at 200%
   * zoom, a phone), follows the window while the person has not chosen, and `toggle-collapse` is the person's choice (it sticks).
   */
  collapsed: boolean;
  /**
   * The worker's connection state, passed through unchanged (doc comment added by D2c; no type change). Two fields feed copy:
   * `port` is named in the pairing card and in the unpaired card ("Meridian answered on port N"; left out when absent or not a
   * port), and `meridianPorts` with two or more ports while the phase is `unpaired` or `revoked` makes the panel show the calm
   * "more than one program says it is Meridian" card with Try again (and no Connect). The logic layer does not need a flag:
   * it forwards every state it gets, including the `connection` that a failed `pair:start` ('ambiguous') carries.
   */
  connection: ConnectionState;
  /**
   * Meridian could not draft long answers (setup/budget); the UI shows a calm hint, never an alarm. `known` (additive, CTRL2/UX-08) is
   * false until the first answer of Meridian (the session reply or the status) has told whether a writing model is set up: while it is
   * false `available` is true, so a user with a working model never sees "Long answers need a writing model" flash. The note belongs to
   * `known === true && available === false` only.
   */
  generation: {
    available: boolean; known?: boolean; message?: string; budgetExhausted: boolean; spentUsd?: number; limitUsd?: number | null;
    /**
     * additive (UXFIN/SEC-06): Meridian used up the drafts it writes ON ITS OWN for this page (`drafts_paused` poll event). Writing is still
     * available: the person asks for a draft with "Draft with AI" on a question. The panel shows ONE calm, dismissible note (not remembered
     * for other pages); the logic clears the flag when the page's session starts over.
     */
    draftsPaused?: boolean;
  };
  job: { company: string; title: string; location: string; tracked: TrackedState } | null;
  research: ResearchView;
  progress: { total: number; done: number; items: ProgressItem[] };
  fillableCount: number; // verified, Tab-tier fields that "Fill verified" would insert now
  keysPaused: boolean; // assisted Tab handling paused by the user
  noForm: boolean; // activated but no application form detected (yet)
  profileEmpty: boolean;
  permissions: { allSites: boolean; crossOriginFrames: number };
  pendingDrafts: number;
  /**
   * Per-site switches of the panel menu ("Start automatically on this site", "Hide the hint on this site"). Omit to hide them.
   * `alwaysOn` is true for a rule of this browser AND for a site Meridian's own setting lists (`always_on_sites`).
   * additive (CTRL3/UX-13): `alwaysOnNeedsPermission` is true after the person asked for "start automatically" and the browser has not
   * granted the extension access to the site yet (the switch stays OFF, the question is asked in a small window): the menu shows ONE calm
   * line ("Allow access to this site in the window that opened") instead of a stale switch; it goes away by itself when the access is granted.
   */
  site?: {
    host: string; alwaysOn: boolean; hintHidden: boolean; alwaysOnNeedsPermission?: boolean;
    /**
     * additive (UXFIN/UX-13): the person switched "start automatically" OFF and a rule they did not set here still starts the assistant (a rule
     * for a parent site, one set in Meridian): `rule` names it (as the worker reported it, null when it could not be read out), `text` is the
     * worker's own sentence, which says where to remove it (English: the fallback). The menu shows ONE calm line under the switch, which stays ON.
     */
    alwaysOnNote?: { code: "rule"; rule: string | null; text: string };
  };
  /** The content script was orphaned (extension reloaded or updated): the panel only offers "Reload this page". */
  orphaned?: boolean;
  /**
   * additive (CTRL2/CPL-13): this posting is already recorded (`applied`) or an application is being prepared in Meridian
   * (`preparing`). A prominent banner at the top of the panel: "You applied on 14 Oct. Continue anyway?" with Continue
   * (`duplicate-continue`) and Close (`deactivate`). While `applied` and not acknowledged the controller does NOT insert on Tab
   * (it advances) and says why in a toast. Null/absent when nothing is shown (never duplicated, or already acknowledged).
   * `since` is an ISO date or timestamp when the server knows it.
   * additive (CTRL3/UXR-08): `status` says what became of an `applied` application so the banner words it right: "You applied on 14 Oct"
   * (`applied`, `interview`, `offer`), "...it was rejected" (`rejected`), "...you withdrew it" (`withdrawn`), "...no answer came" (`ghosted`).
   * The sentence "I won't insert anything until you continue" is TRUE: while the banner is up and unacknowledged Tab, Enter, a click on
   * Insert / Replace and "Fill verified" all refuse (`ChipView.duplicateBlocksInsert`).
   */
  duplicate?: { state: "applied" | "preparing"; since?: string; status?: DuplicateStatus } | null;
  /**
   * additive (CTRL2/CPL-18, FR-02): show the 3-step first-use hint card ("Focus a field", "Tab inserts and moves on", "Esc hides
   * the suggestion"). The controller sets it while the form is ready and the person has not yet focused an assist field or
   * inserted anything on this page; it never sets it on later insertions.
   * CTRL3/UXR-10: the card is for good once the person has had their FIRST answer inserted, or pressed "Got it": the logic reads the worker's
   * remembered look (`ui:get`, entry `dismissed.first_use`, 400 days) before it ever sets `firstUse`, writes the entry itself after the first
   * insertion (or asks the UI to through `AssistUI.dismissNote`), and the UI's own "Got it" writes the same entry. So `firstUse` is already
   * false on every later application page; the UI still hides the card while its own copy of the entry says so.
   */
  firstUse?: boolean;
  /**
   * additive (CTRL2/FR-03): shown when Tab reaches the end of the form while required questions are still empty: "N required
   * questions are still empty", each listed item focuses its field when clicked (`focus-item`), "Dismiss" is `review-dismiss`.
   * Never blocks Tab or Submit. Null/absent otherwise.
   */
  review?: { missingRequired: number; items: { key: string; label: string }[] } | null;
  /**
   * additive (CTRL2/UX-03): "Fill verified" just inserted `count` answers and they can all be taken back for a few seconds. The panel
   * shows an "Undo all" button (`UIAction.undo-all`) while this is present; it disappears by itself when the window closes (the controller
   * re-renders then). Alt+Z after a bulk fill undoes only the field the person is on.
   * CTRL3/UXR-06: Alt+Shift+Z (Option+Shift+Z on a Mac) is the keyboard way to "Undo all" while `batchUndo` is present, from a form field,
   * from the panel and from inside a frame; the logic swallows the key only when there is a batch to take back. The UI may print it.
   */
  batchUndo?: { count: number } | null;
  /**
   * additive (CTRL3/SEC-01, UXR-06): "Fill verified" does NOT fill at once: the first press (`fill-verified`) opens this confirmation row,
   * which lists EVERY field the button will fill (label and the value that goes in). `fill-confirm` (one click / Enter) fills exactly
   * these, `fill-cancel` (or Esc) closes it. Only when `AssistUI.capabilities.fillPreview` is true; a UI without it keeps the one-click
   * behaviour. `count` is the number of fields (`items` may be shortened: the row says "and N more"). Null/absent when nothing is pending.
   * After a confirmed fill the logic moves the keyboard back to the form field the person was on (Alt+Shift+Z undoes the whole batch while
   * `batchUndo` is present).
   */
  fillPreview?: { count: number; items: { key: string; label: string; value: string }[] } | null;
}

/** What became of an application that is already in the tracker (`JobSummary.status`, reduced to the cases that change the words). */
export type DuplicateStatus = "applied" | "rejected" | "withdrawn" | "ghosted" | "interview" | "offer";

export type ToastKind =
  | "filled" | "undone" | "saved" | "draft_ready" | "regenerating" | "copied" | "no_suggestion" | "paused" | "resumed"
  | "connection_lost" | "connection_back" | "error"
  /** additive (UXFIN/UXR-01): "Save to tracker" on a posting Meridian already has as sent: it says so (it is NOT "saved") */
  | "already_recorded";
/**
 * Optional `params` the UI understands (everything else is ignored; text is never taken from the server for toasts):
 * `label` (field label, filled/undone/draft_ready), `count` (number, filled: "5 answers inserted"), `scope`
 * ("application" | "reuse", saved).
 */
export interface ToastView {
  kind: ToastKind;
  params?: Record<string, string | number>;
  tone: "info" | "ok" | "warn";
  /**
   * additive (CTRL2/UX-03): the toast offers an Undo button that emits `{ type: "undo-all" }` (the "N answers inserted" toast of Fill
   * verified). `undone` after such an undo carries `count` instead of `label`: "N answers taken back".
   */
  undoable?: boolean;
}

/** "Save your answer in Meridian?" prompt after the user typed or edited something. */
export interface SaveAskView {
  key: string;
  fieldLabel: string;
  valuePreview: string;
  allowReuse: boolean; // false for narrative / company-specific / protected questions
  edited: boolean; // edited a suggestion vs typed from scratch
}
/** Offered after a submit click followed by an application-confirmation page. */
export interface TrackAskView { company: string; title: string; evidence: string }

/**
 * additive (UI2/UX-04): a sentence only a screen reader hears (a polite live region, nothing is drawn). `inserted` is raised after
 * an insertion, whichever way it happened (Tab, Enter, a click, an asynchronous file or combobox fill): "Inserted <label>: <value>".
 * `value` is the display value; the UI shortens it. The UI also derives this from the insertion flash when the logic never calls it,
 * and skips an announcement it already made for the same words, so calling it is always safe.
 */
export interface AnnounceView { kind: "inserted"; label: string; value?: string }

export type UIAction =
  | { type: "accept"; key: string }
  | { type: "replace"; key: string }
  | { type: "regenerate"; key: string; instruction: "again" | "shorter" | "longer" | "different" }
  | { type: "draft"; key: string } // explicit draft for a short field
  | { type: "dismiss"; key: string }
  | { type: "undo"; key: string }
  | { type: "undo-all" } // additive (CTRL2/UX-03): take back every answer of the last "Fill verified" (toast / panel button)
  | { type: "fill-verified" } // with `capabilities.fillPreview`: opens the confirmation row (`PanelView.fillPreview`); else fills at once
  | { type: "fill-confirm" } // additive (CTRL3/SEC-01): fill exactly what the confirmation row lists
  | { type: "fill-cancel" } // additive (CTRL3/SEC-01): close the confirmation row
  | { type: "toggle-collapse" }
  | { type: "toggle-keys" }
  | { type: "focus-item"; key: string }
  | { type: "connect" } // start pairing
  | { type: "cancel-pairing" }
  | { type: "retry-connection" }
  | { type: "open-meridian" }
  | { type: "open-options"; anchor?: "permissions" | "connection" | "behaviour" | "help" } // "help": additive (MISC3/UX-12), the options page's keys and colours
  | { type: "research-company"; company: string }
  | { type: "research-site"; domain: string }
  | { type: "research-refresh" }
  | { type: "save-answer"; key: string; remember: "application" | "reuse" }
  | { type: "save-never"; key: string } // do not ask again for this answer
  | { type: "save-setting"; value: "always" | "never" }
  | { type: "track-record" }
  | { type: "track-dismiss" }
  | { type: "track-save" } // additive (CTRL2/CPL-10): "Save to tracker": record the posting without claiming it was sent
  | { type: "track-applied" } // additive (CTRL2/CPL-10): "I applied": open the record card without confirmation evidence
  | { type: "duplicate-continue" } // additive (CTRL2/CPL-13): acknowledge the duplicate banner; Tab inserts again
  | { type: "rewrite"; key: string } // additive (CTRL2/CPL-09): explicit regenerate of a stale draft with the new context
  | { type: "keys-help" } // additive (CTRL2/UX-02): open the in-panel key cheat sheet
  | { type: "review-dismiss" } // additive (CTRL2/FR-03): close the end-of-form review
  | { type: "use-cv"; id: string; key?: string } // additive (UXFIN/CPL-20): attach this other document (one of the chip's `alternatives`) instead; `key` defaults to the field the chip is on
  | { type: "panel-escape" } // additive (UI3/UX-02): Esc inside the panel or a prompt (nothing of the UI used it): give the keyboard back to the form field
  | { type: "deactivate" }
  | { type: "always-on-site" } // toggle "start automatically on this site"
  | { type: "hide-hint-site" };

export interface AssistUI {
  /** Every frame mounts a UI instance for chips; only the TOP frame mounts the panel (`panel: true`). */
  mount(options: { panel: boolean }): void;
  unmount(): void;
  setPanel(view: PanelView): void;
  showChip(view: ChipView): void;
  updateChipAnchor(anchor: ChipView["anchor"]): void;
  hideChip(): void;
  /** Brief visual confirmation on the field itself (outline + sparkle) after an insertion. */
  flash(anchor: ChipView["anchor"], tone: Tone): void;
  /**
   * additive (D1, optional): the complete current set of persistent markers, replaced on every call (nodes are reused by
   * `key`, so calling it on every scroll frame with fresh anchors is cheap). A marker stays until a call omits it.
   */
  setMarks?(marks: FieldMark[]): void;
  toast(view: ToastView): void;
  /** additive (UI2/UX-04, optional): a screen-reader-only polite announcement (see AnnounceView). */
  announce?(view: AnnounceView): void;
  askSave(view: SaveAskView | null): void;
  askTrack(view: TrackAskView | null): void;
  /** Subscribe to user interactions (clicks on UI buttons; always isTrusted-checked inside the UI). */
  onAction(handler: (action: UIAction) => void): () => void;
  /** `settings.language` ("auto" follows navigator.language). Call once after mount and whenever the setting changes. */
  setLanguage(language: Language): void;
  /** Move keyboard focus into the panel / pending prompt (the Alt+Shift+M shortcut). False when there is nothing to focus. */
  focusPanel(): boolean;
  /**
   * additive (CTRL3/UXR-03, optional): the logic matched a chord of the numbered chip actions (Control+Option+1..7 on macOS, Alt+Shift+1..7
   * elsewhere, by `event.code` or the printed digit) and asks the UI to run action `n` of the chip on screen. True ONLY when the chip
   * really has a visible control with that number and it was run: the logic swallows the key then and not otherwise (a chord that took
   * no action never alters what the person types). A UI that still matches the keys itself may leave this out.
   */
  chipAction?(n: number): boolean;
  /**
   * additive (CTRL3): what this UI can render, so a logic newer than the UI never strands the person. `fillPreview`: the confirmation row of
   * `PanelView.fillPreview` (without it "Fill verified" fills at once). `chipActions`: `chipAction` is implemented.
   */
  capabilities?: { fillPreview?: boolean; chipActions?: boolean };
  /**
   * additive (CTRL3/UXR-10, optional): remember for good that a note of the panel was seen (`first_use`: the person inserted their first
   * answer). The UI keeps it in its own remembered look (the same entry as the "Got it" button); without this method the logic writes the
   * entry itself through the worker (`ui:set`).
   */
  dismissNote?(kind: "first_use"): void;
  /**
   * additive (SEC-01, optional): called right before the assistant writes into the page. The person decides on what the chip and the panel
   * show, so a write is only made while they are on top of the page: a layer of the page in the top layer (a popover, a dialog) that opens
   * after the assistant's UI would hide them. The UI puts itself back on top (and takes back what the page did to its host) and answers
   * whether its chip / panel are now drawn above everything that paints. False: it could not be shown, nothing is written. Without a visible
   * chip or panel there is nothing to hide and the answer is true. A UI without this method (the headless one) draws nothing.
   */
  ensureOnTop?(): boolean;
  /** Test hook: only present in __E2E__ builds. */
  root?: ShadowRoot;
}

/** Marks elements rendered by the UI layer so the logic never scans or measures them. */
export const UI_HOST_ATTRIBUTE = "data-meridian-assistant";
