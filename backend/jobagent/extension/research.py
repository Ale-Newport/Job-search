"""Background company and role research for the browser assistant (no model calls, ever).

One ``ResearchManager`` per process (``app.state.extension_research``). A page session starts one *run* per
``(tail_key(company), job_key)``; runs are shared by every tab on the same posting and read in O(1) per field.

Pipeline (docs/EXTENSION_DESIGN.md 5.6), every stage bounded and independent:

* ``identify`` (local, instant): company, title and posting identity from the page report (user override, JSON-LD,
  og tags, title patterns, ATS slug). A company below 0.5 confidence parks the run in ``needs_input``.
* ``role``: the job description from JSON-LD, the observed employer text, a stored job row, a single-posting ATS
  API or the fetched posting, accepted only when the title matches the page.
* ``site``: the official website, verified by one homepage fetch before any of its pages may become model context
  ("wrong company" is worse than thin context). A last-resort public web lookup (company name only) sits behind
  the ``web_lookup`` setting and is off under ``MERIDIAN_TEST``.
* ``pages``: about / mission / values / engineering / careers pages of the verified domain only, fixed GET-safe
  URLs, robots.txt honoured, never LinkedIn/Indeed/Glassdoor, never a browser fallback.

All network I/O goes through :func:`fetch_public` (IP-pinned, every redirect hop re-validated, size and time
bounded). Everything fetched is untrusted data: sanitised, hashed, flagged ``trusted_as_instructions: False``.
Caches live under ``data_dir/cache/extension-research`` and hold public data only (never candidate data).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import ipaddress
import json
import os
import re
import socket
import logging
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote_plus, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..discovery import ats_identity, canonical_url
from ..security import is_public_ip, private_checkpoint
from .markup import bounded_html, html_to_text, parse_isolated
from .places import country_of, jsonld_place, labelled_location, text_place, title_place
from .urls import is_standard_https, scrub_url

SCHEMA = 1
SOFT_DEADLINE = 25.0
HARD_DEADLINE = 40.0
CANCEL_GRACE = 20.0
MAX_REQUESTS = 14
REQUEST_TIMEOUT = 8.0
PAGE_LIMIT = 12000
HOMEPAGE_MAX_BODY = 300_000  # a homepage is read only to verify a site: far below the 1.5 MB of any other page
JD_LIMIT = 24000
MAX_CONTEXT_ITEMS = 8
KEEP_RUNS = 32
TTL_PAGE = 7 * 86400
TTL_ROLE = 3 * 86400
TTL_ROBOTS = 86400
TTL_SEARCH = 90 * 86400
TTL_FAILURE = 15 * 60
TTL_BOARD = 3600
MAX_AGE = 30 * 86400
CACHE_FILE_CAP = 2000
PRUNE_INTERVAL = 6 * 3600  # the cache directory is tidied again this often in a long-running process
MAX_JSONLD_NODES = 400  # the page's structured data is read up to this many nodes (a listing page has a few dozen)
MAX_BOARDS = 4  # ATS job boards kept in memory (one is a whole board's JSON: up to 3 MB)
ASSISTED_NOTE = "Page text captured by the Meridian browser assistant."  # appended to a job's text by session.py
MAX_JSONLD_POSTINGS = 3  # JobPosting nodes of one page read as its description (a page may carry eight 200 KB scripts)
TERMINAL = {"done", "partial", "failed", "cancelled"}
SETTLED = TERMINAL | {"needs_input"}
USER_AGENT = "Meridian/1.0 (personal read-only job discovery)"  # same product UA as discovery.fetch
ROBOTS_AGENT = "Meridian"
BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa", ".corp")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏ -‮⁠-⁤﻿]")
# Lines of employer text that read like instructions to a model. THIS IS DEFENCE IN DEPTH, NOT THE CONTROL: a regex can
# never recognise every way to word an instruction (a polite paraphrase, another language, a story). The control is that
# employer text is labelled untrusted data in every model prompt, is never placed where an instruction is expected, and
# that a model's answer is checked against the candidate's own verified facts before anything is typed into a form
# (docs/EXTENSION_DESIGN.md). What the filter buys is dropping the crude, copy-pasted attacks before they reach a prompt,
# and the patterns are kept narrow so that ordinary employer text ("System: Linux", "forget about the commute", a
# "## Instructions" heading) is never removed. The tests pin both sides.
_EN_VERBS = r"(?:ignor(?:e|ing)|disregard(?:ing)?|forget)"
_EN_OVERRIDE = r"(?:ignor(?:e|ing)|disregard(?:ing)?|forget|override|overrule|bypass|disobey)"  # only with "instructions" etc.
_EN_BEFORE = r"(?:previous(?:ly)?|above|prior|earlier|preceding|foregoing|former|original)"
_EN_THINGS = (r"(?:instructions?|prompts?|rules|guidelines|directions|directives|commands?|messages?|context|constraints|"
              r"restrictions|policies|policy|guardrails|safeguards|everything|anything|all|text)")
_ES_VERBS = r"(?:ignor(?:a|e|ar|en|ad)|olvid(?:a|e|ar|en|ad)|desestim(?:a|e|ar|en)|descart(?:a|e|ar|en)|haz caso omiso|haga caso omiso)"
_ES_BEFORE = r"(?:anterior(?:es)?|previ[ao]s?|precedentes?|de arriba|arriba|originales?)"
_ES_THINGS = (r"(?:instrucci(?:o|ó)n(?:es)?|indicaciones|(?:o|ó)rdenes|reglas|normas|directrices|mensajes?|prompts?|"
              r"restricciones|pol(?:i|í)ticas?|contexto|todo|lo)")
INJECTION = re.compile(
    r"(?i)\b" + _EN_VERBS + r"\b[^.\n]{0,40}\b" + _EN_BEFORE + r"\b[^.\n]{0,25}\b" + _EN_THINGS + r"\b"
    r"|\b" + _EN_VERBS + r"\b\W+(?:\w+\W+){0,4}?(?:above|earlier|preceding|before (?:this|that))\b"
    r"|\b" + _EN_OVERRIDE + r"\b[^.\n]{0,30}\b(?:instructions?|system prompts?|prompts?|guidelines|guardrails)\b"
    r"|\b" + _ES_VERBS + r"\b[^.\n]{0,40}\b" + _ES_BEFORE + r"\b[^.\n]{0,25}\b" + _ES_THINGS + r"\b"
    r"|\b" + _ES_VERBS + r"\b[^.\n]{0,40}\b(?:instrucci(?:o|ó)n(?:es)?|indicaciones|(?:o|ó)rdenes)\b"
    r"|\bsystem (?:prompt|message|instructions?)\b|\b(?:prompt|mensaje|instrucciones) del sistema\b"
    r"|\byou are (?:now |no longer )?(?:an? )?(?:ai|assistant|language model|chatbot|llm)\b(?!\s+\w+(?:er|or|ist)\b)"
    r"|\b(?:eres|a partir de ahora eres|act(?:ú|u)a como)\s+(?:ahora\s+)?(?:un|una)?\s*(?:ia|inteligencia artificial|asistente|"
    r"modelo de lenguaje|chatbot)\b"
    r"|\b" + _ES_VERBS + r"\b\W+(?:\w+\W+){0,3}?(?:lo anterior|lo de arriba|lo previo|lo escrito|lo dicho|arriba)\b"
    r"|<\s*/?\s*(?:system|assistant|developer|instructions?)\s*>|\[/?INST\]|<<\s*/?SYS\s*>>"
    r"|<\|(?:im_start|im_end|system|assistant|endoftext)\|>"
    r"|^\s*#{1,6}\s*(?:new|updated|real|actual|additional|important|override|secret|hidden)\s+"
    r"(?:task|instructions?|prompt|directives?|orders?|rules|tareas?|instrucciones)\b"
    r"|^\s*#{1,6}\s*(?:system|developer|assistant)(?:\s+(?:prompt|message|instructions?))?\s*:?\s*$"
    r"|^\s*(?:new|updated|override|nuevas?)\s+(?:task|instructions?|tarea|instrucciones)\s*:"
    r"|\b(?:new|updated|nuevas?)\s+(?:instructions?|instrucciones)\s*:"
)
# An upper-case role label at the start of a line ("SYSTEM: you must..."): case-sensitive, so "System: Linux" is kept.
ROLE_LABEL = re.compile(r"^\s*(?:#{1,6}\s*)?(?:SYSTEM|ASSISTANT|DEVELOPER|ADMIN(?:ISTRATOR)?|SISTEMA)\s*(?:PROMPT|MESSAGE|MENSAJE)?\s*:")
# "i g n o r e", "i.g.n.o.r.e": letters of one word separated by single spaces or punctuation are joined before matching.
SPACED_LETTERS = re.compile(r"(?<![A-Za-z])(?:[A-Za-z][ .\-_*·]){3,}[A-Za-z](?![A-Za-z])")


def looks_like_instructions(line: str) -> bool:
    """Does this line of employer text read like an instruction to a model (see ``INJECTION``: defence in depth)?"""
    text = str(line or "")
    if INJECTION.search(text) or ROLE_LABEL.search(text):
        return True
    joined = SPACED_LETTERS.sub(lambda match: re.sub(r"[^A-Za-z]", "", match.group(0)), text)
    return joined != text and bool(INJECTION.search(joined) or ROLE_LABEL.search(joined))


# --------------------------------------------------------------------------------------------- domains
MULTI_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "com.au", "net.au", "org.au", "co.nz",
    "co.jp", "ne.jp", "or.jp", "co.kr", "com.br", "com.mx", "com.ar", "com.co", "com.sg", "com.hk", "com.cn",
    "com.tr", "com.tw", "com.my", "com.ph", "com.pk", "com.ng", "co.in", "co.id", "co.il", "co.za", "co.ke",
}
# Hosting/ATS suffixes where each sub-label is a different tenant, never "the company's registrable domain".
ATS_SUFFIXES = {
    "myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com",
    "teamtailor.com", "icims.com", "taleo.net", "recruitee.com", "bamboohr.com", "personio.de", "personio.com",
    "pinpointhq.com", "rippling.com", "join.com", "oraclecloud.com", "eightfold.ai", "successfactors.com",
    "successfactors.eu", "jobvite.com", "breezy.hr", "applytojob.com", "workablehq.com", "comeet.com",
    "dayforcehcm.com",
    # candidate portals hosted for the employer under the vendor's own domain (``hsbc.tal.net``): every sub-label is another
    # employer's tenant, so the host is never that employer's own website and two tenants are never one registrable domain
    "tal.net", "avature.net", "csod.com", "ultipro.com", "yello.co", "jobtrain.co.uk", "webrecruit.co.uk", "beapplied.com",
}
HOSTING_SUFFIXES = {
    "github.io", "vercel.app", "netlify.app", "herokuapp.com", "notion.site", "webflow.io", "framer.website",
    "pages.dev", "wixsite.com", "myshopify.com", "carrd.co", "squarespace.com", "blogspot.com", "wordpress.com",
    "medium.com",
}
TENANT_SUFFIXES = ATS_SUFFIXES | HOSTING_SUFFIXES
# Never an employer's own site: job boards, aggregators, social, registries, encyclopaedias, review sites.
NON_OFFICIAL_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "glassdoor.co.uk", "monster.com", "ziprecruiter.com", "reed.co.uk",
    "totaljobs.com", "cv-library.co.uk", "efinancialcareers.com", "wellfound.com", "angel.co", "builtin.com",
    "otta.com", "welcometothejungle.com", "workingnomads.com", "remoteok.com", "jooble.org", "adzuna.co.uk",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com", "reddit.com",
    "wikipedia.org", "wikidata.org", "crunchbase.com", "bloomberg.com", "companieshouse.gov.uk", "opengovuk.com",
    "find-and-update.company-information.service.gov.uk", "trustpilot.com", "ambitionbox.com", "zoominfo.com",
    "dnb.com", "pitchbook.com", "owler.com", "yelp.com", "github.com", "medium.com", "bing.com", "duckduckgo.com",
    "yahoo.com",
}
AGGREGATORS = {"linkedin.com", "indeed.com", "glassdoor.com", "glassdoor.co.uk", "monster.com", "ziprecruiter.com",
               "reed.co.uk", "totaljobs.com"}
ATS_BRANDS = {"greenhouse", "lever", "ashby", "ashbyhq", "workday", "myworkdayjobs", "smartrecruiters", "workable",
              "teamtailor", "icims", "taleo", "recruitee", "bamboohr", "personio", "pinpoint", "rippling", "jobvite",
              "breezy", "linkedin", "indeed", "glassdoor", "join", "careers", "jobs", "apply", "job board",
              "applicant tracking", "eightfold", "successfactors", "oracle"}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address((host or "").strip("[]"))
        return True
    except ValueError:
        return False


def registrable_domain(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    if _is_ip(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if any(host == t or host.endswith("." + t) for t in TENANT_SUFFIXES):
        return host  # every sub-label is a different tenant; never group them
    last2, last3 = ".".join(labels[-2:]), ".".join(labels[-3:])
    return last3 if last2 in MULTI_LABEL_SUFFIXES else last2


def is_ats_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in ATS_SUFFIXES)


def is_non_official(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return (registrable_domain(host) in NON_OFFICIAL_DOMAINS or host in NON_OFFICIAL_DOMAINS
            or host.endswith(".gov.uk") or host.endswith(".gov") or is_ats_host(host))


# --------------------------------------------------------------------------------------------- names
LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:ltd\.?|limited|llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|co\.?|plc|gmbh|ag|s\.?a\.?(?:s\.?)?|"
    r"s\.?l\.?|b\.?v\.?|n\.?v\.?|pty|pte|llp|lp|oy|ab|as|a/s|srl|s\.r\.l\.|kk|k\.k\.|holdings?|group)\.?$", re.I)
PAGE_NOISE = re.compile(
    r"\s*(?:[-|–—·•]\s*)?(?:careers?|jobs?|job board|hiring|work with us|join us|vacancies|home)\s*$", re.I)
GENERIC_TAIL = {"bank", "technologies", "technology", "group", "holdings", "labs", "systems", "software", "payments",
                "capital", "financial", "services", "international", "global", "uk", "europe", "emea", "solutions",
                "partners"}


def clean(value, limit: int) -> str:
    """Strip control and zero-width characters and bound the length. Page data is never trusted."""
    return CONTROL.sub("", str(value or "")).strip()[:limit]


def normalize_company(name: str) -> str:
    """Display-quality company name from noisy page strings ('Jobs at Monzo Bank Ltd | Careers')."""
    text = unicodedata.normalize("NFKC", clean(name, 400)).strip()
    text = re.sub(r"^(?:jobs?|careers?|work|working|life)\s+(?:at|with|@)\s+", "", text, flags=re.I)
    for _ in range(3):
        before = text
        text = PAGE_NOISE.sub("", text).strip(" ,-|–—·")
        text = LEGAL_SUFFIX.sub("", text).strip(" ,-|–—·")
        text = re.sub(r"\s+(?:&|and)$", "", text, flags=re.I).strip(" ,-|–—·")
        if text == before:
            break
    return re.sub(r"\s+", " ", text)[:200]


LATIN_LETTERS = str.maketrans({"ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "Æ": "AE",
                               "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "ı": "i"})


def company_key(name: str) -> str:
    """Stable alphanumeric key of a display name: ascii when the name has Latin letters ('Café Müller' -> 'cafemuller',
    'Søren' -> 'soren'), else the letters of its own script ('字节跳动', 'Яндекс', 'شركة التقنية'): a company written
    in another script has a key too, or it could never be identified (and a page's host would be taken for its name)."""
    text = unicodedata.normalize("NFKD", normalize_company(name).translate(LATIN_LETTERS))
    ascii_key = re.sub(r"[^a-z0-9]+", "", text.encode("ascii", "ignore").decode().lower())
    if ascii_key:
        return ascii_key
    return re.sub(r"[\W_]+", "", "".join(c for c in text if not unicodedata.combining(c)).casefold())


def tail_key(name: str) -> str:
    """Cache/dedupe key: legal suffix and generic trailing words dropped ('Monzo Bank Ltd' -> 'monzo')."""
    tokens = normalize_company(name).split()
    while len(tokens) > 1 and tokens[-1].casefold() in GENERIC_TAIL:
        tokens.pop()
    return company_key(" ".join(tokens))


def humanise_slug(slug: str) -> str:
    words = re.split(r"[-_.]+", slug or "")
    return " ".join(w.capitalize() if not w.isupper() else w for w in words if w)


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", clean(title, 600)).strip()
    text = re.sub(r"\s*\((?:m\s*/\s*[fwd]\s*/\s*[dfwx]|[mfwd]\s*/\s*[mfwd](?:\s*/\s*[mfwdx])?|remote|hybrid|on-?site|"
                  r"[^)]{0,25}\bremote\b[^)]*)\)", "", text, flags=re.I)
    text = re.sub(r"\s*[\[(]?\b(?:req(?:uisition)?|job|ref)\s*(?:id|no\.?|#)?\s*[:#]?\s*[A-Z0-9-]{4,}\b[\])]?", "", text,
                  flags=re.I)
    text = re.sub(r"\s+[-–—|]\s+(?:[A-Z][a-z]+(?:,?\s[A-Z][a-z]+){0,2})$", "", text)  # trailing ' - London'
    return re.sub(r"\s+", " ", text).strip(" -–—|,")[:300]


def title_key(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#.]+", normalize_title(title).casefold()))


def titles_match(a: str, b: str) -> bool:
    """Same role: token Jaccard >= 0.8 or one title contains the other (after normalisation)."""
    left, right = set(title_key(a).split()), set(title_key(b).split())
    if not left or not right:
        return False
    if left <= right or right <= left:
        return min(len(left), len(right)) >= 2 or left == right
    return len(left & right) / len(left | right) >= 0.8


def label_matches_company(label: str, company: str) -> bool:
    """Does a domain label ('monzo', 'jpmorganchase', 'citi') plausibly belong to this company?"""
    label = re.sub(r"[^a-z0-9]", "", (label or "").lower())
    key = company_key(company)
    tokens = [re.sub(r"[^a-z0-9]", "", t) for t in
              unicodedata.normalize("NFKD", normalize_company(company)).encode("ascii", "ignore").decode().lower().split()]
    tokens = [t for t in tokens if t]
    if not label or not key:
        return False
    if label == key or (tokens and label == tokens[0] and len(tokens[0]) >= 3):
        return True
    if len(label) >= 4 and (key.startswith(label) or label.startswith(key)):
        return True
    return SequenceMatcher(None, label, key).ratio() >= 0.85


# Where a candidate website came from. A site the PAGE names (its JSON-LD, its links, its own host) is the page's claim about
# itself: usable for this page's answers once it checks out, never remembered for the company. A site Meridian found
# (its records, the curated map, the user's pin, a search, a guess built from the name) is independent of the page.
TRUSTED_ORIGINS = frozenset({"user_pin", "companies_table", "official_careers_map", "jobs_table"})
PAGE_ASSERTED = frozenset({"jsonld", "page_host", "page_link"})
CACHEABLE_ORIGINS = TRUSTED_ORIGINS | {"web_search", "domain_guess"}


def _without_address(text: str, host: str) -> str:
    """The page's own address removed from a piece of text: ``nova.com - domain for sale`` names nobody."""
    domain = registrable_domain(host)
    for address in {a for a in (host, domain, "www." + domain) if a}:
        text = re.sub(re.escape(address), " ", text, flags=re.I)
    return text


def page_names_company(signals: dict, company: str, host: str) -> bool:
    """Does the page ITSELF (its title, site name, main heading or organisation name) name the company? Whole words only,
    and never by way of the page's own domain name: a parked page called ``nova.com`` does not name Nova."""
    wanted = {key for key in (company_key(company), tail_key(company)) if key}
    meta = signals.get("meta") or {}
    fields = [signals.get("title"), meta.get("og:site_name"), meta.get("og:title"), meta.get("application-name"),
              signals.get("h1"), *[o.get("name") for o in (signals.get("orgs") or [])[:10]]]
    for text in fields:
        tokens = re.findall(r"[^\W_]+", _without_address(str(text or ""), host), re.UNICODE)[:60]
        for size in range(1, 5):
            for start in range(len(tokens) - size + 1):
                if company_key(" ".join(tokens[start:start + size])) in wanted:
                    return True
    return False


# --------------------------------------------------------------------------------------------- ATS hints
APPLY_SUFFIX = {"apply", "applymanually", "autofillwithresume", "usemylastapplication", "application", "apply-now"}
LOCALE = re.compile(r"[a-z]{2}-[A-Za-z]{2}")


def ats_hint(url: str) -> dict:
    """Company/slug/job identity revealed by an ATS-hosted URL. Pure; no network.

    Returns ``{ats, slug, tenant, job_id, board_url, company_guess, name_api, job_api, posting_url}``. Unknown hosts
    return ``{'ats': 'generic', ...}``. Aggregators return ``{'ats': <name>, 'aggregator': True}``.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    query = dict(parse_qsl(parts.query))
    out = {"ats": "generic", "slug": None, "tenant": None, "job_id": None, "board_url": None,
           "company_guess": None, "name_api": None, "job_api": None, "posting_url": None}

    def done(ats, slug, **kw):
        out.update(ats=ats, slug=slug, company_guess=humanise_slug(slug) if slug else None, **kw)
        return out

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io",
                "job-boards.eu.greenhouse.io"}:
        slug = query.get("for") or (segs[0] if segs and segs[0] != "embed" else None)
        job = (segs[segs.index("jobs") + 1] if "jobs" in segs and len(segs) > segs.index("jobs") + 1
               else query.get("token") or query.get("gh_jid"))
        api = f"https://boards-api.{'eu.' if '.eu.' in host else ''}greenhouse.io/v1/boards/{slug}" if slug else None
        job = job if job and str(job).isdigit() else None
        return done("greenhouse", slug, job_id=job, board_url=f"https://{host}/{slug}" if slug else None, name_api=api,
                    job_api=f"{api}/jobs/{job}" if api and job else None)
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and segs:
        api = f"https://api.{'eu.' if '.eu.' in host else ''}lever.co/v0/postings/{segs[0]}"
        job = segs[1] if len(segs) > 1 and re.fullmatch(r"[0-9a-f-]{36}", segs[1]) else None
        return done("lever", segs[0], job_id=job, board_url=f"https://{host}/{segs[0]}",
                    job_api=f"{api}/{job}" if job else None,
                    posting_url=f"https://{host}/{segs[0]}/{job}" if job else None)
    if host == "jobs.ashbyhq.com" and segs:
        job = segs[1] if len(segs) > 1 and re.fullmatch(r"[0-9a-f-]{36}", segs[1]) else None
        return done("ashby", segs[0], job_id=job, board_url=f"https://jobs.ashbyhq.com/{segs[0]}",
                    job_api=f"https://api.ashbyhq.com/posting-api/job-board/{segs[0]}?includeCompensation=true",
                    posting_url=f"https://jobs.ashbyhq.com/{segs[0]}/{job}" if job else None)
    if host.endswith(".myworkdayjobs.com"):
        tenant = host.split(".")[0]
        rest = segs[1:] if segs and LOCALE.fullmatch(segs[0]) else segs
        site = rest[0] if rest and rest[0] != "job" else None
        while rest and rest[-1].casefold() in APPLY_SUFFIX:
            rest = rest[:-1]
        req = re.search(r"_([A-Za-z0-9-]+)$", rest[-1]) if rest else None
        posting = None
        if "job" in rest and site:
            locale = segs[0] if segs and LOCALE.fullmatch(segs[0]) else None
            posting = f"https://{host}/{locale + '/' if locale else ''}" + "/".join(rest)
        return done("workday", tenant, tenant=tenant, job_id=req.group(1) if req else None,
                    board_url=f"https://{host}/{site}" if site else None, posting_url=posting,
                    job_api=(f"https://{host}/wday/cxs/{tenant}/{site}/" + "/".join(rest[rest.index("job"):])
                             if site and "job" in rest else None))
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and segs:
        job = re.match(r"(\d{6,})", segs[1]).group(1) if len(segs) > 1 and re.match(r"\d{6,}", segs[1]) else None
        return done("smartrecruiters", segs[0], job_id=job, board_url=f"https://jobs.smartrecruiters.com/{segs[0]}",
                    name_api=f"https://api.smartrecruiters.com/v1/companies/{segs[0]}",
                    job_api=f"https://api.smartrecruiters.com/v1/companies/{segs[0]}/postings/{job}" if job else None)
    if host == "apply.workable.com" and segs and segs[0] not in {"j", "api"}:
        job = segs[segs.index("j") + 1] if "j" in segs and len(segs) > segs.index("j") + 1 else None
        return done("workable", segs[0], job_id=job, board_url=f"https://apply.workable.com/{segs[0]}",
                    name_api=f"https://apply.workable.com/api/v1/widget/accounts/{segs[0]}",
                    job_api=f"https://apply.workable.com/api/v2/accounts/{segs[0]}/jobs/{job}" if job else None)
    if (host.endswith((".teamtailor.com", ".recruitee.com", ".bamboohr.com", ".pinpointhq.com", ".jobs.personio.de",
                       ".jobs.personio.com"))):
        label = host.split(".")[0]
        ats = "personio" if ".jobs.personio." in host else host.split(".")[1]
        job = None
        for pattern in (r"/jobs/(\d+)", r"/o/([^/]+)", r"/careers/(\d+)", r"/postings/([0-9a-f-]{36})", r"/job/(\d+)"):
            hit = re.search(pattern, parts.path)
            if hit:
                job = hit.group(1)
                break
        return done(ats, label, board_url=f"https://{host}/", job_id=job)
    if host.endswith(".icims.com"):
        label = re.sub(r"^careers?-", "", host.split(".")[0])
        job = re.search(r"/jobs/(\d+)", parts.path)
        return done("icims", label, board_url=f"https://{host}/jobs/search", job_id=job.group(1) if job else None)
    if host.endswith(".taleo.net"):
        return done("taleo", host.split(".")[0], job_id=query.get("job") or query.get("job_id"))
    if host == "ats.rippling.com" and segs:
        return done("rippling", segs[0], job_id=segs[2] if len(segs) > 2 and segs[1] == "jobs" else None)
    if host == "join.com" and len(segs) > 1 and segs[0] == "companies":
        return done("join", segs[1])
    if host.endswith(".oraclecloud.com") or ("sites" in segs and "job" in segs and re.search(r"career|jobs", host)):
        site = segs[segs.index("sites") + 1] if "sites" in segs and len(segs) > segs.index("sites") + 1 else None
        job = segs[segs.index("job") + 1] if "job" in segs and len(segs) > segs.index("job") + 1 else None
        return done("oraclehcm", host.split(".")[0], tenant=site, job_id=job)
    if host.endswith(".eightfold.ai"):
        return done("eightfold", host.split(".")[0],
                    job_id=query.get("pid") or (segs[-1] if segs and segs[-1].isdigit() else None))
    if host.endswith((".successfactors.com", ".successfactors.eu")):
        return done("successfactors", query.get("company"))
    if registrable_domain(host) in AGGREGATORS:
        out.update(ats=registrable_domain(host).split(".")[0], aggregator=True)
        return out
    # gh_jid on a company's own domain => a Greenhouse board embedded on that site (slug unknown)
    if query.get("gh_jid"):
        out.update(ats="greenhouse-embed", job_id=query["gh_jid"])
    return out


def posting_url_for(url: str) -> str:
    """The posting itself: apply/application suffixes and fragments removed, the (job-identifying) query kept."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    while segs and segs[-1].casefold() in APPLY_SUFFIX:
        segs.pop()
    return urlunsplit((parts.scheme, parts.netloc, "/" + "/".join(segs), parts.query, ""))


def strip_apply_suffix(url: str) -> str:
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    while segs and segs[-1].casefold() in APPLY_SUFFIX:
        segs.pop()
    return urlunsplit((parts.scheme, parts.netloc, "/" + "/".join(segs), "", ""))


# --------------------------------------------------------------------------------------------- page parsing
def _types(node) -> list[str]:
    value = node.get("@type", [])
    return [value] if isinstance(value, str) else [x for x in value if isinstance(x, str)]


def _urls(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [x for v in value for x in _urls(v)]
    if isinstance(value, dict):
        return _urls(value.get("@id") or value.get("url") or "")
    return []


def _visit_jsonld(value, nodes: list, depth: int = 0) -> None:
    if depth > 8 or len(nodes) >= MAX_JSONLD_NODES:
        return
    if isinstance(value, list):
        for item in value[:200]:
            _visit_jsonld(item, nodes, depth + 1)
    elif isinstance(value, dict):
        nodes.append(value)
        for key in ("@graph", "itemListElement", "item", "mainEntity", "hiringOrganization", "publisher"):
            if key in value:
                _visit_jsonld(value[key], nodes, depth + 1)


def jsonld_nodes(texts) -> list[dict]:
    nodes: list[dict] = []
    for raw in texts:
        if not isinstance(raw, str) or len(raw) > 600_000:
            continue
        try:
            _visit_jsonld(json.loads(raw), nodes)
        except (ValueError, TypeError, RecursionError):
            continue
    return nodes


def _plain_name(value) -> str:
    """A schema.org value that may be a string, a thing with a name or a list of them -> one short string."""
    if isinstance(value, list):
        return ", ".join(x for x in (_plain_name(v) for v in value[:4]) if x)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def signals_from_nodes(nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    jobs, orgs = [], []
    for node in nodes:
        types = _types(node)
        if "JobPosting" in types:
            org = node.get("hiringOrganization")
            org = org[0] if isinstance(org, list) and org else org
            org = org if isinstance(org, dict) else ({"name": str(org)} if org else {})
            place = jsonld_place(node)
            jobs.append({"title": clean(node.get("title"), 300), "description": str(node.get("description") or ""),
                         "organization": {"name": clean(org.get("name") or org.get("legalName"), 200),
                                          "url": (_urls(org.get("url")) or [""])[0],
                                          "same_as": _urls(org.get("sameAs"))[:8]},
                         "url": str(node.get("url") or ""),
                         "employment_type": clean(_plain_name(node.get("employmentType")), 80) or None,
                         "department": clean(_plain_name(node.get("occupationalCategory") or node.get("department")),
                                             120) or None,
                         "location": clean(place["location"], 200), "country": place["country"],
                         "countries": place["countries"], "remote": place["remote"],
                         "valid_through": clean(node.get("validThrough"), 40) or None})
        if {"Organization", "Corporation", "LocalBusiness", "NGO", "EducationalOrganization", "OnlineBusiness"} & set(types) \
                or any(t.endswith("Organization") for t in types):
            orgs.append({"name": clean(node.get("name") or node.get("legalName"), 200),
                         "url": (_urls(node.get("url")) or [""])[0], "same_as": _urls(node.get("sameAs"))[:8],
                         "description": clean(node.get("description"), 600)})
        if "WebSite" in types:
            orgs.append({"name": clean(node.get("name"), 200), "url": (_urls(node.get("url")) or [""])[0],
                         "same_as": [], "description": ""})
    return jobs, orgs


def parse_page_signals(html: str, page_url: str) -> dict:
    """Everything the research needs from one HTML page. Never executes script. A document past the tag or nesting budget
    is read up to that point (a hostile page must not cost seconds of CPU: see ``markup``)."""
    return signals_from_soup(BeautifulSoup(bounded_html(html), "html.parser"), page_url)


def signals_from_soup(soup, page_url: str) -> dict:
    meta = {}
    wanted = {"og:site_name", "og:title", "og:description", "og:url", "og:type", "description", "twitter:site",
              "application-name"}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in wanted:
            meta[key] = clean(tag.get("content"), 500)
    canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    scripts = [s.string or s.get_text() for s in soup.find_all("script", type=re.compile(r"ld\+json", re.I))]
    jobs, orgs = signals_from_nodes(jsonld_nodes(scripts))
    links = []
    for anchor in soup.select("a[href]")[:1500]:
        try:
            href = urljoin(page_url, anchor["href"].strip())
            if urlsplit(href).scheme not in ("http", "https"):
                continue
        except ValueError:  # a malformed address ('http://[::1') is one bad link, not the end of the research
            continue
        links.append({"href": href, "text": clean(anchor.get_text(" ", strip=True), 120)})
    h1 = soup.find("h1")
    return {"title": clean(soup.title.get_text(" ", strip=True), 300) if soup.title else "",
            "h1": clean(h1.get_text(" ", strip=True), 300) if h1 else "", "meta": meta,
            "canonical": canonical.get("href") if canonical else None,
            "lang": soup.html.get("lang") if soup.html else None, "jobs": jobs, "orgs": orgs, "links": links}


# What a page that could not be read in time says: nothing (the run goes on with the sources it has).
EMPTY_SIGNALS = {"title": "", "h1": "", "meta": {}, "canonical": None, "lang": None, "jobs": [], "orgs": [], "links": []}

SECTION_PATTERNS = [  # (kind, regex on 'path + anchor text', weight)
    ("company_about", re.compile(
        r"\b(?:about(?:[- ]us)?|who[- ]we[- ]are|our[- ](?:story|company)|company|what[- ]we[- ]do)\b", re.I), 10),
    ("company_mission", re.compile(r"\b(?:mission|purpose|vision|principles|manifesto)\b", re.I), 9),
    ("company_values", re.compile(
        r"\b(?:values|culture|how[- ]we[- ]work|ways? of working|life[- ]at|our[- ]people|diversity|inclusion|"
        r"belonging)\b", re.I), 8),
    ("company_engineering", re.compile(
        r"\b(?:engineering|technology|tech|data[- ]science|product|research)\b", re.I), 6),
    ("company_careers", re.compile(
        r"\b(?:careers?|jobs|join[- ](?:us|the[- ]team)|work[- ](?:with|at)[- ]us|early[- ]careers?|graduates?|"
        r"students?|internships?|apprentice\w*)\b", re.I), 7),
    ("company_blog", re.compile(r"\b(?:blog|news(?:room)?|press|stories|insights)\b", re.I), 4),
]
BAD_PATH = re.compile(
    r"privacy|cookie|terms|legal|sign[-_]?in|log[-_]?in|register|account|cart|checkout|/cdn-cgi/|"
    r"\.(?:pdf|png|jpe?g|svg|gif|zip|js|css|xml|ico|mp4)$|/(?:apply|application)(?:/|$)|/jobs?/[^/]+/[^/]+", re.I)
BAD_TEXT = re.compile(r"privacy|cookie|terms|sign[- ]?in|log[- ]?in|apply(?: now)?$", re.I)
EARLY_CAREER = re.compile(r"\b(?:intern(?:ship)?|graduate|placement|apprentice\w*|new grad|early[- ]career|trainee)\b", re.I)
ENGINEERING_TITLE = re.compile(r"\b(?:engineer|developer|software|data|machine learning|devops|sre|security|architect)\b",
                               re.I)
FIXED_PATHS = ("/about", "/about-us", "/company", "/mission", "/values", "/culture", "/careers", "/engineering")


def rank_company_links(links, *, base_domain: str, limit=6, already=(), boost: re.Pattern | None = None) -> list[dict]:
    """Same-site informational pages. Query strings are dropped: fixed, GET-safe URLs only."""
    seen = {urlsplit(u)._replace(query="", fragment="").geturl().rstrip("/") for u in already}
    scored: dict[str, tuple] = {}
    for link in links:
        try:
            parts = urlsplit(link["href"])
            host = (parts.hostname or "").lower()
        except ValueError:  # a malformed address ('http://[::1') is one bad link, not the end of the research
            continue
        if parts.scheme not in ("http", "https") or registrable_domain(host) != base_domain or is_ats_host(host):
            continue
        clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/") \
            or f"{parts.scheme}://{parts.netloc}"
        if (clean_url in seen or BAD_PATH.search(parts.path) or BAD_TEXT.search(link.get("text", ""))
                or parts.path.count("/") > 4):
            continue
        haystack = parts.path.replace("-", " ").replace("_", " ") + " " + link.get("text", "")
        for kind, pattern, weight in SECTION_PATTERNS:
            if pattern.search(haystack):
                score = weight + (3 if parts.path.count("/") <= 1 else 0) + (2 if pattern.search(parts.path) else 0)
                if boost is not None and kind in {"company_careers", "company_engineering"} and boost.search(haystack):
                    score += 4
                if clean_url not in scored or score > scored[clean_url][0]:
                    scored[clean_url] = (score, kind, link.get("text") or parts.path)
                break
    ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
    out, kinds = [], {}
    for url, (score, kind, text) in ranked:  # at most 2 pages per kind so 'about' does not crowd out 'values'
        if kinds.get(kind, 0) >= 2:
            continue
        kinds[kind] = kinds.get(kind, 0) + 1
        out.append({"url": url, "kind": kind, "score": score, "anchor": text})
        if len(out) >= limit:
            break
    return out


BOILERPLATE = re.compile(
    r"^(?:accept(?: all)?(?: cookies)?|reject all|cookie (?:settings|preferences|policy)|manage (?:cookies|preferences)|"
    r"subscribe|sign up for|follow us|all rights reserved|©|skip to (?:main )?content|back to top|share this|"
    r"read more|learn more|menu|close)\b", re.I)


def extract_page_text(html: str, limit=PAGE_LIMIT) -> str:
    """Heading-preserving visible text ('## ' prefixed headings); nav, footers, forms and cookie banners dropped."""
    return text_from_soup(BeautifulSoup(bounded_html(html), "html.parser"), limit)


def read_page(html: str, page_url: str, limit=PAGE_LIMIT) -> dict:
    """The signals AND the readable text of one page from ONE parse (this is what the isolated worker runs: a document is
    never parsed twice). The text is read after the signals because reading it removes nav, forms and scripts."""
    soup = BeautifulSoup(bounded_html(html), "html.parser")
    signals = signals_from_soup(soup, page_url)
    return {"signals": signals, "text": text_from_soup(soup, limit)}


def text_from_soup(soup, limit=PAGE_LIMIT) -> str:
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe", "form", "nav", "footer", "aside",
                     "dialog"]):
        tag.decompose()
    for tag in soup.select('[aria-hidden="true"],[hidden],[role="navigation"],[role="dialog"],.cookie,.cookies,'
                           '#cookie-banner,[id*="cookie"],[class*="cookie-"]'):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup
    lines, seen = [], set()
    for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "dd", "figcaption", "summary"]):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if not text or text in seen or BOILERPLATE.match(text):
            continue
        heading = node.name in {"h1", "h2", "h3", "h4"}
        if not heading and len(text) < 25:
            continue
        if (not heading and node.name in {"li", "p"} and node.find_parent(["li", "p", "blockquote"]) is not None
                and node.find_parent(node.name) is not None):
            continue  # nested duplicates
        seen.add(text)
        lines.append(("## " + text) if heading else text)
    out = "\n".join(lines)
    if len(out) < 80:  # script-rendered shell: fall back to everything visible
        out = re.sub(r"\s+", " ", root.get_text(" ", strip=True))
        if len(out) < 80:
            from ..automation.company_research import published_page_text
            out = published_page_text(soup) or out
    return out[:limit]


def sanitize_untrusted_text(text: str) -> tuple[str, int]:
    """Remove control characters and lines that read like instructions to a model. Returns (text, lines removed).

    This is defence in depth: employer text is also labelled untrusted data in every model prompt."""
    kept, removed = [], 0
    for line in CONTROL.sub("", str(text or "")).splitlines():
        if looks_like_instructions(line):
            removed += 1
            continue
        kept.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip(), removed


def robots_allows(robots_txt: str | None, url: str, user_agent=ROBOTS_AGENT) -> bool:
    if robots_txt is None:
        return True
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(user_agent, url)


# --------------------------------------------------------------------------------------------- report
def web_address(value, limit: int) -> str:
    """An address from a page: control characters and the tabs/newlines a browser itself ignores inside an address are
    removed (``ht<TAB>tps://`` is ``https://`` to every parser), so the stored form is the one that was parsed."""
    return re.sub(r"[\t\n\r]", "", clean(value, limit))


def normalize_report(raw: dict) -> dict:
    """Bounded, sanitised page report. Raises ValueError for an unusable URL. Never contains form values."""
    url = web_address(raw.get("url"), 4000)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("A public http(s) page URL without credentials is required")
    # Nothing that looks like a secret is kept (SEC-10): ``url_clean`` is what is stored, shown and sent to a model.
    url_clean = scrub_url(private_checkpoint({"url": url})["url"])
    posting = posting_url_for(url_clean)
    try:
        job_key = ats_identity(posting)[1] or canonical_url(posting)
    except ValueError as exc:
        raise ValueError("The page URL is not usable") from exc
    meta = {clean(k, 40): clean(v, 500) for k, v in list((raw.get("meta") or {}).items())[:30] if k}
    user = {k: clean(v, 400) or None for k, v in (raw.get("user") or {}).items() if k in {"company", "title", "site"}}
    links = []
    for link in (raw.get("links") or [])[:80]:
        href = web_address(link.get("href") if isinstance(link, dict) else getattr(link, "href", ""), 2000)
        text = clean(link.get("text") if isinstance(link, dict) else getattr(link, "text", ""), 200)
        try:
            usable = urlsplit(href).scheme in ("http", "https")
        except ValueError:  # one malformed link ('http://[::1') must not make the whole page unusable
            continue
        if usable:
            links.append({"href": href, "text": text})
    return {"url": url, "url_clean": url_clean, "host": (parts.hostname or "").lower(), "posting_url": posting,
            "job_key": job_key, "ats": ats_hint(url_clean), "title": clean(raw.get("title"), 400),
            "h1": clean(raw.get("h1"), 400), "lang": clean(raw.get("lang"), 16),
            "company_hint": clean(raw.get("company_hint"), 200) or None, "meta": meta,
            "location_hint": clean(raw.get("location_hint"), 200) or None,
            "canonical": web_address(raw.get("canonical"), 4000) or None,
            "jsonld": [clean(t, 200_000) for t in (raw.get("jsonld") or [])[:8] if isinstance(t, str)],
            "description_text": clean(raw.get("description_text"), 24000), "links": links, "user": user}


# --------------------------------------------------------------------------------------------- public fetch
class FetchError(Exception):
    """kind: blocked_destination | http_error | too_large | timeout | network | redirects | not_text | invalid_url
    | budget | robots | disabled"""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


@dataclass(frozen=True)
class FetchedPage:
    url: str  # requested
    final_url: str  # after redirects (hostnames, never the pinned IP)
    status: int
    content_type: str
    body: bytes
    elapsed_ms: int = 0
    redirects: int = 0

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


async def default_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _loopback_for_tests() -> bool:
    """End-to-end tests serve a fake company site on loopback. Honoured ONLY under MERIDIAN_TEST."""
    return bool(os.environ.get("MERIDIAN_TEST") and os.environ.get("MERIDIAN_EXTENSION_RESEARCH_LOOPBACK"))


STANDARD_PORTS = {"http": 80, "https": 443}


async def fetch_public(url: str, *, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", max_body=1_500_000,
                       timeout=REQUEST_TIMEOUT, max_redirects=5, accept_language="en,*;q=0.5",
                       resolver=default_resolver, public_check=is_public_ip, verify=True,
                       text_only=True, allow_any_port=False) -> FetchedPage:
    """GET one public page. Every hop resolves its host, requires ALL addresses to be public, connects to the
    resolved IP with the original Host header and SNI (no DNS rebinding window) and re-validates redirects.
    Bounded in time (wall clock), size (decompressed bytes) and redirects. Never sends cookies or credentials.

    Every hop, a redirect included, must be ``http`` on port 80 or ``https`` on port 443 (``allow_any_port`` is for the
    tests' own local servers): a page that names ``http://host:6379/`` or an employer site that redirects there must not
    turn Meridian into a probe of arbitrary ports of the public Internet."""
    started, current = time.monotonic(), url
    headers_base = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": accept_language}
    limits = httpx.Timeout(timeout, connect=min(timeout, 4.0))
    try:
        async with httpx.AsyncClient(timeout=limits, follow_redirects=False, trust_env=False, verify=verify,
                                     headers=headers_base) as client:
            async with asyncio.timeout(timeout * 2):  # wall-clock cap; httpx timeouts are per phase
                for hop in range(max_redirects + 1):
                    parts = urlsplit(current)
                    host = (parts.hostname or "").lower().rstrip(".")
                    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
                        raise FetchError("invalid_url", "A public http(s) URL without credentials is required")
                    try:
                        host = host.encode("idna").decode("ascii")  # a host in another script is looked up (and named) as punycode
                        port = parts.port or STANDARD_PORTS[parts.scheme]
                    except (UnicodeError, ValueError):
                        raise FetchError("invalid_url", "The address is not valid") from None
                    if not allow_any_port and port != STANDARD_PORTS[parts.scheme]:
                        raise FetchError("blocked_destination", "Only the standard web ports are allowed")
                    if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
                        raise FetchError("blocked_destination", "Local destinations are not allowed")
                    try:
                        ips = await resolver(host, port)
                    except socket.gaierror:
                        raise FetchError("network", "DNS lookup failed") from None
                    if not ips or any(not public_check(ip) for ip in ips):  # ALL addresses must be public
                        raise FetchError("blocked_destination", "Only public Internet destinations are allowed")
                    ip = ips[0]
                    pinned = f"[{ip}]" if ":" in ip else ip
                    target = urlunsplit((parts.scheme, f"{pinned}:{port}", parts.path or "/", parts.query, ""))
                    host_header = host if port in (80, 443) else f"{host}:{port}"
                    async with client.stream("GET", target, headers={"Host": host_header},
                                             extensions={"sni_hostname": host}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError("http_error", "Redirect without destination", response.status_code)
                            current = urljoin(current, location)
                            continue
                        if response.status_code >= 400:
                            raise FetchError("http_error", f"HTTP {response.status_code}", response.status_code)
                        content_type = response.headers.get("content-type", "")
                        if text_only and not any(t in content_type.lower() for t in ("html", "xml", "json", "text/plain")):
                            raise FetchError("not_text", f"Unsupported content type {content_type[:60]!r}")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():  # post-decompression size limit
                            body.extend(chunk)
                            if len(body) > max_body:
                                raise FetchError("too_large", f"Response exceeds {max_body} bytes")
                        return FetchedPage(url, current, response.status_code, content_type, bytes(body),
                                           round((time.monotonic() - started) * 1000), hop)
                raise FetchError("redirects", "Too many redirects")
    except FetchError:
        raise
    except TimeoutError:
        raise FetchError("timeout", "Timed out") from None
    except httpx.TimeoutException:
        raise FetchError("timeout", "Timed out") from None
    except httpx.HTTPError as exc:
        raise FetchError("network", type(exc).__name__) from None
    except (ValueError, UnicodeError):  # e.g. a redirect to 'http://[::1': a refused address, never a crash
        raise FetchError("invalid_url", "The address is not valid") from None


async def default_fetcher(url: str, **kwargs) -> FetchedPage:
    """The production fetcher. Tests inject their own; under MERIDIAN_TEST nothing reaches the Internet."""
    if os.environ.get("MERIDIAN_TEST") and not _loopback_for_tests():
        raise FetchError("disabled", "Network research is disabled in test mode")
    if _loopback_for_tests():
        kwargs.setdefault("public_check", lambda ip: is_public_ip(ip) or ipaddress.ip_address(ip).is_loopback)
        kwargs.setdefault("allow_any_port", True)  # the end-to-end tests serve their fake company site on a random port
    return await fetch_public(url, **kwargs)


class SearchUnavailable(Exception):
    pass


async def search_official_site(company: str, *, fetcher=None) -> list[dict]:
    """Last-resort public lookup. ONLY the company name leaves the machine, as ``<name> official website``.

    Parses the no-JavaScript DuckDuckGo result page (``a.result__a`` with the target in ``uddg``). Returns candidate
    ``{url, title, rank}``; the caller still verifies every candidate before using it."""
    fetcher = fetcher or default_fetcher
    query = f"{normalize_company(company)} official website"
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query) + "&kl=wt-wt"
    try:
        page = await fetcher(url, accept="text/html", max_body=400_000, timeout=6.0)
    except FetchError as exc:
        raise SearchUnavailable(exc.kind) from exc
    soup = BeautifulSoup(bounded_html(page.text()), "html.parser")
    results = []
    for rank, anchor in enumerate(soup.select("a.result__a")[:10]):
        href = anchor.get("href", "")
        target = parse_qs(urlsplit(href).query).get("uddg", [href])[0]
        if urlsplit(target).scheme in ("http", "https"):
            results.append({"url": target, "title": clean(anchor.get_text(" ", strip=True), 200), "rank": rank})
    if not results:
        raise SearchUnavailable("no_results")
    return results


# --------------------------------------------------------------------------------------------- cache
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: float | None = None) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc) if ts else _now()).isoformat()


class ResearchCache:
    """Public research data on disk. Atomic writes, TTL on read, pruned lazily. Candidate data never goes here."""

    def __init__(self, root: Path, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.pruned_at: float | None = None

    def _path(self, kind: str, name: str) -> Path:
        digest = name if re.fullmatch(r"[a-z0-9_.-]{1,80}", name) else hashlib.sha256(name.encode()).hexdigest()[:32]
        return self.root / kind / (digest + ".json")

    def get(self, kind: str, name: str, ttl: float, *, max_age: float | None = None) -> tuple[dict, bool] | None:
        """(payload, stale). ``stale`` entries are older than ``ttl`` but younger than ``max_age``."""
        path = self._path(kind, name)
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return None
        age = self.clock() - float(data.get("stored_at", 0))
        if age < 0 or age > (max_age if max_age is not None else ttl):
            return None
        return data.get("payload", {}), age > ttl

    def put(self, kind: str, name: str, payload: dict) -> None:
        path = self._path(kind, name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
            tmp.write_text(json.dumps({"schema": SCHEMA, "stored_at": self.clock(), "payload": payload},
                                      ensure_ascii=False), "utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # a read-only or full disk only costs a refetch

    def due(self) -> bool:
        """Has the cache directory not been tidied for ``PRUNE_INTERVAL`` (or ever)?"""
        return self.pruned_at is None or self.clock() - self.pruned_at >= PRUNE_INTERVAL

    def prune(self, force: bool = False) -> None:
        """Drop files older than 30 days and cap the tree at ~2000 files (oldest first), when :meth:`due` (or ``force``).
        Walks the whole tree: callers on the event loop run it in a worker thread."""
        if not force and not self.due():
            return
        self.pruned_at = self.clock()
        if not self.root.exists():
            return
        files = []
        for path in self.root.rglob("*.json"):
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        files.sort(key=lambda item: item[0])
        horizon = self.clock() - MAX_AGE
        for index, (mtime, path) in enumerate(files):
            if mtime < horizon or len(files) - index > CACHE_FILE_CAP:
                with contextlib.suppress(OSError):
                    path.unlink()
        for tmp in self.root.rglob("*.tmp"):
            with contextlib.suppress(OSError):
                if self.clock() - tmp.stat().st_mtime > 3600:
                    tmp.unlink()


# --------------------------------------------------------------------------------------------- job description
SECTION_HEADINGS = {
    "responsibilities": re.compile(r"responsibilit|what you(?:'|’)?ll do|what you will do|the role|your role|about the job|"
                                   r"day[- ]to[- ]day|duties|key tasks", re.I),
    "requirements": re.compile(r"requirement|qualification|what you(?:'|’)?ll need|what we(?:'|’)?re looking for|"
                               r"you(?:'|’)?ll bring|you have|skills|experience", re.I),
    "nice_to_have": re.compile(r"nice to have|bonus|preferred|plus|desirable", re.I),
    "benefits": re.compile(r"benefit|perks|what we offer|compensation|salary|package", re.I),
    "about_company": re.compile(r"about (?:us|the company|the team)|who we are|our (?:mission|story)|company", re.I),
}


def split_sections(text: str, limit=1200) -> dict[str, str]:
    """Up to six job-description sections keyed by kind; for the research panel only, never a model summary."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        heading = line.lstrip("# ").strip() if line.startswith("##") else (
            line.strip().rstrip(":") if line.strip().endswith(":") and len(line.strip()) < 60 else None)
        if heading:
            current = next((key for key, pattern in SECTION_HEADINGS.items() if pattern.search(heading)), None)
            continue
        if current and line.strip():
            sections.setdefault(current, []).append(line.strip())
    return {key: "\n".join(lines)[:limit] for key, lines in list(sections.items())[:6] if lines}


PLACE_KEYS = ("location", "country", "countries", "remote", "employment_type", "department")
FACT_ORDER = {"JSONLD": 0, "ATS_API": 1, "FETCHED_POSTING": 2, "DB": 3, "PAGE": 4}


def role_facts(state: str, source: dict) -> dict:
    """What one source says about the job itself (where it is, what kind of job): ``{state, location, country,
    countries, remote, employment_type, department}``. A source that already read the place (JSON-LD, an ATS API)
    brings its country along; a plain location string (a stored job row, an old cache entry) is read here."""
    location = clean(source.get("location") or "", 200)
    if looks_like_instructions(location):  # employer text is data: instructions in a "location" make the whole place unreadable
        return {"state": state, "location": "", "country": "", "countries": [], "remote": False,
                "employment_type": source.get("employment_type") or None, "department": source.get("department") or None}
    if "country" in source or "countries" in source:
        country = str(source.get("country") or "")
        countries = [str(c) for c in (source.get("countries") or ([country] if country else []))][:6]
        remote = bool(source.get("remote"))
    else:
        place = text_place(location)
        location, country, countries, remote = place["location"], place["country"], place["countries"], place["remote"]
    return {"state": state, "location": location, "country": country, "countries": countries, "remote": remote,
            "employment_type": source.get("employment_type") or None, "department": source.get("department") or None}


def merge_role_facts(facts: list[dict]) -> dict:
    """One answer to 'where is the job and what kind is it' from every source, in order of authority (the posting's
    own structured data, the ATS record, the stored job, then text on the page). The place is taken from ONE source
    as a unit (location, country and remoteness together: a country is never mixed in from a source that describes
    another place); employment type and department come from the first source that has them. Independent of which
    description text the answers are based on."""
    ordered = sorted(facts, key=lambda f: FACT_ORDER.get(f["state"], 9))
    place = next((f for f in ordered if f["location"] or f["country"]), None) or {}
    return {"location": place.get("location", ""), "country": place.get("country", ""),
            "countries": list(place.get("countries", [])), "remote": bool(place.get("remote")),
            "place_source": place.get("state"),
            "employment_type": next((f["employment_type"] for f in ordered if f["employment_type"]), None),
            "department": next((f["department"] for f in ordered if f["department"]), None)}


def page_place(report: dict) -> dict | None:
    """The place the visible page itself states, for pages without usable structured data: an explicit location hint
    of the page report, a 'Location: ...' line of the description, a title that names a city AND its country.
    Employer text is data: instruction-like text never becomes a location."""
    for text in (clean(report.get("location_hint"), 200), labelled_location(report.get("description_text"))):
        if text and not looks_like_instructions(text):
            facts = role_facts("PAGE", text_place(text))
            if facts["location"]:
                return facts
    place = title_place(report.get("h1"), report.get("title"), (report.get("meta") or {}).get("og:title"))
    return role_facts("PAGE", place) if place["location"] else None


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _kind_for_state(state: str) -> str:
    return {"JSONLD": "jsonld_job_posting", "PAGE": "observed_employer_description", "DB": "stored_job_description",
            "ATS_API": "public_job_description", "FETCHED_POSTING": "public_job_description"}.get(
        state, "public_job_description")


def parse_ats_job(ats: str, data, job_id: str | None) -> dict | None:
    """Tolerant single-posting parsers for the public ATS APIs. Shapes follow each vendor's published API; they are
    exercised on fixtures only (never against a live employer in tests)."""
    if not isinstance(data, dict):
        return None
    title = text = location = department = employment = country_hint = ""
    remote = False
    if ats == "greenhouse":
        title, text = data.get("title") or "", html_to_text(data.get("content"))
        location = (data.get("location") or {}).get("name", "") if isinstance(data.get("location"), dict) else ""
        departments = data.get("departments")
        department = departments[0].get("name", "") if isinstance(departments, list) and departments else ""
    elif ats == "lever":
        title = data.get("text") or ""
        parts = [data.get("descriptionPlain") or html_to_text(data.get("description"))]
        for block in data.get("lists") or []:
            if isinstance(block, dict):
                parts.append("## " + str(block.get("text") or "") + "\n" + html_to_text(block.get("content")))
        parts.append(data.get("additionalPlain") or html_to_text(data.get("additional")))
        text = "\n\n".join(p for p in parts if p)
        categories = data.get("categories") if isinstance(data.get("categories"), dict) else {}
        location, department, employment = (categories.get("location", ""), categories.get("team", ""),
                                            categories.get("commitment", ""))
        country_hint, remote = str(data.get("country") or ""), str(data.get("workplaceType") or "").lower() == "remote"
    elif ats == "smartrecruiters":
        title = data.get("name") or ""
        sections = ((data.get("jobAd") or {}).get("sections") or {}) if isinstance(data.get("jobAd"), dict) else {}
        order = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
        text = "\n\n".join(("## " + str(sections[k].get("title") or k) + "\n" + html_to_text(sections[k].get("text")))
                           for k in order if isinstance(sections.get(k), dict) and sections[k].get("text"))
        loc = data.get("location") if isinstance(data.get("location"), dict) else {}
        location = ", ".join(str(loc.get(k)) for k in ("city", "country") if loc.get(k))
        country_hint, remote = str(loc.get("country") or ""), bool(loc.get("remote"))
        department = (data.get("department") or {}).get("label", "") if isinstance(data.get("department"), dict) else ""
        employment = (data.get("typeOfEmployment") or {}).get("label", "") if isinstance(
            data.get("typeOfEmployment"), dict) else ""
    elif ats == "workable":
        title = data.get("title") or ""
        text = "\n\n".join(html_to_text(data.get(k)) for k in ("description", "requirements", "benefits") if data.get(k))
        loc = data.get("location") if isinstance(data.get("location"), dict) else {}
        location = ", ".join(str(loc.get(k)) for k in ("city", "country") if loc.get(k))
        country_hint = str(loc.get("countryCode") or loc.get("country") or "")
        remote = str(data.get("workplace") or "").lower() == "remote" or bool(data.get("telecommuting"))
        department = ", ".join(data.get("department") or []) if isinstance(data.get("department"), list) else ""
    elif ats == "ashby":
        jobs = data.get("jobs") if isinstance(data.get("jobs"), list) else []
        job = next((j for j in jobs if isinstance(j, dict) and str(j.get("id")) == str(job_id)), None)
        if not job:
            return None
        title = job.get("title") or ""
        text = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
        location, department = str(job.get("location") or ""), str(job.get("department") or job.get("team") or "")
        employment = str(job.get("employmentType") or "")
        postal = ((job.get("address") or {}).get("postalAddress") or {}) if isinstance(job.get("address"), dict) else {}
        country_hint = str(postal.get("addressCountry") or "") if isinstance(postal, dict) else ""
        remote = bool(job.get("isRemote"))
    elif ats == "workday":
        info = data.get("jobPostingInfo") if isinstance(data.get("jobPostingInfo"), dict) else {}
        title, text = info.get("title") or "", html_to_text(info.get("jobDescription"))
        location, employment = str(info.get("location") or ""), str(info.get("timeType") or "")
        country_hint = str(_plain_name(info.get("country")) or "")
    if not title or len(text) < 80:
        return None
    place = text_place(clean(location, 200))
    stated = country_of(country_hint)  # the vendor's own country field outranks one read from the location text
    country = stated or ("" if country_hint.strip() else place["country"])
    if country and not place["location"]:
        place["location"] = country
    return {"title": clean(title, 300), "text": text, "location": place["location"], "country": country,
            "countries": [country] if country else place["countries"], "remote": remote or place["remote"],
            "department": clean(department, 120) or None, "employment_type": clean(employment, 80) or None}


# --------------------------------------------------------------------------------------------- run state
@dataclass
class Run:
    id: str
    key: tuple[str, str]
    report: dict
    refresh: bool = False
    company_override: str | None = None
    site_override: str | None = None
    background: bool = False  # a cache refresh nobody is waiting for (stale-while-revalidate)
    state: str = "queued"
    version: int = 0
    company: dict | None = None
    site: dict | None = None
    role: dict | None = None
    jd: dict | None = None
    items: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stages: dict = field(default_factory=dict)
    brief: str = ""
    brief_source: str | None = None
    needs: dict | None = None
    unverified: list = field(default_factory=list)
    stale: bool = False
    requests: int = 0
    headline: str = ""
    bundle_hash: str = ""
    subscribers: dict = field(default_factory=dict)  # session id -> listener | None (ephemeral)
    task: asyncio.Task | None = None
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_handle: object = None
    notify_handle: object = None
    hard_handle: object = None
    blocked_hosts: set = field(default_factory=set)
    unparsable_hosts: set = field(default_factory=set)  # hosts one of whose pages could not be read safely (see _read_page)
    robots: dict = field(default_factory=dict)
    homepage: dict | None = None
    started: float = field(default_factory=time.monotonic)
    updated_at: str = field(default_factory=_iso)
    stage_started: dict = field(default_factory=dict)
    last_notified: int = -1
    cached_pages: list = field(default_factory=list)
    hard_cancelled: bool = False
    skip_note: str = ""  # why company research was not done on purpose (an aggregator's page): shown as the headline


STAGE_LABELS = {"identify": "Identify the company and role", "role": "Read the job description",
                "site": "Find the official website", "pages": "Read company pages"}


class ResearchManager:
    """Owner of every research run. All state is in memory; public data is cached on disk. See the module docstring."""

    def __init__(self, data_dir: Path, db=None, *, fetcher=None, search=None, settings_getter=None, max_concurrent=None,
                 web_lookup: bool | None = None, clock=time.time, pin_writer=None):
        self.data_dir, self.db = Path(data_dir), db
        self.cache = ResearchCache(self.data_dir / "cache" / "extension-research", clock)
        self._fetcher = fetcher
        self._search = search
        self._settings_getter = settings_getter
        self._pin_writer = pin_writer
        self._max_concurrent = max_concurrent
        self._slots: asyncio.Semaphore | None = None
        self._web_lookup = (not os.environ.get("MERIDIAN_TEST")) if web_lookup is None else web_lookup
        self._clock = clock
        self._runs: OrderedDict[str, Run] = OrderedDict()
        self._by_key: dict[tuple[str, str], str] = {}
        self._by_session: dict[str, set[str]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._search_lock = asyncio.Lock()
        self._last_search = 0.0
        self._search_blocked_until = 0.0
        self._boards: dict[str, tuple[float, dict]] = {}
        quiet_http_logs()

    # ------------------------------------------------------------------------------- settings / helpers
    def _settings(self) -> dict:
        values = {"research_enabled": True, "web_lookup": True, "concurrency": 2, "pins": {}}
        if self._settings_getter:
            with contextlib.suppress(Exception):
                values.update(self._settings_getter() or {})
        return values

    def _slot(self) -> asyncio.Semaphore:
        if self._slots is None:
            wanted = self._max_concurrent or int(self._settings().get("concurrency") or 2)
            self._slots = asyncio.Semaphore(max(1, min(4, wanted)))
        return self._slots

    async def _fetch(self, url: str, **kwargs) -> FetchedPage:
        return await (self._fetcher or default_fetcher)(url, **kwargs)

    def _tidy_cache(self) -> None:
        """Keep the cache directory bounded in a process that runs for days: walked in a worker thread, at most once
        per ``PRUNE_INTERVAL`` (never twice at the same time)."""
        if not self.cache.due():
            return
        self.cache.pruned_at = self.cache.clock()  # claimed now: a second page starting meanwhile does not queue another walk
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(self._prune_cache))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _prune_cache(self) -> None:
        with contextlib.suppress(Exception):
            self.cache.prune(force=True)

    # ------------------------------------------------------------------------------- public API
    def start(self, session_id: str, report: dict, *, refresh=False, company_override: str | None = None,
              site_override: str | None = None, listener=None) -> dict:
        """Attach a session to the run for its page (creating it when needed) and return the current snapshot.

        Never blocks and never raises for a research problem (a malformed URL raises ValueError). ``listener`` is
        called with a fresh snapshot on every visible change (debounced)."""
        if self._closed:
            raise RuntimeError("The research manager is closed")
        asyncio.get_running_loop()  # runs are tasks: a loop is required
        normalized = report if "url_clean" in report else normalize_report(report)
        if company_override:
            normalized = {**normalized, "user": {**normalized["user"], "company": clean(company_override, 200)}}
        if site_override:
            normalized = {**normalized, "user": {**normalized["user"], "site": clean(site_override, 300)}}
        self._tidy_cache()
        company = self._identify(normalized)
        key = (tail_key(company["name"]) if company else "", normalized["job_key"])
        run = self._runs.get(self._by_key.get(key, ""))
        if run and run.state not in TERMINAL and not refresh and not site_override:
            self._subscribe(run, session_id, listener)
            return self.snapshot(run.id)
        run = Run(id="rs_" + uuid.uuid4().hex[:20], key=key, report=normalized, refresh=refresh,
                  company_override=company_override, site_override=site_override, company=company)
        run.stages = OrderedDict((sid, {"id": sid, "label": label, "status": "pending", "detail": "", "ms": None})
                                 for sid, label in STAGE_LABELS.items())
        self._set_stage(run, "identify", "done" if company else "failed",
                        f"{company['name']} (from {company['name_source'].replace('_', ' ')})" if company
                        else "The company name could not be read from this page")
        run.headline = f"Researching {company['name']}…" if company else "Which company is this?"
        self._runs[run.id] = run
        self._by_key[key] = run.id
        self._subscribe(run, session_id, listener)
        self._trim()
        run.task = asyncio.get_running_loop().create_task(self._execute(run), name=f"research:{run.id}")
        self._tasks.add(run.task)
        run.task.add_done_callback(self._tasks.discard)
        return self.snapshot(run.id)

    def snapshot(self, run_id: str) -> dict:
        run = self._runs[run_id]
        role = None
        if run.role:
            role = {k: run.role.get(k) for k in ("title", "job_id", "posting_url", "location", "country", "countries",
                                                 "remote", "employment_type", "department", "skills",
                                                 "description_chars", "description_state", "sections")}
        return {
            "run_id": run.id, "state": run.state, "version": run.version,
            "ready_for_answers": run.state in {"done", "partial", "failed"}, "quality": self._quality(run),
            "stale": run.stale, "headline": run.headline, "needs": run.needs,
            "company": ({k: run.company.get(k) for k in ("name", "legal_name", "key", "company_id", "name_source",
                                                         "confidence")} if run.company else None),
            "site": ({k: run.site.get(k) for k in ("url", "domain", "origin", "verified", "evidence")}
                     if run.site else None),
            "role": role, "brief": run.brief, "brief_source": run.brief_source,
            "stages": [dict(stage) for stage in run.stages.values()],
            "sources": [dict(source) for source in run.sources], "notes": list(run.notes[:12]),
            "updated_at": run.updated_at,
        }

    def exists(self, run_id: str | None) -> bool:
        return bool(run_id) and run_id in self._runs

    async def wait(self, run_id: str, timeout: float) -> dict:
        """Wait until the run is settled (done, partial, failed, cancelled or waiting for the user), at most
        ``timeout`` seconds, and return the snapshot. Never raises for a timeout."""
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(run.settled.wait(), max(0.0, timeout))
        return self.snapshot(run_id)

    def job_context(self, run_id: str | None, base: dict | None = None) -> dict:
        """The research as the ``job`` dict ``ApplicationAnswerLLM`` consumes: description + JD-only
        ``context_sources`` + verified-site ``company_context`` (<= 8 pages). Safe to call while the run is still
        working: it returns what has been read so far (and ``research.state`` says so)."""
        context = dict(base or {})
        run = self._runs.get(run_id or "")
        if run is None:
            return context
        report, role = run.report, run.role or {}
        context.update({
            "title": context.get("title") or role.get("title") or report["h1"] or report["title"],
            "company": context.get("company") or (run.company or {}).get("name", ""),
            "company_id": (run.company or {}).get("company_id") or context.get("company_id") or "",
            "location": context.get("location") or role.get("location") or "",
            "country": context.get("country") or role.get("country") or "",
            "countries": list(context.get("countries") or role.get("countries") or []),
            "url": role.get("posting_url") or report["posting_url"], "application_url": report["url_clean"],
            "ats": (report["ats"].get("ats") or "generic"),
            "description": (run.jd or {}).get("text", context.get("description", "")),
            "skills": list(role.get("skills") or context.get("skills") or []),
            "context_sources": ([{"kind": run.jd["kind"], "url": run.jd["url"], "sha256": run.jd["sha256"],
                                  "observed_at": run.jd["observed_at"], "trusted_as_instructions": False}]
                                if run.jd else []),
            "company_context": [dict(item) for item in self._ordered_items(run)],
            "company_research_errors": [{"url": e["url"], "error_type": e["status"], "reason": e["error"] or ""}
                                        for e in run.sources if e["status"] in {"failed", "blocked"}][:6],
            "research": {"run_id": run.id, "bundle_hash": self._bundle_hash(run), "state": run.state,
                         "version": run.version},
        })
        return context

    def bundle_hash(self, run_id: str | None) -> str:
        run = self._runs.get(run_id or "")
        return self._bundle_hash(run) if run else ""

    def settled(self, run_id: str | None) -> bool:
        """Has the run finished reading (done, partial, failed or cancelled)? Until then its bundle is still growing."""
        run = self._runs.get(run_id or "")
        return bool(run and run.state in TERMINAL)

    def set_site(self, run_id: str, domain: str, *, session_id: str | None = None, listener=None) -> dict:
        """The user pins the official website. Validated, then the run restarts as a NEW run (the previous bundle
        stays frozen for answers already drafted). The pin is remembered for the company (``extension_company_sites``)
        only once the new run has actually read that website: a typo or an unreachable address is never kept."""
        run = self._runs[run_id]
        return self._restart(run, session_id, listener, site_override=self.validate_site(domain))

    @staticmethod
    def validate_company(company: str) -> str:
        """The user's correction of the company name -> its display name, or ValueError."""
        name = normalize_company(company)
        if not name or company_key(name) in ATS_BRANDS or len(name) < 2:
            raise ValueError("Enter the company name")
        return name

    def set_company(self, run_id: str, company: str, *, session_id: str | None = None, listener=None) -> dict:
        """'This is not the company': research again with the company the user typed."""
        return self._restart(self._runs[run_id], session_id, listener, company_override=self.validate_company(company))

    def refresh(self, run_id: str, *, session_id: str | None = None, listener=None) -> dict:
        return self._restart(self._runs[run_id], session_id, listener, refresh=True)

    def release(self, session_id: str, run_id: str | None = None) -> None:
        """The session is gone. Runs nobody else needs are cancelled after a 20 s grace (a reload re-attaches)."""
        ids = {run_id} if run_id else set(self._by_session.get(session_id, ()))
        for rid in ids:
            run = self._runs.get(rid or "")
            if run is None:
                continue
            run.subscribers.pop(session_id, None)
            self._by_session.get(session_id, set()).discard(rid)
            if not run.subscribers and not run.background and run.state not in SETTLED and run.task and not run.task.done():
                with contextlib.suppress(RuntimeError):
                    run.cancel_handle = asyncio.get_running_loop().call_later(CANCEL_GRACE, self._cancel_orphan, run)
        if not self._by_session.get(session_id):
            self._by_session.pop(session_id, None)

    def subscribe(self, run_id: str, session_id: str, listener) -> None:
        run = self._runs.get(run_id)
        if run:
            self._subscribe(run, session_id, listener)

    async def close(self) -> None:
        self._closed = True
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for run in self._runs.values():
            for handle in (run.cancel_handle, run.notify_handle, run.hard_handle):
                if handle:
                    handle.cancel()

    # ------------------------------------------------------------------------------- run plumbing
    def _restart(self, old: Run, session_id, listener, *, refresh=False, company_override=None, site_override=None):
        """Start a NEW run for the same page (the old bundle stays frozen for drafts already written)."""
        sid = session_id or next(iter(old.subscribers), "internal")
        report = {**old.report, "user": dict(old.report["user"])}
        if company_override:
            report["user"].pop("site", None)  # a website the user gave for the company they just rejected says nothing here
        snapshot = self.start(sid, report, refresh=True, company_override=company_override,
                              site_override=site_override, listener=listener)
        if session_id and snapshot["run_id"] != old.id:
            self.release(session_id, old.id)
        return snapshot

    def _subscribe(self, run: Run, session_id: str, listener) -> None:
        run.subscribers[session_id] = listener
        self._by_session.setdefault(session_id, set()).add(run.id)
        if run.cancel_handle:
            run.cancel_handle.cancel()
            run.cancel_handle = None

    def _hard_stop(self, run: Run) -> None:
        run.hard_cancelled = True
        if run.task and not run.task.done():
            run.task.cancel()

    def _cancel_orphan(self, run: Run) -> None:
        run.cancel_handle = None
        if not run.subscribers and run.task and not run.task.done() and run.state not in SETTLED:
            run.task.cancel()

    def _trim(self) -> None:
        """Keep at most ``KEEP_RUNS`` runs: the oldest settled ones nobody follows go first. A run that waits for the
        user (``needs_input``) is settled too: a page whose company cannot be read must not stay in memory (with its
        whole page report) for as long as the process lives."""
        for rid in [r for r, run in self._runs.items() if run.state in SETTLED and not run.subscribers]:
            if len(self._runs) <= KEEP_RUNS:
                break
            run = self._runs.pop(rid, None)
            if run is not None and self._by_key.get(run.key) == rid:
                self._by_key.pop(run.key, None)

    def _set_stage(self, run: Run, stage_id: str, status: str, detail: str = "") -> None:
        stage = run.stages[stage_id]
        if status == "running":
            run.stage_started[stage_id] = time.monotonic()
        elif stage["ms"] is None and status != "pending":
            started = run.stage_started.get(stage_id)
            stage["ms"] = round((time.monotonic() - started) * 1000) if started else 0
        stage.update(status=status, detail=detail[:200])
        self._touch(run)

    def _touch(self, run: Run) -> None:
        run.version += 1
        run.updated_at = _iso()
        if not any(run.subscribers.values()):
            return
        if run.state in SETTLED:
            self._flush(run)
            return
        if run.notify_handle is None:
            try:
                run.notify_handle = asyncio.get_running_loop().call_later(0.25, self._flush, run)
            except RuntimeError:
                self._flush(run)

    def _flush(self, run: Run) -> None:
        if run.notify_handle:
            run.notify_handle.cancel()
            run.notify_handle = None
        if run.version == run.last_notified:
            return
        run.last_notified = run.version
        for listener in list(run.subscribers.values()):
            if listener:
                with contextlib.suppress(Exception):
                    listener(self.snapshot(run.id))

    @staticmethod
    def _ordered_items(run: Run) -> list[dict]:
        order = {"ats_company": 0, "company_home": 1, "company_about": 2, "company_mission": 3, "company_values": 4,
                 "company_engineering": 5, "company_careers": 6, "company_blog": 7}
        return sorted(run.items, key=lambda i: (order.get(i["kind"], 9), i["url"]))[:MAX_CONTEXT_ITEMS]

    def _bundle_hash(self, run: Run) -> str:
        parts = sorted(i["sha256"] for i in run.items) + [(run.jd or {}).get("sha256", ""),
                                                          (run.site or {}).get("domain", "")]
        return sha("|".join(parts))

    @staticmethod
    def _quality(run: Run) -> str:
        job = bool(run.jd and len(run.jd["text"]) >= 80)
        company = bool(run.items)
        return "full" if job and company else "job_only" if job else "company_only" if company else "minimal"

    # ------------------------------------------------------------------------------- execution
    async def _execute(self, run: Run) -> None:
        loop = asyncio.get_running_loop()
        try:
            async with self._slot():
                run.state = "running"
                run.started = time.monotonic()
                self._touch(run)
                run.hard_handle = loop.call_later(HARD_DEADLINE, self._hard_stop, run)
                try:
                    async with asyncio.timeout(SOFT_DEADLINE):
                        await self._pipeline(run)
                except TimeoutError:
                    run.notes.append("Research took too long; used what was already read")
                    for stage in run.stages.values():
                        if stage["status"] in {"running", "pending"}:
                            stage.update(status="failed", detail="Timed out; used what was already read")
                except asyncio.CancelledError:
                    if not run.hard_cancelled:
                        raise
                    asyncio.current_task().uncancel()
                    run.notes.append("Research took too long; used what was already read")
                    for stage in run.stages.values():
                        if stage["status"] in {"running", "pending"}:
                            stage.update(status="failed", detail="Timed out; used what was already read")
                except Exception as exc:  # a pipeline bug must never reach the page or a field
                    run.notes.append(f"Research stopped unexpectedly ({type(exc).__name__})")
                    for stage in run.stages.values():
                        if stage["status"] in {"running", "pending"}:
                            stage.update(status="failed", detail="Stopped unexpectedly")
                try:
                    self._finish(run)
                except Exception as exc:
                    run.notes.append(f"Research could not be saved ({type(exc).__name__})")
                    run.state = "partial" if (run.jd or run.items) else "failed"
        except asyncio.CancelledError:
            run.state = "cancelled"
            run.headline = "Research stopped"
            if self._by_key.get(run.key) == run.id:
                self._by_key.pop(run.key, None)
            raise
        finally:
            for handle in (run.hard_handle, run.cancel_handle):
                if handle:
                    handle.cancel()
            run.hard_handle = run.cancel_handle = None
            run.version += 1
            run.updated_at = _iso()
            run.settled.set()
            # Ephemeral subscribers (no listener, e.g. 'open with assistant') never leave on their own.
            for sid in [s for s, cb in run.subscribers.items() if cb is None]:
                run.subscribers.pop(sid, None)
                bucket = self._by_session.get(sid)
                if bucket is not None:
                    bucket.discard(run.id)
                    if not bucket:
                        self._by_session.pop(sid, None)  # no empty entry per 'open with assistant' job, for ever
            self._flush(run)
            if self._by_key.get(run.key) == run.id and run.state in TERMINAL:
                self._by_key.pop(run.key, None)

    async def _guard(self, run: Run, stage_id: str, function) -> None:
        self._set_stage(run, stage_id, "running")
        try:
            status, detail = await function(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status, detail = "failed", f"Stopped unexpectedly ({type(exc).__name__})"
        self._set_stage(run, stage_id, status, detail)

    async def _pipeline(self, run: Run) -> None:
        settings = self._settings()
        if run.company is None:
            await self._guard(run, "role", self._role)
            run.needs = {"field": "company", "suggestions": self._company_suggestions(run.report)}
            for stage_id in ("site", "pages"):
                self._set_stage(run, stage_id, "skipped", "Waiting for the company name")
            run.state = "needs_input"
            run.headline = "Which company is this?"
            return
        if not settings.get("research_enabled", True):
            await self._guard(run, "role", self._role)
            for stage_id in ("site", "pages"):
                self._set_stage(run, stage_id, "skipped", "Company research is switched off")
            run.notes.append("Company research is switched off in Meridian")
            return
        aggregator = run.report["ats"].get("aggregator") and not run.report["user"].get("site")
        if aggregator:
            # LinkedIn, Indeed and the like are never read or searched from here: the job text the page itself carries
            # is used, the company is not researched, and the panel says so (a site the user pins still works).
            name = {"linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor"}.get(
                str(run.report["ats"].get("ats")), str(run.report["ats"].get("ats") or "this site").title())
            await self._guard(run, "role", self._role)
            self._set_stage(run, "site", "skipped", f"Company research is skipped on {name}")
            self._set_stage(run, "pages", "skipped", f"Company research is skipped on {name}")
            run.skip_note = f"Company research is skipped on {name}: using the job text on the page only"
            run.notes.append(f"{name} pages are never read or searched. Add the company's website to research it")
            return
        role_task = asyncio.get_running_loop().create_task(self._guard(run, "role", self._role))
        try:
            await self._guard(run, "site", self._site)
            if run.site and run.site.get("verified"):
                await self._guard(run, "pages", self._pages)
            else:
                self._set_stage(run, "pages", "skipped", "No verified company website")
            await role_task
        finally:
            if not role_task.done():
                role_task.cancel()
            await asyncio.gather(role_task, return_exceptions=True)

    def _finish(self, run: Run) -> None:
        if run.state == "needs_input":
            self._summarise(run)
            return
        statuses = [s["status"] for s in run.stages.values()]
        found = bool(run.jd or run.items)
        if all(s in {"done", "skipped"} for s in statuses) and "done" in statuses:
            run.state = "done"
        else:
            run.state = "partial" if found else "failed"
        self._summarise(run)
        if run.state in {"done", "partial"}:
            self._persist(run)
        if run.background:
            return

    def _summarise(self, run: Run) -> None:
        name = (run.company or {}).get("name", "this company")
        pages = [i for i in run.items if i["kind"] != "ats_company"]
        failed = [s for s in run.sources if s["status"] in {"failed", "blocked"}]
        if run.state == "needs_input":
            run.headline = "Which company is this?"
        elif run.items and run.site:
            run.headline = f"Researched {run.site['domain']}: {len(pages) or len(run.items)} page" \
                f"{'' if len(pages) == 1 else 's'} read" + (f", {len(failed)} unavailable" if failed else "")
        elif run.skip_note:
            run.headline = run.skip_note
        elif run.jd and not run.items:
            run.headline = f"Using the job description only for {name}"
        elif run.items:
            run.headline = f"Used the {name} board profile"
        else:
            run.headline = f"Could not research {name}"
        if run.stages["site"]["status"] == "failed" and not (run.site and run.site.get("verified")):
            candidates = [u["domain"] for u in run.unverified][:3]
            run.needs = run.needs or {"field": "site", "suggestions": candidates}
        run.version += 1

    # ------------------------------------------------------------------------------- stage: identify
    def _identify(self, report: dict) -> dict | None:
        candidates: list[tuple[float, str, str, str | None]] = []

        def add(confidence: float, source: str, name, legal=None):
            name = normalize_company(clean(name, 200))
            key = company_key(name)
            if (2 <= len(name) <= 80 and key and key not in ATS_BRANDS and not re.search(r"https?:|www\.", name, re.I)
                    and not re.fullmatch(r"[\d\W]+", name)):
                candidates.append((confidence, source, name, legal or None))

        user = report["user"]
        if user.get("company"):
            add(1.0, "user", user["company"])
        jobs, _ = signals_from_nodes(jsonld_nodes(report["jsonld"]))
        for job in jobs[:3]:
            if job["organization"]["name"]:
                add(0.95, "jsonld", job["organization"]["name"], job["organization"]["name"])
        site_name = report["meta"].get("og:site_name", "")
        if site_name and company_key(site_name) not in ATS_BRANDS:
            add(0.8, "og_site_name", site_name)
        if report.get("company_hint"):
            add(0.75, "page_hint", report["company_hint"])
        for text in (report["meta"].get("og:title", ""), report["title"], report["h1"]):
            if not text:
                continue
            for pattern in (r"^(?:jobs?|careers?)\s+(?:at|with)\s+(.+)$", r"\bat\s+([A-Z][^|\-–—•]{1,60})$",
                            r"[|•\-–—]\s*([^|•\-–—]{2,60}?)\s*(?:[-|–—]\s*)?(?:careers?|jobs?)?\s*$",
                            r"^(.+?)\s+careers?$"):
                match = re.search(pattern, text.strip(), re.I if pattern.startswith("^") else 0)
                if match:
                    add(0.7, "title", match.group(1))
                    break
        hint = report["ats"]
        if hint.get("company_guess") and not hint.get("aggregator"):
            add(0.6, "ats_slug", hint["company_guess"])
        host = report["host"]
        if hint.get("ats") in {"generic", "greenhouse-embed"} and not is_ats_host(host) and not is_non_official(host) \
                and not _is_ip(host):
            label = registrable_domain(host).split(".")[0]
            if not re.search(r"job|career|recruit|talent|hiring|vacanc|apply|work|staff|employ", label):
                add(0.5, "host", humanise_slug(label))
        if not candidates:
            return None
        confidence, source, name, legal = max(candidates, key=lambda c: c[0])
        if confidence < 0.5:
            return None
        row = self._company_row(name)
        return {"name": name, "legal_name": legal if legal and legal != name else None, "key": tail_key(name),
                "company_id": row["id"] if row else None, "name_source": source, "confidence": confidence,
                "careers_url": (row or {}).get("careers_url") or ""}

    def _company_suggestions(self, report: dict) -> list[str]:
        out: list[str] = []
        for text in (report["meta"].get("og:site_name", ""), report["title"], report["h1"]):
            for part in re.split(r"\s+[|•\-–—]\s+", text or ""):
                name = normalize_company(part)
                if 2 <= len(name) <= 60 and company_key(name) not in ATS_BRANDS and name not in out:
                    out.append(name)
        return out[:5]

    def _company_row(self, name: str) -> dict | None:
        if self.db is None:
            return None
        try:
            for candidate in dict.fromkeys([name, normalize_company(name)]):
                row = self.db.one(
                    "SELECT c.* FROM companies c WHERE c.name=? COLLATE NOCASE UNION "
                    "SELECT c.* FROM company_aliases a JOIN companies c ON c.id=a.company_id "
                    "WHERE a.alias=? COLLATE NOCASE LIMIT 1", (candidate, candidate))
                if row:
                    return row
            wanted = tail_key(name)
            for row in self.db.query("SELECT id,name,careers_url FROM companies"):
                if tail_key(row["name"]) == wanted:
                    return row
        except Exception:
            return None
        return None

    def job_row(self, report: dict) -> dict | None:
        """The existing job row of this posting (read only), or None."""
        return self._job_row(report)

    def _job_row(self, report: dict) -> dict | None:
        """An existing job row for this posting (read only): canonical URL, ATS identity, application URL, alias."""
        if self.db is None:
            return None
        try:
            for url in dict.fromkeys([report["url_clean"], report["posting_url"]]):
                canonical = canonical_url(url)
                ident = ats_identity(url)[1]
                row = self.db.one("SELECT * FROM jobs WHERE canonical_url=? OR identity_key=? OR application_url=?",
                                  (canonical, ident or canonical, url))
                if row:
                    return row
                row = self.db.one("SELECT j.* FROM job_duplicates d JOIN jobs j ON j.id=d.job_id "
                                  "WHERE d.duplicate_url=?", (canonical,))
                if row:
                    return row
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------------------- reading pages
    async def _read_page(self, run: Run, html: str, page_url: str, limit: int = PAGE_LIMIT) -> dict:
        """The signals and the readable text of a fetched page, from ONE parse in a killable worker (``markup``).

        A page that cannot be read safely (a parse bomb, or one past the CPU or wall limit) is skipped, and so is every
        later page of the same host in this run: a hostile site costs the run at most one failed parse, never one per
        page it links to. The run goes on with the sources it has."""
        empty = {"signals": EMPTY_SIGNALS, "text": ""}
        host = (urlsplit(page_url).hostname or "").lower()
        if host in run.unparsable_hosts:
            return empty
        read = await parse_isolated("page", html, page_url, limit, default=None)
        if not isinstance(read, dict) or "signals" not in read:
            run.unparsable_hosts.add(host)
            run.notes.append(f"A page of {host} could not be read safely and was skipped")
            return empty
        return read

    # ------------------------------------------------------------------------------- network helpers
    async def _get(self, run: Run, url: str, *, label: str, kind: str, origin: str, robots=True, guessed=False,
                   record=True, accept=None, max_body=1_500_000) -> FetchedPage:
        """One bounded, polite GET. Budgeted, robots-aware, negative-cached; records a source row."""
        host = (urlsplit(url).hostname or "").lower()
        source = {"label": label[:80], "url": url, "kind": kind, "origin": origin, "status": "ok", "chars": 0,
                  "retrieved_at": None, "cached": False, "error": None}

        def fail(status: str, message: str, error: FetchError):
            source.update(status=status, error=message)
            if record:
                run.sources.append(source)
                self._touch(run)
            raise error

        if host in run.blocked_hosts:
            fail("blocked", "This site asked not to be read automatically", FetchError("robots", "blocked host"))
        negative = self.cache.get("failures", url, TTL_FAILURE)
        if negative and not run.refresh:
            fail("failed", "Unavailable a moment ago; not retrying yet", FetchError(
                negative[0].get("kind", "network"), "recently failed", negative[0].get("status")))
        if robots and not await self._robots_ok(run, url, guessed=guessed):
            fail("blocked", "This site's robots.txt does not allow reading it", FetchError("robots", "disallowed"))
        if run.requests >= MAX_REQUESTS:
            fail("skipped", "Request budget for this page was used up", FetchError("budget", "budget"))
        run.requests += 1
        kwargs = {"max_body": max_body}
        if accept:
            kwargs["accept"] = accept
        try:
            page = await self._fetch(url, **kwargs)
        except FetchError as exc:
            if exc.kind in {"network", "timeout"} or (exc.status or 0) >= 500:
                self.cache.put("failures", url, {"kind": exc.kind, "status": exc.status})
            if exc.status in {403, 429} or exc.kind == "robots":
                run.blocked_hosts.add(host)
                fail("blocked", "This site did not allow it", exc)
            message = {"http_error": f"The site answered HTTP {exc.status}", "timeout": "The site did not answer in time",
                       "network": "The site could not be reached", "blocked_destination": "Not a public website",
                       "too_large": "The page is too large", "not_text": "Not a readable page",
                       "redirects": "Too many redirects", "disabled": "Network research is off"}.get(exc.kind, "Unavailable")
            fail("failed", message, exc)
        source.update(retrieved_at=_iso(), chars=len(page.body))
        if page.final_url != scrub_url(page.final_url):  # a redirect may add ``?utm_*=``/``?ref=``: never stored or sent on
            page = dataclasses.replace(page, final_url=scrub_url(page.final_url))
        if re.search(r"<title[^>]*>[^<]*(?:just a moment|access denied|verify you are human|attention required)",
                     page.text()[:6000], re.I):
            run.blocked_hosts.add(host)
            fail("blocked", "The site asked for human verification", FetchError("robots", "challenge"))
        if record:
            run.sources.append(source)
            self._touch(run)
        return page

    async def _robots_ok(self, run: Run, url: str, *, guessed: bool) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        origin = f"{parts.scheme}://{parts.netloc}"
        if host not in run.robots:
            text, available = None, True
            cached = self.cache.get("robots", host, TTL_ROBOTS)
            if cached and not run.refresh:
                text, available = cached[0].get("body"), bool(cached[0].get("available", True))
            elif run.requests < MAX_REQUESTS:
                run.requests += 1
                try:
                    page = await self._fetch(origin + "/robots.txt", accept="text/plain,*/*;q=0.1", max_body=200_000,
                                             text_only=False)
                    text = page.body.decode("utf-8", errors="replace")[:200_000]
                except FetchError as exc:
                    if exc.status and 400 <= exc.status < 500:
                        text = None  # no robots.txt: everything is allowed
                    else:
                        available = False  # unknown: only navigation-found links may be read
                self.cache.put("robots", host, {"body": text, "available": available})
            run.robots[host] = (text, available)
        text, available = run.robots[host]
        if not available:
            return not guessed
        return robots_allows(text, url)

    async def _json(self, run: Run, url: str, *, label: str, kind: str, origin: str):
        page = await self._get(run, url, label=label, kind=kind, origin=origin, robots=False,
                               accept="application/json", max_body=3_000_000)
        try:
            return json.loads(page.text())
        except ValueError as exc:
            raise FetchError("not_text", "Invalid JSON") from exc

    # ------------------------------------------------------------------------------- stage: role
    async def _role(self, run: Run):
        report = run.report
        page_title = report["user"].get("title") or report["h1"] or report["title"]
        candidates: list[dict] = []
        facts: list[dict] = []  # where the role is / what kind of job: carried whichever description text wins

        def candidate(state, text, title, *, trusted, url=None, extra=None):
            text, removed = sanitize_untrusted_text(text)
            if removed:
                run.notes.append(f"{removed} instruction-like line{'s' if removed != 1 else ''} removed from the job text")
            if len(text) >= 80:
                candidates.append({"state": state, "text": text[:JD_LIMIT], "title": title, "trusted": trusted,
                                   "url": url or report["posting_url"], **(extra or {})})

        for job in self._matching_jobs(report)[:MAX_JSONLD_POSTINGS]:
            structured = role_facts("JSONLD", job)
            facts.append(structured)  # even when the description is too short to be a candidate itself
            candidate("JSONLD", await parse_isolated("html_to_text", job["description"], default=""), job["title"], trusted=True,
                      extra={k: structured[k] for k in PLACE_KEYS})
        if len(report["description_text"]) >= 400:
            candidate("PAGE", report["description_text"], page_title, trusted=True)
        row = self._job_row(report)
        facts.extend(self._stored_facts(row, report))
        if row and len(row.get("description") or "") >= 300:
            fresh = False
            with contextlib.suppress(ValueError, TypeError):
                updated = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
                updated = updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)
                fresh = (_now() - updated).days < 7
            if fresh:
                stored_text = str(row["description"]).replace(ASSISTED_NOTE, "")  # provenance note, not employer text
                candidate("DB", await parse_isolated("html_to_text", stored_text, default=""), row["title"], trusted=True,
                          extra={"job_id": row["id"]})
        best = self._pick_jd(candidates, page_title)
        asked_ats = False
        if best is None or len(best["text"]) < 400:
            cached = None if run.refresh else self.cache.get("role", report["job_key"], TTL_ROLE)
            if cached and len(cached[0].get("text", "")) > len((best or {}).get("text", "")) and len(cached[0]["text"]) >= 80:
                candidates.append({**cached[0], "state": cached[0].get("state", "ATS_API"), "trusted": True,
                                   "url": cached[0].get("url") or report["posting_url"], "cached": True})
                facts.append(role_facts(cached[0].get("state", "ATS_API"), cached[0]))
            else:
                for fetch_candidate in (self._jd_from_ats, self._jd_from_posting):
                    found = await fetch_candidate(run, page_title)
                    asked_ats = asked_ats or fetch_candidate == self._jd_from_ats
                    if found:
                        candidates.append(found)
                        facts.append(role_facts(found["state"], found))
                        break
            best = self._pick_jd(candidates, page_title)
        if not any(f["location"] or f["country"] for f in facts) and not asked_ats:
            # Nothing the page or the database says tells where the job is: the ATS's own record usually does.
            found = await self._jd_from_ats(run, page_title, label="Job details")
            if found:
                facts.append(role_facts(found["state"], found))
        meta = merge_role_facts(facts)
        job_id = (row or {}).get("id") if row else None
        if best is None:
            run.role = self._role_record(run, page_title, None, job_id, meta)
            run.sources.append({"label": "Job description", "url": report["posting_url"], "kind": "job_description",
                                "origin": "none", "status": "failed", "chars": 0, "retrieved_at": None, "cached": False,
                                "error": "The job description could not be read"})
            run.notes.append("The job description could not be read; answers use your profile only")
            self._touch(run)
            return "failed", "The job description could not be read"
        text = best["text"]
        run.jd = {"text": text, "kind": _kind_for_state(best["state"]), "url": best["url"], "sha256": sha(text),
                  "observed_at": _iso()}
        run.role = self._role_record(run, page_title, best, job_id or best.get("job_id"), meta)
        run.sources.append({"label": "Job description", "url": best["url"], "kind": "job_description",
                            "origin": best["state"].lower(), "status": "ok", "chars": len(text),
                            "retrieved_at": run.jd["observed_at"], "cached": bool(best.get("cached")), "error": None})
        self._touch(run)
        return "done", f"{len(text)} characters ({best['state'].replace('_', ' ').title()})"

    @staticmethod
    def _matching_jobs(report: dict) -> list[dict]:
        """The ``JobPosting`` nodes of the page that describe THIS posting (a listing page carries many)."""
        page_title = report["user"].get("title") or report["h1"] or report["title"]
        jobs, _ = signals_from_nodes(jsonld_nodes(report["jsonld"]))
        return [job for job in jobs
                if (not page_title or titles_match(page_title, job["title"])
                    or (job["url"] and canonical_url_safe(job["url"]) == canonical_url_safe(report["posting_url"])))
                or len(jobs) == 1]

    @staticmethod
    def _stored_facts(row: dict | None, report: dict) -> list[dict]:
        """Where the job is according to the stored job row and the visible page (the weakest sources)."""
        facts = []
        if row and row.get("location"):
            facts.append(role_facts("DB", {"location": row["location"]}))
        page = page_place(report)
        if page:
            facts.append(page)
        return facts

    def local_role_facts(self, report: dict) -> dict:
        """Where the job is and what kind of job it is, from what the page and the database already say: instant, no
        network. The session uses it for the first suggestions, before the research run has read anything."""
        facts = [role_facts("JSONLD", job) for job in self._matching_jobs(report)]
        facts.extend(self._stored_facts(self._job_row(report), report))
        return merge_role_facts(facts)

    @staticmethod
    def _pick_jd(candidates: list[dict], page_title: str) -> dict | None:
        """Which description text the answers are based on. Only the TEXT: where the job is and what kind of job it is
        come from ``merge_role_facts`` and never depend on which text won."""
        valid = [c for c in candidates
                 if len(c["text"]) >= 80 and (c["trusted"] or not page_title or titles_match(page_title, c["title"]))]
        if not valid:
            return None
        longest = max(valid, key=lambda c: len(c["text"]))
        structured = next((c for c in valid if c["state"] == "JSONLD"), None)
        if structured and len(structured["text"]) >= 0.8 * len(longest["text"]):
            return structured
        return longest

    def _role_record(self, run: Run, page_title: str, best: dict | None, job_id, meta: dict) -> dict:
        from ..matching import extract_skills
        report = run.report
        text = best["text"] if best else ""
        title = report["user"].get("title") or (best or {}).get("title") or report["h1"] or normalize_title(report["title"])
        return {"title": clean(title, 300), "job_id": job_id, "posting_url": report["posting_url"],
                "location": clean(meta["location"], 200), "country": meta["country"], "countries": meta["countries"],
                "remote": meta["remote"], "employment_type": meta["employment_type"], "department": meta["department"],
                "skills": extract_skills(text)[:30] if text else [], "description_chars": len(text),
                "description_state": best["state"] if best else "NOT_FOUND", "sections": split_sections(text)}

    async def _jd_from_ats(self, run: Run, page_title: str, label: str = "Job description") -> dict | None:
        hint = run.report["ats"]
        api, job_id, ats = hint.get("job_api"), hint.get("job_id"), hint.get("ats")
        if not api or not job_id or ats not in {"greenhouse", "lever", "smartrecruiters", "workable", "ashby", "workday"}:
            return None
        try:
            if ats == "ashby":
                cached = self._boards.get(api)
                if cached and self._clock() - cached[0] < TTL_BOARD:
                    data = cached[1]
                else:
                    data = await self._json(run, api, label="Job board", kind="job_description", origin="ats_api")
                    self._remember_board(api, data)
            else:
                data = await self._json(run, api, label=label, kind="job_description", origin="ats_api")
        except FetchError:
            return None
        parsed = await parse_isolated("ats_job", ats, data, job_id, default=None)
        if not parsed:
            return None
        text, removed = sanitize_untrusted_text(parsed["text"])
        if removed:
            run.notes.append(f"{removed} instruction-like line{'s' if removed != 1 else ''} removed from the job text")
        if len(text) < 80:
            return None
        found = {"state": "ATS_API", "text": text[:JD_LIMIT], "title": parsed["title"], "trusted": True,
                 "url": run.report["posting_url"], **{k: parsed[k] for k in PLACE_KEYS}}
        self.cache.put("role", run.report["job_key"], {**found, "text": found["text"]})
        # The board that hosts this very posting also tells us the company's proper name.
        return found

    def _remember_board(self, api: str, data) -> None:
        """A whole job board is up to 3 MB: keep the few most recent, never every board ever opened."""
        moment = self._clock()
        self._boards = {k: v for k, v in self._boards.items() if moment - v[0] < TTL_BOARD and k != api}
        self._boards[api] = (moment, data)
        for old in sorted(self._boards, key=lambda k: self._boards[k][0])[:-MAX_BOARDS]:
            self._boards.pop(old, None)

    async def _jd_from_posting(self, run: Run, page_title: str) -> dict | None:
        report = run.report
        if report["ats"].get("aggregator"):
            return None  # LinkedIn/Indeed/Glassdoor are never fetched
        try:
            page = await self._get(run, report["posting_url"], label="Job posting", kind="job_description",
                                   origin="fetched_posting", record=False)
        except FetchError:
            return None
        read = await self._read_page(run, page.text(), page.final_url, JD_LIMIT)
        signals, text = read["signals"], read["text"]
        for job in signals["jobs"][:MAX_JSONLD_POSTINGS]:
            if titles_match(page_title or signals["h1"] or signals["title"], job["title"]):
                text, _ = sanitize_untrusted_text(await parse_isolated("html_to_text", job["description"], default=""))
                if len(text) >= 80:
                    found = {"state": "FETCHED_POSTING", "text": text[:JD_LIMIT], "title": job["title"],
                             "trusted": True, "url": report["posting_url"], **{k: job[k] for k in PLACE_KEYS}}
                    self.cache.put("role", report["job_key"], found)
                    return found
        title = signals["h1"] or signals["title"]
        text, _ = sanitize_untrusted_text(text)
        if len(text) >= 400 and titles_match(page_title or title, title):
            found = {"state": "FETCHED_POSTING", "text": text, "title": title, "trusted": False,
                     "url": report["posting_url"]}
            self.cache.put("role", report["job_key"], found)
            return found
        return None

    # ------------------------------------------------------------------------------- stage: site
    def validate_site(self, value: str) -> str:
        """A user-supplied website -> its origin URL, or ValueError. Only public, non-ATS, non-aggregator hosts."""
        raw = clean(value, 300).strip()
        if not raw:
            raise ValueError("Enter the company website")
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower().rstrip(".")
        loopback = _loopback_for_tests() and host in {"127.0.0.1", "localhost"}
        if parts.username or parts.password or parts.scheme not in {"http", "https"} or not host:
            raise ValueError("Enter the company website, such as acme.com")
        if not loopback:
            if parts.scheme != "https" and "://" in raw:
                raise ValueError("Use the https address of the company website")
            if (_is_ip(host) or "." not in host or host.endswith(BLOCKED_HOST_SUFFIXES) or host == "localhost"
                    or parts.port not in {None, 443}
                    or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}", host)):
                raise ValueError("Enter the company website, such as acme.com")
            if is_non_official(host):
                raise ValueError("Use the company's own website, not a job board, social network or encyclopedia")
            return f"https://{host}/"
        return f"{parts.scheme}://{parts.netloc}/"

    def _pin(self, key: str, origin: str) -> None:
        if not key:
            return
        pins = dict(self._settings().get("pins") or {})
        pins[key] = {"domain": origin, "pinned_at": _iso()}
        writer = self._pin_writer
        if writer:
            writer(pins)
        elif self.db is not None:
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                            ("extension_company_sites", json.dumps(pins)))

    async def _site(self, run: Run):
        report, company = run.report, run.company
        settings = self._settings()
        key = tail_key(company["name"])
        await self._refine_company(run)
        cached = None if run.refresh else self.cache.get("company", key, TTL_PAGE, max_age=MAX_AGE)
        override = report["user"].get("site")
        if override:
            try:
                override = self.validate_site(override)  # whatever the page report carried is re-validated here
            except ValueError:
                override = None
        if cached and not override:
            site = cached[0].get("site")
            # Only a site Meridian itself found is ever reused for the company: one that a PAGE named (its JSON-LD, a link,
            # its own host) was that page's claim, and an entry written before this rule is ignored too (SEC-04).
            if site and site.get("verified") and site.get("origin") in CACHEABLE_ORIGINS:
                run.site = dict(site)
                run.stale = bool(cached[1])
                run.brief, run.brief_source = cached[0].get("brief", ""), cached[0].get("brief_source")
                run.cached_pages = list(cached[0].get("page_urls", []))
                if cached[1]:
                    run.notes.append("Using saved company research; refreshing it in the background")
                    self._refresh_in_background(run)
                return "done", f"{site['domain']} (saved)"
        pins = settings.get("pins") or {}
        pinned = (pins.get(key) or {}).get("domain") if isinstance(pins.get(key), dict) else None
        candidates: list[tuple[str, str, bool]] = []  # (origin url, origin kind, trusted without identity check)

        def add(url, origin, trusted=False):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            loopback = _loopback_for_tests() and host in {"127.0.0.1", "localhost"}
            if not host or parts.username or parts.password or parts.scheme not in {"http", "https"}:
                return
            if not loopback and (_is_ip(host) or "." not in host or host.endswith(BLOCKED_HOST_SUFFIXES)):
                return  # a page can name any address; only public hostnames are ever candidates
            if not loopback and origin not in TRUSTED_ORIGINS and not is_standard_https(url):
                return  # nor any port or cleartext http: a page cannot make Meridian probe host:6379 (SEC-12)
            own = label_matches_company(registrable_domain(host).split(".")[0], company["name"])
            if is_non_official(host) and not (own and not is_ats_host(host) and registrable_domain(host) not in AGGREGATORS) \
                    and not loopback:
                return
            origin_url = f"{parts.scheme}://{parts.netloc}/"
            if all(origin_url != c[0] for c in candidates):
                candidates.append((origin_url, origin, trusted))

        if override:
            add(override, "user_pin", True)
        elif pinned:
            add(pinned, "user_pin", True)
        jobs, orgs = signals_from_nodes(jsonld_nodes(report["jsonld"]))
        for job in jobs:
            for url in [job["organization"]["url"], *job["organization"]["same_as"]]:
                if url:
                    add(url, "jsonld")
        for org in orgs:
            if label_matches_company(registrable_domain(urlsplit(org["url"]).hostname or "").split(".")[0], company["name"]):
                add(org["url"], "jsonld")
        row = self._company_row(company["name"])
        careers = (row or {}).get("careers_url") or company.get("careers_url") or ""
        if careers and not is_ats_host(urlsplit(careers).hostname or ""):
            add(careers, "companies_table", True)
        for name, url in _official_careers().items():
            if tail_key(name) == key:
                add(url, "official_careers_map", True)
        if self.db is not None:
            with contextlib.suppress(Exception):
                # A job the assistant itself made from a page is that page's claim, not a record of Meridian's own.
                for found in self.db.query("SELECT url FROM jobs WHERE lower(company)=lower(?) AND ats='generic' "
                                           "AND source!='extension' LIMIT 5", (company["name"],)):
                    add(found["url"], "jobs_table", True)
        host = report["host"]
        if (report["ats"].get("ats") in {"generic", "greenhouse-embed"} and not is_ats_host(host)
                and not is_non_official(host)):
            add(f"{urlsplit(report['url_clean']).scheme}://{urlsplit(report['url_clean']).netloc}/", "page_host")
        for link in report["links"]:
            parts = urlsplit(link["href"])
            lhost = (parts.hostname or "").lower()
            if (lhost and lhost != host and not is_non_official(lhost) and not _is_ip(lhost)
                    and (re.search(r"\b(?:home|website|homepage|back to|company site)\b", link["text"], re.I)
                         or label_matches_company(registrable_domain(lhost).split(".")[0], company["name"]))):
                add(f"{parts.scheme}://{parts.netloc}/", "page_link")
        attempts = 0
        chosen = None
        for url, origin, trusted in list(candidates):
            if attempts >= 3 and not trusted:
                break
            attempts += 1
            ok, evidence = await self._try_verify(run, url, origin, trusted)
            if ok:
                chosen = {"url": url, "domain": urlsplit(url).netloc.lower(), "origin": origin, "verified": True,
                          "evidence": evidence}
                break
        if not chosen and len(key) >= 4 and not override:
            for suffix in ("com", "co.uk", "io")[:3]:
                if attempts >= 5:
                    break
                attempts += 1
                url = f"https://{key}.{suffix}/"
                if any(url == c[0] for c in candidates):
                    continue
                ok, evidence = await self._try_verify(run, url, "domain_guess", False)
                if ok:
                    chosen = {"url": url, "domain": f"{key}.{suffix}", "origin": "domain_guess", "verified": True,
                              "evidence": evidence}
                    break
        if not chosen and not override and self._web_lookup and settings.get("web_lookup", True):
            chosen = await self._lookup_site(run, key)
        if not chosen:
            run.notes.append("The company's own website could not be confirmed, so no company pages are used")
            return "failed", "No verified company website"
        if override and chosen["origin"] == "user_pin" and chosen["url"] == override and self._read(run, chosen["url"]):
            self._pin(key, chosen["url"])  # remembered only after the website was really read, never before
        run.site = chosen
        return "done", chosen["domain"]

    @staticmethod
    def _read(run: Run, origin_url: str) -> bool:
        """Did this run read the homepage of that website (not just trust the address)?"""
        home = run.homepage or {}
        host = urlsplit(home.get("url") or "").hostname or ""
        return bool(home) and registrable_domain(host.lower()) == registrable_domain((urlsplit(origin_url).hostname or "").lower())

    async def _refine_company(self, run: Run) -> None:
        """A first-party ATS board names its company properly; only used to improve a weak guess."""
        company, hint = run.company, run.report["ats"]
        if company["confidence"] >= 0.9 or not hint.get("name_api"):
            return
        try:
            data = await self._json(run, hint["name_api"], label="Job board profile", kind="ats_company",
                                    origin="ats_api")
        except FetchError:
            return
        name = normalize_company(clean(data.get("name") if isinstance(data, dict) else "", 200))
        if name and company_key(name) not in ATS_BRANDS:
            old_key = run.key
            company.update(name=name, key=tail_key(name), name_source="ats_api", confidence=0.95)
            new_key = (company["key"], run.report["job_key"])
            if new_key != old_key and self._by_key.get(new_key) in {None, run.id}:
                if self._by_key.get(old_key) == run.id:
                    self._by_key.pop(old_key, None)
                run.key = new_key
                self._by_key[new_key] = run.id
            run.headline = f"Researching {name}…"
            self._touch(run)
        blurb = (await parse_isolated("html_to_text", data.get("content") or data.get("description") or "", default="")
                 if isinstance(data, dict) else "")
        blurb, removed = sanitize_untrusted_text(blurb)
        if len(blurb) >= 120:
            board = hint.get("board_url") or hint["name_api"]
            run.items.append({"kind": "ats_company", "label": f"{company['name']} on its job board", "origin": "ats_api",
                              "url": board, "title": f"About {company['name']}", "text": blurb[:PAGE_LIMIT],
                              "retrieved_at": _iso(), "sha256": sha(blurb), "trusted_as_instructions": False})
            self._touch(run)

    async def _try_verify(self, run: Run, origin_url: str, origin: str, trusted: bool) -> tuple[bool, str]:
        """One candidate website that cannot be checked (whatever is wrong with its address or page) is a candidate
        that is not verified: the next one is still tried."""
        try:
            return await self._verify_site(run, origin_url, origin, trusted)
        except asyncio.CancelledError:
            raise
        except Exception:
            run.unverified.append({"url": origin_url, "domain": urlsplit(origin_url).netloc.lower(), "origin": origin})
            return False, ""

    async def _verify_site(self, run: Run, origin_url: str, origin: str, trusted: bool) -> tuple[bool, str]:
        """Fetch the homepage once; accept only when it plainly belongs to this company."""
        company = run.company["name"]
        try:
            page = await self._get(run, origin_url, label=f"{urlsplit(origin_url).netloc} homepage", kind="company_home",
                                   origin=origin, record=False, max_body=HOMEPAGE_MAX_BODY)
        except FetchError as exc:
            run.sources.append({"label": f"{urlsplit(origin_url).netloc} homepage", "url": origin_url,
                                "kind": "company_home", "origin": origin,
                                "status": "blocked" if exc.kind == "robots" else "failed", "chars": 0,
                                "retrieved_at": None, "cached": False, "error": "Could not be read"})
            self._touch(run)
            return (trusted and origin == "user_pin"), "Pinned by you (could not be read)"
        final = urlsplit(page.final_url)
        if registrable_domain((final.hostname or "").lower()) != registrable_domain(urlsplit(origin_url).hostname or "") \
                and not trusted:
            run.sources.append({"label": f"{final.netloc} homepage", "url": page.final_url, "kind": "company_home",
                                "origin": origin, "status": "unverified", "chars": len(page.body), "retrieved_at": _iso(),
                                "cached": False, "error": "The site redirects somewhere else"})
            self._touch(run)
            return False, ""
        read = await self._read_page(run, page.text(), page.final_url)
        signals = read["signals"]
        key = tail_key(company)
        label = registrable_domain((final.hostname or "").lower()).split(".")[0]
        # What makes a website THIS company's, besides being named by Meridian's own records (``trusted``):
        # the page itself names the company (never by way of its own domain name), and for a site Meridian only GUESSED
        # (``<name>.com``: the domain label equals the name by construction, so it proves nothing) the company's job board
        # is also linked from it. A domain label alone never verifies anything (SAFE-10); a name too short to be told
        # apart needs the domain to match as well.
        names = page_names_company(signals, company, final.hostname or "")
        linked = origin == "domain_guess" and self._links_to_posting(run, signals)
        if trusted:
            identity = True
            evidence = "Pinned by you" if origin == "user_pin" else "From Meridian's own records"
        elif origin == "domain_guess":
            identity = names and linked
            evidence = f"The page names {company} and links to its job board"
        else:
            identity = names and (len(key) >= 4 or label_matches_company(label, company))
            evidence = f"The page names {company}"
        if identity:
            text, removed = sanitize_untrusted_text(read["text"])
            run.homepage = {"url": page.final_url, "signals": signals}  # never the page itself: it can be 1.5 MB
            home = final.netloc.lower()
            run.sources.append({"label": f"{home} homepage", "url": page.final_url, "kind": "company_home",
                                "origin": origin, "status": "ok", "chars": len(text), "retrieved_at": _iso(),
                                "cached": False, "error": None})
            if len(text) >= 80:
                run.items.append({"kind": "company_home", "label": f"{company} homepage", "origin": origin,
                                  "url": page.final_url, "title": clean(signals["title"] or company, 200),
                                  "text": text[:PAGE_LIMIT], "retrieved_at": _iso(), "sha256": sha(text),
                                  "trusted_as_instructions": False})
            blurb = signals["meta"].get("og:description") or signals["meta"].get("description") or ""
            if blurb and not run.brief:
                run.brief, run.brief_source = clean(blurb, 300), page.final_url
            self._touch(run)
            return True, evidence
        run.sources.append({"label": f"{final.netloc.lower()} homepage", "url": page.final_url, "kind": "company_home",
                            "origin": origin, "status": "unverified", "chars": len(page.body), "retrieved_at": _iso(),
                            "cached": False,
                            "error": f"Could not confirm it is {company}" + (
                                ": nothing links this site to the job posting" if origin == "domain_guess" and names
                                else "")})
        run.unverified.append({"url": origin_url, "domain": urlsplit(origin_url).netloc.lower(), "origin": origin})
        self._touch(run)
        return False, ""

    @staticmethod
    def _links_to_posting(run: Run, signals: dict) -> bool:
        """Does the candidate site link to where THIS posting lives (its job board on the ATS, or its careers site)? A site
        that only shares a name with the company does not."""
        report = run.report
        hint, host = report["ats"], report["host"]
        if report["ats"].get("aggregator"):
            return False
        for link in signals.get("links", [])[:1500]:
            try:
                parts = urlsplit(link["href"])
                lhost = (parts.hostname or "").lower()
            except ValueError:
                continue
            if not lhost:
                continue
            if is_ats_host(host):
                other = ats_hint(link["href"])
                if hint.get("slug") and other.get("ats") == hint.get("ats") and \
                        str(other.get("slug") or "").lower() == str(hint["slug"]).lower():
                    return True
                if lhost == host and host.endswith(".myworkdayjobs.com"):
                    return True
            elif registrable_domain(lhost) == registrable_domain(host):
                return True
        return False

    async def _lookup_site(self, run: Run, key: str) -> dict | None:
        """Last resort: ONE public search for ``<company> official website`` (company name only), cached 90 days."""
        cached = self.cache.get("site_search", key, TTL_SEARCH)
        results = None
        if cached:
            results = cached[0].get("candidates")
        else:
            async with self._search_lock:
                if time.monotonic() < self._search_blocked_until:
                    return None
                wait = 3.0 - (time.monotonic() - self._last_search)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_search = time.monotonic()
                try:
                    results = await (self._search or search_official_site)(run.company["name"])
                except SearchUnavailable:
                    self._search_blocked_until = time.monotonic() + 3600
                    return None
                self.cache.put("site_search", key, {"candidates": results})
        tried = 0
        for result in results or []:
            host = (urlsplit(result["url"]).hostname or "").lower()
            if not host or is_non_official(host) or _is_ip(host):
                continue
            tried += 1
            if tried > 2:
                break
            origin = f"https://{registrable_domain(host)}/" if registrable_domain(host) != host else f"https://{host}/"
            ok, evidence = await self._try_verify(run, origin, "web_search", False)
            if ok:
                self._pin(key, origin)  # never searched again
                return {"url": origin, "domain": urlsplit(origin).netloc, "origin": "web_search", "verified": True,
                        "evidence": evidence}
        return None

    def _refresh_in_background(self, run: Run) -> None:
        """Stale-while-revalidate: a no-subscriber run rewrites the cache; the foreground bundle stays frozen."""
        try:
            report = {**run.report, "user": dict(run.report["user"])}
        except Exception:
            return
        key = (run.key[0], run.key[1] + "#refresh")
        if key in self._by_key:
            return
        background = Run(id="rs_" + uuid.uuid4().hex[:20], key=key, report=report, refresh=True, background=True,
                         company=dict(run.company))
        background.stages = OrderedDict((sid, {"id": sid, "label": label, "status": "pending", "detail": "", "ms": None})
                                        for sid, label in STAGE_LABELS.items())
        self._runs[background.id] = background
        self._by_key[key] = background.id
        background.task = asyncio.get_running_loop().create_task(self._execute(background))
        self._tasks.add(background.task)
        background.task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------------------- stage: pages
    async def _pages(self, run: Run):
        report, site = run.report, run.site
        base = registrable_domain((urlsplit(site["url"]).hostname or "").lower())
        title = run.role["title"] if run.role else (report["h1"] or report["title"])
        boost = EARLY_CAREER if EARLY_CAREER.search(title or "") else None
        links = list((run.homepage or {}).get("signals", {}).get("links", [])) + list(report["links"])
        already = [site["url"]] + [i["url"] for i in run.items]
        ranked = rank_company_links(links, base_domain=base, limit=6, already=already, boost=boost)
        if boost is None and ENGINEERING_TITLE.search(title or ""):
            ranked.sort(key=lambda r: (r["kind"] != "company_engineering", -r["score"]))
        picked = [dict(r, origin="nav_link") for r in ranked]
        for url in run.cached_pages:
            if all(url != p["url"] for p in picked) and len(picked) < 6:
                picked.append({"url": url, "kind": "company_about", "origin": "cache", "anchor": ""})
        if len(picked) < 2:
            for path in FIXED_PATHS:
                if len(picked) >= 4:
                    break
                url = urljoin(site["url"], path)
                if all(url.rstrip("/") != p["url"].rstrip("/") for p in picked) and url.rstrip("/") not in {
                        u.rstrip("/") for u in already}:
                    kind = next((k for k, pattern, _ in SECTION_PATTERNS if pattern.search(path)), "company_about")
                    picked.append({"url": url, "kind": kind, "origin": "guessed_path", "anchor": path, "guessed": True})
        semaphore = asyncio.Semaphore(4)
        host_gates: dict[str, asyncio.Semaphore] = {}

        async def read(entry):
            url = entry["url"]
            hit = None if run.refresh else self.cache.get("pages", url, TTL_PAGE)
            if hit and hit[0].get("text"):
                item = dict(hit[0])
                run.sources.append({"label": item["label"], "url": item["url"], "kind": item["kind"],
                                    "origin": entry["origin"], "status": "ok", "chars": len(item["text"]),
                                    "retrieved_at": item["retrieved_at"], "cached": True, "error": None})
                return item
            gate = host_gates.setdefault((urlsplit(url).hostname or "").lower(), asyncio.Semaphore(2))
            async with semaphore, gate:
                try:
                    page = await self._get(run, url, label=entry["anchor"] or entry["kind"], kind=entry["kind"],
                                           origin=entry["origin"], record=False, guessed=bool(entry.get("guessed")))
                except FetchError as exc:
                    status = ("blocked" if exc.kind == "robots" or exc.status in {403, 429}
                              else "skipped" if exc.kind == "budget" else "failed")
                    run.sources.append({"label": (entry["anchor"] or entry["kind"])[:80], "url": url,
                                        "kind": entry["kind"], "origin": entry["origin"], "status": status, "chars": 0,
                                        "retrieved_at": None, "cached": False,
                                        "error": {"robots": "The site's robots.txt does not allow it", "budget": "Skipped",
                                                  "http_error": ("Page not found" if exc.status == 404
                                                                 else "The site did not allow it"
                                                                 if exc.status in {403, 429} else "Unavailable"),
                                                  }.get(exc.kind, "Unavailable")})
                    self._touch(run)
                    return None
            final = urlsplit(page.final_url)
            if registrable_domain((final.hostname or "").lower()) != base:
                run.sources.append({"label": entry["anchor"][:80] or entry["kind"], "url": url, "kind": entry["kind"],
                                    "origin": entry["origin"], "status": "unverified", "chars": 0, "retrieved_at": None,
                                    "cached": False, "error": "Redirected to another site"})
                self._touch(run)
                return None
            read = await self._read_page(run, page.text(), page.final_url)
            signals = read["signals"]
            text, removed = sanitize_untrusted_text(read["text"])
            if removed:
                run.notes.append(f"{removed} instruction-like line{'s' if removed != 1 else ''} removed from a company page")
            if len(text) < 120:
                run.sources.append({"label": (signals["title"] or entry["kind"])[:80], "url": page.final_url,
                                    "kind": entry["kind"], "origin": entry["origin"], "status": "skipped",
                                    "chars": len(text), "retrieved_at": _iso(), "cached": False,
                                    "error": "Too little readable text"})
                self._touch(run)
                return None
            label = clean(signals["h1"] or signals["title"] or entry["anchor"] or entry["kind"], 120)
            item = {"kind": entry["kind"], "label": label, "origin": entry["origin"], "url": page.final_url,
                    "title": clean(signals["title"] or label, 200), "text": text[:PAGE_LIMIT], "retrieved_at": _iso(),
                    "sha256": sha(text), "trusted_as_instructions": False}
            run.sources.append({"label": label[:80], "url": page.final_url, "kind": entry["kind"],
                                "origin": entry["origin"], "status": "ok", "chars": len(text),
                                "retrieved_at": item["retrieved_at"], "cached": False, "error": None})
            self.cache.put("pages", url, item)
            self._touch(run)
            return item

        results = await asyncio.gather(*(read(entry) for entry in picked[:6]))
        read_ok = [r for r in results if r]
        for item in read_ok:
            if all(item["url"] != existing["url"] for existing in run.items):
                run.items.append(item)
        if not run.brief:
            about = next((i for i in read_ok if i["kind"] in {"company_about", "company_mission"}), None)
            if about:
                first = next((line for line in about["text"].splitlines() if not line.startswith("##") and len(line) > 60), "")
                run.brief, run.brief_source = clean(first, 300), about["url"]
        self._touch(run)
        if not read_ok:
            return "failed", "No company pages could be read"
        return "done", f"{len(read_ok)} page{'s' if len(read_ok) != 1 else ''} read"

    # ------------------------------------------------------------------------------- persistence
    def _persist(self, run: Run) -> None:
        if run.background and False:
            return
        report, company = run.report, run.company
        if run.jd and run.role and run.role["description_state"] != "NOT_FOUND":
            self.cache.put("role", report["job_key"], {"state": run.role["description_state"], "text": run.jd["text"],
                                                       "title": run.role["title"], "location": run.role["location"],
                                                       "country": run.role["country"],
                                                       "countries": run.role["countries"], "remote": run.role["remote"],
                                                       "employment_type": run.role["employment_type"],
                                                       "department": run.role["department"], "url": run.jd["url"]})
        if (company and run.site and run.site.get("verified") and run.items
                and run.site.get("origin") in CACHEABLE_ORIGINS):  # never a site the page itself named (SEC-04)
            self.cache.put("company", tail_key(company["name"]), {
                "company": company["name"], "site": run.site, "brief": run.brief, "brief_source": run.brief_source,
                "page_urls": [i["url"] for i in run.items if i["kind"] not in {"ats_company", "company_home"}]})


def quiet_http_logs() -> None:
    """httpx logs every request at INFO, address and query included, and the backend's log file would then list every site
    researched and every company searched for: only warnings are kept."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def canonical_url_safe(url: str) -> str:
    try:
        return canonical_url(url)
    except ValueError:
        return url


def _official_careers() -> dict:
    try:
        from ..career_sources import OFFICIAL_CAREERS
        return dict(OFFICIAL_CAREERS)
    except Exception:
        return {}


def prime_research(app, job_id: str) -> dict | None:
    """Warm the research cache for a stored job ('open with assistant'). Never raises; returns the snapshot or None.

    The run is started with an ephemeral subscriber, so it finishes (it is cheap and bounded) and nothing cancels it."""
    manager = getattr(app.state, "extension_research", None)
    db = getattr(app.state, "db", None)
    if manager is None or db is None:
        return None
    try:
        job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            return None
        url = job["application_url"] or job["url"]
        report = {"url": url, "title": job["title"], "h1": job["title"], "lang": "", "company_hint": job["company"],
                  "meta": {}, "canonical": None, "jsonld": [], "description_text": (job.get("description") or "")[:24000],
                  "links": [], "user": {"company": job["company"], "title": job["title"]}}
        return manager.start(f"prime:{job_id}", report)
    except Exception:
        return None
