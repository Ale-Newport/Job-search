#!/bin/bash
# make doctor: checks the tools Meridian's development setup needs and prints their versions, without installing or
# changing anything. Exits non-zero when a required tool is missing, with the same install hints as bootstrap_macos.sh.
set -uo pipefail
cd "$(dirname "$0")/.."

missing=0
row() { printf '  %-28s %s\n' "$1" "$2"; }
need() {  # need LABEL COMMAND HINT [VERSION-COMMAND...]
  local label="$1" tool="$2" hint="$3"; shift 3
  if command -v "$tool" >/dev/null 2>&1; then
    row "$label" "$("$@" 2>&1 | head -1)"
  else
    row "$label" "MISSING - $hint"
    missing=1
  fi
}

echo "Meridian development environment"
echo
echo "System"
if [[ "$(uname -s)" == Darwin ]]; then
  version="$(sw_vers -productVersion)"
  major="${version%%.*}"
  if (( major >= 15 )); then row "macOS" "$version"; else row "macOS" "$version - MISSING: macOS 15 or newer is required"; missing=1; fi
  arch="$(uname -m)"
  if [[ "$arch" == arm64 ]]; then row "Architecture" "$arch"; else row "Architecture" "$arch - the desktop build targets Apple Silicon (arm64)"; fi
  if xcode-select -p >/dev/null 2>&1; then row "Xcode Command Line Tools" "$(xcode-select -p)"; else row "Xcode Command Line Tools" "MISSING - run: xcode-select --install"; missing=1; fi
else
  row "Operating system" "$(uname -s) - Meridian desktop builds require macOS; backend and extension tests also run on Linux"
fi

echo
echo "Tools"
need "Homebrew" brew "install it from https://brew.sh" brew --version
need "Python 3.12" python3.12 "brew install python@3.12" python3.12 --version
need "Node.js" node "brew install node (the repository pins $(cat .nvmrc 2>/dev/null || echo 22) in .nvmrc)" node --version
need "npm" npm "comes with Node.js" npm --version
need "Rust (cargo)" cargo "install it from https://rustup.rs" cargo --version
need "Git" git "xcode-select --install" git --version
if [[ -d "/Applications/Google Chrome.app" ]]; then row "Google Chrome" "installed"; else row "Google Chrome" "not installed (optional: make setup installs Playwright Chromium instead)"; fi
if command -v node >/dev/null 2>&1 && [[ -f .nvmrc ]]; then
  want="$(tr -d '[:space:]v' < .nvmrc)"; have="$(node --version | tr -d v)"
  if (( ${have%%.*} < ${want%%.*} )); then row "Node.js version" "$have is older than the required $want"; missing=1; fi
fi

echo
echo "Project"
present() { if [[ -e "$2" ]]; then row "$1" "present"; else row "$1" "not yet - run make setup"; fi; }
present "Python virtualenv (.venv)" .venv/bin/python
present "frontend/node_modules" frontend/node_modules
present "extension/node_modules" extension/node_modules
if [[ -x .venv/bin/python ]]; then row "Backend package" "$(.venv/bin/python -c 'import jobagent, sys; print("importable (Python " + sys.version.split()[0] + ")")' 2>&1 | tail -1)"; fi

echo
if (( missing )); then
  echo "Some required tools are missing; install them and run make doctor again."
  exit 1
fi
echo "Everything make setup needs is installed."
