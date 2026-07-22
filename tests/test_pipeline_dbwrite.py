from pathlib import Path
from types import SimpleNamespace

import pytest

from trackstage import pipeline


def test_write_results_to_db_routes_each_track(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "rekordbox_running", lambda: False)
    monkeypatch.setattr(pipeline, "backup_db", lambda p: Path(str(p) + ".bak"))
    monkeypatch.setattr(pipeline, "restore_db", lambda b, p: None)

    calls = []

    class FakeWriter:
        def __init__(self, db): pass
        def add_track(self, **kw):
            calls.append(kw["title"])
            return "CID"

    class FakeDB:
        def __init__(self, *a, **k): pass
        def commit(self): calls.append("commit")

    monkeypatch.setattr(pipeline, "RekordboxWriter", FakeWriter)
    monkeypatch.setattr(pipeline, "Rekordbox6Database", FakeDB)
    monkeypatch.setenv("REKORDBOX_DB", str(tmp_path / "master.db"))
    (tmp_path / "master.db").write_bytes(b"DB")

    f = tmp_path / "Artist - T.flac"; f.write_bytes(b"x")
    results = [{"file_path": f, "artist": "Artist", "title": "T",
                "meta": {"styles": "House", "bpm": "120", "energy": "5",
                         "camelot": "8A", "vibes": "", "vocal": ""}}]
    out = pipeline.write_results_to_db(results, dry_run=False)
    assert out["written"] == 1
    assert "T" in calls
    assert "commit" in calls


def test_write_results_to_db_dry_run_no_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "rekordbox_running", lambda: False)
    out = pipeline.write_results_to_db([], dry_run=True)
    assert out == {"written": 0, "skipped": 0}
