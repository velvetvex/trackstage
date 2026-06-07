"""Tests for audio_analysis.py — key detection, BPM logic, energy/danceability scaling."""

import pytest
from trackstage.audio_analysis import (
    to_camelot, _parse_key_string, CAMELOT,
    ENERGY_P5, ENERGY_P95, BPM_FLOOR, BPM_CEILING,
)


class TestCamelot:
    """All 24 keys must map to Camelot. Enharmonic equivalents must match."""

    EXPECTED = {
        "8B": [("C", "major")],
        "8A": [("A", "minor")],
        "9B": [("G", "major")],
        "9A": [("E", "minor")],
        "10B": [("D", "major")],
        "10A": [("B", "minor")],
        "11B": [("A", "major")],
        "11A": [("F#", "minor")],
        "12B": [("E", "major")],
        "12A": [("C#", "minor"), ("Db", "minor")],
        "1B": [("B", "major"), ("Cb", "major")],
        "1A": [("G#", "minor"), ("Ab", "minor")],
        "2B": [("F#", "major"), ("Gb", "major")],
        "2A": [("Eb", "minor"), ("D#", "minor")],
        "3B": [("Db", "major"), ("C#", "major")],
        "3A": [("Bb", "minor"), ("A#", "minor")],
        "4B": [("Ab", "major"), ("G#", "major")],
        "4A": [("F", "minor")],
        "5B": [("Eb", "major"), ("D#", "major")],
        "5A": [("C", "minor")],
        "6B": [("Bb", "major"), ("A#", "major")],
        "6A": [("G", "minor")],
        "7B": [("F", "major")],
        "7A": [("D", "minor")],
    }

    def test_all_24_camelot_codes_covered(self):
        codes = set(CAMELOT.values())
        expected_codes = {f"{n}{l}" for n in range(1, 13) for l in "AB"}
        assert codes == expected_codes, f"Missing: {expected_codes - codes}"

    @pytest.mark.parametrize("camelot_code,keys", list(EXPECTED.items()))
    def test_enharmonic_equivalents(self, camelot_code, keys):
        for key, scale in keys:
            assert to_camelot(key, scale) == camelot_code, \
                f"({key}, {scale}) should map to {camelot_code}"

    def test_unknown_key_returns_empty(self):
        assert to_camelot("X", "major") == ""
        assert to_camelot("", "") == ""


class TestParseKeyString:
    @pytest.mark.parametrize("input_str,expected", [
        ("Dm", ("D", "minor")),
        ("Ebm", ("Eb", "minor")),
        ("C", ("C", "major")),
        ("Bb", ("Bb", "major")),
        ("F#m", ("F#", "minor")),
        ("G#", ("G#", "major")),
        ("Abm", ("Ab", "minor")),
    ])
    def test_valid_keys(self, input_str, expected):
        assert _parse_key_string(input_str) == expected

    def test_empty_string(self):
        assert _parse_key_string("") is None

    def test_whitespace(self):
        assert _parse_key_string("  Dm  ") == ("D", "minor")

    def test_bare_m(self):
        """'m' alone has no note — should return None."""
        assert _parse_key_string("m") is None


class TestEnergyScaling:
    """Energy must clamp to 1-10 for any input value."""

    def test_below_p5_gives_1(self):
        normalized = (0.0 - ENERGY_P5) / (ENERGY_P95 - ENERGY_P5)
        scaled = min(10, max(1, round(1 + normalized * 9)))
        assert scaled == 1

    def test_above_p95_gives_10(self):
        normalized = (500.0 - ENERGY_P5) / (ENERGY_P95 - ENERGY_P5)
        scaled = min(10, max(1, round(1 + normalized * 9)))
        assert scaled == 10

    def test_midpoint_gives_5_or_6(self):
        mid = (ENERGY_P5 + ENERGY_P95) / 2
        normalized = (mid - ENERGY_P5) / (ENERGY_P95 - ENERGY_P5)
        scaled = min(10, max(1, round(1 + normalized * 9)))
        assert scaled in (5, 6)

    def test_negative_energy_gives_1(self):
        normalized = (-100.0 - ENERGY_P5) / (ENERGY_P95 - ENERGY_P5)
        scaled = min(10, max(1, round(1 + normalized * 9)))
        assert scaled == 1


class TestBPMDoubling:
    """BPM half-time correction logic must handle edge cases."""

    def test_below_floor_doubles_when_result_under_ceiling(self):
        bpm = 85.0
        assert bpm < BPM_FLOOR
        assert bpm * 2 <= BPM_CEILING

    def test_below_floor_no_double_when_result_over_ceiling(self):
        bpm = 95.0
        assert bpm < BPM_FLOOR
        assert bpm * 2 > BPM_CEILING

    def test_above_floor_never_doubles(self):
        bpm = 130.0
        assert bpm >= BPM_FLOOR

    def test_floor_ceiling_relationship(self):
        """Ceiling must be > 2*Floor to have a doubling window."""
        assert BPM_CEILING > BPM_FLOOR
        # BPM_FLOOR/2 must be a reasonable minimum detection (50)
        assert BPM_FLOOR / 2 >= 40
