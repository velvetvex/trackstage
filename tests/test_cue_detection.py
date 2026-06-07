"""Tests for cue_detection.py — structural analysis edge cases."""

import numpy as np
import pytest
from trackstage.cue_detection import (
    _beat_energy_contour, _smooth, _find_transitions, _dedupe, CUE_COLORS,
)


class TestBeatEnergyContour:
    def test_basic_contour(self):
        sr = 44100
        audio = np.random.randn(sr * 10).astype(np.float32)
        beats = np.linspace(0, 9, 40).astype(np.float32)
        contour = _beat_energy_contour(audio, beats, sr)
        assert len(contour) == 39  # len(beats) - 1
        assert all(e >= 0 for e in contour)

    def test_empty_beats(self):
        audio = np.zeros(44100, dtype=np.float32)
        beats = np.array([], dtype=np.float32)
        contour = _beat_energy_contour(audio, beats)
        assert len(contour) == 0

    def test_beats_beyond_audio(self):
        """Beats past audio end should be gracefully handled."""
        audio = np.zeros(44100, dtype=np.float32)  # 1 second
        beats = np.array([0.0, 0.5, 1.5, 2.0], dtype=np.float32)  # 1.5 and 2.0 beyond
        contour = _beat_energy_contour(audio, beats)
        assert len(contour) <= 3  # should stop before going out of bounds


class TestSmooth:
    def test_preserves_length(self):
        arr = np.random.randn(100).astype(np.float32)
        smoothed = _smooth(arr, window=16)
        assert len(smoothed) == len(arr)

    def test_short_array(self):
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        smoothed = _smooth(arr, window=16)
        assert len(smoothed) == 3

    def test_constant_array_unchanged(self):
        arr = np.full(100, 5.0, dtype=np.float32)
        smoothed = _smooth(arr, window=16)
        np.testing.assert_allclose(smoothed[16:-16], 5.0, atol=0.01)


class TestFindTransitions:
    def test_step_function(self):
        """Sharp step up should be detected as a rise."""
        smoothed = np.concatenate([
            np.full(100, 0.1),
            np.full(100, 0.9),
        ]).astype(np.float32)
        beats = np.linspace(0, 60, 200).astype(np.float32)
        rises, drops = _find_transitions(smoothed, beats)
        assert len(rises) >= 1
        assert rises[0][0] > 50  # transition around beat 100

    def test_flat_energy_no_transitions(self):
        smoothed = np.full(200, 0.5, dtype=np.float32)
        beats = np.linspace(0, 60, 200).astype(np.float32)
        rises, drops = _find_transitions(smoothed, beats)
        assert len(rises) == 0
        assert len(drops) == 0


class TestDedupe:
    def test_removes_nearby(self):
        transitions = [(50, 0.5), (55, 0.8), (100, 0.6)]
        result = _dedupe(transitions, min_gap=32)
        assert len(result) == 2  # 50 and 55 are within 32, keep strongest

    def test_empty_input(self):
        assert _dedupe([], min_gap=32) == []

    def test_keeps_sorted_by_position(self):
        transitions = [(100, 0.5), (50, 0.8), (200, 0.6)]
        result = _dedupe(transitions, min_gap=32)
        positions = [r[0] for r in result]
        assert positions == sorted(positions)


class TestCueColors:
    def test_all_types_have_colors(self):
        required = {"mix_in", "buildup", "drop", "breakdown", "mix_out"}
        assert set(CUE_COLORS.keys()) == required

    def test_colors_are_valid_rgb(self):
        for cue_type, colors in CUE_COLORS.items():
            for channel in ("Red", "Green", "Blue"):
                assert channel in colors, f"{cue_type} missing {channel}"
                val = int(colors[channel])
                assert 0 <= val <= 255, f"{cue_type} {channel}={val} out of range"
