#!/bin/bash
# Freeze the Python backend into the Tauri sidecar binary.
#
# The browser extension is built and checked first because the sidecar embeds extension/dist/chrome (the backend installs
# it into Meridian's data folder so the user can load it into Chrome). The script fails, and publishes nothing to
# src-tauri/binaries, when extension/dist/chrome is missing or fails its production check, and again when the frozen backend
# does not hold every file of it (and nothing else under extension/): see build_extension.py --verify-sidecar. Environment:
#   MERIDIAN_EXTENSION_READY=1  the extension was just built (make extension); only re-check it, do not rebuild
#   SKIP_PYINSTALLER=1          do everything except the long PyInstaller step and print the command that would run
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p src-tauri/binaries
export PYINSTALLER_CONFIG_DIR="$PWD/.build-cache/pyinstaller"

if [ "${MERIDIAN_EXTENSION_READY:-}" = "1" ]; then
  .venv/bin/python scripts/build_extension.py --check-only
else
  .venv/bin/python scripts/build_extension.py
fi
if [ ! -f extension/dist/chrome/manifest.json ]; then
  echo "build_sidecar: extension/dist/chrome/manifest.json is missing, so the Chrome extension cannot be embedded." >&2
  echo "build_sidecar: build it with 'make extension' (needs Node.js; see extension/package.json)." >&2
  exit 1
fi

command=(.venv/bin/pyinstaller --noconfirm --clean --onefile --name meridian-backend
  --paths backend --collect-all jobagent --collect-all playwright --collect-all keyring
  --hidden-import keyring.backends.macOS --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto
  --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.lifespan.on
  --add-data 'migrations:migrations' --add-data 'extension/dist/chrome:extension/chrome' scripts/sidecar_entry.py)
if [ "${SKIP_PYINSTALLER:-}" = "1" ]; then
  echo "build_sidecar: SKIP_PYINSTALLER=1, not running:"
  printf '  '
  printf '%q ' "${command[@]}"
  printf '\n'
  exit 0
fi
"${command[@]}"

# The packaged backend must carry exactly the extension it installs (every file of extension/dist/chrome, nothing else under
# extension/); fail here rather than in a user's Chrome. Nothing is copied to src-tauri/binaries unless this passes.
.venv/bin/python scripts/build_extension.py --verify-sidecar dist/meridian-backend
cp dist/meridian-backend "src-tauri/binaries/meridian-backend-$(rustc --print host-tuple)"
