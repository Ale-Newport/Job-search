#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# The browser bridge: like the installed app, this backend listens for the Meridian Assistant extension on the first free port of
# 47615 to 47624. If Meridian.app is running too, two programs answer like Meridian and the extension REFUSES to pair with either
# (it will not choose between them: nothing is sent to any of them). Pairing that is already set up keeps working, because a paired
# browser proves which one holds its key. To resolve it:
#   - quit one of the two; or
#   - start this backend without the bridge:   MERIDIAN_BRIDGE=0 ./scripts/dev_backend.sh   (or MERIDIAN_BRIDGE=0 make backend).
# Do not give the development bridge a private port range: the shipped extension only looks at 47615 to 47624.
# This backend keeps its own data (.local-data), so its pairings are independent of the installed app's.
export MERIDIAN_DEV=1
export MERIDIAN_DATA_DIR="${MERIDIAN_DATA_DIR:-$PWD/.local-data}"
exec .venv/bin/python -m jobagent.main
