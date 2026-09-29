"""Enable the daily discovery workflow and its public sources without touching credentials."""

import argparse
import asyncio
import json
from pathlib import Path

from jobagent.db import Database, dumps, now, uid
from jobagent.tracking import daily_data, refresh_daily

SOURCES = [
    (
        "Trackr · UK Tech Graduate 2027",
        "trackr",
        "https://app.the-trackr.com/uk-tech/graduate-programmes",
        {"region": "UK", "industry": "Tech", "season": "2027", "programme_type": "graduate-programmes"},
    ),
    (
        "GitHub · SpeedyApply International New Grad",
        "github",
        "https://github.com/speedyapply/2027-SWE-College-Jobs",
        {"repository": "speedyapply/2027-SWE-College-Jobs", "paths": ["NEW_GRAD_INTL.md"]},
    ),
    (
        "GitHub · New Grad 2027",
        "github",
        "https://github.com/vanshb03/New-Grad-2027",
        {"repository": "vanshb03/New-Grad-2027", "paths": ["README.md"]},
    ),
    ("LinkedIn alerts", "linkedin", "https://www.linkedin.com/jobs/", {"delivery": "email"}),
    ("Indeed alerts", "indeed", "https://www.indeed.com/", {"delivery": "email"}),
]


def configure(db):
    with db.transaction() as conn:
        for key, value in {
            "tracking_first": True,
            "automation_paused": True,
            "auto_apply": False,
            "default_mode": "manual",
            "scheduler_enabled": True,
            "daily_min_match": 50,
            "laya_autostart": False,
        }.items():
            conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, dumps(value)))
        for name, kind, url, config in SOURCES:
            existing = conn.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE sources SET name=?,kind=?,config=?,enabled=1,poll_minutes=1440,updated_at=? WHERE id=?",
                    (name, kind, dumps(config), now(), existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO sources(id,name,kind,url,config,enabled,poll_minutes,created_at,updated_at) VALUES(?,?,?,?,?,1,1440,?,?)",
                    (uid(), name, kind, url, dumps(config), now(), now()),
                )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    db = Database(args.data_dir / "database/meridian.sqlite3")
    configure(db)
    from jobagent.job_alerts import reprocess_alerts

    print("Existing alert offers processed:", reprocess_alerts(db))
    if args.refresh:
        result = await refresh_daily(db, args.data_dir)
        print(json.dumps(result, indent=2))
    d = daily_data(db)
    print(json.dumps({k: d[k] for k in ("total", "new_today", "applied", "last_refresh")}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
