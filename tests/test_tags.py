"""Tests for trackstage.tags — unified tag reading/writing."""

import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trackstage.tags import (
    build_comment, merge_comment, build_grouping, read_tags,
    read_comment, write_tags, write_analysis_tags, EXTENSIONS,
)


class TestBuildComment:
    def test_full_meta(self):
        meta = {
            "styles": "Breakbeat, Techno",
            "catno": "MCST 40131",
            "energy": "7",
            "danceability": "8",
            "vibes": "dark, driving",
            "vocal": "instrumental",
        }
        result = build_comment(meta)
        assert result == "Breakbeat, Techno | Cat# MCST 40131 | Energy: 7/10 | Dance: 8/10 | dark, driving | instrumental"

    def test_minimal_meta(self):
        meta = {"energy": "5", "danceability": "6"}
        result = build_comment(meta)
        assert result == "Energy: 5/10 | Dance: 6/10"

    def test_no_styles_with_catno(self):
        meta = {"catno": "ABC-001", "energy": "4", "danceability": "3"}
        result = build_comment(meta)
        assert result == "Cat# ABC-001 | Energy: 4/10 | Dance: 3/10"
        assert "styles" not in result.lower()

    def test_empty_meta(self):
        result = build_comment({})
        assert result == ""

    def test_only_styles_and_catno(self):
        meta = {"styles": "Deep House", "catno": "REC-123"}
        result = build_comment(meta)
        assert result == "Deep House | Cat# REC-123"


class TestMergeComment:
    def test_preserves_discogs_data(self):
        existing = "Breakbeat, Techno | Cat# MCST 40131"
        result = merge_comment(existing, "3", "5", "", "instrumental")
        assert "Breakbeat, Techno" in result
        assert "Cat# MCST 40131" in result
        assert "Energy: 3/10" in result
        assert "Dance: 5/10" in result
        assert "instrumental" in result

    def test_strips_old_analysis(self):
        existing = "Techno | Cat# XYZ | Energy: 5/10 | Dance: 6/10 | dark, driving | instrumental"
        result = merge_comment(existing, "8", "9", "deep", "voice")
        # Old values stripped
        assert result.count("Energy:") == 1
        assert "Energy: 8/10" in result
        assert result.count("Dance:") == 1
        assert "Dance: 9/10" in result
        # Old vibes stripped
        assert "dark, driving" not in result
        # New values present
        assert "deep" in result
        assert "voice" in result
        # Discogs data preserved
        assert "Techno" in result
        assert "Cat# XYZ" in result

    def test_empty_existing(self):
        result = merge_comment("", "7", "8", "dark, driving", "voice")
        assert "Energy: 7/10" in result
        assert "Dance: 8/10" in result
        assert "dark, driving" in result
        assert "voice" in result

    def test_strips_compound_vibes(self):
        """Compound vibe strings like 'dark, euphoric' should be stripped."""
        existing = "House | dark, euphoric | Energy: 4/10"
        result = merge_comment(existing, "6", "7", "deep, melancholic", "")
        assert "dark, euphoric" not in result
        assert "House" in result
        assert "Energy: 6/10" in result
        assert "deep, melancholic" in result

    def test_preserves_genre_with_commas(self):
        """Genres like 'Breakbeat, Techno' should not be stripped as vibes."""
        existing = "Breakbeat, Techno | Cat# ABC"
        result = merge_comment(existing, "5", "5", "", "")
        assert "Breakbeat, Techno" in result

    def test_no_analysis_fields(self):
        """When no analysis provided, existing Discogs stays."""
        existing = "House | Cat# 001"
        result = merge_comment(existing, "", "", "", "")
        assert result == "House | Cat# 001"


class TestBuildGrouping:
    def test_normal_case(self):
        r = {"vibes": ["deep", "driving"], "moods": ["relaxed", "party"]}
        result = build_grouping(r)
        assert result == "deep, driving, relaxed, party"

    def test_no_duplicates(self):
        """Moods that already appear in vibes should not be duplicated."""
        r = {"vibes": ["deep", "driving"], "moods": ["deep", "party"]}
        result = build_grouping(r)
        assert result == "deep, driving, party"
        assert result.count("deep") == 1

    def test_empty_vibes(self):
        r = {"vibes": [], "moods": ["chill", "ambient"]}
        result = build_grouping(r)
        assert result == "chill, ambient"

    def test_empty_moods(self):
        r = {"vibes": ["dark", "euphoric"], "moods": []}
        result = build_grouping(r)
        assert result == "dark, euphoric"

    def test_empty_both(self):
        r = {"vibes": [], "moods": []}
        result = build_grouping(r)
        assert result == ""

    def test_missing_keys(self):
        r = {}
        result = build_grouping(r)
        assert result == ""


class TestReadTags:
    @patch('trackstage.tags.MP3')
    def test_reads_mp3_tags(self, mock_mp3):
        mock_audio = MagicMock()
        mock_audio.tags = {"TPE1": "Test Artist", "TIT2": "Test Title"}
        mock_mp3.return_value = mock_audio

        result = read_tags(Path("/fake/path/file.mp3"))
        assert "artist" in result
        assert "title" in result
        assert result["artist"] == "Test Artist"
        assert result["title"] == "Test Title"

    @patch('trackstage.tags.FLAC')
    def test_reads_flac_tags(self, mock_flac):
        mock_audio = MagicMock()
        mock_audio.get.side_effect = lambda key, default=[]: {
            "artist": ["FLAC Artist"],
            "title": ["FLAC Title"],
        }.get(key, default)
        mock_flac.return_value = mock_audio

        result = read_tags(Path("/fake/path/file.flac"))
        assert result["artist"] == "FLAC Artist"
        assert result["title"] == "FLAC Title"

    def test_fallback_to_filename(self):
        """When tags are empty, falls back to 'Artist - Title' filename pattern."""
        with patch('trackstage.tags.MP3') as mock_mp3:
            mock_audio = MagicMock()
            mock_audio.tags = None
            mock_mp3.return_value = mock_audio

            result = read_tags(Path("/fake/path/DJ Shadow - Midnight.mp3"))
            assert result["artist"] == "DJ Shadow"
            assert result["title"] == "Midnight"

    def test_fallback_no_separator(self):
        """Filename without ' - ' uses whole stem as title."""
        with patch('trackstage.tags.MP3') as mock_mp3:
            mock_audio = MagicMock()
            mock_audio.tags = None
            mock_mp3.return_value = mock_audio

            result = read_tags(Path("/fake/path/untitled_track.mp3"))
            assert result["title"] == "untitled_track"

    def test_returns_dict_with_keys(self):
        """Result always contains artist and title keys."""
        with patch('trackstage.tags.MP3') as mock_mp3:
            mock_audio = MagicMock()
            mock_audio.tags = None
            mock_mp3.return_value = mock_audio

            result = read_tags(Path("/fake/test.mp3"))
            assert "artist" in result
            assert "title" in result


class TestExtensions:
    def test_all_formats_present(self):
        assert '.flac' in EXTENSIONS
        assert '.mp3' in EXTENSIONS
        assert '.aiff' in EXTENSIONS
        assert '.aif' in EXTENSIONS
        assert '.m4a' in EXTENSIONS

    def test_is_set(self):
        assert isinstance(EXTENSIONS, set)
