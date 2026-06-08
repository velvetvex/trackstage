"""Tests for trackstage.xml — the single source of truth for Rekordbox XML ops."""

import xml.etree.ElementTree as ET
import pytest
from pathlib import Path

from trackstage.xml import (
    to_rb_location,
    to_rb_windows_path,
    sanitize_xml,
    load_or_bootstrap_xml,
    save_xml,
    update_xml_track,
    ENERGY_TO_RATING,
    MOOD_TO_COLOUR,
)


class TestToRbLocation:
    def test_wsl_path_converts(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/track.flac")
        loc = to_rb_location(p)
        assert loc.startswith("file://localhost/C:/")
        assert "track.flac" in loc

    def test_spaces_encoded(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/My Library/test file.flac")
        loc = to_rb_location(p)
        # Spaces should be percent-encoded
        assert "%20" in loc
        assert "My%20Library" in loc
        assert "test%20file.flac" in loc

    def test_non_wsl_path(self):
        p = Path("/home/user/music/track.mp3")
        loc = to_rb_location(p)
        assert loc.startswith("file://localhost/")
        assert "track.mp3" in loc


class TestToRbWindowsPath:
    def test_wsl_converts(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/track.flac")
        result = to_rb_windows_path(p)
        assert result == "C:/Users/Kaitlyn/Music/track.flac"

    def test_no_url_encoding(self):
        p = Path("/mnt/d/My Music/Artist [Label]/track file.flac")
        result = to_rb_windows_path(p)
        assert result == "D:/My Music/Artist [Label]/track file.flac"
        # No percent encoding
        assert "%" not in result

    def test_non_wsl_unchanged(self):
        p = Path("/home/user/music/track.mp3")
        result = to_rb_windows_path(p)
        assert result == "/home/user/music/track.mp3"


class TestSanitizeXml:
    def test_strips_null(self):
        assert sanitize_xml("hello\x00world") == "helloworld"

    def test_strips_control_chars(self):
        assert sanitize_xml("a\x01b\x08c\x0bd\x0ce\x1f") == "abcde"

    def test_preserves_normal(self):
        text = "Normal Text 123 !@# with unicode"
        assert sanitize_xml(text) == text

    def test_preserves_newline_tab(self):
        # \n (0x0a) and \t (0x09) and \r (0x0d) should NOT be stripped
        assert sanitize_xml("line\ttab") == "line\ttab"
        assert sanitize_xml("line\nnewline") == "line\nnewline"


class TestLoadOrBootstrapXml:
    def test_creates_structure(self, tmp_path):
        xml_path = tmp_path / "test.xml"
        tree, root, max_id = load_or_bootstrap_xml(xml_path)

        assert max_id == 0
        assert root.tag == "DJ_PLAYLISTS"
        assert root.get("Version") == "1.0.0"

        # PRODUCT
        product = root.find("PRODUCT")
        assert product is not None
        assert product.get("Name") == "rekordbox"

        # COLLECTION
        collection = root.find("COLLECTION")
        assert collection is not None
        assert collection.get("Entries") == "0"

        # PLAYLISTS/NODE[@Name='ROOT']
        playlists = root.find("PLAYLISTS")
        assert playlists is not None
        root_node = playlists.find("NODE[@Name='ROOT']")
        assert root_node is not None
        assert root_node.get("Type") == "0"

    def test_reads_existing_and_returns_max_id(self, tmp_path):
        xml_path = tmp_path / "existing.xml"

        # Create a valid XML with tracks
        root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
        ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="7.0.0",
                      Company="AlphaTheta")
        collection = ET.SubElement(root, "COLLECTION", Entries="3")
        ET.SubElement(collection, "TRACK", TrackID="1", Name="Track 1",
                      Location="file://localhost/C:/track1.flac")
        ET.SubElement(collection, "TRACK", TrackID="5", Name="Track 2",
                      Location="file://localhost/C:/track2.flac")
        ET.SubElement(collection, "TRACK", TrackID="42", Name="Track 3",
                      Location="file://localhost/C:/track3.flac")
        pl = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="0")

        tree = ET.ElementTree(root)
        tree.write(xml_path, encoding="utf-8", xml_declaration=True)

        # Now load it
        loaded_tree, loaded_root, max_id = load_or_bootstrap_xml(xml_path)
        assert max_id == 42
        assert loaded_root.tag == "DJ_PLAYLISTS"
        tracks = loaded_root.findall(".//COLLECTION/TRACK")
        assert len(tracks) == 3

    def test_save_and_reload(self, tmp_path):
        xml_path = tmp_path / "roundtrip.xml"
        tree, root, _ = load_or_bootstrap_xml(xml_path)

        # Add a track
        collection = root.find("COLLECTION")
        ET.SubElement(collection, "TRACK", TrackID="1", Name="Test Track")
        collection.set("Entries", "1")

        save_xml(tree, xml_path)

        # Reload
        tree2, root2, max_id = load_or_bootstrap_xml(xml_path)
        assert max_id == 1
        tracks = root2.findall(".//COLLECTION/TRACK")
        assert len(tracks) == 1
        assert tracks[0].get("Name") == "Test Track"


class TestUpdateXmlTrack:
    def _make_track_el(self, **attrs):
        defaults = {
            "TrackID": "1",
            "Name": "Test Track",
            "Artist": "Test Artist",
            "AverageBpm": "0.00",
            "Tonality": "",
            "Grouping": "",
            "Comments": "Techno, Acid | Cat# TEST001",
            "Rating": "0",
            "Colour": "",
            "Location": "file://localhost/C:/test.flac",
        }
        defaults.update(attrs)
        return ET.Element("TRACK", **defaults)

    def test_sets_bpm(self):
        track_el = self._make_track_el()
        r = {"bpm": "128.00"}
        changed = update_xml_track(track_el, r)
        assert changed is True
        assert track_el.get("AverageBpm") == "128.00"

    def test_sets_key(self):
        track_el = self._make_track_el()
        r = {"camelot": "8A"}
        changed = update_xml_track(track_el, r)
        assert changed is True
        assert track_el.get("Tonality") == "8A"

    def test_sets_grouping(self):
        track_el = self._make_track_el()
        r = {"vibes": ["dark", "driving"], "moods": ["aggressive"]}
        changed = update_xml_track(track_el, r)
        assert changed is True
        assert track_el.get("Grouping") == "dark, driving, aggressive"

    def test_merges_comments(self):
        track_el = self._make_track_el(Comments="Techno, Acid | Cat# TEST001")
        r = {"energy": "7", "danceability": "8", "vibes": ["deep"], "vocal": "instrumental"}
        changed = update_xml_track(track_el, r)
        assert changed is True
        comments = track_el.get("Comments")
        assert "Techno, Acid" in comments
        assert "Cat# TEST001" in comments
        assert "Energy: 7/10" in comments
        assert "Dance: 8/10" in comments
        assert "deep" in comments
        assert "instrumental" in comments

    def test_sets_rating_from_energy(self):
        track_el = self._make_track_el()
        r = {"energy": "7"}
        changed = update_xml_track(track_el, r)
        assert changed is True
        assert track_el.get("Rating") == "204"

    def test_rating_mapping_extremes(self):
        # Low energy
        track_el = self._make_track_el()
        update_xml_track(track_el, {"energy": "1"})
        assert track_el.get("Rating") == "51"

        # High energy
        track_el = self._make_track_el()
        update_xml_track(track_el, {"energy": "10"})
        assert track_el.get("Rating") == "255"

    def test_sets_colour_from_mood(self):
        track_el = self._make_track_el()
        r = {"moods": ["happy", "party"]}
        changed = update_xml_track(track_el, r)
        assert changed is True
        # happy has higher priority than party in MOOD_PRIORITY
        assert track_el.get("Colour") == "0xFFA500"

    def test_colour_priority_aggressive_first(self):
        track_el = self._make_track_el()
        r = {"moods": ["happy", "aggressive"]}
        update_xml_track(track_el, r)
        # aggressive is first in MOOD_PRIORITY
        assert track_el.get("Colour") == "0xFF0000"

    def test_sets_cue_points(self):
        track_el = self._make_track_el()
        # Add existing cue to verify replacement
        ET.SubElement(track_el, "POSITION_MARK", Name="old", Type="0", Start="0")

        r = {
            "cues": [
                {"name": "Mix In", "type": "mix_in", "time": 1.234},
                {"name": "Drop 1", "type": "drop", "time": 64.5},
            ]
        }
        changed = update_xml_track(track_el, r)
        assert changed is True

        cues = track_el.findall("POSITION_MARK")
        assert len(cues) == 2  # old one removed, 2 new ones added
        assert cues[0].get("Name") == "Mix In"
        assert cues[0].get("Start") == "1.234"
        assert cues[0].get("Type") == "0"
        assert cues[0].get("Num") == "-1"
        # Check color attrs from CUE_COLORS
        assert cues[0].get("Green") == "226"  # cyan for mix_in

        assert cues[1].get("Name") == "Drop 1"
        assert cues[1].get("Start") == "64.5"
        assert cues[1].get("Red") == "232"  # red for drop

    def test_all_fields_together(self):
        track_el = self._make_track_el(Comments="Deep House | Cat# DH001")
        r = {
            "bpm": "124.00",
            "camelot": "5B",
            "energy": "6",
            "danceability": "7",
            "vibes": ["deep", "euphoric"],
            "moods": ["happy", "relaxed"],
            "vocal": "voice",
            "cues": [
                {"name": "Mix In", "type": "mix_in", "time": 0.5},
                {"name": "Drop", "type": "drop", "time": 32.0},
                {"name": "Mix Out", "type": "mix_out", "time": 300.0},
            ],
        }
        changed = update_xml_track(track_el, r)
        assert changed is True
        assert track_el.get("AverageBpm") == "124.00"
        assert track_el.get("Tonality") == "5B"
        assert track_el.get("Rating") == "153"  # energy 6 -> 153
        assert track_el.get("Colour") == "0xFFA500"  # happy
        assert "deep, euphoric, happy, relaxed" == track_el.get("Grouping")
        assert len(track_el.findall("POSITION_MARK")) == 3

    def test_no_change_returns_false(self):
        track_el = self._make_track_el(Comments="")
        r = {}  # empty result
        changed = update_xml_track(track_el, r)
        assert changed is False
