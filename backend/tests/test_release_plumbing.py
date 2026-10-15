"""Release plumbing added after the extension review (MAKE-01, DEV-01, LS-01, create_dmg.sh).

Same rules as test_repo_plumbing.py: nothing here touches the real build outputs. `make` is only asked for dry runs of the real
Makefile, or runs the real recipe against a temporary tree with fake runners; scripts that read the repository root are copied
into a temporary repository first (they find their root from their own location).
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import pytest

from test_repo_plumbing import BASH, MAKE, REPO, dry_run, fake_extension_tree, run

SCRIPTS = REPO / "scripts"
needs_make = pytest.mark.skipif(MAKE is None, reason="make is not installed")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")


# --- MAKE-01: STRICT=1 turns a missing runner into a failure ------------------------------------------------------------


@needs_make
def test_strict_makes_a_missing_extension_runner_a_failure_but_the_others_still_run(tmp_path):
    env = os.environ | fake_extension_tree(tmp_path, ui_runner=False) | {"FAIL": "no-such-argument"}
    result = run([MAKE, f"PYTHON={tmp_path}/bin/fakepy", "STRICT=1", "test-ext-fast"], cwd=tmp_path, env=env)
    assert result.returncode != 0, "a deleted or renamed runner must fail the release gate"
    assert "== MISSING: panel UI tests (slow): extension/tests/ui/run.py does not exist (STRICT=1)" in result.stdout
    assert "skipped" not in result.stdout
    log = (tmp_path / "log.txt").read_text()
    assert "extension/tests/content/run.py" in log and "npm --prefix extension test" in log, "the other runners ran"


def fresh_tree(tmp_path: Path, name: str, **options) -> tuple[Path, dict]:
    root = tmp_path / name
    root.mkdir()
    return root, os.environ | fake_extension_tree(root, **options)


@needs_make
def test_the_default_stays_tolerant_and_strict_zero_is_the_default(tmp_path):
    for index, extra in enumerate(([], ["STRICT=0"])):
        root, env = fresh_tree(tmp_path, f"tree{index}", ui_runner=False)
        result = run([MAKE, f"PYTHON={root}/bin/fakepy", *extra, "test-ext-fast"], cwd=root, env=env | {"FAIL": "no-such-argument"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "== skipped panel UI tests (slow): extension/tests/ui/run.py does not exist" in result.stdout
        assert "MISSING" not in result.stdout


@needs_make
def test_strict_passes_when_every_runner_exists_and_still_fails_on_a_failing_runner(tmp_path):
    root, env = fresh_tree(tmp_path, "good", ui_runner=True)
    assert run([MAKE, f"PYTHON={root}/bin/fakepy", "STRICT=1", "test-ext-fast"], cwd=root, env=env | {"FAIL": "no-such-argument"}).returncode == 0
    root, env = fresh_tree(tmp_path, "bad", ui_runner=True)
    failing = run([MAKE, f"PYTHON={root}/bin/fakepy", "STRICT=1", "test-ext-fast"], cwd=root, env=env | {"FAIL": "content/run.py"})
    assert failing.returncode != 0 and "== FAILED: content script tests" in failing.stdout


@needs_make
def test_strict_also_makes_the_real_browser_suite_strict_and_the_default_does_not():
    strict = run([MAKE, "-n", "test-ext", "STRICT=1"], cwd=REPO).stdout
    default = dry_run("test-ext")
    step = next(line for line in strict.splitlines() if "extension/tests/e2e/test_extension_e2e.py" in line and "step " in line)
    assert step.strip().endswith("extension/tests/e2e/test_extension_e2e.py --strict; \\"), step
    assert " --strict" not in default


@needs_make
def test_the_release_gate_runs_everything_and_the_extension_suites_strictly():
    recipe = run([MAKE, "-n", "release-gate"], cwd=REPO)
    assert recipe.returncode == 0, recipe.stderr
    out = recipe.stdout
    for part in ("ruff check", "npm --prefix frontend run lint", "npm --prefix extension run typecheck", "pytest -q",
                 "cargo test", "frontend/tests/run_ui_suite.py", "test-ext STRICT=1"):
        assert part in out, part
    assert out.index("pytest -q") < out.index("test-ext STRICT=1"), "the slow extension suites come last"
    assert "release-gate" in (REPO / "Makefile").read_text().splitlines()[2]  # declared phony like every other target


@needs_make
def test_the_isolated_ui_suite_is_its_own_target_and_the_old_chain_is_untouched():
    assert dry_run("test-ui-suite").strip().endswith("frontend/tests/run_ui_suite.py")
    old = dry_run("test-ui")
    assert "frontend/tests/ui_smoke.py" in old and "run_ui_suite" not in old, "README documents test-ui as the hand-started chain"
    assert (REPO / "frontend" / "tests" / "run_ui_suite.py").is_file()


# --- DEV-01 and LS-01: what to do when two Meridians answer ------------------------------------------------------------


def test_the_dev_backend_documents_the_two_listener_refusal_and_how_to_resolve_it():
    text = (SCRIPTS / "dev_backend.sh").read_text()
    comment = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    assert "REFUSES to pair" in comment
    assert "MERIDIAN_BRIDGE=0" in comment
    assert "47615 to 47624" in comment
    assert "private port range" in comment, "the tempting but wrong fix is ruled out in writing"
    code = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert "MERIDIAN_BRIDGE" not in "\n".join(code), "the bridge stays ON by default: it is how a real extension meets dev code"
    makefile = (REPO / "Makefile").read_text()
    assert re.search(r"#.*MERIDIAN_BRIDGE=0 make backend", makefile)


@needs_bash
def test_the_dev_backend_script_is_still_valid_shell():
    assert run([BASH, "-n", str(SCRIPTS / "dev_backend.sh")]).returncode == 0


# --- create_dmg.sh: the app and the version come from the build, never from the script ----------------------------------


def make_fake_repo(root: Path, version: str = "7.8.9") -> Path:
    (root / "scripts").mkdir(parents=True)
    for name in ("create_dmg.sh", "build_signed_app.py"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    (root / "src-tauri").mkdir()
    (root / "src-tauri" / "tauri.conf.json").write_text('{"productName": "Meridian", "version": "%s"}' % version)
    return root


def fake_app(path: Path, written: float) -> Path:
    (path / "Contents").mkdir(parents=True)
    (path / "Contents" / "Info.plist").write_text("<plist/>")
    for part in (path / "Contents" / "Info.plist", path / "Contents", path):
        os.utime(part, (written, written))
    return path


def plan(root: Path, *arguments: str, env: dict | None = None) -> dict[str, str]:
    environment = {k: v for k, v in os.environ.items() if k not in {"CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR", "PYTHON"}}
    environment.update(env or {})
    result = run([BASH, str(root / "scripts" / "create_dmg.sh"), "--print-plan", *arguments], cwd=root, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


@needs_bash
def test_create_dmg_takes_the_newest_built_app_from_the_same_lookup_as_the_signed_build(tmp_path):
    root = make_fake_repo(tmp_path)
    now = time.time()
    fake_app(root / "src-tauri/target/release/bundle/macos/Meridian.app", now - 3600)
    newest = fake_app(root / "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Meridian.app", now - 60)
    result = plan(root)
    assert Path(result["app"]).resolve() == newest.resolve(), "the --target build is the newest, not the plain release one"
    assert result["version"] == "7.8.9"
    assert Path(result["dmg"]).name == f"Meridian_7.8.9_{os.uname().machine.replace('arm64', 'aarch64')}.dmg"
    assert Path(result["dmg"]).parent.resolve() == (newest.parent.parent / "dmg").resolve(), "next to the app's bundle folder"


@needs_bash
def test_create_dmg_follows_a_custom_cargo_target_directory_like_the_signed_build_does(tmp_path):
    root = make_fake_repo(tmp_path / "repo")
    elsewhere = fake_app(tmp_path / "cargo-target/release/bundle/macos/Meridian.app", time.time())
    result = plan(root, env={"CARGO_TARGET_DIR": str(tmp_path / "cargo-target")})
    assert Path(result["app"]).resolve() == elsewhere.resolve()


@needs_bash
def test_create_dmg_version_follows_tauri_conf_and_an_explicit_app_wins(tmp_path):
    root = make_fake_repo(tmp_path, version="12.0.1")
    fake_app(root / "src-tauri/target/release/bundle/macos/Meridian.app", time.time())
    explicit = fake_app(tmp_path / "somewhere" / "Meridian.app", time.time() - 99999)
    assert plan(root)["version"] == "12.0.1"
    result = plan(root, "--app", str(explicit))
    assert result["app"] == str(explicit) and result["version"] == "12.0.1"
    assert result["dmg"].endswith("/dmg/Meridian_12.0.1_" + os.uname().machine.replace("arm64", "aarch64") + ".dmg")


@needs_bash
def test_create_dmg_without_any_built_app_says_what_to_do_and_builds_nothing(tmp_path):
    root = make_fake_repo(tmp_path)
    result = run([BASH, str(root / "scripts" / "create_dmg.sh"), "--print-plan"], cwd=root,
                 env={k: v for k, v in os.environ.items() if k not in {"CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR", "PYTHON"}})
    assert result.returncode != 0
    assert "No built Meridian.app was found" in result.stderr and "make build-app" in result.stderr
    assert not (root / ".build-cache").exists() and not list(root.rglob("*.dmg"))


def test_create_dmg_no_longer_hard_codes_the_release_path_or_a_version():
    text = (SCRIPTS / "create_dmg.sh").read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "target/release/bundle" not in code
    assert not re.search(r"\d+\.\d+\.\d+", code), "the version comes from src-tauri/tauri.conf.json"
    assert "build_signed_app" in code and "tauri.conf.json" in code
    assert "lsregister -u" in text and "pluginkit -m -i com.meridian.jobagent.assistant.Extension" in text, "LS-01: one copy of the app"
    assert os.access(SCRIPTS / "create_dmg.sh", os.X_OK), "it is run directly"


@needs_bash
def test_create_dmg_is_valid_shell_and_rejects_unknown_arguments():
    assert run([BASH, "-n", str(SCRIPTS / "create_dmg.sh")]).returncode == 0
    bad = run([BASH, str(SCRIPTS / "create_dmg.sh"), "--bogus"], cwd=REPO)
    assert bad.returncode == 2 and "unknown argument" in bad.stderr
    helped = run([BASH, str(SCRIPTS / "create_dmg.sh"), "--help"], cwd=REPO)
    assert helped.returncode == 0 and "--print-plan" in helped.stdout
