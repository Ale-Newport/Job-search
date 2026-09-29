#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
root_dir="${MERIDIAN_DATA_DIR:-$HOME/Library/Application Support/Meridian}"
python3.12 -m venv "$root_dir/models/laya-runtime"
if [[ "$(uname -m)" == arm64 ]]; then extra=mlx; else extra=torch; fi
# Pinned reference revision is recorded in docs/DEPENDENCIES.md; the backend stays isolated.
revision="${LAYA_REVISION:-9060f073e7836d6f10a6b0596f362e362f32107e}"
"$root_dir/models/laya-runtime/bin/pip" install "laya-browser-agent[$extra] @ git+https://github.com/ChenneyZhuang/laya-browser-agent.git@$revision"
"$root_dir/models/laya-runtime/bin/localdecide" doctor
echo "Start the local service with:"
printf '  "%s" serve --host 127.0.0.1 --port 8791\n' "$root_dir/models/laya-runtime/bin/localdecide"
