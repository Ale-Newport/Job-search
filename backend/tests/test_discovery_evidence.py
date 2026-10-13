import json

import pytest

from jobagent.career_pages import board_references, embedded_jobs, page_links
from jobagent.db import Database, dumps, now
from jobagent.discovery import DiscoveryResult, discover, ingest_job, parse_jsonld, run_sources, source_health


@pytest.mark.parametrize('failure,retry', [('net::ERR_HTTP2_PROTOCOL_ERROR', True),
    ('Public browser navigation returned HTTP 403', False), ('net::ERR_CERT_DATE_INVALID', False)])
async def test_public_render_protocol_retry_preserves_access_and_certificate_boundaries(monkeypatch, failure, retry):
    from jobagent import public_renderer
    calls = []
    async def render(url, *, http1=False):
        calls.append((url, http1))
        if not http1:
            raise ValueError(failure)
        return '<a href="/jobs/1">Job</a>', url
    monkeypatch.setattr(public_renderer, '_render_public_page', render)
    if retry:
        assert await public_renderer.render_public_page('https://example.test/careers') == ('<a href="/jobs/1">Job</a>', 'https://example.test/careers')
        assert calls == [('https://example.test/careers', False), ('https://example.test/careers', True)]
    else:
        with pytest.raises(ValueError, match=failure):
            await public_renderer.render_public_page('https://example.test/careers')
        assert len(calls) == 1

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'discovery.sqlite3')


def add_source(db, identifier='board', kind='greenhouse'):
    db.execute('INSERT INTO sources(id,name,kind,url,config,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
               (identifier, 'Synthetic public board', kind, 'https://boards.greenhouse.io/fixture', dumps({'board': 'fixture'}), now(), now()))


async def test_documented_publisher_boundary_is_not_an_empty_success_or_a_network_retry(db, monkeypatch):
    from jobagent.discovery import PublisherPermissionRequired
    async def forbidden_fetch(*args, **kwargs):
        pytest.fail('A restricted source must not issue network requests without recorded authorization')
    monkeypatch.setattr('jobagent.discovery.fetch', forbidden_fetch)
    add_source(db)
    config = {'board': 'fixture', 'access_policy': {'automation_requires_written_permission': True,
                                                  'evidence_url': 'https://example.test/robots.txt'}}
    db.execute('UPDATE sources SET config=?', (dumps(config),))
    with pytest.raises(PublisherPermissionRequired):
        await discover({'kind': 'greenhouse', 'url': 'https://boards.greenhouse.io/fixture', 'config': config})
    result = await run_sources(db)
    assert result['items'][0]['state'] == 'ACCESS_DENIED'
    assert result['items'][0]['evidence'].get('complete') is not True
    assert db.one('SELECT state FROM source_scans')['state'] == 'FAILED'
    assert 'written permission' in source_health(db, db.one('SELECT * FROM sources'))['last_error']


async def test_recorded_publisher_permission_allows_only_the_existing_public_reader(monkeypatch):
    async def public_feed(url, **kwargs):
        return b'{"jobs":[]}', 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', public_feed)
    result = await discover({'kind':'json','url':'https://example.test/jobs.json','config':{
        'access_policy': {'automation_requires_written_permission': True,
                          'written_permission_evidence': 'Recorded synthetic permission for this public feed'}}})
    assert result == [] and result.evidence['complete'] is True


def test_published_workday_locale_and_board_references_are_generic():
    text = '<a href="https://fixture.wd3.myworkdayjobs.com/en-us/External/login">Jobs</a><a href="https://jobs.smartrecruiters.com/Fixture">Jobs</a>'
    refs = board_references(text, 'https://example.test/careers')
    assert [(s['kind'], s['config']['board']) for s in refs] == [('workday', 'External'), ('smartrecruiters', 'Fixture')]
    assert not board_references('We use Greenhouse to hire engineers', 'https://example.test/careers')
    assert page_links('<a href="/cn/social-recruitment">社会招聘</a>','https://example.test')[0] == ['https://example.test/cn/social-recruitment']


def test_hydrated_collection_uses_a_verified_listing_template():
    data = {'props': {'pageProps': {'jobIndexData': {'filters': {'locations': [{'name': 'London'}]},
            'listings': [{'title': 'Data Engineer', 'slug': 'data-engineer', 'greenhouseId': 123, 'locationIndices': [0]},
                         {'title': 'Backend Engineer', 'slug': 'backend-engineer', 'greenhouseId': 456}]}}}}
    text = '<a href="/careers/listing/data-engineer/123">Data Engineer</a><script type="application/json">'+json.dumps(data)+'</script>'
    jobs = embedded_jobs(text, 'https://example.test/careers/search', 'Fixture')
    assert len(jobs) == 2 and jobs[0]['location'] == 'London'
    assert jobs[1]['url'] == 'https://example.test/careers/listing/backend-engineer/456'
    assert not embedded_jobs('<script type="application/json">'+json.dumps(data)+'</script>', 'https://example.test', 'Fixture')


def test_nuxt_hydration_and_cycles_are_bounded():
    data = [{'jobs': 1}, [2], {'title': 3, 'url': 4}, 'Backend Engineer', '/vacancies/backend-engineer']
    jobs = embedded_jobs('<script id="__NUXT_DATA__" type="application/json">'+json.dumps(data)+'</script>', 'https://example.test', 'Fixture')
    assert jobs[0]['title'] == 'Backend Engineer'
    assert embedded_jobs('<script id="__NUXT_DATA__" type="application/json">[{"self":0}]</script>', 'https://example.test', 'Fixture') == []


def test_listing_navigation_and_login_are_not_jobs():
    searches, jobs = page_links('<a href="/vacancies">Search jobs</a><a href="/job-offers/backend-123">Backend Engineer</a><a href="/jobs/privacy">Privacy</a><a href="/jobs/login">Sign in</a>', 'https://example.test')
    assert searches == ['https://example.test/vacancies']
    assert jobs == [{'title': 'Backend Engineer', 'url': 'https://example.test/job-offers/backend-123'}]
    assert page_links('<a href="/jobs/results">work_outline Jobs Jobs</a><a href="/jobs/legal/rights.pdf">Know your rights</a><a href="/jobs/students">Students</a>', 'https://example.test')[1] == []
    assert page_links('<a href="/en-gb/details/123456/early-career-engineer">Early Career Engineer</a>', 'https://example.test')[1] == [
        {'title':'Early Career Engineer','url':'https://example.test/en-gb/details/123456/early-career-engineer'}]
    assert not page_links('<a href="/blog/detail/123456">Meet our graduate software engineers</a>','https://example.test')[1]


def test_anonymous_public_job_filter_reads_do_not_allow_candidate_data_or_mutations():
    from jobagent.public_renderer import public_search_request
    url = 'https://example.test/api/v1/public/supplier/config/job/filters'
    assert public_search_request('POST',url,'{"locale":"en-GB"}')
    assert not public_search_request('POST',url,'{"email":"candidate@example.test"}')
    assert not public_search_request('POST',url+'/update','{}')


def test_published_category_navigation_precedes_alert_login_links():
    searches,jobs = page_links('<a href="/search/?jobAlerts=true&amp;rcmLoggedIn=true">Jobs</a><a href="/go/Experienced-Professional-jobs/123456/"></a>', 'https://example.test')
    assert searches[0] == 'https://example.test/go/Experienced-Professional-jobs/123456/'
    assert jobs == []


def test_successfactors_anonymous_list_requests_exclude_account_and_alert_context():
    from jobagent.public_renderer import public_search_request
    url = 'https://example.test/services/recruiting/v1/jobs'
    payload = {'locale':'en_GB','pageNumber':0,'categoryId':123456,'rcmCandidateId':'','alertId':''}
    assert public_search_request('POST',url,json.dumps(payload))
    assert not public_search_request('POST',url,json.dumps({**payload,'rcmCandidateId':'private-id'}))
    assert not public_search_request('POST',url,json.dumps({**payload,'alertId':'private-alert'}))
    assert not public_search_request('POST',url,json.dumps({**payload,'email':'private@example.test'}))
    assert not public_search_request('POST',url+'/apply',json.dumps(payload))


def test_cms_navigation_is_not_a_hydrated_job():
    data = {'vacanciesPage': {'navigation': [{'title': 'Job openings at Fixture', 'url': '/vacancies'}]}}
    assert embedded_jobs('<script type="application/json">'+json.dumps(data)+'</script>', 'https://example.test', 'Fixture') == []


def test_react_streamed_role_collection_is_data_not_executed_code():
    role = {'openRoles': [{'fields': {'name': 'Careers: Open Roles: Data Engineer', 'slug': 'data-engineer',
            'blocks': [{'fields': {'applyLink': 'https://example.test/application/123', 'location': ['London']}}]}}]}
    payload = 'a:'+json.dumps(role)+'\n'
    text = '<script>self.__next_f.push([1,'+json.dumps(payload)+'])</script>'
    jobs = embedded_jobs(text, 'https://example.test/careers', 'Fixture')
    assert len(jobs) == 1 and jobs[0]['title'] == 'Data Engineer'


@pytest.mark.parametrize('method,path,body,allowed', [
    ('GET', '/jobs/search', None, True),
    ('POST', '/api/search/jobs', '{"keyword":"engineer"}', True),
    ('POST', '/wday/cxs/fixture/External/jobs', '{"offset":0,"limit":20}', True),
    ('POST', '/jobs/apply', '{}', False),
    ('POST', '/api/search/jobs', '{"email":"private@example.test"}', False),
    ('POST', '/api/jobs', '{}', False),
    ('DELETE', '/jobs/123', None, False),
    ('POST', '/graphql', '{"query":"query Jobs { jobs { title } }"}', True),
    ('POST', '/graphql', '{"query":"mutation Apply { apply }"}', False),
])
def test_public_renderer_only_allows_job_search_reads(method, path, body, allowed):
    from jobagent.public_renderer import public_search_request
    assert public_search_request(method, 'https://example.test'+path, body) is allowed


@pytest.mark.asyncio
async def test_public_browser_fallback_reads_html_and_closes_its_isolated_session(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from jobagent.public_renderer import render_public_page
    monkeypatch.delenv('MERIDIAN_TEST', raising=False)
    async def permitted(url):
        assert url == 'https://example.test/careers'
    monkeypatch.setattr('jobagent.public_renderer.validate_public_url', permitted)
    closed = []
    class Page:
        url = 'https://example.test/careers'
        async def goto(self, url, **kwargs):
            return SimpleNamespace(status=200)
        async def wait_for_load_state(self, *a, **k):
            pass
        async def content(self):
            return '<main>Published jobs</main>'
        async def title(self):
            return 'Fixture Careers'
        def on(self, event, handler):
            assert event == 'response'
    class Context:
        async def route(self, pattern, handler):
            assert pattern == '**/*'
        async def new_page(self):
            return Page()
    class Browser:
        async def new_context(self, **kwargs):
            assert kwargs == {'service_workers': 'block', 'accept_downloads': False}
            return Context()
        async def close(self):
            closed.append(True)
    class Chromium:
        async def launch(self, **kwargs):
            assert kwargs == {'channel': 'chrome', 'headless': True}
            return Browser()
    @asynccontextmanager
    async def runtime():
        yield SimpleNamespace(chromium=Chromium())
    monkeypatch.setattr('playwright.async_api.async_playwright', runtime)
    html, url = await render_public_page('https://example.test/careers')
    assert html == '<main>Published jobs</main>' and url == Page.url and closed == [True]


@pytest.mark.asyncio
async def test_workday_keeps_first_page_total_when_later_pages_report_zero(monkeypatch):
    import httpx
    async def permitted(url):
        return None
    monkeypatch.setattr('jobagent.discovery.validate_public_url', permitted)
    offsets = []
    def respond(request):
        payload = json.loads(request.content)
        offset = payload['offset']
        offsets.append(offset)
        count = min(payload['limit'], 55-offset)
        return httpx.Response(200, json={'total': 55 if offset == 0 else 0,
                              'jobPostings': [{'title': 'Engineer', 'externalPath': '/job/'+str(offset+i)} for i in range(count)]})
    original = httpx.AsyncClient
    monkeypatch.setattr('jobagent.discovery.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    jobs = await discover({'kind': 'workday', 'url': 'https://fixture.wd1.myworkdayjobs.com/en-us/External', 'config': {}})
    assert len(jobs) == 55 and offsets == [0, 20, 40]
    assert jobs.evidence['complete'] and jobs.evidence['published_total'] == 55


def test_jsonld_nullable_salary_is_valid():
    jobs = parse_jsonld('<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer","baseSalary":{"value":null}}</script>', 'https://example.test/jobs/123')
    assert jobs[0]['salary_min'] is None


def test_daily_scheduler_retries_a_failed_cycle_once_after_cooldown(db):
    from datetime import datetime, timedelta, timezone
    from jobagent.main import daily_discovery_should_enqueue
    at = datetime.now(timezone.utc)
    key = 'discovery:synthetic-day'
    assert daily_discovery_should_enqueue(db, key, at=at)
    def insert(identifier, state, minutes):
        stamp = (at-timedelta(minutes=minutes)).isoformat()
        db.execute('INSERT INTO automation_tasks(id,kind,state,dedupe_key,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                   (identifier, 'discovery', state, key, stamp, stamp))
    insert('first', 'FAILED', 31)
    assert daily_discovery_should_enqueue(db, key, at=at)
    insert('second', 'QUEUED', 1)
    assert not daily_discovery_should_enqueue(db, key, at=at)
    db.execute("UPDATE automation_tasks SET state='FAILED',updated_at=? WHERE id='second'", ((at-timedelta(minutes=31)).isoformat(),))
    assert not daily_discovery_should_enqueue(db, key, at=at)


@pytest.mark.asyncio
async def test_malformed_public_board_never_closes_existing_jobs(db, monkeypatch):
    add_source(db)
    job, _ = ingest_job(db, {'title': 'Engineer', 'company': 'Fixture', 'url': 'https://boards.greenhouse.io/fixture/jobs/123'}, 'board')
    db.execute("UPDATE jobs SET availability='OPEN'")
    async def fetch(*a, **k):
        return b'{"error":"unexpected response"}', 'application/json', a[0]
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    result = await run_sources(db)
    assert result['items'][0]['state'] == 'FAILED'
    assert db.one('SELECT availability FROM jobs WHERE id=?', (job['id'],))['availability'] == 'OPEN'
    assert source_health(db, db.one('SELECT * FROM sources'))['health'] == 'FAILED'


@pytest.mark.asyncio
async def test_complete_empty_board_and_partial_board_are_distinct(db, monkeypatch):
    add_source(db)
    job, _ = ingest_job(db, {'title': 'Engineer', 'company': 'Fixture', 'url': 'https://boards.greenhouse.io/fixture/jobs/123'}, 'board')
    db.execute("UPDATE jobs SET availability='OPEN'")
    async def partial(_):
        return DiscoveryResult([], authoritative=True, complete=False, limitations=['limit reached'])
    monkeypatch.setattr('jobagent.discovery.discover', partial)
    assert (await run_sources(db))['items'][0]['state'] == 'PARTIAL'
    assert db.one('SELECT availability FROM jobs')['availability'] == 'OPEN'
    async def empty(_):
        return DiscoveryResult([], authoritative=True, complete=True, method='Verified public API', total=0)
    monkeypatch.setattr('jobagent.discovery.discover', empty)
    assert (await run_sources(db))['items'][0]['state'] == 'EMPTY'
    assert db.one('SELECT availability FROM jobs WHERE id=?', (job['id'],))['availability'] == 'CLOSED'


@pytest.mark.asyncio
async def test_one_empty_board_does_not_close_a_job_on_another_current_board(db, monkeypatch):
    add_source(db, 'first')
    add_source(db, 'second')
    raw = {'title': 'Engineer', 'company': 'Fixture', 'url': 'https://boards.greenhouse.io/fixture/jobs/123'}
    async def first(_):
        return DiscoveryResult([raw], authoritative=True, complete=True)
    monkeypatch.setattr('jobagent.discovery.discover', first)
    await run_sources(db)
    async def second(source):
        return DiscoveryResult([] if source['id']=='first' else [raw], authoritative=True, complete=True)
    monkeypatch.setattr('jobagent.discovery.discover', second)
    await run_sources(db)
    assert db.one('SELECT availability FROM jobs')['availability'] == 'OPEN'
    assert db.one('SELECT count(*) AS n FROM source_job_observations')['n'] == 2


@pytest.mark.asyncio
async def test_two_hop_official_navigation_reaches_public_ats(monkeypatch):
    async def fetch(url, **kwargs):
        pages = {'https://example.test/careers': '<a href="/careers/open-roles">Open roles</a>',
                 'https://example.test/careers/open-roles': '<a href="https://jobs.ashbyhq.com/fixture">Jobs</a>',
                 'https://api.ashbyhq.com/posting-api/job-board/fixture?includeCompensation=true': json.dumps({'jobs': [{'title': 'Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/fixture/123'}]})}
        return pages[url].encode(), 'text/html', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'careers', 'url': 'https://example.test/careers', 'config': {'company_name': 'Fixture'}})
    assert len(jobs) == 1 and jobs[0]['company'] == 'Fixture'
    assert jobs.evidence['complete'] and jobs.evidence['authoritative']
    assert jobs.evidence['endpoints'][-1].startswith('https://api.ashbyhq.com')


@pytest.mark.asyncio
async def test_repeated_lever_page_is_a_failure_not_a_complete_board(monkeypatch):
    body = json.dumps([{'id': str(i), 'text': 'Engineer', 'hostedUrl': 'https://jobs.lever.co/fixture/'+str(i)} for i in range(100)]).encode()
    async def fetch(url, **kwargs):
        return body, 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    with pytest.raises(ValueError, match='repeated'):
        await discover({'kind': 'lever', 'url': 'https://jobs.lever.co/fixture', 'config': {}})


@pytest.mark.parametrize('kind,body,config', [
    ('trackr', '{"programmes":[]}', {}),
    ('github', '| Company | Position | Apply |\n|---|---|---|\n', {'repository': 'fixture/jobs'}),
    ('csv', 'title,company,url\n', {}),
])
@pytest.mark.asyncio
async def test_recognizable_empty_feeds_are_empty_not_failures(monkeypatch, kind, body, config):
    async def fetch(url, **kwargs):
        return body.encode(), 'text/plain', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': kind, 'url': 'https://example.test/jobs', 'config': config})
    assert jobs == [] and jobs.evidence['complete']


@pytest.mark.asyncio
async def test_duplicate_postings_do_not_inflate_new_or_distinct_jobs(db, monkeypatch):
    add_source(db)
    raw = {'title': 'Engineer', 'company': 'Fixture', 'url': 'https://boards.greenhouse.io/fixture/jobs/123'}
    async def repeated(_):
        return DiscoveryResult([raw, raw], complete=True, authoritative=True)
    monkeypatch.setattr('jobagent.discovery.discover', repeated)
    result = (await run_sources(db))['items'][0]
    assert result['jobs_found'] == 1 and result['new_jobs'] == 1 and result['duplicates'] == 1


@pytest.mark.asyncio
async def test_workable_public_feed_and_detail_use_the_same_job_identity(monkeypatch):
    async def fetch(url, **kwargs):
        if '/widget/' in url:
            data = {'name': 'Fixture', 'jobs': [{'title': 'Backend Engineer', 'shortcode': 'ABCD1234', 'url': 'https://apply.workable.com/j/ABCD1234', 'city': 'London'}]}
        else:
            assert url == 'https://apply.workable.com/api/v2/accounts/fixture/jobs/ABCD1234'
            data = {'shortcode': 'ABCD1234', 'state': 'published', 'isInternal': False, 'description': 'Build Python services.', 'requirements': 'Python', 'benefits': ''}
        return json.dumps(data).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'workable', 'url': 'https://apply.workable.com/fixture', 'config': {}})
    assert jobs[0]['description'] == 'Build Python services. Python '
    assert jobs.evidence['complete'] and jobs.evidence['authoritative']


@pytest.mark.asyncio
async def test_configured_published_feed_only_uses_ids_on_the_public_directory(monkeypatch):
    async def fetch(url, **kwargs):
        data = ['123'] if url.endswith('directory.json') else [{'id': 123, 'position': 'Engineer', 'city': 'London'}, {'id': 456, 'position': 'Unlisted Engineer'}]
        return json.dumps(data).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'careers', 'url': 'https://example.test/careers', 'config': {'company_name': 'Fixture', 'public_job_feed':
                          {'url': 'https://example.test/jobs.json', 'listed_ids_url': 'https://example.test/directory.json', 'url_template': 'https://example.test/positions/{id}', 'title_key': 'position', 'location_key': 'city'}}})
    assert len(jobs) == 1 and jobs[0]['url'] == 'https://example.test/positions/123'
    assert jobs.evidence['complete'] and jobs.evidence['authoritative']


@pytest.mark.asyncio
async def test_observed_eightfold_api_pages_until_the_published_total(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    starts = []
    async def fetch(url, **kwargs):
        offset = int(parse_qs(urlsplit(url).query)['start'][0])
        starts.append(offset)
        posts = [{'id': str(i), 'name': 'Backend Engineer', 'locations': ['London']} for i in range(offset, min(offset+2, 5))]
        return json.dumps({'status': 200, 'error': {'message':'','body':''}, 'data': {'positions': posts, 'count': 5}}).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'eightfold', 'url': 'https://example.test/api/pcsx/search?domain=fixture.test&start=0', 'config': {'company_name': 'Fixture'}})
    assert len(jobs) == 5 and starts == [0, 2, 4] and jobs.evidence['complete']
    assert jobs[0]['url'] == 'https://example.test/careers?domain=fixture.test&pid=0'


def test_rendered_search_data_does_not_promote_location_facets_to_jobs():
    from jobagent.public_renderer import response_job_records
    records = response_job_records({'data': {'positions': [{'id': '123', 'name': 'Engineer', 'locations': ['London']}],
                                           'filterDef': {'positions': {'locations': [{'id': '456', 'name': 'London'}]}}}})
    assert len(records) == 1 and records[0]['title'] == 'Engineer'


def test_location_links_on_job_cards_use_the_card_heading():
    text = '<ul><li><h3>Analytics Engineering Team Lead</h3><nav><a href="https://apply.workable.com/j/01018F02BC">London</a><a href="https://apply.workable.com/j/01018F02BC">Southampton</a></nav></li></ul>'
    jobs = page_links(text, 'https://example.test/careers')[1]
    assert jobs == [{'title': 'Analytics Engineering Team Lead', 'url': 'https://apply.workable.com/j/01018F02BC'}]


def test_invalid_marked_declaration_does_not_discard_published_jobs():
    from jobagent.career_pages import parse_html
    soup = parse_html('<![A-Z]><a href="/jobs/123">Software Engineer</a>')
    assert soup.find('a').get_text() == 'Software Engineer'


def test_career_social_link_is_not_a_job():
    text = '<a data-lbl="search-jobs-facebook" href="https://www.facebook.com/140323369373151">Facebook</a>'
    assert page_links(text, 'https://example.test/careers')[1] == []


@pytest.mark.asyncio
async def test_eightfold_ignores_oversized_pages_after_its_configured_limit(monkeypatch):
    async def fetch(url, **kwargs):
        return json.dumps({'data': {'positions': [{'id': str(i), 'name': 'Engineer'} for i in range(10)], 'count': 20}}).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'eightfold', 'url': 'https://example.test/api/pcsx/search?domain=fixture.test', 'config': {'max_jobs': 3}})
    assert len(jobs) == 3 and not jobs.evidence['complete']


async def test_eightfold_changed_total_is_partial_without_claiming_a_posting_cap(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    async def fetch(url, **kwargs):
        offset = int(parse_qs(urlsplit(url).query)['start'][0])
        posts = [{'id': str(i), 'name': 'Engineer'} for i in range(offset, 2)]
        return json.dumps({'data': {'positions': posts, 'count': 3}}).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'eightfold', 'url': 'https://example.test/api/pcsx/search?domain=fixture.test', 'config': {}})
    assert len(jobs) == 2 and not jobs.evidence['complete']
    assert 'published total' in jobs.evidence['limitations'][0]
    assert not any('Posting limit' in limitation for limitation in jobs.evidence['limitations'])


@pytest.mark.asyncio
async def test_published_search_feed_uses_observed_pagination_and_job_paths(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    starts = []
    async def fetch(url, **kwargs):
        start = int(parse_qs(urlsplit(url).query).get('offset', ['0'])[0])
        starts.append(start)
        data = {'hits': 5, 'jobs': [{'id': str(i), 'title': 'Engineer', 'job_path': '/jobs/'+str(i)} for i in range(start, min(start+2, 5))]}
        return json.dumps(data).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    config = {'company_name': 'Fixture', 'public_job_feed': {'url': 'https://example.test/search.json', 'collection_key': 'jobs', 'url_key': 'job_path',
             'pagination': {'offset_param': 'offset', 'limit_param': 'result_limit', 'total_key': 'hits'}}}
    jobs = await discover({'kind': 'careers', 'url': 'https://example.test', 'config': config})
    assert len(jobs) == 5 and starts == [0, 2, 4] and jobs.evidence['complete']
    assert jobs[0]['url'] == 'https://example.test/jobs/0'


def test_search_destinations_in_react_props_are_read_without_running_scripts():
    text = '<div data-react-props=\''+json.dumps({'searchInputProps': {'action': '/en/search'}})+'\'></div>'
    assert page_links(text, 'https://example.test')[0] == ['https://example.test/en/search']


def test_availability_reconciliation_uses_an_indexed_job_lookup(tmp_path):
    from jobagent.db import Database
    db = Database(tmp_path/'indexed.sqlite3')
    plan = db.query("EXPLAIN QUERY PLAN SELECT 1 FROM source_job_observations WHERE job_id=? AND state='OPEN' AND observed_at>=?", ('job', '2026-01-01'))
    assert any('idx_source_observations_job_state' in row['detail'] for row in plan)


@pytest.mark.asyncio
@pytest.mark.parametrize('html,expected', [
    ('<main>Explore all our careers</main><footer>Apply now</footer>', 'UNKNOWN'),
    ('<main>Backend Engineer <button>Apply now</button></main>', 'OPEN'),
    ('<main>This job posting is no longer available.</main>', 'CLOSED'),
    ('<main>Backend Engineer <button>Apply now</button></main><footer>Archived positions are no longer accepting applications</footer>', 'OPEN'),
])
async def test_job_verification_scopes_open_and_closed_evidence(monkeypatch, html, expected):
    from jobagent.job_validity import verify_job
    class DB:
        def execute(self, *_):
            pass
    async def fetch(url):
        return html.encode(), 'text/html', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    assert await verify_job(DB(), {'id': 'job', 'title': 'Backend Engineer', 'url': 'https://example.test/job'}) == expected


def test_posting_validity_does_not_invent_an_application_deadline():
    from jobagent.job_validity import deadline_evidence
    evidence = deadline_evidence({'metadata': {'valid_through': '2126-01-01'}})
    assert evidence['application_deadline'] is None and evidence['deadline_state'] == 'POSTING_VALIDITY'
    assert deadline_evidence({'description': 'Closing date: 15 October 2027', 'metadata': {'valid_through': '2126-01-01'}})['deadline_state'] == 'EXPLICIT_DEADLINE'


def test_upgrade_corrects_proven_legacy_validity_dates_and_retains_real_deadlines(db):
    values = []
    for index in range(3):
        job,_ = ingest_job(db,{'title':'Engineer','company':'Fixture','url':f'https://example.test/jobs/{index}'})
        metadata = {'valid_through':'2126-01-01T00:00:00+00:00'}
        if index==1:
            metadata['application_deadline']='2126-01-01T00:00:00+00:00'
        description = 'Closing date: 1 January 2126' if index==2 else 'Published job details'
        db.execute('UPDATE jobs SET metadata=?,description=?,application_deadline=?,deadline_source=?,deadline_state=? WHERE id=?',
                   (dumps(metadata),description,metadata['valid_through'],job['url'],'EXPLICIT_DEADLINE',job['id']))
        values.append(job['id'])
    db.execute("UPDATE alembic_version SET version_num='0010_discovery_observation_index'")
    Database(db.path)
    corrected = db.one('SELECT * FROM jobs WHERE id=?',(values[0],))
    assert corrected['application_deadline'] is None and corrected['deadline_state']=='POSTING_VALIDITY'
    assert json.loads(corrected['metadata'])['legacy_posting_validity_deadline']=='2126-01-01T00:00:00+00:00'
    assert all(db.one('SELECT application_deadline FROM jobs WHERE id=?',(identifier,))['application_deadline'] for identifier in values[1:])


def test_job_search_terms_do_not_mimic_private_field_names():
    from jobagent.public_renderer import public_search_request
    assert public_search_request('POST', 'https://example.test/search', '{"query":"iPhone software engineer"}')
    assert not public_search_request('POST', 'https://example.test/search', '{"applicant":{"phoneNumber":"123"}}')


@pytest.mark.asyncio
async def test_oracle_alias_uses_observed_api_site_and_actual_page_size(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    offsets = []
    async def fetch(url, **kwargs):
        finder = parse_qs(urlsplit(url).query)['finder'][0]
        assert 'siteNumber=CX_123' in finder and 'sortBy=POSTING_DATES_DESC' in finder
        import re
        offset = int(re.search(r'offset=(\d+)', finder).group(1))
        offsets.append(offset)
        posts = [{'Id': str(i), 'Title': 'Engineer'} for i in range(offset, min(offset+2, 5))]
        return json.dumps({'items': [{'TotalJobsCount': 5, 'requisitionList': posts}]}).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch', fetch)
    jobs = await discover({'kind': 'oraclehcm', 'url': 'https://careers.example.test/en/sites/jobsearch/jobs',
                          'config': {'board': 'jobsearch', 'api_site': 'CX_123', 'api_sort': 'POSTING_DATES_DESC', 'company_name': 'Fixture'}})
    assert offsets == [0, 2, 4] and len(jobs) == 5 and jobs.evidence['complete']
    assert jobs[0]['url'] == 'https://careers.example.test/en/sites/jobsearch/job/0'
def test_explicit_open_roles_can_link_to_an_unfamiliar_employer_portal():
    from jobagent.career_pages import page_links
    searches, jobs = page_links('<a href="https://higher.example/results">Open Roles</a>'
        '<a href="https://linkedin.com/company/example">Open Roles</a>'
        '<a href="https://other.example/research">Research</a>', 'https://employer.example/careers')
    assert searches == ['https://higher.example/results'] and jobs == []


def test_role_detail_links_are_listings_and_not_search_navigation():
    searches,jobs = page_links('<a href="/roles/123456">Software Engineer, London</a>'
                              '<a href="/roles/123457">View role</a>', 'https://portal.example/results')
    assert not searches
    assert [job['url'] for job in jobs] == ['https://portal.example/roles/123456']


@pytest.mark.asyncio
async def test_public_wordpress_jobs_use_published_total_and_nested_fields(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    offsets = []
    async def fetch(url, **kwargs):
        offset = int(parse_qs(urlsplit(url).query).get('offset', ['0'])[0])
        offsets.append(offset)
        if kwargs.get('response_metadata') is not None:
            kwargs['response_metadata']['x-wp-total'] = '3'
        jobs = [{'id':i,'title':{'rendered':'Graduate &amp; Engineer'},'link':f'https://example.test/role/{i}',
                 'meta':{'ats_location':'London'},'content':{'rendered':'<p>Build software</p>'}} for i in range(offset,min(offset+2,3))]
        return json.dumps(jobs).encode(), 'application/json', url
    monkeypatch.setattr('jobagent.discovery.fetch',fetch)
    result = await discover({'kind':'careers','url':'https://example.test/careers','config':{'company_name':'Fixture',
        'public_job_feed':{'url':'https://example.test/wp-json/wp/v2/roles?per_page=2','url_key':'link',
            'title_key':'title.rendered','location_key':'meta.ats_location','description_keys':['content.rendered'],
            'pagination':{'offset_param':'offset','limit_param':'per_page','total_header':'x-wp-total'}}}})
    assert offsets == [0,2]
    assert len(result) == 3 and result.evidence['complete'] and result.evidence['published_total'] == 3
    assert result[0]['location'] == 'London' and result[0]['description'] == '<p>Build software</p>'


def test_anonymous_published_contentful_accepts_only_graphql_reads():
    from jobagent.public_renderer import public_search_request
    url = 'https://graphql.contentful.com/content/v1/spaces/published/environments/master/'
    assert public_search_request('POST',url,json.dumps({'query':'{ jobCollection { title } }'}))
    assert not public_search_request('POST',url,json.dumps({'query':'mutation { saveApplication { id } }'}))
    assert not public_search_request('POST',url,json.dumps({'query':'query { jobCollection { title } }','variables':{'email':'person@example.test'}}))


@pytest.mark.asyncio
async def test_parallel_boards_serialize_sqlite_writes_with_short_busy_timeout(db, monkeypatch):
    import time
    original_connect = db._connect
    def impatient_connection():
        conn = original_connect()
        conn.execute('PRAGMA busy_timeout=20')
        return conn
    monkeypatch.setattr(db,'_connect',impatient_connection)
    for identifier in ['first','second','third','fourth']:
        add_source(db,identifier)
    async def public_board(source):
        return DiscoveryResult([{'title':'Engineer','company':'Fixture','url':'https://example.test/job/'+source['id']}],complete=True,authoritative=True)
    from jobagent import discovery
    original_ingest = discovery.ingest_job
    def slow_write(database, *args, **kwargs):
        # A real writer lock longer than this test connection's wait. Without
        # serialization, another board's metadata/ingestion fails with SQLITE_BUSY.
        with database.transaction():
            time.sleep(.08)
        return original_ingest(database,*args,**kwargs)
    monkeypatch.setattr(discovery,'discover',public_board)
    monkeypatch.setattr(discovery,'ingest_job',slow_write)
    report = await run_sources(db)
    assert report['new_jobs'] == 4
    assert not any(source['error'] for source in report['items'])
    assert db.one('SELECT COUNT(*) AS n FROM source_job_observations')['n'] == 4


def test_historical_email_alerts_do_not_hide_current_inbox_authentication_failure(db):
    add_source(db,'alerts','indeed')
    db.execute("UPDATE sources SET config=?,last_checked=? WHERE id='alerts'",(dumps({'delivery':'email'}),now()))
    source = db.one("SELECT * FROM sources WHERE id='alerts'")
    assert source_health(db,source)['health'] == 'AWAITING_INBOX_CONNECTION'
    db.execute("INSERT INTO integrations(id,provider,status,last_error) VALUES('gmail','gmail','connected','Reconnect email')")
    health = source_health(db,source)
    assert health['health'] == 'INBOX_ERROR'
    assert health['delivery_evidence'][0]['last_error'] == 'Reconnect email'


@pytest.mark.asyncio
async def test_one_unpublished_workday_path_keeps_valid_postings_and_prevents_absence_closure(monkeypatch):
    import httpx
    original = httpx.AsyncClient
    data = {'total':2,'jobPostings':[{'title':'Published engineer','externalPath':'/job/London/Engineer_R1','locationsText':'London'},
                                   {'title':'Unpublished engineer','externalPath':None}]}
    def respond(request):return httpx.Response(200,json=data)
    monkeypatch.setattr('jobagent.discovery.httpx.AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(respond),**kwargs))
    async def public(url):return None
    monkeypatch.setattr('jobagent.discovery.validate_public_url',public)
    result = await discover({'kind':'workday','url':'https://fixture.wd3.myworkdayjobs.com/External','config':{'board':'External'}})
    assert len(result) == 1 and not result.evidence['complete']
    assert result[0]['url'].endswith('/External/job/London/Engineer_R1')
    assert any('omitted' in limitation for limitation in result.evidence['limitations'])


@pytest.mark.asyncio
async def test_complete_board_reconciles_legacy_urls_and_retains_history_ids(db,monkeypatch):
    add_source(db,'board','workday')
    add_source(db,'alias','workday')
    db.execute('UPDATE sources SET url=? WHERE id=?',('https://fixture.wd3.myworkdayjobs.com/External','alias'))
    old,_ = ingest_job(db,{'title':'Old engineer','company':'Fixture','url':'https://fixture.wd3.myworkdayjobs.com/en-US/External/job/London/Old_R1'})
    alive,_ = ingest_job(db,{'title':'Engineer','company':'Fixture','url':'https://fixture.wd3.myworkdayjobs.com/External/job/London/OldSlug_R2'})
    db.execute('UPDATE jobs SET identity_key=url')  # The pre-upgrade URL identity scheme.
    db.execute('INSERT INTO source_job_observations VALUES(?,?,?,?)',('alias',old['id'],'OPEN',now()))
    async def board(source):
        return DiscoveryResult([{'title':'Engineer','company':'Fixture','url':'https://fixture.wd3.myworkdayjobs.com/External/job/London/NewSlug_R2'}],
            complete=True,authoritative=True,endpoints=['https://fixture.wd3.myworkdayjobs.com/wday/cxs/fixture/External/jobs'])
    monkeypatch.setattr('jobagent.discovery.discover',board)
    await run_sources(db,source_id='board')
    assert db.one('SELECT availability FROM jobs WHERE id=?',(old['id'],))['availability'] == 'CLOSED'
    current = db.one('SELECT * FROM jobs WHERE id=?',(alive['id'],))
    assert current['availability'] == 'OPEN'
    assert current['canonical_url'] == alive['canonical_url']
    assert current['application_url'].endswith('/NewSlug_R2')
    assert db.one('SELECT state FROM source_job_observations WHERE source_id=? AND job_id=?',('alias',old['id']))['state'] == 'CLOSED'
    assert db.one('SELECT id FROM job_duplicates WHERE job_id=? AND duplicate_url=?',(alive['id'],alive['application_url']))


@pytest.mark.asyncio
async def test_declared_sitemap_uses_only_actual_structured_role_urls(monkeypatch):
    async def fetch(url,**kwargs):
        if url.endswith('.xml'):
            body = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.test/careers/job/123-engineer</loc></url><url><loc>https://example.test/about</loc></url></urlset>'
        else:
            body = '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Engineer','hiringOrganization':{'name':'Fixture'},'url':url})+'</script>'
        return body.encode(),'application/xml',url
    monkeypatch.setattr('jobagent.sitemaps.fetch',fetch)
    jobs = await discover({'name':'Fixture careers','kind':'careers','url':'https://example.test',
        'config':{'company_name':'Fixture','public_sitemap':{'url':'https://example.test/sitemap.xml'}}})
    assert len(jobs) == 1 and jobs[0]['source'] == 'Fixture careers'
    assert not jobs.evidence['authoritative'] and not jobs.evidence['complete']


@pytest.mark.asyncio
async def test_regional_greenhouse_uses_the_documented_board_token_api(monkeypatch):
    async def fetch(url,**kwargs):
        assert url.startswith('https://boards-api.greenhouse.io/')
        return b'{"jobs":[]}','application/json',url
    monkeypatch.setattr('jobagent.discovery.fetch',fetch)
    jobs = await discover({'kind':'greenhouse','url':'https://boards.eu.greenhouse.io/fixture','config':{'board':'fixture'}})
    assert not jobs and jobs.evidence['complete']



@pytest.mark.asyncio
async def test_observed_successfactors_pages_preserve_nonengineering_roles_and_scope(monkeypatch):
    import httpx
    from jobagent.successfactors import discover_public_jobs
    def page(ids,total=3):
        return {'totalJobs':total,'jobSearchResult':[{'response':{'id':str(i),'unifiedUrlTitle':'Credit-Analyst','unifiedStandardTitle':'Credit Analyst','jobLocationShort':['London']}} for i in ids]}
    initial=page([1,2])
    endpoint='https://example.test/services/recruiting/v1/jobs'
    descriptor={'url':endpoint,'public_filters':{'locale':'en_GB','pageNumber':0,'categoryId':123},'first_page':initial}
    requested=[]
    async def permitted(url): assert url == endpoint
    monkeypatch.setattr('jobagent.successfactors.validate_public_url',permitted)
    def respond(request):
        payload=json.loads(request.content)
        requested.append(payload)
        return httpx.Response(200,json=page([3]))
    original=httpx.AsyncClient
    monkeypatch.setattr('jobagent.successfactors.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    jobs=await discover_public_jobs(descriptor,'<a href="/job/Credit-Analyst/1-en_GB">Credit Analyst</a>','https://example.test/go/Finance-jobs/123/',{})
    assert len(jobs)==3 and jobs[2]['url']=='https://example.test/job/Credit-Analyst/3-en_GB'
    assert requested==[{'locale':'en_GB','pageNumber':1,'categoryId':123}]
    assert not jobs.evidence['complete'] and jobs.evidence['published_total']==3
    assert 'Category search does not cover all company jobs' in jobs.evidence['limitations']
    descriptor['public_filters']['categoryId']=0
    jobs=await discover_public_jobs(descriptor,'<a href="/job/Credit-Analyst/1-en_GB">Credit Analyst</a>','https://example.test/search/',{})
    assert jobs.evidence['complete']


@pytest.mark.asyncio
async def test_successfactors_repeated_pages_preserve_partial_evidence(monkeypatch):
    import httpx
    from jobagent.successfactors import discover_public_jobs
    data={'totalJobs':2,'jobSearchResult':[{'response':{'id':'1','unifiedUrlTitle':'Auditor','unifiedStandardTitle':'Auditor'}}]}
    descriptor={'url':'https://example.test/services/recruiting/v1/jobs','public_filters':{'locale':'en_GB','pageNumber':0},'first_page':data}
    async def permitted(url): pass
    monkeypatch.setattr('jobagent.successfactors.validate_public_url',permitted)
    original=httpx.AsyncClient
    monkeypatch.setattr('jobagent.successfactors.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data)),**kwargs))
    jobs=await discover_public_jobs(descriptor,'<a href="/job/Auditor/1-en_GB">Auditor</a>','https://example.test/search/',{})
    assert len(jobs)==1 and not jobs.evidence['complete']
    assert any('repeated postings' in message for message in jobs.evidence['limitations'])
    with pytest.raises(ValueError,match='anonymous'):
        await discover_public_jobs({**descriptor,'public_filters':{**descriptor['public_filters'],'rcmCandidateId':'private'}},'', 'https://example.test/search/',{})
    with pytest.raises(ValueError,match='matching real job URL'):
        await discover_public_jobs(descriptor,'', 'https://example.test/search/',{})


def test_successfactors_published_entity_slug_stays_exact():
    from jobagent.successfactors import parse_jobs
    jobs=parse_jobs({'jobSearchResult':[{'response':{'id':'63393','unifiedUrlTitle':'Change-&amp;-Enablement','unifiedStandardTitle':'Change & Enablement'}}]},'https://example.test/job','en_GB','Fixture')
    assert jobs[0]['url']=='https://example.test/job/Change-&amp;-Enablement/63393-en_GB'


@pytest.mark.asyncio
async def test_public_renderer_reads_dom_after_pending_job_responses(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from jobagent.public_renderer import render_public_page
    monkeypatch.delenv('MERIDIAN_TEST',raising=False)
    ready=False
    async def permitted(url): pass
    monkeypatch.setattr('jobagent.public_renderer.validate_public_url',permitted)
    async def body():
        nonlocal ready
        await asyncio.sleep(.02)
        ready=True
        return b'{"jobs":[{"id":123,"title":"Data Engineer"}]}'
    response=SimpleNamespace(url='https://example.test/jobs/search',status=200,
                             headers={'content-type':'application/json'},body=body,
                             request=SimpleNamespace(resource_type='xhr',method='GET',post_data=None))
    class Page:
        url='https://example.test/careers'
        def on(self,event,handler): self.handler=handler
        async def goto(self,*a,**k):
            self.handler(response)
            return SimpleNamespace(status=200)
        async def wait_for_load_state(self,*a,**k): pass
        async def content(self):
            return '<a href="/job/123/">Data Engineer</a>' if ready else '<main>Loading</main>'
        async def title(self): return 'Careers'
    class Context:
        async def route(self,*a): pass
        async def new_page(self): return Page()
    class Browser:
        async def new_context(self,**k): return Context()
        async def close(self): pass
    class Chromium:
        async def launch(self,**k): return Browser()
    @asynccontextmanager
    async def runtime(): yield SimpleNamespace(chromium=Chromium())
    monkeypatch.setattr('playwright.async_api.async_playwright',runtime)
    html,_=await render_public_page(Page.url)
    assert '<a href="/job/123/">Data Engineer</a>' in html
    assert 'meridian-public-search-data' in html
