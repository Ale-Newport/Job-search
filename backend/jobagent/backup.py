from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"MERIDIAN1\n"
MAX_BACKUP_BYTES = 512 * 1024 * 1024


def derive(password: str, salt: bytes) -> bytes:
    if len(password) < 12:
        raise ValueError("Use a backup password with at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())


def export_backup(db, data_dir: Path, password: str) -> Path:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = derive(password, salt)
    with tempfile.TemporaryDirectory(dir=data_dir / "cache") as temporary:
        snapshot = Path(temporary) / "meridian.sqlite3"
        with sqlite3.connect(db.path) as source, sqlite3.connect(snapshot) as target:
            source.backup(target)
        # References are harmless, but tokens can never be present in a backup.
        with sqlite3.connect(snapshot) as clean:
            clean.execute("DELETE FROM secret_references")
            clean.execute("UPDATE integrations SET status='disconnected',last_sync=NULL,last_error=NULL")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "database/meridian.sqlite3")
            archive.writestr(
                "manifest.json", json.dumps({"version": 1, "created_at": datetime.now(timezone.utc).isoformat()})
            )
            total = snapshot.stat().st_size
            for path in (data_dir / "documents").rglob("*"):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
                    if total > MAX_BACKUP_BYTES:
                        raise ValueError("This backup exceeds the 512 MB export limit")
                    archive.write(path, path.relative_to(data_dir))
        ciphertext = AESGCM(key).encrypt(nonce, buffer.getvalue(), MAGIC)
    destination = (
        data_dir
        / "backups"
        / (
            datetime.now(timezone.utc).strftime("meridian-%Y%m%d-%H%M%S-")
            + base64.urlsafe_b64encode(os.urandom(4)).decode().rstrip("=")
            + ".meridian"
        )
    )
    destination.write_bytes(MAGIC + salt + nonce + ciphertext)
    destination.chmod(0o600)
    return destination


def restore_backup(db, data_dir: Path, raw: bytes, password: str):
    if not raw.startswith(MAGIC) or len(raw) > MAX_BACKUP_BYTES:
        raise ValueError("Invalid or oversized Meridian backup")
    position = len(MAGIC)
    salt, nonce, ciphertext = raw[position : position + 16], raw[position + 16 : position + 28], raw[position + 28 :]
    try:
        decrypted = AESGCM(derive(password, salt)).decrypt(nonce, ciphertext, MAGIC)
    except Exception as exc:
        raise ValueError("The backup password is incorrect or the file is damaged") from exc
    with tempfile.TemporaryDirectory(dir=data_dir / "cache") as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
            total = 0
            for entry in archive.infolist():
                path = Path(entry.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or (path.parts[0] not in ("database", "documents", "manifest.json"))
                ):
                    raise ValueError("Unsafe path in backup")
                total += entry.file_size
                if total > MAX_BACKUP_BYTES:
                    raise ValueError("Decompressed backup exceeds the size limit")
                destination = staging / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not entry.is_dir():
                    destination.write_bytes(archive.read(entry))
        snapshot = staging / "database/meridian.sqlite3"
        if not snapshot.is_file():
            raise ValueError("Backup database is missing")
        with sqlite3.connect(snapshot) as restored:
            if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup database integrity check failed")
            required = {"applications", "facts", "document_versions", "settings", "alembic_version"}
            tables = {x[0] for x in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required.issubset(tables):
                raise ValueError("This is not a compatible Meridian database")
            for version_id, old_path in restored.execute("SELECT id,path FROM document_versions").fetchall():
                parts = Path(old_path).parts
                if "documents" not in parts:
                    raise ValueError("Invalid document path in backup database")
                relative = Path(*parts[parts.index("documents") + 1 :])
                if ".." in relative.parts or not (staging / "documents" / relative).is_file():
                    raise ValueError("Referenced document is missing from backup")
                restored.execute(
                    "UPDATE document_versions SET path=? WHERE id=?",
                    (str(data_dir / "documents" / relative), version_id),
                )
            restored.execute("UPDATE integrations SET status='disconnected'")
            restored.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('automation_paused','true')")
        # Immutable filenames let existing files coexist; copy before database replacement.
        for file in (staging / "documents").rglob("*"):
            if file.is_file():
                target = data_dir / "documents" / file.relative_to(staging / "documents")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
        with sqlite3.connect(snapshot) as source, sqlite3.connect(db.path) as target:
            source.backup(target)
    return {
        "restored": True,
        "message": "Backup restored. Automation is paused; reconnect integrations and review interrupted applications.",
    }
