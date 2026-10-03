import pytest

from jobagent.backup import export_backup, restore_backup
from jobagent.db import Database
from jobagent.security import public_url


def test_encrypted_backup_roundtrip_excludes_sessions(tmp_path):
    for child in ("database", "cache", "documents", "backups", "browser-profile"):
        (tmp_path / child).mkdir()
    db = Database(tmp_path / "database/meridian.sqlite3")
    db.execute("INSERT INTO settings VALUES('theme','\"dark\"')")
    (tmp_path / "browser-profile/cookies").write_text("session-secret")
    backup = export_backup(db, tmp_path, "long-secure-test-password")
    raw = backup.read_bytes()
    assert b"SQLite format" not in raw and b"session-secret" not in raw
    db.execute("UPDATE settings SET value='\"light\"' WHERE key='theme'")
    with pytest.raises(ValueError, match="password"):
        restore_backup(db, tmp_path, raw, "incorrect-password")
    restore_backup(db, tmp_path, raw, "long-secure-test-password")
    assert db.one("SELECT value FROM settings WHERE key='theme'")["value"] == '"dark"'
    assert db.one("SELECT value FROM settings WHERE key='automation_paused'")["value"] == "true"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1:8765/api/settings",
        "http://169.254.169.254/",
        "https://username:password@example.com/",
    ],
)
def test_internal_urls_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_explicit_local_fixture_urls():
    assert public_url("http://127.0.0.1:9999/form", allow_local=True).endswith("/form")


def test_old_backup_migrates_before_replacing_current_workspace(tmp_path):
    for child in ("database", "cache", "documents", "backups"):
        (tmp_path / child).mkdir()
    db = Database(tmp_path / "database/meridian.sqlite3")
    db.execute("DROP TABLE notification_receipts")
    db.execute("UPDATE alembic_version SET version_num='0002_fact_history'")
    old_backup = export_backup(db, tmp_path, "old-backup-test-password")
    Database(db.path)
    restore_backup(db, tmp_path, old_backup.read_bytes(), "old-backup-test-password")
    assert db.one("SELECT version_num FROM alembic_version")["version_num"] == "0007_candidate_intelligence"
    assert db.query("SELECT * FROM notification_receipts") == []
