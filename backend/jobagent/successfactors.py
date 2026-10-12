"""Anonymous SuccessFactors searches observed on the employer's public page."""
import json
import re
from urllib.parse import urljoin,urlsplit

import httpx

from .discovery import DiscoveryResult,MAX_BODY,MAX_JOBS,validate_public_url
from .public_renderer import public_search_request


def published_template(data,text,page_url,locale):
    from .career_pages import parse_html
    anchors = [urljoin(page_url,a['href']) for a in parse_html(text).select('a[href]')]
    for item in data.get('jobSearchResult',[]):
        record = item.get('response',{})
        slug,identifier = record.get('unifiedUrlTitle'),str(record.get('id',''))
        if not isinstance(slug,str) or not identifier:
            continue
        suffix = f'/{slug}/{identifier}-{locale}'
        for anchor in anchors:
            if urlsplit(anchor).hostname == urlsplit(page_url).hostname and urlsplit(anchor).path.endswith(suffix):
                return anchor[:-len(suffix)]
    raise ValueError('Published job search has no matching real job URL template')


def parse_jobs(data,prefix,locale,company):
    rows = data.get('jobSearchResult')
    if not isinstance(rows,list):
        raise ValueError('Published job search has no job collection')
    result = []
    for item in rows:
        record = item.get('response',{}) if isinstance(item,dict) else {}
        identifier = str(record.get('id',''))
        slug,title = record.get('unifiedUrlTitle'),record.get('unifiedStandardTitle')
        if not re.fullmatch(r'\d+',identifier) or not isinstance(slug,str) or not re.fullmatch(r'[A-Za-z0-9%_.(),&;-]+',slug) or not isinstance(title,str) or not title.strip():
            raise ValueError('Published job search includes an invalid posting identity, title or URL slug')
        locations = record.get('jobLocationShort') or []
        result.append({'title':title,'company':company,'url':f'{prefix}/{slug}/{identifier}-{locale}',
                       'external_id':identifier,'location':'; '.join(locations) if isinstance(locations,list) else str(locations),
                       'metadata':{'discovery_evidence':'Published SuccessFactors search','description_state':'NOT_PUBLISHED_IN_LISTING',
                                   'posting_end':record.get('unifiedStandardEnd')}})
    return result


async def discover_public_jobs(observed,text,page_url,config,fetch_page=None):
    endpoint,filters,data = observed['url'],dict(observed['public_filters']),observed['first_page']
    if urlsplit(endpoint).hostname != urlsplit(page_url).hostname or not public_search_request('POST',endpoint,json.dumps(filters)):
        raise ValueError('Published job search must be anonymous and on the employer portal')
    locale = filters.get('locale','')
    if not isinstance(locale,str) or not re.fullmatch(r'[a-z]{2}_[A-Z]{2}',locale) or filters.get('pageNumber') != 0:
        raise ValueError('Published search requires an observed first page and locale')
    total = data.get('totalJobs')
    if not isinstance(total,int) or isinstance(total,bool) or total < 0:
        raise ValueError('Published job search has no valid total')
    if total == 0:
        # A category is a subset of the company's entire career source.
        return DiscoveryResult([],complete=not filters.get('categoryId'),authoritative=True,method='Published SuccessFactors search',endpoints=[endpoint],total=0,
                               limitations=['Category search does not cover all company jobs'] if filters.get('categoryId') else [])
    prefix = published_template(data,text,page_url,locale)
    company = config.get('company_name') or config.get('company') or ''
    jobs = parse_jobs(data,prefix,locale,company)
    maximum = min(MAX_JOBS,max(1,int(config.get('max_jobs',MAX_JOBS))))
    seen = {job['external_id'] for job in jobs}
    limitations = ['Descriptions are not published in board listings']
    page_number = 1
    await validate_public_url(endpoint)
    async with httpx.AsyncClient(timeout=25,trust_env=False) as client:
        while len(jobs) < min(total,maximum):
            if page_number >= 200:
                limitations.append('Public search page limit reached')
                break
            try:
                if fetch_page:
                    data = await fetch_page(endpoint,filters|{'pageNumber':page_number})
                else:
                    async with client.stream('POST',endpoint,json=filters|{'pageNumber':page_number}) as response:
                        response.raise_for_status()
                        content=bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content)>MAX_BODY:
                                raise ValueError('Public search response exceeds the download limit')
                    data = json.loads(content)
                if data.get('totalJobs') != total:
                    raise ValueError('Published job total changed during pagination')
                page = parse_jobs(data,prefix,locale,company)
                ids = {job['external_id'] for job in page}
                if not page or len(ids)!=len(page) or ids <= seen:
                    raise ValueError('Published pagination ended early or repeated postings')
                jobs.extend(job for job in page if job['external_id'] not in seen)
                seen.update(ids)
                page_number += 1
            except (httpx.HTTPError,ValueError) as error:
                limitations.append('Pagination stopped: '+str(error)[:200])
                break
    jobs=jobs[:maximum]
    scoped = bool(filters.get('categoryId'))
    if scoped:
        limitations.append('Category search does not cover all company jobs')
    if len(jobs)<total:
        limitations.append('Published search is incomplete')
    return DiscoveryResult(jobs,complete=not scoped and len(jobs)==total,authoritative=True,method='Published SuccessFactors search',endpoints=[endpoint,page_url],total=total,limitations=limitations)
