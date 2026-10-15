"""Candidate-portal vendors whose hosts are NEVER an employer's own website (CPL-19 / MISC2): a posting on ``hsbc.tal.net`` is hosted
by the vendor Tal.net for HSBC, so the research must not take ``hsbc.tal.net`` (or any other tenant of the vendor) for the
company's site, group two tenants as one registrable domain, or call it official. Offline."""
import json

import pytest

from jobagent.extension import research as R
from test_extension_research import FakeWeb, company_page, make_manager, posting_report, run_to_end  # noqa: E402

# (tenant host, the posting address on it): the shapes the vendors really use
VENDORS = [
    ("hsbc.tal.net", "https://hsbc.tal.net/vx/lang-en-GB/mobile-0/appcentre-ext/brand-4/candidate/so/pm/1/pl/1/opp/12345-Analyst/en-GB"),
    ("acme.avature.net", "https://acme.avature.net/careers/JobDetail/Analyst/12345"),
    ("acme.csod.com", "https://acme.csod.com/ux/ats/careersite/1/home/requisition/12345?c=acme"),
    ("recruiting.ultipro.com", "https://recruiting.ultipro.com/ACM1000/JobBoard/0a1b2c3d/OpportunityDetail?opportunityId=1"),
    ("acme.yello.co", "https://acme.yello.co/jobs/12345"),
    ("acme.jobtrain.co.uk", "https://acme.jobtrain.co.uk/vacancy/12345"),
    ("acme.webrecruit.co.uk", "https://acme.webrecruit.co.uk/jobs/12345"),
    ("app.beapplied.com", "https://app.beapplied.com/apply/abc123"),
]
NEW_SUFFIXES = ["tal.net", "avature.net", "csod.com", "ultipro.com", "yello.co", "jobtrain.co.uk", "webrecruit.co.uk", "beapplied.com"]


@pytest.mark.parametrize("suffix", NEW_SUFFIXES)
def test_the_vendor_is_an_ats_and_never_an_official_site(suffix):
    assert suffix in R.ATS_SUFFIXES
    for host in (suffix, "acme." + suffix, "careers.acme." + suffix):
        assert R.is_ats_host(host) and R.is_non_official(host), host
        assert R.registrable_domain(host) == host  # every tenant is a different employer: never grouped under the vendor


def test_a_lookalike_that_only_ends_the_same_way_is_not_the_vendor():
    for host in ("hsbctal.net", "notavature.net", "csod.com.evil.example", "tal.net.evil.example", "monzo.com"):
        assert not R.is_ats_host(host), host


@pytest.mark.parametrize("host,url", VENDORS, ids=[v[0] for v in VENDORS])
async def test_a_posting_on_a_vendor_tenant_is_never_taken_for_the_employers_own_site(tmp_path, host, url):
    job = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Graduate Analyst",
           "description": "<p>" + "Work with clients across a global bank every day. " * 12 + "</p>",
           "hiringOrganization": {"@type": "Organization", "name": "Acme Industries"}}
    web = FakeWeb()
    web.add(f"https://{host}/", company_page("Acme Industries | Careers", "<nav><a href='/about'>About</a></nav>",
                                             name="Acme Industries"))  # the vendor's page for this tenant claims to be Acme
    web.add(f"https://{host}/about", company_page("About Acme Industries", "<p>Apply here.</p>", name="Acme Industries"))
    manager = make_manager(tmp_path, web)
    done = await run_to_end(manager, "tab-1", posting_report(url=url, title="Graduate Analyst - Acme", h1="Graduate Analyst",
                                                             jsonld=[json.dumps(job)]))
    assert done["site"] is None or not done["site"]["domain"].endswith(host.split(".", 1)[-1]), done["site"]
    assert not [c for c in web.calls if c.startswith(f"https://{host}/") and c.count("/") <= 3], web.calls  # root and /about: never tried
    assert done["company"]["name"] == "Acme Industries"
    await manager.close()


async def test_a_link_or_a_jsonld_website_on_a_vendor_tenant_is_not_the_companys_site_either(tmp_path):
    job = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Graduate Analyst",
           "description": "<p>" + "Work with clients across a global bank every day. " * 12 + "</p>",
           "hiringOrganization": {"@type": "Organization", "name": "HSBC", "url": "https://hsbc.tal.net/"}}
    web = FakeWeb()
    web.add("https://hsbc.tal.net/", company_page("HSBC | Careers", "", name="HSBC"))
    manager = make_manager(tmp_path, web)
    done = await run_to_end(manager, "tab-1", posting_report(
        url="https://www.example-jobs-board.example/jobs/12345", title="Graduate Analyst - HSBC", h1="Graduate Analyst",
        jsonld=[json.dumps(job)], links=[{"href": "https://hsbc.tal.net/", "text": "Company website"}]))
    assert done["site"] is None or done["site"]["domain"] != "hsbc.tal.net"
    assert not web.fetched("hsbc.tal.net")
    await manager.close()
