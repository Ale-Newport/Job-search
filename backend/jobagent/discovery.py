"""Bounded public-source discovery and atomic canonical job ingestion."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import io
import ipaddress
import json
import math
import re
import socket
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .db import decode_row, dumps, now, uid
from .matching import (
    contract_types,
    candidate_match_evidence,
    extract_skills,
    match_job,
    normalized,
    numeric_years,
    text_values,
    work_arrangement,
)

MAX_BODY = 8 * 1024 * 1024
MAX_JOBS = 2000
_active_sources: set[str] = set()
_source_guard = threading.Lock()
TRACKING_PARAMS = {"ref", "source", "src", "gh_src", "lever-source", "trk", "trackingid", "refid", "fbclid", "gclid"}


class DiscoveryResult(list):
    """List-compatible postings with explicit limits and authoritative scope."""
    def __init__(self, jobs=(), *, complete=False, authoritative=False, method='', endpoints=(), total=None, limitations=()):
        super().__init__(jobs)
        self.evidence = {'complete': complete, 'authoritative': authoritative, 'method': method,
                         'endpoints': list(endpoints), 'published_total': total, 'limitations': list(limitations)}


def posting_collection(data, key=None):
    items = data.get(key) if isinstance(data, dict) and key else data if key is None else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('Public board returned an invalid posting collection; this is not an empty board')
    return items


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use a public HTTP or HTTPS URL without credentials")
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:
        host = "[" + host + "]"
    if parts.port and parts.port not in {80, 443}:
        host += f":{parts.port}"
    path = re.sub("/+", "/", parts.path).rstrip("/") or "/"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.casefold().startswith("utm_") and k.casefold() not in TRACKING_PARAMS
    )
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def ats_identity(url: str) -> tuple[str, str | None]:
    parts = urlsplit(url)
    segments = [p for p in parts.path.split("/") if p]
    host = parts.hostname or ""
    params = dict(parse_qsl(parts.query))
    if params.get('pid') and parts.path.rstrip('/').endswith('/careers'):
        return 'eightfold', f'eightfold:{host}:{params["pid"]}'
    career_job = re.match(r'/careers/job/(\d+)(?:-|/|$)', parts.path)
    if career_job:
        return 'eightfold', f'eightfold:{host}:{career_job.group(1)}'
    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    }:
        if "jobs" in segments and segments[-1].isdigit():
            return "greenhouse", f"greenhouse:{segments[0]}:{segments[-1]}"
        if params.get("gh_jid"):
            return "greenhouse", f"greenhouse:{segments[0] if segments else host}:{params['gh_jid']}"
        return "greenhouse", None
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return "lever", f"lever:{host}:{':'.join(segments[:2])}" if len(segments) >= 2 else None
    if host == "jobs.ashbyhq.com":
        return "ashby", f"ashby:{':'.join(segments[:2])}" if len(segments) >= 2 else None
    if host == 'apply.workable.com' and 'j' in segments:
        index = segments.index('j')
        if len(segments) > index+1:
            return 'workable', 'workable:'+segments[index+1].upper()
    if host.endswith('.myworkdayjobs.com') and 'job' in segments:
        requisition = re.search(r'_([^/]+)$', segments[-1])
        return 'workday', f'workday:{host}:{requisition.group(1)}' if requisition else None
    if 'sites' in segments and 'job' in segments and ('oraclecloud.com' in host or re.search(r'career|jobs', host)):
        site_index = segments.index('sites')
        job_index = segments.index('job')
        if len(segments) > max(site_index, job_index)+1:
            return 'oraclehcm', f'oraclehcm:{host}:{segments[site_index+1]}:{segments[job_index+1]}'
    for domain, name in (
        ("myworkdayjobs.com", "workday"),
        ("linkedin.com", "linkedin"),
        ("indeed.com", "indeed"),
        ("smartrecruiters.com", "smartrecruiters"),
        ("workable.com", "workable"),
    ):
        if host == domain or host.endswith("." + domain):
            return name, None
    return "generic", None


def clean_text(value) -> str:
    return BeautifulSoup(html.unescape(str(value or "")), "html.parser").get_text(" ", strip=True)


def posting_board_scope(url):
    """Identify a published board scope, including older posting URL formats."""
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    parts = [part for part in parsed.path.split('/') if part]
    if host.endswith('.myworkdayjobs.com'):
        if parts[:2] == ['wday','cxs'] and len(parts) >= 4:
            return 'workday',host,parts[3].casefold()
        if parts and re.fullmatch(r'[a-z]{2}-[a-z]{2}',parts[0],re.I):
            parts = parts[1:]
        return ('workday',host,parts[0].casefold()) if parts else None
    if host in {'boards-api.greenhouse.io','boards-api.eu.greenhouse.io'} and parts[:2] == ['v1','boards'] and len(parts)>2:
        return 'greenhouse',parts[2]
    if host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'} and parts:
        return 'greenhouse',parts[0]
    if host == 'jobs.ashbyhq.com' and parts:
        return 'ashby',parts[0]
    if host == 'api.ashbyhq.com' and parts[:2] == ['posting-api','job-board'] and len(parts)>2:
        return 'ashby',parts[2]
    if host in {'jobs.lever.co','jobs.eu.lever.co'} and parts:
        return 'lever','eu' if '.eu.' in host else 'global',parts[0]
    if host in {'api.lever.co','api.eu.lever.co'} and parts[:2] == ['v0','postings'] and len(parts)>2:
        return 'lever','eu' if '.eu.' in host else 'global',parts[2]
    if host == 'apply.workable.com':
        if parts[:4] == ['api','v1','widget','accounts'] and len(parts)>4:
            return 'workable',parts[4]
        if parts and parts[0] not in {'j','api'}:
            return 'workable',parts[0]
    return None


def normalize_job(raw: dict, source="manual") -> dict:
    metadata = raw.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not isinstance(metadata, dict):
        raise ValueError("Job metadata must be an object")
    metadata = dict(metadata)
    for key in (
        "industries",
        "work_arrangement",
        "contract_types",
        "sponsorship_available",
        "education_requirements",
        "experience_years_required",
        "salary_unit",
    ):
        if raw.get(key) is not None and raw.get(key) != []:
            metadata[key] = raw[key]
    metadata["industries"] = text_values(metadata.get("industries") or metadata.get("industry"))
    metadata["work_arrangement"] = work_arrangement(metadata.get("work_arrangement"))
    metadata["contract_types"] = contract_types(metadata.get("contract_types") or metadata.get("employment_type"))
    metadata["experience_years_required"] = numeric_years(metadata.get("experience_years_required"))
    sponsorship = metadata.get("sponsorship_available")
    if isinstance(sponsorship, str) and sponsorship.lower() in {"true", "false"}:
        sponsorship = sponsorship.lower() == "true"
    metadata["sponsorship_available"] = sponsorship if isinstance(sponsorship, bool) else None
    url = canonical_url(str(raw.get("url") or raw.get("application_url") or ""))
    ats, identity = ats_identity(url)
    title = clean_text(raw.get("title"))[:400]
    company = clean_text(raw.get("company"))[:300]
    if not title or not company:
        raise ValueError("A job requires a title and company")
    description = clean_text(raw.get("description"))[:250000]
    result = {
        "title": title,
        "company": company,
        "location": clean_text(_locations(raw.get("location")))[:1000],
        "url": url,
        "canonical_url": url,
        "identity_key": identity or url,
        "application_url": canonical_url(raw.get("application_url") or url),
        "description": description,
        "source": raw.get("source") or source,
        "ats": ats if ats != "generic" else raw.get("ats", "generic"),
        "skills": sorted(set(raw.get("skills") or extract_skills(description))),
        "salary_min": raw.get("salary_min"),
        "salary_max": raw.get("salary_max"),
        "currency": raw.get("currency"),
        "remote": raw.get("remote"),
        "experience_level": raw.get("experience_level"),
        "posted_at": raw.get("posted_at"),
        "metadata": metadata,
    }
    for field in ("salary_min", "salary_max"):
        if result[field] == "":
            result[field] = None
        if result[field] is not None:
            result[field] = float(result[field])
            if not math.isfinite(result[field]) or result[field] < 0:
                raise ValueError("Salary must be finite and nonnegative")
    if isinstance(result["remote"], str):
        remote = result["remote"].strip().lower()
        result["remote"] = (
            True
            if remote in {"true", "yes", "1", "remote"}
            else False
            if remote in {"false", "no", "0", "onsite", "on-site"}
            else None
        )
    if metadata["work_arrangement"] is not None:
        result["remote"] = metadata["work_arrangement"] == "remote"
    elif result["remote"] is True:
        metadata["work_arrangement"] = "remote"
    from .job_validity import deadline_evidence
    result.update(deadline_evidence(result))
    return result


def matching_context(db):
    facts = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    return {'facts': facts, 'profiles': db.query('SELECT * FROM search_profiles'),
            'feedback': db.query('SELECT f.*,j.title,j.company FROM job_feedback f JOIN jobs j ON j.id=f.job_id ORDER BY f.created_at DESC LIMIT 100'),
            'candidate_evidence': candidate_match_evidence(facts)}


def rescore_job(db, job: dict, context=None):
    company = decode_row(
        db.one(
            "SELECT * FROM companies WHERE id=? OR name=? COLLATE NOCASE",
            (job.get("company_id"), job.get("company", "")),
        )
    )
    if company:
        job = dict(job, company_priority=company["priority"], company_blacklist_keywords=company["blacklist_keywords"])
    context = context if context is not None else matching_context(db)
    facts, profiles, feedback = context['facts'], context['profiles'], context['feedback']
    if not profiles and company:
        profiles = [
            {
                "id": None,
                "config": {
                    "roles": company["preferred_roles"],
                    "locations": company["locations"],
                    "minimum_salary": company["salary_preference"].get("minimum_salary"),
                    "salary_currency": company["salary_preference"].get("salary_currency"),
                },
            }
        ]
    matches = [match_job(job, facts, profile, feedback, candidate_evidence=context['candidate_evidence']) for profile in profiles] or [
        match_job(job, facts, None, feedback, candidate_evidence=context['candidate_evidence'])
    ]
    best = max(matches, key=lambda m: (m["eligible"], m["score"]))
    best["profiles"] = [
        {"id": m["profile_id"], "score": m["score"], "eligible": m["eligible"], "exclusions": m["exclusions"]}
        for m in matches
    ]
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET match_score=?,priority_score=?,match_details=?,updated_at=? WHERE id=?",
            (best["score"], best["priority_score"], dumps(best), now(), job["id"]),
        )
        for match in matches:
            conn.execute(
                "INSERT INTO job_matches VALUES(?,?,?,?,?,?)",
                (uid(), job["id"], match["profile_id"], match["score"], dumps(match), now()),
            )
    return best


def ingest_job(db, raw: dict, source_id: str | None = None, *, match_context=None) -> tuple[dict, bool]:
    job = normalize_job(raw)
    stamp = now()
    duplicate = False
    payload_hash = hashlib.sha256(dumps({k: v for k, v in job.items() if k != 'deadline_verified_at'}).encode()).hexdigest()
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM jobs WHERE canonical_url=? OR identity_key=?", (job["canonical_url"], job["identity_key"])
        ).fetchone()
        if not existing and len(job["description"]) >= 200:
            candidates = conn.execute(
                "SELECT * FROM jobs WHERE lower(company)=lower(?) AND lower(title)=lower(?) AND lower(location)=lower(?) LIMIT 25",
                (job["company"], job["title"], job["location"]),
            ).fetchall()
            for candidate in candidates:
                # Cross-post only with the same substantive description, not title alone.
                if normalized(candidate["description"]) == normalized(job["description"]):
                    existing = candidate
                    break
        if existing:
            duplicate = True
            job_id = existing["id"]
            old_target = existing['application_url'] or existing['url']
            new_target = job['application_url'] or job['url']
            if source_id and ats_identity(old_target)[1] and ats_identity(old_target) == ats_identity(new_target) and urlsplit(old_target).path != urlsplit(new_target).path:
                conn.execute('INSERT OR IGNORE INTO job_duplicates VALUES(?,?,?,?,?)',
                             (uid(),job_id,old_target,'Previously verified application URL',stamp))
                conn.execute('UPDATE jobs SET application_url=? WHERE id=?',(new_target,job_id))
            conn.execute(
                "INSERT OR IGNORE INTO job_duplicates VALUES(?,?,?,?,?)",
                (uid(), job_id, job["url"], "Canonical URL, ATS identity or identical cross-post", stamp),
            )
            supplementary = json.loads(existing["metadata"] or "{}")
            supplementary.update({k: v for k, v in job["metadata"].items() if v not in (None, [], "")})
            conn.execute("UPDATE jobs SET metadata=? WHERE id=?", (dumps(supplementary), job_id))
            # A refresh may add a better description without resetting pipeline state.
            if len(job["description"]) >= len(existing["description"]):
                merged_metadata = json.loads(existing["metadata"] or "{}")
                merged_metadata.update(
                    {key: value for key, value in job["metadata"].items() if value not in (None, [], "")}
                )
                if job["remote"] is False and job["metadata"].get("work_arrangement") is None:
                    merged_metadata["work_arrangement"] = None
                salary_refresh = job["salary_min"] is not None or job["salary_max"] is not None
                if salary_refresh:
                    # Amount, currency and period must come from the same advertisement snapshot.
                    merged_metadata["salary_unit"] = job["metadata"].get("salary_unit")
                description_changed = job['description'] != existing['description']
                conn.execute(
                    "UPDATE jobs SET " + ("description=?," if description_changed else "") + "skills=?,salary_min=?,salary_max=?,currency=?,metadata=?,remote=COALESCE(?,remote),posted_at=COALESCE(?,posted_at),experience_level=COALESCE(?,experience_level),updated_at=? WHERE id=?",
                    (*([job['description']] if description_changed else []),
                        dumps(job["skills"]),
                        job["salary_min"] if salary_refresh else existing["salary_min"],
                        job["salary_max"] if salary_refresh else existing["salary_max"],
                        job["currency"] if salary_refresh else existing["currency"],
                        dumps(merged_metadata),
                        job["remote"],
                        job["posted_at"],
                        job["experience_level"],
                        stamp,
                        job_id,
                    ),
                )
        else:
            job_id = uid()
            company = conn.execute(
                "SELECT id,priority FROM companies WHERE name=? COLLATE NOCASE", (job["company"],)
            ).fetchone()
            fields = dict(
                job,
                id=job_id,
                company_id=company["id"] if company else None,
                created_at=stamp,
                updated_at=stamp,
                status="DISCOVERED",
            )
            keys = list(fields)
            values = [dumps(value) if isinstance(value, (list, dict)) else value for value in fields.values()]
            conn.execute(f"INSERT INTO jobs({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", values)
            for skill in job["skills"]:
                conn.execute("INSERT OR IGNORE INTO skills(id,name) VALUES(?,?)", (uid(), skill))
                skill_id = conn.execute("SELECT id FROM skills WHERE name=?", (skill,)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO job_skills VALUES(?,?,0)", (job_id, skill_id))
        from .job_validity import deadline_evidence
        deadlines = deadline_evidence(job)
        # A partial aggregator record must not erase a published employer deadline.
        prior_deadline = conn.execute('SELECT application_deadline,deadline_source FROM jobs WHERE id=?', (job_id,)).fetchone()
        publication_validity = deadlines['deadline_state'] == 'POSTING_VALIDITY' and prior_deadline['deadline_source'] in (None, job['url'])
        if deadlines['application_deadline'] or not prior_deadline['application_deadline'] or publication_validity:
            conn.execute('UPDATE jobs SET application_deadline=?,deadline_timezone=?,deadline_source=?,deadline_state=?,deadline_verified_at=? WHERE id=?',
                         (deadlines['application_deadline'], deadlines['deadline_timezone'], deadlines['deadline_source'], deadlines['deadline_state'], deadlines['deadline_verified_at'], job_id))
        metadata = json.loads(conn.execute("SELECT metadata FROM jobs WHERE id=?", (job_id,)).fetchone()[0] or "{}")
        provenance = metadata.get("discovery_sources", [])
        entry = {
            "name": job["source"],
            "source_id": source_id,
            "url": job["metadata"].get("listing_source_url") or job["url"],
        }
        if entry not in provenance:
            provenance.append(entry)
        metadata["discovery_sources"] = provenance
        conn.execute("UPDATE jobs SET metadata=? WHERE id=?", (dumps(metadata), job_id))
        conn.execute(
            "INSERT OR IGNORE INTO job_sources VALUES(?,?,?,?,?,?)",
            (uid(), job_id, source_id, job["url"], raw.get("external_id"), stamp),
        )
        conn.execute(
            "INSERT OR IGNORE INTO job_snapshots VALUES(?,?,?,?,?)", (uid(), job_id, payload_hash, dumps(job), stamp)
        )
    persisted = decode_row(db.one("SELECT * FROM jobs WHERE id=?", (job_id,)))
    match = rescore_job(db, persisted, match_context)
    persisted.update(match_score=match["score"], priority_score=match["priority_score"], match_details=match)
    return persisted, duplicate


async def validate_public_url(url: str):
    parsed = urlsplit(canonical_url(url))
    if parsed.hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local and private network destinations are not allowed for discovery")
    infos = await asyncio.to_thread(
        socket.getaddrinfo,
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    if not infos or any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise ValueError("Discovery accepts public Internet destinations only")


async def fetch(url: str, *, accept="application/json,text/html", max_body=MAX_BODY, response_metadata=None) -> tuple[bytes, str, str]:
    async with httpx.AsyncClient(
        timeout=25,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "Meridian/1.0 (personal read-only job discovery)", "Accept": accept},
    ) as client:
        for _ in range(6):
            await validate_public_url(url)
            async with client.stream("GET", url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect without destination")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                if response_metadata is not None:
                    # Public pagination evidence only; never persist cookies or
                    # authentication headers from a source response.
                    response_metadata['x-wp-total'] = response.headers.get('x-wp-total')
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_body:
                        raise ValueError(f"Source exceeds the {max_body // (1024 * 1024)} MB download limit")
                return bytes(content), response.headers.get("content-type", ""), str(response.url)
    raise ValueError("Too many redirects")


def _locations(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(filter(None, (_locations(x) for x in value)))
    if isinstance(value, dict):
        if isinstance(value.get('location'), str):
            return value['location']
        address = value.get("address", value)
        if isinstance(address, str):
            return address
        return ", ".join(
            _locations(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k)
        ) or value.get("name", "")
    return ""


def parse_jsonld(text: str, url: str) -> list[dict]:
    from .career_pages import parse_html
    soup = parse_html(text)
    postings = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if "JobPosting" in ([types] if isinstance(types, str) else types):
                postings.append(value)
            for key in ("@graph", "itemListElement", "item"):
                if key in value:
                    visit(value[key])

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    result = []
    for post in postings:
        organization = post.get("hiringOrganization") or {}
        salary = post.get("baseSalary") or {}
        amount = salary.get("value", {}) if isinstance(salary, dict) else {}
        if isinstance(amount, (float, int)):
            amount = {"minValue": amount, "maxValue": amount}
        if not isinstance(amount, dict):
            amount = {}
        remote = True if post.get("jobLocationType") == "TELECOMMUTE" else None
        experience_requirement = post.get("experienceRequirements")
        months = experience_requirement.get("monthsOfExperience") if isinstance(experience_requirement, dict) else None
        experience_years = (
            numeric_years(months / 12) if isinstance(months, (int, float)) and not isinstance(months, bool) else None
        )
        result.append(
            {
                "title": post.get("title", ""),
                "company": organization.get("name", "") if isinstance(organization, dict) else str(organization),
                "url": urljoin(url, post.get("url") or url),
                "description": post.get("description", ""),
                "location": _locations(post.get("jobLocation")),
                "remote": remote,
                "posted_at": post.get("datePosted"),
                "salary_min": amount.get("minValue"),
                "salary_max": amount.get("maxValue"),
                "currency": salary.get("currency") if isinstance(salary, dict) else None,
                "metadata": {
                    "salary_unit": amount.get("unitText"),
                    "valid_through": post.get("validThrough"),
                    "employment_type": post.get("employmentType"),
                    "work_arrangement": "remote" if remote else None,
                    "industries": post.get("industry"),
                    "education_requirements": post.get("educationRequirements"),
                    "experience_years_required": experience_years,
                    "raw_experience_requirements": experience_requirement,
                },
            }
        )
    return result


def parse_greenhouse(data: dict, board: str) -> list[dict]:
    return [
        {
            "title": item["title"],
            "company": board,
            "url": item["absolute_url"],
            "location": item.get("location", {}).get("name", ""),
            "description": item.get("content", ""),
            "ats": "greenhouse",
            "external_id": str(item["id"]),
            "metadata": {"updated_at": item.get("updated_at"), "departments": item.get("departments", [])},
        }
        for item in posting_collection(data, 'jobs')
    ]


def parse_lever(data: list, board: str) -> list[dict]:
    result = []
    for item in posting_collection(data):
        salary = item.get("salaryRange") or {}
        result.append(
            {
                "title": item["text"],
                "company": board,
                "url": item["hostedUrl"],
                "application_url": item.get("applyUrl", item["hostedUrl"]),
                "location": item.get("categories", {}).get("location", ""),
                "description": " ".join(
                    [
                        item.get("descriptionPlain", item.get("description", "")),
                        *[
                            str(section.get("text", "")) + " " + str(section.get("content", ""))
                            for section in item.get("lists", [])
                        ],
                        item.get("additionalPlain", ""),
                    ]
                ),
                "ats": "lever",
                "external_id": item["id"],
                "remote": True
                if item.get("workplaceType") == "remote"
                else False
                if item.get("workplaceType") == "on-site"
                else None,
                "salary_min": salary.get("min"),
                "salary_max": salary.get("max"),
                "currency": salary.get("currency"),
                "metadata": {
                    "salary_unit": salary.get("interval"),
                    "categories": item.get("categories", {}),
                    "work_arrangement": item.get("workplaceType"),
                    "employment_type": item.get("categories", {}).get("commitment"),
                },
            }
        )
    return result


def parse_ashby(data: dict, board: str) -> list[dict]:
    return [
        {
            "title": item["title"],
            "company": board,
            "url": item["jobUrl"],
            "application_url": item.get("applyUrl", item["jobUrl"]),
            "location": item.get("location", ""),
            "description": item.get("descriptionPlain", item.get("descriptionHtml", "")),
            "ats": "ashby",
            "external_id": item.get("id"),
            "remote": item.get("isRemote"),
            "posted_at": item.get("publishedAt"),
            "metadata": {
                "department": item.get("department"),
                "compensation": item.get("compensation"),
                "employment_type": item.get("employmentType"),
                "work_arrangement": item.get("workplaceType"),
            },
        }
        for item in posting_collection(data, 'jobs')
        if item.get("isListed", True)
    ]


async def ingest_url(db, url: str, source_id=None):
    body, content_type, final_url = await fetch(url)
    data = body.decode("utf-8", errors="replace")
    postings = parse_jsonld(data, final_url)
    if not postings:
        ats, identity = ats_identity(final_url)
        parts = [x for x in urlsplit(final_url).path.split("/") if x]
        if ats in {"greenhouse", "lever", "ashby"} and identity and parts:
            source = {"kind": ats, "url": final_url, "config": {"board": parts[0]}}
            jobs = await discover(source)
            postings = [j for j in jobs if ats_identity(j["url"])[1] == identity]
    if not postings:
        raise ValueError(
            "No structured JobPosting was found. Add title, company and description manually, or configure this company's public ATS source. Login-only pages require the dedicated browser."
        )
    items = []
    duplicates = 0
    for post in postings[:MAX_JOBS]:
        item, duplicate = ingest_job(db, post, source_id)
        items.append(item)
        duplicates += int(duplicate)
    return {"items": items, "total": len(items), "duplicates": duplicates, "job": items[0]}


async def discover_career_page(text, final, config):
    from .career_pages import board_references, career_scripts, embedded_jobs, page_links

    company = config.get('company_name') or config.get('company') or ''
    found, endpoints, limitations, visited = {}, [final], [], {canonical_url(final)}
    boards_seen = set()
    complete_boards = []
    pending = [(text, final, 0)]
    downloaded = 0
    while pending:
        current, location, depth = pending.pop(0)
        structured = parse_jsonld(current, location) or embedded_jobs(current, location, company)
        for job in structured:
            found[job['url']] = job
        boards = board_references(current, location)
        if not boards and not structured and depth == 0:
            for script_url in career_scripts(current, location):
                try:
                    script, _, script_final = await fetch(script_url, max_body=2*1024*1024)
                    boards.extend(board_references(script.decode('utf-8', errors='replace'), location))
                    endpoints.append(script_final)
                except (ValueError, httpx.HTTPError):
                    # A script that cannot be inspected is not an empty job board.
                    continue
        for board_source in boards[:6]:
            identity = (board_source['kind'], board_source['config']['board'])
            if identity in boards_seen:
                continue
            boards_seen.add(identity)
            board_source['config'].update({k: v for k, v in config.items() if k in ('company_name', 'company_id', 'max_jobs')})
            try:
                board_jobs = await discover(board_source)
                found.update({job['url']: job for job in board_jobs})
                proof = getattr(board_jobs, 'evidence', {})
                endpoints.extend(proof.get('endpoints', []))
                limitations.extend(proof.get('limitations', []))
                complete_boards.append(bool(proof.get('complete') and proof.get('authoritative')))
            except (ValueError, httpx.HTTPError) as exc:
                complete_boards.append(False)
                limitations.append(f"Published {board_source['kind']} board could not be read: {type(exc).__name__}")
        searches, listing_jobs = page_links(current, location)
        for job in listing_jobs:
            if job['url'] not in found and company:
                job.update(company=company, metadata={'discovery_evidence': 'published career listing link', 'description_state': 'NOT_PUBLISHED_IN_LISTING'})
                found[job['url']] = job
        # Successful authoritative boards need no broad crawl. Other pages can
        # publish jobs one navigation step away, on another official career host.
        links = ([] if boards else searches[:4]) + [j['url'] for j in listing_jobs[:12] if not structured]
        if depth >= 2:
            continue
        for link in links:
            key = canonical_url(link)
            if key in visited or downloaded >= 20:
                continue
            visited.add(key)
            downloaded += 1
            try:
                child, _, child_final = await fetch(link)
                endpoints.append(child_final)
                pending.append((child.decode('utf-8', errors='replace'), child_final, depth+1))
            except (ValueError, httpx.HTTPError) as exc:
                limitations.append(f'Published career link could not be read: {type(exc).__name__}')
    if not found:
        if complete_boards and all(complete_boards):
            return DiscoveryResult([], complete=True, authoritative=True, method='Published ATS references', endpoints=endpoints, total=0, limitations=limitations)
        raise ValueError('No readable public job collection found; the career page is not evidence of an empty board' + (': ' + '; '.join(limitations[:2]) if limitations else ''))
    authoritative = bool(complete_boards) and all(complete_boards)
    if not authoritative:
        limitations.append('Published career pages may show a subset; absence does not prove a job closed')
    return DiscoveryResult(list(found.values()), complete=authoritative, authoritative=authoritative, method='Published career data and ATS references', endpoints=endpoints, limitations=list(dict.fromkeys(limitations)))


class PublisherPermissionRequired(ValueError):
    """The configured source has a documented authorization boundary."""


async def discover(source: dict) -> list[dict]:
    config = source.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)
    policy = config.get('access_policy') or {}
    if isinstance(policy, dict) and policy.get('automation_requires_written_permission') is True:
        evidence = policy.get('written_permission_evidence')
        if not isinstance(evidence, str) or not evidence.strip():
            raise PublisherPermissionRequired('Automated collection requires the publisher\'s written permission; '
                                              'use an authorized alert or manually imported listing instead.')
    kind, url = source["kind"], source.get("url", "")
    parts = [x for x in urlsplit(url).path.split("/") if x]
    board = str(config.get("board") or config.get("company") or (parts[0] if parts else ""))
    evidence = {'complete': False, 'authoritative': False, 'method': kind, 'endpoints': [url], 'total': None, 'limitations': []}
    if isinstance(config.get('public_sitemap'), dict):
        from .sitemaps import discover_sitemap
        result = await discover_sitemap(config)
        for job in result:
            if config.get('company_name'):
                job['company'] = config['company_name']
            job['source'] = source.get('name',kind)
        return result
    if isinstance(config.get('published_board'), dict):
        published = config['published_board']
        if published.get('kind') not in {'greenhouse', 'lever', 'ashby', 'workday', 'oraclehcm', 'eightfold', 'smartrecruiters', 'workable'}:
            raise ValueError('Published board must use a supported public API')
        child_config = {k: v for k, v in config.items() if k not in {'published_board', 'public_job_feed'}} | published.get('config', {})
        result = await discover({'kind': published['kind'], 'url': published['url'], 'config': child_config, 'name': source.get('name')})
        result.evidence['endpoints'].append(config.get('board_evidence_url') or url)
        return result
    elif isinstance(config.get('public_job_feed'), dict):
        feed = config['public_job_feed']
        feed_url = feed.get('url')
        template = feed.get('url_template')
        if not isinstance(feed_url, str) or not (isinstance(template, str) and '{id}' in template or isinstance(feed.get('url_key'), str)):
            raise ValueError('Published JSON feed needs a URL and a published job URL field or observed {id} template')
        pagination = feed.get('pagination')
        response_metadata = {}
        fetch_options = {'response_metadata': response_metadata} if isinstance(pagination, dict) and pagination.get('total_header') else {}
        body, _, feed_final = await fetch(feed_url, **fetch_options)
        data = json.loads(body)
        records = posting_collection(data, feed.get('collection_key'))
        complete, published_total = True, None
        if isinstance(pagination, dict):
            published_total = data.get(pagination.get('total_key')) if isinstance(data, dict) else None
            if pagination.get('total_header'):
                if pagination['total_header'].lower() != 'x-wp-total':
                    raise ValueError('Unsupported public pagination total header')
                header_total = response_metadata.get('x-wp-total')
                published_total = int(header_total) if isinstance(header_total, str) and header_total.isdigit() else None
            if not isinstance(published_total, int) or isinstance(published_total, bool) or published_total < len(records):
                raise ValueError('Published search feed has no valid total')
            maximum = min(MAX_JOBS, max(1, int(config.get('max_jobs', MAX_JOBS))))
            seen = {hashlib.sha256(dumps(records).encode()).hexdigest()}
            while records and len(records) < min(published_total, maximum):
                parsed = urlsplit(feed_url)
                params = dict(parse_qsl(parsed.query)) | {pagination['offset_param']: str(len(records)), pagination['limit_param']: str(min(100, maximum-len(records)))}
                page_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ''))
                body, _, _ = await fetch(page_url)
                page = posting_collection(json.loads(body), feed.get('collection_key'))
                digest = hashlib.sha256(dumps(page).encode()).hexdigest()
                if not page or digest in seen:
                    raise ValueError('Published search pagination ended early or repeated a page')
                seen.add(digest)
                records.extend(page)
            records = records[:maximum]
            complete = len(records) >= published_total
        listed_ids = None
        endpoints = [feed_final]
        if feed.get('listed_ids_url'):
            body, _, ids_final = await fetch(feed['listed_ids_url'])
            listed = json.loads(body)
            if not isinstance(listed, list) or any(not isinstance(item, (str, int)) for item in listed):
                raise ValueError('Published directory did not return a job identifier list')
            listed_ids = {str(identifier) for identifier in listed}
            endpoints.append(ids_final)
        jobs = []
        def feed_value(record, key):
            if not isinstance(key, str):
                return None
            if key in record:
                return record[key]
            value = record
            for part in key.split('.'):
                value = value.get(part) if isinstance(value, dict) else None
            return value
        for record in records:
            identifier = feed_value(record, feed.get('id_key', 'id'))
            if identifier is None or listed_ids is not None and str(identifier) not in listed_ids:
                continue
            title = feed_value(record, feed.get('title_key', 'title'))
            if not isinstance(title, str) or not title:
                raise ValueError('Published JSON job has no title in its configured title field')
            description = ' '.join(str(feed_value(record, key) or '') for key in feed.get('description_keys', ['description']))
            destination = feed_value(record, feed.get('url_key')) if feed.get('url_key') else template.replace('{id}', quote(str(identifier), safe=''))
            if not isinstance(destination, str) or not destination:
                raise ValueError('Published JSON job has no destination')
            jobs.append({'title': title, 'company': config.get('company_name') or config.get('company') or '',
                         'url': urljoin(feed_final, destination), 'description': description,
                         'location': feed_value(record, feed.get('location_key', 'location')) or '', 'external_id': str(identifier),
                         'metadata': {'discovery_evidence': 'Published job feed', 'description_state': 'PUBLISHED' if description else 'NOT_PUBLISHED_IN_LISTING', 'employment_type': feed_value(record, feed.get('employment_key', 'employment_type'))}})
        if not complete:
            evidence['limitations'].append('Posting limit reached before the published total')
        if jobs and not any(job['description'] for job in jobs):
            evidence['limitations'].append('Descriptions are not published in the listing feed')
        evidence.update(complete=complete, authoritative=listed_ids is not None or isinstance(pagination, dict) or feed.get('authoritative') is True, method='Published JSON job feed', endpoints=endpoints, total=published_total if published_total is not None else len(jobs))
    elif kind in {"greenhouse", "lever", "ashby"}:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", board):
            raise ValueError("Configure a valid public ATS board identifier")
        if kind == "greenhouse":
            # Greenhouse's documented board-token API also returns EU-hosted
            # absolute posting URLs. The UI hostname is not an API hostname.
            url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true"
        elif kind == "lever":
            region = "api.eu.lever.co" if config.get("region") == "eu" or "eu.lever.co" in url else "api.lever.co"
            url = f"https://{region}/v0/postings/{quote(board)}?mode=json&limit=100&skip=0"
        else:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true"
        body, _, _ = await fetch(url, max_body=64 * 1024 * 1024)
        data = json.loads(body)
        evidence.update(complete=True, authoritative=True, endpoints=[url], method=kind+' public API')
        if kind == "lever":
            items = posting_collection(data)
            seen_pages = {hashlib.sha256(body).hexdigest()}
            while len(data) == 100 and len(items) < MAX_JOBS:
                body, _, _ = await fetch(url.rsplit("skip=", 1)[0] + "skip=" + str(len(items)))
                data = json.loads(body)
                posting_collection(data)
                digest = hashlib.sha256(body).hexdigest()
                if digest in seen_pages:
                    raise ValueError('Public board pagination repeated a page')
                seen_pages.add(digest)
                items.extend(data)
            jobs = parse_lever(items, board)
            if len(data) == 100 and len(items) >= MAX_JOBS:
                evidence.update(complete=False, limitations=['Posting limit reached before the final page'])
        else:
            jobs = parse_greenhouse(data, board) if kind == "greenhouse" else parse_ashby(data, board)
        evidence['total'] = len(jobs) if evidence['complete'] else None
    elif kind == 'workday' and (urlsplit(url).hostname or '').endswith('.myworkdayjobs.com'):
        parsed = urlsplit(url)
        tenant = parsed.hostname.split('.')[0]
        site = board
        if parts and re.fullmatch(r'[a-z]{2}-[a-z]{2}',parts[0], re.I) and len(parts)>1 and not config.get('board'):
            site = parts[1]
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',site):
            raise ValueError('Invalid public Workday site identifier')
        endpoint = f'https://{parsed.hostname}/wday/cxs/{tenant}/{site}/jobs'
        await validate_public_url(endpoint)
        jobs=[]
        maximum=min(MAX_JOBS,max(1,int(config.get('max_jobs',MAX_JOBS))))
        evidence.update(authoritative=True, endpoints=[endpoint], method='Workday public CXS', limitations=['Descriptions are not published in board listings'])
        seen_pages = set()
        async with httpx.AsyncClient(timeout=25,trust_env=False) as client:
            for offset in range(0,maximum,20):
                async with client.stream('POST',endpoint,json={'limit':min(20,maximum-offset),'offset':offset,'searchText':'','appliedFacets':{}}) as response:
                    response.raise_for_status()
                    content=bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content)>MAX_BODY:
                            raise ValueError('Workday board response exceeds the download limit')
                page=json.loads(content)
                postings=posting_collection(page, 'jobPostings')
                digest = hashlib.sha256(content).hexdigest()
                if digest in seen_pages and postings:
                    raise ValueError('Workday pagination repeated a page')
                seen_pages.add(digest)
                total = page.get('total')
                # CXS includes the total on the first page; subsequent pages
                # commonly send total=0 even though they contain more postings.
                if isinstance(total, int) and not isinstance(total, bool) and total >= offset+len(postings):
                    evidence['total'] = max(total, evidence['total'] or 0)
                for posting in postings:
                    path=posting.get('externalPath')
                    if not isinstance(path,str) or not path.startswith('/job/') or not isinstance(posting.get('title'),str) or not posting['title'].strip():
                        evidence['limitations'].append('A Workday listing was omitted because it has no public job path or title')
                        continue
                    jobs.append({'title':posting['title'],'company':config.get('company_name',tenant),
                                 'url':f'https://{parsed.hostname}/{site}'+path,'location':posting.get('locationsText',''),
                                 'ats':'workday','metadata':{'public_board':endpoint,'requisition_ids':posting.get('bulletFields',[]),'description_state':'NOT_PUBLISHED_IN_LISTING'}})
                if not postings or (evidence['total'] is not None and offset+len(postings)>=evidence['total']) or len(postings)<min(20,maximum-offset):
                    evidence['complete'] = evidence['total'] is None or offset+len(postings) >= evidence['total']
                    if not evidence['complete']:
                        evidence['limitations'].append('Board returned a short page before its published total')
                    break
        if not evidence['complete']:
            evidence['limitations'].append('Posting limit reached before the final page')
        if any('omitted because' in limitation for limitation in evidence['limitations']):
            if not jobs:
                raise ValueError('Workday board returned postings, but none has a usable public job path and title')
            evidence['complete'] = False
            evidence['limitations'] = list(dict.fromkeys(evidence['limitations']))
    elif kind == 'eightfold':
        parsed = urlsplit(url)
        if parsed.path not in ('/api/pcsx/search', '/api/apply/v2/jobs'):
            raise ValueError('Eightfold source needs a public search endpoint observed from the employer portal')
        params = dict(parse_qsl(parsed.query))
        if not params.get('domain'):
            raise ValueError('Eightfold source needs its published employer domain')
        params = {k: v for k, v in params.items() if k in ('domain', 'query', 'location', 'sort_by')}
        jobs, offset, seen_pages = [], 0, set()
        maximum = min(MAX_JOBS, max(1, int(config.get('max_jobs', MAX_JOBS))))
        evidence.update(authoritative=True, method='Observed public Eightfold search API', endpoints=[url])
        while offset < maximum:
            request_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params | {'start': offset, 'num': min(100, maximum-offset)}), ''))
            body, _, _ = await fetch(request_url)
            data = json.loads(body)
            error_payload = data.get('error') if isinstance(data, dict) else None
            has_error = any(bool(value) for value in error_payload.values()) if isinstance(error_payload, dict) else bool(error_payload)
            if not isinstance(data, dict) or has_error or data.get('status', 200) != 200:
                raise ValueError('Public career search returned an error payload')
            page = data.get('data', data)
            postings = posting_collection(page, 'positions')
            total = page.get('count') if isinstance(page.get('count'), int) else page.get('total')
            if isinstance(total, int) and not isinstance(total, bool) and total >= offset+len(postings):
                evidence['total'] = max(total, evidence['total'] or 0)
            digest = hashlib.sha256(dumps(postings).encode()).hexdigest()
            if postings and digest in seen_pages:
                raise ValueError('Public career search pagination repeated a page')
            seen_pages.add(digest)
            for posting in postings[:maximum-offset]:
                identifier = str(posting['id'])
                destination = posting.get('positionUrl')
                if not isinstance(destination, str) or not destination.startswith('https://'):
                    destination = f'https://{parsed.hostname}/careers?'+urlencode({'domain': params['domain'], 'pid': identifier})
                jobs.append({'title': posting.get('name') or posting['title'], 'company': config.get('company_name', params['domain']),
                             'url': destination, 'location': '; '.join(str(loc) for loc in posting.get('locations', []) if isinstance(loc, str)),
                             'posted_at': datetime.fromtimestamp(int(posting['postedTs']), timezone.utc).isoformat() if posting.get('postedTs') else None,
                             'ats': 'eightfold', 'external_id': identifier, 'metadata': {'description_state': 'NOT_PUBLISHED_IN_LISTING', 'requisition_ids': [posting.get('displayJobId')]}})
            offset += len(postings)
            if not postings or (evidence['total'] is not None and offset >= evidence['total']):
                evidence['complete'] = evidence['total'] is None or offset >= evidence['total']
                break
        if not evidence['complete']:
            evidence['limitations'].append('Posting limit reached before the final page' if offset >= maximum else
                'Public search ended before its published total was reached; listings may have changed during pagination')
    elif kind == 'workable' and (urlsplit(url).hostname or '') == 'apply.workable.com':
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', board):
            raise ValueError('Configure a valid public Workable account identifier')
        endpoint = f'https://apply.workable.com/api/v1/widget/accounts/{quote(board)}'
        body, _, _ = await fetch(endpoint)
        data = json.loads(body)
        postings = posting_collection(data, 'jobs')
        jobs = [{'title': post['title'], 'company': data.get('name') or config.get('company_name', board),
                 'url': post['url'], 'application_url': post.get('application_url') or post['url'],
                 'location': ', '.join(str(post[key]) for key in ('city', 'state', 'country') if post.get(key)),
                 'remote': post.get('telecommuting'), 'posted_at': post.get('published_on'), 'ats': 'workable',
                 'external_id': post.get('shortcode'), 'metadata': {'employment_type': post.get('employment_type'),
                                                               'education_requirements': post.get('education'),
                                                               'description_state': 'NOT_PUBLISHED_IN_LISTING'}} for post in postings]
        evidence.update(complete=True, authoritative=True, method='Workable public job widget', endpoints=[endpoint], total=len(jobs))
        detail_gate = asyncio.Semaphore(3)
        async def detail(job):
            if not job.get('external_id'):
                return
            async with detail_gate:
                detail_url = f"https://apply.workable.com/api/v2/accounts/{quote(board)}/jobs/{quote(str(job['external_id']))}"
                try:
                    body, _, _ = await fetch(detail_url)
                    post = json.loads(body)
                    if not isinstance(post, dict) or post.get('shortcode') != job['external_id']:
                        raise ValueError('Workable detail identity does not match its public listing')
                    job['description'] = ' '.join(post.get(key) or '' for key in ('description', 'requirements', 'benefits'))
                    job['metadata']['description_state'] = 'PUBLISHED'
                    if post.get('state') != 'published' or post.get('isInternal'):
                        job['metadata']['closed'] = True
                except (ValueError, TypeError, httpx.HTTPError):
                    evidence['limitations'].append('A published job detail could not be read')
        await asyncio.gather(*(detail(job) for job in jobs[:min(MAX_JOBS, int(config.get('detail_limit', 100)))]))
        if len(jobs) > min(MAX_JOBS, int(config.get('detail_limit', 100))):
            evidence['limitations'].append('Some descriptions are limited to public listing fields')
    elif kind == 'oraclehcm':
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', board):
            raise ValueError('Configure a valid public Oracle Candidate Experience site')
        parsed = urlsplit(url)
        base = config.get('api_url') or f'https://{parsed.hostname}/hcmRestApi/resources/latest/recruitingCEJobRequisitions'
        if urlsplit(base).path != '/hcmRestApi/resources/latest/recruitingCEJobRequisitions':
            raise ValueError('Oracle source requires an observed public recruiting endpoint')
        maximum = min(MAX_JOBS, max(1, int(config.get('max_jobs', MAX_JOBS))))
        jobs = []
        evidence.update(authoritative=True, method='Oracle public Candidate Experience API', endpoints=[base])
        seen_pages = set()
        offset = 0
        api_site = str(config.get('api_site') or board)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', api_site):
            raise ValueError('Oracle API site must be observed from the public search request')
        sort_by = config.get('api_sort')
        sort_clause = ',sortBy='+sort_by if isinstance(sort_by, str) and re.fullmatch(r'[A-Z_]{1,60}', sort_by) else ''
        while offset < maximum:
            limit = min(100, maximum-offset)
            query = urlencode({'onlyData': 'true', 'expand': 'requisitionList', 'finder': f'findReqs;siteNumber={api_site},limit={limit},offset={offset}{sort_clause}'})
            body, _, _ = await fetch(base+'?'+query)
            data = json.loads(body)
            groups = posting_collection(data, 'items')
            if len(groups) != 1 or 'requisitionList' not in groups[0]:
                raise ValueError('Oracle public search returned no recognizable requisition collection')
            page = groups[0]
            postings = posting_collection(page, 'requisitionList')
            total = page.get('TotalJobsCount')
            if isinstance(total, int) and total >= offset+len(postings):
                evidence['total'] = max(total, evidence['total'] or 0)
            digest = hashlib.sha256(dumps([posting.get('Id') for posting in postings]).encode()).hexdigest()
            if postings and digest in seen_pages:
                raise ValueError('Oracle pagination repeated a page')
            seen_pages.add(digest)
            for posting in postings[:maximum-offset]:
                identifier = str(posting['Id'])
                prefix = parsed.path.split('/sites/', 1)[0] if '/sites/' in parsed.path else '/hcmUI/CandidateExperience/en'
                destination = f'https://{parsed.hostname}{prefix}/sites/{board}/job/{quote(identifier)}'
                jobs.append({'title': posting['Title'], 'company': config.get('company_name', board), 'url': destination,
                             'description': posting.get('ShortDescriptionStr') or '', 'location': posting.get('PrimaryLocation') or '',
                             'posted_at': posting.get('PostedDate'), 'external_id': identifier, 'ats': 'oraclehcm',
                             'metadata': {'application_deadline': posting.get('PostingEndDate'), 'employment_type': posting.get('JobType'),
                                          'description_state': 'SUMMARY_ONLY'}})
            offset += len(postings)
            if not postings or (evidence['total'] is not None and offset >= evidence['total']):
                evidence['complete'] = evidence['total'] is not None and offset >= evidence['total']
                break
        if not evidence['complete']:
            evidence['limitations'].append('Posting limit reached before the final page')
    elif kind == "smartrecruiters":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", board):
            raise ValueError("Configure a SmartRecruiters company identifier")
        jobs, offset = [], 0
        evidence.update(authoritative=True, method='SmartRecruiters public API')
        while offset < MAX_JOBS:
            base = f"https://api.smartrecruiters.com/v1/companies/{quote(board)}/postings"
            body, _, _ = await fetch(f"{base}?limit=100&offset={offset}&destination=PUBLIC")
            page = json.loads(body)
            postings = posting_collection(page, 'content')
            evidence['total'] = page.get('totalFound') if isinstance(page.get('totalFound'), int) else None
            for item in postings:
                # Listings omit descriptions; fetch details with a conservative cap per run.
                if len(jobs) >= min(int(config.get("max_jobs", 100)), MAX_JOBS):
                    break
                detail, _, _ = await fetch(base + "/" + quote(str(item["id"])))
                post = json.loads(detail)
                sections = post.get("jobAd", {}).get("sections", {})
                description = " ".join(
                    section.get("text", "") for section in sections.values() if isinstance(section, dict)
                )
                location = post.get("location", {})
                jobs.append(
                    {
                        "title": post.get("name", item.get("name", "")),
                        "company": post.get("company", {}).get("name", board),
                        "url": post.get("postingUrl") or f"https://jobs.smartrecruiters.com/{board}/{post['id']}",
                        "application_url": post.get("applyUrl")
                        or post.get("postingUrl")
                        or f"https://jobs.smartrecruiters.com/{board}/{post['id']}",
                        "description": description,
                        "location": ", ".join(
                            str(location[k]) for k in ("city", "region", "country") if location.get(k)
                        ),
                        "remote": location.get("remote"),
                        "posted_at": post.get("releasedDate"),
                        "ats": "smartrecruiters",
                        "external_id": str(post["id"]),
                        "metadata": {
                            "employment_type": post.get("typeOfEmployment"),
                            "industries": post.get("industry"),
                        },
                    }
                )
            offset += len(postings)
            if (
                not postings
                or (evidence['total'] is not None and offset >= evidence['total'])
                or len(jobs) >= min(int(config.get("max_jobs", 100)), MAX_JOBS)
            ):
                evidence['complete'] = not postings or (evidence['total'] is not None and len(jobs) >= evidence['total'])
                break
        if not evidence['complete']:
            evidence['limitations'].append('Detail download limit reached before the final page')
    elif kind == "rss":
        body, _, final = await fetch(url, accept="application/rss+xml,application/atom+xml,application/xml")
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("RSS entities and document types are not supported")
        root = ET.fromstring(body)
        if root.tag.lower() != 'rss' and root.tag != '{http://www.w3.org/2005/Atom}feed':
            raise ValueError('Response is not an RSS or Atom job feed')
        jobs = []
        entries = list(root.findall(".//item")) + list(root.findall("{http://www.w3.org/2005/Atom}entry"))
        for entry in entries[:40]:
            title = entry.findtext("title") or entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
            link = entry.findtext("link")
            if not link:
                element = entry.find("{http://www.w3.org/2005/Atom}link")
                link = element.get("href") if element is not None else None
            if not link:
                continue
            link = urljoin(final, link)
            try:
                data, _, final_job = await fetch(link)
                parsed = parse_jsonld(data.decode("utf-8", errors="replace"), final_job)
            except (ValueError, httpx.HTTPError):
                parsed = []
            if parsed:
                jobs.extend(parsed)
            elif config.get("company_name"):
                jobs.append(
                    {
                        "title": title,
                        "company": config["company_name"],
                        "url": link,
                        "description": entry.findtext("description")
                        or entry.findtext("{http://www.w3.org/2005/Atom}summary")
                        or "",
                    }
                )
        if entries and not jobs:
            raise ValueError("Feed entries need structured job pages or a configured company_name")
        evidence.update(complete=len(entries) <= 40, method='Public RSS or Atom feed', total=len(jobs))
    elif kind == "github":
        repository = config.get("repository") or "/".join(parts[:2])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""):
            raise ValueError("GitHub source needs owner/repository")
        branch = quote(str(config.get("branch", "HEAD")), safe="")
        paths = config.get("paths", ["README.md"])
        if isinstance(paths, str):
            paths = [paths]
        from .feeds import parse_github_tables

        jobs = []
        recognized = False
        for path in paths[:10]:
            body, _, _ = await fetch(
                f"https://raw.githubusercontent.com/{repository}/{branch}/{quote(str(path), safe='/')}"
            )
            text = body.decode("utf-8", errors="replace")
            parser = config.get("parser", "markdown")
            if parser == "json":
                parsed = json.loads(text)
                jobs.extend(posting_collection(parsed, None if isinstance(parsed, list) else 'jobs'))
                recognized = True
            elif parser == "csv":
                reader = csv.DictReader(io.StringIO(text))
                jobs.extend(list(reader))
                recognized = bool(reader.fieldnames and {'title', 'company', 'url'} <= set(reader.fieldnames)) or recognized
            else:
                parsed = parse_github_tables(text, f"https://github.com/{repository}/blob/{branch}/{path}")
                jobs.extend(parsed)
                recognized = bool(parsed.valid_tables) or recognized
        if not jobs and not recognized:
            raise ValueError("No job rows found. Configure a Markdown/HTML job table, JSON or CSV path.")
        evidence.update(complete=len(paths) <= 10, method='Published GitHub job feed', total=len(jobs))
    elif kind == "trackr":
        from .feeds import parse_trackr

        params = {
            "region": config.get("region", "UK"),
            "industry": config.get("industry", "Tech"),
            "season": str(config.get("season", datetime.now().year + 1)),
            "type": config.get("programme_type", "graduate-programmes"),
        }
        body, _, _ = await fetch("https://api.the-trackr.com/programmes?" + urlencode(params))
        data = json.loads(body)
        posting_collection(data, 'programmes')
        jobs = parse_trackr(data, url)
        evidence.update(complete=True, method='Trackr public programme API', total=len(jobs))
    elif kind in {"linkedin", "indeed"} and config.get("delivery") == "email":
        # Inbound mail imports these; a daily public-web refresh must not claim a sync.
        return []
    elif kind in {
        "json",
        "csv",
        "url",
        "careers",
        "company",
        "html",
        "generic",
        "workable",
        "teamtailor",
        "icims",
        "taleo",
        "workday",
    }:
        body, content_type, final = await fetch(url)
        text = body.decode("utf-8", errors="replace")
        if kind == "json" or "application/json" in content_type:
            parsed = json.loads(text)
            jobs = posting_collection(parsed, None if isinstance(parsed, list) else 'jobs')
            evidence.update(complete=True, method='Public JSON feed', total=len(jobs))
        elif kind == "csv":
            reader = csv.DictReader(io.StringIO(text))
            jobs = list(reader)
            if not jobs and not (reader.fieldnames and {'title', 'company', 'url'} <= set(reader.fieldnames)):
                raise ValueError('CSV response has no recognizable job columns')
            evidence.update(complete=True, method='Public CSV feed', total=len(jobs))
        else:
            try:
                jobs = await discover_career_page(text, final, config)
            except ValueError as exc:
                if not str(exc).startswith('No readable public job collection'):
                    raise
                from .public_renderer import render_public_page
                from .career_pages import page_links
                searches, _ = page_links(text, final)
                direct_search = bool(re.search(r'/(?:search|jobsearch|jobs|vacancies)(?:[/?]|$)', urlsplit(final).path, re.I))
                candidates = list(dict.fromkeys([final, *searches[:2]] if direct_search else [*searches[:2], final]))
                jobs = None
                failures = []
                for candidate in candidates:
                    rendered = None
                    try:
                        rendered, rendered_url = await render_public_page(candidate)
                        soup = BeautifulSoup(rendered, 'html.parser')
                        endpoint_node = soup.find('script', id='meridian-public-search-endpoints')
                        searches = json.loads(endpoint_node.get_text()) if endpoint_node else []
                        endpoint = next((item['url'] for item in searches if item.get('method') == 'GET' and urlsplit(item['url']).path in ('/api/pcsx/search', '/api/apply/v2/jobs')), None)
                        oracle_endpoint = next((item['url'] for item in searches if item.get('method') == 'GET' and urlsplit(item['url']).path == '/hcmRestApi/resources/latest/recruitingCEJobRequisitions'), None)
                        successfactors = next((item for item in searches if item.get('method') == 'POST' and urlsplit(item['url']).path == '/services/recruiting/v1/jobs' and item.get('public_filters') is not None),None)
                        collection_node = soup.find('script',id='meridian-public-paginated-collection')
                        site = re.search(r'/sites/([^/]+)', urlsplit(rendered_url).path)
                        if collection_node:
                            collection=json.loads(collection_node.get_text())
                            details=collection['evidence']
                            jobs=DiscoveryResult(collection['jobs'],complete=details['complete'],authoritative=details['authoritative'],method=details['method'],endpoints=details['endpoints'],total=details['published_total'],limitations=details['limitations'])
                        elif successfactors:
                            from .successfactors import discover_public_jobs
                            jobs = await discover_public_jobs(successfactors,rendered,rendered_url,config)
                        elif endpoint:
                            jobs = await discover({'kind': 'eightfold', 'url': endpoint, 'config': config})
                        elif oracle_endpoint and site:
                            query = dict(parse_qsl(urlsplit(oracle_endpoint).query))
                            finder = query.get('finder', '')
                            api_site = re.search(r'(?:[;,]|^)siteNumber=([^,;]+)', finder)
                            api_sort = re.search(r'(?:[;,]|^)sortBy=([^,;]+)', finder)
                            if not api_site:
                                raise ValueError('Observed Oracle search request has no API site identity')
                            jobs = await discover({'kind': 'oraclehcm', 'url': rendered_url, 'config': config | {'board': site.group(1), 'api_url': oracle_endpoint.split('?', 1)[0], 'api_site': api_site.group(1), 'api_sort': api_sort.group(1) if api_sort else None}})
                        else:
                            jobs = await discover_career_page(rendered, rendered_url, config)
                        break
                    except ValueError as render_error:
                        failures.append(str(render_error))
                        if rendered is not None:
                            # Redirected/CSR landing pages can publish the real
                            # search link only after rendering. Follow those
                            # observed links, never invent an endpoint.
                            child_searches,_ = page_links(rendered,rendered_url)
                            candidates.extend(link for link in child_searches[:2] if link not in candidates and len(candidates)<6)
                if jobs is None:
                    raise ValueError('; '.join(dict.fromkeys(failures))[:400])
                jobs.evidence['method'] += ' (isolated public browser)'
            proof = jobs.evidence
            evidence.update(complete=proof['complete'], authoritative=proof['authoritative'], method=proof['method'], endpoints=proof['endpoints'], total=proof['published_total'], limitations=proof['limitations'])
    else:
        raise ValueError(
            "This platform requires a manual browser session; use public ATS, careers, GitHub, JSON or CSV sources for unattended discovery"
        )
    for job in jobs:
        if config.get("company_name"):
            job["company"] = config["company_name"]
        job["source"] = source.get("name", kind)
    if len(jobs) >= MAX_JOBS:
        evidence.update(complete=False)
        evidence['limitations'].append('Posting limit reached')
    return DiscoveryResult(jobs[:MAX_JOBS], **evidence)


def discovery_busy(db=None):
    with _source_guard:
        return str(db.path.resolve()) in _active_sources if db is not None else bool(_active_sources)


async def run_sources(db, data_dir=None, source_id=None, due_only=False):
    key = str(db.path.resolve())
    with _source_guard:
        if key in _active_sources:
            return {"status": "already_running", "items": [], "total": 0, "jobs_found": 0, "new_jobs": 0}
        _active_sources.add(key)
    try:
        return await _run_sources(db, data_dir, source_id, due_only)
    finally:
        with _source_guard:
            _active_sources.discard(key)


async def _run_sources(db, data_dir=None, source_id=None, due_only=False):
    sources = db.query("SELECT * FROM sources WHERE enabled=1" + (" AND id=?" if source_id else ""), (source_id,) if source_id else ())
    if source_id and not sources:
        raise ValueError('Enabled source not found')
    gate = asyncio.Semaphore(4)
    writer = asyncio.Lock()

    async def persist(function, *args, **kwargs):
        # Fetch boards concurrently, but do not make their worker threads
        # compete for SQLite's single writer during a long reconciliation.
        async with writer:
            return await asyncio.to_thread(function, *args, **kwargs)

    async def scan(source):
        if json.loads(source['config'] or '{}').get('delivery') == 'email':
            return {'source_id': source['id'], 'name': source['name'], 'jobs_found': 0, 'new_jobs': 0,
                    'duplicates': 0, 'error': None, 'state': 'EMAIL_DELIVERY'}
        if due_only and source['last_checked']:
            try:
                if (datetime.now(timezone.utc)-datetime.fromisoformat(source['last_checked'])).total_seconds() < source['poll_minutes']*60:
                    return None
            except ValueError:
                pass
        count = duplicates = new_count = 0
        observed_ids = set()
        matching_ids = set()
        errors = []
        proof = {}
        scan_id = uid()
        async with gate:
            await persist(db.execute, "INSERT INTO source_scans(id,source_id,state,started_at) VALUES(?,?,'RUNNING',?)", (scan_id, source['id'], now()))
            try:
                async with asyncio.timeout(300):
                    jobs = await discover(decode_row(source))
                    proof = getattr(jobs, 'evidence', {'complete': False, 'authoritative': False, 'method': 'legacy extractor'})
                    context = await asyncio.to_thread(matching_context, db)
                    threshold_row = await asyncio.to_thread(db.one, "SELECT value FROM settings WHERE key='daily_min_match'")
                    threshold = json.loads(threshold_row['value']) if threshold_row else 50
                    for job in jobs:
                        try:
                            persisted, duplicate = await persist(ingest_job, db, job, source['id'], match_context=context)
                            observed_ids.add(persisted['id'])
                            count = len(observed_ids)
                            duplicates += int(duplicate)
                            new_count += int(not duplicate)
                            from .job_validity import eligible
                            if persisted['match_score'] >= threshold and persisted['match_details'].get('eligible') and eligible(persisted):
                                matching_ids.add(persisted['id'])
                        except (ValueError, KeyError, TypeError) as exc:
                            errors.append(type(exc).__name__)
                error = '; '.join(errors[:3]) or None
                if not error and proof.get('authoritative'):
                    # Only complete authoritative boards can prove removal by
                    # absence. A failed, truncated or generic scan never closes jobs.
                    def reconcile_board():
                        with db.transaction() as conn:
                            previous = conn.execute('SELECT job_id FROM job_sources WHERE source_id=? UNION SELECT job_id FROM source_job_observations WHERE source_id=?', (source['id'], source['id'])).fetchall()
                            present = set(observed_ids)
                            scopes = set()
                            if proof.get('complete'):
                                # Legacy imports can lack a source association or
                                # use the old URL identity scheme. A complete
                                # board also verifies those exact board URLs.
                                scopes = {scope for endpoint in proof.get('endpoints',[]) if (scope := posting_board_scope(endpoint))}
                                current = [dict(conn.execute('SELECT id,url,application_url FROM jobs WHERE id=?',(identifier,)).fetchone()) for identifier in present]
                                by_identity = {identity:row for row in current if (identity := ats_identity(row['application_url'] or row['url'])[1])}
                                kinds = sorted({scope[0] for scope in scopes})
                                legacy = [dict(row) for row in conn.execute('SELECT id,url,application_url FROM jobs WHERE availability!=\'CLOSED\' AND ats IN ('+','.join('?' for _ in kinds)+')', kinds)
                                          if posting_board_scope(row['application_url'] or row['url']) in scopes] if kinds else []
                                previous.extend({'job_id':row['id']} for row in legacy)
                                for row in legacy:
                                    fresh = by_identity.get(ats_identity(row['application_url'] or row['url'])[1])
                                    if fresh:
                                        present.add(row['id'])
                                        # Keep its original canonical URL and all
                                        # application/history IDs; refresh only
                                        # the current employer application link.
                                        replacement = fresh['application_url'] or fresh['url']
                                        previous_target = row['application_url'] or row['url']
                                        if replacement != previous_target:
                                            conn.execute('INSERT OR IGNORE INTO job_duplicates VALUES(?,?,?,?,?)',
                                                         (uid(),row['id'],previous_target,'Previously verified application URL',now()))
                                            conn.execute('UPDATE jobs SET application_url=? WHERE id=?',(replacement,row['id']))
                            affected = present | ({row['job_id'] for row in previous} if proof.get('complete') else set())
                            stamp = now()
                            recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                            aliases = set()
                            if scopes:
                                for other in conn.execute('SELECT s.id,s.url,(SELECT metadata FROM source_scans WHERE source_id=s.id ORDER BY started_at DESC LIMIT 1) metadata FROM sources s'):
                                    metadata = json.loads(other['metadata'] or '{}')
                                    other_scopes = {scope for endpoint in [other['url'],*metadata.get('endpoints',[])] if (scope := posting_board_scope(endpoint))}
                                    if scopes & other_scopes:
                                        aliases.add(other['id'])
                            for job_id in affected:
                                state = 'OPEN' if job_id in present else 'CLOSED'
                                conn.execute('INSERT INTO source_job_observations VALUES(?,?,?,?) ON CONFLICT(source_id,job_id) DO UPDATE SET state=excluded.state,observed_at=excluded.observed_at', (source['id'], job_id, state, stamp))
                                if state == 'CLOSED' and aliases:
                                    # An older scan of this same board cannot
                                    # overrule its newly verified removal.
                                    conn.execute("UPDATE source_job_observations SET state='CLOSED',observed_at=? WHERE job_id=? AND source_id IN ("+','.join('?' for _ in aliases)+')', (stamp,job_id,*aliases))
                                open_elsewhere = conn.execute("SELECT 1 FROM source_job_observations WHERE job_id=? AND state='OPEN' AND observed_at>=?", (job_id, recent)).fetchone()
                                conn.execute('UPDATE jobs SET availability=?,last_verified=? WHERE id=?', ('OPEN' if open_elsewhere else 'CLOSED', stamp, job_id))
                    await persist(reconcile_board)
            except Exception as exc:
                import sqlite3
                error = (f'HTTP {exc.response.status_code}: public career source rejected the request' if isinstance(exc, httpx.HTTPStatusError) else
                         f'{type(exc).__name__}: {str(exc)[:300]}' if isinstance(exc, ValueError) else
                         'TimeoutError: source scan exceeded 300s' if isinstance(exc, TimeoutError) else
                         f'SQLite persistence error: {str(exc)[:160]}' if isinstance(exc, sqlite3.OperationalError) else
                         f'{type(exc).__name__}: public career extraction failed; verify the configured source')
            health = ('ACCESS_DENIED' if error and (any(f'HTTP {code}' in error for code in (401,403)) or
                                                   error.startswith('PublisherPermissionRequired:')) else
                      'RATE_LIMITED' if error and 'HTTP 429' in error else
                      'TIMEOUT' if error and 'Timeout' in error else 'FAILED' if error else
                      'EMPTY' if not count and proof.get('complete') else 'PARTIAL' if not proof.get('complete') else 'HEALTHY')
            proof['health'] = health
            proof['matching_jobs'] = len(matching_ids)
            await persist(db.execute, 'UPDATE sources SET last_checked=?,last_error=?,jobs_found=?,updated_at=? WHERE id=?', (now(), error, count, now(), source['id']))
            await persist(db.execute, 'UPDATE source_scans SET state=?,finished_at=?,jobs_found=?,new_jobs=?,error=?,metadata=? WHERE id=?', ('FAILED' if error else 'COMPLETED', now(), count, new_count, error, dumps(proof), scan_id))
        return {'source_id': source['id'], 'name': source['name'], 'jobs_found': count, 'new_jobs': new_count,
                'duplicates': duplicates, 'error': error, 'state': health, 'evidence': proof}

    results = [r for r in await asyncio.gather(*(scan(source) for source in sources)) if r is not None]
    return {'items': results, 'total': len(results), 'jobs_found': sum(x['jobs_found'] for x in results),
            'new_jobs': sum(x['new_jobs'] for x in results)}


def source_health(db, source):
    result = decode_row(source)
    if not result['enabled']:
        result['health'] = 'DISABLED'
    elif result['config'].get('delivery') == 'email':
        inboxes = db.query("SELECT provider,status,last_sync,last_error FROM integrations WHERE provider IN ('gmail','outlook','imap')")
        connected = [row for row in inboxes if row['status'] == 'connected']
        result['delivery_evidence'] = inboxes
        result['health'] = ('AWAITING_INBOX_CONNECTION' if not connected else 'INBOX_ERROR' if all(row['last_error'] for row in connected)
                            else 'RECEIVING_ALERTS' if result['last_checked'] else 'AWAITING_ALERTS')
    else:
        scan = db.one('SELECT * FROM source_scans WHERE source_id=? ORDER BY started_at DESC LIMIT 1', (result['id'],))
        evidence = json.loads(scan['metadata'] or '{}') if scan else {}
        result['health'] = ('RUNNING' if scan and scan['state'] == 'RUNNING' else evidence.get('health') or
                            ('FAILED' if result['last_error'] else 'PREVIOUSLY_CHECKED' if result['last_checked'] else 'NOT_CHECKED'))
        result['scan_evidence'] = evidence
    return result
