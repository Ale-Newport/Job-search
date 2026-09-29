#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export MERIDIAN_DEV=1
export MERIDIAN_DATA_DIR="${MERIDIAN_DATA_DIR:-$PWD/.local-data}"
exec .venv/bin/python -m jobagent.main
