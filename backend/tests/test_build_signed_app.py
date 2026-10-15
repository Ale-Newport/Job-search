"""scripts/build_signed_app.py: the packaging gate around `tauri build`.

Nothing here runs a real tauri build or touches the real repository: the module is loaded with ROOT pointing at a
throwaway repository whose `node_modules/.bin/tauri`, `scripts/build_extension.py`, `scripts/build_safari_assistant.sh` and
`rustc` are stubs that record how they were called. verify_app() runs the real codesign and plutil on tiny ad-hoc signed
apps (a copy of /usr/bin/true as the executable), so the Safari checks are the real ones.
"""

from __future__ import annotations

import importlib.util
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build_signed_app.py"
CODESIGN = shutil.which("codesign")
needs_codesign = pytest.mark.skipif(CODESIGN is None or sys.platform != "darwin", reason="codesign is macOS only")

APPEX = "Meridian Assistant Extension.appex"
TRIPLE = "aarch64-apple-darwin"
FINISHED_ONE = "    Finished 1 bundle at:\n        {path} (112.25 MiB)\n"


def load(root: Path | None = None):
    """The real script as a module; with `root`, a module whose ROOT (and what derives from it) is that folder."""
    spec = importlib.util.spec_from_file_location(f"build_signed_app_{id(root)}", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if root is not None:
        module.ROOT = root
        module.SAFARI_OUT = root / "build/safari"
    return module


def make_executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


INFO = {"CFBundleURLTypes": [{"CFBundleURLSchemes": ["meridian"]}]}


def make_bundle(path: Path, identifier: str, kind: str, extra: dict | None = None) -> Path:
    (path / "Contents/MacOS").mkdir(parents=True)
    executable = path / "Contents/MacOS/main"
    shutil.copy("/usr/bin/true", executable)
    info = {"CFBundleExecutable": "main", "CFBundleIdentifier": identifier, "CFBundlePackageType": kind, **(extra or {})}
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    return path


def sign(path: Path, entitlements: dict | None = None) -> None:
    command = [CODESIGN, "--force", "--sign", "-"]
    if entitlements is not None:
        plist = path.parent / (path.name + ".entitlements")
        plist.write_bytes(plistlib.dumps(entitlements))
        command += ["--entitlements", str(plist)]
    done = subprocess.run([*command, str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def make_appex(path: Path, entitlements: dict | None = None, manifest: bool = True) -> Path:
    make_bundle(path, "com.meridian.jobagent.assistant.Extension", "XPC!")
    if manifest:
        (path / "Contents/Resources").mkdir()
        (path / "Contents/Resources/manifest.json").write_text("{}")
    sign(path, {"com.apple.security.app-sandbox": True} if entitlements is None else entitlements)
    return path


def make_app(path: Path, appex: Path | None = None, scheme: bool = True, signed: bool = True, backend: bool = True) -> Path:
    make_bundle(path, "com.meridian.jobagent", "APPL", INFO if scheme else {})
    if backend:
        shutil.copy("/usr/bin/true", path / "Contents/MacOS/meridian-backend")
    if appex is not None:
        (path / "Contents/PlugIns").mkdir()
        shutil.copytree(appex, path / "Contents/PlugIns" / APPEX)
    if signed:
        sign(path)
    return path


# --- verify_app: the real codesign/plutil checks ---------------------------------------------------------------------------


@needs_codesign
def test_an_app_without_the_safari_extension_is_not_verified_when_it_was_supposed_to_carry_it(tmp_path):
    """The old verify_app only looked at an appex that existed, so a build that lost it printed 'Verified ... embedded'."""
    module = load()
    app = make_app(tmp_path / "Meridian.app")
    assert module.verify_app(app) == []  # a build without Xcode legitimately has none
    problems = module.verify_app(app, expect_safari=True)
    assert len(problems) == 1 and "missing from the app" in problems[0] and APPEX in problems[0]


@needs_codesign
def test_a_complete_signed_app_passes_with_and_without_the_expectation(tmp_path):
    module = load()
    appex = make_appex(tmp_path / "x.appex")
    app = make_app(tmp_path / "Meridian.app", appex)
    assert module.verify_app(app, expect_safari=True) == []
    assert module.verify_app(app) == []


@needs_codesign
def test_an_embedded_appex_is_checked_even_when_it_was_not_expected(tmp_path):
    module = load()
    appex = make_appex(tmp_path / "x.appex", entitlements={"com.apple.security.app-sandbox": True, "com.apple.security.network.client": True})
    problems = module.verify_app(make_app(tmp_path / "Meridian.app", appex))
    assert any("not sandbox-only" in problem for problem in problems), problems


@needs_codesign
def test_an_appex_without_the_web_extension_is_a_problem(tmp_path):
    module = load()
    app = make_app(tmp_path / "Meridian.app", make_appex(tmp_path / "x.appex", manifest=False))
    assert any("does not contain the web extension" in problem for problem in module.verify_app(app))


@needs_codesign
def test_an_app_that_does_not_register_the_meridian_scheme_is_a_problem(tmp_path):
    module = load()
    problems = module.verify_app(make_app(tmp_path / "Meridian.app", scheme=False))
    assert problems == ["Contents/Info.plist does not register the meridian:// URL scheme"]


@needs_codesign
def test_an_unsigned_or_tampered_app_and_a_missing_app_are_problems(tmp_path):
    module = load()
    assert any("codesign" in problem for problem in module.verify_app(make_app(tmp_path / "Unsigned.app", signed=False)))
    app = make_app(tmp_path / "Meridian.app")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({**plistlib.loads((app / "Contents/Info.plist").read_bytes()), "X": 1}))
    assert any("codesign" in problem for problem in module.verify_app(app))
    assert module.verify_app(tmp_path / "Nothing.app") == [f"{tmp_path / 'Nothing.app'} does not exist"]


@needs_codesign
def test_verify_app_on_the_command_line_reports_problems_and_exits_non_zero(tmp_path):
    """The script itself, in a folder that is its ROOT, with a build_extension.py that answers for the packaged backend."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts/build_signed_app.py")
    (root / "scripts/build_extension.py").write_text(
        "import os, sys\nsys.stderr.write('stub verdict\\n')\nsys.exit(int(os.environ.get('STUB_PACKAGED_FAIL', '0')))\n")
    app = make_app(tmp_path / "Meridian.app")
    base = [sys.executable, str(root / "scripts/build_signed_app.py"), "--verify-app", str(app)]
    assert subprocess.run(base, capture_output=True, text=True).returncode == 0
    failed = subprocess.run([*base, "--expect-safari"], capture_output=True, text=True)
    assert failed.returncode == 1 and "problem: " in failed.stderr and "missing from the app" in failed.stderr
    env = os.environ | {"STUB_PACKAGED_FAIL": "1"}
    stale = subprocess.run(base, capture_output=True, text=True, env=env)
    assert stale.returncode == 1 and "does not carry the current Chrome extension" in stale.stderr and "stub verdict" in stale.stderr
    nothing = subprocess.run([*base[:-1], str(tmp_path / "Nothing.app")], capture_output=True, text=True)
    assert nothing.returncode == 1 and nothing.stderr.count("problem: ") == 1, "a missing app is one problem, not two"
    lonely = subprocess.run([sys.executable, str(SCRIPT), "--expect-safari"], capture_output=True, text=True)
    assert lonely.returncode != 0 and "--verify-app" in lonely.stderr


# --- reading what tauri built ------------------------------------------------------------------------------------------------


def test_bundle_paths_reads_tauris_finished_report():
    module = load()
    log = (
        "   Compiling meridian v0.1.0 (/x/src-tauri)\n    Finished `dev` profile [unoptimized + debuginfo] target(s) in 33.84s\n"
        "        Warn skipping app notarization\n"
        "    Finished 1 bundle at:\n"
        "        /x y/src-tauri/target/debug/bundle/macos/Meridian.app (112.25 MiB)\n"
    )
    assert module.bundle_paths(log) == [Path("/x y/src-tauri/target/debug/bundle/macos/Meridian.app")]


def test_bundle_paths_reads_several_bundles_ansi_colours_and_the_one_line_form():
    module = load()
    several = (
        "\x1b[32m    Finished\x1b[0m 2 bundles at:\n"
        "        /t/release/bundle/macos/Meridian.app (110.00 MiB)\n"
        "        /t/release/bundle/dmg/Meridian_0.1.0_aarch64.dmg (45.5 MiB)\n"
        "\n"
        "        /not/a/bundle\n"
    )
    assert module.bundle_paths(several) == [
        Path("/t/release/bundle/macos/Meridian.app"), Path("/t/release/bundle/dmg/Meridian_0.1.0_aarch64.dmg")]
    assert module.bundle_paths("Finished 1 bundle at: /t/Meridian.app (1 KiB)\n") == [Path("/t/Meridian.app")]
    assert module.bundle_paths("Finished `release` profile [optimized] target(s) in 56s\nnothing else\n") == []
    assert module.bundle_paths("") == []


def test_requested_target_reads_both_spellings_and_ignores_cargo_arguments():
    module = load()
    assert module.requested_target(["--bundles", "app"]) is None
    assert module.requested_target(["--target", "x86_64-apple-darwin"]) == "x86_64-apple-darwin"
    assert module.requested_target(["-t", "x86_64-apple-darwin"]) == "x86_64-apple-darwin"
    assert module.requested_target(["--target=universal-apple-darwin", "--debug"]) == "universal-apple-darwin"
    assert module.requested_target(["--", "--target", "elsewhere"]) is None
    assert module.requested_target(["--target"]) is None


def test_the_rust_host_triple_comes_from_rustc_and_falls_back_to_its_verbose_output(tmp_path, monkeypatch):
    module = load()
    tools = tmp_path / "tools"
    make_executable(tools / "rustc", '#!/bin/sh\n[ "$1 $2" = "--print host-tuple" ] && { echo " aarch64-apple-darwin"; exit 0; }\nexit 1\n')
    monkeypatch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    assert module.host_triple() == TRIPLE
    make_executable(tools / "rustc", '#!/bin/sh\n[ "$1" = "-vV" ] && { echo "rustc 1.80.0"; echo "host: x86_64-apple-darwin"; exit 0; }\nexit 1\n')
    assert module.host_triple() == "x86_64-apple-darwin"
    make_executable(tools / "rustc", "#!/bin/sh\nexit 1\n")
    with pytest.raises(SystemExit) as raised:
        module.host_triple()
    assert "host target triple" in str(raised.value)
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit) as raised:
        module.host_triple()
    assert "rustc" in str(raised.value)


# --- main(): a throwaway repository with stub tools ---------------------------------------------------------------------------


STUB_TAURI = """#!/bin/bash
# Stub `tauri build`: records its arguments; STUB_APP (a path) is created as a minimal app, announced like tauri does when
# STUB_REPORT=1; STUB_APPEX is copied into it; STUB_NO_BACKEND leaves out the backend; STUB_FAIL makes it fail.
printf '%s\\n' "$@" > "$STUB_LOG/tauri.args"
echo "tauri stub stdout line"
echo "   Compiling meridian" >&2
if [ -n "$STUB_FAIL" ]; then echo "error: boom" >&2; exit "$STUB_FAIL"; fi
if [ -n "$STUB_APP" ]; then
  mkdir -p "$STUB_APP/Contents/MacOS"
  cp "$STUB_PLIST" "$STUB_APP/Contents/Info.plist"
  if [ -z "$STUB_NO_BACKEND" ]; then echo frozen > "$STUB_APP/Contents/MacOS/meridian-backend"; fi
  if [ -n "$STUB_APPEX" ]; then mkdir -p "$STUB_APP/Contents/PlugIns" && cp -R "$STUB_APPEX" "$STUB_APP/Contents/PlugIns/"; fi
  if [ "$STUB_REPORT" = 1 ]; then echo "    Finished 1 bundle at:" >&2; echo "        $STUB_APP (12.00 MiB)" >&2; fi
fi
exit 0
"""


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path / "repo"
        self.log = tmp_path / "log"
        self.log.mkdir()
        self.calls: list = []
        self.module = load(self.root)
        self.monkeypatch = monkeypatch
        (self.root / "src-tauri/binaries").mkdir(parents=True)
        (self.root / "src-tauri/tauri.conf.json").write_text('{"version": "0.1.0"}')
        (self.root / "src-tauri/binaries" / f"meridian-backend-{TRIPLE}").write_text("frozen")
        (self.root / "extension/dist/safari").mkdir(parents=True)
        (self.root / "extension/dist/safari/manifest.json").write_text("{}")
        make_executable(self.root / "node_modules/.bin/tauri", STUB_TAURI)
        # build_extension.py: records its arguments, fails when STUB_SIDECAR_FAIL (the sidecar in src-tauri/binaries) or
        # STUB_PACKAGED_FAIL (the backend inside a built app) is set
        (self.root / "scripts").mkdir()
        (self.root / "scripts/build_extension.py").write_text(
            "import os, sys\nopen(os.environ['STUB_LOG'] + '/build_extension.args', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "packaged = '/Contents/MacOS/' in sys.argv[2]\nsys.stderr.write('stub verdict\\n')\n"
            "sys.exit(int(os.environ.get('STUB_PACKAGED_FAIL' if packaged else 'STUB_SIDECAR_FAIL', '0')))\n")
        self.safari_script(check=0)
        tools = tmp_path / "tools"
        make_executable(tools / "rustc", f"#!/bin/sh\necho {TRIPLE}\n")
        plist = tmp_path / "Info.plist"
        plist.write_bytes(plistlib.dumps(INFO))
        for key, value in {"STUB_LOG": str(self.log), "STUB_PLIST": str(plist), "PATH": f"{tools}:/usr/bin:/bin"}.items():
            monkeypatch.setenv(key, value)
        for key in ("STUB_APP", "STUB_REPORT", "STUB_APPEX", "STUB_FAIL", "STUB_SIDECAR_FAIL", "STUB_PACKAGED_FAIL", "STUB_NO_BACKEND", "MERIDIAN_SKIP_SAFARI",
                    "CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(self.module, "resolve_identity", lambda: "Apple Development: Test (ABCDE12345)")
        monkeypatch.setattr(self.module, "identity_name", lambda identity: identity)
        monkeypatch.setattr(self.module, "remember_identity", lambda identity: self.calls.append("remember_identity"))
        monkeypatch.setattr(self.module, "verify_app", self.fake_verify)
        self.problems: list = []
        self.verified: list = []

    def fake_verify(self, app, expect_safari=False):
        self.verified.append((Path(app), expect_safari))
        return list(self.problems)

    def safari_script(self, check: int, build_appex: bool = True):
        out = self.root / "build/safari/Meridian Assistant.app/Contents/PlugIns" / APPEX
        body = '#!/bin/bash\n[ "$1" = "--check" ] && exit ' + str(check) + "\n"
        body += 'echo build >> "$STUB_LOG/safari.calls"\n'
        if build_appex:
            body += f'mkdir -p "{out}"\n'
        make_executable(self.root / "scripts/build_safari_assistant.sh", body)

    def env(self, **values):
        for key, value in values.items():
            self.monkeypatch.setenv(key, str(value))

    def run(self, *arguments, capsys):
        try:
            self.module.main(list(arguments))
            code = 0
        except SystemExit as exit_:
            code = exit_.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def tauri_args(self):
        path = self.log / "tauri.args"
        return path.read_text().splitlines() if path.exists() else None

    def app(self, *parts):
        return self.root.joinpath("src-tauri/target", *parts, "bundle/macos/Meridian.app")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def appex_source(tmp_path: Path) -> Path:
    path = tmp_path / "appex-source" / APPEX
    (path / "Contents/Resources").mkdir(parents=True)
    return path


# (c) help ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("arguments", [["--help"], ["-h"], ["--bundles", "app", "--help"], ["--no-safari", "-h"]])
def test_help_has_no_side_effects(harness, capsys, arguments):
    """--help used to resolve the identity, write .local-data/signing-identity.json and build the Safari appex first."""
    def forbidden(*args, **kwargs):
        raise AssertionError("help must not do this")
    for name in ("resolve_identity", "remember_identity", "build_safari", "verify_sidecar", "host_triple", "run_tauri"):
        harness.monkeypatch.setattr(harness.module, name, forbidden)
    code, out, err = harness.run(*arguments, capsys=capsys)
    assert code == 0, err
    assert "build_signed_app.py" in out and "--no-safari" in out and "--verify-app" in out
    assert "--- tauri build --help ---" in out
    assert harness.tauri_args() == ["build", "--help"], "tauri's own help, with no --config and no signing identity"
    assert not (harness.root / ".local-data").exists() and not (harness.root / "build").exists()


def test_a_help_flag_after_the_runner_separator_is_not_a_request_for_help(harness, capsys):
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", "--", "-h", capsys=capsys)
    assert code == 0, err
    assert "Verified" in out and "--- tauri build --help ---" not in out


def test_help_in_a_separate_process_touches_nothing(tmp_path):
    """The script itself, copied to a folder that is its ROOT: no keychain query, no writes, exit 0."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts/build_signed_app.py")
    tools = tmp_path / "tools"
    make_executable(tools / "security", f'#!/bin/sh\ntouch "{tmp_path}/security-was-called"\nexit 1\n')
    env = {"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, str(root / "scripts/build_signed_app.py"), "--help"], capture_output=True,
                          text=True, env=env, cwd="/")
    assert done.returncode == 0, done.stderr
    assert "Usage" not in done.stderr and "build_signed_app.py" in done.stdout
    assert sorted(path.name for path in root.rglob("*")) == ["build_signed_app.py", "scripts"]
    assert not (tmp_path / "security-was-called").exists()


# (d) the sidecar gate ----------------------------------------------------------------------------------------------------


def test_a_sidecar_that_fails_the_extension_check_stops_everything_before_tauri(harness, capsys):
    harness.env(STUB_SIDECAR_FAIL=1)
    harness.monkeypatch.setattr(harness.module, "build_safari",
                                lambda identity: harness.calls.append("build_safari") or harness.module.SafariBuild({}, "x"))
    code, out, err = harness.run("--bundles", "app", capsys=capsys)
    assert isinstance(code, str) and "Refusing to package" in code and f"meridian-backend-{TRIPLE}" in code
    assert harness.tauri_args() is None, "tauri build must not run"
    assert harness.calls == [], "nothing is written and no Safari appex is built after a failed sidecar check"
    args = (harness.log / "build_extension.args").read_text().split()
    assert args == ["--verify-sidecar", str(harness.root / f"src-tauri/binaries/meridian-backend-{TRIPLE}"), "--compare-contents"]


def test_a_missing_sidecar_is_reported_before_tauri_runs(harness, capsys):
    (harness.root / f"src-tauri/binaries/meridian-backend-{TRIPLE}").unlink()
    code, out, err = harness.run(capsys=capsys)
    assert "does not exist" in code and "build-app" in code
    assert harness.tauri_args() is None and not (harness.log / "build_extension.args").exists()


def test_a_good_sidecar_is_verified_once_then_tauri_runs(harness, capsys):
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--bundles", "app", capsys=capsys)
    assert code == 0, err
    before, after = (harness.log / "build_extension.args").read_text().splitlines()
    app_backend = harness.app("release") / "Contents/MacOS/meridian-backend"
    assert before == f"--verify-sidecar {harness.root / f'src-tauri/binaries/meridian-backend-{TRIPLE}'} --compare-contents"
    assert after == f"--verify-sidecar {app_backend} --compare-contents", "and once more inside the finished app"
    assert harness.tauri_args()[:2] == ["build", "--config"] and harness.tauri_args()[-2:] == ["--bundles", "app"]


def test_the_sidecar_of_a_cross_target_build_is_the_one_named_after_that_target(harness, capsys):
    (harness.root / "src-tauri/binaries/meridian-backend-x86_64-apple-darwin").write_text("frozen")
    (harness.root / f"src-tauri/binaries/meridian-backend-{TRIPLE}").unlink()
    harness.env(STUB_APP=harness.app("x86_64-apple-darwin", "release"))
    code, out, err = harness.run("--no-safari", "--target", "x86_64-apple-darwin", capsys=capsys)
    assert code == 0, err
    args = (harness.log / "build_extension.args").read_text().split()
    assert args[0] == "--verify-sidecar" and args[1].endswith("meridian-backend-x86_64-apple-darwin")


# (b) finding the built app -----------------------------------------------------------------------------------------------


def test_the_app_tauri_reports_is_the_one_verified_even_for_a_debug_build(harness, capsys):
    """BUILT_APP was fixed to target/release: a --debug build was not verified and said nothing."""
    debug = harness.app("debug")
    harness.env(STUB_APP=debug, STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", "--debug", "--bundles", "app", capsys=capsys)
    assert code == 0, err
    assert harness.verified == [(debug, False)]
    assert f"Verified {debug}: signature valid, no Safari extension embedded." in out
    assert "tauri stub stdout line" in out and "Compiling meridian" in err, "tauri's output is still shown as it runs"


def test_a_stale_release_app_is_not_verified_in_place_of_the_fresh_build(harness, capsys):
    stale = harness.app("release")
    (stale / "Contents").mkdir(parents=True)
    old = time.time() - 3600
    for path in (stale, stale / "Contents"):
        os.utime(path, (old, old))
    fresh = harness.app("aarch64-apple-darwin", "release")
    harness.env(STUB_APP=fresh)  # tauri says nothing about where it wrote
    code, out, err = harness.run("--no-safari", "--target", TRIPLE, capsys=capsys)
    assert code == 0, err
    assert harness.verified == [(fresh, False)]


def test_among_several_fresh_bundles_the_newest_wins_and_older_ones_are_ignored(tmp_path):
    module = load(tmp_path)
    started = time.time()
    apps = {name: tmp_path / "target" / name / "bundle/macos/Meridian.app" for name in ("old", "first", "second")}
    for name, age in (("old", -3600), ("first", 5), ("second", 10)):
        (apps[name] / "Contents").mkdir(parents=True)
        for path in (apps[name], apps[name] / "Contents"):
            os.utime(path, (started + age, started + age))
    assert module.find_built_apps(["no report"], started, [tmp_path / "target"]) == [apps["second"]]
    assert module.find_built_apps(["no report"], started + 100, [tmp_path / "target"]) == []
    reported = FINISHED_ONE.format(path=apps["first"])
    assert module.find_built_apps([reported], started, [tmp_path / "target"]) == [apps["first"]], "what tauri says wins"
    assert module.find_built_apps([FINISHED_ONE.format(path=tmp_path / "gone.app")], started, [tmp_path / "target"]) == [apps["second"]]


@pytest.mark.parametrize("variable, relative", [("CARGO_TARGET_DIR", False), ("CARGO_TARGET_DIR", True), ("CARGO_BUILD_TARGET_DIR", False)])
def test_a_custom_cargo_target_directory_is_searched(harness, tmp_path, capsys, variable, relative):
    target = (harness.root / "elsewhere/target") if relative else (tmp_path / "outside/target")
    app = target / "release/bundle/macos/Meridian.app"
    harness.env(**{variable: "elsewhere/target" if relative else target}, STUB_APP=app)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert code == 0, err
    assert harness.verified == [(app, False)]


def test_no_built_app_is_an_error_not_a_silent_pass(harness, capsys):
    code, out, err = harness.run(capsys=capsys)  # tauri stub builds nothing
    assert isinstance(code, str) and "no built Meridian.app was found" in code and "NOT verified" in code
    assert "Verified" not in out
    assert harness.verified == []


def test_no_bundle_builds_have_nothing_to_verify_and_say_so(harness, capsys):
    code, out, err = harness.run("--no-safari", "--no-bundle", capsys=capsys)
    assert code == 0, err
    assert "nothing to verify" in out and harness.verified == []


def test_a_failing_tauri_build_stops_with_its_exit_code_and_verifies_nothing(harness, capsys):
    harness.env(STUB_FAIL=3, STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert isinstance(code, str) and "exit code 3" in code
    assert harness.verified == [] and "Verified" not in out and "error: boom" in err


def test_a_backend_inside_the_built_app_that_lost_the_extension_fails_the_build(harness, capsys):
    """The sidecar can change between the check and the bundler's copy (a concurrent build_sidecar.sh)."""
    harness.env(STUB_PACKAGED_FAIL=1, STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert isinstance(code, str) and "failed verification" in code
    assert "the packaged backend does not carry the current Chrome extension: stub verdict" in code
    assert "Verified" not in out


def test_an_app_without_a_backend_fails_the_build(harness, capsys):
    harness.env(STUB_NO_BACKEND=1, STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert isinstance(code, str) and "meridian-backend is missing: the app has no backend" in code
    assert "Verified" not in out


# (a) the Safari extension must be in the app when it was handed to the bundler ------------------------------------------------


def test_a_built_app_missing_the_appex_fails_instead_of_printing_verified(harness, capsys, tmp_path):
    """The reported defect: 'Verified ...: Safari extension embedded and sandboxed' for an app without an appex."""
    harness.safari_script(check=0)
    app = harness.app("release")
    harness.env(STUB_APP=app, STUB_REPORT=1)
    # the real verify_app, with codesign/plutil answering "fine", sees the missing appex by itself
    real = load()
    harness.monkeypatch.setattr(real, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))
    harness.monkeypatch.setattr(harness.module, "verify_app", real.verify_app)
    code, out, err = harness.run("--bundles", "app", capsys=capsys)
    assert isinstance(code, str) and "failed verification" in code and "missing from the app" in code
    assert "Verified" not in out and "Safari extension embedded" not in out


def test_the_expectation_follows_whether_an_appex_was_handed_to_the_bundler(harness, capsys):
    app = harness.app("release")
    harness.env(STUB_APP=app, STUB_REPORT=1)
    code, out, err = harness.run("--bundles", "app", capsys=capsys)
    assert code == 0, err
    assert harness.verified == [(app, True)]
    config = harness.tauri_args()[harness.tauri_args().index("--config") + 1]
    assert f"PlugIns/{APPEX}" in config and "../build/safari/Meridian Assistant.app/Contents/PlugIns/" in config
    harness.verified.clear()
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert code == 0 and harness.verified == [(app, False)]


def test_an_embedded_appex_is_announced_in_the_verified_line(harness, capsys, tmp_path):
    source = appex_source(tmp_path)
    app = harness.app("release")
    harness.env(STUB_APP=app, STUB_REPORT=1, STUB_APPEX=source)
    code, out, err = harness.run("--bundles", "app", capsys=capsys)
    assert code == 0, err
    assert f"Verified {app}: signature valid, Safari extension embedded and sandboxed." in out
    assert "NOT included" not in out


def test_problems_found_in_the_built_app_fail_the_build(harness, capsys):
    harness.problems = ["codesign --verify --deep --strict failed: boom"]
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert isinstance(code, str) and "failed verification" in code and "boom" in code
    assert "Verified" not in out


# (e) the last line says when the Safari extension was left out -------------------------------------------------------------


def check_last_line(out: str, reason: str):
    last = out.rstrip("\n").splitlines()[-1]
    assert last == f"Safari extension: NOT included in this build ({reason}). The app has no Safari support.", last


def test_no_safari_flag_ends_with_an_explicit_line(harness, capsys):
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert code == 0, err
    check_last_line(out, "--no-safari was given")
    assert not (harness.log / "safari.calls").exists()


def test_the_skip_variable_ends_with_an_explicit_line(harness, capsys):
    harness.env(MERIDIAN_SKIP_SAFARI=1, STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run(capsys=capsys)
    assert code == 0, err
    check_last_line(out, "MERIDIAN_SKIP_SAFARI=1")
    assert "Safari extension: skipped (MERIDIAN_SKIP_SAFARI=1)" in err
    assert harness.verified[0][1] is False


def test_a_missing_swift_toolchain_ends_with_an_explicit_line(harness, capsys):
    harness.safari_script(check=1)
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run(capsys=capsys)
    assert code == 0, err
    check_last_line(out, "the Swift toolchain (Xcode) is not available")
    assert not (harness.log / "safari.calls").exists()


def test_an_unbuilt_safari_web_extension_ends_with_an_explicit_line(harness, capsys):
    (harness.root / "extension/dist/safari/manifest.json").unlink()
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run(capsys=capsys)
    assert code == 0, err
    check_last_line(out, "extension/dist/safari is not built (run make extension)")


def test_no_bundle_builds_also_end_with_the_safari_line(harness, capsys):
    code, out, err = harness.run("--no-safari", "--no-bundle", capsys=capsys)
    assert code == 0, err
    check_last_line(out, "--no-safari was given")


def test_a_build_that_includes_the_appex_prints_no_skip_line(harness, capsys, tmp_path):
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1, STUB_APPEX=appex_source(tmp_path))
    code, out, err = harness.run(capsys=capsys)
    assert code == 0, err
    assert "NOT included" not in out and out.rstrip().splitlines()[-1].startswith("Verified ")


def test_the_identity_is_still_required_and_print_identity_writes_nothing(harness, capsys):
    harness.monkeypatch.setattr(harness.module, "resolve_identity", lambda: None)
    code, out, err = harness.run(capsys=capsys)
    assert "signing identity is required" in code and harness.tauri_args() is None
    code, out, err = harness.run("--print-identity", capsys=capsys)
    assert "No usable signing identity" in code
    harness.monkeypatch.setattr(harness.module, "resolve_identity", lambda: "Apple Development: Test (ABCDE12345)")
    code, out, err = harness.run("--print-identity", capsys=capsys)
    assert code == 0 and out.strip() == "Apple Development: Test (ABCDE12345)" and harness.calls == []
