"""What the assistant must do on each fixture, field by field (the acceptance table of the e2e suite).

Values come from ``seed.py``; states, tones and insertion tiers from docs/EXTENSION_DESIGN.md section 5.3. Every
expectation here that depends on the answer engine was cross-checked against the real engine (the ``seed_oracle``
scenario runs the engine on the scanned fixture controls and compares).

Field ``kind`` tells the harness how to read the control after a Tab:
  text     input/textarea value                       select    selected option label
  radio    value attribute of the checked radio       checks    value attributes of the checked checkboxes
  file     names of the attached files                editable  innerText of a contenteditable
"""

from __future__ import annotations

from dataclasses import dataclass

import seed

VERIFIED, INFERRED, DRAFT = "VERIFIED", "INFERRED", "GENERATED_DRAFT"
NEEDS_YOU, BLANK, CONSENT, SKIP = "NEEDS_YOU", "SENSITIVE_BLANK", "CONSENT", "SKIP"
DRAFT_TEXT = "<<a draft built from the verified project story>>"  # result sentinel for a drafted answer
ANY = "ANY"  # the product may legitimately answer in several ways here: only the safety invariants are asserted


@dataclass(frozen=True)
class F:
    """One assist field of a fixture, in tab order."""

    sel: str  # CSS selector of the control that takes focus (first member for groups)
    state: str  # SuggestionState the chip must show ('SKIP': no chip, except a file field: its quiet chip says why the upload is left alone)
    insert: str = "tab"  # InsertTier
    chip: str | None = None  # text the chip value must contain
    result: object = None  # value of the control after Tab (None = unchanged / empty)
    kind: str = "text"
    tone: str | None = None  # data-tone of the chip for the three trust colours
    note: str = ""
    known_issue: str = ""  # the product does not meet this row yet (reason): reported as a finding, never silently skipped
    bulk: bool = True  # included by the panel's "Fill verified" button (False: left to Tab/Enter)
    skip_code: str | None = None  # a SKIPped upload: which of the extension's skip reasons its quiet chip must give (ui.Chip.expect_skip)

    @property
    def inserted(self) -> bool:
        return self.result is not None


def verified(sel: str, value: str, **kw) -> F:
    return F(sel, VERIFIED, "tab", chip=value, result=kw.pop("result", value), tone="verified", **kw)


GREENHOUSE: list[F] = [
    verified("#first_name", seed.FIRST),
    verified("#last_name", seed.LAST),
    verified("#email", seed.EMAIL),
    verified("#phone", seed.PHONE),
    F("#resume", VERIFIED, "tab", chip=seed.CV_FILENAME, result=seed.CV_FILENAME, kind="file", tone="verified"),
    F("#cover_letter", SKIP, "never", kind="file", skip_code="file_cover_letter",
      note="never attach anything but the approved CV to a CV field: a quiet chip (state SKIP, nothing to insert) says so"),
    verified("#linkedin", seed.LINKEDIN),
    verified("#github", seed.GITHUB),
    verified("#portfolio", seed.PORTFOLIO),
    F("#degree", INFERRED, "tab", chip="Master's degree", result="Master's degree", tone="inferred"),
    F("#employer", INFERRED, "tab", chip="Brightwave Labs", result="Brightwave Labs", tone="inferred"),
    F("#colour", NEEDS_YOU, "never", note="no fact: stays blank and says so"),
    F("#authorised", VERIFIED, "explicit", chip="Yes", kind="select",
      note="work authorization is protected: Enter or click, never plain Tab (the chip wears the lock tone)"),
    F("#sponsorship_yes", VERIFIED, "explicit", chip="No", kind="radio",
      note="sponsorship is protected: Enter or click, never plain Tab"),
    verified("#salary", str(seed.SALARY), result=str(seed.SALARY),
             note="a confirmed salary policy scoped to the job's country (needs the job location)"),
    verified("#start_date", seed.START_DATE),
    F("#source", NEEDS_YOU, "never", kind="select",
      note="how the candidate found the job is not in any profile: Meridian never claims to be the source"),
    F("#lang_en", VERIFIED, "tab", chip="English", kind="checks", result=["English", "Spanish"], tone="verified"),
    F("#why", DRAFT, "tab", kind="text", tone="draft", result=DRAFT_TEXT,
      note="drafted in the background from facts + company research"),
    F("#gender", BLANK, "never", kind="select", note="personal-disclosure policy leaves it blank"),
    F("#ethnicity", BLANK, "never", kind="select"),
    F("#disability_yes", BLANK, "never", kind="radio"),
    F("#veteran", BLANK, "never", kind="select"),
    F("#gdpr_consent", CONSENT, "never", kind="checks", note="consent is the user's decision"),
    F("#privacy_notice", CONSENT, "never", kind="checks"),
]

# Controls the extension must NEVER write to on the greenhouse form, whatever the user does with Tab.
GREENHOUSE_NEVER_WRITTEN = ["cover_letter", "colour", "source", "gender", "ethnicity", "disability_yes", "disability_no",
                            "disability_na", "veteran", "gdpr_consent", "privacy_notice", "submit_app",
                            "g-recaptcha-response"]

LEVER: list[F] = [
    F("#resume", VERIFIED, "tab", chip=seed.CV_FILENAME, result=seed.CV_FILENAME, kind="file", tone="verified"),
    verified("#name", seed.NAME),
    verified("#email", seed.EMAIL),
    verified("#phone", seed.PHONE),
    F("#org", NEEDS_YOU, "never", note="no current employer fact"),
    verified("#linkedin", seed.LINKEDIN),
    verified("#github", seed.GITHUB),
    verified("#portfolio", seed.PORTFOLIO),
    F("#comments", ANY, "never", note="free-text 'additional information': drafted or left to the user, never invented"),
    F("#work_auth", VERIFIED, "explicit", chip="Yes", kind="select"),
    F("#eeo_gender", BLANK, "never", kind="select"),
    F("#eeo_race", BLANK, "never", kind="select"),
]

REACT: list[F] = [
    verified("#first", seed.FIRST),
    verified("#last", seed.LAST),
    verified("#email", seed.EMAIL),
    F("#country", VERIFIED, "tab", chip="United Kingdom", kind="select", result="United Kingdom", tone="verified"),
]

PREFILLED: list[F] = [
    F("#first_name", VERIFIED, "tab", chip=seed.FIRST, kind="text", result=None, tone="verified",
      note="holds 'Alexander': a different value is never overwritten, the chip offers Replace"),
    F("#last_name", VERIFIED, "tab", chip=seed.LAST, kind="text", result=None, tone="verified",
      note="already equals the suggestion: nothing to do"),
    verified("#email", seed.EMAIL),
    F("#country", VERIFIED, "tab", chip="United Kingdom", kind="select", result="United Kingdom", tone="verified",
      note="'Select an option' counts as empty"),
    verified("#phone", seed.PHONE),
]

CHOICES: list[F] = [
    F("#country", VERIFIED, "tab", chip="United Kingdom", kind="select", result="United Kingdom", tone="verified"),
    F("#relocate_yes", VERIFIED, "tab", chip="Yes", kind="radio", result=["yes"], tone="verified",
      note="a confirmed Profile answer (Settings > Profile > Job preferences)"),
    F("#auth_yes", VERIFIED, "explicit", chip="Yes", kind="radio", note="right to work is protected"),
    F("#lang_en", VERIFIED, "tab", chip="English", kind="checks", result=["English", "Spanish"], tone="verified"),
    F("#languages_multi", VERIFIED, "tab", chip="English", kind="multi", result=["English", "Spanish"],
      tone="verified", note="a <select multiple>"),
    F("#workplace", VERIFIED, "tab", chip="Hybrid", kind="select", result="Hybrid", tone="verified"),
]

SPA_STEP1: list[F] = [verified("#first_name", seed.FIRST), verified("#last_name", seed.LAST)]
SPA_STEP2: list[F] = [verified("#email", seed.EMAIL), verified("#phone", seed.PHONE),
                      verified("#linkedin", seed.LINKEDIN)]

SUBMIT_FLOW: list[F] = [verified("#first_name", seed.FIRST), verified("#last_name", seed.LAST),
                        verified("#email", seed.EMAIL)]

CONSENT_PAGE_DEFAULT: list[F] = [
    F("#pronouns", BLANK, "never", kind="select"),
    F("#gender", BLANK, "never", kind="select"),
    F("#disability_yes", BLANK, "never", kind="radio"),
    F("#terms", CONSENT, "never", kind="checks"),
    F("#marketing", CONSENT, "never", kind="checks"),
    F("#transcribe_yes", CONSENT, "never", kind="radio"),
]

# Engine cross-check: question text -> (answer, confidence level) the real engine must return for the seeded
# candidate (a deterministic subset; checked by the seed_oracle scenario against jobagent's CandidateEngine).
ORACLE: dict[str, tuple[str | None, str]] = {
    "First Name *": (seed.FIRST, "EXACT"),
    "Last Name *": (seed.LAST, "EXACT"),
    "Email *": (seed.EMAIL, "EXACT"),
    "Phone *": (seed.PHONE, "EXACT"),
    "LinkedIn Profile": (seed.LINKEDIN, "EXACT"),
    "GitHub": (seed.GITHUB, "EXACT"),
    "Portfolio / personal website": (seed.PORTFOLIO, "EXACT"),
    "Highest completed degree": ("Master's degree", "DERIVED_HIGH"),
    "Most recent employer": ("Brightwave Labs", "DERIVED_HIGH"),
    "What are your salary expectations (GBP)?": (str(seed.SALARY), "POLICY"),
    "When can you start?": (seed.START_DATE, "EXACT"),
    "Full name": (seed.NAME, "EXACT"),
    "Will you now or in the future require visa sponsorship?": ("No", "EXACT"),
    "Are you legally authorized to work in the United Kingdom?": ("Yes", "EXACT"),
    "Which languages do you speak fluently?": ("English, Spanish", "EXACT"),
}
