"""Tests for mood_detection.py — thresholds, energy gating, vibe mapping."""

import pytest
from trackstage.mood_detection import (
    CLASSIFIER_THRESHOLDS, ENERGY_GATED_VIBES, VIBE_MAP, CLASSIFIERS,
    _compute_mel_patches,
)
import numpy as np


class TestVibeMap:
    def test_all_moods_have_vibes(self):
        moods = {"aggressive", "happy", "relaxed", "sad", "party"}
        for mood in moods:
            assert mood in VIBE_MAP, f"Missing vibe mapping for {mood}"

    def test_vibe_values_unique(self):
        vibes = list(VIBE_MAP.values())
        assert len(vibes) == len(set(vibes)), "Duplicate vibes"


class TestEnergyGating:
    def test_driving_requires_energy_5(self):
        assert ENERGY_GATED_VIBES["driving"] == 5

    def test_gated_vibes_exist_in_vibe_map(self):
        for vibe in ENERGY_GATED_VIBES:
            assert vibe in VIBE_MAP.values(), f"Gated vibe '{vibe}' not in VIBE_MAP"


class TestClassifierThresholds:
    def test_all_classifiers_have_thresholds(self):
        mood_classifiers = [c for c in CLASSIFIERS if c != "voice_instrumental"]
        for clf in mood_classifiers:
            assert clf in CLASSIFIER_THRESHOLDS, f"Missing threshold for {clf}"

    def test_party_has_higher_threshold(self):
        """Party was raised to 0.70 to reduce false positives."""
        assert CLASSIFIER_THRESHOLDS["mood_party"] > CLASSIFIER_THRESHOLDS["mood_aggressive"]

    def test_thresholds_in_valid_range(self):
        for clf, thresh in CLASSIFIER_THRESHOLDS.items():
            assert 0.0 < thresh < 1.0, f"{clf} threshold {thresh} out of range"


class TestMelPatches:
    def test_short_audio_returns_empty(self):
        """Track < 2 seconds should return empty patches."""
        audio = np.zeros(16000, dtype=np.float32)  # 1 second at 16kHz
        patches = _compute_mel_patches(audio)
        assert len(patches) == 0

    def test_normal_audio_produces_patches(self):
        audio = np.random.randn(16000 * 30).astype(np.float32)  # 30 seconds
        patches = _compute_mel_patches(audio)
        assert len(patches) > 0
        assert patches.shape[1] == 128  # 128 frames per patch
        assert patches.shape[2] == 96   # 96 mel bands

    def test_patch_shape_consistent(self):
        audio = np.random.randn(16000 * 60).astype(np.float32)
        patches = _compute_mel_patches(audio)
        for patch in patches:
            assert patch.shape == (128, 96)
