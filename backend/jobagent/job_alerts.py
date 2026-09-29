"""Extract job links from LinkedIn/Indeed alert mail without logging in to scrape."""

import re
from email.utils import parseaddr
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup


def job_destination(href):
    parsed = urlsplit(href)
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)", parsed.path)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match[1]}", "linkedin"
    elif host == "indeed.com" or host.endswith(".indeed.com"):
        query = parse_qs(parsed.query)
        key = (query.get("jk") or query.get("vjk") or [None])[0]
        if key and re.fullmatch(r"[A-Za-z0-9_-]{5,80}", key):
            return f"https://{host}/viewjob?jk={key}", "indeed"
    return None


def plain_alert_links(body):
    links = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        destinations = [job_destination(u) for u in re.findall(r"https?://[^\s<>]+", block)]
        destinations = [d for d in destinations if d]
        if not destinations or len(lines) < 3:
            continue
        title, employer = lines[:2]
        if title.startswith("http") or employer.startswith("http") or len(title) > 300:
            continue
        parts = re.split(r"\s+(?:-|–|·|•)\s+", employer, maxsplit=1)
        company, location = parts[0], parts[1] if len(parts) > 1 else ""
        for url, platform in destinations:
            links.append(
                {
                    "url": url,
                    "platform": platform,
                    "title": title,
                    "company": company,
                    "location": location,
                    "context": re.sub(r"https?://[^\s<>]+", "", block)[:1500],
                }
            )
    return links[:100]


def is_job_alert(message):
    sender = parseaddr(message.get("sender", ""))[1].rsplit("@", 1)[-1].lower()
    trusted = any(sender == p + ".com" or sender.endswith("." + p + ".com") for p in ("indeed", "linkedin"))
    text = message.get("subject", "") + "\n" + message.get("body", "")[:600]
    return trusted and bool(
        re.search(r"job alert|\d+ (?:more|new) .*jobs|jobs? (?:for you|match)|recommended jobs", text, re.I)
    )


def alert_links(html):
    soup = BeautifulSoup(html[:400000], "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        destination = job_destination(a["href"])
        if not destination:
            continue
        url, platform = destination
        title = a.get_text(" ", strip=True)
        container = a.find_parent("td") or a.parent
        lines = list(container.stripped_strings) if container else []
        company_node = container.select_one('[class*="company"], [class*="employer"]') if container else None
        company = company_node.get_text(" ", strip=True) if company_node else ""
        if not company and title in lines:
            after = lines[lines.index(title) + 1 :]
            if after:
                candidate = after[0]
                if 2 <= len(candidate) <= 100 and not re.search(
                    r"apply|view|jobs|salary|£|\$|ago|match|unsubscribe", candidate, re.I
                ):
                    company = re.split(r"\s+[·•]\s+", candidate)[0]
        links.append(
            {"url": url, "title": title, "company": company, "platform": platform, "context": " | ".join(lines)[:1200]}
        )
    return links[:100]


def ingest_alert(db, message):
    from .db import now
    from .discovery import ingest_job

    sender = parseaddr(message.get("sender", ""))[1].rsplit("@", 1)[-1].lower()
    platform = next(
        (p for p in ("linkedin", "indeed") if sender == p + ".com" or sender.endswith("." + p + ".com")), None
    )
    candidates = plain_alert_links(message.get("body", "")) + message.get("metadata", {}).get("job_alert_links", [])
    links = list({v["url"]: v for v in reversed(candidates) if v.get("platform") == platform}.values())
    if not platform or not links:
        return []
    source = db.one(
        "SELECT id FROM sources WHERE enabled=1 AND kind=? AND json_extract(config,'$.delivery')='email'", (platform,)
    )
    if not source:
        return []
    jobs = []
    for link in links:
        if (
            not link.get("company")
            or len(link["title"]) < 5
            or re.fullmatch(r"apply(?: now)?|view(?: job)?|see more jobs", link["title"], re.I)
        ):
            continue
        job, _ = ingest_job(
            db,
            {
                "title": link["title"],
                "company": link["company"],
                "url": link["url"],
                "source": ("LinkedIn" if platform == "linkedin" else "Indeed") + " alerts",
                "location": link.get("location", ""),
                "posted_at": message.get("received_at"),
                "metadata": {
                    "listing_source_url": link["url"],
                    "email_alert_id": message["external_id"],
                    "alert_excerpt": link["context"],
                    "details_unverified": True,
                },
            },
            source["id"],
        )
        jobs.append(job["id"])
    if jobs:
        db.execute(
            "UPDATE sources SET last_checked=?,jobs_found=(SELECT count(DISTINCT job_id) FROM job_sources WHERE source_id=?),last_error=NULL,updated_at=? WHERE id=?",
            (now(), source["id"], now(), source["id"]),
        )
    return list(dict.fromkeys(jobs))


def reprocess_alerts(db):
    """Upgrade previously imported plain-text alerts without fetching mail or duplicating rows."""
    import json
    from .db import dumps, now

    total = 0
    for row in db.query("SELECT * FROM email_messages WHERE application_id IS NULL"):
        message = dict(row)
        if not is_job_alert(message):
            if message["classification"] == "RECRUITER_MESSAGE":
                from .mail import classify_message

                analysis = classify_message(message["subject"], message["body"], message["received_at"])
                if analysis["category"] == "OTHER":
                    metadata = json.loads(message["metadata"] or "{}")
                    metadata["analysis"] = analysis
                    with db.transaction() as conn:
                        conn.execute(
                            "UPDATE email_messages SET classification='OTHER',metadata=? WHERE id=?",
                            (dumps(metadata), message["id"]),
                        )
                        conn.execute(
                            "UPDATE human_tasks SET status='resolved',answer='No recruitment update detected; original email retained',updated_at=? WHERE kind='EMAIL_LINK' AND question LIKE ?",
                            (now(), f"%{message['id']}%"),
                        )
            continue
        message["metadata"] = json.loads(message["metadata"] or "{}")
        if message["metadata"].get("alert_parser_version") == 1:
            continue
        jobs = ingest_alert(db, message)
        message["metadata"].update(
            alert_job_ids=jobs,
            alert_parser_version=1,
            analysis={
                "category": "OTHER",
                "status": None,
                "confidence": 1,
                "evidence": "Job alert, not an application update",
            },
        )
        with db.transaction() as conn:
            conn.execute(
                "UPDATE email_messages SET classification='OTHER',confidence=1,metadata=? WHERE id=?",
                (dumps(message["metadata"]), message["id"]),
            )
            conn.execute(
                "UPDATE human_tasks SET status='resolved',answer='Job alert imported as opportunities',updated_at=? WHERE kind='EMAIL_LINK' AND question LIKE ?",
                (now(), f"%{message['id']}%"),
            )
        total += len(jobs)
    return total
