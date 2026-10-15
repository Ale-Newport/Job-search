"""scripts/build_signed_app.py, LS-01 hardening: a build reminds the person of OTHER registered copies of Meridian.

macOS binds the meridian:// address and the Safari extension to one registered copy, so a build that finishes next to an older
copy (the one in /Applications, a mounted .dmg, an old build folder) says so and shows how to forget it. The reminder only reads
(Spotlight and `pluginkit -m`); it never registers or un-registers anything, and it is silent when nothing else is known.

Same throwaway repository as test_build_signed_app.py (stub tauri, stub rustc, stub build_extension.py); `mdfind` and `pluginkit`
are stubs here too, so the verdict never depends on what is installed on the machine running the tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_build_signed_app import APPEX, Harness, appex_source, make_executable


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def stub_tools(tmp_path: Path, monkeypatch, *, spotlight: list[Path] = (), plugin_apps: list[Path] = (), fail: bool = False) -> Path:
    """`mdfind` and `pluginkit` as stubs that print the given copies and record how they were called."""
    tools = tmp_path / "ls-tools"
    log = tmp_path / "ls-calls.log"
    status = 1 if fail else 0
    make_executable(tools / "mdfind", "#!/bin/sh\necho \"mdfind $*\" >> \"" + str(log) + "\"\n"
                    + "".join(f"echo '{path}'\n" for path in spotlight) + f"exit {status}\n")
    plugin_lines = "".join(
        f"echo '    Path = {app}/Contents/PlugIns/{APPEX}'\necho '    Parent Bundle = {app}'\n" for app in plugin_apps)
    make_executable(tools / "pluginkit", "#!/bin/sh\necho \"pluginkit $*\" >> \"" + str(log) + "\"\n"
                    + "echo 'com.meridian.jobagent.assistant.Extension(0.1.0)'\n" + plugin_lines + f"exit {status}\n")
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    return log


def make_app_dir(path: Path) -> Path:
    (path / "Contents").mkdir(parents=True)
    return path


def test_other_copies_known_to_spotlight_and_pluginkit_are_found_and_the_new_build_is_not_one_of_them(harness, tmp_path, monkeypatch):
    installed = make_app_dir(tmp_path / "Applications/Meridian.app")
    dmg = make_app_dir(tmp_path / "Volumes/Meridian/Meridian.app")
    staged = make_app_dir(tmp_path / "scratch/Meridian Assistant.app")
    built = make_app_dir(tmp_path / "target/release/bundle/macos/Meridian.app")
    gone = tmp_path / "Trash/Meridian.app"  # Spotlight and PlugInKit lag behind the file system
    log = stub_tools(tmp_path, monkeypatch, spotlight=[installed, built, gone, dmg], plugin_apps=[staged, installed])
    copies = harness.module.known_copies([built])
    assert copies == sorted([installed.resolve(), dmg.resolve(), staged.resolve()])
    assert built.resolve() not in copies and gone not in copies
    calls = log.read_text().splitlines()
    assert calls == ["mdfind kMDItemCFBundleIdentifier == 'com.meridian.jobagent'", "pluginkit -mAvv -i com.meridian.jobagent.assistant.Extension"], \
        "only questions are asked: nothing is registered, un-registered or launched"


def test_a_symlinked_path_of_the_new_build_still_counts_as_the_new_build(harness, tmp_path, monkeypatch):
    built = make_app_dir(tmp_path / "real/Meridian.app")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    stub_tools(tmp_path, monkeypatch, spotlight=[tmp_path / "alias/Meridian.app"])
    assert harness.module.known_copies([built]) == []


@pytest.mark.parametrize("fail", [False, True])
def test_nothing_known_or_the_tools_failing_finds_nothing_and_breaks_nothing(harness, tmp_path, monkeypatch, fail):
    stub_tools(tmp_path, monkeypatch, fail=fail)
    assert harness.module.known_copies([tmp_path / "x/Meridian.app"]) == []
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))  # no mdfind, no pluginkit at all (not macOS): still not an error
    assert harness.module.known_copies([tmp_path / "x/Meridian.app"]) == []


def test_the_reminder_names_each_copy_the_new_build_the_unregister_command_and_the_pluginkit_check(harness, tmp_path):
    module = harness.module
    assert module.registration_reminder([], [Path("/b/Meridian.app")]) == ""
    text = module.registration_reminder([Path("/Applications/Meridian.app"), Path("/Volumes/M/Meridian.app")], [Path("/b/Meridian.app")])
    assert "  /Applications/Meridian.app" in text and "  /Volumes/M/Meridian.app" in text
    assert "the new build is /b/Meridian.app" in text
    assert f'{module.LSREGISTER} -u "<path of that copy>"' in text
    assert module.LSREGISTER.endswith("LaunchServices.framework/Support/lsregister")
    assert "pluginkit -m -i com.meridian.jobagent.assistant.Extension" in text
    assert "meridian://" in text and "ONE registered copy" in text


def test_a_finished_build_prints_the_reminder_on_stderr_and_leaves_the_last_line_of_stdout_alone(harness, tmp_path, monkeypatch, capsys):
    older = make_app_dir(tmp_path / "Applications/Meridian.app")
    stub_tools(tmp_path, monkeypatch, spotlight=[older])
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert code == 0, err
    assert "Other copies of Meridian are known to macOS" in err and f"  {older.resolve()}" in err and "lsregister" in err
    assert "Other copies" not in out, "stdout keeps its contract: the last line is the verdict or the Safari note"
    last = out.rstrip("\n").splitlines()[-1]
    assert last == "Safari extension: NOT included in this build (--no-safari was given). The app has no Safari support.", last
    assert f"Verified {harness.app('release')}" in out


def test_a_build_with_the_appex_still_ends_with_its_verified_line(harness, tmp_path, monkeypatch, capsys):
    older = make_app_dir(tmp_path / "Volumes/Meridian/Meridian.app")
    stub_tools(tmp_path, monkeypatch, plugin_apps=[older])
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1, STUB_APPEX=appex_source(tmp_path))
    code, out, err = harness.run(capsys=capsys)
    assert code == 0, err
    assert "Other copies of Meridian" in err and str(older.resolve()) in err
    assert out.rstrip().splitlines()[-1].startswith("Verified ")


def test_with_no_other_copy_the_build_says_nothing_about_registrations(harness, tmp_path, monkeypatch, capsys):
    stub_tools(tmp_path, monkeypatch)
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert code == 0, err
    assert "Other copies" not in err and "lsregister" not in err and "lsregister" not in out


def test_only_the_new_build_registered_is_not_a_reason_to_print_anything(harness, tmp_path, monkeypatch, capsys):
    """Spotlight lists the app that was just built: that is the one to keep, not an older registration."""
    harness.env(STUB_APP=harness.app("release"), STUB_REPORT=1)
    stub_tools(tmp_path, monkeypatch, spotlight=[harness.app("release")], plugin_apps=[harness.app("release")])
    code, out, err = harness.run("--no-safari", capsys=capsys)
    assert code == 0, err
    assert "Other copies" not in err


def test_a_build_that_made_no_bundle_looks_at_no_registrations(harness, tmp_path, monkeypatch, capsys):
    log = stub_tools(tmp_path, monkeypatch, spotlight=[make_app_dir(tmp_path / "Applications/Meridian.app")])
    code, out, err = harness.run("--no-safari", "--no-bundle", capsys=capsys)
    assert code == 0, err
    assert "Other copies" not in err and not log.exists(), "nothing was built, so nothing is compared with anything"


def test_the_script_only_asks_about_registrations_and_never_changes_one():
    """lsregister appears in the text it prints for the person to run, never as a command this script runs."""
    source = (Path(__file__).resolve().parents[2] / "scripts/build_signed_app.py").read_text()
    runs = [line for line in source.splitlines() if "run([" in line or "subprocess.run(" in line or "check_output(" in line]
    assert not any("lsregister" in line.lower() or "LSREGISTER" in line for line in runs), runs
    assert source.count("LSREGISTER") == 2, "defined once and printed once"
