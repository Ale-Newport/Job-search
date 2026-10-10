"""Import reviewed public project evidence into an explicitly selected existing database.

Saved snapshots and a human-reviewed JSON bundle are required. This command does
not fetch sources, change identity facts, or modify historical application answers.
"""

import argparse
import json
from pathlib import Path

from jobagent.db import Database
from jobagent.intelligence.public_evidence import import_public_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing database path. Take a backup before invoking.")
    parser.add_argument("bundle", type=Path, help="JSON bundle containing saved snapshots and reviewed project facts")
    parser.add_argument("--source-priority", type=int, default=60, help="Public evidence priority, between 1 and 60")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("The database must already exist.")
    if not args.bundle.is_file():
        parser.error("The reviewed evidence bundle must already exist.")
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        result = import_public_evidence(Database(args.database, migrate=False), bundle,
                                        source_priority=args.source_priority)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
