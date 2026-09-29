"""Authenticated app routes. Authentication and origin validation are installed by main."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import decode_row, dumps, get_db, historical_facts, now, uid
from .discovery import canonical_url, ingest_job, ingest_url, rescore_job, run_sources
from .documents import (
    MAX_IMPORT,
    SUBMITTED_STATUSES,
    attach_document,
    document_path,
    generate_document,
    import_document,
    select_facts,
)
from .matching import normalized

router = APIRouter()
STATUSES = {
    "DISCOVERED",
    "SCORED",
    "SHORTLISTED",
    "IGNORED",
    "PREPARING",
    "NEEDS_REVIEW",
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
    "ERROR",
}
DEFAULT_SETTINGS = {
    "app_name": "Meridian",
    "theme": "system",
    "onboarding_completed": False,
    "automation_paused": True,
    "auto_apply": False,
    "default_mode": "review",
    "max_applications_day": 5,
    "max_applications_company": 2,
    "minimum_interval_seconds": 120,
    "min_match": 85,
    "min_confidence": 0.9,
    "allowed_domains": [],
    "browser_engine": "laya",
    "browser_confidence_threshold": 0.9,
    "laya_endpoint": "http://127.0.0.1:8791",
    "jev_endpoint": "",
    "jev_model": "",
    "jev_fallback_enabled": False,
    "text_provider": "none",
    "text_base_url": "",
    "text_model": "",
    "monthly_budget": 0,
    "notifications_enabled": True,
    "email_poll_minutes": 10,
    "email_interval_minutes": 10,
    "discovery_interval_minutes": 120,
    "scheduler_enabled": False,
    "rules": [],
    "sensitive_policies": {},
}


def validate_config_tree(value, depth=0):
    if depth > 12:
        raise HTTPException(422, "Configuration nesting exceeds the supported limit")
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(
                r"password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|credential", key, re.I
            ):
                raise HTTPException(422, "Credentials belong in Keychain integration settings")
            validate_config_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            validate_config_tree(child, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise HTTPException(422, "Configuration numbers must be finite")


def get_settings(db):
    result = dict(DEFAULT_SETTINGS)
    for row in db.query("SELECT key,value FROM settings"):
        try:
            result[row["key"]] = json.loads(row["value"])
        except ValueError:
            result[row["key"]] = row["value"]
    return result


def required(db, table, identifier):
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (identifier,))
    if row is None:
        raise HTTPException(404, "Item not found")
    return decode_row(row)


def list_result(db, table, where="", params=(), limit=100, offset=0, order="created_at DESC"):
    clause = f" WHERE {where}" if where else ""
    total = db.one(f"SELECT COUNT(*) AS n FROM {table}{clause}", params)["n"]
    rows = db.query(f"SELECT * FROM {table}{clause} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset))
    return {"items": [decode_row(row) for row in rows], "total": total}


def update_row(db, table, identifier, values):
    required(db, table, identifier)
    if values:
        values["updated_at"] = now()
        db.execute(
            f"UPDATE {table} SET {','.join(key + '=?' for key in values)} WHERE id=?",
            (*[dumps(value) if isinstance(value, (dict, list)) else value for value in values.values()], identifier),
        )
    return required(db, table, identifier)


def refresh_matches(db):
    offset = 0
    while True:
        jobs = db.query("SELECT * FROM jobs ORDER BY id LIMIT 100 OFFSET ?", (offset,))
        if not jobs:
            break
        for job in jobs:
            rescore_job(db, decode_row(job))
        offset += len(jobs)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FactPayload(Payload):
    category: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=300)
    value: str = Field(min_length=1, max_length=20000)
    source: str = Field(default="manual", max_length=1000)
    verification_status: Literal["verified", "unverified", "rejected"] = "unverified"
    locked: bool = False
    notes: str = Field(default="", max_length=10000)


class FactPatch(Payload):
    category: str | None = Field(default=None, min_length=1, max_length=80)
    key: str | None = Field(default=None, min_length=1, max_length=300)
    value: str | None = Field(default=None, min_length=1, max_length=20000)
    source: str | None = Field(default=None, max_length=1000)
    verification_status: Literal["verified", "unverified", "rejected"] | None = None
    locked: bool | None = None
    notes: str | None = Field(default=None, max_length=10000)


@router.get("/facts")
def facts_list(
    request: Request,
    category: str | None = None,
    q: str = "",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    clauses, params = [], []
    if category:
        clauses.append("category=?")
        params.append(category)
    if q:
        clauses.append("(key LIKE ? OR value LIKE ?)")
        params.extend([f"%{q}%"] * 2)
    return list_result(get_db(request), "facts", " AND ".join(clauses), params, limit, offset)


def sync_fact_entity(db, fact):
    category = fact["category"]
    with db.transaction() as conn:
        for table in ("education", "experiences", "projects", "candidate_skills"):
            conn.execute(f"DELETE FROM {table} WHERE fact_id=?", (fact["id"],))
        if category == "education":
            conn.execute(
                "INSERT INTO education(id,fact_id,qualification) VALUES(?,?,?)", (uid(), fact["id"], fact["value"])
            )
        elif category == "experience":
            conn.execute("INSERT INTO experiences(id,fact_id,title) VALUES(?,?,?)", (uid(), fact["id"], fact["value"]))
        elif category == "project":
            conn.execute("INSERT INTO projects(id,fact_id,name) VALUES(?,?,?)", (uid(), fact["id"], fact["value"]))
        elif category == "skill":
            conn.execute("INSERT OR IGNORE INTO skills(id,name) VALUES(?,?)", (uid(), normalized(fact["value"])))
            skill = conn.execute("SELECT id FROM skills WHERE name=?", (normalized(fact["value"]),)).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO candidate_skills VALUES(?,?,?)", (fact["candidate_id"], skill["id"], fact["id"])
            )


def invalidate_fact_evidence(db, conn, fact, seen=None):
    seen = seen if seen is not None else set()
    if fact["id"] in seen:
        return
    seen.add(fact["id"])
    dependents = conn.execute(
        "SELECT id FROM saved_answers WHERE EXISTS(SELECT 1 FROM json_each(saved_answers.fact_ids) WHERE value=?)",
        (fact["id"],),
    ).fetchall()
    for saved in dependents:
        derived = conn.execute(
            "SELECT * FROM facts WHERE source=? AND id!=?", ("manual_answer:" + saved["id"], fact["id"])
        ).fetchone()
        if derived and derived["id"] not in seen:
            invalidate_fact_evidence(db, conn, dict(derived), seen)
            conn.execute(
                "UPDATE facts SET verification_status='unverified',locked=0,updated_at=? WHERE id=?",
                (now(), derived["id"]),
            )
    conn.execute(
        "INSERT INTO fact_history VALUES(?,?,?,?,?)", (uid(), fact["id"], dumps(fact), fact["updated_at"], now())
    )
    conn.execute(
        "UPDATE saved_answers SET verified=0,updated_at=? WHERE EXISTS(SELECT 1 FROM json_each(saved_answers.fact_ids) WHERE value=?)",
        (now(), fact["id"]),
    )
    applications = conn.execute(
        "SELECT DISTINCT a.id,a.status FROM applications a JOIN application_answers aa ON aa.application_id=a.id WHERE EXISTS(SELECT 1 FROM json_each(aa.fact_ids) WHERE value=?)",
        (fact["id"],),
    ).fetchall()
    for application in applications:
        if application["status"] not in SUBMITTED_STATUSES:
            conn.execute(
                "UPDATE application_answers SET verified=0 WHERE application_id=? AND EXISTS(SELECT 1 FROM json_each(application_answers.fact_ids) WHERE value=?)",
                (application["id"], fact["id"]),
            )
        db.event(
            application["id"],
            application["status"],
            f"Candidate fact {fact['id']} changed; historical answer evidence retained. Future reuse requires review.",
            "system",
            conn=conn,
        )
    conn.execute(
        "UPDATE document_versions SET approved=0 WHERE EXISTS(SELECT 1 FROM json_each(document_versions.fact_ids) WHERE value=?)",
        (fact["id"],),
    )


@router.post("/facts", status_code=201)
def fact_create(payload: FactPayload, request: Request):
    db, stamp = get_db(request), now()
    values = payload.model_dump()
    values.update(
        id=uid(), candidate_id=db.one("SELECT id FROM candidates LIMIT 1")["id"], created_at=stamp, updated_at=stamp
    )
    db.execute(f"INSERT INTO facts({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
    sync_fact_entity(db, values)
    refresh_matches(db)
    return required(db, "facts", values["id"])


@router.patch("/facts/{fact_id}")
def fact_update(fact_id: str, payload: FactPatch, request: Request):
    db = get_db(request)
    fact = required(db, "facts", fact_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if (
        fact["locked"]
        and any(
            key in changes and changes[key] != fact[key] for key in ("value", "key", "category", "verification_status")
        )
        and changes.get("locked") is not False
    ):
        raise HTTPException(409, "Unlock this fact before changing its content")
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Fact not found")
        current = dict(row)
        if (
            current["locked"]
            and any(
                key in changes and changes[key] != current[key]
                for key in ("value", "key", "category", "verification_status")
            )
            and changes.get("locked") is not False
        ):
            raise HTTPException(409, "Unlock this fact before changing its content")
        material = any(
            key in changes and changes[key] != current[key]
            for key in ("value", "key", "category", "verification_status", "locked")
        )
        if material:
            invalidate_fact_evidence(db, conn, current)
        elif changes:
            conn.execute(
                "INSERT INTO fact_history VALUES(?,?,?,?,?)",
                (uid(), fact_id, dumps(current), current["updated_at"], now()),
            )
        if changes:
            changes["updated_at"] = now()
            conn.execute(
                f"UPDATE facts SET {','.join(key + '=?' for key in changes)} WHERE id=?", (*changes.values(), fact_id)
            )
    updated = required(db, "facts", fact_id)
    sync_fact_entity(db, updated)
    refresh_matches(db)
    return updated


@router.delete("/facts/{fact_id}")
def fact_delete(fact_id: str, request: Request):
    db = get_db(request)
    fact = required(db, "facts", fact_id)
    if fact["locked"]:
        raise HTTPException(409, "Unlock this fact before deleting it")
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Fact not found")
        if row["locked"]:
            raise HTTPException(409, "Unlock this fact before deleting it")
        invalidate_fact_evidence(db, conn, dict(row))
        conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
    refresh_matches(db)
    return {"deleted": True}


class RelationshipPayload(Payload):
    from_fact_id: str
    to_fact_id: str
    kind: str = Field(min_length=1, max_length=100)
    notes: str = ""


@router.get("/relationships")
def relationships(request: Request):
    return list_result(get_db(request), "relationships", limit=500)


@router.post("/relationships", status_code=201)
def relationship_create(payload: RelationshipPayload, request: Request):
    db = get_db(request)
    required(db, "facts", payload.from_fact_id)
    required(db, "facts", payload.to_fact_id)
    if payload.from_fact_id == payload.to_fact_id:
        raise HTTPException(422, "A relationship needs two different facts")
    identifier = uid()
    try:
        db.execute(
            "INSERT INTO relationships VALUES(?,?,?,?,?,?)",
            (identifier, payload.from_fact_id, payload.to_fact_id, payload.kind, payload.notes, now()),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Relationship already exists") from exc
    return required(db, "relationships", identifier)


@router.post("/documents/import", status_code=201)
async def document_import(request: Request, file: UploadFile = File(...)):
    content = await file.read(MAX_IMPORT + 1)
    try:
        return import_document(get_db(request), request.app.state.data_dir, content, file.filename or "document.txt")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/documents")
def documents_list(request: Request, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    db = get_db(request)
    result = list_result(db, "documents", limit=limit, offset=offset)
    for document in result["items"]:
        version = decode_row(
            db.one(
                "SELECT * FROM document_versions WHERE document_id=? ORDER BY version DESC LIMIT 1", (document["id"],)
            )
        )
        document.update(
            latest_version=version,
            latest_version_id=version["id"] if version else None,
            versions_count=db.one("SELECT COUNT(*) AS n FROM document_versions WHERE document_id=?", (document["id"],))[
                "n"
            ],
        )
    return result


@router.get("/documents/{document_id}/versions")
def document_versions(document_id: str, request: Request):
    db = get_db(request)
    required(db, "documents", document_id)
    result = list_result(db, "document_versions", "document_id=?", (document_id,), order="version DESC")
    for version in result["items"]:
        version["facts_used"] = historical_facts(db, version["fact_ids"], version["created_at"])
    return result


@router.get("/document-versions/{version_id}/download")
def document_download(version_id: str, request: Request):
    try:
        path, version = document_path(get_db(request), request.app.state.data_dir, version_id)
        return FileResponse(path, media_type=version["mime_type"], filename=version["filename"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/document-versions/{version_id}/approve")
def document_approve(version_id: str, request: Request):
    db = get_db(request)
    try:
        document_path(db, request.app.state.data_dir, version_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.execute("UPDATE document_versions SET approved=1 WHERE id=?", (version_id,))
    return required(db, "document_versions", version_id)


class GeneratePayload(Payload):
    job_id: str
    kind: Literal["cv", "cover_letter"]
    fact_ids: list[str] = Field(default_factory=list, max_length=500)
    approved: bool = False


@router.post("/documents/generate")
def document_generate(payload: GeneratePayload, request: Request):
    try:
        return generate_document(get_db(request), request.app.state.data_dir, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class JobPayload(Payload):
    title: str = Field(min_length=1, max_length=400)
    company: str = Field(min_length=1, max_length=300)
    url: str = Field(max_length=4000)
    application_url: str = ""
    location: str = ""
    description: str = Field(default="", max_length=250000)
    source: str = "manual"
    ats: str = "generic"
    skills: list[str] = Field(default_factory=list)
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    currency: str | None = None
    remote: bool | None = None
    posted_at: str | None = None
    experience_level: str | None = None
    industries: list[str] = Field(default_factory=list)
    work_arrangement: Literal["remote", "hybrid", "onsite"] | None = None
    contract_types: list[
        Literal["full_time", "part_time", "contract", "temporary", "internship", "volunteer", "other"]
    ] = Field(default_factory=list)
    sponsorship_available: bool | None = None
    education_requirements: str | None = None
    experience_years_required: float | None = Field(default=None, ge=0, le=100)
    salary_unit: str | None = None


@router.get("/jobs")
def jobs_list(
    request: Request,
    q: str = "",
    status: str | None = None,
    source: str | None = None,
    min_match: float = 0,
    profile_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    clauses, params = ["match_score>=?"], [min_match]
    for key, value in (("status", status), ("source", source)):
        if value:
            clauses.append(f"{key}=?")
            params.append(value)
    if q.strip():
        terms = re.findall(r"[\w]+", q)[:12]
        if terms:
            clauses.append("rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?)")
            params.append(" AND ".join('"' + term + '"*' for term in terms))
    if profile_id:
        required(get_db(request), "search_profiles", profile_id)
        clauses.append(
            "EXISTS(SELECT 1 FROM json_each(jobs.match_details,'$.profiles') p WHERE json_extract(p.value,'$.id')=? AND json_extract(p.value,'$.eligible')=1)"
        )
        params.append(profile_id)
    return list_result(
        get_db(request), "jobs", " AND ".join(clauses), params, limit, offset, "priority_score DESC,created_at DESC"
    )


@router.post("/jobs", status_code=201)
def job_create(payload: JobPayload, request: Request):
    try:
        job, duplicate = ingest_job(get_db(request), payload.model_dump())
        job["duplicate"] = duplicate
        return job
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class URLPayload(Payload):
    url: str = Field(min_length=1, max_length=4000)


@router.post("/jobs/ingest")
async def job_ingest(payload: URLPayload, request: Request):
    import httpx

    try:
        return await ingest_url(get_db(request), payload.url)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, request: Request):
    db = get_db(request)
    job = required(db, "jobs", job_id)
    job["application"] = decode_row(db.one("SELECT * FROM applications WHERE job_id=?", (job_id,)))
    job["sources"] = db.query("SELECT * FROM job_sources WHERE job_id=?", (job_id,))
    return job


class JobPatch(Payload):
    status: str | None = None
    description: str | None = Field(default=None, max_length=250000)
    application_url: str | None = None


@router.patch("/jobs/{job_id}")
def job_update(job_id: str, payload: JobPatch, request: Request):
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if changes.get("status") and changes["status"] not in STATUSES:
        raise HTTPException(422, "Unknown pipeline status")
    if changes.get("application_url"):
        try:
            changes["application_url"] = canonical_url(changes["application_url"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    job = update_row(get_db(request), "jobs", job_id, changes)
    if "description" in changes:
        rescore_job(get_db(request), job)
    return required(get_db(request), "jobs", job_id)


@router.get("/jobs/{job_id}/match")
def job_match(job_id: str, request: Request):
    return rescore_job(get_db(request), required(get_db(request), "jobs", job_id))


class PreparePayload(Payload):
    mode: Literal["manual", "review", "auto"] = "review"
    document_version_id: str | None = None


@router.post("/jobs/{job_id}/prepare")
def job_prepare(job_id: str, payload: PreparePayload, request: Request):
    return prepare_application(
        get_db(request), job_id, payload.mode, payload.document_version_id, request.app.state.data_dir
    )


def prepare_application(db, job_id, mode="review", document_version_id=None, data_dir=None):
    if data_dir is None:
        data_dir = db.path.parent.parent
    payload = PreparePayload(mode=mode, document_version_id=document_version_id)
    stamp = now()
    job = required(db, "jobs", job_id)
    document = None
    if payload.document_version_id:
        document = required(db, "document_versions", payload.document_version_id)
        try:
            document_path(db, data_dir, document["id"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    with db.transaction() as conn:
        existing = conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            application_id = existing["id"]
        else:
            application_id = uid()
            conn.execute(
                "INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES(?,?,?,'PREPARING',?,?)",
                (application_id, job_id, payload.mode, stamp, stamp),
            )
            conn.execute(
                "INSERT INTO application_events VALUES(?,?,?,?,?,?)",
                (uid(), application_id, "PREPARING", "Application workspace prepared", "manual", stamp),
            )
            conn.execute("UPDATE jobs SET status='SHORTLISTED',updated_at=? WHERE id=?", (stamp, job_id))
        if document:
            try:
                attach_document(db, conn, application_id, document["id"], "cv")
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
    application = required(db, "applications", application_id)
    if not existing:
        trusted = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
        if trusted and not document:
            # Preparation produces a reviewable draft; saving/uploading needs explicit approval.
            draft = generate_document(db, data_dir, job_id, "cv", approved=False)
            application["draft"] = draft
        else:
            application["draft"] = None
        question = (
            "Review candidate facts and approve a tailored CV before filling this application"
            if not document
            else "Review the selected CV and application answers before submitting"
        )
        db.execute(
            "INSERT INTO human_tasks(id,application_id,kind,question,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (uid(), application_id, "PREPARATION_REVIEW", question, stamp, stamp),
        )
        db.event(application_id, "NEEDS_REVIEW", "Documents and candidate evidence require review", "system")
        application["status"] = "NEEDS_REVIEW"
    application["job"] = job
    application["duplicate"] = bool(existing)
    return application


class FeedbackPayload(Payload):
    interested: bool
    reason: str = Field(default="", max_length=500)


@router.post("/jobs/{job_id}/feedback")
def job_feedback(job_id: str, payload: FeedbackPayload, request: Request):
    db = get_db(request)
    required(db, "jobs", job_id)
    db.execute("INSERT INTO job_feedback VALUES(?,?,?,?,?)", (uid(), job_id, payload.interested, payload.reason, now()))
    db.execute(
        "UPDATE jobs SET status=?,updated_at=? WHERE id=?",
        ("SHORTLISTED" if payload.interested else "IGNORED", now(), job_id),
    )
    refresh_matches(db)
    return required(db, "jobs", job_id)


class SourcePayload(Payload):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "greenhouse",
        "lever",
        "ashby",
        "github",
        "json",
        "csv",
        "url",
        "careers",
        "company",
        "linkedin",
        "indeed",
        "workday",
        "generic",
        "html",
        "rss",
        "smartrecruiters",
        "workable",
        "teamtailor",
        "icims",
        "taleo",
    ]
    url: str = ""
    enabled: bool = True
    config: dict = Field(default_factory=dict)
    poll_minutes: int = Field(default=360, ge=15, le=10080)


def validate_source(values):
    validate_config_tree(values.get("config", {}))
    if values.get("url"):
        try:
            values["url"] = canonical_url(values["url"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    config = values.get("config", {})
    if any(re.search(r"password|secret|api.?key|access.?token|refresh.?token", key, re.I) for key in config):
        raise HTTPException(422, "Save credentials in an integration's Keychain settings")
    return values


@router.get("/sources")
def sources_list(request: Request):
    return list_result(get_db(request), "sources", limit=500)


@router.post("/sources", status_code=201)
def source_create(payload: SourcePayload, request: Request):
    values = validate_source(payload.model_dump())
    values.update(id=uid(), created_at=now(), updated_at=now())
    get_db(request).execute(
        f"INSERT INTO sources({','.join(values)}) VALUES({','.join('?' for _ in values)})",
        tuple(dumps(v) if isinstance(v, dict) else v for v in values.values()),
    )
    return required(get_db(request), "sources", values["id"])


@router.patch("/sources/{source_id}")
def source_update(source_id: str, payload: dict, request: Request):
    db = get_db(request)
    existing = required(db, "sources", source_id)
    try:
        full = SourcePayload(**({k: existing[k] for k in SourcePayload.model_fields} | payload))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return update_row(db, "sources", source_id, validate_source(full.model_dump()))


@router.delete("/sources/{source_id}")
def source_delete(source_id: str, request: Request):
    required(get_db(request), "sources", source_id)
    get_db(request).execute("DELETE FROM sources WHERE id=?", (source_id,))
    return {"deleted": True}


class RunPayload(Payload):
    source_id: str | None = None


@router.post("/sources/run")
async def sources_run(payload: RunPayload, request: Request):
    try:
        return await run_sources(get_db(request), request.app.state.data_dir, payload.source_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class CompanyPayload(Payload):
    name: str = Field(min_length=1, max_length=300)
    careers_url: str = ""
    locations: list[str] = Field(default_factory=list)
    preferred_roles: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=0, le=100)
    notes: str = ""
    poll_minutes: int = Field(default=360, ge=15, le=10080)
    auto_apply: bool = False
    salary_preference: dict = Field(default_factory=dict)
    blacklist_keywords: list[str] = Field(default_factory=list)


def sync_company_source(db, company):
    if not company["careers_url"]:
        return
    source = db.one("SELECT id FROM sources WHERE json_extract(config,'$.company_id')=?", (company["id"],))
    config = {"company_id": company["id"], "company_name": company["name"]}
    if source:
        db.execute(
            "UPDATE sources SET name=?,url=?,config=?,poll_minutes=?,updated_at=? WHERE id=?",
            (
                company["name"] + " careers",
                company["careers_url"],
                dumps(config),
                company["poll_minutes"],
                now(),
                source["id"],
            ),
        )
    else:
        db.execute(
            "INSERT INTO sources(id,name,kind,url,config,poll_minutes,created_at,updated_at) VALUES(?,?,'careers',?,?,?,?,?)",
            (
                uid(),
                company["name"] + " careers",
                company["careers_url"],
                dumps(config),
                company["poll_minutes"],
                now(),
                now(),
            ),
        )


@router.get("/companies")
def companies_list(request: Request, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return list_result(get_db(request), "companies", limit=limit, offset=offset, order="priority DESC,name")


@router.post("/companies", status_code=201)
def company_create(payload: CompanyPayload, request: Request):
    db = get_db(request)
    values = payload.model_dump()
    if values["careers_url"]:
        try:
            values["careers_url"] = canonical_url(values["careers_url"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    values.update(id=uid(), created_at=now(), updated_at=now())
    try:
        db.execute(
            f"INSERT INTO companies({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(dumps(v) if isinstance(v, (dict, list)) else v for v in values.values()),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Company already exists") from exc
    sync_company_source(db, values)
    db.execute("UPDATE jobs SET company_id=? WHERE company=? COLLATE NOCASE", (values["id"], values["name"]))
    return required(db, "companies", values["id"])


@router.patch("/companies/{company_id}")
def company_update(company_id: str, payload: dict, request: Request):
    db = get_db(request)
    existing = required(db, "companies", company_id)
    try:
        values = CompanyPayload(**({k: existing[k] for k in CompanyPayload.model_fields} | payload)).model_dump()
        if values["careers_url"]:
            values["careers_url"] = canonical_url(values["careers_url"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        updated = update_row(db, "companies", company_id, values)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Company name already exists") from exc
    sync_company_source(db, updated)
    return updated


@router.delete("/companies/{company_id}")
def company_delete(company_id: str, request: Request):
    db = get_db(request)
    required(db, "companies", company_id)
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET company_id=NULL WHERE company_id=?", (company_id,))
        conn.execute("DELETE FROM sources WHERE json_extract(config,'$.company_id')=?", (company_id,))
        conn.execute("DELETE FROM companies WHERE id=?", (company_id,))
    return {"deleted": True}


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote: bool | None = None
    minimum_salary: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    experience_levels: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    work_arrangements: list[Literal["remote", "hybrid", "onsite"]] = Field(default_factory=list)
    contract_types: list[
        Literal["full_time", "part_time", "contract", "temporary", "internship", "volunteer", "other"]
    ] = Field(default_factory=list)
    max_posting_age_days: int | None = Field(default=None, ge=1, le=3650)
    requires_sponsorship: bool | None = None
    weights: dict[
        Literal[
            "skills", "role", "location", "seniority", "salary", "technology_preference", "education", "experience"
        ],
        float,
    ] = Field(default_factory=dict)
    mode: Literal["manual", "review", "auto"] = "review"
    min_match: float = Field(default=50, ge=0, le=100)
    stretch_factor: float = Field(default=0.15, ge=0, le=1)

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, value):
        if any(not math.isfinite(weight) or weight < 0 for weight in value.values()):
            raise ValueError("Matching weights must be finite and nonnegative")
        if value and (not math.isfinite(sum(value.values())) or sum(value.values()) <= 0):
            raise ValueError("At least one matching weight must be positive, with a finite total")
        return {key: weight / sum(value.values()) * 100 for key, weight in value.items()} if value else {}


class ProfilePayload(Payload):
    name: str = Field(min_length=1, max_length=200)
    config: SearchConfig = Field(default_factory=SearchConfig)


class RuleConditions(Payload):
    min_match: float = Field(default=85, ge=0, le=100)
    location: str = ""
    role_contains: str = ""
    ats: str = ""


class AutomationRule(Payload):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    action: Literal["prepare_application", "auto_apply"] = "prepare_application"


@router.get("/search-profiles")
def profiles_list(request: Request):
    return list_result(get_db(request), "search_profiles", limit=500)


@router.post("/search-profiles", status_code=201)
def profile_create(payload: ProfilePayload, request: Request):
    db, identifier, stamp = get_db(request), uid(), now()
    db.execute(
        "INSERT INTO search_profiles VALUES(?,?,?,?,?)",
        (identifier, payload.name, dumps(payload.config.model_dump()), stamp, stamp),
    )
    refresh_matches(db)
    return required(db, "search_profiles", identifier)


@router.patch("/search-profiles/{profile_id}")
def profile_update(profile_id: str, payload: dict, request: Request):
    db = get_db(request)
    existing = required(db, "search_profiles", profile_id)
    try:
        values = ProfilePayload(**({"name": existing["name"], "config": existing["config"]} | payload)).model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result = update_row(db, "search_profiles", profile_id, values)
    refresh_matches(db)
    return result


@router.delete("/search-profiles/{profile_id}")
def profile_delete(profile_id: str, request: Request):
    db = get_db(request)
    required(db, "search_profiles", profile_id)
    db.execute("DELETE FROM search_profiles WHERE id=?", (profile_id,))
    refresh_matches(db)
    return {"deleted": True}


@router.get("/applications")
def applications_list(
    request: Request,
    status: str | None = None,
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    db, params, clauses = get_db(request), [], []
    if status:
        clauses.append("a.status=?")
        params.append(status)
    if q:
        clauses.append("(j.title LIKE ? OR j.company LIKE ?)")
        params.extend([f"%{q}%"] * 2)
    clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.query(
        "SELECT a.*,j.title,j.company,j.location,j.ats,j.match_score FROM applications a JOIN jobs j ON j.id=a.job_id"
        + clause
        + " ORDER BY a.updated_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    total = db.one("SELECT COUNT(*) AS n FROM applications a JOIN jobs j ON j.id=a.job_id" + clause, params)["n"]
    return {"items": [decode_row(row) for row in rows], "total": total}


@router.get("/applications/{application_id}")
def application_detail(application_id: str, request: Request):
    db = get_db(request)
    result = required(db, "applications", application_id)
    result["job"] = required(db, "jobs", result["job_id"])
    for name, table in (
        ("events", "application_events"),
        ("answers", "application_answers"),
        ("runs", "automation_runs"),
        ("tasks", "human_tasks"),
    ):
        result[name] = [
            decode_row(row)
            for row in db.query(f"SELECT * FROM {table} WHERE application_id=? ORDER BY created_at", (application_id,))
        ]
    for answer in result["answers"]:
        answer["evidence"] = historical_facts(db, answer["fact_ids"], answer["created_at"])
    result["documents"] = [
        decode_row(row)
        for row in db.query(
            "SELECT v.*,d.kind FROM document_versions v JOIN application_documents d ON v.id=d.document_version_id WHERE d.application_id=? ORDER BY v.created_at DESC",
            (application_id,),
        )
    ]
    result["emails"] = [
        decode_row(row)
        for row in db.query(
            "SELECT * FROM email_messages WHERE application_id=? ORDER BY received_at DESC", (application_id,)
        )
    ]
    return result


class ApplicationPatch(Payload):
    status: str | None = None
    notes: str | None = Field(default=None, max_length=20000)
    mode: Literal["manual", "review", "auto"] | None = None


@router.patch("/applications/{application_id}")
def application_update(application_id: str, payload: ApplicationPatch, request: Request):
    db = get_db(request)
    required(db, "applications", application_id)
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    status = values.pop("status", None)
    if status:
        if status not in STATUSES:
            raise HTTPException(422, "Unknown pipeline status")
        db.event(application_id, status, "Status updated manually", "manual")
    update_row(db, "applications", application_id, values)
    return application_detail(application_id, request)


class AnswerPayload(Payload):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=20000)
    fact_ids: list[str] = Field(default_factory=list)
    verified: bool = True


def save_answer(
    db, question, answer, fact_ids, verified, application_id=None, source_note="Explicit answer supplied by the user"
):
    if fact_ids:
        try:
            select_facts(db, fact_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    identifier, stamp = uid(), now()
    key = re.sub(r"[^\w]+", " ", normalized(question)).strip()
    with db.transaction() as conn:
        previous = conn.execute("SELECT id FROM saved_answers WHERE question=?", (key,)).fetchone()
        saved_id = previous["id"] if previous else uid()
        source = f"manual_answer:{saved_id}"
        manual_fact = conn.execute("SELECT * FROM facts WHERE source=? LIMIT 1", (source,)).fetchone()
        verification = "verified" if verified else "unverified"
        if manual_fact:
            manual_fact_id = manual_fact["id"]
            if manual_fact["value"] != answer or manual_fact["verification_status"] != verification:
                invalidate_fact_evidence(db, conn, dict(manual_fact))
                conn.execute(
                    "UPDATE facts SET value=?,verification_status=?,notes=?,updated_at=? WHERE id=?",
                    (answer, verification, source_note, stamp, manual_fact_id),
                )
        else:
            manual_fact_id = uid()
            candidate_id = conn.execute("SELECT id FROM candidates LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    manual_fact_id,
                    candidate_id,
                    "saved_answer",
                    key,
                    answer,
                    source,
                    verification,
                    0,
                    source_note,
                    stamp,
                    stamp,
                ),
            )
        provenance = list(dict.fromkeys([*fact_ids, manual_fact_id]))
        if previous:
            conn.execute(
                "UPDATE saved_answers SET answer=?,fact_ids=?,verified=?,updated_at=? WHERE id=?",
                (answer, dumps(provenance), verified, stamp, saved_id),
            )
        else:
            conn.execute(
                "INSERT INTO saved_answers VALUES(?,?,?,?,?,?,?)",
                (saved_id, key, answer, dumps(provenance), verified, stamp, stamp),
            )
        if application_id:
            conn.execute(
                "INSERT INTO application_answers VALUES(?,?,?,?,?,?,?)",
                (identifier, application_id, question, answer, dumps(provenance), verified, stamp),
            )
    return {
        "id": identifier,
        "question": question,
        "answer": answer,
        "fact_ids": provenance,
        "verified": verified,
        "saved_answer_id": saved_id,
        "candidate_fact_id": manual_fact_id,
    }


@router.post("/applications/{application_id}/answers", status_code=201)
def application_answer(application_id: str, payload: AnswerPayload, request: Request):
    db = get_db(request)
    required(db, "applications", application_id)
    return save_answer(db, **payload.model_dump(), application_id=application_id)


@router.get("/human-tasks")
def human_tasks(
    request: Request, status: str = "open", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
):
    return list_result(get_db(request), "human_tasks", "lower(status)=lower(?)", (status,), limit, offset)


class ResolvePayload(Payload):
    answer: str = Field(min_length=1, max_length=20000)
    save: bool = True


@router.post("/human-tasks/{task_id}/resolve")
def task_resolve(task_id: str, payload: ResolvePayload, request: Request):
    db = get_db(request)
    task = required(db, "human_tasks", task_id)
    if task["status"].lower() != "open":
        raise HTTPException(409, "This task has already been resolved")
    if payload.save and task["kind"] not in {
        "PREPARATION_REVIEW",
        "INTERRUPTED",
        "AUTO_GATE",
        "AUTOMATION_ERROR",
        "SUBMISSION_UNCONFIRMED",
    }:
        save_answer(
            db,
            task["question"],
            payload.answer,
            [],
            True,
            task["application_id"],
            source_note=f"Explicit answer from human_task:{task_id}",
        )

    db.execute(
        "UPDATE human_tasks SET answer=?,status='resolved',updated_at=? WHERE id=?", (payload.answer, now(), task_id)
    )
    if task["application_id"]:
        app = required(db, "applications", task["application_id"])
        db.event(app["id"], app["status"], "Human input recorded: " + task["question"][:200], "manual")
    return required(db, "human_tasks", task_id)


@router.get("/activity")
def activity(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    db = get_db(request)
    return {
        "items": db.query(
            "SELECT e.*,j.title,j.company FROM application_events e JOIN applications a ON a.id=e.application_id JOIN jobs j ON j.id=a.job_id ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ),
        "total": db.one("SELECT COUNT(*) AS n FROM application_events")["n"],
    }


@router.get("/dashboard")
def dashboard(request: Request):
    from .analytics import pipeline_analytics

    db = get_db(request)
    settings = get_settings(db)
    history = pipeline_analytics(db)

    def scalar(sql, params=()):
        return db.one(sql, params)["n"]

    stats = {
        "jobs": scalar("SELECT COUNT(*) AS n FROM jobs"),
        "strong_matches": scalar(
            "SELECT COUNT(*) AS n FROM jobs WHERE match_score>=? AND status!='IGNORED' AND CASE WHEN json_valid(match_details) THEN json_extract(match_details,'$.eligible') ELSE 0 END=1",
            (settings["min_match"],),
        ),
        "applications": scalar("SELECT COUNT(*) AS n FROM applications"),
        "needs_review": scalar("SELECT COUNT(*) AS n FROM applications WHERE status='NEEDS_REVIEW'"),
        "applied": history["submitted"],
        "interviews": history["interviews"],
        "offers": history["offers"],
        "open_tasks": scalar("SELECT COUNT(*) AS n FROM human_tasks WHERE lower(status)='open'"),
        "verified_facts": scalar("SELECT COUNT(*) AS n FROM facts WHERE verification_status='verified' OR locked=1"),
    }
    deadlines = db.query(
        "SELECT e.id,e.subject,e.deadline,e.application_id,j.title,j.company FROM email_messages e LEFT JOIN applications a ON a.id=e.application_id LEFT JOIN jobs j ON j.id=a.job_id WHERE e.deadline IS NOT NULL AND e.deadline>=? ORDER BY e.deadline LIMIT 20",
        (now()[:10],),
    )
    return {
        "stats": stats,
        "recent_events": activity(request, limit=10, offset=0)["items"],
        "deadlines": deadlines,
        "source_health": sources_list(request)["items"],
        "automation_paused": settings["automation_paused"],
    }


@router.get("/analytics")
def analytics(request: Request):
    from .analytics import pipeline_analytics

    return pipeline_analytics(get_db(request))


@router.get("/search")
def search(request: Request, q: str = Query(min_length=1, max_length=300)):
    db = get_db(request)
    like, items = "%" + q + "%", []
    definitions = [
        ("job", "SELECT id,title AS title,company AS subtitle FROM jobs WHERE title LIKE ? OR company LIKE ?"),
        ("company", "SELECT id,name AS title,careers_url AS subtitle FROM companies WHERE name LIKE ? OR notes LIKE ?"),
        (
            "application",
            "SELECT a.id,j.title AS title,j.company AS subtitle FROM applications a JOIN jobs j ON j.id=a.job_id WHERE j.title LIKE ? OR j.company LIKE ?",
        ),
        (
            "email",
            "SELECT id,subject AS title,sender AS subtitle FROM email_messages WHERE subject LIKE ? OR body LIKE ?",
        ),
        ("fact", "SELECT id,key AS title,value AS subtitle FROM facts WHERE key LIKE ? OR value LIKE ?"),
        (
            "answer",
            "SELECT id,question AS title,answer AS subtitle FROM saved_answers WHERE question LIKE ? OR answer LIKE ?",
        ),
        (
            "document",
            "SELECT d.id,d.name AS title,d.kind AS subtitle FROM documents d WHERE d.name LIKE ? OR EXISTS(SELECT 1 FROM document_versions v WHERE v.document_id=d.id AND v.text LIKE ?)",
        ),
    ]
    for kind, sql in definitions:
        items.extend(
            dict(row, type=kind, subtitle=str(row["subtitle"] or "")[:250])
            for row in db.query(sql + " LIMIT 12", (like, like))
        )
    return {"items": items, "total": len(items)}


@router.get("/settings")
def settings_get(request: Request):
    return get_settings(get_db(request))


@router.patch("/settings")
async def settings_patch(payload: dict, request: Request):
    validate_config_tree(payload)
    if len(dumps(payload)) > 100000:
        raise HTTPException(422, "Settings payload is too large")
    for key, value in payload.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) or re.search(
            r"password|secret|api_key|access_token|refresh_token", key
        ):
            raise HTTPException(422, "Secrets belong in Keychain integration settings")
        if key in DEFAULT_SETTINGS and DEFAULT_SETTINGS[key] is not None:
            expected = type(DEFAULT_SETTINGS[key])
            if expected in (bool, str, list, dict) and not isinstance(value, expected):
                raise HTTPException(422, f"Invalid type for {key}")
        if key in {
            "max_applications_day",
            "max_applications_company",
            "minimum_interval_seconds",
            "email_poll_minutes",
            "email_interval_minutes",
            "discovery_interval_minutes",
        } and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400):
            raise HTTPException(422, f"{key} must be an integer between 1 and 86400")
        if key in {"min_confidence", "browser_confidence_threshold"} and (
            not isinstance(value, (int, float)) or not 0.5 <= value <= 1
        ):
            raise HTTPException(422, "Confidence must be between 0.5 and 1")
        if key == "min_match" and (not isinstance(value, (int, float)) or not 0 <= value <= 100):
            raise HTTPException(422, "Minimum match must be between 0 and 100")
        if key == "monthly_budget" and (not isinstance(value, (int, float)) or value < 0):
            raise HTTPException(422, "Budget must be nonnegative")
        if key == "allowed_domains" and any(
            not isinstance(domain, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+", domain) or "." not in domain
            for domain in value
        ):
            raise HTTPException(422, "Allowed domains must be hostnames without paths or wildcards")
        if key == "default_mode" and value not in {"manual", "review", "auto"}:
            raise HTTPException(422, "Invalid application mode")
        if key == "browser_engine" and value not in {"laya", "jev", "hybrid", "deterministic"}:
            raise HTTPException(422, "Invalid browser engine")
        if key == "text_provider" and value not in {
            "none",
            "ollama",
            "openai",
            "anthropic",
            "gemini",
            "openai-compatible",
        }:
            raise HTTPException(422, "Unknown text provider")
        if key == "rules":
            if len(value) > 100:
                raise HTTPException(422, "At most 100 automation rules are supported")
            try:
                payload[key] = [AutomationRule(**rule).model_dump() for rule in value]
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, f"Invalid automation rule: {exc}") from exc
    with get_db(request).transaction() as conn:
        for key, value in payload.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, dumps(value)),
            )
    automation = getattr(request.app.state, "automation", None)
    if automation and "automation_paused" in payload:
        if payload["automation_paused"]:
            automation.pause()
        else:
            await automation.resume()
    return get_settings(get_db(request))
