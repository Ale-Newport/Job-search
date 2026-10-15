// Scripted view models for every state of the visual layer. Typed against ui-api.ts, so contract drift fails typecheck.

import type { ConnectionPhase } from "../../src/shared/messages";
import type { GenerationStatus, SuggestionState } from "../../src/shared/protocol";
import type { AssistUI, ChipView, FieldMark, PanelView, ProgressItem, ResearchView, SaveAskView, ToastKind, ToastView, Tone, TrackAskView } from "../../src/content/ui-api";

export type Anchor = ChipView["anchor"];

export interface SceneExpect {
  /** data-tone on the chip. */
  tone?: string;
  quiet?: boolean;
  rich?: boolean;
  /** chip must not exist / be hidden */
  noChip?: boolean;
  placement?: "below" | "above" | "beside" | "overlay";
  has?: string[];
  lacks?: string[];
  /** testid -> substring of its text, per locale */
  text?: Record<string, { en?: string; es?: string }>;
  /** ids of page elements the chip must not overlap (the next field) */
  noCover?: string[];
  /** persistent field markers expected on screen: how many, and the tone class each must carry */
  marks?: { count: number; tones?: string[] };
  /** outlines of recommended options expected on screen (CPL-05) */
  options?: number;
}

export interface SceneCtx {
  ui: AssistUI;
  anchor: Anchor;
}

export interface Scene {
  id: string;
  group: "chip" | "panel" | "notice" | "matrix" | "mark";
  /** mount the panel (top frame) for this scene */
  panel?: boolean;
  /** id of the page element the chip anchors to */
  field?: string;
  /** also run at 360 px and in a 300 px iframe-sized viewport */
  responsive?: boolean;
  /** also run at 200% / 400% browser zoom (550x350 and 320x256 CSS px) */
  zoom?: boolean;
  /** real clicks (data-testid) performed before the screenshot */
  clicks?: string[];
  /** real typing into a page field (id) before the screenshot: the chip shrinks to a pill while the person types */
  typing?: { id: string; text: string };
  /** skip the screenshot (assertions only) */
  noShot?: boolean;
  /** screenshot the whole viewport instead of cropping around the UI */
  fullShot?: boolean;
  expect?: SceneExpect;
  setup(ctx: SceneCtx): void;
}

export const DRAFT = [
  "I've spent the last six years building reliable data platforms, and Acme Robotics' work on warehouse automation is exactly the kind of problem I want to spend the next few on. At Northwind I led the migration of our batch pipelines to a streaming architecture, which cut end-to-end latency from hours to minutes and gave the operations team live visibility they had never had before. That project taught me how much of robotics software is really about trustworthy data: sensors lie, networks drop, and the systems around the robot have to cope gracefully.",
  "What draws me to this role is the combination of product ownership and hard engineering. Your recent launch of the Atlas picking arm shows a team that ships hardware and software together, and I'd like to contribute on the fleet-telemetry side, where my experience with Python, Kafka and observability would be useful from day one. I'm also keen to work in London alongside a team that values mentoring, which has been the most rewarding part of my current job.",
].join("\n\n");

export const SOURCES = [
  { label: "Acme Robotics — About us", url: "https://acme-robotics.example/about" },
  { label: "Careers · Senior Software Engineer", url: "https://acme-robotics.example/careers/senior-software-engineer" },
  { label: "Atlas picking arm launch", url: "https://acme-robotics.example/blog/atlas" },
];

export function chipView(anchor: Anchor, over: Partial<ChipView> = {}): ChipView {
  return {
    key: "f0:12", fieldLabel: "Email address", state: "VERIFIED", insert: "tab", tone: "verified", operation: "TYPE_TEXT",
    value: "alex.newport@example.com", whyCode: "profile_fact", whySummary: "From your verified profile", sources: [],
    canRegenerate: false, canDraft: false, review: false, conflictsWithCurrent: false, undoable: false, dwellRemainingMs: 0,
    keys: ["tab", "shift-tab", "esc"], anchor, placement: "below", ...over,
  };
}

/** Rectangle of a page element of the rig (the "next field" a chip must stay off). */
export function pageRect(id: string): Anchor {
  const el = document.getElementById(id);
  if (!el) return { x: 0, y: 0, w: 0, h: 0 };
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}

const ITEMS: [string, string, ProgressItem["status"], ProgressItem["tone"] | undefined, boolean][] = [
  ["First name", "Personal information", "inserted", "verified", true],
  ["Last name", "Personal information", "inserted", "verified", true],
  ["Email", "Personal information", "inserted", "verified", true],
  ["Phone", "Personal information", "inserted", "verified", true],
  ["Current location", "Personal information", "filled", undefined, false],
  ["LinkedIn profile", "Links", "inserted", "verified", false],
  ["Website", "Links", "skipped", undefined, false],
  ["Resume / CV", "Documents", "inserted", "verified", true],
  ["Cover letter", "Documents", "pending", undefined, false],
  ["Years of experience", "Experience", "inserted", "inferred", true],
  ["Current employer", "Experience", "edited", undefined, true],
  ["Notice period", "Experience", "pending", undefined, true],
  ["Salary expectations", "Experience", "needs_you", undefined, false],
  ["Why do you want to work at Acme Robotics and what draws you to this particular role?", "Questions", "pending", "draft", true],
  ["Tell us about a project you are proud of", "Questions", "pending", "draft", true],
  ["Are you legally authorised to work in the UK?", "Eligibility", "pending", undefined, true],
  ["Will you now or in the future require sponsorship?", "Eligibility", "pending", undefined, true],
  ["Gender", "Diversity (optional)", "locked", undefined, false],
  ["Ethnicity", "Diversity (optional)", "locked", undefined, false],
  ["I agree to the privacy policy", "Consent", "locked", undefined, true],
];

export function progressItems(count = ITEMS.length): ProgressItem[] {
  return ITEMS.slice(0, count).map(([label, section, status, tone, required], index) => ({
    key: `f0:${index}`, label, section, status, tone, required, current: index === 12 || (count < 13 && index === count - 2),
  }));
}

export function researchView(over: Partial<ResearchView> = {}): ResearchView {
  return {
    state: "done", headline: "Researched Acme Robotics", quality: "full", needsCompany: false, needsSite: false, company: "Acme Robotics", site: "acme-robotics.example",
    sources: [
      { label: "Acme Robotics — About us", url: "https://acme-robotics.example/about", status: "ok" },
      { label: "Careers · Senior Software Engineer", url: "https://acme-robotics.example/careers/senior-software-engineer", status: "ok" },
      { label: "Our values", url: "https://acme-robotics.example/values", status: "ok" },
      { label: "Atlas picking arm launch", url: "https://acme-robotics.example/blog/atlas", status: "ok" },
      { label: "acme.example/team", url: "https://acme.example/team", status: "unverified" },
      { label: "Press kit", url: "https://acme-robotics.example/press", status: "blocked" },
      { label: "Careers feed", url: "https://acme-robotics.example/careers.rss", status: "failed" },
      { label: "Investors", url: "https://acme-robotics.example/investors", status: "skipped" },
    ],
    ...over,
  };
}

export function panelView(over: Partial<PanelView> = {}): PanelView {
  const items = progressItems();
  return {
    collapsed: false,
    connection: { phase: "ready", port: 47615, appVersion: "1.4.0", latestExtensionVersion: "0.1.0" },
    generation: { available: true, budgetExhausted: false, spentUsd: 1.2, limitUsd: 10 },
    job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "none" },
    research: researchView(),
    progress: { total: items.length, done: items.filter((i) => i.status === "inserted" || i.status === "filled" || i.status === "edited").length, items },
    fillableCount: 4, keysPaused: false, noForm: false, profileEmpty: false, permissions: { allSites: true, crossOriginFrames: 0 }, pendingDrafts: 0,
    site: { host: "boards.greenhouse.io", alwaysOn: false, hintHidden: false },
    ...over,
  };
}

function conn(phase: ConnectionPhase, extra: Partial<PanelView["connection"]> = {}): PanelView["connection"] {
  return { phase, port: 47615, appVersion: "1.4.0", ...extra };
}

const scenes: Scene[] = [];
const add = (scene: Scene): void => { scenes.push(scene); };

// ---- chip: ready states ------------------------------------------------------------------------------------------
add({ id: "chip-verified-email", group: "chip", field: "fx-top", responsive: true, expect: { tone: "verified", quiet: false, rich: false, has: ["chip-accept", "hint-tab", "hint-shift-tab", "hint-esc"], placement: "below", text: { "chip-trust": { en: "Verified", es: "Verificado" }, "chip-why": { en: "From your verified profile", es: "De tu perfil verificado" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor)) });
add({ id: "chip-verified-short", group: "chip", field: "fx-top", expect: { tone: "verified", rich: false },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "First name", value: "Alex" })) });
add({ id: "chip-verified-select", group: "chip", field: "fx-top", expect: { tone: "verified", has: ["chip-accept"], text: { "chip-value": { en: "Choose", es: "Elegir" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Country", operation: "SELECT_OPTION", value: "GB", optionLabel: "United Kingdom" })) });
add({ id: "chip-verified-radio", group: "chip", field: "fx-top", expect: { tone: "verified" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Do you have the right to work in the UK?", operation: "CHECK_RADIO", value: "Yes", optionLabel: "Yes" })) });
add({ id: "chip-verified-checkboxes", group: "chip", field: "fx-top", expect: { tone: "verified" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Which skills apply?", operation: "SET_CHECKBOXES", value: "Python, SQL, Docker, Kubernetes" })) });
add({ id: "chip-verified-file", group: "chip", field: "fx-top", expect: { tone: "verified", text: { "chip-value": { en: "Attach file", es: "Adjuntar archivo" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Resume / CV", operation: "ATTACH_FILE", value: "Alex-Newport-CV.pdf", whyCode: "cv_attach", whySummary: "Your approved CV" })) });
add({ id: "chip-verified-review", group: "chip", field: "fx-top", expect: { tone: "verified", has: ["chip-review"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { review: true, fieldLabel: "Current salary", value: "£68,000" })) });
add({ id: "chip-protected-explicit", group: "chip", field: "fx-top", responsive: true, expect: { tone: "locked", has: ["chip-protected", "hint-enter", "chip-accept"], lacks: ["hint-tab"], text: { "chip-trust": { en: "Protected answer", es: "Respuesta protegida" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Veteran status", insert: "explicit", tone: "locked", value: "I am not a protected veteran", whyCode: "policy", whySummary: "Follows your saved preferences", keys: ["enter", "shift-tab", "esc"] })) });
add({ id: "chip-inferred", group: "chip", field: "fx-top", responsive: true, expect: { tone: "inferred", text: { "chip-trust": { en: "Check this", es: "Revísalo" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Years of experience", state: "INFERRED", tone: "inferred", value: "6", whyCode: "derived", whySummary: "Worked out from your profile" })) });
add({ id: "chip-conflict", group: "chip", field: "fx-top", expect: { tone: "verified", has: ["chip-replace", "chip-conflict"], lacks: ["chip-accept"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { conflictsWithCurrent: true })) });
add({ id: "chip-undoable", group: "chip", field: "fx-top", expect: { has: ["chip-undo", "chip-inserted", "hint-undo"], lacks: ["chip-accept"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { undoable: true })) });

// ---- chip: drafts ---------------------------------------------------------------------------------------------------
const draft = (anchor: Anchor, over: Partial<ChipView> = {}): ChipView => chipView(anchor, {
  key: "f0:30", fieldLabel: "Why do you want to work at Acme Robotics?", state: "GENERATED_DRAFT", tone: "draft", value: DRAFT, whyCode: "generated",
  whySummary: "Drafted from your profile and this company", whyDetail: "Based on your Northwind data-platform project and Acme's warehouse automation focus.",
  sources: SOURCES, canRegenerate: true, review: true, generation: { status: "DONE", wordCount: 164 }, keys: ["tab", "shift-tab", "regenerate", "esc"], ...over,
});
add({ id: "chip-draft-long", group: "chip", field: "fx-text", responsive: true, expect: { tone: "draft", rich: true, has: ["chip-more", "chip-meta", "chip-sources", "chip-regen", "chip-accept"], text: { "chip-trust": { en: "AI draft — review it", es: "Borrador con IA: revísalo" }, "chip-meta": { en: "164 words", es: "164 palabras" } } },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor)) });
add({ id: "chip-draft-dwell", group: "chip", field: "fx-text", expect: { tone: "draft", has: ["chip-dwell"] },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor, { dwellRemainingMs: 60000 })) });
add({ id: "chip-draft-expanded", group: "chip", field: "fx-text", clicks: ["chip-more"], expect: { tone: "draft", rich: true },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor)) });
add({ id: "chip-draft-sources-open", group: "chip", field: "fx-text", clicks: ["chip-sources-toggle"], expect: { tone: "draft" },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor, { sources: [...SOURCES, { label: "Our values", url: "https://acme-robotics.example/values" }, { label: "Press kit", url: "https://acme-robotics.example/press" }] })) });
add({ id: "chip-draft-short", group: "chip", field: "fx-text", expect: { tone: "draft", rich: true },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor, { value: "I'm excited by Acme's mission to make warehouses safer and more efficient.", sources: [], whyDetail: undefined, generation: { status: "DONE", wordCount: 12 } })) });
add({ id: "chip-draft-no-sources", group: "chip", field: "fx-text", expect: { tone: "draft", lacks: ["chip-sources"] },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor, { sources: [], canRegenerate: false })) });
add({ id: "chip-draft-above", group: "chip", field: "fx-bottom", expect: { placement: "above" },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor)) });

// ---- chip: busy -------------------------------------------------------------------------------------------------------
const STATUSES: GenerationStatus[] = ["QUEUED", "GENERATING", "VALIDATING", "REGENERATING", "FAILED"];
for (const status of STATUSES) {
  const failed = status === "FAILED";
  add({ id: `chip-generating-${status.toLowerCase()}`, group: "chip", field: "fx-text", responsive: status === "GENERATING", expect: { tone: failed ? "neutral" : "busy", lacks: ["chip-accept"], has: ["chip-status", "hint-tab"] },
    setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
      key: "f0:31", fieldLabel: "Tell us about a project you are proud of", state: "GENERATING", tone: "busy", value: "", whyCode: "generated", whySummary: "", insert: "never", operation: null,
      generation: { status }, keys: ["tab", "shift-tab", "esc"],
    })) });
}
add({ id: "chip-generating-attempt2", group: "chip", field: "fx-text", expect: { tone: "busy" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { state: "GENERATING", tone: "busy", value: "", insert: "never", operation: null, generation: { status: "GENERATING", attempt: 2 } })) });

// ---- chip: calm / locked ----------------------------------------------------------------------------------------------
const NEEDS: [string, string, string][] = [
  ["missing-fact", "missing_candidate_fact", "missing_fact"],
  ["setup-required", "setup_required", "setup_required"],
  ["budget", "budget", "budget"],
  ["model-error", "model_error", "model_error"],
  ["validation-failed", "validation_failed", "validation_failed"],
  ["format-error", "format_error", "format_error"],
  ["no-matching-option", "no_matching_option", "no_matching_option"],
  ["protected-enter", "protected", "policy"],
];
for (const [slug, reasonCode, whyCode] of NEEDS) {
  add({ id: `chip-needs-${slug}`, group: "chip", field: "fx-top", responsive: slug === "missing-fact", expect: { tone: "neutral", quiet: true, lacks: ["chip-accept"], text: { "chip-label": { en: "Needs you", es: "Te toca a ti" } } },
    setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
      fieldLabel: "Notice period", state: "NEEDS_YOU", tone: "neutral", insert: "never", value: "", operation: null, whyCode, whySummary: "", reasonCode, reasonText: "", keys: ["tab", "shift-tab", "esc"],
    })) });
}
add({ id: "chip-needs-draft-button", group: "chip", field: "fx-top", expect: { tone: "neutral", quiet: true, has: ["chip-draft"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Anything else we should know?", state: "NEEDS_YOU", tone: "neutral", insert: "never", value: "", operation: null, whyCode: "missing_fact", whySummary: "", reasonCode: "missing_fact", canDraft: true })) });
add({ id: "chip-needs-fill-failed", group: "chip", field: "fx-top", expect: { tone: "error", quiet: true },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Country", state: "NEEDS_YOU", tone: "error", insert: "never", value: "", operation: null, whyCode: "", whySummary: "", reasonCode: "fill_failed" })) });
add({ id: "chip-consent", group: "chip", field: "fx-top", responsive: true, expect: { tone: "locked", quiet: true, lacks: ["chip-accept"], text: { "chip-label": { en: "Yours to decide", es: "La decides tú" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "I agree to the privacy policy", state: "CONSENT", tone: "locked", insert: "never", value: "", operation: null, whyCode: "consent", whySummary: "", reasonCode: "consent" })) });
add({ id: "chip-sensitive-blank", group: "chip", field: "fx-top", expect: { tone: "locked", quiet: true, text: { "chip-label": { en: "Left blank", es: "Lo dejo en blanco" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Gender (optional)", state: "SENSITIVE_BLANK", tone: "locked", insert: "never", value: "", operation: null, whyCode: "sensitive_blank", whySummary: "", reasonCode: "sensitive_blank" })) });
add({ id: "chip-not-applicable", group: "chip", field: "fx-top", expect: { tone: "neutral", quiet: true },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Sponsorship details", state: "NOT_APPLICABLE", tone: "neutral", insert: "never", value: "", operation: null, whyCode: "not_applicable", whySummary: "", reasonCode: "not_applicable" })) });
add({ id: "chip-skip", group: "chip", field: "fx-top", expect: { noChip: true },
  setup: ({ ui, anchor }) => { ui.showChip(chipView(anchor)); ui.showChip(chipView(anchor, { key: "f0:99", state: "SKIP", insert: "never" })); } });
add({ id: "chip-hide", group: "chip", field: "fx-top", expect: { noChip: true },
  setup: ({ ui, anchor }) => { ui.showChip(chipView(anchor)); ui.hideChip(); } });

// ---- chip: robustness --------------------------------------------------------------------------------------------------
add({ id: "chip-long-unbroken", group: "chip", field: "fx-top", responsive: true, expect: { tone: "verified" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "A".repeat(160), value: `https://example.test/${"segment-".repeat(40)}`, whySummary: "Y".repeat(200), whyCode: "unknown_code" })) });
add({ id: "chip-long-label-multiline", group: "chip", field: "fx-top", expect: { tone: "verified", rich: true },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "Address", value: "12 Example Street\nFlat 3\nLondon\nE1 6AN\nUnited Kingdom" })) });
add({ id: "chip-unknown-why-fallback", group: "chip", field: "fx-top", expect: { text: { "chip-why": { en: "Brand-new server reason", es: "Brand-new server reason" } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { whyCode: "brand_new_code", whySummary: "Brand-new server reason" })) });
add({ id: "chip-hostile-strings", group: "chip", field: "fx-top", expect: { tone: "verified" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
    fieldLabel: "<img src=x onerror=\"window.__pwned=1\">Name", value: "<b>bold</b><script>window.__pwned=2</script>", whySummary: "<i>why</i>", whyCode: "x",
    sources: [{ label: "<u>evil</u>", url: "javascript:window.__pwned=3" }, { label: "data link", url: "data:text/html,<script>window.__pwned=4</script>" }, { label: "fine", url: "https://example.test/ok" }],
    whyDetail: "<svg onload=window.__pwned=5>",
  })) });

// ---- chip: placement ------------------------------------------------------------------------------------------------------
add({ id: "chip-flip-above", group: "chip", field: "fx-bottom", responsive: true, expect: { placement: "above" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor)) });
add({ id: "chip-pref-above", group: "chip", field: "fx-mid", expect: { placement: "above" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { placement: "above" })) });
add({ id: "chip-clamp-right", group: "chip", field: "fx-right", responsive: true, expect: {},
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor)) });
add({ id: "chip-tall-field", group: "chip", field: "fx-tall", expect: { placement: "beside" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor)) });
add({ id: "chip-anchor-offscreen", group: "chip", field: "fx-top", noShot: true, expect: {},
  setup: ({ ui, anchor }) => { ui.showChip(chipView(anchor)); ui.updateChipAnchor({ ...anchor, y: -400 }); } });
add({ id: "chip-follow-scroll", group: "chip", field: "fx-top", noShot: true, expect: {},
  setup: ({ ui, anchor }) => { ui.showChip(chipView(anchor)); ui.updateChipAnchor({ ...anchor, y: anchor.y + 60 }); } });


// ---- chip: keeping off the next field -----------------------------------------------------------------------------------
add({ id: "chip-avoid-next", group: "chip", field: "fx-pair-a", responsive: true, zoom: true, expect: { placement: "above", noCover: ["fx-pair-b"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { avoid: [pageRect("fx-pair-b")] })) });
add({ id: "chip-avoid-irrelevant", group: "chip", field: "fx-pair-a", expect: { placement: "below" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { avoid: [{ x: 700, y: 560, w: 120, h: 40 }] })) });
// "beside" is the third candidate (wave 3): these three are about the tie-break between above and below, so a rectangle to the
// right of the field keeps "beside" from being the cleanest side here (it has its own scenes below).
const beBlocked = (anchor: Anchor): Anchor => ({ x: anchor.x + 400, y: anchor.y - 20, w: 300, h: 130 });
add({ id: "chip-avoid-both-sides-above-cleaner", group: "chip", field: "fx-pair-a", expect: { placement: "above" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { avoid: [pageRect("fx-pair-b"), { x: anchor.x, y: anchor.y - 70, w: 30, h: 8 }, beBlocked(anchor)] })) });
add({ id: "chip-avoid-both-sides-below-cleaner", group: "chip", field: "fx-pair-a", expect: { placement: "below" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { placement: "above", avoid: [{ x: 0, y: anchor.y - 130, w: 600, h: 120 }, { x: anchor.x, y: anchor.y + 60, w: 20, h: 6 }, beBlocked(anchor)] })) });
// (a short answer would now fold to one line above the field; a longer one cannot, and this scene is about the full chip's tie-break)
const LONGER = "A longer answer that needs the whole chip: it does not fit on one line, so the chip cannot fold to a pill to get out of the way.";
add({ id: "chip-avoid-no-room-above", group: "chip", field: "fx-top", expect: { placement: "below" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { value: LONGER, avoid: [{ x: anchor.x, y: anchor.y + 44, w: 320, h: 34 }, beBlocked(anchor)] })) });
add({ id: "chip-fold-no-room-above", group: "chip", field: "fx-top", expect: { placement: "above", has: ["chip-pill-text", "chip-pill-key"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { avoid: [{ x: anchor.x, y: anchor.y + 44, w: 320, h: 34 }, beBlocked(anchor)] })) });


// ---- chip: the question, beside the field, keeping off the previous field and the label (UX-01) ----------------------------------
const QUESTION = "What is the best email address to reach you on?";
add({ id: "chip-question", group: "chip", field: "fx-top", expect: { tone: "verified", has: ["chip-question"], text: { "chip-question": { en: QUESTION, es: QUESTION } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: QUESTION })) });
add({ id: "chip-question-long", group: "chip", field: "fx-top", responsive: true, expect: { tone: "verified", has: ["chip-question"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: `${QUESTION} `.repeat(6).trim() })) });
add({ id: "chip-question-quiet", group: "chip", field: "fx-top", responsive: true, expect: { quiet: true, has: ["chip-question"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
    fieldLabel: "Notice period", questionText: "How much notice do you need to give your current employer?", state: "NEEDS_YOU", tone: "neutral", insert: "never", value: "", operation: null,
    whyCode: "missing_fact", whySummary: "", reasonCode: "missing_fact",
  })) });
add({ id: "chip-question-protected", group: "chip", field: "fx-top", expect: { tone: "locked", has: ["chip-question", "chip-protected"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
    fieldLabel: "Work authorisation", questionText: "Are you legally authorised to work in the United Kingdom?", insert: "explicit", tone: "locked", value: "Yes",
    whyCode: "policy", whySummary: "Follows your saved preferences", keys: ["enter", "shift-tab", "esc"],
  })) });
add({ id: "chip-question-draft", group: "chip", field: "fx-text", expect: { tone: "draft", rich: true, has: ["chip-question"] },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor, { questionText: "Why do you want to work at Acme Robotics?" })) });
add({ id: "chip-beside", group: "chip", field: "fx-top", expect: { tone: "verified", placement: "beside", has: ["chip-question", "chip-accept"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: QUESTION, placement: "beside" })) });
add({ id: "chip-beside-no-room", group: "chip", field: "fx-right", expect: { placement: "below" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: QUESTION, placement: "beside" })) });
add({ id: "chip-beside-draft", group: "chip", field: "fx-text", expect: { rich: true, placement: "beside" },
  setup: ({ ui, anchor }) => ui.showChip(draft(anchor, { placement: "beside", questionText: "Why do you want to work at Acme Robotics?" })) });
// a stacked form: the field above, the field below and the label sit within a chip's height of the focused field. Above covers the
// previous field and the label, below covers the next one: the chip goes beside the field.
add({ id: "chip-avoid-previous-and-label", group: "chip", field: "fx-pair-a", expect: { placement: "beside", noCover: ["fx-pair-b"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
    questionText: QUESTION, placement: "above",
    avoid: [pageRect("fx-pair-b"), { x: anchor.x, y: anchor.y - 56, w: anchor.w, h: 50 }, { x: anchor.x, y: anchor.y - 22, w: 200, h: 18 }],
  })) });

// ---- chip: the recommended option of a choice control (CPL-05) -------------------------------------------------------------------
const union = (ids: string[]): Anchor => {
  const rects = ids.map(pageRect);
  const x = Math.min(...rects.map((r) => r.x)); const y = Math.min(...rects.map((r) => r.y));
  return { x, y, w: Math.max(...rects.map((r) => r.x + r.w)) - x, h: Math.max(...rects.map((r) => r.y + r.h)) - y };
};
const radioView = (over: Partial<ChipView> = {}): ChipView => chipView(union(["fx-opt-yes", "fx-opt-no"]), {
  fieldLabel: "Will you now or in the future require visa sponsorship?", questionText: "Will you now or in the future require visa sponsorship?",
  operation: "CHECK_RADIO", value: "No", optionLabel: "No", optionAnchor: pageRect("fx-opt-no"), ...over,
});
add({ id: "chip-option-radio", group: "chip", field: "fx-opt-yes", expect: { tone: "verified", options: 1, has: ["option-mark"] },
  setup: ({ ui }) => ui.showChip(radioView()) });
add({ id: "chip-option-checkboxes", group: "chip", field: "fx-opt-a", expect: { tone: "verified", options: 2 },
  setup: ({ ui }) => ui.showChip(chipView(union(["fx-opt-a", "fx-opt-b"]), {
    fieldLabel: "Which languages do you speak?", operation: "SET_CHECKBOXES", value: "English, Spanish", optionLabel: "English, Spanish",
    optionAnchor: pageRect("fx-opt-a"), optionAnchors: [pageRect("fx-opt-a"), pageRect("fx-opt-b")],
  })) });
add({ id: "chip-option-protected", group: "chip", field: "fx-opt-yes", expect: { tone: "locked", options: 1 },
  setup: ({ ui }) => ui.showChip(radioView({ insert: "explicit", tone: "locked", whyCode: "policy", whySummary: "Follows your saved preferences", keys: ["enter", "shift-tab", "esc"] })) });
add({ id: "chip-option-after-insert", group: "chip", field: "fx-opt-yes", expect: { options: 0, has: ["chip-undo"] },
  setup: ({ ui }) => ui.showChip(radioView({ undoable: true })) });
add({ id: "chip-option-needs-you", group: "chip", field: "fx-opt-yes", expect: { quiet: true, options: 0 },
  setup: ({ ui }) => ui.showChip(radioView({ state: "NEEDS_YOU", tone: "neutral", insert: "never", value: "", operation: null, reasonCode: "no_matching_option" })) });

// ---- chip: a skipped file field says why, in one calm sentence (CPL-06) ----------------------------------------------------------------
const SKIPS: [string, "file_unapproved" | "file_cover_letter" | "file_none" | "generic", string, string][] = [
  ["unapproved", "file_unapproved", "CV not approved yet", "CV aún sin aprobar"],
  ["cover-letter", "file_cover_letter", "Yours to upload", "La subes tú"],
  ["none", "file_none", "No CV in Meridian yet", "Aún no hay CV en Meridian"],
  ["generic", "generic", "Yours to upload", "La subes tú"],
];
for (const [slug, code, en, es] of SKIPS) {
  const withButton = code === "file_unapproved" || code === "file_none";
  add({ id: `chip-skip-file-${slug}`, group: "chip", field: "fx-top", responsive: slug === "unapproved", expect: { tone: "neutral", quiet: true, lacks: ["chip-accept", ...(withButton ? [] : ["chip-open-meridian"])], has: withButton ? ["chip-open-meridian"] : [], text: { "chip-label": { en, es } } },
    setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
      fieldLabel: "Resume / CV", questionText: slug === "cover-letter" ? "Cover letter" : "Resume / CV", state: "SKIP", tone: "neutral", insert: "never", value: "", operation: null,
      whyCode: "", whySummary: "", skipReason: { code, text: "Meridian only attaches your approved CV to a CV upload." }, keys: ["esc"],
    })) });
}
add({ id: "chip-skip-file-unknown-code", group: "chip", field: "fx-top", expect: { quiet: true, text: { "chip-reason": { en: "A reason the server worded itself.", es: "A reason the server worded itself." } } },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
    fieldLabel: "Portfolio", state: "SKIP", tone: "neutral", insert: "never", value: "", operation: null, whyCode: "", whySummary: "",
    skipReason: { code: "brand_new" as never, text: "A reason the server worded itself." }, keys: ["esc"],
  })) });

// ---- chip: a draft written for older or other context (CPL-09), a value that is already in the field (UX-09) ---------------------------
const STALE: [string, "research" | "profile" | "company-rejected", boolean][] = [
  ["research", "research", false], ["profile", "profile", true], ["company-rejected", "company-rejected", true],
];
for (const [slug, reason, blocks] of STALE) {
  add({ id: `chip-stale-${slug}`, group: "chip", field: "fx-text", responsive: slug === "company-rejected", expect: {
      tone: "draft", rich: true, has: ["chip-stale", "chip-rewrite"], lacks: blocks ? ["chip-accept", "hint-tab"] : [], ...(blocks ? {} : { has: ["chip-stale", "chip-rewrite", "chip-accept"] }),
    },
    setup: ({ ui, anchor }) => ui.showChip(draft(anchor, {
      questionText: "Why do you want to work at Acme Robotics?", staleReason: reason, staleBlocksInsert: blocks, insert: blocks ? "never" : "tab",
      keys: blocks ? ["shift-tab", "regenerate", "esc"] : ["tab", "shift-tab", "regenerate", "esc"],
    })) });
}
add({ id: "chip-already-inserted", group: "chip", field: "fx-top", expect: { tone: "verified", has: ["chip-already"], lacks: ["chip-accept", "chip-undo"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { alreadyInserted: true, keys: ["shift-tab", "esc"], questionText: QUESTION })) });
add({ id: "chip-pill-typing", group: "chip", field: "fx-top", typing: { id: "fx-top", text: "alex" }, expect: { tone: "verified", lacks: ["chip-accept", "chip-why"], has: ["chip-pill-text"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: QUESTION, conflictsWithCurrent: true })) });

// ---- chip: text that reads right to left -----------------------------------------------------------------------------------
add({ id: "chip-bidi-data", group: "chip", field: "fx-top", responsive: true, expect: { tone: "verified" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "الاسم الكامل", value: "علي أحمد المنصوري", whyCode: "unknown_code", whySummary: "من ملفك الشخصي الموثق",
    sources: [{ label: "عن الشركة — نبذة", url: "https://acme-robotics.example/about" }] })) });
add({ id: "chip-bidi-mixed", group: "chip", field: "fx-top", expect: { tone: "verified" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { fieldLabel: "טלפון", value: "+972 54-123-4567 (נייד)", whyCode: "unknown_code", whySummary: "מהפרופיל המאומת שלך" })) });

// ---- persistent field markers ------------------------------------------------------------------------------------------------
const mark = (anchor: Anchor, tone: Tone, over: Partial<FieldMark> = {}): FieldMark => ({ key: "f0:5", anchor, tone, ...over });
add({ id: "mark-inferred", group: "mark", field: "fx-top", responsive: true, zoom: true, expect: { marks: { count: 1, tones: ["inferred"] }, lacks: ["chip"] },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "inferred", { label: "Years of experience" })]) });
add({ id: "mark-draft", group: "mark", field: "fx-text", responsive: true, expect: { marks: { count: 1, tones: ["draft"] } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "draft", { label: "Why do you want to work at Acme Robotics?" })]) });
add({ id: "mark-tones", group: "mark", field: "fx-top", expect: { marks: { count: 5, tones: ["inferred", "draft", "verified", "error", "neutral"] } },
  setup: ({ ui }) => ui.setMarks?.([
    mark(pageRect("fx-top"), "inferred", { key: "a" }), mark(pageRect("fx-mid"), "draft", { key: "b" }), mark(pageRect("fx-bottom"), "verified", { key: "c" }),
    mark(pageRect("fx-right"), "error", { key: "d" }), mark(pageRect("fx-text"), "neutral", { key: "e" }),
  ]) });
add({ id: "mark-right-edge", group: "mark", field: "fx-right", responsive: true, expect: { marks: { count: 1, tones: ["inferred"] } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "inferred")]) });
add({ id: "mark-top-edge", group: "mark", field: "fx-edge", expect: { marks: { count: 1, tones: ["draft"] } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "draft")]) });
add({ id: "mark-fills-viewport", group: "mark", field: "fx-tall", expect: { marks: { count: 1, tones: ["draft"] } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "draft")]) });
add({ id: "mark-small-control", group: "mark", field: "fx-small", expect: { marks: { count: 1, tones: ["inferred"] } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "inferred", { label: "I have a driving licence" })]) });
add({ id: "mark-with-chip", group: "mark", field: "fx-top", expect: { tone: "inferred", marks: { count: 2, tones: ["inferred", "draft"] } },
  setup: ({ ui, anchor }) => {
    ui.setMarks?.([mark(anchor, "inferred"), mark(pageRect("fx-text"), "draft", { key: "other" })]);
    ui.showChip(chipView(anchor, { state: "INFERRED", tone: "inferred", value: "6", fieldLabel: "Years of experience", whyCode: "derived", whySummary: "Worked out from your profile" }));
  } });
add({ id: "mark-hostile-label", group: "mark", field: "fx-top", expect: { marks: { count: 1 } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "inferred", { label: "<img src=x onerror=window.__pwned=1>" + "L".repeat(120) })]) });
add({ id: "mark-bidi-label", group: "mark", field: "fx-top", expect: { marks: { count: 1 } },
  setup: ({ ui, anchor }) => ui.setMarks?.([mark(anchor, "draft", { label: "لماذا تريد العمل معنا؟" })]) });

// ---- matrix: every SuggestionState x tone ---------------------------------------------------------------------------------
const STATES: SuggestionState[] = ["SKIP", "NOT_APPLICABLE", "SENSITIVE_BLANK", "CONSENT", "GENERATING", "GENERATED_DRAFT", "VERIFIED", "INFERRED", "NEEDS_YOU"];
const TONES: Tone[] = ["verified", "inferred", "draft", "neutral", "locked", "busy", "error"];
for (const state of STATES) {
  for (const tone of TONES) {
    const ready = state === "VERIFIED" || state === "INFERRED" || state === "GENERATED_DRAFT";
    add({ id: `matrix-${state.toLowerCase()}-${tone}`, group: "matrix", field: "fx-top", expect: state === "SKIP" ? { noChip: true } : {},
      setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, {
        state, tone, value: ready ? "Sample value" : "", insert: ready ? "tab" : "never", operation: ready ? "TYPE_TEXT" : null,
        generation: state === "GENERATING" ? { status: "GENERATING" } : undefined, canDraft: state === "NEEDS_YOU",
        reasonCode: state === "NEEDS_YOU" ? "missing_fact" : undefined, canRegenerate: state === "GENERATED_DRAFT",
      })) });
  }
}

// ---- panel -------------------------------------------------------------------------------------------------------------------
const panelScene = (id: string, over: Partial<PanelView> | (() => Partial<PanelView>), extra: Partial<Scene> = {}): void => {
  add({ id, group: "panel", panel: true, responsive: true, setup: ({ ui }) => ui.setPanel(panelView(typeof over === "function" ? over() : over)), ...extra });
};
panelScene("panel-ready-20", { pendingDrafts: 2 }, { expect: { has: ["panel-fill-verified", "progress-list", "panel-research", "panel-drafts", "panel-spend", "panel-keys"] } });
panelScene("panel-ready-6", () => {
  const items = progressItems(6);
  return { progress: { total: 6, done: 3, items }, fillableCount: 2 };
}, { expect: { has: ["progress-item"] } });
panelScene("panel-nothing-fillable", { fillableCount: 0 }, { expect: { has: ["panel-fill-verified"] } });
panelScene("panel-collapsed", { collapsed: true }, { expect: { has: ["panel-pill"], lacks: ["panel-card"] } });
panelScene("panel-collapsed-no-form", { collapsed: true, noForm: true, progress: { total: 0, done: 0, items: [] } }, { expect: { has: ["panel-pill"] } });
panelScene("panel-collapsed-offline", { collapsed: true, connection: conn("no_bridge") }, { expect: { has: ["panel-pill"] } });
panelScene("panel-more-open", {}, { clicks: ["panel-more"], expect: { has: ["panel-more-box", "panel-always-on", "panel-hide-hint", "panel-deactivate", "panel-dock-left"] } });
panelScene("panel-more-no-site", () => ({ site: undefined }), { clicks: ["panel-more"], expect: { has: ["panel-more-box"], lacks: ["panel-always-on"] } });
panelScene("panel-dock-left", {}, { clicks: ["panel-more", "panel-dock-left"] });
panelScene("panel-keys-paused", { keysPaused: true }, { expect: { has: ["panel-keys"] } });
panelScene("panel-applied", { job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "applied" } }, { expect: { text: { "panel-sub": { en: "Already recorded", es: "Ya registrada" } } } });
panelScene("panel-long-names", { job: { company: "Extraordinarily Long Company Name International Holdings Limited", title: "Principal Staff Distinguished Software Engineer, Platform Reliability and Developer Experience", location: "", tracked: "none" } });
panelScene("panel-no-job", { job: null });
panelScene("panel-hostile-strings", () => {
  const items = progressItems(5).map((item, i) => ({ ...item, label: i === 0 ? "<img src=x onerror=window.__pwned=1>Name" : item.label }));
  return {
    job: { company: "<b>Evil</b> Corp", title: "<script>window.__pwned=2</script>", location: "", tracked: "none" },
    progress: { total: 5, done: 1, items },
    research: researchView({ sources: [{ label: "<u>x</u>", url: "javascript:window.__pwned=3", status: "ok" }, { label: "ok", url: "https://example.test/", status: "ok" }] }),
  };
});
panelScene("panel-update-ready", () => ({ connection: { ...conn("ready"), extensionOutdated: true } }), { expect: { has: ["calm-update"] } });
panelScene("panel-profile-empty", { profileEmpty: true, fillableCount: 0 }, { expect: { has: ["calm-profile-empty"] } });
panelScene("panel-drafts-unavailable", { generation: { available: false, budgetExhausted: false, message: "No model" } }, { expect: { has: ["note-drafts"] } });
panelScene("panel-budget-reached", { generation: { available: true, budgetExhausted: true, spentUsd: 10, limitUsd: 10 } }, { expect: { has: ["note-budget"], lacks: ["note-drafts"] } });
panelScene("panel-cross-origin", { permissions: { allSites: false, crossOriginFrames: 2 } }, { expect: { has: ["note-cross-origin", "note-allow-all"] } });
panelScene("panel-all-notes", { profileEmpty: true, generation: { available: false, budgetExhausted: false }, permissions: { allSites: false, crossOriginFrames: 1 }, connection: { ...conn("ready"), extensionOutdated: true } });
panelScene("panel-no-form", { noForm: true, progress: { total: 0, done: 0, items: [] }, research: researchView({ state: "none", company: "", sources: [], quality: "none" }), job: null }, { expect: { has: ["calm-no-form"], lacks: ["progress-list"] } });
// the controller parks an orphaned script in phase `error`; the header must not call that a connection problem
panelScene("panel-orphaned", { orphaned: true, connection: conn("error", { detail: "orphaned" }) }, {
  expect: {
    has: ["calm-orphaned", "calm-reload"], lacks: ["progress-list", "panel-footer", "calm-error", "calm-retry"],
    text: { "panel-sub": { en: "Needs a page reload", es: "Hay que recargar la página" } },
  },
});
panelScene("panel-pill-expand-from-orphan", { orphaned: true, collapsed: true, connection: conn("error", { detail: "orphaned" }) }, { expect: { has: ["panel-pill"] } });

// connection phases
const PHASES: [ConnectionPhase, Partial<PanelView["connection"]>][] = [
  ["no_bridge", {}], ["unpaired", {}], ["revoked", {}], ["proof_failed", {}], ["disabled", {}], ["restoring", {}], ["error", { detail: "ECONNRESET" }],
];
for (const [phase, extra] of PHASES) {
  panelScene(`panel-conn-${phase}`, { connection: conn(phase, extra) }, { expect: { has: [`calm-${phase}`], lacks: ["progress-list", "panel-fill-verified", "panel-footer"] } });
}
panelScene("panel-conn-unpaired-port", { connection: conn("unpaired") },
  { expect: { has: ["calm-unpaired", "calm-connect"], text: { "calm-unpaired": { en: "answered on port 47615", es: "respondido en el puerto 47615" } } } });
panelScene("panel-conn-unpaired-noport", { connection: { phase: "unpaired" } }, { expect: { has: ["calm-unpaired", "calm-connect"], lacks: ["calm-ambiguous"] } });
// two or more programs answer like Meridian: no Connect that cannot work, a calm explanation and one Try again
for (const [name, phase] of [["ambiguous", "unpaired"], ["ambiguous-revoked", "revoked"]] as const) {
  panelScene(`panel-conn-${name}`, { connection: conn(phase, { port: undefined, meridianPorts: [47615, 47616] }) }, {
    zoom: name === "ambiguous",
    expect: {
      has: ["calm-ambiguous", "calm-retry"], lacks: ["calm-connect", "calm-unpaired", "calm-revoked", "progress-list", "panel-fill-verified", "panel-footer"],
      text: {
        "calm-ambiguous": { en: "More than one program on this Mac says it is Meridian", es: "Más de un programa de este Mac dice ser Meridian" },
        "panel-sub": { en: "Connection paused for your safety", es: "Conexión en pausa por tu seguridad" },
      },
    },
  });
}
panelScene("panel-conn-ambiguous-three", { connection: conn("unpaired", { port: undefined, meridianPorts: [47615, 47616, 47618] }) },
  { expect: { has: ["calm-ambiguous"], text: { "calm-ambiguous": { en: "47615, 47616, 47618", es: "47615, 47616, 47618" } } } });
panelScene("panel-collapsed-ambiguous", { collapsed: true, connection: conn("unpaired", { port: undefined, meridianPorts: [47615, 47616] }) }, { expect: { has: ["panel-pill"] } });
// one answering program is the normal case, whatever else the list holds
panelScene("panel-conn-single-meridian", { connection: conn("unpaired", { meridianPorts: [47615] }) }, { noShot: true, expect: { has: ["calm-unpaired", "calm-connect"], lacks: ["calm-ambiguous"] } });
// the list comes from the worker, but a number shown to the user is checked: junk entries do not make a second program
panelScene("panel-conn-ambiguous-junk", () => ({ connection: conn("unpaired", { meridianPorts: [47615, 0, 70000, 1.5, "x" as unknown as number, 47615] }) }),
  { noShot: true, expect: { has: ["calm-unpaired", "calm-connect"], lacks: ["calm-ambiguous"] } });
panelScene("panel-conn-connecting", { connection: conn("connecting") }, { zoom: true, expect: { has: ["calm-connecting", "spinner"], lacks: ["progress-list", "panel-fill-verified", "panel-footer", "calm-retry"], text: { "calm-connecting": { en: "Connecting to Meridian…", es: "Conectando con Meridian…" } } } });
panelScene("panel-collapsed-connecting", { collapsed: true, connection: conn("connecting") }, { expect: { has: ["panel-pill"] } });
panelScene("panel-bidi-job", () => {
  const items = progressItems(6).map((item, i) => ({ ...item, label: ["الاسم الأول", "اسم العائلة", "البريد الإلكتروني", "מספר טלפון", "Current location", "ملف لينكدإن"][i] ?? item.label, section: i < 3 ? "المعلومات الشخصية" : "Links" }));
  return { job: { company: "شركة الأفق للروبوتات", title: "مهندس برمجيات أول", location: "الرياض", tracked: "none" }, progress: { total: 6, done: 3, items }, research: researchView({ company: "شركة الأفق للروبوتات", site: "ofoq.example" }) };
}, { expect: { has: ["progress-item"] } });
panelScene("panel-conn-pairing", () => ({ connection: conn("pairing", { pairing: { pairingId: "p1", code: "482913", expiresAt: Date.now() + 118_000 } }) }),
  { expect: {
    has: ["calm-pairing", "pairing-code", "pairing-countdown", "pairing-help", "pairing-port", "pairing-nowindow", "calm-open-meridian", "calm-cancel"],
    text: {
      "calm-pairing": { en: "Meridian itself must open a small window", es: "Meridian debe abrir por sí mismo una ventana" },
      "pairing-port": { en: "Meridian answered on port 47615.", es: "Meridian ha respondido en el puerto 47615." },
      "pairing-nowindow": { en: "Open Meridian from the menu bar", es: "Abre Meridian desde la barra de menús" },
      "pairing-countdown": { en: "Expires in 1:", es: "Caduca en 1:" },
    },
  } });
// no port known (a state the worker published without one): the port sentence is left out, the rest stays
panelScene("panel-conn-pairing-noport", () => ({ connection: { phase: "pairing", pairing: { pairingId: "p1", code: "482913", expiresAt: Date.now() + 118_000 } } }),
  { expect: { has: ["calm-pairing", "pairing-code", "pairing-countdown", "pairing-nowindow", "calm-cancel"], lacks: ["pairing-port"] } });
// a port that is not a port is never shown
panelScene("panel-conn-pairing-badport", () => ({ connection: conn("pairing", { port: 99999, pairing: { pairingId: "p1", code: "482913", expiresAt: Date.now() + 118_000 } }) }),
  { noShot: true, expect: { has: ["calm-pairing", "pairing-nowindow"], lacks: ["pairing-port"] } });
panelScene("panel-conn-pairing-expiring", () => ({ connection: conn("pairing", { pairing: { pairingId: "p1", code: "007120", expiresAt: Date.now() + 4_000 } }) }), { noShot: true, expect: { has: ["pairing-code"] } });
panelScene("panel-conn-pairing-expired", () => ({ connection: conn("pairing", { pairing: { pairingId: "p1", code: "482913", expiresAt: Date.now() - 1_000 } }) }),
  { expect: { has: ["calm-pairing-expired", "calm-new-code"], lacks: ["pairing-code"] } });

// research strip
const R = (over: Partial<ResearchView>): Partial<PanelView> => ({ research: researchView(over) });
panelScene("panel-research-queued", R({ state: "queued", quality: "none", sources: [], site: null }), { expect: { has: ["research-headline"] } });
panelScene("panel-research-running", R({ state: "running", quality: "none", sources: researchView().sources.slice(0, 2), site: null }), { expect: { has: ["research-headline"] } });
panelScene("panel-research-running-nocompany", R({ state: "running", quality: "none", sources: [], company: "", site: null }));
panelScene("panel-research-done-full", R({}), { expect: { has: ["research-count", "research-toggle", "research-site"], lacks: ["research-quality"] } });
panelScene("panel-research-done-job-only", R({ quality: "job_only", site: null, sources: researchView().sources.slice(0, 1) }));
panelScene("panel-research-done-company-only", R({ quality: "company_only" }), { expect: { has: ["research-quality"] } });
panelScene("panel-research-done-minimal", R({ quality: "minimal", sources: [], site: null }));
panelScene("panel-research-partial", R({ state: "partial", quality: "company_only" }));
panelScene("panel-research-failed", R({ state: "failed", quality: "none", sources: [], site: null }));
panelScene("panel-research-cancelled", R({ state: "cancelled", quality: "none", sources: [], site: null }));
panelScene("panel-research-needs-company", R({ state: "needs_input", quality: "none", needsCompany: true, company: "", sources: [], site: null }), { expect: { has: ["research-form-company", "research-input-company"] } });
panelScene("panel-research-needs-site", R({ state: "needs_input", quality: "none", needsSite: true, sources: [], site: null }), { expect: { has: ["research-form-site"] } });
panelScene("panel-research-sources-open", R({}), { clicks: ["research-toggle"], expect: { has: ["research-sources", "research-source"] } });
panelScene("panel-research-form-company", R({}), { clicks: ["research-not-this"], expect: { has: ["research-form-company"] } });
panelScene("panel-research-form-site", R({}), { clicks: ["research-add-site"], expect: { has: ["research-form-site"] } });
panelScene("panel-research-none", R({ state: "none", company: "", sources: [], quality: "none", site: null }), { expect: { lacks: ["panel-research"] } });


// ---- panel: duplicate banner, first use, end-of-form review, key cheat sheet, tracker items, research without a surprise form (wave 3) --
const daysAgo = (n: number): string => new Date(Date.now() - n * 86_400_000).toISOString();
panelScene("panel-duplicate-applied", () => ({ job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "applied" }, duplicate: { state: "applied", since: daysAgo(3) } }),
  { zoom: true, expect: { has: ["panel-duplicate", "dup-continue", "dup-close"], text: { "dup-title": { en: "You already applied to this job", es: "Ya enviaste tu candidatura a esta oferta" }, "dup-hint": { en: "You applied on", es: "Enviaste tu candidatura el" } } } });
panelScene("panel-duplicate-nodate", () => ({ job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "applied" }, duplicate: { state: "applied" } }),
  { expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "Meridian has it recorded as applied", es: "Meridian la tiene registrada como enviada" } } } });
panelScene("panel-duplicate-preparing", () => ({ job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "preparing" }, duplicate: { state: "preparing", since: daysAgo(10) } }),
  { expect: { has: ["panel-duplicate", "dup-continue"], text: { "dup-title": { en: "This job is already in your Tracker", es: "Esta oferta ya está en tu Tracker" } } } });
panelScene("panel-duplicate-badsince", { duplicate: { state: "applied", since: "<b>not a date</b>" } }, { noShot: true, expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "Meridian has it recorded as applied", es: "Meridian la tiene registrada como enviada" } } } });
panelScene("panel-duplicate-collapsed", { collapsed: true, duplicate: { state: "applied", since: daysAgo(3) } }, { expect: { has: ["panel-pill", "pill-dup"] } });
panelScene("panel-first-use", { firstUse: true }, { zoom: true, expect: { has: ["panel-first-use", "first-steps", "first-dismiss", "first-keys"] } });
panelScene("panel-review", () => {
  const items = progressItems();
  return { review: { missingRequired: 8, items: [11, 13, 14, 15, 16].map((i) => ({ key: items[i].key, label: items[i].label })) } };
}, { zoom: true, expect: { has: ["panel-review", "review-list", "review-item", "review-dismiss", "review-more"], text: { "review-title": { en: "8 required questions are still empty", es: "Faltan 8 preguntas obligatorias por responder" } } } });
panelScene("panel-review-one", () => ({ review: { missingRequired: 1, items: [{ key: "f0:11", label: "Notice period" }] } }),
  { expect: { has: ["panel-review"], lacks: ["review-more"], text: { "review-title": { en: "1 required question is still empty", es: "Falta 1 pregunta obligatoria por responder" } } } });
panelScene("panel-batch-undo", { batchUndo: { count: 5 } }, { zoom: true, expect: { has: ["panel-batch-undo", "panel-undo-all"], text: { "batch-count": { en: "5 answers inserted", es: "5 respuestas insertadas" } } } });
panelScene("panel-drafts-unknown", { generation: { available: false, known: false, budgetExhausted: false } }, { expect: { lacks: ["note-drafts"] } });
panelScene("panel-help", {}, { clicks: ["panel-help"], zoom: true, expect: { has: ["help-sheet", "help-keys", "help-back", "help-row"], lacks: ["progress-list", "panel-fill-verified"] } });
panelScene("panel-more-track", {}, { clicks: ["panel-more"], expect: { has: ["panel-track-save", "panel-track-applied", "panel-deactivate"] } });
panelScene("panel-more-track-prospective", () => ({ job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "prospective" } }),
  { clicks: ["panel-more"], expect: { has: ["panel-track-applied"], lacks: ["panel-track-save"] } });
panelScene("panel-more-track-applied", () => ({ job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "applied" } }),
  { clicks: ["panel-more"], expect: { has: ["panel-deactivate"], lacks: ["panel-track-save", "panel-track-applied"] } });
// the website could not be confirmed: a sentence and a link, not a form that opens by itself
panelScene("panel-research-site-unconfirmed", R({ state: "partial", quality: "company_only", needsSite: true, site: null }),
  { expect: { has: ["research-need", "research-add-site"], lacks: ["research-form-site"], text: { "research-need": { en: "I couldn't confirm Acme Robotics's website. Add it?", es: "No he podido confirmar la web de Acme Robotics. ¿La añades?" } } } });
panelScene("panel-research-privacy", R({}), { clicks: ["research-toggle"], expect: { has: ["research-privacy"], text: { "research-privacy": { en: "Meridian itself reads these public pages", es: "Meridian lee estas páginas públicas por sí mismo" } } } });

// ---- wave 4: choice groups whose options run past their controls, the folded pill, duplicates by status, the fill confirmation -----------------------
// the chip's anchor is the controls; every option (control + printed label) is in `avoid`, as the logic sends them
const groupView = (inputs: string[], options: string[], recommended: string, over: Partial<ChipView> = {}): ChipView => chipView(union(inputs), {
  fieldLabel: "Will you now or in the future require visa sponsorship?", questionText: "Will you now or in the future require visa sponsorship?",
  operation: "CHECK_RADIO", value: "No", optionLabel: "No", optionAnchor: pageRect(recommended), placement: "beside",
  avoid: [pageRect("fxg-next"), ...options.map(pageRect), pageRect("fxg-prev")], ...over,
});
add({ id: "chip-group-row", group: "chip", field: "fxg-h-i1", responsive: true, expect: { tone: "verified", options: 1, noCover: ["fxg-h-o1", "fxg-h-o2"] },
  setup: ({ ui }) => ui.showChip(groupView(["fxg-h-i1", "fxg-h-i2"], ["fxg-h-o1", "fxg-h-o2"], "fxg-h-i2")) });
add({ id: "chip-group-column", group: "chip", field: "fxg-v-i1", expect: { tone: "verified", options: 2, noCover: ["fxg-v-o1", "fxg-v-o2", "fxg-v-o3"] },
  setup: ({ ui }) => ui.showChip(chipView(union(["fxg-v-i1", "fxg-v-i2", "fxg-v-i3"]), {
    fieldLabel: "Which languages do you speak?", questionText: "Which languages do you speak?", operation: "SET_CHECKBOXES", value: "English, Spanish", optionLabel: "English, Spanish",
    optionAnchor: pageRect("fxg-v-i1"), optionAnchors: [pageRect("fxg-v-i1"), pageRect("fxg-v-i2")], placement: "beside",
    avoid: [pageRect("fxg-v-o1"), pageRect("fxg-v-o2"), pageRect("fxg-v-o3")],
  })) });
add({ id: "chip-group-touch", group: "chip", responsive: true, field: "fxg-t-i1", expect: { tone: "verified", noCover: ["fxg-t-o1", "fxg-t-o2"] },
  setup: ({ ui }) => ui.showChip(groupView(["fxg-t-i1", "fxg-t-i2"], ["fxg-t-o1", "fxg-t-o2"], "fxg-t-i2", { placement: "below", avoid: [pageRect("fxg-t-o1"), pageRect("fxg-t-o2")] })) });
// a choice group the logic did not describe (an older logic): never beside, where the labels are unknown
add({ id: "chip-group-unlabelled", group: "chip", field: "fxg-h-i1", expect: { tone: "verified", noCover: ["fxg-h-o1", "fxg-h-o2"] },
  setup: ({ ui }) => ui.showChip(groupView(["fxg-h-i1", "fxg-h-i2"], [], "fxg-h-i2", { avoid: [pageRect("fxg-next")] })) });

// no side is clean (a panel on the right, a field above and a field below): the chip folds to one line and says which key inserts it
// (in a narrow window the panel is a bottom sheet and the field is the rig's own, with a neighbour above and below it)
const foldAnchor = (field: Anchor): Anchor => (window.innerWidth >= 700 ? { x: 300, y: 300, w: 260, h: 34 } : field);
const foldAvoid = (a: Anchor): Anchor[] => [{ x: a.x, y: a.y + 74, w: a.w, h: a.h }, { x: a.x, y: a.y - 74, w: a.w, h: a.h }];
add({ id: "chip-fold-pill", group: "chip", responsive: true, panel: true, field: "fx-top", expect: { tone: "verified", has: ["chip-pill-text", "chip-pill-key", "chip-accept", "hint-tab"], lacks: ["chip-why"] },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView()); const at = foldAnchor(anchor); ui.showChip(chipView(at, { fieldLabel: "Email address", questionText: QUESTION, placement: "beside", avoid: foldAvoid(at) })); } });
add({ id: "chip-fold-pill-inferred", group: "chip", panel: true, field: "fx-top", expect: { tone: "inferred", has: ["chip-pill-text", "chip-pill-key"] },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView()); const at = foldAnchor(anchor); ui.showChip(chipView(at, { fieldLabel: "Years of experience", state: "INFERRED", tone: "inferred", value: "6", whyCode: "derived", placement: "beside", avoid: foldAvoid(at) })); } });
// a protected answer is where Tab does not insert: it keeps the note that says why and the Enter key
add({ id: "chip-fold-never-protected", group: "chip", panel: true, field: "fx-top", expect: { tone: "locked", has: ["chip-protected", "hint-enter", "chip-accept"], lacks: ["chip-pill-text", "hint-tab"] },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView()); const at = foldAnchor(anchor); ui.showChip(chipView(at, { fieldLabel: "Veteran status", insert: "explicit", tone: "locked", value: "I am not a protected veteran", whyCode: "policy", keys: ["enter", "shift-tab", "esc"], placement: "beside", avoid: foldAvoid(at) })); } });
add({ id: "chip-fold-pill-conflict", group: "chip", responsive: true, panel: true, field: "fx-top", expect: { tone: "verified", has: ["chip-pill-text", "chip-replace"] },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView()); const at = foldAnchor(anchor); ui.showChip(chipView(at, { fieldLabel: "Email address", conflictsWithCurrent: true, placement: "beside", avoid: foldAvoid(at) })); } });
// a draft is worth reading: it is never folded, it goes where it covers least
add({ id: "chip-fold-never-a-draft", group: "chip", panel: true, field: "fx-text", expect: { tone: "draft", rich: true, lacks: ["chip-pill-text"] },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView()); const at = foldAnchor(anchor); ui.showChip(draft(at, { avoid: foldAvoid(at) })); } });
add({ id: "chip-duplicate-blocked", group: "chip", field: "fx-top", responsive: true, expect: { tone: "verified", has: ["chip-duplicate"], lacks: ["chip-accept", "chip-replace"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: QUESTION, insert: "never", duplicateBlocksInsert: true, reasonCode: "duplicate", keys: ["shift-tab", "esc"] })) });

const dupPanel = (status: NonNullable<PanelView["duplicate"]>["status"], since?: string): Partial<PanelView> => ({
  job: { company: "Acme Robotics", title: "Senior Software Engineer", location: "London, UK", tracked: "applied" }, duplicate: { state: "applied", status, since },
});
panelScene("panel-duplicate-rejected", () => dupPanel("rejected", daysAgo(20)), { expect: { has: ["panel-duplicate", "dup-continue"], text: { "dup-hint": { en: "it was rejected", es: "fue rechazada" } } } });
panelScene("panel-duplicate-withdrawn", () => dupPanel("withdrawn", daysAgo(20)), { expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "then withdrew it", es: "luego la retiraste" } } } });
panelScene("panel-duplicate-ghosted", () => dupPanel("ghosted"), { expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "recorded as applied, with no answer", es: "sin respuesta" } } } });
panelScene("panel-duplicate-interview", () => dupPanel("interview", daysAgo(9)), { noShot: true, expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "You applied on", es: "Enviaste tu candidatura el" } } } });

// the end-of-form reminder with long questions, and the cards folded to their titles
const LONG_QUESTIONS = [
  "Are you legally authorised to work in the United Kingdom, now or at any time in the future?",
  "Will you now or in the future require sponsorship for employment visa status (for example, Skilled Worker)?",
  "I consent to Acme Robotics processing my personal data for the purposes of this recruitment process",
  "I have read and agree to the privacy notice, the data retention policy and the candidate terms of use",
  "Please describe any prior employment, secondment or contracting relationship with Acme Robotics or its subsidiaries",
];
const longReview = (): Partial<PanelView> => ({ review: { missingRequired: 7, items: LONG_QUESTIONS.map((label, i) => ({ key: `f0:${20 + i}`, label })) } });
panelScene("panel-review-long", longReview, { zoom: true, expect: { has: ["panel-review", "review-item", "review-more", "review-fold"] } });
panelScene("panel-review-folded", longReview, { clicks: ["review-fold"], expect: { has: ["panel-review", "review-fold", "review-title"], lacks: ["review-item"] } });
panelScene("panel-review-everything", () => ({
  ...longReview(), profileEmpty: true, generation: { available: false, budgetExhausted: false }, permissions: { allSites: false, crossOriginFrames: 1 }, connection: { ...conn("ready"), extensionOutdated: true },
}), { expect: { has: ["panel-review", "panel-notices", "panel-fill-verified"] } });
panelScene("panel-first-use-folded", { firstUse: true }, { clicks: ["first-fold"], expect: { has: ["panel-first-use", "first-fold"], lacks: ["first-steps"] } });

// "Fill verified" asks first, with the whole list
const PREVIEW_ITEMS = [
  { key: "f0:0", label: "First name", value: "Alex" }, { key: "f0:1", label: "Last name", value: "Newport" }, { key: "f0:2", label: "Email", value: "alex.newport@example.com" },
  { key: "f0:3", label: "Phone", value: "+44 7700 900123" }, { key: "f0:5", label: "LinkedIn profile", value: "https://www.linkedin.com/in/alex-newport-0123456789abcdef" },
];
panelScene("panel-fill-preview", () => ({ fillPreview: { count: 5, items: PREVIEW_ITEMS } }), { zoom: true, expect: { has: ["panel-fill-preview", "fill-preview-item", "fill-confirm", "fill-cancel"], lacks: ["panel-fill-verified"] } });
panelScene("panel-fill-preview-more", () => ({ fillPreview: { count: 17, items: PREVIEW_ITEMS } }), { expect: { has: ["panel-fill-preview", "fill-preview-more"] } });
panelScene("panel-fill-preview-one", () => ({ fillPreview: { count: 1, items: PREVIEW_ITEMS.slice(0, 1) } }), { noShot: true, expect: { has: ["fill-confirm"], text: { "fill-preview-title": { en: "Fill 1 answer?", es: "¿Rellenar 1 respuesta?" } } } });
panelScene("panel-more-permission", () => ({ site: { host: "boards.greenhouse.io", alwaysOn: false, hintHidden: false, alwaysOnNeedsPermission: true } }),
  { clicks: ["panel-more"], expect: { has: ["panel-always-on-permission", "panel-hide-hint"], lacks: ["panel-always-on"] } });

// ---- wave 5 (UXFIN): the next fields come first, another CV, paused drafts, the rule that still applies, interview and offer -------------------
// The NEXT field (named by `avoidNext`) is the worst thing a chip can cover after the options of a choice group: a short answer folds to its pill
// and goes ABOVE the field even though that lies on the field's own label and on the field above, because below it would lie on the next field.
// (a rectangle to the right of the field keeps "beside" from being the cleanest side: these scenes are about above and below)
const aheadAvoid = (anchor: Anchor): Anchor[] => [pageRect("fx-pair-b"), { x: anchor.x, y: anchor.y - 22, w: anchor.w, h: 20 }, { x: anchor.x, y: anchor.y - 66, w: anchor.w, h: 34 }, beBlocked(anchor)];
add({ id: "chip-ahead-before-label", group: "chip", field: "fx-pair-a", responsive: true, expect: { placement: "above", noCover: ["fx-pair-b"], has: ["chip-pill-text"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: "Last name", avoid: aheadAvoid(anchor), avoidNext: 1 })) });
// the same rectangles with nothing said about which one is the next field: every rectangle counts the same, and below (the cheaper one) stays
add({ id: "chip-ahead-unnamed", group: "chip", field: "fx-pair-a", expect: { placement: "below" },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { questionText: "Last name", avoid: aheadAvoid(anchor) })) });
// nothing above can hold the chip (the field is at the top of the window): the pill goes below, over the next field, and says which key inserts
add({ id: "chip-ahead-no-room-above", group: "chip", field: "fx-edge", expect: { placement: "below", has: ["chip-pill-text", "chip-pill-key"] },
  setup: ({ ui, anchor }) => ui.showChip(chipView(anchor, { avoid: [{ x: anchor.x, y: anchor.y + 44, w: anchor.w, h: 34 }, beBlocked(anchor)], avoidNext: 1 })) });

const cvChip = (anchor: Anchor, over: Partial<ChipView> = {}): ChipView => chipView(anchor, {
  fieldLabel: "Resume / CV", questionText: "Resume / CV", operation: "ATTACH_FILE", value: "Alex-Newport-CV.pdf", whyCode: "cv_attach",
  alternatives: [{ id: "doc-2", filename: "Alex-Newport-CV-data-engineering.pdf", version: 3 }, { id: "doc-3", filename: "Alex-CV-2025.pdf", version: 2 }], documentKind: "cv", ...over,
});
add({ id: "chip-cv-menu", group: "chip", field: "fx-top", responsive: true, expect: { tone: "verified", has: ["chip-docs", "chip-docs-toggle"], lacks: ["chip-doc-list"] },
  setup: ({ ui, anchor }) => ui.showChip(cvChip(anchor)) });
add({ id: "chip-cv-menu-open", group: "chip", field: "fx-top", responsive: true, clicks: ["chip-docs-toggle"], expect: { tone: "verified", has: ["chip-docs", "chip-doc-list", "chip-doc"] },
  setup: ({ ui, anchor }) => ui.showChip(cvChip(anchor)) });
add({ id: "chip-cv-menu-letter", group: "chip", field: "fx-top", clicks: ["chip-docs-toggle"],
  expect: { has: ["chip-docs-toggle"], text: { "chip-docs-toggle": { en: "Use another cover letter", es: "Usar otra carta de presentación" } } },
  setup: ({ ui, anchor }) => ui.showChip(cvChip(anchor, { documentKind: "cover_letter", fieldLabel: "Cover letter", questionText: "Cover letter" })) });
add({ id: "chip-cv-menu-blocked", group: "chip", field: "fx-top", expect: { has: ["chip-duplicate"], lacks: ["chip-docs", "chip-accept"] },
  setup: ({ ui, anchor }) => ui.showChip(cvChip(anchor, { insert: "never", duplicateBlocksInsert: true, reasonCode: "duplicate", keys: ["shift-tab", "esc"] })) });

panelScene("panel-drafts-paused", { generation: { available: true, budgetExhausted: false, draftsPaused: true } },
  { zoom: true, expect: { has: ["note-drafts-paused"], text: { "note-drafts-paused": { en: "paused automatic drafts on this page", es: "pausado los borradores automáticos" } } } });
panelScene("panel-drafts-paused-and-unavailable", { generation: { available: false, known: true, budgetExhausted: false, draftsPaused: true } }, { noShot: true, expect: { has: ["note-drafts"], lacks: ["note-drafts-paused"] } });
panelScene("panel-more-always-on-rule", () => ({ site: { host: "boards.greenhouse.io", alwaysOn: true, hintHidden: false, alwaysOnNote: { code: "rule", rule: "*.greenhouse.io", text: "The assistant still starts by itself here because of the rule for *.greenhouse.io." } } }),
  { clicks: ["panel-more"], expect: { has: ["panel-always-on", "panel-always-on-rule"], text: { "panel-always-on-rule": { en: "*.greenhouse.io", es: "*.greenhouse.io" } } } });
panelScene("panel-more-always-on-rule-unnamed", () => ({ site: { host: "boards.greenhouse.io", alwaysOn: true, hintHidden: false, alwaysOnNote: { code: "rule", rule: null, text: "A rule still applies." } } }),
  { noShot: true, clicks: ["panel-more"], expect: { has: ["panel-always-on-rule"], text: { "panel-always-on-rule": { en: "Another rule still starts", es: "Otra regla sigue iniciando" } } } });
// a note about a rule is true only while the switch says ON: with it OFF there is nothing to say
panelScene("panel-more-always-on-rule-gone", () => ({ site: { host: "boards.greenhouse.io", alwaysOn: false, hintHidden: false, alwaysOnNote: { code: "rule", rule: "*.greenhouse.io", text: "x" } } }),
  { noShot: true, clicks: ["panel-more"], expect: { has: ["panel-always-on"], lacks: ["panel-always-on-rule"] } });
panelScene("panel-duplicate-offer", () => dupPanel("offer", daysAgo(30)), { expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "received an offer", es: "recibiste una oferta" } } } });
panelScene("panel-duplicate-interview-worded", () => dupPanel("interview", daysAgo(9)), { expect: { has: ["panel-duplicate"], text: { "dup-hint": { en: "interview stage", es: "fase de entrevista" } } } });

// ---- the whole thing on a page ---------------------------------------------------------------------------------------------
add({ id: "combo-page", group: "notice", panel: true, field: "fx-top", fullShot: true, expect: { tone: "verified" },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView({ pendingDrafts: 1 })); ui.showChip(chipView(anchor)); } });
add({ id: "combo-draft-page", group: "notice", panel: true, field: "fx-text", fullShot: true, expect: { tone: "draft" },
  setup: ({ ui, anchor }) => { ui.setPanel(panelView({ collapsed: false })); ui.showChip(chipView(anchor, { key: "f0:30", fieldLabel: "Why Acme?", state: "GENERATED_DRAFT", tone: "draft", value: DRAFT, whyCode: "generated", whySummary: "", sources: SOURCES, canRegenerate: true, review: true, generation: { status: "DONE", wordCount: 164 }, keys: ["tab", "shift-tab", "regenerate", "esc"] })); } });

// ---- notices ---------------------------------------------------------------------------------------------------------------------
const TOASTS: [ToastKind, ToastView["tone"], Record<string, string | number> | undefined][] = [
  ["filled", "ok", { label: "Email address" }], ["filled", "ok", { count: 5 }], ["filled", "ok", undefined], ["undone", "info", { label: "Email address" }],
  ["saved", "ok", { scope: "reuse" }], ["draft_ready", "info", { label: "Why Acme Robotics?" }], ["regenerating", "info", undefined], ["copied", "ok", undefined],
  ["no_suggestion", "info", undefined], ["paused", "info", undefined], ["resumed", "info", undefined], ["connection_lost", "warn", undefined],
  ["connection_back", "ok", undefined], ["error", "warn", { message: "ECONNRESET at 127.0.0.1:47615" }], ["undone", "info", { count: 5 }],
  ["already_recorded", "info", undefined],
];
add({ id: "toast-all", group: "notice", field: "fx-top", panel: true, responsive: true, expect: { has: ["toast"] },
  setup: ({ ui }) => { for (const [kind, tone, params] of TOASTS.slice(0, 3)) ui.toast({ kind, tone, params }); } });
TOASTS.forEach(([kind, tone, params], index) => {
  add({ id: `toast-${index}-${kind}`, group: "notice", expect: { has: ["toast"] }, noShot: index > 3 && kind !== "already_recorded",
    setup: ({ ui }) => ui.toast({ kind, tone, params }) });
});
add({ id: "toast-undoable", group: "notice", field: "fx-top", panel: true, responsive: true, expect: { has: ["toast", "toast-undo"], text: { toast: { en: "5 answers inserted", es: "5 respuestas insertadas" } } },
  setup: ({ ui }) => ui.toast({ kind: "filled", tone: "ok", params: { count: 5 }, undoable: true }) });
const saveView = (over: Partial<SaveAskView> = {}): SaveAskView => ({ key: "f0:7", fieldLabel: "Notice period", valuePreview: "One month, negotiable", allowReuse: true, edited: false, ...over });
add({ id: "ask-save", group: "notice", panel: true, responsive: true, expect: { has: ["save-ask", "save-application", "save-reuse", "save-no", "save-more"] },
  setup: ({ ui }) => { ui.setPanel(panelView()); ui.askSave(saveView()); } });
add({ id: "ask-save-edited-noreuse", group: "notice", panel: true, expect: { has: ["save-ask", "save-only-here"], lacks: ["save-reuse"] },
  setup: ({ ui }) => { ui.setPanel(panelView()); ui.askSave(saveView({ edited: true, allowReuse: false, fieldLabel: "Why do you want to work at Acme Robotics?", valuePreview: "I'm drawn to the company's focus on safer warehouses and the chance to build fleet telemetry that real operators rely on every day." })); } });
add({ id: "ask-save-more", group: "notice", panel: true, clicks: ["save-more"], expect: { has: ["save-always", "save-never"] },
  setup: ({ ui }) => { ui.setPanel(panelView({ collapsed: true })); ui.askSave(saveView()); } });
add({ id: "ask-save-no-panel", group: "notice", expect: { has: ["save-ask"] }, responsive: true,
  setup: ({ ui }) => ui.askSave(saveView()) });
const trackView = (over: Partial<TrackAskView> = {}): TrackAskView => ({ company: "Acme Robotics", title: "Senior Software Engineer", evidence: "Thank you for applying! We've received your application.", ...over });
add({ id: "ask-track", group: "notice", panel: true, responsive: true, expect: { has: ["track-ask", "track-record", "track-dismiss", "track-evidence"] },
  setup: ({ ui }) => { ui.setPanel(panelView({ collapsed: true })); ui.askTrack(trackView()); } });
add({ id: "ask-both", group: "notice", panel: true, expect: { has: ["track-ask", "save-ask"] },
  setup: ({ ui }) => { ui.setPanel(panelView()); ui.askSave(saveView()); ui.askTrack(trackView({ evidence: "" })); } });
add({ id: "flash-verified", group: "notice", field: "fx-top", expect: { has: ["flash"] },
  setup: ({ ui, anchor }) => ui.flash(anchor, "verified") });
add({ id: "flash-inferred", group: "notice", field: "fx-top", expect: { has: ["flash"] }, noShot: true, setup: ({ ui, anchor }) => ui.flash(anchor, "inferred") });
add({ id: "flash-draft", group: "notice", field: "fx-text", expect: { has: ["flash"] }, noShot: true, setup: ({ ui, anchor }) => ui.flash(anchor, "draft") });

const ZOOM_IDS = new Set([
  "chip-verified-email", "chip-protected-explicit", "chip-inferred", "chip-draft-long", "chip-needs-missing-fact", "chip-consent", "chip-generating-generating",
  "chip-flip-above", "chip-clamp-right", "chip-long-unbroken", "panel-ready-20", "panel-ready-6", "panel-collapsed", "panel-more-open", "panel-conn-pairing",
  "panel-conn-no_bridge", "panel-research-needs-company", "panel-research-sources-open", "panel-all-notes", "ask-save", "ask-track", "ask-both", "toast-all",
  "combo-page", "combo-draft-page", "chip-bidi-data", "panel-bidi-job", "chip-question-draft", "chip-stale-research", "chip-skip-file-unapproved",
]);
for (const scene of scenes) if (ZOOM_IDS.has(scene.id)) scene.zoom = true;

export const SCENES: Scene[] = scenes;
