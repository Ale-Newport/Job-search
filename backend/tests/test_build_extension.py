"""scripts/build_extension.py: the production-build gate (check_variant), the key check and the sidecar embedding check.

The gate is what stops a test build, a placeholder or a widened manifest from being embedded in the sidecar or signed into the
Safari appex, so every rule it makes is proven here in both directions. Most tests build a small synthetic dist/ in a temporary
folder (no Node, no network, nothing from the real extension/dist), and a few run against the real extension/dist when it has been
built (they skip cleanly when it has not).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("meridian_build_extension", REPO / "scripts" / "build_extension.py")
build_extension = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_extension)

# The contract of docs/EXTENSION_DESIGN.md 7.1, spelled out here on purpose: the gate must enforce the design, not itself.
PERMISSIONS = ["activeTab", "scripting", "storage", "alarms"]
OPTIONAL_HOSTS = ["https://*/*", "http://*/*"]
LOOPBACK = "http://127.0.0.1/*"
REAL_DIST = REPO / "extension" / "dist"
BUNDLES = ("background.js", "content.js", "hint.js", "options.js")
# Realistic and harmless: mentions of things the gate must not mistake for violations (document_idle, evaluate, notificationsEnabled).
BENIGN_JS = (
    "(() => {\n"
    '  "use strict";\n'
    '  const runAt = "document_idle", evaluate = (x) => x + 1, retrieval = evaluate(2), notificationsEnabled = false;\n'
    "  chrome.storage.session.get(null); chrome.alarms.create('poll', { periodInMinutes: 1 });\n"
    "  return [runAt, retrieval, notificationsEnabled];\n"
    "})();\n"
)


def filler(extra: str = "") -> str:
    """A bundle-sized JavaScript text (a real bundle is far larger than the gate's placeholder limit)."""
    text = BENIGN_JS * 14
    assert len(text.encode()) > build_extension.MIN_BUNDLE_BYTES
    return text + extra


def tauri_version() -> str:
    return json.loads((REPO / "src-tauri" / "tauri.conf.json").read_text())["version"]


def ats_hosts() -> list[str]:
    return json.loads((REPO / "extension" / "ats-hosts.json").read_text())["matches"]


def manifest_for(variant: str) -> dict:
    manifest = {
        "manifest_version": 3,
        "name": "__MSG_extName__",
        "description": "__MSG_extDescription__",
        "default_locale": "en",
        "version": tauri_version(),
        "icons": {"16": "icons/icon-16.png", "128": "icons/icon-128.png"},
        "action": {"default_title": "__MSG_actionTitle__", "default_icon": {"16": "icons/icon-16.png"}},
        "background": {"service_worker": "background.js"},
        "permissions": list(PERMISSIONS),
        "host_permissions": [LOOPBACK, *ats_hosts()],
        "optional_host_permissions": list(OPTIONAL_HOSTS),
        "content_scripts": [{"matches": ats_hosts(), "js": ["hint.js"], "run_at": "document_idle", "all_frames": False}],
        "options_ui": {"page": "options.html", "open_in_tab": True},
    }
    if variant == "chrome":
        manifest["key"] = (REPO / "extension" / "manifest-key.txt").read_text().strip()
        manifest["minimum_chrome_version"] = "120"
    else:
        manifest["browser_specific_settings"] = {"safari": {"strict_min_version": "17.0"}}
    return manifest


def write_variant(dist: Path, variant: str) -> Path:
    out = dist / variant
    (out / "_locales" / "en").mkdir(parents=True)
    (out / "icons").mkdir()
    (out / "_locales" / "en" / "messages.json").write_text('{"extName": {"message": "Meridian Assistant"}}')
    for icon in ("icon-16.png", "icon-128.png"):
        (out / "icons" / icon).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    for bundle in BUNDLES:
        (out / bundle).write_text(filler())
    (out / "options.html").write_text('<!doctype html><script src="options.js"></script>')
    (out / "manifest.json").write_text(json.dumps(manifest_for(variant), indent=2))
    return out


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    write_variant(root, "chrome")
    write_variant(root, "safari")
    return root


def edit_manifest(dist: Path, variant: str, change) -> None:
    path = dist / variant / "manifest.json"
    manifest = json.loads(path.read_text())
    change(manifest)
    path.write_text(json.dumps(manifest, indent=2))


def problems_of(dist: Path, variant: str) -> list[str]:
    return build_extension.check_variant(variant, dist)


def assert_problem(problems: list[str], *needles: str) -> None:
    assert any(all(needle in problem for needle in needles) for problem in problems), (needles, problems)


# --- the good build --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["chrome", "safari"])
def test_a_clean_production_build_passes(dist, variant):
    assert problems_of(dist, variant) == []


def test_the_pinned_chrome_key_derives_the_registered_extension_id():
    key = (REPO / "extension" / "manifest-key.txt").read_text().strip()
    assert build_extension.chrome_extension_id(key) == "ophngbnbiekjhemmabbpildjedfphlbc"
    assert build_extension.check_source() == []


# --- test builds and debug leftovers ---------------------------------------------------------------------------------

def test_an_e2e_variant_copied_in_as_the_production_build_is_rejected(dist):
    """What the e2e variant adds (a localhost host, e2e.html/e2e.js, the __E2E__ hooks) must each be reported."""
    out = dist / "chrome"
    edit_manifest(dist, "chrome", lambda m: m["host_permissions"].append("http://localhost/*"))
    (out / "e2e.html").write_text("<!doctype html>")
    (out / "e2e.js").write_text("window.__meridianE2E = true;")
    (out / "content.js").write_text(filler("if (__E2E__) { window.__meridianAssist.debug = () => ({}); }"))
    problems = problems_of(dist, "chrome")
    assert_problem(problems, "http://localhost/*", "neither loopback nor a curated ATS host")
    assert_problem(problems, "test or debug files", "e2e.html")
    assert_problem(problems, "test or debug files", "e2e.js")
    assert_problem(problems, "content.js", "__E2E__")


@pytest.mark.parametrize("name", ["__E2E__", "__SHADOW__", "__SAFARI__"])
@pytest.mark.parametrize("bundle", BUNDLES)
def test_a_build_time_global_that_was_not_replaced_is_rejected(dist, name, bundle):
    (dist / "safari" / bundle).write_text(filler(f"const open = {name} === 'open';"))
    assert_problem(problems_of(dist, "safari"), bundle, "build-time global", name)


@pytest.mark.parametrize("snippet", ["eval('1+1');", "const f = new Function('return 1');", "x = eval ('1')", "new  Function ( 'a' )"])
def test_dynamic_code_evaluation_is_rejected(dist, snippet):
    (dist / "chrome" / "background.js").write_text(filler(snippet))
    assert_problem(problems_of(dist, "chrome"), "background.js", "dynamic code evaluation")


@pytest.mark.parametrize(
    "snippet",
    [
        "chrome.sidePanel.open({ tabId: 1 });",
        "chrome.webNavigation.onCompleted.addListener(() => {});",
        "browser.downloads.download({ url: 'x' });",
        "chrome.idle.queryState(15, () => {});",
        "chrome.notifications.create('a', {});",
        "chrome . offscreen . createDocument({});",
        "const contexts = await chrome.runtime.getContexts({});",
    ],
)
def test_an_api_that_safari_lacks_is_rejected_in_every_bundle(dist, snippet):
    for variant in ("chrome", "safari"):
        (dist / variant / "options.js").write_text(filler(snippet))
        assert_problem(problems_of(dist, variant), "options.js", "an API that Safari does not provide")


def test_a_source_map_file_or_reference_is_rejected(dist):
    (dist / "chrome" / "content.js.map").write_text("{}")
    (dist / "safari" / "hint.js").write_text(filler("//# sourceMappingURL=hint.js.map"))
    assert_problem(problems_of(dist, "chrome"), "test or debug files", "content.js.map")
    assert_problem(problems_of(dist, "safari"), "hint.js", "references a source map")


def test_a_ds_store_file_is_rejected(dist):
    (dist / "chrome" / ".DS_Store").write_bytes(b"\0")
    assert_problem(problems_of(dist, "chrome"), "test or debug files", ".DS_Store")


# --- placeholders and missing files ------------------------------------------------------------------------------------

@pytest.mark.parametrize("bundle", BUNDLES)
def test_a_placeholder_bundle_is_rejected(dist, bundle):
    (dist / "chrome" / bundle).write_text("// placeholder\n")
    assert_problem(problems_of(dist, "chrome"), bundle, "looks like a placeholder")


def test_an_empty_bundle_and_an_empty_icon_are_rejected(dist):
    (dist / "safari" / "content.js").write_text("")
    (dist / "safari" / "icons" / "icon-16.png").write_bytes(b"")
    problems = problems_of(dist, "safari")
    assert_problem(problems, "content.js", "placeholder")
    assert_problem(problems, "icon-16.png", "empty")


@pytest.mark.parametrize("relative", ["background.js", "options.html", "icons/icon-128.png", "hint.js"])
def test_a_file_the_manifest_needs_is_reported_missing(dist, relative):
    (dist / "chrome" / relative).unlink()
    assert_problem(problems_of(dist, "chrome"), relative, "missing")


def test_a_missing_manifest_asks_for_a_build(dist):
    (dist / "chrome" / "manifest.json").unlink()
    assert problems_of(dist, "chrome") == ["extension/dist/chrome/manifest.json is missing: build the extension first (make extension)"]


def test_a_manifest_that_is_not_json_is_a_problem_not_a_crash(dist):
    (dist / "safari" / "manifest.json").write_text("{ not json")
    assert_problem(problems_of(dist, "safari"), "manifest.json", "cannot be read as JSON")


def test_a_missing_default_locale_file_is_reported(dist):
    shutil.rmtree(dist / "chrome" / "_locales")
    assert_problem(problems_of(dist, "chrome"), "_locales/en/messages.json is missing")


# --- the manifest contract (docs/EXTENSION_DESIGN.md 7.1) ----------------------------------------------------------------

@pytest.mark.parametrize("variant", ["chrome", "safari"])
@pytest.mark.parametrize(
    "permissions",
    [
        PERMISSIONS + ["tabs"],
        PERMISSIONS + ["history"],
        PERMISSIONS + ["webRequest"],
        PERMISSIONS + ["downloads"],
        PERMISSIONS + ["<all_urls>"],
        PERMISSIONS[:-1],
        list(reversed(PERMISSIONS)),
        [],
    ],
)
def test_the_permissions_must_be_exactly_the_four_of_the_design(dist, variant, permissions):
    edit_manifest(dist, variant, lambda m: m.update(permissions=permissions))
    assert_problem(problems_of(dist, variant), "permissions must be exactly")


@pytest.mark.parametrize("host", ["<all_urls>", "https://*/*", "http://localhost/*", "https://example.com/*", "http://127.0.0.1:*/*"])
def test_a_host_permission_outside_loopback_and_the_curated_list_is_rejected(dist, host):
    edit_manifest(dist, "chrome", lambda m: m["host_permissions"].append(host))
    assert_problem(problems_of(dist, "chrome"), f"host_permissions has {host}")


def test_loopback_must_stay_a_host_permission(dist):
    edit_manifest(dist, "safari", lambda m: m.update(host_permissions=ats_hosts()))
    assert_problem(problems_of(dist, "safari"), f"host_permissions must include {LOOPBACK}")


@pytest.mark.parametrize("hosts", [["https://*/*"], ["https://*/*", "http://*/*", "ftp://*/*"], [], ["<all_urls>"]])
def test_the_optional_hosts_are_exactly_the_two_broad_patterns(dist, hosts):
    edit_manifest(dist, "chrome", lambda m: m.update(optional_host_permissions=hosts))
    assert_problem(problems_of(dist, "chrome"), "optional_host_permissions must be exactly")


def test_the_only_static_content_script_is_the_hint_on_ats_hosts(dist):
    def widen(manifest):
        manifest["content_scripts"][0]["js"] = ["hint.js", "content.js"]

    def broaden(manifest):
        manifest["content_scripts"][0]["matches"] = ["https://*/*"]

    def second(manifest):
        manifest["content_scripts"].append(copy.deepcopy(manifest["content_scripts"][0]))

    for change in (widen, broaden, second):
        out = dist / "chrome"
        original = (out / "manifest.json").read_text()
        edit_manifest(dist, "chrome", change)
        assert_problem(problems_of(dist, "chrome"), "the only static content script must be hint.js")
        (out / "manifest.json").write_text(original)
    assert problems_of(dist, "chrome") == []


def test_a_toolbar_popup_is_rejected(dist):
    edit_manifest(dist, "chrome", lambda m: m["action"].update(default_popup="popup.html"))
    assert_problem(problems_of(dist, "chrome"), "default_popup must not be set")


def test_version_must_equal_the_tauri_version(dist):
    edit_manifest(dist, "chrome", lambda m: m.update(version="9.9.9"))
    assert_problem(problems_of(dist, "chrome"), "9.9.9", "differs from tauri.conf.json")


def test_manifest_version_two_and_unlocalised_names_are_rejected(dist):
    edit_manifest(dist, "safari", lambda m: m.update(manifest_version=2, name="Meridian Assistant"))
    problems = problems_of(dist, "safari")
    assert_problem(problems, "manifest_version must be 3")
    assert_problem(problems, "must be localised")


def test_the_service_worker_and_options_page_are_pinned(dist):
    edit_manifest(dist, "chrome", lambda m: m.update(background={"service_worker": "sw.js"}, options_ui={"page": "other.html"}))
    problems = problems_of(dist, "chrome")
    assert_problem(problems, "service_worker must be background.js")
    assert_problem(problems, "options_ui.page must be options.html")


# --- the Chrome key and the Safari manifest ----------------------------------------------------------------------------

def test_the_chrome_manifest_needs_the_pinned_key_and_a_minimum_version(dist):
    edit_manifest(dist, "chrome", lambda m: (m.pop("key"), m.pop("minimum_chrome_version")))
    problems = problems_of(dist, "chrome")
    assert_problem(problems, "must carry the pinned public key")
    assert_problem(problems, "minimum_chrome_version is missing")


def test_a_chrome_key_whose_id_is_not_registered_is_rejected(dist):
    key = manifest_for("chrome")["key"]
    other = key[:40] + ("B" if key[40] != "B" else "C") + key[41:]
    edit_manifest(dist, "chrome", lambda m: m.update(key=other))
    assert_problem(problems_of(dist, "chrome"), "which is not in CHROME_EXTENSION_IDS")


def test_the_chrome_manifest_must_not_carry_safari_settings(dist):
    edit_manifest(dist, "chrome", lambda m: m.update(browser_specific_settings={"safari": {"strict_min_version": "17.0"}}))
    assert_problem(problems_of(dist, "chrome"), "browser_specific_settings belongs only in the Safari manifest")


@pytest.mark.parametrize(
    "field, value",
    [
        ("key", "MIIB-anything"),
        ("minimum_chrome_version", "120"),
        ("commands", {"_execute_action": {}}),
        ("side_panel", {"default_path": "panel.html"}),
        ("update_url", "https://example.com/update.xml"),
    ],
)
def test_the_safari_manifest_must_not_contain_chrome_only_fields(dist, field, value):
    edit_manifest(dist, "safari", lambda m: m.update({field: value}))
    assert_problem(problems_of(dist, "safari"), f"the Safari manifest must not contain {field}")


def test_the_safari_manifest_needs_a_minimum_safari_version(dist):
    edit_manifest(dist, "safari", lambda m: m.pop("browser_specific_settings"))
    assert_problem(problems_of(dist, "safari"), "strict_min_version is missing")


def test_a_private_key_in_the_source_tree_is_rejected(tmp_path, monkeypatch):
    folder = tmp_path / "extension"
    folder.mkdir()
    (folder / "manifest-key.txt").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    monkeypatch.setattr(build_extension, "EXTENSION", folder)
    assert_problem(build_extension.check_source(), "PRIVATE key")
    (folder / "manifest-key.txt").unlink()
    assert_problem(build_extension.check_source(), "manifest-key.txt is missing")
    (folder / "manifest-key.txt").write_text("MIIBIjANBgkq\n")
    assert build_extension.check_source() == []


# --- the real build, when there is one -------------------------------------------------------------------------------------

def real_problems(variant: str) -> list[str]:
    """Problems of the real extension/dist/<variant>. Another session may rebuild it while this runs (the build empties the folder
    first), so a result that complains about missing files is read again a few times before it counts."""
    for attempt in range(4):
        problems = build_extension.check_variant(variant)
        if not any("is missing" in problem for problem in problems) or attempt == 3:
            return problems
        time.sleep(3)
    return problems


@pytest.mark.parametrize("variant", ["chrome", "safari"])
def test_the_real_production_build_passes_when_it_has_been_built(variant):
    if not (REAL_DIST / variant / "manifest.json").is_file():
        pytest.skip(f"extension/dist/{variant} has not been built (make extension)")
    assert real_problems(variant) == [], "extension/dist is stale or broken: run make extension"


def test_the_real_e2e_build_is_never_accepted_as_a_production_build(tmp_path):
    if not (REAL_DIST / "e2e" / "manifest.json").is_file():
        pytest.skip("extension/dist/e2e has not been built (make test-ext builds it)")
    try:
        shutil.copytree(REAL_DIST / "e2e", tmp_path / "chrome")
    except (OSError, shutil.Error):
        pytest.skip("extension/dist/e2e was being rebuilt")
    problems = build_extension.check_variant("chrome", tmp_path)
    assert_problem(problems, "test or debug files", "e2e.html")
    assert_problem(problems, "neither loopback nor a curated ATS host")


# --- scripts/build_sidecar.sh embeds exactly dist/chrome -----------------------------------------------------------------

def chrome_dist_with(files: list[str], root: Path) -> Path:
    for relative in files:
        path = root / "chrome" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return root


CHROME_FILES = ["manifest.json", "background.js", "content.js", "hint.js", "options.html", "options.js", "icons/icon-16.png"]


def archive_listing(entries: list[str]) -> str:
    """What `pyi-archive_viewer --list --recursive --brief` prints: a header, then one entry per line with one space of indent."""
    noise = ["struct", "pyimod01_archive", "jobagent/extension/assets.py", "jobagent/extension/api.py", "migrations/versions/0013_x.py"]
    return "Contents of 'meridian-backend' (PKG/CArchive):\n" + "\n".join(" " + entry for entry in noise + entries) + "\n"


def test_a_sidecar_that_embeds_the_whole_extension_passes(tmp_path):
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    listing = archive_listing([f"extension/chrome/{name}" for name in CHROME_FILES])
    assert build_extension.sidecar_problems(listing, root) == []


def test_a_sidecar_with_only_the_manifest_is_rejected(tmp_path):
    """The old check looked for extension/chrome/manifest.json alone and would have shipped this."""
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    problems = build_extension.sidecar_problems(archive_listing(["extension/chrome/manifest.json"]), root)
    for name in CHROME_FILES[1:]:
        assert_problem(problems, f"does not contain extension/chrome/{name}")
    assert len(problems) == len(CHROME_FILES) - 1


def test_a_sidecar_without_any_extension_is_rejected(tmp_path):
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    assert len(build_extension.sidecar_problems(archive_listing([]), root)) == len(CHROME_FILES)


@pytest.mark.parametrize("stray", ["extension/chrome/e2e.html", "extension/e2e/content.js", "extension/safari/manifest.json"])
def test_a_sidecar_carrying_anything_else_under_extension_is_rejected(tmp_path, stray):
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    listing = archive_listing([f"extension/chrome/{name}" for name in CHROME_FILES] + [stray])
    assert_problem(build_extension.sidecar_problems(listing, root), stray, "not part of extension/dist/chrome")


def test_the_python_package_named_extension_is_not_mistaken_for_the_embedded_extension(tmp_path):
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    listing = archive_listing([f"extension/chrome/{name}" for name in CHROME_FILES] + ["jobagent/extension/server.py"])
    assert build_extension.sidecar_problems(listing, root) == []


def test_an_empty_chrome_dist_cannot_pass_the_sidecar_check(tmp_path):
    (tmp_path / "chrome").mkdir()
    assert_problem(build_extension.sidecar_problems(archive_listing([]), tmp_path), "holds no files")
    assert_problem(build_extension.sidecar_problems(archive_listing([]), tmp_path / "missing"), "holds no files")


def fake_viewer(directory: Path, listing: str, exit_code: int = 0) -> None:
    viewer = directory / "pyi-archive_viewer"
    listing_file = directory / "listing.txt"
    listing_file.write_text(listing)
    viewer.write_text(f"#!/bin/sh\ncat '{listing_file}'\nexit {exit_code}\n")
    viewer.chmod(viewer.stat().st_mode | stat.S_IXUSR)


def test_archive_listing_reads_the_viewer_next_to_the_interpreter_and_reports_failures(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    fake_viewer(bin_dir, archive_listing(["extension/chrome/manifest.json"]))
    assert " extension/chrome/manifest.json" in build_extension.archive_listing(tmp_path / "meridian-backend")
    fake_viewer(bin_dir, "boom\n", exit_code=3)
    with pytest.raises(build_extension.BuildError, match="cannot list"):
        build_extension.archive_listing(tmp_path / "meridian-backend")


def test_verify_sidecar_option_exits_nonzero_when_the_extension_is_incomplete(tmp_path, monkeypatch, capsys):
    if not (REAL_DIST / "chrome" / "manifest.json").is_file():
        pytest.skip("extension/dist/chrome has not been built (make extension)")
    names = sorted(
        path.relative_to(REAL_DIST / "chrome").as_posix() for path in (REAL_DIST / "chrome").rglob("*") if path.is_file()
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    fake_viewer(bin_dir, archive_listing([f"extension/chrome/{name}" for name in names]))
    build_extension.main(["--verify-sidecar", "meridian-backend"])
    assert "embeds all of extension/dist/chrome" in capsys.readouterr().out
    fake_viewer(bin_dir, archive_listing([f"extension/chrome/{name}" for name in names if name != "hint.js"]))
    with pytest.raises(SystemExit) as stopped:
        build_extension.main(["--verify-sidecar", "meridian-backend"])
    assert stopped.value.code == 1
    assert "extension/chrome/hint.js" in capsys.readouterr().err


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def embedded_hashes(root: Path, **overrides: str) -> dict[str, str]:
    """What archive_hashes() answers for a backend that embeds exactly root/chrome (with `overrides` for named files)."""
    hashes = {
        f"extension/chrome/{path.relative_to(root / 'chrome').as_posix()}": sha(path)
        for path in sorted((root / "chrome").rglob("*"))
        if path.is_file()
    }
    return hashes | {f"extension/chrome/{name}": digest for name, digest in overrides.items()}


def test_a_sidecar_that_embeds_the_current_bytes_passes_the_content_check(tmp_path):
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    assert build_extension.sidecar_content_problems(embedded_hashes(root), root) == []


def test_a_same_size_older_file_is_caught_by_the_content_check_only(tmp_path):
    """A sidecar frozen before the extension was rebuilt has the same names and often the same sizes (a version bump)."""
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    stale = embedded_hashes(root, **{"manifest.json": hashlib.sha256(b"y").hexdigest()})
    listing = archive_listing([f"extension/chrome/{name}" for name in CHROME_FILES])
    assert build_extension.sidecar_problems(listing, root) == [], "names alone cannot see it"
    problems = build_extension.sidecar_content_problems(stale, root)
    assert problems == ["the backend's extension/chrome/manifest.json differs from extension/dist/chrome/manifest.json"]


def test_every_differing_file_is_reported_and_files_missing_from_the_archive_are_left_to_the_name_check(tmp_path):
    root = chrome_dist_with(CHROME_FILES, tmp_path)
    hashes = embedded_hashes(root, **{"content.js": "0" * 64, "hint.js": "1" * 64})
    del hashes["extension/chrome/background.js"]
    problems = build_extension.sidecar_content_problems(hashes, root)
    assert [problem.split()[2] for problem in problems] == ["extension/chrome/content.js", "extension/chrome/hint.js"]


def fake_python(directory: Path, lines: str, exit_code: int = 0) -> None:
    """The interpreter next to the viewer: prints `lines` instead of reading a real PyInstaller archive."""
    output = directory / "hashes.txt"
    output.write_text(lines)
    python = directory / "python"
    python.write_text(f"#!/bin/sh\ncat '{output}'\nexit {exit_code}\n")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)


def test_archive_hashes_reads_name_and_digest_lines_through_the_interpreter_next_to_the_viewer(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python"))
    monkeypatch.setattr(build_extension, "ROOT", tmp_path)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "bin").symlink_to(bin_dir)
    fake_viewer(bin_dir, "")
    fake_python(bin_dir, f"{'a' * 64} extension/chrome/manifest.json\n{'b' * 64} extension/chrome/icons/icon 16.png\n")
    assert build_extension.archive_hashes(tmp_path / "meridian-backend") == {
        "extension/chrome/manifest.json": "a" * 64, "extension/chrome/icons/icon 16.png": "b" * 64,
    }
    fake_python(bin_dir, "Traceback: not a PyInstaller archive\n", exit_code=1)
    with pytest.raises(build_extension.BuildError, match="cannot read the files embedded"):
        build_extension.archive_hashes(tmp_path / "meridian-backend")


def test_verify_sidecar_compares_contents_only_when_asked(tmp_path, monkeypatch, capsys):
    root = chrome_dist_with(CHROME_FILES, tmp_path / "dist")
    monkeypatch.setattr(build_extension, "DIST", root)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    fake_viewer(bin_dir, archive_listing([f"extension/chrome/{name}" for name in CHROME_FILES]))
    current = embedded_hashes(root)
    stale = embedded_hashes(root, **{"content.js": "f" * 64})

    def hashes_line(hashes):
        return "".join(f"{digest} {name}\n" for name, digest in hashes.items())

    fake_python(bin_dir, hashes_line(stale))
    build_extension.main(["--verify-sidecar", "meridian-backend"])  # names only: passes
    assert "byte for byte" not in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:
        build_extension.main(["--verify-sidecar", "meridian-backend", "--compare-contents"])
    assert stopped.value.code == 1
    error = capsys.readouterr().err
    assert "older build of the Chrome extension" in error and "extension/chrome/content.js differs" in error
    fake_python(bin_dir, hashes_line(current))
    build_extension.main(["--verify-sidecar", "meridian-backend", "--compare-contents"])
    assert "byte for byte" in capsys.readouterr().out
    fake_python(bin_dir, "boom\n", exit_code=1)
    with pytest.raises(SystemExit) as stopped:
        build_extension.main(["--verify-sidecar", "meridian-backend", "--compare-contents"])
    assert stopped.value.code == 1 and "cannot read the files embedded" in capsys.readouterr().err


def test_compare_contents_needs_a_sidecar_to_compare():
    with pytest.raises(SystemExit) as stopped:
        build_extension.main(["--compare-contents"])
    assert stopped.value.code == 2


def test_install_only_and_no_install_contradict_each_other():
    with pytest.raises(SystemExit) as stopped:
        build_extension.main(["--install-only", "--no-install"])
    assert stopped.value.code == 2
