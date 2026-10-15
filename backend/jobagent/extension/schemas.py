"""Wire contract of the bridge (protocol 1). The TypeScript mirror is extension/src/shared/protocol.ts.

Requests are validated strictly on size and shape and are ALWAYS treated as untrusted page data. Responses are
plain dictionaries produced by the server; the models below document them and are used to validate in tests.
Never add a field that carries a value the user typed unless the endpoint is explicitly a feedback endpoint.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------------------------- enums

Browser = Literal["chrome", "safari"]

# Suggestion state computed server side from the engine result (docs/EXTENSION_DESIGN.md section 5.3).
SuggestionState = Literal[
    "SKIP",  # not an answerable field (password, hidden, file without a document, challenge page, ...)
    "NOT_APPLICABLE",  # conditional question whose parent answer makes it irrelevant
    "SENSITIVE_BLANK",  # personal-disclosure policy leaves it blank
    "CONSENT",  # legal / consent question: the user decides, never inserted
    "GENERATING",  # long-form draft is being written in the background
    "GENERATED_DRAFT",  # validated long-form draft (always requires review)
    "VERIFIED",  # exact verified fact or policy
    "INFERRED",  # derived from verified facts (amber: check it)
    "NEEDS_YOU",  # nothing safe to suggest
]
# How the suggestion may be inserted: 'tab' = Tab key, 'explicit' = Enter / click only, 'never' = display only.
InsertTier = Literal["tab", "explicit", "never"]
FillOperation = Literal[
    "TYPE_TEXT",  # input / textarea / contenteditable (native setter + input/change events)
    "SELECT_OPTION",  # native <select>
    "CHECK_RADIO",  # radio group: choose the radio whose label matches
    "SET_CHECKBOX",  # single checkbox / switch (checked: bool)
    "SET_CHECKBOXES",  # checkbox group or <select multiple> (labels: list)
    "OPEN_AND_SELECT",  # custom combobox: open menu, optionally type search_text, click the matching option
    "ATTACH_FILE",  # <input type=file>: download the approved document and attach it
]
GenerationStatus = Literal["QUEUED", "GENERATING", "REGENERATING", "VALIDATING", "DONE", "FAILED", "CANCELLED"]
Outcome = Literal["accepted", "edited", "rejected", "skipped", "manual"]
Remember = Literal["none", "application", "reuse"]
SaveCorrections = Literal["ask", "always", "never"]
Language = Literal["auto", "en", "es"]
ResearchState = Literal["queued", "running", "needs_input", "done", "partial", "failed", "cancelled"]
TrackedState = Literal["none", "prospective", "preparing", "applied"]
ErrorCode = Literal[
    "unauthorized", "origin", "host", "restoring", "invalid", "too_large", "rate_limited", "not_found", "expired",
    "disabled", "unavailable", "conflict", "internal",
]


class Strict(BaseModel):
    """Requests drop unknown keys: a page cannot smuggle engine-internal underscore keys through the bridge."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)


# --------------------------------------------------------------------------------------------- pairing / hello


class PairStartRequest(Strict):
    client_name: str = Field(default="Browser", max_length=60)  # display only, attacker controlled
    extension_version: str = Field(default="", max_length=32)
    locale: str = Field(default="", max_length=16)


class PairStartResponse(BaseModel):
    pairing_id: str
    code: str  # six digits, shown by the EXTENSION and typed by the user into Meridian's approval dialog
    expires_in: int  # seconds
    poll_after_ms: int = 1500


class PairStatusResponse(BaseModel):
    state: Literal["pending", "approved", "denied", "expired", "claimed"]
    token: str | None = None  # delivered once, only to the Origin that started the pairing
    client_id: str | None = None


class HelloResponse(BaseModel):
    app: Literal["Meridian"] = "Meridian"
    bridge: int = 1
    app_version: str
    latest_extension_version: str
    port: int
    # Present only when ?client=<id>&nonce=<b64url> names an active client:
    # proof = b64url(HMAC-SHA256(key=sha256(secret), msg=b"meridian-bridge-v1|"+nonce+b"|"+str(port)))
    proof: str | None = None
    # Additive. For a client that was revoked: ``revoked`` true plus
    # revoked_proof = b64url(HMAC-SHA256(key=sha256(secret), msg=b"meridian-bridge-v1-revoked|"+nonce+b"|"+str(port))).
    # Never both ``proof`` and ``revoked_proof``; an unknown client gets neither.
    revoked: bool = False
    revoked_proof: str | None = None
    # sha256 tree digest of the bundled extension ("" when none), so a rebuild with the same version is noticed
    latest_extension_digest: str = ""


# --------------------------------------------------------------------------------------------- settings / status


class ExtensionSettings(BaseModel):
    enabled: bool = True
    language: Language = "auto"
    auto_advance: bool = True  # Tab inserts AND moves to the next field
    skip_filled: bool = True  # Tab advance skips fields that already have a value
    tab_inserts_protected: bool = False  # allow Tab (not only Enter/click) for confirmed sensitive/legal answers
    save_corrections: SaveCorrections = "ask"
    background_research: bool = True
    background_drafts: bool = True
    # Laya and the local writing model may pick among the offered options and write a known value as the field asks (all on this Mac)
    local_assist: bool = True
    web_lookup: bool = True  # last-resort public web search for the official company site (company name only)
    hint_on_ats: bool = True
    # What one page may spend on drafts. Unset (None, or cleared) means the default of ``api.DEFAULT_DRAFT_BUDGET_USD`` (the
    # server answers with that amount): there is no "unlimited", a large amount is how to ask for it.
    draft_budget_usd: float | None = None
    always_on_sites: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    protocol: int = 1
    app_version: str
    latest_extension_version: str
    language: Language
    # {verified_facts:int, has_cv:bool (an APPROVED CV exists), display_name:str|None, unapproved_cvs?:int (imported versions still
    # waiting for approval; the panel explains an unattached CV upload with it; absent = not said)}
    profile: dict[str, Any]
    generation: dict[str, Any]  # {available, tested, provider, local, message, monthly_limit_usd, spent_usd}
    settings: ExtensionSettings


# --------------------------------------------------------------------------------------------- page / sessions


class PageLink(Strict):
    href: str = Field(max_length=2000)
    text: str = Field(default="", max_length=200)


class PageReport(Strict):
    """What the content script reads from the TOP frame. Never form values, never full HTML."""

    url: str = Field(max_length=4000)
    title: str = Field(default="", max_length=400)
    h1: str = Field(default="", max_length=400)
    lang: str = Field(default="", max_length=16)
    company_hint: str | None = Field(default=None, max_length=200)
    meta: dict[str, str] = Field(default_factory=dict)  # og:site_name, og:title, og:description, description, ...
    canonical: str | None = Field(default=None, max_length=4000)
    jsonld: list[str] = Field(default_factory=list, max_length=8)  # RAW ld+json texts (<= 200 KB each)
    description_text: str = Field(default="", max_length=24000)  # employer description only, no form widgets
    location_hint: str | None = Field(default=None, max_length=200)  # where the page says the job is (optional, untrusted)
    links: list[PageLink] = Field(default_factory=list, max_length=80)
    user: dict[str, str | None] = Field(default_factory=dict)  # explicit overrides: company, title, site


class SessionCreateRequest(Strict):
    page: PageReport
    tab_key: str = Field(default="", max_length=64)  # opaque, only for deduplicating reloads of the same tab
    locale: str = Field(default="", max_length=16)  # navigator.language, for server-side text only


# --------------------------------------------------------------------------------------------- elements / suggest


class OptionIn(Strict):
    label: str = Field(default="", max_length=300)
    value: str = Field(default="", max_length=300)
    disabled: bool = False


class ElementIn(Strict):
    """A control as emitted by backend/jobagent/automation/observation.js, pruned by the content script.

    The server recomputes the question text (adapter.question) and the question id, ignores any client-supplied
    underscore key and NEVER receives what the user typed into text fields (``value`` is only meaningful for
    choice controls; text controls send ``has_value`` instead).
    """

    node_id: int
    role: str = Field(default="", max_length=40)
    tag: str = Field(default="", max_length=20)
    type: str = Field(default="", max_length=30)
    explicit_type: str | None = Field(default=None, max_length=30)
    label: str = Field(default="", max_length=20000)
    context: str = Field(default="", max_length=20000)
    description: str = Field(default="", max_length=20000)
    placeholder: str = Field(default="", max_length=500)
    section_id: str = Field(default="", max_length=80)
    section_label: str = Field(default="", max_length=200)
    form_id: str | int = ""
    group: str = Field(default="", max_length=500)
    field_path: str = Field(default="", max_length=300)
    name: str = Field(default="", max_length=300)
    id: str = Field(default="", max_length=300)
    autocomplete: str = Field(default="", max_length=60)
    required: bool = False
    enabled: bool = True
    readonly: bool = False
    multiple: bool = False
    checked: bool = False
    expanded: bool = False
    has_value: bool = False
    value: str = Field(default="", max_length=300)  # choice controls only
    selected_text: str = Field(default="", max_length=300)
    selected_values: list[str] = Field(default_factory=list, max_length=100)
    max_length: int | None = None
    min_length: int | None = None
    pattern: str = Field(default="", max_length=300)
    min: str | None = None
    max: str | None = None
    step: str | None = None
    accept: str = Field(default="", max_length=200)
    operations: list[str] = Field(default_factory=list, max_length=12)
    options: list[OptionIn] = Field(default_factory=list, max_length=1000)
    phone_country_code: str | None = None
    phone_national_only: bool = False
    auxiliary_control: bool = False
    manual_key: str = Field(default="", max_length=600)
    # The question as the PERSON sees it (the text of the visible label), when the control's accessible name (``label``) was
    # taken from somewhere else (an aria-label, a hidden span): the server answers what the person reads, and a field whose two
    # names disagree is never offered as verified. Optional: absent (or equal to ``label``) means the two are the same.
    visible_label: str = Field(default="", max_length=20000)
    # Index of the control in document order among the controls of the scan this request comes from (comparable within
    # ONE request). A partial request (a conditional follow-up and its parent) lists them in any order; this restores
    # "the question above". Optional: without it the observer's own numbering (document order of the first scan) is used.
    position: int | None = Field(default=None, ge=0, le=1_000_000)


class SuggestRequest(Strict):
    document_id: str = Field(default="", max_length=80)  # observation document identity (stale detection)
    frame_url: str = Field(default="", max_length=4000)
    frame_key: str = Field(default="top", max_length=40)  # 'top' or 'f<frameId>'
    elements: list[ElementIn] = Field(max_length=400)
    focus_node_id: int | None = None
    mode: Literal["scan", "refresh", "focus"] = "scan"
    challenge: bool = False
    mfa: bool = False
    login: bool = False


class SuggestionValue(BaseModel):
    display: str
    operation: FillOperation
    text: str | None = None
    form_value: Any | None = None
    option: dict[str, str] | None = None  # {label, value}
    labels: list[str] | None = None
    label: str | None = None
    search_text: str | None = None
    checked: bool | None = None
    # {id, filename, mime, size, sha256, name?, alternatives?: [{id, filename, version}]}: ``alternatives`` (CPL-20) are the other
    # documents of the same kind THIS page may be offered and this field would take (at most 6, the default excluded): a click-only
    # "use another CV" menu swaps one in (fetch it by id with GET /documents/{id}/content); Tab always inserts the default.
    document: dict[str, Any] | None = None


class Suggestion(BaseModel):
    node_id: int
    frame_key: str = "top"
    question: str
    question_id: str
    request_id: str
    knowledge_version: str
    state: SuggestionState
    insert: InsertTier
    suggestion: SuggestionValue | None = None
    confidence: dict[str, Any] | None = None  # {value: 0..1, level: EXACT|POLICY|DERIVED_HIGH|DERIVED_MEDIUM|GENERATED}
    why: dict[str, Any] | None = None  # {code, summary(en), intent, rules[], facts[], sensitivity, sources[]}
    flags: dict[str, Any] = Field(default_factory=dict)  # protected, legal, leave_blank, requires_review, conditional, reusable, members
    # {root_cause, code, message(en), actions[], candidate_value?}; ``code`` is the design's vocabulary or ``draft_outdated``
    # (a finished draft no longer holds: a fact it cites changed, or the company it is about was rejected; action: draft_with_ai)
    needs: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None  # see 5.5
    # {job_description: bool, company_pages: int, research_state, knowledge_version?: the version the draft was WRITTEN under: a page
    # holding a different one marks the draft "profile changed" and does not insert it by Tab; absent = the page never guesses,
    # research_bundle?: str (the research bundle the draft was written from), stale?: true + reason?: 'research_changed' (still
    # offered) | 'facts_changed' | 'company_rejected' (those two come with state NEEDS_YOU and needs.code 'draft_outdated')}
    based_on: dict[str, Any] | None = None


class SuggestResponse(BaseModel):
    knowledge_version: str
    results: list[Suggestion]
    skipped: list[dict[str, Any]] = Field(default_factory=list)  # [{node_id, reason: group_member|button|auxiliary|not_a_field}]
    # The session's event cursor when the request STARTED (pass it to /poll): every event emitted while the request ran is
    # numbered after it and is delivered by that poll (the same state may reach the page twice, never zero times). The
    # results themselves are never older than an event emitted before they were sent: a draft that finished meanwhile is
    # already in them (``generation.id`` is the draft's identity, ``generation.status`` only moves forward).
    cursor: int


class PollRequest(Strict):
    cursor: int = 0
    wait_ms: int = Field(default=20000, ge=0, le=25000)


class PollResponse(BaseModel):
    cursor: int
    # {type:'suggestion', suggestion:Suggestion} | {type:'research', research:dict} | {type:'knowledge', knowledge_version:str}
    # | {type:'generation_budget', ...} | {type:'tracked', tracked:TrackedState, status?:str, since?:str (JobSummary)}
    # | {type:'drafts_paused', code:'AUTO_DRAFT_LIMIT', message:str}: this page used up the drafts Meridian writes on its own
    #   (drafts the user asks for still work: unlike 'generation_budget' it does NOT make writing unavailable)
    events: list[dict[str, Any]]


class PollTarget(Strict):
    session_id: str = Field(max_length=80)
    cursor: int = Field(default=0, ge=0)


class PollManyRequest(Strict):
    """POST /v1/poll: ONE long-poll for every page of a browser (a browser allows ~6 connections per host: a poll per
    page starves the pages opened last). At most 16 pages, each listed once; every page must be this browser's."""

    sessions: list[PollTarget] = Field(min_length=1, max_length=16)
    # 0..250 answers at once (a quick "anything now?" check that never takes a page over from a long poll waiting for it)
    wait_ms: int = Field(default=20000, ge=0, le=25000)


class PollManyEntry(BaseModel):
    session_id: str
    # ok: events after `cursor` | expired: open the page again (POST /sessions) | unknown: not a page of this browser
    # | released: the page was closed | superseded: a newer poll of this browser took the page over
    status: Literal["ok", "expired", "unknown", "released", "superseded"]
    cursor: int  # the cursor to ask with next
    events: list[dict[str, Any]]


class PollManyResponse(BaseModel):
    sessions: list[PollManyEntry]  # one per requested page, in request order; returned as soon as ANY page has events


class RegenerateRequest(Strict):
    question_id: str = Field(max_length=100)
    node_id: int | None = None
    frame_key: str = Field(default="top", max_length=40)
    instruction: Literal["again", "shorter", "longer", "different"] | str = Field(default="again", max_length=300)
    element: ElementIn | None = None  # lets the server draft for a short field on explicit user request
    current_text: str | None = Field(default=None, max_length=20000)  # draft being revised (never the user's typing)


# --------------------------------------------------------------------------------------------- feedback / tracking


class FeedbackRequest(Strict):
    question_id: str = Field(max_length=100)
    frame_key: str = Field(default="top", max_length=40)
    outcome: Outcome
    suggestion_state: SuggestionState | None = None
    request_id: str | None = Field(default=None, max_length=80)
    final_value: str | None = Field(default=None, max_length=20000)
    final_values: list[str] | None = Field(default=None, max_length=100)
    remember: Remember = "application"
    element: ElementIn | None = None  # the control, so the server can compute the same memory key as the engine


class FeedbackResponse(BaseModel):
    saved: bool
    application_id: str | None = None
    scope: str | None = None
    promoted_to_profile: bool = False
    message: str | None = None
    # Present only on a refusal that has a precise reason: never_saved_{password,hidden,file,otp,card,bank,id,
    # security_answer} | other_party_label | ambiguous_label | invalid_value
    code: str | None = None


class TrackRequest(Strict):
    # ``save`` (UXR-01): "Save to tracker" in the panel menu. The job and a bare PREPARING application are written, a submission
    # is NEVER recorded, and an application that was already sent is left exactly as it is (answer: ``already_recorded``).
    event: Literal["submit_clicked", "confirmation_seen", "record", "save"]
    evidence: str = Field(default="", max_length=400)  # short visible-text excerpt, never form values
    applied_at: str | None = None
    notes: str = Field(default="", max_length=2000)


class TrackResponse(BaseModel):
    # ``saved``: the posting is in the tracker as a PREPARING application (``save``). ``already_recorded`` also answers a ``save``
    # for an application that was sent: nothing was changed and ``tracked`` is ``applied``.
    state: Literal["noted", "recorded", "already_recorded", "saved"]
    job_id: str | None = None
    application_id: str | None = None
    tracked: TrackedState = "none"


class JobSummary(BaseModel):
    """``SessionCreateResponse.job``: what the panel knows about the posting. ``status`` / ``since`` (UXR-08) are present only
    when an application exists, so the duplicate banner can say what really happened to it:

    * ``status``: the application's own status (``PREPARING``, ``APPLIED``, ``INTERVIEW``, ``REJECTED``, ``WITHDRAWN``, ...);
      ``tracked`` is ``applied`` for every status that means it was sent (rejected and withdrawn ones included).
    * ``since``: when it was SENT (ISO date or timestamp): the earliest APPLIED / CONFIRMED event, else the day it entered the
      tracker. Present only for a sent application. ``tracked_at`` carries the same value (the name the panel reads today).
    """

    company: str
    title: str
    location: str
    job_id: str | None = None
    application_id: str | None = None
    tracked: TrackedState = "none"
    status: str | None = None
    since: str | None = None
    tracked_at: str | None = None


# --------------------------------------------------------------------------------------------- research corrections


class ResearchCompanyRequest(Strict):
    """POST /v1/sessions/{sid}/research/company: 'this is not the company, it is <company>' (the user's correction)."""

    company: str = Field(max_length=200)


class ResearchSiteRequest(Strict):
    """POST /v1/sessions/{sid}/research/site: pin the official website of the company (a bare domain or a URL)."""

    domain: str = Field(max_length=300)


# --------------------------------------------------------------------------------------------- documents


class DocumentInfo(BaseModel):
    id: str  # document_versions.id
    name: str
    filename: str
    mime: str
    size: int
    kind: str = "cv"
    version: int
    default: bool = False
    # 'base': one of the user's own CVs; 'job': the CV Meridian wrote for ONE posting (listed only for the page of that job)
    scope: Literal["base", "job"] = "base"


class DocumentContent(BaseModel):
    id: str
    filename: str
    mime: str
    size: int
    sha256: str
    data_b64: str


class ErrorBody(BaseModel):
    detail: str
    code: ErrorCode
