"""Public job-list parsers. Source claims retain their provenance and unknown fields."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


def text_cell(value):
    value = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", value)
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True).strip(" *")


def links_cell(value, base):
    links = [a.get("href") for a in BeautifulSoup(value, "html.parser").find_all("a", href=True)]
    links += re.findall(r"\[[^\]]*\]\((https?://[^\s)]+)(?:\s+[^)]*)?\)", value)
    return [urljoin(base, link) for link in links if urlsplit(urljoin(base, link)).scheme in {"http", "https"}]


def parse_github_tables(content: str, source_url: str) -> list[dict]:
    tables = []
    header, rows = [], []
    for line in content.splitlines():
        if not line.strip().startswith("|"):
            if rows:
                tables.append((header, rows))
            header, rows = [], []
            continue
        cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
        if all(re.fullmatch(r"[\s:\-]+", cell) for cell in cells):
            continue
        if not header:
            header = [text_cell(c).lower() for c in cells]
        else:
            rows.append(cells)
    if rows:
        tables.append((header, rows))
    for table in BeautifulSoup(content, "html.parser").find_all("table"):
        trs = table.find_all("tr")
        if trs:
            tables.append(
                (
                    [c.get_text(" ", strip=True).lower() for c in trs[0].find_all(["th", "td"])],
                    [[str(c) for c in row.find_all("td")] for row in trs[1:]],
                )
            )
    jobs, seen = [], set()
    for headers, rows in tables:

        def index(names):
            return next((i for i, h in enumerate(headers) if h in names), None)

        company_i, title_i = index({"company", "company name"}), index({"position", "role", "job title", "title"})
        apply_i, location_i = (
            index({"posting", "apply", "application", "link", "application link", "application/link"}),
            index({"location", "locations"}),
        )
        if company_i is None or title_i is None or apply_i is None:
            continue
        company = ""
        for cells in rows:
            if len(cells) < len(headers):
                continue
            label = text_cell(cells[company_i])
            if label and label not in {"↳", "↪", "→", "", '"'}:
                company = label
            title = text_cell(cells[title_i]).replace("🆕", "").strip()
            links = links_cell(cells[apply_i], source_url)
            if (
                not links
                or not company
                or not title
                or "🔒" in cells[apply_i]
                or re.search(r"\bclosed\b", text_cell(cells[apply_i]), re.I)
            ):
                continue
            url = links[0]
            if url in seen:
                continue
            seen.add(url)
            age_i = index({"age"})
            age_text = text_cell(cells[age_i]) if age_i is not None else ""
            age_match = re.fullmatch(r"(\d+)d", age_text)
            posted_at = (
                (datetime.now(timezone.utc) - timedelta(days=int(age_match[1]))).isoformat() if age_match else None
            )
            posted_i = index({"date posted", "date"})
            if not posted_at and posted_i is not None:
                try:
                    current = datetime.now(timezone.utc)
                    posted = datetime.strptime(text_cell(cells[posted_i]), "%b %d").replace(
                        year=current.year, tzinfo=timezone.utc
                    )
                    if posted.date() > current.date():
                        posted = posted.replace(year=posted.year - 1)
                    posted_at = posted.isoformat()
                except ValueError:
                    pass
            jobs.append(
                {
                    "posted_at": posted_at,
                    "company": company,
                    "title": title,
                    "url": url,
                    "location": text_cell(cells[location_i]) if location_i is not None else "",
                    "metadata": {
                        "listing_source_url": source_url,
                        "listing_fields": {h: text_cell(c) for h, c in zip(headers, cells)},
                        "company_url": next(iter(links_cell(cells[company_i], source_url)), None),
                    },
                }
            )
    return jobs


def parse_trackr(data: dict, source_url: str) -> list[dict]:
    jobs = []
    today = datetime.now(timezone.utc).date().isoformat()
    for p in data.get("programmes", []):
        # Rows without a live URL are anticipated programmes, not open vacancies.
        if not p.get("url") or not p.get("company", {}).get("name"):
            continue
        closed = bool(p.get("closingDate") and p["closingDate"][:10] < today and not p.get("rolling"))
        company = p["company"]
        jobs.append(
            {
                "title": p["name"],
                "company": company["name"],
                "url": p["url"],
                "location": "; ".join(", ".join(reversed(loc.split("|"))) for loc in p.get("locations", []))
                or p.get("region", ""),
                "description": p.get("notes") or "",
                "posted_at": p.get("openingDate"),
                "external_id": p["id"],
                "experience_level": "graduate",
                "metadata": {
                    "listing_source_url": source_url,
                    "company_description": company.get("description"),
                    "company_url": company.get("careersSite"),
                    "company_source": "Trackr",
                    "application_deadline": p.get("closingDate"),
                    "closed": closed,
                    "opening_date": p.get("openingDate"),
                    "cv_required": p.get("cv"),
                    "cover_letter": p.get("coverLetter"),
                    "written_answers": p.get("writtenAnswers"),
                    "sponsorship": company.get("sponsorsVisa") or None,
                    "recruitment_process": p.get("process"),
                    "categories": p.get("categories", []),
                    "listing_notes": p.get("notes"),
                    "rolling": p.get("rolling"),
                },
            }
        )
    return jobs
