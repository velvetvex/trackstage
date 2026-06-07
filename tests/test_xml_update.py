"""Tests for XML update logic — cue points, location encoding, tag preservation."""

import xml.etree.ElementTree as ET
import pytest
from pathlib import Path

from trackstage.pipeline import (
    to_rb_location, sanitize_xml, _build_comment,
    load_or_bootstrap_xml, _save_xml,
)
from trackstage.cue_detection import CUE_COLORS


class TestRbLocation:
    def test_wsl_path_to_windows(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/test.flac")
        loc = to_rb_location(p)
        assert loc.startswith("file://localhost/C:/")
        assert "test.flac" in loc

    def test_spaces_encoded(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/My Library/test file.flac")
        loc = to_rb_location(p)
        assert "%20" in loc or " " not in loc.split("localhost")[1]

    def test_special_chars_encoded(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/Artist [Label CAT#123]/track.flac")
        loc = to_rb_location(p)
        assert "file://localhost/" in loc

    def test_unicode_in_path(self):
        """Japanese/special chars in path must not crash."""
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/義理 EP/track.flac")
        loc = to_rb_location(p)
        assert "file://localhost/" in loc

    def test_non_wsl_path(self):
        """Non /mnt/ path should still produce valid URI."""
        p = Path("/home/user/music/track.flac")
        loc = to_rb_location(p)
        assert loc.startswith("file://localhost/")


class TestSanitizeXml:
    def test_strips_control_chars(self):
        assert sanitize_xml("hello\x00world") == "helloworld"
        assert sanitize_xml("test\x0b\x0c") == "test"

    def test_preserves_normal_text(self):
        assert sanitize_xml("Normal Text 123") == "Normal Text 123"

    def test_preserves_unicode(self):
        assert sanitize_xml("Küss die Hand") == "Küss die Hand"


class TestBuildComment:
    def test_full_meta(self):
        meta = {
            "styles": "Techno, Acid",
            "catno": "MORD030",
            "energy": "8",
            "danceability": "7",
            "vibes": "dark, driving",
            "vocal": "instrumental",
        }
        c = _build_comment(meta)
        assert "Techno, Acid" in c
        assert "Cat# MORD030" in c
        assert "Energy: 8/10" in c
        assert "Dance: 7/10" in c
        assert "dark, driving" in c
        assert "instrumental" in c

    def test_empty_meta(self):
        c = _build_comment({})
        assert c == ""

    def test_partial_meta(self):
        c = _build_comment({"energy": "5"})
        assert "Energy: 5/10" in c
        assert "Cat#" not in c


class TestCueXmlFormat:
    """Verify cue point XML attributes match Rekordbox format."""

    def test_cue_attrs_complete(self):
        cue = {"name": "Drop", "time": 45.123, "type": "drop"}
        colors = CUE_COLORS.get(cue["type"], {})
        attrs = {
            "Name": cue["name"],
            "Type": "0",
            "Start": str(cue["time"]),
            "Num": "-1",
        }
        attrs.update(colors)
        el = ET.Element("POSITION_MARK", **attrs)
        assert el.get("Name") == "Drop"
        assert el.get("Type") == "0"
        assert el.get("Start") == "45.123"
        assert el.get("Red") is not None

    def test_all_cue_types_produce_valid_xml(self):
        for cue_type in CUE_COLORS:
            colors = CUE_COLORS[cue_type]
            attrs = {"Name": "Test", "Type": "0", "Start": "1.0", "Num": "-1"}
            attrs.update(colors)
            el = ET.Element("POSITION_MARK", **attrs)
            # Verify XML serialization doesn't crash
            ET.tostring(el)


class TestBootstrapXml:
    def test_bootstrap_new_xml(self, tmp_path):
        xml_path = tmp_path / "test.xml"
        tree, root, max_id = load_or_bootstrap_xml(xml_path)
        assert root.tag == "DJ_PLAYLISTS"
        assert root.find("COLLECTION") is not None
        assert root.find("PLAYLISTS") is not None
        assert max_id == 0

    def test_save_and_reload(self, tmp_path):
        xml_path = tmp_path / "test.xml"
        tree, root, _ = load_or_bootstrap_xml(xml_path)

        # Add a track
        collection = root.find("COLLECTION")
        ET.SubElement(collection, "TRACK", TrackID="1", Name="Test", Location="file://test")
        collection.set("Entries", "1")
        _save_xml(tree, xml_path)

        # Reload
        tree2, root2, max_id2 = load_or_bootstrap_xml(xml_path)
        assert max_id2 == 1
        tracks = root2.findall(".//COLLECTION/TRACK")
        assert len(tracks) == 1
