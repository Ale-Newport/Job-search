"""The Chrome extension folder shipped with Meridian and its stable "Load unpacked" location.

Chrome stores the absolute path of an unpacked extension, so Meridian copies the bundled build to a path that does not
change across app updates: ``<data_dir>/extensions/chrome`` (``~/Library/Application Support/Meridian/extensions/chrome``).

* **Where the build may come from** (``extension_source``). The folder the user loads in Chrome is code that runs in
  their browser, so only Meridian's own bundle may feed it:

  - a *frozen* sidecar (PyInstaller: ``sys.frozen`` or ``sys._MEIPASS``) uses ``sys._MEIPASS/extension`` and nothing
    else. It never looks next to its own code: ``Path(__file__).parents[3]`` is the user's temporary directory there
    (``$TMPDIR/extension/dist``), where any local process can plant a decoy folder that Meridian would then install as
    "the" extension and whose version it would advertise. ``MERIDIAN_EXTENSION_SRC`` is honoured by a frozen sidecar
    only when it lies inside ``MERIDIAN_APP_BUNDLE``: the variable the Tauri parent sets (release builds only) to the
    signed ``Meridian.app`` that launched the sidecar, whose files cannot be changed without breaking its signature.
    Tauri does not set ``MERIDIAN_EXTENSION_SRC`` today; if it ever does, it must point inside the bundle;
  - an unfrozen run (development, tests) uses ``MERIDIAN_EXTENSION_SRC``, then ``<repo>/extension/dist``.

  Every candidate must hold ``chrome/manifest.json`` whose ``key`` derives one of ``CHROME_EXTENSION_IDS``: the folder
  Chrome loads must BE the pinned Meridian Assistant (a keyless or foreign manifest would get another id, and Meridian
  would advertise and install something that is not what the pairing and the app UI recognise). A candidate that fails
  is skipped, and when none passes ``install_chrome_extension`` says why (``UntrustedExtension``). The key proves the
  identity the manifest claims, not who wrote the folder (the public key is public): that part is the rule above, that
  only the signed bundle (or a developer's own checkout) is ever read.
* ``install_chrome_extension`` replaces the folder only when the content digest changed OR the installed folder no longer
  matches what it should hold (it is re-hashed at every call, so a changed or damaged installed folder is repaired, not
  trusted). The copy is hashed again before it replaces anything: a source that changes while it is copied is refused.
  The swap is two renames (there is no atomic directory-over-directory rename), the digest marker lives OUTSIDE the
  folder (Chrome warns about foreign files inside an extension) and is written last, so a crash at any point heals on
  the next start.
* Dotfiles, source maps and symlinks are never shipped; directories are 0755 and files 0644.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from . import CHROME_EXTENSION_IDS

FOLDER = "chrome"
MARKER = "chrome.install.json"  # sibling of the extension folder, not inside it
STAMP = "build.json"  # inside the build: {"digest": tree_digest(chrome, ignore={STAMP})}, written last by extension/build.mjs
NO_VERSION = "0.0.0"
CHROME_MANAGED = frozenset({"_metadata"})  # what Chrome itself may add to an unpacked extension's folder (top level only)
_version_cache: dict[str, tuple[int, str]] = {}
_digest_cache: dict[str, tuple[tuple, str]] = {}
_trust_cache: dict[str, tuple[tuple, Exception | None]] = {}
_ABSENT = object()


class UntrustedExtension(ValueError):
    """A folder that is not the pinned Meridian Assistant, or one that changed while it was being copied. The message is
    a plain sentence without paths; Meridian shows it instead of installing the folder."""


def frozen() -> bool:
    """A PyInstaller sidecar (``sys.frozen`` or ``sys._MEIPASS``): its own code lives in a temporary extraction folder."""
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def extension_id(key) -> str | None:
    """The id Chrome derives from a manifest ``key``: the first 32 hex digits of sha256(DER public key), 0-f -> a-p.
    None when ``key`` is not a base64 key at all."""
    if not isinstance(key, str) or not key.strip():
        return None
    try:
        der = base64.b64decode(key.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not der:
        return None
    return "".join(chr(ord("a") + int(ch, 16)) for ch in hashlib.sha256(der).hexdigest()[:32])


def manifest_problem(manifest) -> str | None:
    """Why a parsed manifest is not the pinned Meridian Assistant, or None. Only ``CHROME_EXTENSION_IDS`` count here:
    ``MERIDIAN_EXTENSION_IDS`` widens which origins the bridge recognises, never which folder Meridian installs."""
    if not isinstance(manifest, dict):
        return "The extension manifest is not valid"
    derived = extension_id(manifest.get("key"))
    if derived is None:
        return "The extension in this build is not pinned to the Meridian Assistant id (its manifest has no valid key)"
    if derived not in CHROME_EXTENSION_IDS:
        return "The extension in this build is not the Meridian Assistant (its key does not match the pinned id)"
    return None


def _read_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The extension manifest is not an object")
    return data


def _judge(manifest: Path):
    """None when ``manifest`` is a pinned Meridian Assistant manifest, the refusal (an exception) when it is not, and
    ``_ABSENT`` when there is no manifest file at all. Cached per file stamp: every status call asks."""
    try:
        status = os.lstat(manifest)
    except OSError:
        return _ABSENT
    if stat.S_ISLNK(status.st_mode):
        return UntrustedExtension("The extension manifest is a link, which Meridian does not follow")
    if not stat.S_ISREG(status.st_mode):
        return _ABSENT
    stamp = (status.st_mtime_ns, status.st_size, status.st_ino)
    cached = _trust_cache.get(str(manifest))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        problem = manifest_problem(_read_manifest(manifest))
    except (OSError, ValueError):
        verdict: Exception | None = ValueError("The extension manifest cannot be read")
    else:
        verdict = UntrustedExtension(problem) if problem else None
    if len(_trust_cache) >= 16:
        _trust_cache.clear()
    _trust_cache[str(manifest)] = (stamp, verdict)
    return verdict


def _inside_app_bundle(path: Path) -> bool:
    """Is ``path`` inside the ``.app`` the Tauri parent announced in ``MERIDIAN_APP_BUNDLE``? (Symlinks resolved.)"""
    bundle = os.environ.get("MERIDIAN_APP_BUNDLE")
    if not bundle:
        return False
    try:
        root, resolved = Path(bundle).resolve(), path.resolve()
    except (OSError, RuntimeError):
        return False
    return root.suffix == ".app" and resolved.is_relative_to(root)


def source_candidates() -> list[Path]:
    """Where a build may be taken from, best first (see the module docstring for why a frozen sidecar has no repo)."""
    override = os.environ.get("MERIDIAN_EXTENSION_SRC")
    found: list[Path] = []
    if frozen():
        if override and _inside_app_bundle(Path(override)):
            found.append(Path(override))
        if getattr(sys, "_MEIPASS", None):
            found.append(Path(sys._MEIPASS) / "extension")
        return found
    if override:
        found.append(Path(override))
    found.append(Path(__file__).resolve().parents[3] / "extension" / "dist")  # backend/jobagent/extension/ -> repo
    return found


def inspect_source() -> tuple[Path | None, Exception | None]:
    """(the first candidate that holds a pinned ``chrome/manifest.json``, why an earlier candidate that held a manifest was
    refused). A candidate without a manifest is simply not there; a refused one never stops the search."""
    refusal = None
    for candidate in source_candidates():
        verdict = _judge(candidate / FOLDER / "manifest.json")
        if verdict is _ABSENT:
            continue
        if verdict is None:
            return candidate, refusal
        refusal = refusal or verdict
    return None, refusal


def extension_source() -> Path | None:
    """The directory whose ``chrome/`` holds the build this Meridian ships, or None (none bundled, or none trusted)."""
    return inspect_source()[0]


def bundled_version(source: Path | None = None) -> str:
    """Version of the extension that ships with this build ('0.0.0' when none is bundled)."""
    source = source or extension_source()
    if source is None:
        return NO_VERSION
    manifest = source / FOLDER / "manifest.json"
    try:
        stamp = manifest.stat().st_mtime_ns
        cached = _version_cache.get(str(manifest))
        if cached and cached[0] == stamp:
            return cached[1]
        version = str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or NO_VERSION)
    except (OSError, ValueError, AttributeError):
        return NO_VERSION
    _version_cache[str(manifest)] = (stamp, version)
    return version


def bundled_digest(source: Path | None = None) -> str:
    """Content digest of the extension that ships with this build, as stamped by the build ('' when none is bundled or the
    build carries no stamp). It is ``tree_digest(chrome, ignore={STAMP})``: the whole shipped tree except the stamp itself.
    ``hello`` advertises it next to the version so that a rebuilt extension whose version number did not change is still
    seen as outdated by the copy a browser has loaded (the copy compares the digest in its own stamp)."""
    source = source or extension_source()
    if source is None:
        return ""
    stamp = source / FOLDER / STAMP
    try:
        status = os.lstat(stamp)
        if not stat.S_ISREG(status.st_mode):
            return ""
        key = (status.st_mtime_ns, status.st_size, status.st_ino)
        cached = _digest_cache.get(str(stamp))
        if cached and cached[0] == key:
            return cached[1]
        digest = json.loads(stamp.read_text(encoding="utf-8")).get("digest")
    except (OSError, ValueError, AttributeError):
        return ""
    value = digest if isinstance(digest, str) and len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest) else ""
    if len(_digest_cache) >= 16:
        _digest_cache.clear()
    _digest_cache[str(stamp)] = (key, value)
    return value


def version_tuple(version: str | None) -> tuple[int, ...]:
    parts = []
    for piece in str(version or "").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_older(version: str | None, reference: str | None) -> bool:
    """True when ``version`` is lower than ``reference`` (numeric dotted compare; unknown counts as 0)."""
    a, b = version_tuple(version), version_tuple(reference)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


def _skipped(directory, names):
    """Dotfiles, source maps, caches and symlinks never ship (the digest and the copy must agree)."""
    return [
        name for name in names
        if name.startswith(".") or name.endswith(".map") or name == "__pycache__" or (Path(directory) / name).is_symlink()
    ]


def tree_digest(root: Path, ignore=frozenset()) -> str:
    """sha256 over the sorted (relative path, sha256 of the bytes) of every shipped file. ``ignore`` names top-level
    entries that are not part of the build (what Chrome keeps in an installed folder)."""
    entries = []
    for directory, folders, files in os.walk(root):
        here = Path(directory)
        skipped = set(_skipped(here, [*folders, *files]))
        if here == Path(root):
            skipped |= set(ignore)
        folders[:] = sorted(name for name in folders if name not in skipped)
        entries += [here / name for name in files if name not in skipped]
    digest = hashlib.sha256()
    for path in sorted(entries):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def installed_path(data_dir: Path) -> Path:
    return Path(data_dir) / "extensions" / FOLDER


def installed_info(data_dir: Path) -> dict:
    """What is installed right now: {installed, path, version}. Reads the manifest of the folder itself, and a folder whose
    manifest is not the pinned Meridian Assistant is reported as not installed (with a ``problem``), so the app offers to
    prepare it again instead of pointing the user at it."""
    target = installed_path(data_dir)
    try:
        manifest = _read_manifest(target / "manifest.json")
    except (OSError, ValueError):
        return {"installed": False, "path": str(target), "version": ""}
    problem = manifest_problem(manifest)
    if problem is not None:
        return {"installed": False, "path": str(target), "version": "", "problem": problem}
    return {"installed": True, "path": str(target), "version": str(manifest.get("version") or "")}


def _remove(path: Path) -> None:
    """Delete a leftover whatever it is: a link is unlinked (rmtree refuses it), a folder removed, an absent path ignored."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _installed_matches(target: Path, digest: str) -> bool:
    """Is the installed folder exactly the build with this digest (not a link, nothing added, changed or missing)?"""
    try:
        return (target.is_dir() and not target.is_symlink() and (target / "manifest.json").is_file()
                and tree_digest(target, CHROME_MANAGED) == digest)
    except OSError:
        return False


def install_chrome_extension(data_dir: Path, source: Path | None = None) -> dict:
    """Copy ``<source>/chrome`` to ``<data_dir>/extensions/chrome``; replace only when the content changed.

    Returns {installed, path, version, digest, name, changed} or {installed: False, reason} when nothing is bundled.
    Raises :class:`UntrustedExtension` (a ``ValueError``) for a folder that is not the pinned Meridian Assistant, and a
    plain ``ValueError`` for a manifest that cannot be read."""
    if source is None:
        source = extension_source()
        if source is None:
            refusal = inspect_source()[1]  # a folder was found and refused (a decoy, a damaged manifest) vs nothing at all
            if refusal is not None:
                raise refusal
            return {"installed": False, "reason": "The browser extension is not bundled with this build"}
    else:
        verdict = _judge(Path(source) / FOLDER / "manifest.json")
        if isinstance(verdict, Exception):
            raise verdict
    origin = Path(source) / FOLDER
    manifest = _read_manifest(origin / "manifest.json")
    digest = tree_digest(origin)
    base = Path(data_dir) / "extensions"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    target, marker = base / FOLDER, base / MARKER
    info = {
        "installed": True, "path": str(target), "version": str(manifest.get("version", "")), "digest": digest,
        "name": str(manifest.get("name", "")),
    }
    try:
        current = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if current.get("digest") == digest and _installed_matches(target, digest):
        return info | {"changed": False}
    # Clean leftovers of a crash, then stage next to the target so the rename stays on one filesystem.
    for leftover in base.glob(f".{FOLDER}.*"):
        _remove(leftover)
    staging = base / f".{FOLDER}.new-{os.getpid()}"
    shutil.copytree(origin, staging, symlinks=True, ignore=_skipped)
    try:
        # What gets installed is what was just copied, not what was read a moment ago: a source that changes between the
        # hash and the copy (another process writing into it) is refused, and the copy's own manifest is the one judged.
        if tree_digest(staging) != digest:
            raise UntrustedExtension("The bundled extension changed while Meridian was copying it")
        copied = _read_manifest(staging / "manifest.json")
        problem = manifest_problem(copied)
        if problem is not None:
            raise UntrustedExtension(problem)
    except BaseException:
        _remove(staging)
        raise
    info |= {"version": str(copied.get("version", "")), "name": str(copied.get("name", ""))}
    for path in [staging, *staging.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    retired = base / f".{FOLDER}.old-{os.getpid()}"
    if target.exists() or target.is_symlink():
        os.rename(target, retired)  # Chrome re-reads the path on reload/restart
    os.rename(staging, target)
    _remove(retired)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": info["version"], "digest": digest}), encoding="utf-8")
    os.replace(temporary, marker)  # marker last: a crash before this simply copies again at the next start
    return info | {"changed": True}
