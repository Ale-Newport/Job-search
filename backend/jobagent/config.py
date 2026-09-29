from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Meridian"


def data_directory() -> Path:
    root = Path(os.environ.get("MERIDIAN_DATA_DIR", Path.home() / "Library/Application Support" / APP_NAME))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("database", "documents", "browser-profile", "cache", "logs", "backups", "models"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
    return root
