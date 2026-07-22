import subprocess
from pathlib import Path

from trackstage import dbwriter


def test_rekordbox_running_true(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a, 0,
            stdout="rekordbox.exe   1234 Console", stderr=""))
    assert dbwriter.rekordbox_running() is True


def test_rekordbox_running_false(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a, 0,
            stdout="INFO: No tasks are running", stderr=""))
    assert dbwriter.rekordbox_running() is False


def test_rekordbox_running_subprocess_error_returns_false(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("tasklist.exe not found")
    monkeypatch.setattr(subprocess, "run", boom)
    assert dbwriter.rekordbox_running() is False


def test_backup_and_restore(tmp_path):
    db = tmp_path / "master.db"
    db.write_bytes(b"ORIGINAL")
    bak = dbwriter.backup_db(db)
    assert bak.exists()
    assert bak.read_bytes() == b"ORIGINAL"
    db.write_bytes(b"CORRUPTED")
    dbwriter.restore_db(bak, db)
    assert db.read_bytes() == b"ORIGINAL"
