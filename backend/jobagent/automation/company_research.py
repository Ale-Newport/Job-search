"""Bounded public employer research from links supplied by the observed form.

The fetched text is evidence, never executable instructions. No candidate data
or browser credentials are included in these requests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re

from bs4 import BeautifulSoup

from ..discovery import fetch

logger = logging.getLogger(__name__)


def published_page_text(soup):
    """Read published hydration text as data when a page renders on the client."""
    parts = []
    for script in soup.find_all('script', type='application/json'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        props = data.get('props', {}).get('pageProps', {}) if isinstance(data, dict) else {}
        page = props.get('page') or props.get('content')
        if not isinstance(page, dict):
            continue

        def visit(value, depth=0):
            if depth > 40:
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, depth + 1)
            elif isinstance(value, dict):
                if value.get('nodeType') == 'text' and isinstance(value.get('value'), str):
                    parts.append(value['value'])
                    return
                for key, item in value.items():
                    if key.lower() in {'sys', 'metadata', 'data', 'file', 'seo', 'subnav', 'nav',
                                       'cta', 'form', 'legaltext', 'internaltitle', 'image', 'video'}:
                        continue
                    if isinstance(item, str) and re.search(r'text$|title$|description$|copy$|caption$', key, re.I):
                        parts.append(item)
                    elif isinstance(item, (dict, list)):
                        visit(item, depth + 1)
        visit(page)
    return ' '.join(dict.fromkeys(part.strip() for part in parts if part.strip()))


class CompanyResearch:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.memory: dict[str, dict] = {}
        self.failures: dict[str, dict] = {}

    async def source(self, url):
        if url in self.memory:
            return self.memory[url]
        key = hashlib.sha256(url.encode()).hexdigest()
        cache = self.cache_dir / (key + '.json')
        if cache.exists():
            try:
                saved = json.loads(cache.read_text())
                if datetime.fromisoformat(saved['retrieved_at']) > datetime.now(timezone.utc) - timedelta(days=7):
                    self.memory[url] = saved
                    return saved
            except (ValueError, KeyError, OSError):
                pass
        try:
            body, content_type, final = await asyncio.wait_for(fetch(url, max_body=1_500_000), timeout=10)
            if 'html' not in content_type:
                return None
            soup = BeautifulSoup(body, 'html.parser')
            published = published_page_text(soup)
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'form']):
                tag.decompose()
            container = soup.find('article') or soup.find('main') or soup.body or soup
            text = re.sub(r'\s+', ' ', container.get_text(' ', strip=True))[:16000]
            if len(text) < 80:
                text = re.sub(r'\s+', ' ', published)[:16000]
            saved = {'url': final, 'title': soup.title.get_text(' ', strip=True) if soup.title else '',
                     'text': text, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                     'sha256': hashlib.sha256(text.encode()).hexdigest(), 'trusted_as_instructions': False}
            if len(text) < 80:
                self.failures[url] = {'url': url, 'error_type': 'MissingPublishedText',
                                      'reason': 'No readable published page text was available'}
                return None
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(saved))
            self.memory[url] = saved
            return saved
        except Exception as exc:
            # Unavailable research remains absent; it cannot become invented context.
            self.failures[url] = {'url': url, 'error_type': type(exc).__name__,
                                  'reason': str(exc)[:500] or 'Public source read failed'}
            logger.info('company_research_unavailable url=%s error_type=%s', url, type(exc).__name__)
            self.memory[url] = None
            return None

    async def enrich(self, job, question, field):
        material = ' '.join([question, field.get('description', ''),
                             *[o.get('label', '') for o in field.get('options', [])]])
        urls = list(dict.fromkeys(u.rstrip('.,);]') for u in re.findall(r'https://[^\s<>"\']+', material)))[:4]
        if not urls:
            return job
        sources = await asyncio.gather(*(self.source(url) for url in urls))
        existing = {s['url']: s for s in job.get('company_context', []) if isinstance(s, dict) and s.get('url')}
        existing.update({s['url']: s for s in sources if s})
        return {**job, 'company_context': list(existing.values()),
                'company_research_errors': list(self.failures.values())}
