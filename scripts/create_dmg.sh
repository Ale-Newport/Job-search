#!/bin/bash
# Headless fallback when Finder styling in the Tauri DMG bundler is unavailable.
set -euo pipefail
cd "$(dirname "$0")/.."
app_path="$PWD/src-tauri/target/release/bundle/macos/Meridian.app"
output_path="$PWD/src-tauri/target/release/bundle/dmg/Meridian_0.1.0_$(uname -m | sed 's/arm64/aarch64/').dmg"
test -d "$app_path"
codesign --verify --deep --strict "$app_path"
mkdir -p "$PWD/.build-cache" "$(dirname "$output_path")"
stage_path="$(mktemp -d "$PWD/.build-cache/dmg-stage.XXXXXX")"
trap 'rm -rf "$stage_path"' EXIT
ditto "$app_path" "$stage_path/Meridian.app"
ln -s /Applications "$stage_path/Applications"
hdiutil create -volname Meridian -srcfolder "$stage_path" -format UDZO -ov "$output_path"
hdiutil verify "$output_path"
