"""Tests for loudness.py — gain calculation, tag format, edge cases."""

import pytest
from trackstage.loudness import TARGET_LUFS, format_loudness_log
import numpy as np


class TestGainCalculation:
    def test_target_lufs_reasonable(self):
        """Target should be between -14 (broadcast) and -6 (club)."""
        assert -14.0 <= TARGET_LUFS <= -6.0

    def test_gain_formula(self):
        """gain = target - measured. Quiet track gets positive gain."""
        measured = -20.0
        gain = TARGET_LUFS - measured
        assert gain > 0  # quiet track needs boost

    def test_loud_track_gets_negative_gain(self):
        measured = -5.0
        gain = TARGET_LUFS - measured
        assert gain < 0  # loud track needs reduction


class TestFormatLoudnessLog:
    def test_normal_output(self):
        loudness = {"lufs": -12.5, "peak": 0.95, "gain_db": 3.5, "range_lu": 8.0}
        result = format_loudness_log(loudness)
        assert "LUFS: -12.5" in result
        assert "Gain: +3.5 dB" in result

    def test_null_lufs(self):
        loudness = {"lufs": None, "peak": None, "gain_db": None, "range_lu": None}
        result = format_loudness_log(loudness)
        assert "No loudness data" in result

    def test_clip_warning(self):
        """Tracks where gain + peak > 0 dBFS should show clip warning."""
        loudness = {"lufs": -20.0, "peak": 0.99, "gain_db": 11.0, "range_lu": 5.0}
        result = format_loudness_log(loudness)
        assert "CLIP" in result

    def test_no_clip_warning_when_safe(self):
        loudness = {"lufs": -10.0, "peak": 0.5, "gain_db": 1.0, "range_lu": 5.0}
        result = format_loudness_log(loudness)
        assert "CLIP" not in result

    def test_zero_peak_no_crash(self):
        """Peak of 0 should not cause log10(0) crash."""
        loudness = {"lufs": -30.0, "peak": 0.0, "gain_db": 21.0, "range_lu": 2.0}
        result = format_loudness_log(loudness)
        assert "LUFS" in result
