"""Run a bounded discovery cycle on a private validation copy, never live data."""
import argparse
import asyncio
import json
from pathlib import Path

from jobagent.companies import coverage, ensure_watchlist, import_configured_sources
from jobagent.db import Database
from jobagent.discovery import run_sources


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.data_dir / 'database/meridian.sqlite3')
    ensure_watchlist(db)
    import_configured_sources(db)
    from jobagent.career_sources import seed_official_sources, import_observed_boards
    import_observed_boards(db)
    seed_official_sources(db)
    before = db.one('SELECT count(*) AS n FROM jobs')['n']
    result = await run_sources(db, args.data_dir)
    report = {'cycle': result, 'companies': coverage(db), 'before_jobs': before,
              'after_jobs': db.one('SELECT count(*) AS n FROM jobs')['n'],
              'scope': 'Private database snapshot; production records untouched'}
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'sources': result['total'], 'new_jobs': result['new_jobs'],
                      'failures': sum(bool(s.get('error')) for s in result['items']),
                      'companies': report['companies']['total'], 'configured': report['companies']['configured']}))


if __name__ == '__main__':
    asyncio.run(main())
