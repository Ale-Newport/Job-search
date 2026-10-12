"""Export public source and company verification metadata without candidate data."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobagent.companies import coverage
from jobagent.discovery import source_health


class ReadOnlyDatabase:
    def __init__(self, path):
        self.connection = sqlite3.connect('file:' + str(path.resolve()) + '?mode=ro', uri=True)
        self.connection.row_factory = sqlite3.Row

    def query(self, query, parameters=()):
        return [dict(row) for row in self.connection.execute(query, parameters)]

    def one(self, query, parameters=()):
        row = self.connection.execute(query, parameters).fetchone()
        return dict(row) if row else None


def public_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https'):
        return ''
    host = parts.hostname or ''
    if parts.port:
        host += ':' + str(parts.port)
    private = re.compile(r'(?:key|token|secret|password|signature)$|email|candidate|authorization|^auth$|^passcode$', re.I)
    parameters = [(key, val) for key, val in parse_qsl(parts.query) if not private.search(key)]
    return urlunsplit((parts.scheme, host, parts.path, urlencode(parameters), ''))


def public_error(value):
    if not value:
        return None
    return re.sub(r'https?://[^\s<>"\x27]+', lambda match: public_url(match.group()), value)[:500]


def export(database):
    db = ReadOnlyDatabase(database)
    companies = coverage(db)
    sources = []
    for raw in db.query('SELECT * FROM sources ORDER BY name'):
        row = source_health(db, raw)
        evidence = row.get('scan_evidence') or {}
        sources.append({'name': row['name'], 'kind': row['kind'], 'url': public_url(row['url']),
                        'enabled': bool(row['enabled']), 'last_checked': row['last_checked'],
                        'health': row['health'], 'jobs_found': row['jobs_found'],
                        'error': public_error(row['last_error']),
                        'evidence': {key: evidence.get(key) for key in
                                     ('complete', 'authoritative', 'method', 'published_total', 'matching_jobs', 'limitations')},
                        'endpoints': [public_url(url) for url in evidence.get('endpoints', [])]})
    company_rows = [{'company': row['company'], 'health': row['health'], 'daily_enabled': row['daily_enabled'],
                     'sources': [{'name': source['name'], 'url': public_url(source['url']), 'health': source['health']}
                                 for source in row['sources']]} for row in companies['items']]
    refreshed = db.one("SELECT value FROM settings WHERE key='daily_last_refresh'")
    db.connection.close()
    return {'audited_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'Read-only installed database; public discovery metadata only; no candidate facts, answers, documents or credentials.',
            'source_count': len(sources), 'health_counts': dict(Counter(row['health'] for row in sources)),
            'company_count': companies['total'], 'company_health_counts': dict(Counter(row['health'] for row in company_rows)),
            'daily_enabled_companies': companies['daily_enabled'],
            'last_full_refresh': json.loads(refreshed['value']) if refreshed else None,
            'sources': sources, 'companies': company_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = export(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('source_count', 'health_counts', 'company_count', 'company_health_counts', 'daily_enabled_companies', 'last_full_refresh')}))


if __name__ == '__main__':
    main()
