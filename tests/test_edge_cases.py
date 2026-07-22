"""Edge cases found by thinking about real library scenarios.

Re-ported 2026-07-21 after the XML backend was retired. The original file
imported deleted symbols (to_rb_location / sanitize_xml / _build_comment from
the old xml.py path, and _merge_comment / write_analysis_tags from
scripts/analyze_library.py). This version targets the current modules:
comment-merge + tag writing live in trackstage.tags, and the WSL→Windows path
converter is trackstage.rekordbox.to_rb_windows_path (replacing to_rb_location).
"""

import glob
from pathlib import Path

import numpy as np
import pytest

from trackstage.audio_analysis import (
    to_camelot, _parse_key_string, BPM_FLOOR, BPM_CEILING,
)
from trackstage.cue_detection import _beat_energy_contour
from trackstage.loudness import format_loudness_log
from trackstage.rekordbox import to_rb_windows_path
from trackstage.tags import merge_comment, write_analysis_tags


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
        """60 BPM ambient — 60*2=120, under ceiling."""
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
        assert to_camelot("C#", "minor") == "12A"
        assert to_camelot("Db", "minor") == "12A"

    def test_all_essentia_keys_mapped(self):
        """Essentia can return any of these note names."""
        essentia_notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#",
                          "A", "A#", "B"]
        for note in essentia_notes:
            for scale in ["major", "minor"]:
                result = to_camelot(note, scale)
                assert result != "", f"({note}, {scale}) not in CAMELOT dict"

    def test_parse_key_double_flat(self):
        """Weird key strings should not crash."""
        result = _parse_key_string("Bbb")
        assert result is not None

    def test_parse_key_camelot_notation_input(self):
        """If someone passes '8A' as key string, it shouldn't crash."""
        _parse_key_string("8A")  # just don't raise


class TestCommentMergeEdgeCases:
    """Edge cases beyond the happy-path coverage in test_tags.TestMergeComment."""

    def test_comment_with_only_pipes(self):
        result = merge_comment(" | | ", "5", "5", "", "")
        assert "Energy: 5/10" in result

    def test_comment_with_multiple_cat_numbers(self):
        existing = "Techno | Cat# ABC123 | Cat# DEF456"
        result = merge_comment(existing, "5", "5", "", "")
        assert "Cat# ABC123" in result
        assert "Cat# DEF456" in result

    def test_comment_unicode(self):
        existing = "テクノ | Cat# JP001"
        result = merge_comment(existing, "7", "8", "dark", "")
        assert "テクノ" in result
        assert "Energy: 7/10" in result

    def test_comment_very_long(self):
        """Extremely long comment should not crash."""
        existing = "A" * 1000
        result = merge_comment(existing, "5", "5", "", "")
        assert "Energy: 5/10" in result

    def test_driving_vibe_case_insensitive_strip(self):
        """'driving' should be stripped on re-run, replaced by new vibe."""
        existing = "Techno | Cat# XYZ | driving"
        result = merge_comment(existing, "5", "5", "euphoric", "")
        assert "driving" not in result
        assert "euphoric" in result


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
        """Shouldn't happen but essentia is unpredictable — must not crash."""
        audio = np.zeros(44100 * 5, dtype=np.float32)
        beats = np.array([-0.5, 0.5, 1.0], dtype=np.float32)
        contour = _beat_energy_contour(audio, beats)
        assert len(contour) >= 1


class TestWindowsPathEdgeCases:
    """to_rb_windows_path — replaces the retired XML to_rb_location.

    Rekordbox stores Windows paths (C:/...); the DB writer must convert every
    WSL /mnt/<drive>/ path. Previously only exercised indirectly (monkeypatched
    in test_add_engine); these are the real unit tests.
    """

    def test_mnt_c_becomes_windows_drive(self):
        p = Path("/mnt/c/Users/Kaitlyn/Music/Library/Track.flac")
        assert to_rb_windows_path(p) == "C:/Users/Kaitlyn/Music/Library/Track.flac"

    def test_other_drive_letter(self):
        p = Path("/mnt/d/Music/a.flac")
        assert to_rb_windows_path(p) == "D:/Music/a.flac"

    def test_parentheses_preserved(self):
        p = Path("/mnt/c/Music/Library/Track (Remix).flac")
        loc = to_rb_windows_path(p)
        assert loc.startswith("C:/")
        assert "(Remix)" in loc

    def test_ampersand_preserved(self):
        p = Path("/mnt/c/Music/DJ Jazzy Jeff & Fresh Prince.mp3")
        loc = to_rb_windows_path(p)
        assert loc.startswith("C:/")
        assert "&" in loc

    def test_non_mount_path_passthrough(self):
        """A non-/mnt path (e.g. tmp) is returned unchanged, not corrupted."""
        p = Path("/tmp/x.flac")
        assert to_rb_windows_path(p) == "/tmp/x.flac"


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


class TestWriteAnalysisTags:
    """write_analysis_tags on a real FLAC from the library (skips if none)."""

    @pytest.fixture
    def test_flac(self):
        flacs = glob.glob(
            "/mnt/c/Users/Kaitlyn/Music/Library/**/*.flac", recursive=True)
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
        result = write_analysis_tags(test_flac, {})
        assert result is True
