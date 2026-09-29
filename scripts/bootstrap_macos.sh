#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then echo 'Meridian desktop builds require macOS.'; exit 1; fi
for tool in brew python3.12 node npm cargo; do
  if ! command -v "$tool" >/dev/null; then
    case "$tool" in
      brew) echo 'Install Homebrew from https://brew.sh, then rerun make setup.' ;;
      python3.12) echo 'Install Python: brew install python@3.12' ;;
      node|npm) echo 'Install Node: brew install node' ;;
      cargo) echo 'Install Rust using the official installer at https://rustup.rs' ;;
    esac
    exit 1
  fi
done
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
npm ci
npm --prefix frontend ci
if [[ ! -d '/Applications/Google Chrome.app' ]]; then
  echo 'Google Chrome is missing. Installing the Playwright Chromium runtime.'
  .venv/bin/python -m playwright install chromium
fi
if ! command -v uv >/dev/null; then echo 'Optional: brew install uv for faster Python environment management.'; fi
if ! command -v pnpm >/dev/null; then echo 'Optional pnpm is absent; this project uses npm lockfiles.'; fi
if ! command -v ollama >/dev/null; then echo 'Optional local text generation: install Ollama from https://ollama.com'; fi
./scripts/build_sidecar.sh
echo 'Setup complete. make dev starts Meridian; make build produces a local macOS bundle.'
