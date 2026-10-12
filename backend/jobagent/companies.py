"""Canonical employer aliases; distinct group subsidiaries stay independent."""
from .db import dumps, now, uid

REQUIRED_COMPANIES = '''Monzo|Wise|Softwire|Accenture|Revolut|LSEG|JPMorgan Chase|Bloomberg|Amazon|Microsoft|Google|Meta|Apple|NVIDIA|Netflix|Palantir|Uber|Stripe|Cloudflare|Datadog|Snowflake|Databricks|Salesforce|Oracle|IBM|Adobe|Cisco|Dell Technologies|Intel|AMD|Arm|ByteDance|TikTok|Spotify|Deliveroo|Deloitte|Huawei Technologies|UBS|University College London (UCL)|EY|Citigroup|HSBC|Barclays|Morgan Stanley|Deutsche Bank|Goldman Sachs|Bank of America|Santander|NatWest|Lloyds Banking Group|Standard Chartered|BNP Paribas|Société Générale|BlackRock|Blackstone|Jane Street|Citadel|Citadel Securities|Two Sigma|Millennium|Point72|Man Group|Marshall Wace|American Express|Mastercard|Visa|Capital One|Klarna|Checkout.com|Starling Bank|Zopa|Adyen'''.split('|')
ALIASES = {'Citigroup': ['Citi'], 'JPMorgan Chase': ['J.P. Morgan', 'JP Morgan', 'JPMorgan'],
           'University College London (UCL)': ['UCL', 'University College London']}


def ensure_watchlist(db):
    with db.transaction() as conn:
        for name in REQUIRED_COMPANIES:
            company = conn.execute('SELECT * FROM companies WHERE name=? COLLATE NOCASE', (name,)).fetchone()
            alias_rows = [conn.execute('SELECT * FROM companies WHERE name=? COLLATE NOCASE', (alias,)).fetchone() for alias in ALIASES.get(name, [])]
            if not company:
                if any(alias_rows):
                    company = next(r for r in alias_rows if r)
                    conn.execute('UPDATE companies SET name=?,updated_at=? WHERE id=?', (name, now(), company['id']))
                else:
                    identifier = uid()
                    conn.execute('INSERT INTO companies(id,name,created_at,updated_at) VALUES(?,?,?,?)', (identifier, name, now(), now()))
                    company = conn.execute('SELECT * FROM companies WHERE id=?', (identifier,)).fetchone()
            for alias in [name, *ALIASES.get(name, [])]:
                conn.execute('INSERT OR IGNORE INTO company_aliases(alias,company_id,payload) VALUES(?,?,?)', (alias, company['id'], '{}'))
            for old in alias_rows:
                if not old or old['id'] == company['id']:
                    continue
                # Full original settings retained as evidence; all configured career
                # sources remain separate, even when their employer is canonicalised.
                conn.execute('INSERT INTO company_aliases(alias,company_id,payload) VALUES(?,?,?) ON CONFLICT(alias) DO UPDATE SET company_id=excluded.company_id,payload=excluded.payload',
                             (old['name'], company['id'], dumps(dict(old))))
                for table in ('jobs', 'company_domains', 'company_watch_rules', 'professional_contacts'):
                    conn.execute(f'UPDATE {table} SET company_id=? WHERE company_id=?', (company['id'], old['id']))
                conn.execute("UPDATE sources SET config=json_set(config,'$.company_id',?) WHERE json_extract(config,'$.company_id')=?", (company['id'], old['id']))
                if old['careers_url'] and not company['careers_url']:
                    conn.execute('UPDATE companies SET careers_url=? WHERE id=?', (old['careers_url'], company['id']))
                # Existing source records aren't discarded. Preserve an unregistered
                # second portal in sources so every distinct URL can still be scanned.
                if old['careers_url'] and old['careers_url'] != company['careers_url'] and not conn.execute('SELECT id FROM sources WHERE url=?', (old['careers_url'],)).fetchone():
                    conn.execute('INSERT INTO sources(id,name,kind,url,config,poll_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                                 (uid(), name+' — retained portal', 'company', old['careers_url'], dumps({'company_id': company['id'], 'company': name}), old['poll_minutes'], now(), now()))
                conn.execute('DELETE FROM companies WHERE id=?', (old['id'],))


def coverage(db):
    from .discovery import source_health
    rows = []
    for company in db.query('SELECT * FROM companies ORDER BY name'):
        sources = [source_health(db, s) for s in db.query("SELECT * FROM sources WHERE json_extract(config,'$.company_id')=? OR lower(json_extract(config,'$.company'))=lower(?) OR (url=? AND url!='')", (company['id'], company['name'], company['careers_url']))]
        enabled = [s for s in sources if s['enabled']]
        healthy = [s for s in enabled if s['health'] in ('HEALTHY', 'EMPTY')]
        partial = [s for s in enabled if s['health'] == 'PARTIAL']
        health = ('UNCONFIGURED' if not sources else 'DISABLED' if not enabled else 'VERIFIED' if healthy else
                  'PARTIAL' if partial else 'FAILED' if all(s['last_error'] for s in enabled) else 'NOT_VERIFIED')
        rows.append({'company_id': company['id'], 'company': company['name'], 'careers_url': company['careers_url'],
                     'sources': sources, 'health': health, 'daily_enabled': bool(enabled),
                     'reason': 'No verified career source is configured; add the official portal URL.' if not sources else None})
    return {'items': rows, 'total': len(rows), 'configured': sum(bool(r['sources']) for r in rows),
            'verified': sum(r['health'] == 'VERIFIED' for r in rows), 'partial': sum(r['health'] == 'PARTIAL' for r in rows),
            'daily_enabled': sum(r['daily_enabled'] for r in rows)}


def import_configured_sources(db):
    """Retain the actual employer sources even in workspaces without company rows."""
    import json
    for source in db.query("SELECT * FROM sources WHERE kind IN ('ashby','greenhouse','lever','smartrecruiters','workday')"):
        config = json.loads(source['config'] or '{}')
        if config.get('company_id'):
            continue
        name = config.get('company_name') or source['name']
        aliases = db.query('SELECT a.alias,c.id,c.name FROM company_aliases a JOIN companies c ON c.id=a.company_id')
        matches = [a for a in aliases if name.casefold()==a['alias'].casefold() or name.casefold().startswith(a['alias'].casefold()+' - ')]
        if matches:
            company_id = matches[0]['id']
        else:
            company = db.one('SELECT id FROM companies WHERE name=? COLLATE NOCASE', (name,))
            company_id = company['id'] if company else uid()
            if not company:
                db.execute('INSERT INTO companies(id,name,careers_url,created_at,updated_at) VALUES(?,?,?,?,?)', (company_id,name,source['url'],now(),now()))
        config['company_id'] = company_id
        db.execute('UPDATE sources SET config=? WHERE id=?', (dumps(config),source['id']))
        db.execute("UPDATE companies SET careers_url=? WHERE id=? AND careers_url=''", (source['url'],company_id))
        db.execute('UPDATE jobs SET company_id=? WHERE company_id IS NULL AND company=? COLLATE NOCASE', (company_id,name))
