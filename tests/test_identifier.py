from trackstage.identifier import identify


class FakeClient:
    def __init__(self, search_results, releases):
        self._search = search_results
        self._releases = releases
        self.searched = None

    def search(self, q):
        self.searched = q
        return self._search

    def get_release(self, rid):
        return self._releases.get(rid)


def _rel(rid, title, tracklist=None):
    return {"id": rid, "title": title, "genres": ["Electronic"],
            "styles": ["Electro"], "labels": [{"name": "PIAS", "catno": "X1"}],
            "year": 2004, "tracklist": tracklist or []}


def test_forced_discogs_id_skips_search():
    c = FakeClient([], {337822: _rel(337822, "Soulwax - Any Minute Now")})
    meta, conf = identify(c, "Soulwax", "E Talking", discogs_id=337822)
    assert meta["discogs_id"] == "337822"
    assert c.searched is None
    assert conf == 100


def test_high_confidence_first_pass():
    results = [{"id": 1, "title": "Soulwax - E Talking",
                "label": ["PIAS"]}]
    c = FakeClient(results, {1: _rel(1, "Soulwax - E Talking")})
    meta, conf = identify(c, "Soulwax", "E Talking")
    assert meta is not None
    assert conf >= 85


def test_no_results_returns_none():
    c = FakeClient([], {})
    meta, conf = identify(c, "Nobody", "Nothing")
    assert meta is None
    assert conf == 0
