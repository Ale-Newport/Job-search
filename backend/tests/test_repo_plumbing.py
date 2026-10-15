"""Repository plumbing around the browser extension: Makefile gates, scripts/clean_build.py, scripts/build_sidecar.sh,
scripts/bootstrap_macos.sh and scripts/build_safari_assistant.sh --print-steps.

Nothing here touches the real build outputs: scripts that delete or write are copied into a temporary repository first (they
find their root from their own location), and `make` is only ever asked for a dry run (`-n`) of the real Makefile.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pty
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_build_extension import write_variant

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
MAKE = shutil.which("make")
BASH = shutil.which("bash")


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"meridian_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clean_build = load_script("clean_build")


def run(command, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 120)
    return subprocess.run(command, **kwargs)


def dry_run(target: str) -> str:
    result = run([MAKE, "-n", target], cwd=REPO)
    assert result.returncode == 0, result.stderr
    return result.stdout


# --- Makefile -------------------------------------------------------------------------------------------------------------

pytestmark_make = pytest.mark.skipif(MAKE is None, reason="make is not installed")


@pytestmark_make
def test_test_ext_runs_the_unit_tests_through_the_package_script():
    """`cd extension/tests/unit && node --test` failed every test (no TypeScript loader): the package script has it."""
    recipe = dry_run("test-ext")
    assert "npm --prefix extension test" in recipe
    assert "node --test'" not in recipe and "cd extension/tests/unit" not in recipe
    package = json.loads((REPO / "extension" / "package.json").read_text())
    assert "register.mjs" in package["scripts"]["test"]
    assert (REPO / "extension" / "tests" / "unit" / "register.mjs").is_file()


@pytestmark_make
def test_test_ext_wires_every_extension_runner_in_order():
    recipe = dry_run("test-ext")
    assert "scripts/build_extension.py --e2e" in recipe
    steps = [
        "npm --prefix extension test",
        'node --import ./tests/unit/register.mjs --test "tests/widgets/unit/*.test.mjs"',
        "extension/tests/content/run.py",
        "extension/tests/ui/run.py",
        "extension/tests/widgets/run.py",
        "node extension/tests/widgets/bundle-report.mjs",
        "extension/tests/e2e/test_extension_e2e.py",
    ]
    positions = [recipe.index(f" {step}") if f" {step}" in recipe else -1 for step in steps]
    assert -1 not in positions, dict(zip(steps, positions))
    assert positions == sorted(positions)
    for runner in ("tests/widgets/run.py", "tests/widgets/bundle-report.mjs", "tests/widgets/unit"):
        assert (REPO / "extension" / runner).exists(), runner


@pytestmark_make
def test_the_bundle_report_step_runs_from_the_repository_root_without_a_directory_change():
    """It used to need `cd extension`: from the root esbuild named its inputs extension/src/... and the report cried foul."""
    recipe = dry_run("test-ext")
    step = next(line for line in recipe.splitlines() if "bundle-report" in line and "step " in line)
    assert "cd extension" not in step and "sh -c" not in step
    assert step.strip().endswith("node extension/tests/widgets/bundle-report.mjs; \\")


@pytestmark_make
def test_test_ext_fast_is_unit_content_and_ui_only():
    recipe = dry_run("test-ext-fast")
    for step in ("npm --prefix extension test", "extension/tests/content/run.py", "extension/tests/ui/run.py"):
        assert step in recipe
    for slow in ("e2e", "widgets/run.py", "bundle-report"):
        assert slow not in recipe, slow
    assert "scripts/build_extension.py --install-only" in recipe  # dependencies first, a no-op when current


@pytestmark_make
def test_typecheck_and_lint_include_the_extension_and_never_rewrite_files():
    assert "npm --prefix extension run typecheck" in dry_run("typecheck")
    assert "npm --prefix frontend run typecheck" in dry_run("typecheck")
    lint = dry_run("lint")
    ruff = next(line for line in lint.splitlines() if "ruff" in line)
    assert "extension/tests" in ruff and "--no-fix" in ruff and " --fix" not in ruff
    assert "npm --prefix frontend run lint" in lint and "cargo fmt" in lint and "-- --check" in lint


@pytestmark_make
def test_clean_asks_nothing_and_clean_dry_deletes_nothing():
    assert dry_run("clean").strip().endswith("scripts/clean_build.py --yes")
    assert dry_run("clean-dry").strip().endswith("scripts/clean_build.py --dry-run")


@pytestmark_make
def test_build_targets_still_build_the_extension_first():
    for target in ("build", "build-app"):
        recipe = dry_run(target).splitlines()
        assert recipe[0].endswith("scripts/build_extension.py"), recipe
        assert "MERIDIAN_EXTENSION_READY=1 ./scripts/build_sidecar.sh" in recipe[1]


@pytestmark_make
def test_typecheck_explains_missing_extension_dependencies_instead_of_a_cryptic_tsc_error(tmp_path):
    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    result = run([MAKE, "typecheck"], cwd=tmp_path)
    assert result.returncode != 0
    assert "Extension dependencies are not installed: run make setup" in result.stderr
    assert "frontend" not in result.stdout, "the guard must stop the gate before anything else runs"


def fake_extension_tree(root: Path, ui_runner: bool = True) -> dict[str, str]:
    (root / "extension/tests/unit").mkdir(parents=True)
    (root / "extension/tests/content").mkdir(parents=True)
    (root / "extension/tests/unit/register.mjs").write_text("")
    (root / "extension/tests/content/run.py").write_text("")
    if ui_runner:
        (root / "extension/tests/ui").mkdir(parents=True)
        (root / "extension/tests/ui/run.py").write_text("")
    shutil.copy(REPO / "Makefile", root / "Makefile")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "fakepy").write_text(
        '#!/bin/bash\necho "fakepy $*" >> "$LOG"\ncase "$*" in *"$FAIL"*) exit 1;; esac\nexit 0\n'
    )
    (bin_dir / "npm").write_text('#!/bin/bash\necho "npm $*" >> "$LOG"\nexit 0\n')
    for script in bin_dir.iterdir():
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "LOG": str(root / "log.txt")}


@pytestmark_make
def test_a_failing_extension_runner_fails_the_target_after_the_others_ran(tmp_path):
    env = os.environ | fake_extension_tree(tmp_path) | {"FAIL": "content/run.py"}
    result = run([MAKE, f"PYTHON={tmp_path}/bin/fakepy", "test-ext-fast"], cwd=tmp_path, env=env)
    log = (tmp_path / "log.txt").read_text()
    assert result.returncode != 0
    assert "== FAILED: content script tests" in result.stdout
    assert "extension/tests/ui/run.py" in log, "the panel UI runner must still run after the content runner failed"
    assert "npm --prefix extension test" in log


@pytestmark_make
def test_a_missing_extension_runner_is_reported_and_skipped(tmp_path):
    env = os.environ | fake_extension_tree(tmp_path, ui_runner=False) | {"FAIL": "no-such-argument"}
    result = run([MAKE, f"PYTHON={tmp_path}/bin/fakepy", "test-ext-fast"], cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "== skipped panel UI tests (slow): extension/tests/ui/run.py does not exist" in result.stdout
    assert "extension/tests/content/run.py" in (tmp_path / "log.txt").read_text()


# --- root package.json, bootstrap, .gitignore -----------------------------------------------------------------------------

def test_the_root_typecheck_script_covers_the_extension():
    scripts = json.loads((REPO / "package.json").read_text())["scripts"]
    assert "npm --prefix frontend run typecheck" in scripts["typecheck"]
    assert "npm --prefix extension run typecheck" in scripts["typecheck"]
    assert scripts["test:extension"] == "npm --prefix extension test"


def test_bootstrap_installs_the_extension_dependencies_between_the_frontend_and_the_sidecar_build():
    text = (SCRIPTS / "bootstrap_macos.sh").read_text()
    frontend = text.index("npm --prefix frontend ci")
    extension = text.index(".venv/bin/python scripts/build_extension.py --install-only")
    sidecar = text.index("./scripts/build_sidecar.sh")
    assert frontend < extension < sidecar
    assert text.index("python3.12 -m venv .venv") < extension  # the helper runs in the venv


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
@pytest.mark.parametrize("script", ["bootstrap_macos.sh", "build_sidecar.sh", "build_safari_assistant.sh"])
def test_shell_scripts_parse(script):
    result = run([BASH, "-n", str(SCRIPTS / script)])
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(), reason="not a git checkout")
def test_generated_extension_output_is_ignored_and_the_sources_are_not():
    def ignored(path: str) -> bool:
        return run(["git", "check-ignore", "-q", path], cwd=REPO).returncode == 0

    for generated in ("extension/dist/chrome/manifest.json", "extension/node_modules/esbuild/package.json", "build/safari/x.app/Contents/Info.plist"):
        assert ignored(generated), generated
    for tracked in (
        "extension/manifest-key.txt", "extension/package-lock.json", "extension/build.mjs", "extension/src/background/index.ts",
        "extension/safari/ext/SafariWebExtensionHandler.swift", "extension/safari/resources/appex.entitlements",
    ):
        assert not ignored(tracked), tracked


# --- scripts/clean_build.py -----------------------------------------------------------------------------------------------

OUTPUT_DIRS = ("dist", "build/safari", "frontend/dist", "extension/dist/chrome", "src-tauri/target/release", "src-tauri/binaries")
KEPT = (
    ".local-data/app.db", "node_modules/pkg/index.js", "frontend/node_modules/pkg/index.js", "extension/node_modules/pkg/index.js",
    ".venv/bin/python", ".git/HEAD", "backend/jobagent/core.py", "extension/src/index.ts", "frontend/src/App.tsx", "src-tauri/src/main.rs",
)


def scratch_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in KEPT:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep")
    for relative in OUTPUT_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)
        (root / relative / "artifact").write_text("generated")
    (root / "scripts").mkdir()
    shutil.copy(SCRIPTS / "clean_build.py", root / "scripts" / "clean_build.py")
    return root


def script_of(root: Path) -> list[str]:
    return [sys.executable, str(root / "scripts" / "clean_build.py")]


def survivors(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("artifact")}


def assert_kept(root: Path) -> None:
    for relative in KEPT:
        assert (root / relative).read_text() == "keep", f"{relative} was touched"
    assert (root / "scripts" / "clean_build.py").is_file()


def test_help_describes_the_script_and_deletes_nothing(tmp_path):
    root = scratch_repo(tmp_path)
    result = run([*script_of(root), "--help"], cwd=root, stdin=subprocess.DEVNULL)
    assert result.returncode == 0
    for word in ("--dry-run", "--yes", ".local-data", "node_modules"):
        assert word in result.stdout
    assert len(survivors(root)) == len(OUTPUT_DIRS)


@pytest.mark.parametrize("argument", ["--nope", "-x", "dist", "--hlep"])
def test_an_unknown_argument_is_an_error_and_deletes_nothing(tmp_path, argument):
    """The old script ignored its arguments, so `clean_build.py --help` deleted the build outputs."""
    root = scratch_repo(tmp_path)
    result = run([*script_of(root), argument], cwd=root, stdin=subprocess.DEVNULL)
    assert result.returncode == 2
    assert len(survivors(root)) == len(OUTPUT_DIRS)


def test_dry_run_lists_every_output_and_deletes_nothing(tmp_path):
    root = scratch_repo(tmp_path)
    result = run([*script_of(root), "--dry-run"], cwd=root, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    for top in ("dist", "build", "frontend/dist", "extension/dist", "src-tauri/target", "src-tauri/binaries"):
        assert f"  {top}  (" in result.stdout, top
    assert "Dry run: nothing was removed." in result.stdout
    assert len(survivors(root)) == len(OUTPUT_DIRS)
    assert_kept(root)


def test_dry_run_beats_yes(tmp_path):
    root = scratch_repo(tmp_path)
    result = run([*script_of(root), "--yes", "--dry-run"], cwd=root, stdin=subprocess.DEVNULL)
    assert result.returncode == 0
    assert len(survivors(root)) == len(OUTPUT_DIRS)


def test_without_yes_and_without_a_terminal_nothing_is_deleted(tmp_path):
    root = scratch_repo(tmp_path)
    result = run([*script_of(root)], cwd=root, stdin=subprocess.DEVNULL)
    assert result.returncode == 2
    assert "refusing to delete without --yes" in result.stderr
    assert len(survivors(root)) == len(OUTPUT_DIRS)


def test_yes_removes_exactly_the_build_outputs(tmp_path):
    root = scratch_repo(tmp_path)
    result = run([*script_of(root), "--yes"], cwd=root, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert survivors(root) == set()
    for top in ("dist", "build", "frontend/dist", "extension/dist", "src-tauri/target", "src-tauri/binaries"):
        assert not (root / top).exists(), top
    assert_kept(root)
    again = run([*script_of(root), "--yes"], cwd=root, stdin=subprocess.DEVNULL)
    assert again.returncode == 0 and "no build outputs to remove" in again.stdout


def ask_on_a_terminal(root: Path, answer: bytes) -> subprocess.CompletedProcess:
    """Run the script with a pseudo-terminal as stdin (so it asks) and type `answer`."""
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            script_of(root), cwd=root, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        os.close(slave)
        os.write(master, answer)
        out, err = process.communicate(timeout=60)
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)
    finally:
        os.close(master)


@pytest.mark.parametrize("answer", [b"n\n", b"\n", b"no\n", b"yes please\n", b"\x04"])
def test_on_a_terminal_anything_but_yes_keeps_everything(tmp_path, answer):
    root = scratch_repo(tmp_path)
    result = ask_on_a_terminal(root, answer)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Remove these directories? [y/N]" in result.stdout
    assert len(survivors(root)) == len(OUTPUT_DIRS)


@pytest.mark.parametrize("answer", [b"y\n", b"yes\n", b"  Y \n"])
def test_on_a_terminal_yes_removes_the_outputs(tmp_path, answer):
    root = scratch_repo(tmp_path)
    result = ask_on_a_terminal(root, answer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert survivors(root) == set()
    assert_kept(root)


def test_the_real_output_list_never_touches_protected_folders():
    assert set(clean_build.OUTPUTS) == {"dist", "build", "frontend/dist", "extension/dist", "src-tauri/target", "src-tauri/binaries"}
    for relative in clean_build.OUTPUTS:
        assert clean_build.protection_problem(REPO, relative) is None, relative


@pytest.mark.parametrize(
    "relative",
    [
        ".local-data", ".local-data/qa", "node_modules", "frontend/node_modules", "extension/tests/widgets/node_modules/x",
        ".venv", ".git", ".local-models", "frontend", "extension", ".", "", "..", "../elsewhere", "/etc",
    ],
)
def test_protected_and_foreign_paths_are_refused_even_if_listed_by_mistake(tmp_path, monkeypatch, relative):
    root = scratch_repo(tmp_path)
    assert clean_build.protection_problem(root, relative), relative
    monkeypatch.setattr(clean_build, "OUTPUTS", (relative,))
    assert clean_build.main(["--yes"], root=root) == 1
    assert_kept(root)
    assert len(survivors(root)) == len(OUTPUT_DIRS)


def test_a_symbolic_link_output_is_left_alone(tmp_path, capsys):
    root = scratch_repo(tmp_path)
    shutil.rmtree(root / "dist")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious").write_text("do not delete")
    (root / "dist").symlink_to(elsewhere, target_is_directory=True)
    assert clean_build.main(["--yes"], root=root) == 1
    assert "symbolic link" in capsys.readouterr().err
    assert (elsewhere / "precious").read_text() == "do not delete"
    assert (root / "dist").is_symlink()
    assert not (root / "frontend/dist").exists(), "the other outputs are still cleaned"


# --- scripts/build_sidecar.sh -------------------------------------------------------------------------------------------

CHROME_FILES_LISTING = "listing.txt"


def sidecar_root(tmp_path: Path, chrome: bool = True) -> Path:
    """A throwaway repository holding the real build scripts, a synthetic extension build and stub PyInstaller tools."""
    root = tmp_path / "repo"
    for relative in (
        "scripts/build_sidecar.sh", "scripts/build_extension.py", "src-tauri/tauri.conf.json", "extension/ats-hosts.json",
        "extension/manifest-key.txt", "backend/jobagent/extension/__init__.py",
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / relative, root / relative)
    dist = root / "extension" / "dist"
    write_variant(dist, "chrome")
    write_variant(dist, "safari")
    if not chrome:
        shutil.rmtree(dist / "chrome")
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(sys.executable)
    (venv / "pyinstaller").write_text('#!/bin/sh\nmkdir -p dist\necho frozen > dist/meridian-backend\necho "$@" > pyinstaller.args\n')
    (venv / "pyi-archive_viewer").write_text(f"#!/bin/sh\ncat '{root / CHROME_FILES_LISTING}'\n")
    tools = root / "tools"
    tools.mkdir()
    (tools / "rustc").write_text('#!/bin/sh\n[ "$1 $2" = "--print host-tuple" ] && echo aarch64-apple-darwin\n')
    for tool in (*venv.iterdir(), *tools.iterdir()):
        if tool.name != "python":
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return root


def write_listing(root: Path, drop: str | None = None, add: tuple[str, ...] = ()) -> None:
    names = sorted(p.relative_to(root / "extension/dist/chrome").as_posix() for p in (root / "extension/dist/chrome").rglob("*") if p.is_file())
    entries = ["struct", "jobagent/extension/assets.py", *[f"extension/chrome/{n}" for n in names if n != drop], *add]
    (root / CHROME_FILES_LISTING).write_text("Contents of 'meridian-backend':\n" + "".join(f" {entry}\n" for entry in entries))


def build_sidecar(root: Path, **environment: str) -> subprocess.CompletedProcess:
    env = {"PATH": f"{root / 'tools'}:/usr/bin:/bin", "HOME": str(root), "MERIDIAN_EXTENSION_READY": "1"} | environment
    return run([BASH, str(root / "scripts" / "build_sidecar.sh")], cwd=root, env=env)


def sidecar_binary(root: Path) -> Path:
    return root / "src-tauri" / "binaries" / "meridian-backend-aarch64-apple-darwin"


pytestmark_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")


@pytestmark_bash
def test_the_sidecar_build_fails_loudly_without_the_chrome_extension(tmp_path):
    root = sidecar_root(tmp_path, chrome=False)
    write_listing(root)
    for skip in ("1", ""):
        result = build_sidecar(root, SKIP_PYINSTALLER=skip)
        assert result.returncode != 0
        assert "extension/dist/chrome/manifest.json is missing" in result.stderr
        assert not (root / "pyinstaller.args").exists(), "PyInstaller must not run without the extension"
        assert not sidecar_binary(root).exists()


@pytestmark_bash
def test_the_sidecar_build_refuses_a_test_build_in_dist_chrome(tmp_path):
    root = sidecar_root(tmp_path)
    (root / "extension/dist/chrome/e2e.html").write_text("<!doctype html>")
    result = build_sidecar(root, SKIP_PYINSTALLER="1")
    assert result.returncode != 0
    assert "e2e.html" in result.stderr


@pytestmark_bash
def test_the_sidecar_command_embeds_dist_chrome_as_extension_chrome(tmp_path):
    root = sidecar_root(tmp_path)
    result = build_sidecar(root, SKIP_PYINSTALLER="1")
    assert result.returncode == 0, result.stderr
    assert "extension/dist/chrome:extension/chrome" in result.stdout
    assert not sidecar_binary(root).exists()


@pytestmark_bash
def test_a_complete_frozen_sidecar_is_published_to_the_tauri_binaries_folder(tmp_path):
    root = sidecar_root(tmp_path)
    write_listing(root)
    result = build_sidecar(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "extension/dist/chrome:extension/chrome" in (root / "pyinstaller.args").read_text()
    assert sidecar_binary(root).read_text() == "frozen\n"


@pytestmark_bash
@pytest.mark.parametrize("drop", ["hint.js", "options.js", "icons/icon-16.png", "_locales/en/messages.json"])
def test_a_frozen_sidecar_missing_any_extension_file_is_not_published(tmp_path, drop):
    """The old check looked at manifest.json only, so a sidecar with a broken extension went to Tauri."""
    root = sidecar_root(tmp_path)
    write_listing(root, drop=drop)
    result = build_sidecar(root)
    assert result.returncode != 0
    assert f"extension/chrome/{drop}" in result.stderr
    assert not sidecar_binary(root).exists()


@pytestmark_bash
def test_a_frozen_sidecar_with_a_test_file_under_extension_is_not_published(tmp_path):
    root = sidecar_root(tmp_path)
    write_listing(root, add=("extension/chrome/e2e.js",))
    result = build_sidecar(root)
    assert result.returncode != 0
    assert "extension/chrome/e2e.js" in result.stderr
    assert not sidecar_binary(root).exists()


@pytestmark_bash
def test_a_frozen_sidecar_with_no_extension_at_all_is_not_published(tmp_path):
    root = sidecar_root(tmp_path)
    (root / CHROME_FILES_LISTING).write_text("Contents of 'meridian-backend':\n struct\n jobagent/extension/assets.py\n")
    assert build_sidecar(root).returncode != 0
    assert not sidecar_binary(root).exists()


# --- scripts/build_safari_assistant.sh ------------------------------------------------------------------------------------

@pytestmark_bash
def test_the_safari_script_prints_the_manual_steps_without_building_anything():
    result = run([BASH, str(SCRIPTS / "build_safari_assistant.sh"), "--print-steps"], cwd="/")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for needle in (
        "Safari > Settings > Extensions", '"Meridian Assistant"', "Always Allow on Every Website", "/Applications",
        "Develop > Allow Unsigned Extensions", "Settings > Browser assistant",
        "/usr/bin/pluginkit -m -i com.meridian.jobagent.assistant.Extension",
    ):
        assert needle in out, needle
    assert result.stderr == ""
    help_text = run([BASH, str(SCRIPTS / "build_safari_assistant.sh"), "--help"], cwd="/").stdout
    assert "--print-steps" in help_text


@pytestmark_bash
def test_the_safari_script_prints_the_steps_after_a_successful_build_and_names_the_signing_identity():
    text = (SCRIPTS / "build_safari_assistant.sh").read_text()
    tail = text[text.rindex('echo "built: $APP"'):]
    assert 'print_steps "$IDENTITY"' in tail
    assert "signed with:" in tail


# --- extension/tests/widgets/bundle-report.mjs ------------------------------------------------------------------------------

NODE = shutil.which("node")
ESBUILD = REPO / "extension" / "node_modules" / "esbuild"
needs_node_and_esbuild = pytest.mark.skipif(
    NODE is None or not ESBUILD.is_dir(), reason="node or the extension's npm dependencies are not installed"
)


@needs_node_and_esbuild
def test_the_bundle_report_gives_the_same_verdict_from_every_directory(tmp_path):
    """A clean production build exited 1 ("inputs outside extension/src") from the repository root and 0 from extension/."""
    report = REPO / "extension" / "tests" / "widgets" / "bundle-report.mjs"
    results = {}
    for name, cwd in (("root", REPO), ("extension", REPO / "extension"), ("elsewhere", tmp_path)):
        done = run([NODE, str(report), "--json"], cwd=cwd, timeout=300)
        results[name] = done
        data = json.loads(done.stdout)
        assert data["outside_src"] == [] and data["problems"] == [], (name, data["problems"])
        assert done.returncode == 0, (name, done.stderr)
    assert len({done.stdout for done in results.values()}) == 1, "the report must not depend on the caller's directory"
    adapters = json.loads(results["root"].stdout)["widget_adapters"]
    assert adapters and all(path.startswith("src/") for path in adapters)
