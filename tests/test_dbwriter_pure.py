from trackstage.dbwriter import (
    content_fields, computed_tag_names, resolve_tag_ids,
)


class TestContentFields:
    def test_bpm_scaled_by_100(self):
        f = content_fields({}, {"bpm": "128.8"})
        assert f["BPM"] == 12880

    def test_bpm_missing_is_none(self):
        f = content_fields({}, {})
        assert f["BPM"] is None

    def test_energy_maps_to_rating(self):
        f = content_fields({}, {"energy": "6"})
        assert f["Rating"] == 3

    def test_mood_maps_to_color(self):
        f = content_fields({}, {"moods": ["party"]})
        assert f["ColorID"] == "4"

    def test_no_mood_color_zero(self):
        f = content_fields({}, {"moods": []})
        assert f["ColorID"] == "0"

    def test_year_from_meta(self):
        f = content_fields({"year": "2004"}, {})
        assert f["ReleaseYear"] == 2004

    def test_camelot_key_passed_through(self):
        f = content_fields({}, {"camelot": "6A"})
        assert f["KeyName"] == "6A"

    def test_comment_built_from_meta(self):
        f = content_fields(
            {"styles": "Electro", "catno": "X1"},
            {"energy": "5"})
        assert "Electro" in f["Commnt"]


class TestComputedTagNames:
    def test_genre_and_vibe_union(self):
        meta = {"styles": "House"}
        analysis = {"energy": "6", "vibes": ["driving"], "moods": []}
        names = computed_tag_names(meta, analysis)
        assert "House" in names
        assert "Driving" in names

    def test_situation_included(self):
        names = computed_tag_names({}, {"energy": "8"})
        assert "Peak" in names  # rekordbox.compute_situation("8")


class TestResolveTagIds:
    def test_case_insensitive_match(self):
        existing = {"house": "111", "driving": "222"}
        ids = resolve_tag_ids(existing, {"House", "Driving"})
        assert set(ids) == {"111", "222"}

    def test_unmatched_skipped(self):
        existing = {"house": "111"}
        ids = resolve_tag_ids(existing, {"House", "Peak"})
        assert ids == ["111"]

    def test_empty(self):
        assert resolve_tag_ids({}, set()) == []
