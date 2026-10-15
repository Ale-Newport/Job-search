#!/bin/bash
# Headless fallback when Finder styling in the Tauri DMG bundler is unavailable.
#
#   scripts/create_dmg.sh                 package the newest Meridian.app that a build wrote
#   scripts/create_dmg.sh --app PATH      package that app instead
#   scripts/create_dmg.sh --print-plan    print the app and the .dmg this would make; builds nothing
#
# The app is found the way scripts/build_signed_app.py finds the one it just built (the same lookup: every Cargo target directory
# it knows, `<target>/<profile>/bundle/macos/*.app` and `<target>/<triple>/<profile>/bundle/macos/*.app`, the newest bundle wins), and
# the version is the one in src-tauri/tauri.conf.json. Neither is typed in here, so a release can never package a stale path or
# carry a stale version in its file name. The .dmg goes next to the app's `macos` folder, in `<...>/bundle/dmg`.
#
# After installing from the .dmg, keep ONE copy of Meridian.app (the one in /Applications). macOS binds the meridian:// address the
# browser extension uses ("Open Meridian") and the Safari extension to whichever copy it registered, and the build folders hold copies
# with the same bundle id (src-tauri/target/.../Meridian.app, build/safari/Meridian Assistant.app). Un-register those with
#   /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "<path>"
# and check `pluginkit -m -i com.meridian.jobagent.assistant.Extension` lists the /Applications copy before testing Safari.
set -euo pipefail
cd "$(dirname "$0")/.."

app_override=""
plan_only=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --app) [ "$#" -ge 2 ] || { echo "--app needs a path" >&2; exit 2; }; app_override="$2"; shift 2 ;;
    --print-plan) plan_only=1; shift ;;
    -h|--help) awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

python="${PYTHON:-$PWD/.venv/bin/python}"
[ -x "$python" ] || python="python3"

# Line 1: the app. Line 2: the version.
plan="$(APP_OVERRIDE="$app_override" "$python" - <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "scripts"))
import build_signed_app

version = json.loads((root / "src-tauri" / "tauri.conf.json").read_text())["version"]
override = os.environ.get("APP_OVERRIDE", "")
if override:
    app = Path(override)
else:
    found = build_signed_app.find_built_apps([], 0)
    if not found:
        sys.exit("No built Meridian.app was found under " + ", ".join(str(d) for d in build_signed_app.target_directories())
                 + ". Build the app first (make build-app), or pass --app PATH.")
    app = found[0]
print(app)
print(version)
PY
)"
app_path="$(printf '%s\n' "$plan" | sed -n 1p)"
version="$(printf '%s\n' "$plan" | sed -n 2p)"
arch="$(uname -m | sed 's/arm64/aarch64/')"
output_path="$(cd "$(dirname "$app_path")/.." && pwd)/dmg/Meridian_${version}_${arch}.dmg"

if [ "$plan_only" = 1 ]; then
  printf 'app=%s\nversion=%s\ndmg=%s\n' "$app_path" "$version" "$output_path"
  exit 0
fi

test -d "$app_path"
codesign --verify --deep --strict "$app_path"
mkdir -p "$PWD/.build-cache" "$(dirname "$output_path")"
stage_path="$(mktemp -d "$PWD/.build-cache/dmg-stage.XXXXXX")"
trap 'rm -rf "$stage_path"' EXIT
ditto "$app_path" "$stage_path/Meridian.app"
ln -s /Applications "$stage_path/Applications"
hdiutil create -volname Meridian -srcfolder "$stage_path" -format UDZO -ov "$output_path"
hdiutil verify "$output_path"
echo "Packaged $app_path as $output_path"
echo "Install it, keep a single Meridian.app (see the notes at the top of this script), then open it once from /Applications."
