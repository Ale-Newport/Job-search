"""Official public career entry points; scan health is independent of configuration.

These entry pages are candidates for structured discovery, not claims that every
employer publishes an accessible feed. Existing custom sources always take priority.
"""
from .db import dumps, now, uid

OFFICIAL_CAREERS = {
    'Monzo': 'https://monzo.com/careers',
    'Wise': 'https://wise.jobs/',
    'Softwire': 'https://www.softwire.com/careers/',
    'Accenture': 'https://www.accenture.com/gb-en/careers',
    'Revolut': 'https://www.revolut.com/careers/',
    'LSEG': 'https://www.lseg.com/en/careers',
    'JPMorgan Chase': 'https://www.jpmorganchase.com/careers',
    'Bloomberg': 'https://www.bloomberg.com/company/careers/',
    'Amazon': 'https://www.amazon.jobs/',
    'Microsoft': 'https://careers.microsoft.com/',
    'Google': 'https://www.google.com/about/careers/applications/',
    'Meta': 'https://www.metacareers.com/jobs/',
    'Apple': 'https://jobs.apple.com/en-gb/search',
    'NVIDIA': 'https://www.nvidia.com/en-us/about-nvidia/careers/',
    'Netflix': 'https://jobs.netflix.com/',
    'Palantir': 'https://www.palantir.com/careers/',
    'Uber': 'https://www.uber.com/global/en/careers/',
    'Stripe': 'https://stripe.com/jobs/search',
    'Cloudflare': 'https://www.cloudflare.com/careers/jobs/',
    'Datadog': 'https://careers.datadoghq.com/',
    'Snowflake': 'https://careers.snowflake.com/',
    'Databricks': 'https://www.databricks.com/company/careers/open-positions',
    'Salesforce': 'https://careers.salesforce.com/',
    'Oracle': 'https://www.oracle.com/careers/',
    'IBM': 'https://www.ibm.com/careers/',
    'Adobe': 'https://careers.adobe.com/',
    'Cisco': 'https://jobs.cisco.com/',
    'Dell Technologies': 'https://jobs.dell.com/',
    'Intel': 'https://jobs.intel.com/',
    'AMD': 'https://careers.amd.com/',
    'Arm': 'https://careers.arm.com/',
    'ByteDance': 'https://jobs.bytedance.com/en/',
    'TikTok': 'https://lifeattiktok.com/search',
    'Spotify': 'https://www.lifeatspotify.com/jobs',
    'Deliveroo': 'https://careers.deliveroo.co.uk/',
    'Deloitte': 'https://www.deloitte.com/global/en/careers.html',
    'Huawei Technologies': 'https://career.huawei.com/',
    'UBS': 'https://www.ubs.com/global/en/careers.html',
    'University College London (UCL)': 'https://www.ucl.ac.uk/work-at-ucl/search-ucl-jobs',
    'EY': 'https://careers.ey.com/',
    'Citigroup': 'https://jobs.citi.com/',
    'HSBC': 'https://www.hsbc.com/careers',
    'Barclays': 'https://search.jobs.barclays/',
    'Morgan Stanley': 'https://www.morganstanley.com/people-opportunities',
    'Deutsche Bank': 'https://careers.db.com/',
    'Goldman Sachs': 'https://www.goldmansachs.com/careers',
    'Bank of America': 'https://careers.bankofamerica.com/',
    'Santander': 'https://www.santander.com/en/careers',
    'NatWest': 'https://jobs.natwestgroup.com/',
    'Lloyds Banking Group': 'https://www.lloydsbankinggroup.com/careers.html',
    'Standard Chartered': 'https://www.sc.com/en/global-careers/',
    'BNP Paribas': 'https://group.bnpparibas/en/careers',
    'Société Générale': 'https://careers.societegenerale.com/en',
    'BlackRock': 'https://careers.blackrock.com/',
    'Blackstone': 'https://www.blackstone.com/careers/',
    'Jane Street': 'https://www.janestreet.com/join-jane-street/open-roles/',
    'Citadel': 'https://www.citadel.com/careers/',
    'Citadel Securities': 'https://www.citadelsecurities.com/careers/',
    'Two Sigma': 'https://careers.twosigma.com/',
    'Millennium': 'https://www.mlp.com/careers/',
    'Point72': 'https://careers.point72.com/',
    'Man Group': 'https://www.man.com/careers',
    'Marshall Wace': 'https://www.mwam.com/careers/',
    'American Express': 'https://www.americanexpress.com/en-us/careers/',
    'Mastercard': 'https://careers.mastercard.com/',
    'Visa': 'https://corporate.visa.com/en/careers.html',
    'Capital One': 'https://www.capitalonecareers.com/',
    'Klarna': 'https://www.klarna.com/careers/',
    'Checkout.com': 'https://www.checkout.com/careers',
    'Starling Bank': 'https://www.starlingbank.com/careers/',
    'Zopa': 'https://www.zopa.com/careers',
    'Adyen': 'https://careers.adyen.com/',
}


def seed_official_sources(db):
    """Add missing employer entry points, preserving all saved choices and portals."""
    for name, url in OFFICIAL_CAREERS.items():
        company = db.one('SELECT * FROM companies WHERE name=? COLLATE NOCASE', (name,))
        if not company:
            continue
        if db.one("SELECT id FROM sources WHERE json_extract(config,'$.company_id')=? OR (url=? AND url!='')", (company['id'], company['careers_url'] or url)):
            continue
        # A user's portal overrides this fallback. No source is labelled verified
        # until a real scan or job verification establishes its current state.
        entry = company['careers_url'] or url
        stamp = now()
        db.execute('INSERT INTO sources(id,name,kind,url,config,poll_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (uid(), name+' careers', 'careers', entry, dumps({'company_id':company['id'],'company_name':name,'official_entry':True}),company['poll_minutes'],stamp,stamp))
        db.execute("UPDATE companies SET careers_url=? WHERE id=? AND careers_url=''", (entry,company['id']))


def import_observed_boards(db):
    """Derive public board identifiers only from existing employer job evidence."""
    from urllib.parse import urlsplit, urlunsplit
    import re
    from .discovery import ats_identity
    for company in db.query('SELECT * FROM companies'):
        for job in db.query('SELECT id,url,application_url FROM jobs WHERE company=? COLLATE NOCASE', (company['name'],)):
            url = job['application_url'] or job['url']
            kind,_ = ats_identity(url)
            parsed = urlsplit(url)
            parts = [p for p in parsed.path.split('/') if p]
            if kind in ('greenhouse','lever','ashby') and parts:
                board = parts[0]
                entry = urlunsplit((parsed.scheme,parsed.netloc,'/'+board,'',''))
            elif kind=='workday' and parsed.hostname.endswith('.myworkdayjobs.com') and len(parts)>=2:
                site = parts[1] if re.fullmatch(r'[a-z]{2}-[A-Z]{2}',parts[0]) else parts[0]
                board = site
                entry = urlunsplit((parsed.scheme,parsed.netloc,'/'+site,'',''))
            else:
                continue
            if db.one('SELECT id FROM sources WHERE url=?',(entry,)):
                continue
            stamp=now()
            db.execute('INSERT INTO sources(id,name,kind,url,config,poll_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (uid(),company['name']+' public board',kind,entry,dumps({'company_id':company['id'],'company_name':company['name'],'board':board,'evidence_job_id':job['id'],'max_jobs':100}),company['poll_minutes'],stamp,stamp))
