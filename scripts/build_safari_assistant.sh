#!/bin/bash
# Build the Safari half of Meridian Assistant with swiftc + codesign only (no Xcode project):
#
#   <out>/Meridian Assistant.app                              stand-alone wrapper app (com.meridian.jobagent.assistant)
#     Contents/PlugIns/Meridian Assistant Extension.appex     the Safari web extension (com.meridian.jobagent.assistant.Extension)
#
# The appex is what ships: scripts/build_signed_app.py embeds it in Meridian.app/Contents/PlugIns (its identifier is
# prefixed by Meridian's com.meridian.jobagent). The wrapper app only exists so the extension can be built, registered
# and tried on its own. Both are signed here with the identity the Tauri build uses and sandbox-only entitlements; the
# appex is signed before it is embedded because the Tauri bundler never re-signs nested code. Nothing is installed,
# and nothing is registered with LaunchServices or pluginkit unless --verify-registration is given (then only a
# throwaway copy, which is unregistered and deleted before the script exits).
#
# Usage: build_safari_assistant.sh [options]
#   --extension-dir DIR    built web extension (default extension/dist/safari)
#   --out DIR              output directory (default build/safari)
#   --identity NAME|-      codesign identity. Default: $MERIDIAN_SIGNING_IDENTITY, else the identity
#                          scripts/build_signed_app.py --print-identity resolves, else ad-hoc "-" (Safari then
#                          ignores the extension unless Develop > Allow Unsigned Extensions is on)
#   --version V            CFBundleShortVersionString/CFBundleVersion (default: version of src-tauri/tauri.conf.json)
#   --verify-registration  also check that pluginkit/Safari's extension service sees the appex, using a temporary copy
#   --check                exit 0 when the toolchain needed for this build is available, 1 otherwise
#   --print-steps          print what the user has to do in Safari afterwards and exit (builds nothing; a build prints it too)
# Environment: ARCHS (default: this Mac's architecture, e.g. "arm64 x86_64" for universal), MIN_MACOS (default: the
# minimumSystemVersion of tauri.conf.json), ICON (.icns for the wrapper app, default src-tauri/icons/icon.icns).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/extension/safari"
TAURI_CONF="$ROOT/src-tauri/tauri.conf.json"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
EXT_DIR="$ROOT/extension/dist/safari"
OUT_DIR="$ROOT/build/safari"
IDENTITY="${MERIDIAN_SIGNING_IDENTITY:-}"
VERSION=""
VERIFY_REGISTRATION=0
CHECK_ONLY=0
APP_ID="com.meridian.jobagent.assistant"
APPEX_ID="$APP_ID.Extension"
TMP=""
STAGE=""
STAGED_APPEX=""
STAGED_APP=""

die() { echo "build_safari_assistant: $*" >&2; exit 1; }
note() { echo "build_safari_assistant: $*" >&2; }

# Always runs on exit: removes scratch files and, if a registration check was started, unregisters the throwaway copy.
cleanup() {
  if [ -n "$STAGED_APPEX" ]; then pluginkit -r "$STAGED_APPEX" >/dev/null 2>&1 || true; fi
  if [ -n "$STAGED_APP" ]; then "$LSREGISTER" -u "$STAGED_APP" >/dev/null 2>&1 || true; fi
  if [ -n "$STAGE" ]; then rm -rf "$STAGE"; fi
  if [ -n "$TMP" ]; then rm -rf "$TMP"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# print_steps [IDENTITY]: the manual part. Safari never lets an installer switch an extension on or grant it website access, so
# these steps are the user's own. IDENTITY ("-" = ad-hoc) only decides how firmly the unsigned-extensions step is worded.
print_steps() {
  local identity="${1:-}" unsigned
  if [ "$identity" = "-" ]; then
    unsigned="this build is signed ad-hoc: Safari ignores it until you do this"
  else
    unsigned="Safari may not accept a development-signed build without it, and it asks again after every restart"
  fi
  cat <<STEPS

Manual steps in Safari (nothing below is done for you; this build registered and installed nothing):
  1. Let macOS find the extension. It travels inside Meridian.app (Contents/PlugIns/Meridian Assistant Extension.appex), so open
     Meridian from /Applications once. Use that copy only: more copies of Meridian.app register the extension twice.
  2. In Safari choose Safari > Settings > Extensions (or press Command-comma and open the Extensions tab) and tick
     "Meridian Assistant".
     Not listed? Quit Safari with Command-Q and open it again. Still not listed: choose Develop > Allow Unsigned Extensions
     (${unsigned}).
     No Develop menu? Open Safari > Settings > Advanced and tick "Show features for web developers". Safari switches the option
     off again every time it quits.
  3. Give it website access. With "Meridian Assistant" selected in the Extensions list, open "Edit Websites..." (or answer the
     pop-up Safari shows the first time) and choose "Allow" with "Always Allow on Every Website". Forms embedded in other sites'
     frames need every website; the curated job-site list alone does not cover them.
  4. Optional: Safari > View > Customize Toolbar..., then drag the Meridian Assistant button into the toolbar.
  5. Open a job application, click the Meridian Assistant button and choose Connect. Type the 6-digit code it shows into the
     Meridian window, or approve it in Meridian > Settings > Browser assistant.
To check from a terminal that macOS knows the extension (it prints one line when it does):
  /usr/bin/pluginkit -m -i $APPEX_ID
STEPS
}

while [ $# -gt 0 ]; do
  case "$1" in
    --print-steps) print_steps; exit 0 ;;
    --extension-dir) EXT_DIR="${2:?--extension-dir needs a directory}"; shift 2 ;;
    --out) OUT_DIR="${2:?--out needs a directory}"; shift 2 ;;
    --identity) IDENTITY="${2:?--identity needs a value}"; shift 2 ;;
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --verify-registration) VERIFY_REGISTRATION=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

# --- toolchain ----------------------------------------------------------------------------------------------------
have_toolchain() {
  [ "$(uname -s)" = "Darwin" ] || return 1
  local tool sdk
  for tool in xcrun codesign lipo ditto plutil pluginkit /usr/libexec/PlistBuddy; do
    command -v "$tool" >/dev/null 2>&1 || [ -x "$tool" ] || return 1
  done
  xcrun --find swiftc >/dev/null 2>&1 || return 1
  sdk="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)" || return 1
  [ -d "$sdk/System/Library/Frameworks/SafariServices.framework" ] || return 1
}
if [ "$CHECK_ONLY" = 1 ]; then
  have_toolchain && exit 0
  exit 1
fi
have_toolchain || die "the Swift toolchain, the macOS SDK with SafariServices or the code signing tools are missing (install Xcode or the Command Line Tools)"

# --- inputs -------------------------------------------------------------------------------------------------------
[ -f "$EXT_DIR/manifest.json" ] || die "no built Safari web extension at $EXT_DIR (run: make extension)"
EXT_DIR="$(cd "$EXT_DIR" && pwd)"
[ "$(plutil -extract manifest_version raw -o - "$EXT_DIR/manifest.json" 2>/dev/null)" = "3" ] || die "$EXT_DIR/manifest.json is not a Manifest V3 file"
if [ -z "$VERSION" ]; then
  VERSION="$(plutil -extract version raw -o - "$TAURI_CONF" 2>/dev/null)" || die "cannot read the version from $TAURI_CONF"
fi
case "$VERSION" in
  ''|*[!0-9.]*|.*|*.|*..*) die "version must look like 1.2.3, got '$VERSION'" ;;
esac
ARCHS="${ARCHS:-$(uname -m)}"
MIN_MACOS="${MIN_MACOS:-$(plutil -extract bundle.macOS.minimumSystemVersion raw -o - "$TAURI_CONF" 2>/dev/null || echo 15.0)}"
ICON="${ICON-$ROOT/src-tauri/icons/icon.icns}"
for file in host/main.swift ext/SafariWebExtensionHandler.swift resources/app-Info.plist resources/appex-Info.plist \
    resources/app.entitlements resources/appex.entitlements; do
  [ -f "$SRC/$file" ] || die "missing source file extension/safari/$file"
done
# Never ship test hooks or debug artefacts inside the signed appex.
STRAY="$(find "$EXT_DIR" \( -name '*.map' -o -name 'e2e*' -o -name '.DS_Store' \) -print -quit)"
if [ -n "$STRAY" ]; then
  die "$EXT_DIR contains source maps, e2e files or .DS_Store ($STRAY); rebuild it with: make extension"
fi

# --- signing identity ---------------------------------------------------------------------------------------------
if [ -z "$IDENTITY" ]; then
  PYTHON="$ROOT/.venv/bin/python"
  [ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"
  RESOLVED=""
  if [ -n "$PYTHON" ]; then
    RESOLVED="$("$PYTHON" "$ROOT/scripts/build_signed_app.py" --print-identity 2>/dev/null)" || RESOLVED=""
  fi
  if [ -n "$RESOLVED" ]; then
    IDENTITY="$RESOLVED"
  else
    IDENTITY="-"
    note "WARNING: no signing identity could be resolved (set MERIDIAN_SIGNING_IDENTITY); signing ad-hoc."
    note "WARNING: Safari ignores an ad-hoc signed extension unless Develop > Allow Unsigned Extensions is on."
  fi
fi
SIGN=(--force --sign "$IDENTITY" --options runtime)
case "$IDENTITY" in
  "Developer ID"*) SIGN+=(--timestamp) ;;
  *) SIGN+=(--timestamp=none) ;;
esac
if [ "$IDENTITY" = "-" ]; then note "signing identity: ad-hoc"; else note "signing identity: $IDENTITY"; fi

# --- build --------------------------------------------------------------------------------------------------------
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
APP="$OUT_DIR/Meridian Assistant.app"
APPEX="$APP/Contents/PlugIns/Meridian Assistant Extension.appex"
SDK="$(xcrun --sdk macosx --show-sdk-path)"
SDKVER="$(xcrun --sdk macosx --show-sdk-version)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APPEX/Contents/MacOS" "$APPEX/Contents/Resources"
# The wrapper app is a build intermediate: keep Spotlight away so it is not picked up as a second Meridian Assistant.
touch "$OUT_DIR/.metadata_never_index"
TMP="$(mktemp -d)"

# compile NAME SWIFTC-ARGS...: one slice per architecture in $ARCHS, joined with lipo, result at $TMP/NAME.
compile() {
  local name="$1" arch parts=()
  shift
  for arch in $ARCHS; do
    xcrun swiftc -O -swift-version 5 -sdk "$SDK" -target "$arch-apple-macos$MIN_MACOS" "$@" -o "$TMP/$name.$arch"
    parts+=("$TMP/$name.$arch")
  done
  if [ "${#parts[@]}" -gt 1 ]; then lipo -create "${parts[@]}" -output "$TMP/$name"; else mv "${parts[0]}" "$TMP/$name"; fi
}
compile host "$SRC/host/main.swift" -framework SafariServices -framework Cocoa
compile ext "$SRC/ext/SafariWebExtensionHandler.swift" -parse-as-library -module-name MeridianAssistantExtension \
  -application-extension -Xlinker -e -Xlinker _NSExtensionMain -framework SafariServices
cp "$TMP/host" "$APP/Contents/MacOS/Meridian Assistant"
cp "$TMP/ext" "$APPEX/Contents/MacOS/Meridian Assistant Extension"

for pair in "app-Info.plist:$APP/Contents/Info.plist" "appex-Info.plist:$APPEX/Contents/Info.plist"; do
  sed -e "s/@VERSION@/$VERSION/g" -e "s/@BUILD@/$VERSION/g" -e "s/@MIN_MACOS@/$MIN_MACOS/g" \
    "$SRC/resources/${pair%%:*}" > "${pair#*:}"
  # What Xcode stamps and LaunchServices/pluginkit expect to see.
  /usr/libexec/PlistBuddy \
    -c "Add :CFBundleSupportedPlatforms array" -c "Add :CFBundleSupportedPlatforms:0 string MacOSX" \
    -c "Add :DTPlatformName string macosx" -c "Add :DTSDKName string macosx$SDKVER" \
    -c "Add :DTPlatformVersion string $SDKVER" "${pair#*:}"
  plutil -lint "${pair#*:}" >/dev/null
done
printf 'APPL????' > "$APP/Contents/PkgInfo"
if [ -n "$ICON" ] && [ -f "$ICON" ]; then cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"; fi
# manifest.json must sit at the root of the appex Resources. No extended attributes or quarantine flags are copied.
ditto --noextattr --noqtn --norsrc "$EXT_DIR" "$APPEX/Contents/Resources"

# quiet COMMAND...: run it, show its output only if it fails (codesign chatters about replacing swiftc's ad-hoc signature).
quiet() {
  local output
  if ! output="$("$@" 2>&1)"; then
    echo "$output" >&2
    die "failed: $*"
  fi
}

# Sign inner code first and never use --deep: every nested bundle keeps its own entitlements.
quiet codesign "${SIGN[@]}" --entitlements "$SRC/resources/appex.entitlements" "$APPEX"
quiet codesign "${SIGN[@]}" --entitlements "$SRC/resources/app.entitlements" "$APP"

# --- verify what was built ----------------------------------------------------------------------------------------
fail() { die "verification failed: $*"; }
ok() { echo "  ok  $*" >&2; }
plist() { plutil -extract "$2" raw -o - "$1" 2>/dev/null; }
entitlements() { codesign -d --entitlements - --xml "$1" 2>/dev/null | plutil -convert json -o - - 2>/dev/null; }

if ! VERIFY_OUTPUT="$(codesign --verify --deep --strict "$APP" 2>&1)"; then
  echo "$VERIFY_OUTPUT" >&2
  fail "codesign --verify --deep --strict $APP"
fi
ok "codesign --verify --deep --strict (app and appex)"
[ "$(entitlements "$APPEX")" = '{"com.apple.security.app-sandbox":true}' ] \
  || fail "appex entitlements must be exactly com.apple.security.app-sandbox, got: $(entitlements "$APPEX")"
[ "$(entitlements "$APP")" = '{"com.apple.security.app-sandbox":true}' ] || fail "wrapper app entitlements must be sandbox only"
ok "entitlements: app-sandbox only (appex and wrapper)"
[ "$(plist "$APPEX/Contents/Info.plist" CFBundleIdentifier)" = "$APPEX_ID" ] || fail "appex CFBundleIdentifier"
case "$APPEX_ID" in com.meridian.jobagent.*) ;; *) fail "appex id $APPEX_ID is not prefixed by com.meridian.jobagent" ;; esac
[ "$(plist "$APPEX/Contents/Info.plist" NSExtension.NSExtensionPointIdentifier)" = "com.apple.Safari.web-extension" ] \
  || fail "appex NSExtensionPointIdentifier"
[ "$(plist "$APPEX/Contents/Info.plist" NSExtension.NSExtensionPrincipalClass)" = "SafariWebExtensionHandler" ] \
  || fail "appex NSExtensionPrincipalClass"
[ "$(plist "$APPEX/Contents/Info.plist" CFBundlePackageType)" = "XPC!" ] || fail "appex CFBundlePackageType"
[ "$(plist "$APPEX/Contents/Info.plist" LSMinimumSystemVersion)" = "$MIN_MACOS" ] || fail "appex LSMinimumSystemVersion"
[ "$(plist "$APP/Contents/Info.plist" CFBundleIdentifier)" = "$APP_ID" ] || fail "wrapper CFBundleIdentifier"
ok "Info.plist: $APPEX_ID, extension point com.apple.Safari.web-extension, macOS $MIN_MACOS+, version $VERSION"
for entry in manifest.json background.js content.js hint.js options.html; do
  [ -f "$APPEX/Contents/Resources/$entry" ] || fail "$entry missing from the appex Resources"
done
ok "appex Resources hold the web extension (manifest.json, background.js, content.js, hint.js, options.html)"
APPEX_DETAILS="$(codesign -dv "$APPEX" 2>&1)"
grep -q 'flags=.*runtime' <<<"$APPEX_DETAILS" || fail "appex is not signed with the hardened runtime"
for binary in "$APP/Contents/MacOS/Meridian Assistant" "$APPEX/Contents/MacOS/Meridian Assistant Extension"; do
  [ "$(lipo -archs "$binary" | tr ' ' '\n' | sort | tr '\n' ' ')" = "$(echo "$ARCHS" | tr ' ' '\n' | sort | tr '\n' ' ')" ] \
    || fail "architectures of $binary are not: $ARCHS"
done
ok "hardened runtime; architectures: $ARCHS"
if [ "$IDENTITY" = "-" ]; then
  note "built ad-hoc signed bundles (no TeamIdentifier): Safari only loads them with Develop > Allow Unsigned Extensions"
else
  grep -q '^TeamIdentifier=[A-Z0-9]\{10\}$' <<<"$APPEX_DETAILS" || fail "appex has no TeamIdentifier: the signing identity is not a team certificate"
  ok "TeamIdentifier $(sed -n 's/^TeamIdentifier=//p' <<<"$APPEX_DETAILS")"
fi

# --- optional: does the system see the extension? ------------------------------------------------------------------
if [ "$VERIFY_REGISTRATION" = 1 ]; then
  [ -x "$LSREGISTER" ] || die "lsregister not found at $LSREGISTER"
  STAGE="$(mktemp -d "${TMPDIR:-/tmp}/meridian-safari-check.XXXXXX")"
  STAGE="$(cd "$STAGE" && pwd -P)"   # pluginkit reports canonical paths (/private/var, no double slashes)
  STAGED_APP="$STAGE/Meridian Assistant.app"
  STAGED_APPEX="$STAGED_APP/Contents/PlugIns/Meridian Assistant Extension.appex"
  ditto "$APP" "$STAGED_APP"
  "$LSREGISTER" -f -R -trusted "$STAGED_APP"
  LISTED=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    LISTED="$(pluginkit -mAvv -p com.apple.Safari.web-extension 2>/dev/null || true)"
    case "$LISTED" in *"$STAGED_APPEX"*) break ;; esac
    LISTED=""
    sleep 1
  done
  [ -n "$LISTED" ] || fail "pluginkit does not list the staged appex ($STAGED_APPEX)"
  ok "pluginkit lists $APPEX_ID (SDK com.apple.Safari.web-extension, parent Meridian Assistant)"
  STATUS="$("$STAGED_APP/Contents/MacOS/Meridian Assistant" --status || true)"
  case "$STATUS" in
    *'"host":"com.meridian.jobagent.assistant"'*'"installed":true'*)
      ok "Safari's extension service knows the extension: $STATUS" ;;
    *) fail "SafariServices does not report the extension as installed: $STATUS" ;;
  esac
  cleanup
  STAGE=""
  STAGED_APP=""
  STAGED_APPEX=""
  ok "temporary registration removed"
fi
echo "built: $APP"
echo "appex: $APPEX"
if [ "$IDENTITY" = "-" ]; then echo "signed with: ad-hoc (no team)"; else echo "signed with: $IDENTITY"; fi
print_steps "$IDENTITY"
