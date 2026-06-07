"""Tests for comment merging in analyze_library.py — must not clobber Discogs data."""

import sys
sys.path.insert(0, 'scripts')

from analyze_library import _merge_comment


class TestCommentMerge:
    def test_preserves_discogs_metadata(self):
        existing = "Breakbeat, Techno | Cat# MCST 40131"
        result = _merge_comment(existing, "3", "5", "", "instrumental")
        assert "Breakbeat, Techno" in result
        assert "Cat# MCST 40131" in result
        assert "Energy: 3/10" in result
        assert "Dance: 5/10" in result

    def test_empty_existing_comment(self):
        result = _merge_comment("", "7", "8", "dark, driving", "voice")
        assert "Energy: 7/10" in result
        assert "Dance: 8/10" in result
        assert "dark, driving" in result
        assert "voice" in result

    def test_replaces_old_energy_dance(self):
        """Re-analyzing should replace old values, not stack them."""
        existing = "Techno | Cat# XYZ | Energy: 5/10 | Dance: 6/10 | instrumental"
        result = _merge_comment(existing, "8", "9", "dark", "voice")
        assert result.count("Energy:") == 1
        assert "Energy: 8/10" in result
        assert "Dance: 9/10" in result
        assert "Energy: 5/10" not in result
        assert "Dance: 6/10" not in result

    def test_replaces_old_vibes(self):
        existing = "Acid | Cat# ABC | Energy: 3/10 | dark"
        result = _merge_comment(existing, "3", "4", "euphoric", "")
        assert "dark" not in result
        assert "euphoric" in result

    def test_no_analysis_data_preserves_original(self):
        existing = "House, Techno | Cat# FOO123"
        result = _merge_comment(existing, "", "", "", "")
        assert "House, Techno" in result
        assert "Cat# FOO123" in result

    def test_pipe_delimiter_consistency(self):
        result = _merge_comment("", "5", "6", "deep", "instrumental")
        parts = result.split(" | ")
        assert len(parts) == 4
        assert parts[0] == "Energy: 5/10"
        assert parts[1] == "Dance: 6/10"
        assert parts[2] == "deep"
        assert parts[3] == "instrumental"

    def test_old_instrumental_stripped(self):
        """Old vocal tag should be replaced, not duplicated."""
        existing = "Techno | instrumental"
        result = _merge_comment(existing, "5", "5", "", "voice")
        assert result.count("instrumental") == 0
        assert "voice" in result

    def test_complex_existing_comment(self):
        """Real-world comment with styles, catno, old analysis."""
        existing = "Acid, Techno, Industrial | Cat# MORD030 | Energy: 9/10 | Dance: 7/10 | dark, driving | instrumental"
        result = _merge_comment(existing, "8", "6", "deep", "voice")
        assert "Acid, Techno, Industrial" in result
        assert "Cat# MORD030" in result
        assert "Energy: 8/10" in result
        assert "Dance: 6/10" in result
        assert "deep" in result
        assert "voice" in result
        assert result.count("Energy:") == 1
