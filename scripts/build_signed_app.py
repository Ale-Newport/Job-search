"""Build with a stable local signing identity so Keychain grants survive updates.

Only certificate identities are inspected; private keys stay in macOS Keychain.
Set MERIDIAN_SIGNING_IDENTITY explicitly when multiple development identities exist.

When the Swift toolchain is available the Safari half of Meridian Assistant is built first
(scripts/build_safari_assistant.sh) and its pre-signed .appex is merged into the bundle through the `--config` JSON
(bundle.macOS.files -> Contents/PlugIns). Builds without Xcode, or with MERIDIAN_SKIP_SAFARI=1, ship without
Safari support, and the last line printed says so and why.

Before `tauri build` the frozen backend in src-tauri/binaries/meridian-backend-<target triple> is checked with
scripts/build_extension.py --verify-sidecar --compare-contents, so an extension-less or stale sidecar (older bytes of the
extension than extension/dist/chrome) is never packaged. After it, the app
that tauri reports (or, failing that, the newest bundle created after the build started) is verified: signature,
the Meridian URL scheme, the extension inside the packaged backend and, when the Safari extension was embedded, its
presence and sandbox-only entitlements.

After the build, when macOS also knows OTHER copies of Meridian (the one in /Applications, a mounted .dmg, an older build
folder) a reminder is printed to stderr: macOS binds the meridian:// address and the Safari extension to ONE registered copy,
so keep a single one, and `lsregister -u "<path>"` forgets a copy that was removed or replaced (LS-01). The reminder only reads
(Spotlight and `pluginkit -m`), never registers or un-registers anything, and is silent when no other copy is found.

  build_signed_app.py [--no-safari] [tauri build arguments...]
  build_signed_app.py -h | --help           print this text and tauri's own help; nothing else happens
  build_signed_app.py --print-identity      print the resolved identity name and exit (writes nothing)
  build_signed_app.py --verify-app PATH [--expect-safari]
                                            check a built Meridian.app (signature, Info.plist, the Chrome extension in
                                            its backend, the embedded Safari extension when present or expected) and exit
"""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import threading
import time
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
SAFARI_OUT = ROOT / "build/safari"
SAFARI_APPEX = "Meridian Assistant Extension.appex"
URL_SCHEME = "meridian"  # meridian://open lets the browser extension launch the app (src-tauri/Info.plist)
APP_ID = "com.meridian.jobagent"
APPEX_ID = "com.meridian.jobagent.assistant.Extension"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class SafariBuild(NamedTuple):
    files: dict  # {destination under Contents/: source Path}; empty when the Safari extension is not part of the build
    skipped: str  # why `files` is empty ("" when it is not)


def valid_identities():
    identities = subprocess.check_output(["security", "find-identity", "-v", "-p", "codesigning"], text=True)
    return re.findall(r'\b([A-F0-9]{40}) "([^"]+)"', identities)


def resolve_identity():
    """The configured identity (name or fingerprint), or None. Reads, never writes."""
    valid = valid_identities()
    saved = ROOT / ".local-data/signing-identity.json"
    identity = os.environ.get("MERIDIAN_SIGNING_IDENTITY")
    if not identity and saved.exists():
        identity = json.loads(saved.read_text())["identity"]
    if not identity:
        development = [name for _, name in valid if name.startswith("Apple Development:")]
        if len(development) == 1:
            identity = development[0]
    if not identity or not any(identity in (fingerprint, name) for fingerprint, name in valid):
        return None
    return identity


def identity_name(identity):
    """Certificate name for an identity given as a name or a SHA-1 fingerprint."""
    for fingerprint, name in valid_identities():
        if identity in (fingerprint, name):
            return name
    return identity


def remember_identity(identity):
    saved = ROOT / ".local-data/signing-identity.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"identity": identity}) + "\n")
    saved.chmod(0o600)


def tauri_version():
    return json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())["version"]


def build_safari(identity):
    """Build and sign the Safari appex. Returns a SafariBuild: its files may be empty, and then `skipped` says why."""
    if os.environ.get("MERIDIAN_SKIP_SAFARI") == "1":
        print("Safari extension: skipped (MERIDIAN_SKIP_SAFARI=1)", file=sys.stderr)
        return SafariBuild({}, "MERIDIAN_SKIP_SAFARI=1")
    script = ROOT / "scripts/build_safari_assistant.sh"
    if subprocess.run([str(script), "--check"], cwd=ROOT).returncode != 0:
        print("Safari extension: skipped, the Swift toolchain (Xcode) is not available. The app ships without it.",
              file=sys.stderr)
        return SafariBuild({}, "the Swift toolchain (Xcode) is not available")
    web_extension = ROOT / "extension/dist/safari"
    if not (web_extension / "manifest.json").is_file():
        print("Safari extension: skipped, extension/dist/safari is not built. Run `make extension` first.",
              file=sys.stderr)
        return SafariBuild({}, "extension/dist/safari is not built (run make extension)")
    subprocess.run([str(script), "--identity", identity_name(identity), "--out", str(SAFARI_OUT),
                    "--version", tauri_version()], cwd=ROOT, check=True)
    appex = SAFARI_OUT / "Meridian Assistant.app/Contents/PlugIns" / SAFARI_APPEX
    if not appex.is_dir():
        sys.exit("Safari extension build finished without producing " + str(appex))
    return SafariBuild({"PlugIns/" + SAFARI_APPEX: appex}, "")


def tauri_config(identity, extra_files):
    macos = {"signingIdentity": identity}
    if extra_files:
        # Sources resolve relative to src-tauri/ (where tauri.conf.json lives). The bundler copies them verbatim and
        # does not re-sign them, which is why they are signed above. It signs the outer app after copying.
        macos["files"] = {dest: os.path.relpath(source, ROOT / "src-tauri") for dest, source in extra_files.items()}
    return {"bundle": {"macOS": macos}}


def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


# --- the sidecar -----------------------------------------------------------------------------------------------------


def host_triple():
    """The Rust host target triple, which names the sidecar tauri bundles when no --target is given."""
    try:
        result = run(["rustc", "--print", "host-tuple"])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        match = re.search(r"^host:\s*(\S+)$", run(["rustc", "-vV"]).stdout, re.M)  # rustc before 1.84
    except OSError as error:
        sys.exit(f"Cannot run rustc to find the target triple of the sidecar: {error}")
    if not match:
        sys.exit("Cannot determine the Rust host target triple (rustc --print host-tuple and rustc -vV gave nothing).")
    return match.group(1)


def requested_target(arguments):
    """The value of tauri's -t/--target in `arguments`, or None. Arguments after a literal `--` belong to cargo."""
    arguments = list(arguments)
    for index, argument in enumerate(arguments):
        if argument == "--":
            break
        if argument in ("-t", "--target") and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith("--target="):
            return argument.split("=", 1)[1]
    return None


def sidecar_check(sidecar, **kwargs):
    """build_extension.py --verify-sidecar --compare-contents: the right files, and the bytes of extension/dist/chrome."""
    command = [sys.executable, str(ROOT / "scripts/build_extension.py"), "--verify-sidecar", str(sidecar), "--compare-contents"]
    return subprocess.run(command, cwd=ROOT, **kwargs)


def verify_sidecar(triple):
    """Refuse to go on unless the frozen backend tauri will bundle carries the current Chrome extension (exits otherwise)."""
    sidecar = ROOT / "src-tauri/binaries" / f"meridian-backend-{triple}"
    if not sidecar.is_file():
        sys.exit(f"The sidecar {sidecar} does not exist. Build it first: make build-app (or scripts/build_sidecar.sh).")
    # Names and bytes: a sidecar frozen before the extension was last rebuilt must not be packaged either.
    if sidecar_check(sidecar).returncode != 0:
        sys.exit(f"Refusing to package: {sidecar} does not carry the current Chrome extension. "
                 "Rebuild it with scripts/build_sidecar.sh (make build-app does everything in order).")


def packaged_sidecar_problems(app):
    """The backend inside a built app (what the user runs) must carry the current extension, as the one checked before."""
    sidecar = Path(app) / "Contents/MacOS/meridian-backend"
    if not sidecar.is_file():
        return [f"{sidecar} is missing: the app has no backend"]
    checked = sidecar_check(sidecar, text=True, capture_output=True)
    if checked.returncode == 0:
        return []
    return ["the packaged backend does not carry the current Chrome extension: " + (checked.stderr or checked.stdout).strip()]


# --- running tauri and finding what it built -----------------------------------------------------------------------------


def wants_help(arguments):
    """True for -h/--help among the arguments meant for this script or tauri (not those after a literal `--`)."""
    for argument in arguments:
        if argument == "--":
            return False
        if argument in ("-h", "--help"):
            return True
    return False


def print_help():
    """Our usage, then tauri's. Reads nothing and writes nothing outside the terminal."""
    print(__doc__.strip(), flush=True)
    tauri = ROOT / "node_modules/.bin/tauri"
    if tauri.is_file():
        print("\n--- tauri build --help ---\n", flush=True)
        subprocess.run([str(tauri), "build", "--help"], cwd=ROOT)


def run_tauri(arguments):
    """Run `tauri build`, echo its output as it comes, and return (exit code, [stdout text, stderr text])."""
    process = subprocess.Popen(arguments, cwd=ROOT, text=True, errors="replace", stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    captured = {"out": [], "err": []}

    def pump(stream, key, sink):
        for line in stream:
            captured[key].append(line)
            sink.write(line)
            sink.flush()

    threads = [threading.Thread(target=pump, args=(process.stdout, "out", sys.stdout), daemon=True),
               threading.Thread(target=pump, args=(process.stderr, "err", sys.stderr), daemon=True)]
    for thread in threads:
        thread.start()
    process.wait()
    for thread in threads:
        thread.join()
    return process.returncode, ["".join(captured["out"]), "".join(captured["err"])]


def bundle_paths(text):
    """Paths tauri reports after "Finished N bundle(s) at:" (one per indented line, the size suffix removed)."""
    lines = ANSI.sub("", text).splitlines()
    paths = []
    for index, line in enumerate(lines):
        reported = re.search(r"Finished\s+\d+\s+bundles?\s+at:?\s*(.*)$", line)
        if not reported:
            continue
        entries = [reported.group(1).strip()] if reported.group(1).strip() else []
        for following in lines[index + 1:]:
            if not following.strip() or not following[0].isspace():
                break
            entries.append(following.strip())
        for entry in entries:
            entry = re.sub(r"\s+\([\d.,]+\s*[KMGT]?i?B\)$", "", entry)
            path = Path(entry)
            paths.append(path if path.is_absolute() else ROOT / "src-tauri" / path)
    return paths


def target_directories(environ=None):
    """Cargo target directories a tauri build may have written to (the default one and any CARGO_TARGET_DIR)."""
    environ = os.environ if environ is None else environ
    roots = [ROOT / "src-tauri/target", ROOT / "target"]
    for name in ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR"):
        value = environ.get(name)
        if value:
            path = Path(value)
            roots += [path] if path.is_absolute() else [ROOT / path, ROOT / "src-tauri" / path]
    return list(dict.fromkeys(roots))


def bundle_time(app):
    """When the bundle was last written: the newest of the app, its Contents folder, Info.plist and signature."""
    names = ("", "Contents", "Contents/Info.plist", "Contents/_CodeSignature")
    return max((app / name).stat().st_mtime for name in names if (app / name).exists())


def find_built_apps(outputs, started, roots=None):
    """The app bundle(s) a tauri build produced: those it reports, else the newest one written after `started`."""
    reported = []
    for text in outputs:
        for path in bundle_paths(text):
            if path.suffix == ".app" and path.is_dir() and path not in reported:
                reported.append(path)
    if reported:
        return reported
    fresh = []
    for root in target_directories() if roots is None else roots:
        # <target>/<profile>/bundle/macos/X.app, and <target>/<triple>/<profile>/bundle/macos/X.app for --target
        for pattern in ("*/bundle/macos/*.app", "*/*/bundle/macos/*.app"):
            fresh += [(bundle_time(app), app) for app in root.glob(pattern) if app.is_dir()]
    fresh = [(written, app) for written, app in fresh if written >= started]
    return [max(fresh, key=lambda item: item[0])[1]] if fresh else []


# --- verifying the built app ---------------------------------------------------------------------------------------------


def verify_app(app, expect_safari=False):
    """Verify a built Meridian.app. Returns a list of problems (empty when the app is sound).

    expect_safari: the Safari appex was built and handed to the bundler, so it MUST be in the app. Without it an app
    that has no appex is fine (a build without Xcode), and one that has an appex is still checked.
    """
    problems = []
    app = Path(app)
    if not app.is_dir():
        return [f"{app} does not exist"]
    check = run(["codesign", "--verify", "--deep", "--strict", str(app)])
    if check.returncode:
        problems.append("codesign --verify --deep --strict failed: " + check.stderr.strip())
    problems += scheme_problems(app)
    appex = app / "Contents/PlugIns" / SAFARI_APPEX
    if expect_safari and not appex.is_dir():
        problems.append(f"the Safari extension was built and handed to the bundler but {appex} is missing from the app")
    if appex.exists():
        entitlements = run(["codesign", "-d", "--entitlements", "-", "--xml", str(appex)])
        converted = run(["plutil", "-convert", "json", "-o", "-", "-"], input=entitlements.stdout)
        if converted.stdout.strip() != '{"com.apple.security.app-sandbox":true}':
            problems.append("the appex entitlements are not sandbox-only: " + converted.stdout.strip())
        details = run(["codesign", "-dv", str(appex)]).stderr
        outer = run(["codesign", "-dv", str(app)]).stderr
        team = re.search(r"^TeamIdentifier=(\S+)$", details, re.M)
        outer_team = re.search(r"^TeamIdentifier=(\S+)$", outer, re.M)
        if (team and team.group(1)) != (outer_team and outer_team.group(1)):
            problems.append("the appex and the app are signed by different teams")
        if not (appex / "Contents/Resources/manifest.json").is_file():
            problems.append("the appex does not contain the web extension")
    return problems


def scheme_problems(app):
    """The app must register meridian:// (the browser extension launches Meridian through it)."""
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError) as error:
        return [f"{app}/Contents/Info.plist cannot be read: {error}"]
    schemes = [scheme for entry in info.get("CFBundleURLTypes", []) for scheme in entry.get("CFBundleURLSchemes", [])]
    return [] if URL_SCHEME in schemes else [f"Contents/Info.plist does not register the {URL_SCHEME}:// URL scheme"]


# --- other registered copies (LS-01) ----------------------------------------------------------------------------------------


def known_copies(built_apps):
    """Other copies of Meridian that macOS knows about, as sorted paths: those Spotlight lists for the app's bundle id and those
    PlugInKit lists for the Safari extension (the app that holds it), minus the freshly built `built_apps` and anything that no
    longer exists. Only reads; a tool that is missing or fails just finds nothing."""
    ignore = set()
    for app in built_apps:
        try:
            ignore.add(Path(app).resolve())
        except OSError:
            ignore.add(Path(app))
    found = set()
    try:
        spotlight = run(["mdfind", f"kMDItemCFBundleIdentifier == '{APP_ID}'"], timeout=20)
        found |= {Path(line) for line in spotlight.stdout.splitlines() if line.strip().endswith(".app")} if spotlight.returncode == 0 else set()
        plugins = run(["pluginkit", "-mAvv", "-i", APPEX_ID], timeout=20)
        found |= {Path(match) for match in re.findall(r"^\s*Path = (.+?\.app)/Contents/PlugIns/", plugins.stdout, re.M)}
    except (OSError, subprocess.SubprocessError):
        pass
    copies = set()
    for path in found:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in ignore:
            copies.add(resolved)
    return sorted(copies)


def registration_reminder(copies, built_apps):
    """The text that explains why other registered copies matter and how to forget one; empty when there are none."""
    if not copies:
        return ""
    built = ", ".join(str(app) for app in built_apps) or "the new build"
    lines = ["", f"Other copies of Meridian are known to macOS (the new build is {built}):"]
    lines += [f"  {copy}" for copy in copies]
    lines += [
        "macOS ties the meridian:// address (the browser extension's \"Open Meridian\") and the Safari extension to ONE registered copy,",
        "so keep a single copy (normally the one in /Applications). To forget a copy that you removed or replaced:",
        f"  {LSREGISTER} -u \"<path of that copy>\"",
        f"Then check that `pluginkit -m -i {APPEX_ID}` lists the copy you use before testing Safari.",
    ]
    return "\n".join(lines)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    # Anything not listed here (for example `--bundles app`) is passed to `tauri build` untouched. Help comes first: it
    # must not resolve the identity, write .local-data or build anything.
    if wants_help(arguments):
        print_help()
        return
    parser = argparse.ArgumentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--print-identity", action="store_true")
    parser.add_argument("--no-safari", action="store_true")
    parser.add_argument("--verify-app")
    parser.add_argument("--expect-safari", action="store_true")
    options, tauri_arguments = parser.parse_known_args(arguments)
    if options.expect_safari and not options.verify_app:
        sys.exit("--expect-safari only goes with --verify-app PATH.")
    if options.verify_app:
        problems = verify_app(options.verify_app, expect_safari=options.expect_safari)
        problems += packaged_sidecar_problems(options.verify_app) if Path(options.verify_app).is_dir() else []
        for problem in problems:
            print("problem: " + problem, file=sys.stderr)
        sys.exit(1 if problems else 0)
    identity = resolve_identity()
    if options.print_identity:
        if not identity:
            sys.exit("No usable signing identity is configured.")
        print(identity_name(identity))
        return
    if not identity:
        sys.exit("A stable Apple code-signing identity is required. Set MERIDIAN_SIGNING_IDENTITY to an installed valid certificate; ad-hoc signing would reset Keychain trust.")
    verify_sidecar(requested_target(tauri_arguments) or host_triple())
    remember_identity(identity)
    safari = SafariBuild({}, "--no-safari was given") if options.no_safari else build_safari(identity)
    config = json.dumps(tauri_config(identity, safari.files))
    started = time.time() - 1  # the clock and the file system's timestamps may differ slightly
    code, outputs = run_tauri([str(ROOT / "node_modules/.bin/tauri"), "build", "--config", config, *tauri_arguments])
    if code:
        sys.exit(f"tauri build failed with exit code {code}.")
    if "--no-bundle" in tauri_arguments:
        print("No app bundle was requested (--no-bundle): there is nothing to verify.")
    else:
        apps = find_built_apps(outputs, started)
        if not apps:
            sys.exit("tauri build finished but no built Meridian.app was found: not at the path it reported and not "
                     "among the bundles written after the build started under "
                     + ", ".join(str(root) for root in target_directories())
                     + ". The app was NOT verified.")
        for app in apps:
            problems = verify_app(app, expect_safari=bool(safari.files)) + packaged_sidecar_problems(app)
            if problems:
                sys.exit(f"The built app failed verification ({app}):\n  " + "\n  ".join(problems))
            embedded = (app / "Contents/PlugIns" / SAFARI_APPEX).is_dir()
            print(f"Verified {app}: signature valid, "
                  + ("Safari extension embedded and sandboxed." if embedded else "no Safari extension embedded."))
        reminder = registration_reminder(known_copies(apps), apps)
        if reminder:  # advice, not a result: on stderr, so the last line printed stays the verdict (or the Safari note) below
            print(reminder, file=sys.stderr, flush=True)
    if not safari.files:
        print(f"Safari extension: NOT included in this build ({safari.skipped}). The app has no Safari support.",
              flush=True)


if __name__ == "__main__":
    main()
