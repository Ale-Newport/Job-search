"""A backup never carries what the purge deletes: the DELETEs of ``export_backup`` leave their cells in the file's free pages
unless the snapshot is vacuumed (SEC-08 / BK-01). These tests read the BYTES of the archived database, not the rows."""
import io
import sqlite3
import zipfile

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jobagent.backup import MAGIC, derive, export_backup, restore_backup
from jobagent.db import Database

PASSWORD = "long-secure-test-password"
TOKEN_HASH = "a1b2c3d4" * 8  # the 64-hex sha256 of a paired browser's secret: also the key of the hello proof
SENTINELS = (TOKEN_HASH.encode(), b"SENTINELURL", b"SENTINELCOMPANY", b"SENTINELTITLE", b"SENTINELPIN", b"SENTINELEVENT",
             b"SENTINELSERVICE", b"SENTINELACCOUNT", b"sentinel-employer.example", b"chrome-extension://sentinelorigin",
             b"SENTINELPAIRNAME")


@pytest.fixture
def workspace(tmp_path):
    for child in ("database", "cache", "documents", "backups"):
        (tmp_path / child).mkdir()
    db = Database(tmp_path / "database/meridian.sqlite3")
    url = "https://sentinel-employer.example/jobs/SENTINELURL?x=1"
    db.execute("INSERT INTO extension_clients(id,name,browser,origin,token_hash,created_at) VALUES(?,?,?,?,?,?)",
               ("c1", "n", "chrome", "chrome-extension://sentinelorigin00", TOKEN_HASH, "t"))
    db.execute("INSERT INTO extension_pairings(id,code,client_name,browser,origin,state,created_at,expires_at) "
               "VALUES('p1','123456','SENTINELPAIRNAME','chrome','chrome-extension://sentinelorigin00','denied','t','t')")
    db.execute("INSERT INTO extension_sessions(id,client_id,url,canonical_url,company,title,metadata,created_at,"
               "updated_at) VALUES('s1','c1',?,?,'SENTINELCOMPANY','SENTINELTITLE',?,'t','t')",
               (url, url, '{"user":{"company":"SENTINELPIN"}}'))
    db.execute("INSERT INTO extension_events(id,session_id,kind,details,created_at) VALUES('e1','s1','edit',?,'t')",
               ('{"saved":true,"note":"SENTINELEVENT"}',))
    db.execute("INSERT INTO secret_references(id,provider,keychain_service,account,created_at) VALUES(?,?,?,?,?)",
               ("sr1", "openai", "SENTINELSERVICE", "SENTINELACCOUNT", "t"))
    return tmp_path, db


def archived_database(path, password=PASSWORD) -> bytes:
    """What a holder of the backup and its password gets: the bytes of the archived SQLite file, decrypted."""
    raw = path.read_bytes()
    position = len(MAGIC)
    salt, nonce, ciphertext = raw[position:position + 16], raw[position + 16:position + 28], raw[position + 28:]
    plain = AESGCM(derive(password, salt)).decrypt(nonce, ciphertext, MAGIC)
    return zipfile.ZipFile(io.BytesIO(plain)).read("database/meridian.sqlite3")


def test_the_archived_database_holds_no_trace_of_the_purged_rows(workspace):
    root, db = workspace
    data = archived_database(export_backup(db, root, PASSWORD))
    leaked = [s.decode() for s in SENTINELS if s in data]
    assert leaked == []  # the rows are gone from the tables AND from the free pages


def test_the_purged_tables_are_empty_in_the_archived_database_and_the_rest_survives(workspace, tmp_path_factory):
    root, db = workspace
    db.execute("INSERT INTO settings VALUES('theme','\"dark\"') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    data = archived_database(export_backup(db, root, PASSWORD))
    copy = tmp_path_factory.mktemp("snapshot") / "snapshot.sqlite3"
    copy.write_bytes(data)
    check = sqlite3.connect(copy)
    try:
        counts = {name: check.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in (
            "extension_clients", "extension_sessions", "extension_events", "extension_pairings", "secret_references")}
        assert counts == dict.fromkeys(counts, 0)
        assert check.execute("SELECT value FROM settings WHERE key='theme'").fetchone()[0] == '"dark"'
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("PRAGMA freelist_count").fetchone()[0] == 0  # nothing deleted is left in a free page
    finally:
        check.close()


def test_the_live_database_is_not_touched_by_an_export(workspace):
    root, db = workspace
    export_backup(db, root, PASSWORD)
    assert db.one("SELECT token_hash FROM extension_clients WHERE id='c1'")["token_hash"] == TOKEN_HASH
    assert db.one("SELECT COUNT(*) AS n FROM extension_events")["n"] == 1


def test_a_backup_still_restores(workspace):
    root, db = workspace
    db.execute("INSERT INTO settings VALUES('theme','\"dark\"') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    backup = export_backup(db, root, PASSWORD)
    db.execute("UPDATE settings SET value='\"light\"' WHERE key='theme'")
    restore_backup(db, root, backup.read_bytes(), PASSWORD)
    assert db.one("SELECT value FROM settings WHERE key='theme'")["value"] == '"dark"'
    assert db.query("SELECT * FROM extension_clients") == []
