"""Read employer-published job sitemaps without guessing posting URLs."""
import asyncio
import xml.etree.ElementTree as ET

import httpx

from .career_pages import DETAIL_PATH
from .discovery import MAX_JOBS, DiscoveryResult, ats_identity, canonical_url, fetch, parse_jsonld
from urllib.parse import urlsplit


async def discover_sitemap(config):
    setting = config['public_sitemap']
    queue, visited, locations = [setting['url']], set(), []
    while queue and len(visited) < 10:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        body, _, final = await fetch(url)
        if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
            raise ValueError('Job sitemap contains unsupported XML declarations')
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise ValueError('Published job sitemap is not valid XML') from None
        kind = root.tag.rsplit('}', 1)[-1]
        values = [node.text.strip() for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 'loc' and node.text]
        if kind == 'sitemapindex':
            queue.extend(values)
        elif kind == 'urlset':
            locations.extend(values)
        else:
            raise ValueError('Published job sitemap has no recognizable URL collection')
    paths = list(dict.fromkeys(url for url in locations if DETAIL_PATH.search(urlsplit(url).path)))
    if not paths:
        raise ValueError('Published sitemap has no identifiable job-detail URLs; it is not evidence of an empty board')
    maximum = min(MAX_JOBS, max(1, int(config.get('max_jobs', MAX_JOBS))))
    gate, stop = asyncio.Semaphore(4), asyncio.Event()
    failures, postings = [], []

    async def inspect(url):
        async with gate:
            if stop.is_set():
                return
            try:
                body, _, final = await fetch(url)
                jobs = parse_jsonld(body.decode(errors='replace'), final)
                identity = ats_identity(url)[1]
                matched = [job for job in jobs if canonical_url(job['url']) == canonical_url(final) or
                           identity and ats_identity(job['url'])[1] == identity]
                if not matched:
                    raise ValueError('Sitemap role page has no matching structured job posting')
                postings.extend(matched)
            except Exception as error:
                failures.append(error)
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (403, 429):
                    stop.set()  # No more requests to a portal that rejects access.

    await asyncio.gather(*(inspect(url) for url in paths[:maximum]))
    if not postings:
        rejection = next((error for error in failures if isinstance(error, httpx.HTTPStatusError)), None)
        if rejection:
            raise rejection
        raise ValueError('Published sitemap role pages contain no readable structured jobs')
    limitations = ['A sitemap proves published role pages, not that every application is currently accepting candidates']
    if failures:
        limitations.append(f'{len(failures)} role pages could not be verified')
    if len(paths) > maximum or queue or stop.is_set():
        limitations.append('Sitemap validation stopped before all declared job URLs were checked')
    return DiscoveryResult(postings, complete=False, authoritative=False, method='Published job sitemap and structured role pages',
                           endpoints=list(visited), total=len(paths), limitations=limitations)
