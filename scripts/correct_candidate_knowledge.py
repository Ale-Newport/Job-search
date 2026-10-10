"""Apply the user's explicit corrections to a specified database, never a default."""

import argparse
import json
from pathlib import Path

from jobagent.db import Database
from jobagent.intelligence.candidate_updates import apply_authoritative_candidate_updates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing database path. Take a backup before invoking.")
    parser.add_argument("payload", type=Path, help="Private JSON containing explicit user corrections")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("The database must already exist.")
    print(json.dumps(apply_authoritative_candidate_updates(Database(args.database), json.loads(args.payload.read_text())), indent=2))


if __name__ == "__main__":
    main()
