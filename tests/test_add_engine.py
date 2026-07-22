import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trackstage import add
from trackstage.sourcer import Candidate


@pytest.fixture
def wired(monkeypatch, tmp_path):
    # Environment
    inbox = tmp_path / "inbox"; inbox.mkdir()
    library = tmp_path / "library"; library.mkdir()
    monkeypatch.setenv("INBOX_PATH", str(inbox))
    monkeypatch.setenv("LIBRARY_PATH", str(library))
    monkeypatch.setenv("DISCOGS_TOKEN", "tok")
    monkeypatch.setenv("SLSKD_API_KEY", "key")

    # Sourcer: one FLAC candidate; download writes a fake file into inbox
    cand = Candidate("peer", "Soulwax - E Talking.flac", 40_000_000,
                     None, "flac", True, 0)
    dl_path = inbox / "Soulwax - E Talking.flac"

    class FakeSlskd:
        def __init__(self, *a, **k): pass
        def search(self, q, timeout=30.0):
            return [{"username": "peer",
                     "filename": "Soulwax - E Talking.flac",
                     "size": 40_000_000, "bitRate": None,
                     "freeUploadSlots": True, "queueLength": 0}]
        def download(self, c, wait=True, timeout=600.0):
            dl_path.write_bytes(b"FLACDATA")
            return dl_path

    monkeypatch.setattr(add, "SlskdClient", FakeSlskd)

    # Discogs
    class FakeDiscogs:
        def __init__(self, *a, **k): pass
        def verify(self): return True
    monkeypatch.setattr(add, "DiscogsClient", FakeDiscogs)
    monkeypatch.setattr(add, "identify", lambda *a, **k: (
        {"styles": "Electro", "label": "PIAS", "album": "Any Minute Now",
         "year": "2004", "genre": "Electronic", "catno": "X1",
         "release_title": "Any Minute Now"}, 92))

    # Analyzer (+ cache)
    monkeypatch.setattr(add, "analyze_track", lambda fp, existing_key="": {
        "bpm": "128.8", "camelot": "6A", "energy": "5",
        "danceability": "6", "moods": ["party"], "vibes": ["driving"],
        "vocal": "instrumental", "cues": [], "loudness": None})

    class FakeCache:
        def __init__(self, *a, **k): pass
        def get(self, fp): return None
        def put(self, fp, r): pass
        def close(self): pass
    monkeypatch.setattr(add, "AnalysisCache", FakeCache)

    # Windows-path conversion: the real converter only rewrites /mnt/c paths;
    # the tmp library lives under /tmp, so simulate the mount deterministically.
    monkeypatch.setattr(add, "to_rb_windows_path",
                        lambda p: "C:/Users/Kaitlyn/Music/Library/" + Path(p).name)

    # dbwriter guards + writer + DB
    monkeypatch.setattr(add, "rekordbox_running", lambda: False)
    monkeypatch.setattr(add, "backup_db", lambda p: Path(str(p) + ".bak"))
    monkeypatch.setattr(add, "restore_db", lambda b, p: None)

    written = {}

    class FakeWriter:
        def __init__(self, db): pass
        def add_track(self, **kw):
            written.update(kw)
            return "CID123"
    monkeypatch.setattr(add, "RekordboxWriter", FakeWriter)

    class FakeDB:
        def __init__(self, *a, **k): pass
        def commit(self): written["committed"] = True
    monkeypatch.setattr(add, "Rekordbox6Database", FakeDB)

    return SimpleNamespace(inbox=inbox, library=library, written=written,
                           dl_path=dl_path)


def test_happy_path_writes_and_commits(wired, capsys):
    args = add.build_parser().parse_args(["E Talking by Soulwax", "--yes"])
    rc = add.run_add(args)
    assert rc == 0
    assert wired.written["committed"] is True
    assert wired.written["title"]  # add_track received metadata
    assert wired.written["win_path"].startswith("C:/")


def test_dry_run_no_commit(wired):
    args = add.build_parser().parse_args(["E Talking by Soulwax",
                                          "--yes", "--dry-run"])
    rc = add.run_add(args)
    assert rc == 0
    assert "committed" not in wired.written


def test_refuses_when_rekordbox_running(wired, monkeypatch):
    monkeypatch.setattr(add, "rekordbox_running", lambda: True)
    args = add.build_parser().parse_args(["E Talking by Soulwax", "--yes"])
    rc = add.run_add(args)
    assert rc == 3
    assert "committed" not in wired.written
