"""Remove only generated build outputs, never user data."""

from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
for relative in ("dist", "build", "frontend/dist", "src-tauri/target", "src-tauri/binaries"):
    path = root / relative
    if path.is_dir():
        shutil.rmtree(path)
