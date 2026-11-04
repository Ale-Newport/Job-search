#!/usr/bin/env bash
# One backup of Meridian's data folder, kept in .local-data/backups of this repository (git-ignored). Only the latest copy is kept:
# the previous ones are deleted AFTER the new one is complete and checked, never before.
#
# Run it before installing a new build (`make backup-data`). The app must be closed: the database is a live SQLite file with a
# write-ahead log, and a clone taken while Meridian writes can be inconsistent. The clone is an APFS copy-on-write one (`cp -c`):
# it is instant and takes space only as the original changes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${MERIDIAN_DATA_DIR:-$HOME/Library/Application Support/Meridian}"
DEST="$ROOT/.local-data/backups"
PREFIX="Meridian-backup-"

if pgrep -x meridian >/dev/null || pgrep -x meridian-backend >/dev/null; then
  echo "Meridian is running. Quit it first (osascript -e 'tell application \"Meridian\" to quit') and run this again." >&2
  exit 1
fi
[ -f "$DATA/database/meridian.sqlite3" ] || { echo "No Meridian database in: $DATA" >&2; exit 1; }

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M)"
NEW="$DEST/$PREFIX$STAMP"
PARTIAL="$NEW.partial"
[ ! -e "$NEW" ] || { echo "A backup named $PREFIX$STAMP already exists." >&2; exit 1; }

# a half-finished clone from an earlier attempt is not a backup
for old in "$DEST"/"$PREFIX"*.partial; do
  [ -d "$old" ] && rm -rf -- "$old"
done

cp -cR "$DATA" "$PARTIAL"
# complete? the database file must be the same size as the original, and be readable as a SQLite database
[ "$(stat -f %z "$DATA/database/meridian.sqlite3")" = "$(stat -f %z "$PARTIAL/database/meridian.sqlite3")" ] \
  || { echo "The copy of the database is not the size of the original; keeping the previous backup." >&2; exit 1; }
sqlite3 "file:$PARTIAL/database/meridian.sqlite3?mode=ro&immutable=1" "select count(*) from sqlite_master" >/dev/null \
  || { echo "The copy of the database cannot be read; keeping the previous backup." >&2; exit 1; }
mv "$PARTIAL" "$NEW"

# only now the earlier backups go: directories of this folder whose name starts with the prefix, never the new one
for old in "$DEST"/"$PREFIX"*; do
  [ -d "$old" ] || continue
  [ "$old" = "$NEW" ] && continue
  case "$old" in "$DEST"/"$PREFIX"[0-9]*) rm -rf -- "$old"; echo "Deleted the previous backup: $(basename "$old")";; esac
done
echo "Backup: $NEW"
