"""Evidence-preserving imports and immutable, explicitly approved documents."""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import threading
import zipfile
from collections import defaultdict
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .db import decode_row, dumps, now, uid
from .cv_parser import PARSER_VERSION, extract_docx_text, propose_cv_facts
from .matching import extract_skills

MAX_IMPORT = 20 * 1024 * 1024
_REIMPORT_LOCK = threading.Lock()
SUBMITTED_STATUSES = {
    "APPLYING",
    "APPLIED",
    "CONFIRMED",
    "RECRUITER_SCREEN",
    "ASSESSMENT",
    "TECHNICAL_TEST",
    "INTERVIEW",
    "FINAL_INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
    "GHOSTED",
}
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}


def extract_document(content: bytes, filename: str) -> tuple[str, str, list[str]]:
    suffix = Path(filename).suffix.lower()
    warnings = []
    if len(content) > MAX_IMPORT:
        raise ValueError("Document exceeds the 20 MB limit")
    if not content:
        raise ValueError("Document is empty")
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Unlock the PDF before importing it")
            if len(reader.pages) > 100:
                raise ValueError("Document exceeds the 100-page import limit")
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages)
            empty_pages = [str(index + 1) for index, page in enumerate(pages) if not page.strip()]
            if empty_pages:
                warnings.append(
                    "No readable text on PDF pages "
                    + ", ".join(empty_pages)
                    + "; image-only content was not extracted."
                )
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                    raise ValueError("Expanded DOCX exceeds the safety limit")
            text, warnings = extract_docx_text(content)
        elif suffix in {".txt", ".md", ".csv", ".json"}:
            text = content.decode("utf-8-sig")
        else:
            raise ValueError("Supported imports: PDF, DOCX, UTF-8 TXT, Markdown, CSV and JSON")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Document could not be read; check its format and encryption") from exc
    text = text.replace("\x00", "").strip()
    if len(text) > 500000:
        raise ValueError("Document exceeds the 500,000-character text limit; split the source before importing")
    if not text:
        raise ValueError("No readable text was found. Export a text-based PDF or paste the text as a TXT file")
    return text, MIME_TYPES[suffix], warnings


def extract_text(content: bytes, filename: str) -> tuple[str, str]:
    text, mime, _ = extract_document(content, filename)
    return text, mime


def propose_facts(text: str, source: str) -> list[dict]:
    return propose_cv_facts(text, source)[0]


def save_version(
    db,
    data_dir: Path,
    document: dict,
    content: bytes,
    filename: str,
    mime_type: str,
    text: str,
    fact_ids=None,
    approved=False,
    conn=None,
):
    version_id = uid()
    suffix = Path(filename).suffix.lower()
    target_dir = Path(data_dir) / "documents" / document["id"]
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / (version_id + suffix)
    # 'xb' prevents overwriting a registered version even under concurrent requests.
    with path.open("xb") as file:
        file.write(content)
    path.chmod(0o600)
    try:
        with db.transaction() if conn is None else nullcontext(conn) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS n FROM document_versions WHERE document_id=?", (document["id"],)
            ).fetchone()
            version = row["n"]
            conn.execute(
                "INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    document["id"],
                    version,
                    str(path.resolve()),
                    Path(filename).name,
                    mime_type,
                    hashlib.sha256(content).hexdigest(),
                    text,
                    dumps(fact_ids or []),
                    int(approved),
                    now(),
                ),
            )
            conn.execute("UPDATE documents SET updated_at=? WHERE id=?", (now(), document["id"]))
            stored = dict(conn.execute("SELECT * FROM document_versions WHERE id=?", (version_id,)).fetchone())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return decode_row(stored)


def attach_document(db, conn, application_id, version_id, kind):
    application = conn.execute("SELECT status FROM applications WHERE id=?", (application_id,)).fetchone()
    existing = conn.execute(
        "SELECT document_version_id FROM application_documents WHERE application_id=? AND kind=?",
        (application_id, kind),
    ).fetchall()
    if any(row["document_version_id"] == version_id for row in existing):
        return
    if application["status"] in SUBMITTED_STATUSES:
        raise ValueError("Documents of a submitted application cannot be replaced")
    previous = [row["document_version_id"] for row in existing]
    conn.execute("DELETE FROM application_documents WHERE application_id=? AND kind=?", (application_id, kind))
    conn.execute("INSERT INTO application_documents VALUES(?,?,?)", (application_id, version_id, kind))
    db.event(
        application_id,
        application["status"],
        f"Selected {kind} version {version_id}; previous versions: {', '.join(previous) or 'none'}",
        "manual",
        conn=conn,
    )


def import_document(
    db, data_dir: Path, content: bytes, filename: str, *, existing_document=None, supersedes_version_id=None
):
    filename = Path(filename).name[:200]
    text, mime, warnings = extract_document(content, filename)
    # Validate all proposals before creating the document or writing its immutable file.
    proposed, summary = propose_cv_facts(text, "")
    document_id, stamp = uid(), now()
    document = (
        dict(existing_document)
        if existing_document
        else {
            "id": document_id,
            "name": filename,
            "kind": "import",
            "job_id": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
    )
    version = None
    superseded = []
    try:
        with db.transaction() as conn:
            if not existing_document:
                conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", tuple(document.values()))
            version = save_version(db, data_dir, document, content, filename, mime, text, conn=conn)
            candidate_id = conn.execute("SELECT id FROM candidates LIMIT 1").fetchone()["id"]
            for fact in proposed:
                fact.update(
                    id=uid(),
                    candidate_id=candidate_id,
                    source=f"document:{version['id']}",
                    created_at=stamp,
                    updated_at=stamp,
                )
                conn.execute(
                    "INSERT INTO facts(id,candidate_id,category,key,value,source,verification_status,locked,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        fact["id"],
                        candidate_id,
                        fact["category"],
                        fact["key"],
                        fact["value"],
                        fact["source"],
                        "unverified",
                        0,
                        fact["notes"],
                        stamp,
                        stamp,
                    ),
                )
            version["fact_ids"] = [fact["id"] for fact in proposed]
            conn.execute(
                "UPDATE document_versions SET fact_ids=? WHERE id=?", (dumps(version["fact_ids"]), version["id"])
            )
            if supersedes_version_id:
                prior = conn.execute(
                    "SELECT * FROM facts WHERE source=? AND verification_status='unverified' AND locked=0",
                    (f"document:{supersedes_version_id}",),
                ).fetchall()
                for old in prior:
                    conn.execute(
                        "INSERT INTO fact_history VALUES(?,?,?,?,?)",
                        (uid(), old["id"], dumps(dict(old)), old["updated_at"], stamp),
                    )
                    conn.execute(
                        "UPDATE facts SET verification_status='rejected',notes=?,updated_at=? WHERE id=?",
                        (
                            old["notes"]
                            + f"\nSuperseded by reparsed document version {version['id']}; original evidence retained.",
                            stamp,
                            old["id"],
                        ),
                    )
                    superseded.append(old["id"])
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                (
                    f"document_parser:{version['id']}",
                    dumps(
                        {
                            "parser_version": PARSER_VERSION,
                            "summary": summary,
                            "warnings": warnings,
                            "supersedes_version_id": supersedes_version_id,
                        }
                    ),
                ),
            )
    except BaseException:
        if version:
            Path(version["path"]).unlink(missing_ok=True)
        raise
    document["updated_at"] = stamp
    return {
        "document": document,
        "version": version,
        "proposed_facts": proposed,
        "warnings": warnings,
        "summary": summary,
        "superseded_fact_ids": superseded,
        "already_current": False,
    }


def reimport_document(db, data_dir: Path, document_id: str):
    """Repair an import using a new immutable version; retries return the current parser result."""
    if not _REIMPORT_LOCK.acquire(blocking=False):
        raise ValueError("A document reimport is already running; retry after it completes")
    try:
        document = db.one("SELECT * FROM documents WHERE id=?", (document_id,))
        if not document or document["kind"] != "import":
            raise ValueError("Reimport requires an existing imported source document")
        latest = db.one(
            "SELECT * FROM document_versions WHERE document_id=? ORDER BY version DESC LIMIT 1", (document_id,)
        )
        if not latest:
            raise ValueError("The source document has no stored version")
        path, latest = document_path(db, data_dir, latest["id"])
        record = db.one("SELECT value FROM settings WHERE key=?", (f"document_parser:{latest['id']}",))
        metadata = json.loads(record["value"]) if record else {}
        if metadata.get("parser_version") == PARSER_VERSION:
            return {
                "document": decode_row(document),
                "version": latest,
                "proposed_facts": [
                    decode_row(row)
                    for row in db.query(
                        "SELECT * FROM facts WHERE source=? ORDER BY created_at,rowid", (f"document:{latest['id']}",)
                    )
                ],
                "warnings": metadata.get("warnings", []),
                "summary": metadata.get("summary", {}),
                "superseded_fact_ids": [],
                "already_current": True,
            }
        return import_document(
            db,
            data_dir,
            path.read_bytes(),
            latest["filename"],
            existing_document=document,
            supersedes_version_id=latest["id"],
        )
    finally:
        _REIMPORT_LOCK.release()


def select_facts(db, fact_ids=None):
    if fact_ids:
        facts = db.query(f"SELECT * FROM facts WHERE id IN ({','.join('?' for _ in fact_ids)})", fact_ids)
        if len(facts) != len(set(fact_ids)):
            raise ValueError("One or more referenced facts do not exist")
        if any(f["verification_status"] != "verified" and not f["locked"] for f in facts):
            raise ValueError("Only verified or locked facts can support a generated document")
    else:
        facts = db.query(
            "SELECT * FROM facts WHERE verification_status='verified' OR locked=1 ORDER BY category,created_at"
        )
    if not facts:
        raise ValueError("Verify candidate facts before generating a document")
    return facts


def compose_document(job: dict, kind: str, facts: list[dict]) -> str:
    relevant = extract_skills(job.get("description", ""))
    facts = sorted(facts, key=lambda f: (-sum(skill in f["value"].lower() for skill in relevant), f["created_at"]))
    name = next((f["value"] for f in facts if f["key"].lower() in {"name", "full_name", "full name"}), "")
    if kind == "cover_letter":
        professional = [
            f for f in facts if f["category"] in {"experience", "project", "education", "skill", "publication"}
        ]
        if not professional:
            raise ValueError("A cover letter needs verified professional facts")
        evidence = "\n\n".join(f["value"] for f in professional[:10])
        return f"Application — {job['title']}\n{job['company']}\n\nDear hiring team,\n\nI am applying for the {job['title']} position at {job['company']}. The following is relevant information from my professional background:\n\n{evidence}\n\nThank you for considering my application. I would welcome the opportunity to discuss the role.\n\n{name}".strip()
    grouped = defaultdict(list)
    for fact in facts:
        if fact["category"] not in {
            "preference",
            "work_authorization",
            "salary",
            "answer",
            "saved_answer",
            "sensitive",
        }:
            grouped[fact["category"]].append(fact["value"])
    sections = [name or "Professional profile", f"Application: {job['title']} · {job['company']}"]
    labels = {
        "personal": "Contact",
        "link": "Links",
        "education": "Education",
        "experience": "Experience",
        "project": "Projects",
        "skill": "Skills",
        "publication": "Publications",
        "imported": "Additional background",
    }
    for category in ("personal", "link", "experience", "education", "project", "skill", "publication", "imported"):
        if grouped.get(category):
            sections.extend(["", labels.get(category, category.title()), *grouped.pop(category)])
    for category, values in grouped.items():
        sections.extend(["", category.replace("_", " ").title(), *values])
    return "\n".join(sections)


@lru_cache(maxsize=1)
def pdf_font():
    # Use system Unicode fonts without redistributing proprietary font files.
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont("MeridianUnicode", str(path)))
            return "MeridianUnicode"
    return "Helvetica"


def render_pdf(text: str) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=(595.28, 841.89),
        rightMargin=48,
        leftMargin=48,
        topMargin=46,
        bottomMargin=46,
        title=text.splitlines()[0],
        author="",
    )
    styles = getSampleStyleSheet()
    font = pdf_font()
    styles.add(
        ParagraphStyle(
            name="MeridianBody",
            fontName=font,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#21303b"),
            spaceAfter=7,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="MeridianTitle",
            parent=styles["Heading1"],
            fontName=font,
            fontSize=20,
            leading=25,
            textColor=colors.HexColor("#113f42"),
            spaceAfter=12,
        )
    )
    story = []
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            story.append(Spacer(1, 0.08 * inch))
        else:
            story.append(Paragraph(escape(line), styles["MeridianTitle"] if index == 0 else styles["MeridianBody"]))
    document.build(story)
    return output.getvalue()


def generate_document(db, data_dir, job_id, kind, fact_ids=None, approved=False):
    if kind not in {"cv", "cover_letter"}:
        raise ValueError("Document kind must be cv or cover_letter")
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if job is None:
        raise ValueError("Job not found")
    facts = select_facts(db, fact_ids)
    text = compose_document(job, kind, facts)
    existing = db.one("SELECT * FROM documents WHERE job_id=? AND kind=? ORDER BY created_at LIMIT 1", (job_id, kind))
    previous = (
        db.one(
            "SELECT text FROM document_versions WHERE document_id=? ORDER BY version DESC LIMIT 1", (existing["id"],)
        )
        if existing
        else None
    )
    if not previous:
        previous = db.one(
            "SELECT text FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE d.kind='import' ORDER BY v.created_at DESC LIMIT 1"
        )
    diff = "\n".join(
        difflib.unified_diff(
            (previous["text"] if previous else "").splitlines(),
            text.splitlines(),
            fromfile="previous",
            tofile="proposed",
            lineterm="",
        )
    )
    response = {
        "text": text,
        "diff": diff,
        "fact_ids": [f["id"] for f in facts],
        "facts_used": [{"id": f["id"], "value": f["value"], "source": f["source"]} for f in facts],
        "approved": bool(approved),
        "kind": kind,
    }
    if approved:
        pdf = render_pdf(text)
        document = existing
        if not document:
            stamp = now()
            document = {
                "id": uid(),
                "name": f"{job['company']} — {job['title']} ({kind})",
                "kind": kind,
                "job_id": job_id,
                "created_at": stamp,
                "updated_at": stamp,
            }
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", tuple(document.values()))
        response["document"] = document
        response["version"] = save_version(
            db,
            Path(data_dir),
            document,
            pdf,
            f"{kind}-{job_id[:8]}.pdf",
            "application/pdf",
            text,
            response["fact_ids"],
            True,
        )
        application = db.one("SELECT id,status FROM applications WHERE job_id=?", (job_id,))
        if application and application["status"] not in SUBMITTED_STATUSES:
            with db.transaction() as conn:
                attach_document(db, conn, application["id"], response["version"]["id"], kind)
    return response


def document_path(db, data_dir, version_id: str) -> tuple[Path, dict]:
    version = db.one("SELECT * FROM document_versions WHERE id=?", (version_id,))
    if not version:
        raise ValueError("Document version not found")
    path = Path(version["path"]).resolve()
    root = (Path(data_dir) / "documents").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Registered document is missing or outside the documents directory")
    if hashlib.sha256(path.read_bytes()).hexdigest() != version["content_hash"]:
        raise ValueError("Document content no longer matches its immutable registered version")
    return path, decode_row(version)
