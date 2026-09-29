#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p src-tauri/binaries
export PYINSTALLER_CONFIG_DIR="$PWD/.build-cache/pyinstaller"
.venv/bin/pyinstaller --noconfirm --clean --onefile --name meridian-backend \
  --paths backend --collect-all jobagent --collect-all playwright --collect-all keyring \
  --hidden-import keyring.backends.macOS --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.lifespan.on \
  --add-data 'migrations:migrations' scripts/sidecar_entry.py
cp dist/meridian-backend "src-tauri/binaries/meridian-backend-$(rustc --print host-tuple)"
