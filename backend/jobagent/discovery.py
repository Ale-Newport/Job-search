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
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .db import decode_row, dumps, now, uid
from .matching import (
    contract_types,
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
        "location": clean_text(raw.get("location"))[:1000],
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
    return result


def rescore_job(db, job: dict):
    company = decode_row(
        db.one(
            "SELECT * FROM companies WHERE id=? OR name=? COLLATE NOCASE",
            (job.get("company_id"), job.get("company", "")),
        )
    )
    if company:
        job = dict(job, company_priority=company["priority"], company_blacklist_keywords=company["blacklist_keywords"])
    facts = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    profiles = db.query("SELECT * FROM search_profiles")
    feedback = db.query(
        "SELECT f.*,j.title,j.company FROM job_feedback f JOIN jobs j ON j.id=f.job_id ORDER BY f.created_at DESC LIMIT 100"
    )
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
    matches = [match_job(job, facts, profile, feedback) for profile in profiles] or [
        match_job(job, facts, None, feedback)
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


def ingest_job(db, raw: dict, source_id: str | None = None) -> tuple[dict, bool]:
    job = normalize_job(raw)
    stamp = now()
    duplicate = False
    payload_hash = hashlib.sha256(dumps(job).encode()).hexdigest()
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
            conn.execute(
                "INSERT OR IGNORE INTO job_duplicates VALUES(?,?,?,?,?)",
                (uid(), job_id, job["url"], "Canonical URL, ATS identity or identical cross-post", stamp),
            )
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
                conn.execute(
                    "UPDATE jobs SET description=?,skills=?,salary_min=?,salary_max=?,currency=?,metadata=?,remote=COALESCE(?,remote),posted_at=COALESCE(?,posted_at),experience_level=COALESCE(?,experience_level),updated_at=? WHERE id=?",
                    (
                        job["description"],
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
        conn.execute(
            "INSERT OR IGNORE INTO job_sources VALUES(?,?,?,?,?,?)",
            (uid(), job_id, source_id, job["url"], raw.get("external_id"), stamp),
        )
        conn.execute(
            "INSERT OR IGNORE INTO job_snapshots VALUES(?,?,?,?,?)", (uid(), job_id, payload_hash, dumps(job), stamp)
        )
    persisted = decode_row(db.one("SELECT * FROM jobs WHERE id=?", (job_id,)))
    match = rescore_job(db, persisted)
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


async def fetch(url: str, *, accept="application/json,text/html", max_body=MAX_BODY) -> tuple[bytes, str, str]:
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
        address = value.get("address", value)
        if isinstance(address, str):
            return address
        return ", ".join(
            str(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k)
        ) or value.get("name", "")
    return ""


def parse_jsonld(text: str, url: str) -> list[dict]:
    soup = BeautifulSoup(text, "html.parser")
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
        for item in data.get("jobs", [])
    ]


def parse_lever(data: list, board: str) -> list[dict]:
    result = []
    for item in data:
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
        for item in data.get("jobs", [])
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


async def discover(source: dict) -> list[dict]:
    config = source.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)
    kind, url = source["kind"], source.get("url", "")
    parts = [x for x in urlsplit(url).path.split("/") if x]
    board = str(config.get("board") or config.get("company") or (parts[0] if parts else ""))
    if kind in {"greenhouse", "lever", "ashby"}:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", board):
            raise ValueError("Configure a valid public ATS board identifier")
        if kind == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true"
        elif kind == "lever":
            region = "api.eu.lever.co" if config.get("region") == "eu" or "eu.lever.co" in url else "api.lever.co"
            url = f"https://{region}/v0/postings/{quote(board)}?mode=json&limit=100&skip=0"
        else:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true"
        body, _, _ = await fetch(url, max_body=64 * 1024 * 1024)
        data = json.loads(body)
        if kind == "lever":
            items = list(data)
            while len(data) == 100 and len(items) < MAX_JOBS:
                body, _, _ = await fetch(url.rsplit("skip=", 1)[0] + "skip=" + str(len(items)))
                data = json.loads(body)
                items.extend(data)
            jobs = parse_lever(items, board)
        else:
            jobs = parse_greenhouse(data, board) if kind == "greenhouse" else parse_ashby(data, board)
    elif kind == "smartrecruiters":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", board):
            raise ValueError("Configure a SmartRecruiters company identifier")
        jobs, offset = [], 0
        while offset < MAX_JOBS:
            base = f"https://api.smartrecruiters.com/v1/companies/{quote(board)}/postings"
            body, _, _ = await fetch(f"{base}?limit=100&offset={offset}&destination=PUBLIC")
            page = json.loads(body)
            postings = page.get("content", [])
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
                or offset >= page.get("totalFound", 0)
                or len(jobs) >= min(int(config.get("max_jobs", 100)), MAX_JOBS)
            ):
                break
    elif kind == "rss":
        body, _, final = await fetch(url, accept="application/rss+xml,application/atom+xml,application/xml")
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("RSS entities and document types are not supported")
        root = ET.fromstring(body)
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
    elif kind == "github":
        repository = config.get("repository") or "/".join(parts[:2])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""):
            raise ValueError("GitHub source needs owner/repository")
        branch = quote(str(config.get("branch", "HEAD")), safe="")
        paths = config.get("paths", ["README.md"])
        if isinstance(paths, str):
            paths = [paths]
        jobs, links = [], set()
        for path in paths[:10]:
            body, _, _ = await fetch(
                f"https://raw.githubusercontent.com/{repository}/{branch}/{quote(str(path), safe='/')}"
            )
            text = body.decode("utf-8", errors="replace")
            parser = config.get("parser", "markdown")
            if parser == "json":
                parsed = json.loads(text)
                jobs.extend(parsed if isinstance(parsed, list) else parsed.get("jobs", []))
            elif parser == "csv":
                jobs.extend(list(csv.DictReader(io.StringIO(text))))
            else:
                links.update(re.findall(r"https?://[^\s<>\]\)\"']+", text))
        for link in sorted(links)[:40]:
            if ats_identity(link)[1]:
                try:
                    body, _, final = await fetch(link)
                    jobs.extend(parse_jsonld(body.decode("utf-8", errors="replace"), final))
                except (ValueError, httpx.HTTPError):
                    continue
        if links and not jobs:
            raise ValueError(
                "GitHub links were found but no structured job listings were extractable; use a JSON/CSV feed or direct public ATS source"
            )
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
            jobs = parsed if isinstance(parsed, list) else parsed.get("jobs", [])
        elif kind == "csv":
            jobs = list(csv.DictReader(io.StringIO(text)))
        else:
            jobs = parse_jsonld(text, final)
            if not jobs:
                soup = BeautifulSoup(text, "html.parser")
                boards = {}
                for anchor in soup.find_all("a", href=True):
                    link = urljoin(final, anchor["href"])
                    ats, _ = ats_identity(link)
                    path = [x for x in urlsplit(link).path.split("/") if x]
                    if ats in {"greenhouse", "lever", "ashby"} and path:
                        boards[(ats, path[0])] = link
                for (ats, slug), link in list(boards.items())[:4]:
                    jobs.extend(await discover({"kind": ats, "url": link, "config": {"board": slug}}))
                if not boards:
                    links = [
                        urljoin(final, a["href"])
                        for a in soup.find_all("a", href=True)
                        if any(term in a["href"].lower() for term in ("/job/", "/jobs/", "/careers/", "/positions/"))
                    ]
                    for link in list(dict.fromkeys(links))[:20]:
                        if urlsplit(link).hostname == urlsplit(final).hostname and link != final:
                            try:
                                child, _, location = await fetch(link)
                                jobs.extend(parse_jsonld(child.decode("utf-8", errors="replace"), location))
                            except (ValueError, httpx.HTTPError):
                                continue
                if not jobs:
                    raise ValueError(
                        "No public structured job postings found; configure an ATS board or add a job URL manually"
                    )
    else:
        raise ValueError(
            "This platform requires a manual browser session; use public ATS, careers, GitHub, JSON or CSV sources for unattended discovery"
        )
    for job in jobs:
        if config.get("company_name"):
            job["company"] = config["company_name"]
        job["source"] = source.get("name", kind)
    return jobs[:MAX_JOBS]


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
    sources = db.query(
        "SELECT * FROM sources WHERE enabled=1" + (" AND id=?" if source_id else ""), (source_id,) if source_id else ()
    )
    if source_id and not sources:
        raise ValueError("Enabled source not found")
    results = []
    for source in sources:
        if due_only and source["last_checked"]:
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(source["last_checked"])).total_seconds()
                if elapsed < source["poll_minutes"] * 60:
                    continue
            except ValueError:
                pass
        count = duplicate_count = 0
        errors = []
        try:
            jobs = await discover(decode_row(source))
            for job in jobs:
                try:
                    _, duplicate = await asyncio.to_thread(ingest_job, db, job, source["id"])
                    count += 1
                    duplicate_count += int(duplicate)
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append(str(exc)[:200])
            error = "; ".join(errors[:3]) or None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, ET.ParseError) as exc:
            error = f"{type(exc).__name__}: {str(exc)[:400]}"
        db.execute(
            "UPDATE sources SET last_checked=?,last_error=?,jobs_found=?,updated_at=? WHERE id=?",
            (now(), error, count, now(), source["id"]),
        )
        results.append(
            {
                "source_id": source["id"],
                "name": source["name"],
                "jobs_found": count,
                "new_jobs": count - duplicate_count,
                "duplicates": duplicate_count,
                "error": error,
            }
        )
    return {
        "items": results,
        "total": len(results),
        "jobs_found": sum(x["jobs_found"] for x in results),
        "new_jobs": sum(x["new_jobs"] for x in results),
    }
