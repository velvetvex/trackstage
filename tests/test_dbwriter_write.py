from types import SimpleNamespace
from trackstage.dbwriter import RekordboxWriter


class FakeRow(SimpleNamespace):
    pass


class FakeSession:
    def __init__(self, rows):
        self._rows = rows          # list of dicts for SELECT results
        self.executed = []         # (sql, params) for INSERT/UPDATE

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        if "FROM djmdMyTag" in sql:
            return iter([(r["Name"], r["ID"]) for r in self._rows["my_tags"]])
        if "FROM djmdKey" in sql:
            return iter([(r["ID"], r["ScaleName"]) for r in self._rows["keys"]])
        if "FROM djmdContent" in sql:
            return iter(self._rows.get("existing", []))
        if "FROM djmdSongMyTag" in sql:
            return iter(self._rows.get("song_tags", []))
        return iter([])


class FakeDB:
    """Duck-typed stand-in for pyrekordbox Rekordbox6Database."""
    def __init__(self, rows):
        self.session = FakeSession(rows)
        self.added_content = []
        self.playlists = {}        # name -> row
        self.playlist_adds = []    # (playlist_id, content_id)
        self.artists = {}
        self._id = 1000

    def _next(self):
        self._id += 1
        return str(self._id)

    def add_content(self, path, **kw):
        row = FakeRow(ID=self._next(), FolderPath=str(path),
                      FileNameL=None, **kw)
        self.added_content.append(row)
        return row

    def add_artist(self, name, search_str=None):
        r = FakeRow(ID=self._next(), Name=name)
        self.artists[name] = r
        return r

    def add_album(self, name, artist=None, **kw):
        return FakeRow(ID=self._next(), Name=name)

    def add_genre(self, name):
        return FakeRow(ID=self._next(), Name=name)

    def add_label(self, name):
        return FakeRow(ID=self._next(), Name=name)

    def get_artist(self, **kw):
        r = self.artists.get(kw.get("Name"))
        return SimpleNamespace(first=lambda: r)

    def get_album(self, **kw):
        return SimpleNamespace(first=lambda: None)

    def get_genre(self, **kw):
        return SimpleNamespace(first=lambda: None)

    def get_label(self, **kw):
        return SimpleNamespace(first=lambda: None)

    def get_playlist(self, **kw):
        return SimpleNamespace(first=lambda: self.playlists.get(kw.get("Name")))

    def create_playlist_folder(self, name, parent=None, **kw):
        r = FakeRow(ID=self._next(), Name=name)
        self.playlists[name] = r
        return r

    def create_playlist(self, name, parent=None, **kw):
        r = FakeRow(ID=self._next(), Name=name)
        self.playlists[name] = r
        return r

    def get_playlist_songs(self, **kwargs):
        return []

    def add_to_playlist(self, playlist, content, track_no=None):
        self.playlist_adds.append((playlist.ID, content.ID))
        return FakeRow(ID=self._next())


def _rows():
    return {
        "my_tags": [{"Name": "House", "ID": "H1"},
                    {"Name": "Driving", "ID": "D1"}],
        "keys": [{"ScaleName": "6A", "ID": "K6A"}],
        "song_tags": [],
    }


def test_add_track_creates_content_with_windows_path():
    db = FakeDB(_rows())
    w = RekordboxWriter(db)
    cid = w.add_track(
        wsl_path="/mnt/c/Music/Library/x/Soulwax - E Talking.flac",
        win_path="C:/Music/Library/x/Soulwax - E Talking.flac",
        filename="Soulwax - E Talking.flac",
        title="E Talking", artist="Soulwax",
        meta={"styles": "House", "label": "PIAS", "album": "Any Minute Now",
              "year": "2004", "genre": "Electronic", "catno": "X1"},
        analysis={"bpm": "128.8", "energy": "6", "camelot": "6A",
                  "moods": ["party"], "vibes": ["driving"]},
    )
    assert cid == db.added_content[0].ID
    assert db.added_content[0].FolderPath == \
        "C:/Music/Library/x/Soulwax - E Talking.flac"
    assert db.added_content[0].BPM == 12880
    assert db.added_content[0].Rating == 3


def test_my_tags_resolved_and_inserted():
    db = FakeDB(_rows())
    w = RekordboxWriter(db)
    w.add_track(
        wsl_path="/mnt/c/Music/x/a.flac", win_path="C:/Music/x/a.flac",
        filename="a.flac", title="t", artist="a",
        meta={"styles": "House"},
        analysis={"energy": "6", "vibes": ["driving"], "moods": []},
    )
    inserts = [e for e in db.session.executed
               if "INSERT INTO djmdSongMyTag" in e[0]]
    inserted_tag_ids = {e[1]["tag"] for e in inserts}
    assert inserted_tag_ids == {"H1", "D1"}


def test_playlists_ensured_and_joined():
    db = FakeDB(_rows())
    w = RekordboxWriter(db)
    w.add_track(
        wsl_path="/mnt/c/Music/x/a.flac", win_path="C:/Music/x/a.flac",
        filename="a.flac", title="t", artist="a",
        meta={"styles": "House", "label": "PIAS"},
        analysis={"energy": "6", "moods": []},
    )
    assert "House" in db.playlists
    assert "PIAS" in db.playlists
    assert len(db.playlist_adds) == 2


def test_duplicate_path_skips_content_insert():
    db = FakeDB(_rows())

    def raise_dup(path, **kw):
        raise ValueError(f"Track with path '{path}' already exists in database")
    db.add_content = raise_dup
    # Pre-seed the existing-content lookup
    db.session._rows["existing"] = [("EXIST1",)]
    w = RekordboxWriter(db)
    cid = w.add_track(
        wsl_path="/mnt/c/Music/x/a.flac", win_path="C:/Music/x/a.flac",
        filename="a.flac", title="t", artist="a",
        meta={"styles": "House"}, analysis={"energy": "6", "moods": []},
    )
    assert cid == "EXIST1"
