"""Remove generated build outputs, never sources and never user data.

  clean_build.py --dry-run   list what would be removed and remove nothing
  clean_build.py             list it and ask first; refuses when stdin is not a terminal, because nobody can answer
  clean_build.py --yes       remove without asking (what `make clean` runs)

Removed, relative to the repository root: dist, build (which holds the Safari build in build/safari), frontend/dist,
extension/dist, src-tauri/target and src-tauri/binaries. Never touched: .local-data (the working data folder), node_modules,
.venv, .local-models and .git, whatever is listed above. Rebuild with `make extension`, `make build` or `make setup`.
"""

import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ("dist", "build", "frontend/dist", "extension/dist", "src-tauri/target", "src-tauri/binaries")
# Folders that hold the user's data, installed dependencies or history. A listed output may neither be one of them, lie
# inside one, nor contain one; the check is made before anything is removed, so a mistaken edit of OUTPUTS cannot delete them.
PROTECTED_NAMES = frozenset({".local-data", ".local-models", ".venv", ".git", "node_modules"})
PROTECTED_PATHS = (
    ".local-data",
    ".local-models",
    ".venv",
    ".git",
    "node_modules",
    "frontend/node_modules",
    "extension/node_modules",
    "extension/tests/widgets/node_modules",
)


def protection_problem(root, relative):
    """Why `relative` must not be removed, or None."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        return f"{relative!r} is not a path inside the repository"
    if PROTECTED_NAMES.intersection(parts):
        return f"{relative} is or lies inside a protected folder ({', '.join(sorted(PROTECTED_NAMES))})"
    target = root / relative
    for protected in PROTECTED_PATHS:
        if (root / protected) == target or target in (root / protected).parents:
            return f"{relative} would contain the protected folder {protected}"
    return None


def plan(root=ROOT, outputs=None):
    """(existing output directories to remove, problems that leave an output alone). Reads, never deletes."""
    found, problems = [], []
    for relative in OUTPUTS if outputs is None else outputs:
        refusal = protection_problem(root, relative)
        path = root / relative
        if refusal:
            problems.append(f"skipped {refusal}")
        elif path.is_symlink():
            problems.append(f"skipped {relative}: it is a symbolic link, remove the link yourself if it is meant to go")
        elif path.is_dir():
            found.append(relative)
        elif path.exists():
            problems.append(f"skipped {relative}: it is not a directory")
    return found, problems


def tree_size(path):
    """Bytes under `path`, counting files only and never following symbolic links; unreadable entries count as 0."""
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would be removed, remove nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    options = parser.parse_args(argv)

    found, problems = plan(root)
    for problem in problems:
        print(f"clean_build: {problem}", file=sys.stderr)
    status = 1 if problems else 0
    if not found:
        print("clean_build: no build outputs to remove")
        return status
    print("Build outputs under " + str(root) + ":")
    for relative in found:
        print(f"  {relative}  ({human(tree_size(root / relative))})")
    print("Never touched: .local-data, node_modules, .venv, .local-models, .git and every source file.")
    if options.dry_run:
        print("Dry run: nothing was removed.")
        return status
    if not options.yes:
        if not sys.stdin.isatty():
            print("clean_build: refusing to delete without --yes because there is no terminal to ask on", file=sys.stderr)
            return 2
        try:
            answer = input("Remove these directories? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was removed.")
            return 1
    for relative in found:
        try:
            shutil.rmtree(root / relative)
        except OSError as error:
            print(f"clean_build: could not remove {relative}: {error}", file=sys.stderr)
            status = 1
        else:
            print(f"removed {relative}")
    return status


if __name__ == "__main__":
    sys.exit(main())
