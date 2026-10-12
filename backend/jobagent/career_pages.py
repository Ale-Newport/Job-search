"""Read published career-page data without executing scripts or guessing board IDs."""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

DETAIL_PATH = re.compile(r'/(?:job(?:s|-offers|-details?)?|jobdetail|details?|positions?|roles?|vacanc(?:y|ies)|careers/listing|j)/[^/?#]+', re.I)
SEARCH_TEXT = re.compile(r'\b(?:search|view|see|all|open|explore|find|current)\b.*\b(?:jobs|roles|positions|vacancies|opportunities)\b|\b(?:jobs|open roles|vacancies)\b|招聘|职位|岗位', re.I)
EXCLUDED = re.compile(r'privacy|cookie|sign[- ]?in|login|talent.community|job.alert|fraud|scam|accessibility|/(?:blog|news|stories|events|articles)/|/jobs/(?:saved|recommendations)(?:[/?#]|$)', re.I)
SOCIAL_HOST = re.compile(r'(?:^|\.)(?:linkedin\.com|facebook\.com|instagram\.com|youtube\.com|twitter\.com|x\.com)$', re.I)
ROLE_TITLE = re.compile(r'\b(?:engineer|scientist|analyst|manager|director|intern|graduate|associate|specialist|developer|designer|officer|coordinator|administrator|technician|architect|consultant|researcher|trader|accountant|assistant|lead|head|counsel|executive|recruiter|sales|support)\b', re.I)


def parse_html(text):
    try:
        return BeautifulSoup(text, 'html.parser')
    except ParserRejectedMarkup:
        # Some employer templates contain invalid marked declarations. Remove
        # only those declarations, preserving the actual page and JSON data.
        repaired = re.sub(r'<!\[(?!CDATA\[).*?\]>', '', text, flags=re.S)
        if repaired == text:
            raise
        return BeautifulSoup(repaired, 'html.parser')


def reference_base(soup, url):
    base = soup.find('base', href=True)
    if base:
        return urljoin(url, base['href'])
    for script in soup.select('script:not([src])'):
        match = re.search(r'\bvar phApp\s*=\s*phApp\s*\|\|\s*', script.get_text())
        if match:
            try:
                value, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():])
                candidate = value.get('baseUrl')
                if isinstance(candidate, str) and urlsplit(candidate).hostname == urlsplit(url).hostname:
                    return candidate
            except (ValueError, AttributeError):
                continue
    return url


def board_references(text: str, url: str) -> list[dict]:
    """Only explicit employer-published URLs/board attributes establish identity."""
    soup = parse_html(text)
    base_url = reference_base(soup, url)
    refs = [n.get('href') or n.get('src') for n in soup.select('a[href],iframe[src]')]
    refs.extend(re.findall(r'https://[^\s<>"\x27\\]+', unescape(text).replace('\\/', '/')))
    for node in soup.select('[data-board-token], [data-board], [data-workable]'):
        for key in ('data-board-token', 'data-board', 'data-workable'):
            token = node.get(key)
            if token and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', token):
                if key == 'data-workable':
                    refs.append('https://apply.workable.com/' + token)
                elif 'greenhouse' in str(node).lower():
                    refs.append('https://boards.greenhouse.io/' + token)
    boards = {}
    for reference in refs:
        link = urljoin(base_url, reference)
        parsed = urlsplit(link)
        host = (parsed.hostname or '').lower()
        path = [p for p in parsed.path.split('/') if p]
        kind = board = None
        if host in {'boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'}:
            kind = 'greenhouse'
            board = parse_qs(parsed.query).get('for', [path[0] if path else ''])[0]
        elif host in {'boards-api.greenhouse.io', 'boards-api.eu.greenhouse.io'} and len(path) >= 3 and path[:2] == ['v1', 'boards']:
            kind, board = 'greenhouse', path[2]
        elif host in {'jobs.lever.co', 'jobs.eu.lever.co'} and path:
            kind, board = 'lever', path[0]
        elif host == 'jobs.ashbyhq.com' and path:
            kind, board = 'ashby', path[0]
        elif host.endswith('.myworkdayjobs.com') and path:
            kind = 'workday'
            board = path[1] if re.fullmatch(r'[a-z]{2}-[a-z]{2}', path[0], re.I) and len(path) > 1 else path[0]
        elif host == 'jobs.smartrecruiters.com' and path:
            kind, board = 'smartrecruiters', path[0]
        elif host == 'apply.workable.com' and path and path[0] not in {'j', 'api'}:
            kind, board = 'workable', path[0]
        elif 'sites' in path and ('oraclecloud.com' in host or re.search(r'career|jobs', host)):
            index = path.index('sites')
            if len(path) > index+1:
                kind, board = 'oraclehcm', path[index+1]
        if kind and board and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', board):
            boards[(kind, board)] = {'kind': kind, 'url': link, 'config': {'board': board}}
    return list(boards.values())


def career_scripts(text: str, url: str) -> list[str]:
    soup = parse_html(text)
    refs = [urljoin(url, n['component-url']) for n in soup.select('astro-island[component-url]') if 'job' in (n.get('props') or '').lower()]
    for script in soup.select('script[src]'):
        link = urljoin(url, script['src'])
        if urlsplit(link).hostname != urlsplit(url).hostname:
            continue
        if re.search(r'career|job|position|recruit', urlsplit(link).path, re.I) or (re.search(r'career|jobs', urlsplit(url).hostname or '') and re.search(r'main[-.]', urlsplit(link).path)):
            refs.append(link)
    return list(dict.fromkeys(refs))[:3]


def page_links(text: str, url: str) -> tuple[list[str], list[dict]]:
    soup = parse_html(text)
    base_url = reference_base(soup, url)
    searches, jobs = [], {}
    def search_actions(value, path=''):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path+'.'+key
                if isinstance(child, str) and key.lower() in ('action', 'searchurl', 'search_url') and 'search' in child_path.lower():
                    candidate = urljoin(base_url, child)
                    if urlsplit(candidate).scheme == 'https' and urlsplit(candidate).hostname == urlsplit(url).hostname and not EXCLUDED.search(candidate):
                        searches.append(candidate)
                elif isinstance(child, (dict, list)):
                    search_actions(child, child_path)
        elif isinstance(value, list):
            for child in value:
                search_actions(child, path)
    for node in soup.select('[data-react-props], [data-props]'):
        try:
            search_actions(json.loads(node.get('data-react-props') or node.get('data-props')))
        except (ValueError, TypeError, RecursionError):
            continue
    for node in soup.select('[data-ef-careers-url], #eightfold-careers-url, #eightfold-url, [id$="eightFoldCareerUrl"]'):
        candidate = node.get('data-ef-careers-url') or node.get_text(strip=True)
        if candidate.startswith('https://'):
            searches.append(candidate)
    for anchor in soup.select('a[href]'):
        link = urljoin(base_url, anchor['href'])
        parsed = urlsplit(link)
        title = anchor.get_text(' ', strip=True)
        if parsed.scheme != 'https' or not parsed.hostname or EXCLUDED.search(link + ' ' + title) or re.search(r'\.(?:pdf|png|jpg|svg|js|css)$', parsed.path, re.I):
            continue
        identity = parse_qs(parsed.query)
        identified_query = any(identity.get(k) for k in ('pid', 'jobId', 'job_id', 'gh_jid', 'requisitionId'))
        if not ROLE_TITLE.search(title) and (DETAIL_PATH.search(parsed.path) or identified_query):
            parent = anchor.parent
            for _ in range(4):
                if parent is None:
                    break
                destinations = {a.get('href') for a in parent.select('a[href]') if DETAIL_PATH.search(urlsplit(urljoin(base_url, a['href'])).path)}
                if len(destinations) > 1:
                    break
                heading = next((h.get_text(' ', strip=True) for h in parent.select('h2,h3,h4') if ROLE_TITLE.search(h.get_text())), None)
                if heading:
                    title = heading
                    break
                parent = parent.parent
        detail = bool((DETAIL_PATH.search(parsed.path) or identified_query or re.search(r'/\d{6,}/?$', parsed.path)) and
                      ROLE_TITLE.search(title))
        if detail and 5 <= len(title) <= 180 and not re.fullmatch(r'apply(?: now)?|learn more|read more|details|view (?:job|role)', title, re.I):
            jobs[link] = {'title': title, 'url': link}
        elif SEARCH_TEXT.search(title) or re.search(r'/(?:search(?:-results)?|open-roles|vacancies|jobs)(?:/|$)|/go/[^/]*jobs/\d+/?$', parsed.path, re.I):
            # Follow explicit career destinations, including a separate employer
            # portal, but never social sites or account/application actions.
            explicit_roles = bool(re.fullmatch(r'(?:search(?: for)?|view|see|all|open|explore|find|current)?\s*(?:our\s+|a\s+)?(?:jobs|roles|positions|vacancies|opportunities)', title, re.I))
            if not SOCIAL_HOST.search(parsed.hostname) and (parsed.hostname == urlsplit(url).hostname or re.search(r'career|jobs|recruit|oraclecloud|yello|eightfold', parsed.hostname) or explicit_roles):
                searches.append(link)
    for form in soup.select('form[action]'):
        if form.get('method', 'get').lower() == 'get' and re.search(r'search|jobs', form['action'], re.I):
            searches.insert(0, urljoin(base_url, form['action']))
    # Job listings take precedence over global site search or career articles.
    def search_priority(link):
        parsed = urlsplit(link)
        return (not any(k.lower() in {'jobalerts','rcmloggedin','alertid'} for k,_ in parse_qsl(parsed.query)),
                bool(re.search(r'/(?:jobs|vacancies|open-roles|search-results)(?:[/?]|$)|/go/[^/]*jobs/\d+/?$', parsed.path, re.I)),
                parsed.hostname != urlsplit(url).hostname and bool(re.search(r'career|jobs|recruit', parsed.hostname or '')))
    return sorted(dict.fromkeys(searches), key=search_priority, reverse=True), list(jobs.values())


def _nuxt_value(data: list, index: int, seen: frozenset[int] = frozenset(), budget=None):
    budget = [50000] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or index < 0 or index >= len(data) or index in seen or len(seen) > 35:
        return None
    value = data[index]
    seen = seen | {index}
    if isinstance(value, dict):
        return {k: _nuxt_value(data, v, seen, budget) if isinstance(v, int) else v for k, v in value.items()}
    if isinstance(value, list):
        if value and value[0] in ('Reactive', 'ShallowReactive', 'Ref', 'ShallowRef'):
            return _nuxt_value(data, value[1], seen, budget)
        return [_nuxt_value(data, v, seen, budget) if isinstance(v, int) else v for v in value]
    return value


def embedded_jobs(text: str, url: str, company: str) -> list[dict]:
    """Extract recognizable job collections from JSON hydration, with real URLs."""
    soup = parse_html(text)
    anchors = [urljoin(url, a['href']) for a in soup.select('a[href]')]
    anchors.extend(re.findall(r'https://[^\s<>"\x27\\]+', text.replace('\\/', '/')))
    result = {}
    templates = set()
    query_templates = set()

    def learn_templates(value):
        if isinstance(value, dict):
            identifier = value.get('greenhouseId') or value.get('id')
            slug = value.get('slug')
            if identifier and isinstance(slug, str):
                suffix = '/' + slug + '/' + str(identifier)
                for anchor in anchors:
                    if urlsplit(anchor).path.endswith(suffix):
                        templates.add(anchor[:anchor.rfind(suffix)])
            if identifier:
                parsed = urlsplit(url)
                for key in ('pid', 'jobId', 'job_id'):
                    if parse_qs(parsed.query).get(key) == [str(identifier)]:
                        query_templates.add(key)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    learn_templates(child)
        elif isinstance(value, list):
            for child in value:
                learn_templates(child)

    def visit(value, path='', context=None):
        if isinstance(value, list):
            for item in value:
                visit(item, path, context)
        elif isinstance(value, dict):
            context = value if isinstance(value.get('filters'), dict) else context
            fields = value.get('fields')
            if isinstance(fields, dict) and fields.get('slug') and re.search(r'open.?roles|vacanc|job|position', path, re.I):
                nodes = []
                def flatten(node):
                    if isinstance(node, dict):
                        nodes.append(node)
                        for child in node.values():
                            if isinstance(child, (dict, list)):
                                flatten(child)
                    elif isinstance(node, list):
                        for child in node:
                            flatten(child)
                flatten(fields.get('blocks', []))
                published = next((node for node in nodes if isinstance(node.get('applyLink'), str)), None)
                if published:
                    title = fields.get('title') or fields.get('name') or ''
                    title = re.sub(r'^(?:careers?|jobs?):\s*(?:open roles?:\s*)?', '', title, flags=re.I)
                    if ROLE_TITLE.search(title):
                        destination = urljoin(url, published['applyLink'])
                        description = ' '.join(str(node['value']) for node in nodes if node.get('nodeType') == 'text' and isinstance(node.get('value'), str))
                        result.setdefault(destination, {'title': title, 'url': destination, 'application_url': destination, 'company': company,
                                                       'location': published.get('location') or '', 'description': description,
                                                       'metadata': {'discovery_evidence': 'published CMS role and application link', 'description_state': 'PUBLISHED' if description else 'NOT_PUBLISHED_IN_LISTING'}})
                        return
            title = value.get('title') or value.get('jobTitle') or value.get('position') or value.get('name')
            link = value.get('jobUrl') or value.get('absolute_url') or value.get('hostedUrl') or value.get('applyLink') or value.get('url') or value.get('link')
            if isinstance(title, str) and re.search(r'job|listing|position|vacanc|requisition', path, re.I):
                if not isinstance(link, str):
                    # A published listing may provide slug/id separately. Infer
                    # its template only from a matching actual anchor on this page.
                    identifier = value.get('greenhouseId') or value.get('id') or value.get('jobPostingId') or value.get('jobId')
                    slug = value.get('slug')
                    if identifier:
                        link = next((a for a in anchors if '/'+str(identifier)+'/' in a and DETAIL_PATH.search(urlsplit(a).path)), None)
                        if not link and len(query_templates) == 1:
                            parsed = urlsplit(url)
                            params = {k: v[0] for k, v in parse_qs(parsed.query).items() if k not in ('start', 'offset', 'sort_by')}
                            params[next(iter(query_templates))] = str(identifier)
                            link = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ''))
                    if identifier and isinstance(slug, str):
                        direct = next((a for a in anchors if str(identifier) in a and slug in a), None)
                        if direct:
                            link = direct
                        else:
                            if len(templates) == 1:
                                link = next(iter(templates)) + '/' + slug + '/' + str(identifier)
                if isinstance(link, str) and link and 3 <= len(title) <= 200:
                    destination = urljoin(url, link)
                    posting_fields = any(value.get(k) for k in ('jobUrl', 'absolute_url', 'hostedUrl', 'applyLink', 'greenhouseId', 'jobTitle'))
                    parsed_destination = urlsplit(destination)
                    identified_query = any(parse_qs(parsed_destination.query).get(k) for k in ('pid', 'jobId', 'job_id'))
                    identified_role = bool((DETAIL_PATH.search(parsed_destination.path) or identified_query) and ROLE_TITLE.search(title))
                    if urlsplit(destination).scheme == 'https' and (posting_fields or identified_role):
                        location = value.get('location') or value.get('locationsText') or ''
                        if not location and context and isinstance(value.get('locationIndices'), list):
                            options = context['filters'].get('locations', [])
                            location = ', '.join(str(options[i].get('name', '')) for i in value['locationIndices'] if isinstance(i, int) and 0 <= i < len(options))
                        if isinstance(location, dict):
                            location = location.get('name') or location.get('city') or ''
                        result[destination] = {'title': title, 'url': destination, 'company': company, 'location': location,
                                               'description': value.get('descriptionPlain') or value.get('description') or '',
                                               'metadata': {'discovery_evidence': 'published JSON job collection', 'description_state': 'PUBLISHED' if value.get('description') or value.get('descriptionPlain') else 'NOT_PUBLISHED_IN_LISTING'}}
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    visit(item, path + '.' + key, context)

    for script in soup.select('script[type="application/json"]'):
        try:
            data = json.loads(script.get_text())
            if script.get('id') == '__NUXT_DATA__' and isinstance(data, list):
                data = _nuxt_value(data, 0)
            learn_templates(data)
            visit(data)
        except (ValueError, TypeError, RecursionError):
            continue
    for script in soup.select('script:not([src])'):
        for match in list(re.finditer(r'\b(?:phApp\.ddo|window\.__INITIAL_STATE__|window\.__PRELOADED_STATE__)\s*=\s*', script.get_text()))[:8]:
            try:
                data, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():])
                learn_templates(data)
                visit(data)
            except (ValueError, TypeError, RecursionError):
                continue
    # React server components can carry the same public job collection in JSON
    # string chunks rather than a __NEXT_DATA__ element. Decode data, never JS.
    chunks = []
    for script in soup.select('script:not([src])'):
        for match in re.finditer(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', script.get_text()):
            try:
                chunks.append(json.loads(match.group(1)))
            except ValueError:
                continue
    stream = ''.join(chunks)
    for match in re.finditer(r'(?<![A-Za-z0-9])[0-9a-f]{1,8}:(?=[\[{])', stream):
        try:
            data, _ = json.JSONDecoder().raw_decode(stream[match.end():])
            learn_templates(data)
            visit(data)
        except (ValueError, TypeError, RecursionError):
            continue
    return list(result.values())
