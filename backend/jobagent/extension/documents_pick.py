"""Which of the user's own documents the assistant may offer for an upload field, and what it says when it offers none.

Rules (docs/EXTENSION_DESIGN.md 5.9, SAFE-09 / CPL-06 / CPL-14):

* **Only an APPROVED version, only the user's own.** An imported CV is not approved until the user approves it in Meridian.
* **Only a CV that may go to THIS employer.** Meridian writes a CV per job (``documents.job_id`` = that job, the body names the
  employer and the role). A page may be offered the user's base CVs (``job_id`` empty), the CV written for THIS posting, or
  one the user attached to this very application: never the newest CV of whatever job was prepared last.
  The default is, in order: attached to this application, written for this posting, the CV chosen in Settings, the newest
  base CV. Every other eligible CV is listed too (``GET /v1/documents``) so the panel can offer a choice.
* **A cover letter is job-specific by nature**: only one written for this posting (or attached to this application),
  approved, is offered, with the same checks as a CV. Otherwise the field says why it is left alone.
* Whenever nothing is offered, the reason is specific (not approved yet / none imported / not a document Meridian attaches),
  because a silent skip reads as "the assistant cannot attach files".
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from pathlib import Path

CV_KINDS = ("cv", "resume", "base_cv", "swe_cv", "ai_cv", "data_cv", "import")
LETTER_KINDS = ("cover_letter",)
CV_WORDS = re.compile(r"resume|\bcv\b|curriculum|currículum|lebenslauf", re.I)
NOT_CV_WORDS = re.compile(r"cover|letter|transcript|portfolio|certificate|degree|photo|reference|carta|presentaci", re.I)
COVER_WORDS = re.compile(
    r"cover(?:ing)?[ _-]?letter|letter of (?:motivation|interest|application|intent)|motivation(?:al)?[ _-]?letter|"
    r"carta de (?:presentaci[oó]n|motivaci[oó]n|inter[eé]s)|\bcarta\b.*\bpresentaci|anschreiben|lettre de motivation", re.I)
MAX_DOCUMENT = 10 * 1024 * 1024
MAX_ALTERNATIVES = 6  # the click-only "use another CV" menu is a short list, not the vault

SKIP_MESSAGES = {
    "cv_unapproved": "A CV of yours is waiting for your approval. Approve it in Meridian (Documents) so I can attach it.",
    "cv_missing": "Meridian has no CV for you yet. Import and approve one in Meridian (Documents) so I can attach it.",
    "cover_letter_unapproved": "The cover letter Meridian wrote for this job is not approved yet. Approve it in Meridian so I "
                               "can attach it.",
    "cover_letter_none": "Meridian attaches a cover letter only when it wrote one for this job and you approved it. "
                         "Upload yours here, or write one in Meridian.",
    "file": "Meridian attaches your approved CV (and a cover letter it wrote for this job), nothing else. Upload this "
            "file yourself.",
}


def _read(question: str, element: dict) -> str:
    """What a PERSON can read about a control: the question the server made of it and its label (the visible label when the page
    sent one, ``resolver.prepare_elements`` has put it there). Never ``name`` / ``id``: a page names its controls anything."""
    return " ".join(str(x or "") for x in (question, element.get("label")))


def _named(element: dict) -> str:
    return " ".join(str(element.get(k) or "") for k in ("name", "id"))


def looks_like_cv_field(question: str, element: dict) -> bool:
    """Is this file control a CV field? Only when what the person reads says so (SEC-01 item 7): a ``name`` / ``id`` of 'resume'
    on a control labelled 'Continue' (or labelled nothing) is not a CV field, so the CV is never attached behind a label that
    says something else. Any word that says it is another document ('cover letter', 'transcript') wins over everything."""
    read = _read(question, element)
    return bool(CV_WORDS.search(read)) and not NOT_CV_WORDS.search(read + " " + _named(element))


def looks_like_cover_letter_field(question: str, element: dict) -> bool:
    """The same rule for a cover letter: the words must be on what the person reads, not only in the control's name."""
    return bool(COVER_WORDS.search(_read(question, element)))


# --------------------------------------------------------------------------------------------- reading the vault
def _version_rows(db, data_dir: Path, kinds: tuple, *, approved: bool = True) -> list[dict]:
    rows = db.query(
        "SELECT v.id,v.version,v.filename,v.mime_type,v.content_hash,v.path,v.created_at,d.name,d.kind,d.job_id "
        "FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.approved=? AND d.kind IN ("
        + ",".join("?" * len(kinds)) + ") ORDER BY v.created_at DESC", (int(approved), *kinds))
    root = (Path(data_dir) / "documents").resolve()
    usable = []
    for row in rows:
        try:
            path = Path(row["path"]).resolve()
            if path.is_relative_to(root) and path.is_file():
                usable.append({**row, "size": path.stat().st_size})
        except OSError:
            continue
    return usable


def cv_rows(db, data_dir: Path) -> list[dict]:
    """Every approved CV version whose file is readable, newest first (ALL jobs: callers narrow it with :func:`eligible`)."""
    return _version_rows(db, data_dir, CV_KINDS)


def is_base(row: dict) -> bool:
    return not row.get("job_id")


def eligible(rows: list[dict], job_id: str | None, attached: set | frozenset = frozenset()) -> list[dict]:
    """The CVs this page may be offered: the user's base CVs, the one written for this very job, the one attached here."""
    return [r for r in rows if is_base(r) or (job_id and r["job_id"] == job_id) or r["id"] in attached]


def _attached(db, application_id: str | None, kinds: tuple) -> set:
    if not application_id:
        return set()
    return {r["document_version_id"] for r in db.query(
        "SELECT document_version_id FROM application_documents WHERE application_id=? AND kind IN ("
        + ",".join("?" * len(kinds)) + ")", (application_id, *kinds))}


def _chosen_default(db) -> str | None:
    chosen = db.one("SELECT value FROM settings WHERE key='default_document_version_id'")
    with contextlib.suppress(ValueError, TypeError):
        return json.loads(chosen["value"]) if chosen else None
    return None


def order_cvs(rows: list[dict], *, job_id: str | None, attached: set, chosen: str | None) -> list[dict]:
    """The eligible CVs, the default first: attached here, written for this job, the Settings choice, the newest base CV."""
    pool = eligible(rows, job_id, attached)

    def rank(row: dict) -> int:
        if row["id"] in attached:
            return 0
        if job_id and row["job_id"] == job_id:
            return 1
        if chosen and row["id"] == chosen:
            return 2
        return 3

    return sorted(pool, key=rank)  # a stable sort: within a rank the newest stays first


def info(row: dict, *, default: bool) -> dict:
    """``DocumentInfo`` (+ ``scope``: 'job' for a CV written for one posting, 'base' for the user's own)."""
    return {"id": row["id"], "name": row["name"], "filename": row["filename"], "mime": row["mime_type"], "size": row["size"],
            "kind": "cv", "version": row["version"], "default": default, "sha256": row["content_hash"],
            "scope": "base" if is_base(row) else "job"}


def approved_documents(db, data_dir, application_id: str | None = None, job_id: str | None = None) -> list[dict]:
    """Approved CVs this page may offer, the default first (see the module docstring). Shaped as ``DocumentInfo``."""
    rows = cv_rows(db, data_dir)
    if not rows:
        return []
    ordered = order_cvs(rows, job_id=job_id, attached=_attached(db, application_id, CV_KINDS), chosen=_chosen_default(db))
    return [info(row, default=index == 0) for index, row in enumerate(ordered)]


def base_documents(db, data_dir) -> list[dict]:
    """``GET /v1/documents`` without a page: the user's own (base) approved CVs, the Settings choice (else the newest) first."""
    rows = [r for r in cv_rows(db, data_dir) if is_base(r)]
    ordered = order_cvs(rows, job_id=None, attached=set(), chosen=_chosen_default(db))
    return [info(row, default=index == 0) for index, row in enumerate(ordered)]


def cover_letters(db, data_dir, application_id: str | None, job_id: str | None) -> list[dict]:
    """Approved cover letters written for THIS job (or attached to this application), newest first, shaped as ``info``."""
    attached = _attached(db, application_id, LETTER_KINDS)
    rows = [r for r in _version_rows(db, data_dir, LETTER_KINDS)
            if (job_id and r["job_id"] == job_id) or r["id"] in attached]
    return [{**info(row, default=index == 0), "kind": "cover_letter"} for index, row in enumerate(rows)]


def unapproved_counts(db, job_id: str | None = None) -> dict:
    """{'cv': n, 'cover_letter': n}: versions waiting for the user's approval (base CVs; this job's cover letter)."""
    cv = db.one("SELECT COUNT(*) AS n FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.approved=0 "
                "AND d.kind IN (" + ",".join("?" * len(CV_KINDS)) + ") AND (d.job_id IS NULL OR d.job_id='' OR d.job_id=?)",
                (*CV_KINDS, job_id or ""))["n"]
    letter = db.one("SELECT COUNT(*) AS n FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.approved=0 "
                    "AND d.kind='cover_letter' AND d.job_id=?", (job_id or "\0",))["n"]
    return {"cv": int(cv), "cover_letter": int(letter)}


# --------------------------------------------------------------------------------------------- the decision for a field
def check_upload(document: dict, element: dict) -> None:
    """Mirror the runner's pre-upload check (format against the field's ``accept``) without reading the file. ValueError."""
    accept = [x.strip().lower() for x in (element.get("accept") or "").split(",") if x.strip()]
    suffix = Path(document["filename"]).suffix.lower()
    mime = (document["mime"] or "").lower()
    if suffix not in {".pdf", ".docx", ".txt", ".doc", ".rtf", ".jpg", ".jpeg", ".png"}:
        raise ValueError("This document format is not accepted by application forms.")
    if accept and not any(x == suffix or x == mime or (x.endswith("/*") and mime.startswith(x[:-1])) for x in accept):
        raise ValueError("This field does not accept this document's format (" + suffix + ").")


def decide_upload(question: str, element: dict, *, cvs: list[dict], letters: list[dict], unapproved: dict) -> dict:
    """What to do with one file field -> ``{action, document, kind, reason, message}``.

    ``action`` is ``attach`` (``document`` is the one to offer; ``alternatives`` the other documents the person may swap in with a
    click, see :func:`alternatives_for`), ``problem`` (a document exists but cannot go in this
    field: ``message`` says why) or ``skip`` (nothing is offered: ``reason`` is the code of ``SKIP_MESSAGES`` and
    ``message`` the sentence)."""
    if looks_like_cv_field(question, element):
        kind, pool = "cv", cvs
        if not pool:
            reason = "cv_unapproved" if unapproved.get("cv") else "cv_missing"
            return {"action": "skip", "kind": kind, "reason": reason, "message": SKIP_MESSAGES[reason], "document": None}
    elif looks_like_cover_letter_field(question, element):
        kind, pool = "cover_letter", letters
        if not pool:
            reason = "cover_letter_unapproved" if unapproved.get("cover_letter") else "cover_letter_none"
            return {"action": "skip", "kind": kind, "reason": reason, "message": SKIP_MESSAGES[reason], "document": None}
    else:
        return {"action": "skip", "kind": "other", "reason": "file", "message": SKIP_MESSAGES["file"], "document": None}
    document = pool[0]
    problem = None
    try:
        check_upload(document, element)
    except ValueError as exc:
        problem = str(exc)
    if document["size"] > MAX_DOCUMENT:
        problem = ("Your CV" if kind == "cv" else "Your cover letter") + " is larger than 10 MB, so Meridian will not attach it."
    if problem:
        return {"action": "problem", "kind": kind, "reason": "format", "message": problem, "document": document}
    return {"action": "attach", "kind": kind, "reason": None, "message": None, "document": document,
            "alternatives": alternatives_for(pool[1:], element)}


def alternatives_for(others: list[dict], element: dict) -> list[dict]:
    """The other documents of the same kind this very page may be offered AND this field would take, as ``{id, filename,
    version}`` (nothing a menu does not need): ``suggestion.document.alternatives`` (CPL-20). Only ever a choice the person makes
    with a click: Tab always inserts the default. A document the field refuses (its format, over 10 MB) is not a choice."""
    shown = []
    for other in others:
        try:
            check_upload(other, element)
        except ValueError:
            continue
        if other["size"] > MAX_DOCUMENT:
            continue
        shown.append({"id": other["id"], "filename": other["filename"], "version": other["version"]})
        if len(shown) >= MAX_ALTERNATIVES:
            break
    return shown


# --------------------------------------------------------------------------------------------- the file itself
def document_content(db, data_dir, version_id: str) -> dict:
    """The approved document as base64, only after the immutable-version checks. A CV (any approved one: the page was only
    ever offered an eligible one) or an approved cover letter. Raises ValueError / LookupError / PermissionError /
    OverflowError."""
    from ..documents import document_path
    row = db.one("SELECT v.approved,d.kind FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.id=?",
                 (version_id,))
    if row is None:
        raise LookupError("Document not found")
    if not row["approved"] or row["kind"] not in (*CV_KINDS, *LETTER_KINDS):
        raise PermissionError("Only approved CVs and cover letters can be attached")
    path, version = document_path(db, data_dir, version_id)  # path escape, missing file and hash drift -> ValueError
    size = path.stat().st_size
    if size > MAX_DOCUMENT:
        raise OverflowError("This document is larger than 10 MB")
    data = path.read_bytes()
    return {"id": version_id, "filename": version["filename"], "mime": version["mime_type"], "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "data_b64": base64.b64encode(data).decode()}
