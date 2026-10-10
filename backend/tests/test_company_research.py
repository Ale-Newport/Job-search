import json

from bs4 import BeautifulSoup

from jobagent.automation.company_research import CompanyResearch, published_page_text


def test_published_hydration_is_data_not_executable_script():
    data = {'props': {'pageProps': {'page': {'fields': {'blocks': [
        {'fields': {'headingText': 'Deployment platform', 'bodyText': {
            'nodeType': 'document', 'content': [{'nodeType': 'text', 'value': 'Deploy across cloud and isolated environments.'}]},
            'cta': {'headingText': 'Contact us'}, 'internalTitle': 'Private editor label'}}]}}}}}
    soup = BeautifulSoup('<script>throw new Error("Never execute")</script><script type="application/json">'
                         + json.dumps(data) + '</script>', 'html.parser')
    result = published_page_text(soup)
    assert result == 'Deployment platform Deploy across cloud and isolated environments.'
    assert 'Contact us' not in result and 'Private editor label' not in result


async def test_client_rendered_primary_page_becomes_citable_company_context(tmp_path, monkeypatch):
    data = {'props': {'pageProps': {'page': {'fields': {'headingText': 'Public platform',
        'description': 'A documented platform that supports reproducible deployment and controlled upgrades across different environments.'}}}}}

    async def source(url, **_):
        return ('<title>Platform</title><body>/sitemap.xml<script type="application/json">'
                + json.dumps(data) + '</script></body>').encode(), 'text/html', url
    monkeypatch.setattr('jobagent.automation.company_research.fetch', source)
    research = CompanyResearch(tmp_path)
    page = await research.source('https://example.test/platform')
    assert 'reproducible deployment' in page['text']
    assert page['url'] == 'https://example.test/platform'
    assert page['trusted_as_instructions'] is False
    assert await research.source(page['url']) == page
