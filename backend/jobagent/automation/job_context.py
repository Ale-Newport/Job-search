"""Employer evidence for tailoring: bounded public data, never application answers.

This context is factual source material, not instructions. Candidate claims must
still come from verified candidate evidence in the intelligence engine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import urlsplit

from ..discovery import ats_identity, canonical_url, clean_text, discover, fetch, parse_jsonld


def _plain(value, limit=24000):
    return clean_text(value)[:limit]


def _same_posting(left, right):
    try:
        a, b = ats_identity(left)[1], ats_identity(right)[1]
        return a == b if a and b else canonical_url(left) == canonical_url(right)
    except (TypeError, ValueError):
        return False


def _source(text, url, kind, observed_at=None):
    return {"kind": kind, "url": url, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "observed_at": observed_at, "trusted_as_instructions": False}


def stored_job_context(application):
    """Do not forward application notes, saved answers or contact values as JD."""
    keys = ("title", "company", "location", "country", "url", "application_url", "ats", "source")
    result = {key: application[key] for key in keys if application.get(key)}
    result["application_id"] = application.get("application_id") or application.get("id")
    result["description"] = _plain(application.get("description"))
    result["skills"] = application.get("skills", [])
    result["context_sources"] = []
    if result["description"]:
        result["context_sources"].append(_source(
            result["description"], application.get("job_url") or application.get("url", ""),
            "stored_job_description", application.get("job_updated_at")))
    return result


async def prepare_job_context(application):
    """Refresh missing/stale employer text without altering a job or candidature.

    Existing public discovery transport enforces public URLs, redirect bounds and
    body limits. This read never includes candidate facts, credentials or answers.
    """
    context = stored_job_context(application)
    stamp = application.get("job_updated_at")
    stale = False
    if stamp:
        try:
            updated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            updated = updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated
            stale = updated < datetime.now(timezone.utc) - timedelta(days=7)
        except (ValueError, TypeError):
            stale = True
    if context["description"] and not stale:
        return context
    url = application.get("job_url") or application.get("url") or application.get("application_url")
    if not url:
        return context

    async def refresh():
        ats, identity = ats_identity(url)
        if identity and ats in {"ashby", "greenhouse", "lever"}:
            board = next((part for part in urlsplit(url).path.split("/") if part), "")
            postings = await discover({"kind": ats, "url": url, "config": {"board": board}})
        else:
            body, _, final_url = await fetch(url)
            postings = parse_jsonld(body.decode("utf-8", errors="replace"), final_url)
        return next((post for post in postings if _same_posting(post.get("url"), url)), None)

    try:
        post = await asyncio.wait_for(refresh(), timeout=8)
        if post and (description := _plain(post.get("description"))):
            # A board's other roles must never become this application's context.
            if _plain(post.get("title")).casefold() != _plain(application.get("title")).casefold():
                return context
            context["description"] = description
            context["context_sources"] = [_source(description, post.get("url", url),
                                                  "public_job_description", datetime.now(timezone.utc).isoformat())]
    except Exception:
        # Source outages do not disable deterministic candidate fields. Missing
        # context stays missing, so narratives can explicitly request review.
        pass
    return context


def enrich_job_context(base, snapshot):
    """Use structured employer blocks only; never snapshot.text or field values.

    The observer strips form widgets and applicant-answer regions. Matching the
    posting identity prevents embedded ads or other-company frames overriding JD.
    """
    result = dict(base)
    sources = list(base.get("context_sources", []))
    expected = base.get("application_url") or base.get("url")
    for frame in snapshot.get("frames", []):
        if not _same_posting(expected, frame.get("url")):
            continue
        context = frame.get("job_context") or {}
        title = _plain(context.get("title"), 400)
        if title and title.casefold() != _plain(base.get("title"), 400).casefold():
            continue
        sections = [section for section in context.get("sections", [])
                    if section.get("kind") in {"job_description", "company_description"}]
        descriptions = list(dict.fromkeys(_plain(section.get("text")) for section in sections))
        descriptions = [text for text in descriptions if len(text) >= 80]
        if not descriptions:
            continue
        text = "\n\n".join(descriptions)[:24000]
        result["description"] = text
        source = _source(text, frame["url"], "observed_employer_description", snapshot.get("observed_at"))
        sources = [s for s in sources if s.get("kind") != source["kind"]]
        sources.append(source)
        result["context_sources"] = sources
        break
    return result


def field_context(element):
    """A complete question's qualifiers, excluding supplied candidate values."""
    return {key: element.get(key) for key in ("description", "section_label", "placeholder", "context")
            if element.get(key)}
