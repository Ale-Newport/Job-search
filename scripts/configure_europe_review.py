"""Apply the user's Europe-only, London-first preference while preserving CV and history."""

import argparse
from pathlib import Path

from jobagent.db import Database, decode_row, dumps, now
from jobagent.discovery import rescore_job
from jobagent.tracking import daily_data


def configure(db):
    with db.transaction() as conn:
        for key, value in {
            "suggested_region": "europe",
            "preferred_city": "London",
            "auto_apply": False,
            "automation_paused": True,
        }.items():
            conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, dumps(value)))
        for row in conn.execute("SELECT * FROM search_profiles").fetchall():
            profile = decode_row(row)
            profile["config"].update(region="europe", locations=[], preferred_locations=["London"])
            conn.execute(
                "UPDATE search_profiles SET config=?,updated_at=? WHERE id=?",
                (dumps(profile["config"]), now(), row["id"]),
            )
    for row in db.query("SELECT * FROM jobs"):
        rescore_job(db, decode_row(row))
    d = daily_data(db)
    return {
        "matching_european_jobs": d["total"],
        "daily_shown": len(d["items"]),
        "london_first": [j["location"] for j in d["items"][:5]],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(configure(Database(args.data_dir / "database/meridian.sqlite3")))
