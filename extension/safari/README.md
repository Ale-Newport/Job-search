# Safari half of Meridian Assistant

The Chrome and Safari extensions share one TypeScript source (`extension/src`). This folder only holds what Safari needs on top:
a native shell around the built web extension. There is no Xcode project: `scripts/build_safari_assistant.sh` compiles the
Swift files with `swiftc` and signs with `codesign` (the converter's Xcode 27 project does not build as emitted).

| Path | What it is |
|---|---|
| `ext/SafariWebExtensionHandler.swift` | Principal class of the app extension (appex). Answers a native ping only; it never touches page data, files or the network. |
| `host/main.swift` | Stand-alone wrapper app `Meridian Assistant.app`. A containing app for building, registering and trying the extension on its own. `--status` and `--open-settings` run headless. |
| `resources/*-Info.plist` | Bundle identifiers: wrapper `com.meridian.jobagent.assistant`, appex `com.meridian.jobagent.assistant.Extension`. The appex id is prefixed by Meridian's `com.meridian.jobagent`, which is what lets it live inside `Meridian.app/Contents/PlugIns`. |
| `resources/*.entitlements` | Sandbox only (`com.apple.security.app-sandbox`). No network, file or `get-task-allow` entitlement. |

## What ships

`scripts/build_signed_app.py` builds the appex before `tauri build` (when the Swift toolchain is present and
`extension/dist/safari` exists), signs it with the same identity as the app, and merges it into `bundle.macOS.files`
so it lands in `Meridian.app/Contents/PlugIns/Meridian Assistant Extension.appex`. The Tauri bundler never re-signs nested code, so the appex
keeps its own sandbox entitlements and the outer app's `codesign --verify --deep --strict` covers it. The wrapper app is not shipped.
After the build the script finds the app tauri reports (a `--debug`, `--target` or `CARGO_TARGET_DIR` build included; failing that the newest
bundle written after the build started) and verifies the signature, that the appex is inside it (a missing appex fails the build, it is never
reported as embedded), the sandbox-only entitlements, the matching team and the `meridian://` URL scheme. An app that cannot be found is an
error, not a silent pass. Before tauri runs, the sidecar in `src-tauri/binaries` is checked with `build_extension.py --verify-sidecar
--compare-contents`: a sidecar without the Chrome extension, or one frozen before the extension was last rebuilt, is refused.
`MERIDIAN_SKIP_SAFARI=1` or `--no-safari` ships without Safari support, and so does a Mac without Xcode (with a notice); the last line of
the build then reads `Safari extension: NOT included in this build (<reason>)`, so a release is never silently Safari-less.
`build_signed_app.py --help` prints its usage and tauri's help and does nothing else (no identity lookup, no `.local-data` write, no Safari build).
`build_signed_app.py --verify-app PATH [--expect-safari]` runs the same checks on an app that is already built.

## Commands

```
make extension            # build + check extension/dist/{chrome,safari}
make extension-safari     # ... plus build/safari/Meridian Assistant.app and a throwaway pluginkit registration check
scripts/build_safari_assistant.sh --help
scripts/build_safari_assistant.sh --print-steps   # the manual part below, without building anything
```

A build ends by printing the steps only the user can do (Safari never lets an installer enable an extension or grant it website access):
open Meridian from /Applications once, tick Meridian Assistant in Safari > Settings > Extensions (with Develop > Allow Unsigned Extensions if
Safari does not list it), allow it on every website, and connect from the toolbar button. The same text is in `--print-steps`, which the tests
check, so it cannot drift from the script. `build_signed_app.py` runs this script, so the steps also appear at the end of the Safari part of
`make build`.

The signing identity is the one `build_signed_app.py` resolves (`MERIDIAN_SIGNING_IDENTITY`, the saved choice in `.local-data`, or the only
`Apple Development:` identity in the keychain) unless `--identity NAME|-` or `MERIDIAN_SIGNING_IDENTITY` says otherwise; the script prints
`signing identity: ...` first and `signed with: ...` last. Resolving it reads `.local-data/signing-identity.json`, so automation that must not
read `.local-data` passes `--identity` explicitly.

`--verify-registration` copies the wrapper to a temporary folder, registers it with LaunchServices (`lsregister -f -R -trusted`, which pluginkit
needs for bundles under a temporary directory), checks that `pluginkit` lists the extension and that SafariServices reports it as installed, and then
unregisters and deletes the copy, also when interrupted. Nothing else is ever registered. If you ran something from `build/safari` by hand,
remove stale registrations with `pluginkit -r <path to the appex>` and `lsregister -u <path to the app>`.

## What Meridian can and cannot know about Safari

* **Installed**: `/usr/bin/pluginkit -m -i com.meridian.jobagent.assistant.Extension` prints one line when LaunchServices knows the extension,
  that is after `Meridian.app` was launched from its final folder at least once. It prints nothing otherwise. No permission is needed.
* **Enabled / website access**: not available to Meridian. `SFSafariExtensionManager` and `SFSafariApplication` only answer for the app that
  *contains* the extension. Measured on this Mac: the wrapper app answers (`installed: true, enabled: false`), but a helper executable
  placed in `Meridian.app/Contents/MacOS` is refused (`SFErrorDomain Code=1`) even though it correctly sees `com.meridian.jobagent` as its
  bundle, with a plain run loop, with an AppKit run loop and when signed with Meridian's own identifier; a sandboxed one crashes at launch.
  `--open-settings` shares the same code path and was not run (it would launch the user's Safari). So Meridian shows instructions (Safari > Settings > Extensions > Meridian Assistant, then grant website access, ideally for every
  website) and opens Safari with `open -b com.apple.Safari`.
* Duplicate copies of `Meridian.app` (a build folder and /Applications) register the same extension twice; Safari should be used with the /Applications copy.
* The Apple Development certificate expires after a year and signs with `--timestamp=none`; rebuild before then. No Developer ID identity is installed, so
  the app cannot be notarized or passed to another Mac.
