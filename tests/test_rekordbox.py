"""Tests for trackstage.rekordbox pure functions (no DB required)."""

import pytest

from trackstage.rekordbox import (
    pick_color_id,
    compute_situation,
    compute_vibe_tags,
    compute_sound_tags,
    compute_genre_tags,
    extract_genres_from_comment,
)


# ── pick_color_id ────────────────────────────────────────────────────────────

class TestPickColorId:
    def test_aggressive_wins_over_party(self):
        assert pick_color_id(["aggressive", "party"]) == "2"  # Red

    def test_sad_wins_over_happy(self):
        assert pick_color_id(["sad", "happy"]) == "8"  # Purple

    def test_relaxed_alone(self):
        assert pick_color_id(["relaxed"]) == "5"  # Green

    def test_empty_returns_zero(self):
        assert pick_color_id([]) == "0"

    def test_unknown_mood_returns_zero(self):
        assert pick_color_id(["confused"]) == "0"

    def test_priority_order_aggressive_first(self):
        # aggressive > sad > happy > party > relaxed
        assert pick_color_id(["party", "aggressive"]) == "2"

    def test_happy_alone(self):
        assert pick_color_id(["happy"]) == "3"  # Orange

    def test_party_alone(self):
        assert pick_color_id(["party"]) == "4"  # Yellow


# ── compute_situation ────────────────────────────────────────────────────────

class TestComputeSituation:
    def test_energy_1_ambient(self):
        assert compute_situation("1") == "Ambient"

    def test_energy_2_ambient(self):
        assert compute_situation("2") == "Ambient"

    def test_energy_3_warmup(self):
        assert compute_situation("3") == "Warmup"

    def test_energy_4_warmup(self):
        assert compute_situation("4") == "Warmup"

    def test_energy_5_groove(self):
        assert compute_situation("5") == "Groove"

    def test_energy_6_groove(self):
        assert compute_situation("6") == "Groove"

    def test_energy_7_peak(self):
        assert compute_situation("7") == "Peak"

    def test_energy_8_peak(self):
        assert compute_situation("8") == "Peak"

    def test_energy_9_rave(self):
        assert compute_situation("9") == "Rave"

    def test_energy_10_rave(self):
        assert compute_situation("10") == "Rave"

    def test_invalid_energy_empty(self):
        assert compute_situation("99") == ""

    def test_integer_input(self):
        assert compute_situation(5) == "Groove"


# ── compute_vibe_tags ────────────────────────────────────────────────────────

class TestComputeVibeTags:
    def test_deep_low_energy_gives_deep_and_hypnotic(self):
        r = {"vibes": ["deep"], "moods": []}
        result = compute_vibe_tags(r, energy=2)
        assert "Deep" in result
        assert "Hypnotic" in result

    def test_deep_high_energy_gives_deep_only(self):
        r = {"vibes": ["deep"], "moods": []}
        result = compute_vibe_tags(r, energy=7)
        assert "Deep" in result
        assert "Hypnotic" not in result

    def test_dark_high_energy_aggressive_gives_dark_and_raw(self):
        r = {"vibes": ["dark"], "moods": ["aggressive"]}
        result = compute_vibe_tags(r, energy=8)
        assert "Dark" in result
        assert "Raw" in result

    def test_aggressive_low_energy_no_raw(self):
        r = {"vibes": [], "moods": ["aggressive"]}
        result = compute_vibe_tags(r, energy=5)
        assert "Raw" not in result

    def test_driving_high_energy(self):
        r = {"vibes": ["driving"], "moods": []}
        result = compute_vibe_tags(r, energy=6)
        assert "Driving" in result

    def test_driving_low_energy_no_driving(self):
        r = {"vibes": ["driving"], "moods": []}
        result = compute_vibe_tags(r, energy=3)
        assert "Driving" not in result

    def test_euphoric(self):
        r = {"vibes": ["euphoric"], "moods": []}
        result = compute_vibe_tags(r, energy=5)
        assert "Euphoric" in result

    def test_relaxed_low_energy_melodic(self):
        r = {"vibes": [], "moods": ["relaxed"]}
        result = compute_vibe_tags(r, energy=3)
        assert "Melodic" in result

    def test_relaxed_with_aggressive_no_melodic(self):
        r = {"vibes": [], "moods": ["relaxed", "aggressive"]}
        result = compute_vibe_tags(r, energy=3)
        assert "Melodic" not in result

    def test_relaxed_high_energy_no_melodic(self):
        r = {"vibes": [], "moods": ["relaxed"]}
        result = compute_vibe_tags(r, energy=7)
        assert "Melodic" not in result

    def test_empty_vibes_moods(self):
        r = {"vibes": [], "moods": []}
        result = compute_vibe_tags(r, energy=5)
        assert result == set()

    def test_missing_keys(self):
        r = {}
        result = compute_vibe_tags(r, energy=5)
        assert result == set()


# ── compute_sound_tags ───────────────────────────────────────────────────────

class TestComputeSoundTags:
    def test_vocal_from_voice(self):
        r = {"vocal": "voice"}
        result = compute_sound_tags(r)
        assert "Vocal" in result

    def test_no_vocal_from_instrumental(self):
        r = {"vocal": "instrumental"}
        result = compute_sound_tags(r)
        assert "Vocal" not in result

    def test_acid_from_genre(self):
        r = {}
        result = compute_sound_tags(r, comment="Acid House | Energy: 7")
        assert "Acid" in result

    def test_dub_from_genre(self):
        r = {}
        result = compute_sound_tags(r, comment="Dub Techno | Energy: 5")
        assert "Dub" in result

    def test_dub_not_from_dubstep(self):
        r = {}
        result = compute_sound_tags(r, comment="Dubstep | Energy: 8")
        assert "Dub" not in result

    def test_breaks_from_genre(self):
        r = {}
        result = compute_sound_tags(r, comment="Breakbeat | Energy: 6")
        assert "Breaks" in result

    def test_empty(self):
        r = {}
        result = compute_sound_tags(r, comment="")
        assert result == set()


# ── extract_genres_from_comment ──────────────────────────────────────────────

class TestExtractGenresFromComment:
    def test_normal_genres(self):
        result = extract_genres_from_comment("House, Tech House | Energy: 7")
        assert result == ["house", "tech house"]

    def test_single_genre(self):
        result = extract_genres_from_comment("Techno | Energy: 8")
        assert result == ["techno"]

    def test_empty_string(self):
        result = extract_genres_from_comment("")
        assert result == []

    def test_starts_with_energy(self):
        result = extract_genres_from_comment("Energy: 7 | Dance: 0.8")
        assert result == []

    def test_starts_with_dance(self):
        result = extract_genres_from_comment("Dance: 0.8 | other stuff")
        assert result == []

    def test_starts_with_cat(self):
        result = extract_genres_from_comment("Cat#12345 | House")
        assert result == []

    def test_no_pipe_returns_all_as_genres(self):
        result = extract_genres_from_comment("House, Disco")
        assert result == ["house", "disco"]

    def test_whitespace_trimmed(self):
        result = extract_genres_from_comment("  House ,  Techno  | blah")
        assert result == ["house", "techno"]


# ── compute_genre_tags ───────────────────────────────────────────────────────

class TestComputeGenreTags:
    def test_house_maps_to_house(self):
        result = compute_genre_tags("House | Energy: 5")
        assert "House" in result

    def test_drum_n_bass_maps_to_dnb_jungle(self):
        result = compute_genre_tags("Drum N Bass | Energy: 8")
        assert "DnB/Jungle" in result

    def test_jungle_also_maps_to_dnb_jungle(self):
        result = compute_genre_tags("Jungle | Energy: 7")
        assert "DnB/Jungle" in result

    def test_multiple_genres(self):
        result = compute_genre_tags("House, Disco | Energy: 5")
        assert "House" in result
        assert "Disco" in result

    def test_unknown_genre_skipped(self):
        result = compute_genre_tags("Polka, House | Energy: 3")
        assert "House" in result
        assert len(result) == 1

    def test_breaks_maps_to_breakbeat(self):
        result = compute_genre_tags("Breaks | Energy: 6")
        assert "Breakbeat" in result

    def test_minimal_techno_maps_to_minimal(self):
        result = compute_genre_tags("Minimal Techno | Energy: 5")
        assert "Minimal" in result

    def test_empty_comment(self):
        result = compute_genre_tags("")
        assert result == set()
