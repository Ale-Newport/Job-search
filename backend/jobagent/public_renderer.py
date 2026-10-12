"""Bounded rendering of public career pages in an isolated, read-only browser."""
from __future__ import annotations

import json
import os
import re
from contextlib import suppress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .discovery import MAX_BODY, validate_public_url


def response_job_records(value, path='', depth=0):
    if depth > 25:
        return []
    records = []
    if isinstance(value, list):
        for item in value[:2000]:
            records.extend(response_job_records(item, path, depth+1))
    elif isinstance(value, dict):
        identifier = value.get('id') or value.get('jobId') or value.get('job_id') or value.get('Id') or value.get('shortcode') or value.get('objectID')
        title = value.get('title') or value.get('jobTitle') or value.get('job_title') or value.get('positionTitle') or value.get('Title')
        if not title and path.rsplit('.', 1)[-1].lower() in ('positions', 'jobs', 'jobpostings', 'requisitionlist'):
            title = value.get('name')
        if title and (identifier or value.get('jobUrl')) and re.search(r'job|position|requisition', path, re.I):
            location = value.get('location') or value.get('locations') or value.get('PrimaryLocation') or ''
            if isinstance(location, list):
                location = ', '.join(str(x) for x in location if isinstance(x, str))
            if isinstance(location, dict):
                location = location.get('name') or location.get('city') or ''
            records.append({'id': identifier, 'title': title, 'location': location,
                            'jobUrl': value.get('jobUrl') or value.get('absolute_url') or value.get('hostedUrl') or value.get('detailUrl') or value.get('url'),
                            'description': value.get('description') or value.get('jobDescription') or value.get('job_description') or ''})
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                records.extend(response_job_records(child, path+'.'+key, depth+1))
    return records[:2000]


def public_search_request(method: str, url: str, body: str | None) -> bool:
    if method == 'GET':
        return True
    if method != 'POST':
        return False
    path = urlsplit(url).path.lower()
    if re.search(r'apply|application|candidate|submit|account|profile|subscribe|save', path):
        return False
    try:
        payload = json.loads(body or '{}')
    except (ValueError, RecursionError):
        return False
    def personal_fields(value, depth=0):
        if depth > 25:
            return True
        if isinstance(value, dict):
            return any(re.sub(r'[^a-z0-9]', '', key.lower()) in {'email', 'emailaddress', 'resume', 'resumefile', 'password', 'firstname', 'lastname', 'phone', 'phonenumber', 'coverletter', 'consent'} or personal_fields(child, depth+1) for key, child in value.items())
        if isinstance(value, list):
            return any(personal_fields(child, depth+1) for child in value)
        return False
    if not isinstance(payload, dict) or personal_fields(payload):
        return False
    if path.endswith('/graphql') or urlsplit(url).hostname == 'graphql.contentful.com':
        query = payload.get('query')
        return isinstance(query, str) and bool(re.match(r'\s*(?:query\b|\{)', query)) and not re.search(r'\bmutation\b', query)
    return bool(re.search(r'(?:^|/)(?:search|search-jobs|searchjobs)(?:/|$)', path) or
                path == '/services/recruiting/v1/jobs' and not payload.get('rcmCandidateId') and not payload.get('alertId') or
                re.fullmatch(r'/api/v\d+/public/(?:supplier/)?config/job/filters',path) or
                re.fullmatch(r'/wday/cxs/[^/]+/[^/]+/jobs', path) or
                re.fullmatch(r'/1/indexes/[^/]+/queries?', path) or
                path == '/multi_search' and isinstance(payload.get('searches'), list))


async def render_public_page(url: str) -> tuple[str, str]:
    from playwright.async_api import async_playwright

    await validate_public_url(url)
    if os.environ.get('MERIDIAN_TEST'):
        raise ValueError('No readable public job collection found; public browser fallback requires an explicit browser integration test')
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(service_workers='block', accept_downloads=False)
            allowed_hosts = set()
            count = 0
            request_diagnostics = []

            async def route(request_route):
                nonlocal count
                request = request_route.request
                count += 1
                if count > 200 or request.resource_type in ('image', 'media', 'font'):
                    await request_route.abort()
                    return
                parsed = urlsplit(request.url)
                try:
                    body = request.post_data
                except UnicodeDecodeError:
                    # Binary analytics payloads are never job searches. Decode
                    # failures must not strand an intercepted browser request.
                    await request_route.abort()
                    return
                readable = public_search_request(request.method, request.url, body)
                if request.resource_type in ('fetch', 'xhr') and len(request_diagnostics) < 80:
                    # Paths and allow/block decisions only; never request bodies,
                    # cookies, headers, credentials or query values.
                    request_diagnostics.append({'method': request.method, 'url': urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')), 'allowed': readable})
                if parsed.scheme not in ('https', 'http') or not readable:
                    await request_route.abort()
                    return
                try:
                    if parsed.netloc not in allowed_hosts:
                        await validate_public_url(request.url)
                        allowed_hosts.add(parsed.netloc)
                    await request_route.continue_()
                except Exception:
                    with suppress(Exception):
                        await request_route.abort()

            await context.route('**/*', route)
            page = await context.new_page()
            response_tasks = set()
            public_jobs = []
            public_searches = []
            async def capture(response):
                if len(public_jobs) >= 2000:
                    return
                if response.request.resource_type not in ('fetch', 'xhr') or 'json' not in response.headers.get('content-type', ''):
                    return
                try:
                    if not public_search_request(response.request.method, response.url, response.request.post_data):
                        return
                    if int(response.headers.get('content-length', 0)) > MAX_BODY:
                        return
                    body = await response.body()
                    if len(body) <= MAX_BODY:
                        data = json.loads(body)
                        records = response_job_records(data)
                        public_jobs.extend(records)
                        if records or isinstance(data,dict) and isinstance(data.get('jobSearchResult'),list) or re.search(r'search|queries|multi_search|graphql', urlsplit(response.url).path, re.I):
                            parsed = urlsplit(response.url)
                            params = [(k, v) for k, v in parse_qsl(parsed.query) if k in ('domain', 'start', 'num', 'sort_by', 'query', 'location', 'finder', 'onlyData', 'expand', 'offset', 'limit')]
                            entry = {'method': response.request.method, 'url': urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), '')),
                                                    'keys': list(data.keys()) if isinstance(data, dict) else [],
                                                    'published_total': data.get('count') or data.get('total') or data.get('total_count') if isinstance(data, dict) else None,
                                                    'response_status': response.status,
                                                    'sample_keys': list(data['results'][0].keys()) if isinstance(data, dict) and isinstance(data.get('results'), list) and data['results'] and isinstance(data['results'][0], dict) else []}
                            if parsed.path == '/services/recruiting/v1/jobs' and isinstance(data,dict) and isinstance(data.get('jobSearchResult'),list):
                                filters = json.loads(response.request.post_data or '{}')
                                allowed = {'locale','pageNumber','sortBy','keywords','location','facetFilters','brand','skills','categoryId','alertId','rcmCandidateId'}
                                if set(filters) <= allowed and filters.get('pageNumber') == 0:
                                    entry['public_filters'] = filters
                                    entry['first_page'] = data
                            public_searches.append(entry)
                except Exception:
                    pass
            def receive(response):
                import asyncio
                if len(response_tasks) < 40:
                    task = asyncio.create_task(capture(response))
                    response_tasks.add(task)
                    task.add_done_callback(response_tasks.discard)
            page.on('response', receive)
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
            except Exception as error:
                code = re.search(r'net::[A-Z0-9_]+',str(error))
                raise ValueError('Public browser navigation failed: '+(code.group() if code else type(error).__name__)) from None
            if response and response.status >= 400:
                raise ValueError(f'Public browser navigation returned HTTP {response.status}')
            # Allow the page's initial job search to finish; no control is clicked.
            try:
                await page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            if response_tasks:
                import asyncio
                try:
                    async with asyncio.timeout(5):
                        await asyncio.gather(*tuple(response_tasks), return_exceptions=True)
                except TimeoutError:
                    for task in tuple(response_tasks):
                        task.cancel()
            # Job JSON can finish while its response task is being collected.
            # Read the DOM afterward so its real posting links are available to
            # resolve records that publish IDs rather than absolute URLs.
            text = await page.content()
            if public_jobs:
                payload = json.dumps({'jobs': public_jobs[:2000]}, ensure_ascii=False).replace('</', '<\\/')
                text += '<script type="application/json" id="meridian-public-search-data">'+payload+'</script>'
            successfactors = next((entry for entry in public_searches if entry.get('public_filters') is not None),None)
            if successfactors:
                from .successfactors import discover_public_jobs
                async def public_page(endpoint,filters):
                    if not public_search_request('POST',endpoint,json.dumps(filters)):
                        raise ValueError('Public pagination request is not an anonymous search')
                    # Keep the employer's anonymous CSRF context inside this fresh
                    # browser. No token, cookies or headers leave it or are saved.
                    result = await page.evaluate('''async ({endpoint,filters,maximum}) => {
                        const response = await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':window.CSRFToken || ''},body:JSON.stringify(filters)});
                        if (!response.ok) return {error:'Public pagination returned HTTP '+response.status};
                        if (Number(response.headers.get('content-length') || 0)>maximum) return {error:'Public pagination response exceeds the download limit'};
                        const text=await response.text();
                        if (new TextEncoder().encode(text).length>maximum) return {error:'Public pagination response exceeds the download limit'};
                        return {data:JSON.parse(text)};
                    }''',{'endpoint':endpoint,'filters':filters,'maximum':MAX_BODY})
                    if result.get('error'):
                        raise ValueError(result['error'])
                    return result['data']
                collection = await discover_public_jobs(successfactors,text,page.url,{},fetch_page=public_page)
                payload = json.dumps({'jobs':list(collection),'evidence':collection.evidence},ensure_ascii=False).replace('</','<\\/')
                text += '<script type="application/json" id="meridian-public-paginated-collection">'+payload+'</script>'
            if public_searches:
                payload = json.dumps(public_searches).replace('</', '<\\/')
                text += '<script type="application/json" id="meridian-public-search-endpoints">'+payload+'</script>'
            if request_diagnostics:
                payload = json.dumps(request_diagnostics).replace('</', '<\\/')
                text += '<script type="application/json" id="meridian-public-request-diagnostics">'+payload+'</script>'
            if len(text.encode()) > MAX_BODY:
                raise ValueError('Rendered public career page exceeds the download limit')
            title = (await page.title()).lower()
            if any(phrase in title for phrase in ('just a moment', 'access denied', 'verify you are human')):
                raise ValueError('Public career page requires human access verification')
            return text, page.url
        finally:
            await browser.close()
