"""Build and check the Meridian Assistant browser extension (Chrome and Safari variants).

  build_extension.py                    install dependencies when needed, type-check, build
                                        extension/dist/{chrome,safari}
                                        and run the production-build checks on both
  build_extension.py --check-only       only run the production-build checks on the existing extension/dist
  build_extension.py --typecheck-only   install dependencies when needed and only run `tsc --noEmit`
  build_extension.py --install-only     only install the npm dependencies, and only when node_modules is missing or
                                        differs from package-lock.json (a no-op otherwise, offline-first)
  build_extension.py --verify-sidecar PATH [--compare-contents]
                                        check that the frozen backend at PATH (PyInstaller onefile) embeds every file
                                        of extension/dist/chrome under extension/chrome and nothing else under extension/;
                                        with --compare-contents every embedded file must also be byte-identical to the
                                        one in extension/dist/chrome (the packaging gate of build_signed_app.py uses it:
                                        a sidecar built before the extension was rebuilt is refused)
  build_extension.py --e2e              install dependencies when needed and only build the test variants
                                        (extension/dist/e2e*)
  build_extension.py --safari           also build and sign the Safari app extension (build/safari), Xcode required
  build_extension.py --safari --registration-check
                                        ... and prove with a throwaway copy that pluginkit and Safari's
                                        extension service see it (nothing stays registered)

Dependencies come from extension/package-lock.json (`npm ci`, offline-first) and are only reinstalled when
node_modules does not hold the pinned versions. The test variants are never part of a normal build and never
checked as production output.
The Chrome output is what scripts/build_sidecar.sh embeds into the backend; the Safari output feeds
scripts/build_safari_assistant.sh.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
DIST = EXTENSION / "dist"
VARIANTS = ("chrome", "safari")
PERMISSIONS = ["activeTab", "scripting", "storage", "alarms"]
OPTIONAL_HOSTS = ["https://*/*", "http://*/*"]
LOOPBACK = "http://127.0.0.1/*"
# Chrome-only APIs that Safari lacks; the extension must not use them anywhere (docs/EXTENSION_DESIGN.md 7.1).
UNSUPPORTED_API = re.compile(
    r"\b(?:chrome|browser)\s*\.\s*(?:webNavigation|sidePanel|downloads|idle|notifications|offscreen)\b"
    r"|\.getContexts\s*\("
)
BUILD_ONLY_GLOBALS = re.compile(r"\b__(?:E2E|SHADOW|SAFARI)__\b")
DYNAMIC_CODE = re.compile(r"\beval\s*\(|\bnew\s+Function\s*\(")
BUNDLES = ("background.js", "content.js", "hint.js", "options.js")
MIN_BUNDLE_BYTES = 1024  # every real bundle is far larger; this only rejects a placeholder that slipped through


class BuildError(Exception):
    pass


def say(message):
    print(f"build_extension: {message}", flush=True)


def run(command, **kwargs):
    say("$ " + " ".join(str(part) for part in command))
    result = subprocess.run(command, **kwargs)
    if result.returncode:
        raise BuildError(
            f"command failed with exit code {result.returncode}: {' '.join(str(part) for part in command)}"
        )


def npm():
    path = shutil.which("npm")
    if not path:
        raise BuildError("npm was not found. Install Node.js (https://nodejs.org) to build the browser extension.")
    return path


def dependencies_stale():
    """True when node_modules does not hold what package-lock.json pins (compared by content, never by timestamp).

    npm keeps a hidden lock file inside node_modules listing what is installed. Packages the lock marks optional or
    restricted to another os/cpu (esbuild's platform binaries) are not required to be present.
    """
    modules = EXTENSION / "node_modules"
    if not (modules / ".bin/esbuild").exists() or not (modules / ".bin/tsc").exists():
        return True
    try:
        pinned = json.loads((EXTENSION / "package-lock.json").read_text())["packages"]
        installed = json.loads((modules / ".package-lock.json").read_text())["packages"]
    except (OSError, ValueError, KeyError):
        return True
    for name, entry in pinned.items():
        if not name.startswith("node_modules/"):
            continue
        if name in installed:
            if installed[name].get("version") != entry.get("version"):
                return True
        elif not (entry.get("optional") or entry.get("os") or entry.get("cpu")):
            return True
    return False


def install_dependencies(force):
    if not (EXTENSION / "package-lock.json").is_file():
        raise BuildError("extension/package-lock.json is missing; the extension must be built from its lock file")
    if force or dependencies_stale():
        run([npm(), "ci", "--no-audit", "--no-fund", "--prefer-offline"], cwd=EXTENSION)
    else:
        say("dependencies are up to date (extension/node_modules)")


def chrome_extension_id(key):
    """The id Chrome derives from a manifest `key` (base64 DER public key): sha256, first 16 bytes, hex mapped a-p."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def read_json(path, problems):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        # check_variant can be pointed at a dist folder outside the repository (tests, scratch builds): never fail on the label.
        label = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        problems.append(f"{label} cannot be read as JSON: {error}")
        return None


def check_variant(variant, dist=DIST):
    """Production-build checks for one built variant. Returns a list of human-readable problems."""
    out = dist / variant
    where = f"extension/dist/{variant}"
    problems = []
    if not (out / "manifest.json").is_file():
        return [f"{where}/manifest.json is missing: build the extension first (make extension)"]
    manifest = read_json(out / "manifest.json", problems)
    tauri = read_json(ROOT / "src-tauri/tauri.conf.json", problems)
    ats = read_json(EXTENSION / "ats-hosts.json", problems)
    if manifest is None or tauri is None or ats is None:
        return problems
    ats = ats["matches"]

    if manifest.get("manifest_version") != 3:
        problems.append(f"{where}: manifest_version must be 3")
    if manifest.get("version") != tauri["version"]:
        problems.append(
            f"{where}: manifest version {manifest.get('version')} differs from tauri.conf.json {tauri['version']}"
        )
    if not str(manifest.get("name", "")).startswith("__MSG_") or not str(manifest.get("description", "")).startswith(
        "__MSG_"
    ):
        problems.append(f"{where}: name and description must be localised (__MSG_*__)")
    locale = manifest.get("default_locale")
    if not locale or not (out / "_locales" / locale / "messages.json").is_file():
        problems.append(f"{where}: _locales/{locale}/messages.json is missing")
    if manifest.get("permissions") != PERMISSIONS:
        problems.append(f"{where}: permissions must be exactly {PERMISSIONS}, got {manifest.get('permissions')}")
    hosts = manifest.get("host_permissions", [])
    allowed = {LOOPBACK, *ats}
    if LOOPBACK not in hosts:
        problems.append(f"{where}: host_permissions must include {LOOPBACK}")
    for host in hosts:
        if host not in allowed:
            problems.append(
                f"{where}: host_permissions has {host}: neither loopback nor a curated ATS host (e2e-only entry?)"
            )
    if manifest.get("optional_host_permissions") != OPTIONAL_HOSTS:
        problems.append(f"{where}: optional_host_permissions must be exactly {OPTIONAL_HOSTS}")
    scripts = manifest.get("content_scripts", [])
    if len(scripts) != 1 or scripts[0].get("js") != ["hint.js"] or not set(scripts[0].get("matches", [])) <= set(ats):
        problems.append(f"{where}: the only static content script must be hint.js on the curated ATS hosts")
    if manifest.get("action", {}).get("default_popup"):
        problems.append(f"{where}: action.default_popup must not be set (the toolbar click activates the assistant)")
    if manifest.get("background", {}).get("service_worker") != "background.js":
        problems.append(f"{where}: background.service_worker must be background.js")
    if manifest.get("options_ui", {}).get("page") != "options.html":
        problems.append(f"{where}: options_ui.page must be options.html")

    referenced = set(manifest.get("icons", {}).values()) | set(
        manifest.get("action", {}).get("default_icon", {}).values()
    )
    referenced |= {"background.js", "options.html", *BUNDLES}
    referenced |= {script for entry in scripts for script in entry.get("js", [])}
    for relative in sorted(referenced):
        target = out / relative
        if not target.is_file():
            problems.append(f"{where}/{relative} is referenced by the extension but missing")
        elif relative in BUNDLES and target.stat().st_size < MIN_BUNDLE_BYTES:
            problems.append(
                f"{where}/{relative} is only {target.stat().st_size} bytes: it looks like a placeholder, not a bundle"
            )
        elif target.stat().st_size == 0:
            problems.append(f"{where}/{relative} is empty")

    stray = sorted(
        path.name
        for path in out.rglob("*")
        if path.is_file() and (path.suffix == ".map" or path.name.startswith("e2e") or path.name == ".DS_Store")
    )
    if stray:
        problems.append(f"{where}: test or debug files in the production build: {', '.join(stray)}")
    for bundle in BUNDLES:
        path = out / bundle
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        for label, pattern in (
            ("a build-time global that was not replaced (test hook left in)", BUILD_ONLY_GLOBALS),
            ("dynamic code evaluation", DYNAMIC_CODE),
            ("an API that Safari does not provide", UNSUPPORTED_API),
        ):
            match = pattern.search(text)
            if match:
                problems.append(f"{where}/{bundle} contains {label}: {match.group(0)!r}")
        if "sourceMappingURL" in text:
            problems.append(f"{where}/{bundle} references a source map")

    if variant == "chrome":
        key = manifest.get("key")
        if not key:
            problems.append(f"{where}: the Chrome manifest must carry the pinned public key")
        else:
            identifier = chrome_extension_id(key)
            registered = (ROOT / "backend/jobagent/extension/__init__.py").read_text()
            if identifier not in registered:
                problems.append(f"{where}: key derives id {identifier}, which is not in CHROME_EXTENSION_IDS")
        if not manifest.get("minimum_chrome_version"):
            problems.append(f"{where}: minimum_chrome_version is missing")
        if "browser_specific_settings" in manifest:
            problems.append(f"{where}: browser_specific_settings belongs only in the Safari manifest")
    else:
        for forbidden in ("key", "minimum_chrome_version", "commands", "side_panel", "update_url"):
            if forbidden in manifest:
                problems.append(f"{where}: the Safari manifest must not contain {forbidden}")
        if not manifest.get("browser_specific_settings", {}).get("safari", {}).get("strict_min_version"):
            problems.append(f"{where}: browser_specific_settings.safari.strict_min_version is missing")
    return problems


def check_source():
    problems = []
    key_file = EXTENSION / "manifest-key.txt"
    if not key_file.is_file():
        problems.append("extension/manifest-key.txt is missing")
    elif "PRIVATE" in key_file.read_text():
        problems.append("extension/manifest-key.txt contains a PRIVATE key: it must hold the public key only")
    return problems


def archive_viewer():
    """The pyi-archive_viewer that ships with PyInstaller."""
    # Next to the running interpreter, then in the project's own environment (build_sidecar.sh runs .venv/bin/python), then PATH.
    candidates = [Path(sys.executable).parent / "pyi-archive_viewer", ROOT / ".venv" / "bin" / "pyi-archive_viewer"]
    viewer = next((path for path in candidates if path.is_file()), None)
    if viewer is None:
        found = shutil.which("pyi-archive_viewer")
        if not found:
            raise BuildError("pyi-archive_viewer was not found (it ships with PyInstaller: make setup)")
        viewer = Path(found)
    return viewer


def archive_listing(archive):
    """`pyi-archive_viewer --list --recursive --brief` of a PyInstaller archive: one entry per line, one space indent."""
    result = subprocess.run(
        [str(archive_viewer()), "--list", "--recursive", "--brief", str(archive)], capture_output=True, text=True
    )
    if result.returncode:
        raise BuildError(f"cannot list {archive}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


# Run by the interpreter that has PyInstaller: one "sha256 name" line per embedded file under extension/.
EMBEDDED_HASHES = """
import hashlib, sys
from PyInstaller.archive.readers import CArchiveReader
reader = CArchiveReader(sys.argv[1])
for name in sorted(reader.toc):
    if name.startswith("extension/"):
        print(hashlib.sha256(reader.extract(name)).hexdigest(), name)
"""


def archive_hashes(archive):
    """{name: sha256} of every file under extension/ that the PyInstaller archive embeds (read, not executed)."""
    viewer = archive_viewer()
    # The viewer is a script of the environment that has PyInstaller, so the interpreter next to it can import it.
    python = next((path for path in (viewer.parent / "python", viewer.parent / "python3") if path.is_file()), None)
    result = subprocess.run(
        [str(python or sys.executable), "-c", EMBEDDED_HASHES, str(archive)], capture_output=True, text=True
    )
    if result.returncode:
        raise BuildError(f"cannot read the files embedded in {archive}: {(result.stderr or result.stdout).strip()[-400:]}")
    hashes = {}
    for line in result.stdout.splitlines():
        digest, _, name = line.partition(" ")
        if name:
            hashes[name] = digest
    return hashes


def sidecar_problems(listing, dist=DIST):
    """Problems with a frozen backend whose archive listing is `listing`: the embedded extension must be exactly dist/chrome.

    scripts/build_sidecar.sh embeds extension/dist/chrome as extension/chrome (backend/jobagent/extension/assets.py
    installs it from there). A missing file would reach a user's Chrome as a broken extension, and an unexpected entry
    would mean something other than the checked production build was embedded.
    """
    root = dist / "chrome"
    expected = sorted(
        f"extension/chrome/{path.relative_to(root).as_posix()}" for path in root.rglob("*") if path.is_file()
    )
    if not expected:
        return ["extension/dist/chrome holds no files, so nothing can have been embedded in the backend"]
    entries = {line.strip() for line in listing.splitlines() if line.strip()}
    problems = [f"the backend does not contain {entry}" for entry in expected if entry not in entries]
    wanted = set(expected)
    problems += [
        f"the backend contains {entry}, which is not part of extension/dist/chrome"
        for entry in sorted(entries)
        if entry.startswith("extension/") and entry not in wanted
    ]
    return problems


def sidecar_content_problems(hashes, dist=DIST):
    """Files whose embedded bytes differ from extension/dist/chrome (`hashes` is archive_hashes()'s answer).

    sidecar_problems() proves the right files are there; this proves they are the current ones, so a sidecar frozen before
    the extension was rebuilt (same names, older bytes) is not packaged. Files absent from `hashes` are sidecar_problems()'s.
    """
    root = dist / "chrome"
    problems = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        name = f"extension/chrome/{path.relative_to(root).as_posix()}"
        if name in hashes and hashes[name] != hashlib.sha256(path.read_bytes()).hexdigest():
            problems.append(f"the backend's {name} differs from extension/dist/chrome/{path.relative_to(root).as_posix()}")
    return problems


def check_all(variants):
    problems = check_source()
    for variant in variants:
        found = check_variant(variant)
        say(f"production check {variant}: " + ("ok" if not found else f"{len(found)} problem(s)"))
        problems.extend(found)
    return problems


def build_safari_app(registration_check):
    script = ROOT / "scripts/build_safari_assistant.sh"
    if subprocess.run([str(script), "--check"]).returncode:
        raise BuildError("the Swift toolchain (Xcode or the Command Line Tools) is required for --safari")
    command = [str(script)]
    if registration_check:
        command.append("--verify-registration")
    run(command, cwd=ROOT)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-only", action="store_true", help="only check the existing extension/dist")
    parser.add_argument("--typecheck-only", action="store_true", help="only run tsc --noEmit")
    parser.add_argument("--install-only", action="store_true", help="only install npm dependencies when they are stale")
    parser.add_argument(
        "--verify-sidecar", metavar="PATH", help="check that the frozen backend embeds extension/dist/chrome"
    )
    parser.add_argument(
        "--compare-contents",
        action="store_true",
        help="with --verify-sidecar: the embedded files must also be byte-identical to extension/dist/chrome",
    )
    parser.add_argument("--e2e", action="store_true", help="only build the end-to-end test variants")
    parser.add_argument("--install", action="store_true", help="always run `npm ci` first")
    parser.add_argument("--no-install", action="store_true", help="never run `npm ci`")
    parser.add_argument("--skip-typecheck", action="store_true")
    parser.add_argument("--safari", action="store_true", help="also build and sign the Safari app extension")
    parser.add_argument("--registration-check", action="store_true", help="with --safari: verify with pluginkit")
    options = parser.parse_args(argv)
    if options.install_only and options.no_install:
        parser.error("--install-only and --no-install contradict each other")
    if options.compare_contents and not options.verify_sidecar:
        parser.error("--compare-contents only goes with --verify-sidecar PATH")
    try:
        if options.verify_sidecar:
            problems = sidecar_problems(archive_listing(options.verify_sidecar), DIST)
            if problems:
                raise BuildError(
                    f"{options.verify_sidecar} does not carry the Chrome extension:\n  - " + "\n  - ".join(problems)
                )
            if options.compare_contents:
                problems = sidecar_content_problems(archive_hashes(options.verify_sidecar), DIST)
                if problems:
                    raise BuildError(
                        f"{options.verify_sidecar} carries an older build of the Chrome extension "
                        "(rebuild the sidecar: scripts/build_sidecar.sh):\n  - " + "\n  - ".join(problems)
                    )
            say(
                f"{options.verify_sidecar} embeds all of extension/dist/chrome as extension/chrome"
                + (", byte for byte" if options.compare_contents else "")
            )
            return
        if options.install_only:
            install_dependencies(options.install)
            return
        if options.typecheck_only or options.e2e:
            if not options.no_install:
                install_dependencies(options.install)
            run([npm(), "run", "typecheck" if options.typecheck_only else "build:e2e"], cwd=EXTENSION)
            return
        if not options.check_only:
            if not options.no_install:
                install_dependencies(options.install)
            if not options.skip_typecheck:
                run([npm(), "run", "typecheck"], cwd=EXTENSION)
            run([npm(), "run", "build"], cwd=EXTENSION)
        problems = check_all(VARIANTS)
        if problems:
            raise BuildError("the extension build failed its production checks:\n  - " + "\n  - ".join(problems))
        if options.safari:
            build_safari_app(options.registration_check)
    except BuildError as error:
        print(f"build_extension: ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    say("done: extension/dist/chrome" + (", extension/dist/safari" + (" and build/safari" if options.safari else "")))


if __name__ == "__main__":
    main()
