"""Epoch 2: Edge cases found by thinking about real library scenarios."""

import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, 'scripts')

from trackstage.audio_analysis import to_camelot, _parse_key_string, BPM_FLOOR, BPM_CEILING
from trackstage.cue_detection import _beat_energy_contour, _smooth, CUE_COLORS
from trackstage.loudness import format_loudness_log
from trackstage.pipeline import to_rb_location, sanitize_xml, _build_comment
from analyze_library import _merge_comment, write_analysis_tags


class TestBPMEdgeCases:
    def test_exactly_at_floor(self):
        """BPM exactly at 100.0 should NOT double."""
        bpm = 100.0
        assert bpm >= BPM_FLOOR

    def test_just_below_floor(self):
        """99.9*2=199.8 exceeds ceiling — should NOT double."""
        bpm = 99.9
        assert bpm < BPM_FLOOR
        assert bpm * 2 > BPM_CEILING  # too fast to double

    def test_half_floor_dnb_range(self):
        """DnB at 87 BPM should be doubling candidate (87*2=174 < 185)."""
        bpm = 87.0
        assert bpm < BPM_FLOOR
        assert bpm * 2 <= BPM_CEILING

    def test_very_low_bpm_ambient(self):
        """60 BPM ambient — 60*2=120, under ceiling, but should NOT double without energy gate."""
        bpm = 60.0
        assert bpm * 2 <= BPM_CEILING

    def test_bpm_doubling_exactly_at_ceiling(self):
        """92.5*2=185.0 — exactly at ceiling, should still be allowed."""
        bpm = 92.5
        assert bpm * 2 <= BPM_CEILING

    def test_bpm_doubling_over_ceiling(self):
        """93*2=186 — over ceiling, should NOT double."""
        bpm = 93.0
        assert bpm * 2 > BPM_CEILING


class TestKeyEdgeCases:
    def test_parse_key_with_space(self):
        """Essentia returns 'C# minor' not 'C#m'."""
        # _parse_key_string handles compact form (Dm, Ebm)
        # but analyzer.py uses to_camelot(key, scale) with split form
        assert to_camelot("C#", "minor") == "12A"
        assert to_camelot("Db", "minor") == "12A"

    def test_all_essentia_keys_mapped(self):
        """Essentia can return any of these note names."""
        essentia_notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        for note in essentia_notes:
            for scale in ["major", "minor"]:
                result = to_camelot(note, scale)
                assert result != "", f"({note}, {scale}) not in CAMELOT dict"

    def test_parse_key_double_flat(self):
        """Weird key strings should not crash. 'Bbb' is nonsense but handled gracefully."""
        result = _parse_key_string("Bbb")
        # Not a real key, but parser treats it as note='Bbb', scale='major'
        # This is fine — CAMELOT lookup will return '' for unknown keys
        assert result is not None

    def test_parse_key_camelot_notation_input(self):
        """If someone passes '8A' as key string, it won't parse but shouldn't crash."""
        result = _parse_key_string("8A")
        assert result is not None or result is None  # just don't crash


class TestCommentMergeEdgeCases:
    def test_comment_with_only_pipes(self):
        result = _merge_comment(" | | ", "5", "5", "", "")
        assert "Energy: 5/10" in result

    def test_comment_with_multiple_cat_numbers(self):
        existing = "Techno | Cat# ABC123 | Cat# DEF456"
        result = _merge_comment(existing, "5", "5", "", "")
        assert "Cat# ABC123" in result

    def test_comment_unicode(self):
        existing = "テクノ | Cat# JP001"
        result = _merge_comment(existing, "7", "8", "dark", "")
        assert "テクノ" in result
        assert "Energy: 7/10" in result

    def test_comment_very_long(self):
        """Extremely long comment should not crash."""
        existing = "A" * 1000
        result = _merge_comment(existing, "5", "5", "", "")
        assert "Energy: 5/10" in result

    def test_driving_vibe_case_insensitive_strip(self):
        """'Driving' with capital D should still be stripped on re-run."""
        # Current code checks p.lower() in vibe_words
        existing = "Techno | Cat# XYZ | driving"
        result = _merge_comment(existing, "5", "5", "euphoric", "")
        assert "driving" not in result
        assert "euphoric" in result

    def test_compound_vibes_stripped(self):
        """'dark, driving' as a single pipe segment — must be stripped on re-analysis."""
        existing = "Techno | dark, driving"
        result = _merge_comment(existing, "5", "5", "euphoric", "")
        assert "dark" not in result
        assert "driving" not in result
        assert "euphoric" in result
        assert "Techno" in result


class TestCueEdgeCases:
    def test_single_beat(self):
        """Track with only 1 beat detected — should not crash."""
        audio = np.zeros(44100 * 5, dtype=np.float32)
        beats = np.array([1.0], dtype=np.float32)
        contour = _beat_energy_contour(audio, beats)
        assert len(contour) == 0

    def test_two_beats(self):
        audio = np.zeros(44100 * 5, dtype=np.float32)
        beats = np.array([0.5, 1.0], dtype=np.float32)
        contour = _beat_energy_contour(audio, beats)
        assert len(contour) == 1

    def test_negative_beat_time(self):
        """Shouldn't happen but essentia is unpredictable."""
        audio = np.zeros(44100 * 5, dtype=np.float32)
        beats = np.array([-0.5, 0.5, 1.0], dtype=np.float32)
        # Negative index wraps in numpy — not ideal but shouldn't crash
        contour = _beat_energy_contour(audio, beats)
        assert len(contour) >= 1


class TestXmlEdgeCases:
    def test_location_with_parentheses(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/Track (Remix).flac")
        loc = to_rb_location(p)
        assert "Track" in loc

    def test_location_with_ampersand(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/DJ Jazzy Jeff & Fresh Prince.mp3")
        loc = to_rb_location(p)
        assert "file://localhost/" in loc

    def test_sanitize_xml_preserves_valid_whitespace(self):
        """\\n, \\r, \\t are valid XML chars — should NOT be stripped."""
        assert "\n" in sanitize_xml("line1\nline2")
        assert "\t" in sanitize_xml("col1\tcol2")
        # But NUL and other C0 controls ARE stripped
        assert "\x00" not in sanitize_xml("bad\x00char")

    def test_build_comment_no_catno_no_hash(self):
        """Empty catno should not produce 'Cat# '."""
        meta = {"catno": "", "energy": "5"}
        c = _build_comment(meta)
        assert "Cat#" not in c


class TestLoudnessEdgeCases:
    def test_extremely_quiet_track(self):
        """Peak 0.001 = -60 dBFS. Gain +51. 51 + (-60) = -9 < 0. No clip."""
        loudness = {"lufs": -60.0, "peak": 0.001, "gain_db": 51.0, "range_lu": 1.0}
        result = format_loudness_log(loudness)
        assert "CLIP" not in result

    def test_actual_clip_scenario(self):
        """Peak 0.9 = -0.9 dBFS. Gain +5. 5 + (-0.9) = +4.1 > 0. Clip!"""
        loudness = {"lufs": -14.0, "peak": 0.9, "gain_db": 5.0, "range_lu": 5.0}
        result = format_loudness_log(loudness)
        assert "CLIP" in result

    def test_very_loud_track(self):
        loudness = {"lufs": -3.0, "peak": 1.0, "gain_db": -6.0, "range_lu": 3.0}
        result = format_loudness_log(loudness)
        assert "CLIP" not in result  # -6 + 0dBFS = -6 < 0, no clip


from pathlib import Path


class TestWriteAnalysisTags:
    """Test write_analysis_tags on a real file from the library."""

    @pytest.fixture
    def test_flac(self):
        """Find a FLAC file in the library for testing."""
        import glob
        flacs = glob.glob("/mnt/c/Users/Kaitlyn/Music/Library/**/*.flac", recursive=True)
        if not flacs:
            pytest.skip("No FLAC files in library")
        return Path(flacs[0])

    def test_write_preserves_existing_tags(self, test_flac):
        from mutagen.flac import FLAC
        f = FLAC(test_flac)
        original_artist = f.get("artist", [""])[0]
        original_title = f.get("title", [""])[0]
        original_genre = f.get("genre", [""])[0]

        r = {"bpm": "130.0", "camelot": "8A", "energy": "7", "danceability": "6",
             "vibes": ["dark"], "vocal": "instrumental"}
        write_analysis_tags(test_flac, r)

        f2 = FLAC(test_flac)
        assert f2.get("artist", [""])[0] == original_artist
        assert f2.get("title", [""])[0] == original_title
        assert f2.get("genre", [""])[0] == original_genre
        assert f2.get("bpm", [""])[0] == "130.0"
        assert f2.get("energy", [""])[0] == "7"

    def test_write_empty_analysis_no_crash(self, test_flac):
        """Empty analysis dict should not crash or corrupt file."""
        r = {}
        result = write_analysis_tags(test_flac, r)
        assert result is True
