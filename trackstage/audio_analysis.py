"""
audio_analysis.py — Essentia-based audio analysis for DJ metadata.

Extracts: key (Camelot), BPM, energy (1-10), danceability (1-10).
"""

import logging
from pathlib import Path

import numpy as np
import essentia
from essentia.standard import (
    MonoLoader,
    KeyExtractor,
    RhythmExtractor2013,
    Energy,
    Danceability,
    OnsetRate,
)

essentia.log.infoActive = False
essentia.log.warningActive = False

log = logging.getLogger(__name__)

# Calibrated from 678-track library (2026-06-07)
ENERGY_P5 = 18.0
ENERGY_P95 = 346.0
DANCE_P5 = 0.92
DANCE_P95 = 2.33
ONSET_RATE_P5 = 2.0
ONSET_RATE_P95 = 9.0

# BPM range for DJ music — if detected BPM is below floor, double it
BPM_FLOOR = 100.0
BPM_CEILING = 185.0

CAMELOT = {
    ("C", "major"): "8B",  ("A", "minor"): "8A",
    ("G", "major"): "9B",  ("E", "minor"): "9A",
    ("D", "major"): "10B", ("B", "minor"): "10A",
    ("A", "major"): "11B", ("F#", "minor"): "11A",
    ("E", "major"): "12B", ("C#", "minor"): "12A", ("Db", "minor"): "12A",
    ("B", "major"): "1B",  ("Cb", "major"): "1B",  ("G#", "minor"): "1A", ("Ab", "minor"): "1A",
    ("F#", "major"): "2B", ("Gb", "major"): "2B",  ("Eb", "minor"): "2A", ("D#", "minor"): "2A",
    ("Db", "major"): "3B", ("C#", "major"): "3B",  ("Bb", "minor"): "3A", ("A#", "minor"): "3A",
    ("Ab", "major"): "4B", ("G#", "major"): "4B",  ("F", "minor"): "4A",
    ("Eb", "major"): "5B", ("D#", "major"): "5B",  ("C", "minor"): "5A",
    ("Bb", "major"): "6B", ("A#", "major"): "6B",  ("G", "minor"): "6A",
    ("F", "major"): "7B",  ("D", "minor"): "7A",
}


def to_camelot(key: str, scale: str) -> str:
    return CAMELOT.get((key, scale), "")


def analyze(file_path: Path, existing_key: str = "") -> dict:
    """Run audio analysis on a single file. Returns metadata dict.

    existing_key: key from Discogs/tags (e.g. "Dm", "Ebm") for cross-reference.
    """
    result = {
        "bpm": "",
        "key": "",
        "camelot": "",
        "key_confidence": "",
        "energy": "",
        "danceability": "",
    }

    try:
        audio = MonoLoader(filename=str(file_path), sampleRate=44100)()
    except Exception as e:
        log.warning(f"  ⚠  Could not load audio: {e}")
        return result

    # --- Key detection with confidence + Discogs cross-reference ---
    try:
        key, scale, key_strength = KeyExtractor()(audio)
        result["key_confidence"] = str(round(key_strength, 2))
        if key_strength >= 0.5 or not existing_key:
            result["key"] = f"{key} {scale}"
            result["camelot"] = to_camelot(key, scale)
        else:
            # Low confidence — prefer existing Discogs key
            result["key"] = existing_key
            ek = _parse_key_string(existing_key)
            if ek:
                result["camelot"] = to_camelot(*ek)
            log.info(f"  ℹ  Key confidence {key_strength:.2f} < 0.5, using existing: {existing_key}")
    except Exception as e:
        log.warning(f"  ⚠  Key detection failed: {e}")
        if existing_key:
            result["key"] = existing_key
            ek = _parse_key_string(existing_key)
            if ek:
                result["camelot"] = to_camelot(*ek)

    # --- Energy (computed before BPM so it can gate half-time correction) ---
    try:
        frame_size = 2048
        hop_size = 1024
        energy_algo = Energy()
        total_energy = 0.0
        n_frames = 0
        for i in range(0, len(audio) - frame_size, hop_size):
            frame = audio[i:i + frame_size]
            total_energy += energy_algo(frame)
            n_frames += 1
        if n_frames > 0:
            avg_energy = total_energy / n_frames
            normalized = (avg_energy - ENERGY_P5) / (ENERGY_P95 - ENERGY_P5)
            scaled = min(10, max(1, round(1 + normalized * 9)))
            result["energy"] = str(scaled)
    except Exception as e:
        log.warning(f"  ⚠  Energy analysis failed: {e}")

    # --- BPM with half-time correction (gated by onset density + confidence + energy) ---
    try:
        bpm, _, rhythm_conf, _, _ = RhythmExtractor2013(method="multifeature")(audio)
        if rhythm_conf < 0.3:
            result["bpm"] = ""
        else:
            if bpm < BPM_FLOOR and bpm * 2 <= BPM_CEILING:
                energy_val = int(result.get("energy") or "0")
                if energy_val >= 7:
                    # High energy + sub-100 BPM = always halftime (DnB/jungle/hardcore)
                    bpm = bpm * 2
                else:
                    _, onset_rate = OnsetRate()(audio)
                    if onset_rate > 4.5 and rhythm_conf < 2.5:
                        bpm = bpm * 2
            result["bpm"] = str(round(bpm, 1))
    except Exception as e:
        log.warning(f"  ⚠  BPM detection failed: {e}")

    # --- Danceability: blend DFA with onset density ---
    try:
        dance_val, _ = Danceability()(audio)
        dfa_norm = (dance_val - DANCE_P5) / (DANCE_P95 - DANCE_P5)
        dfa_norm = max(0.0, min(1.0, dfa_norm))

        _, onset_rate_val = OnsetRate()(audio)
        onset_norm = (onset_rate_val - ONSET_RATE_P5) / (ONSET_RATE_P95 - ONSET_RATE_P5)
        onset_norm = max(0.0, min(1.0, onset_norm))

        # 50/50 blend: DFA captures regularity, onset rate captures rhythmic density
        blended = 0.5 * dfa_norm + 0.5 * onset_norm
        scaled = min(10, max(1, round(1 + blended * 9)))
        result["danceability"] = str(scaled)
    except Exception as e:
        log.warning(f"  ⚠  Danceability analysis failed: {e}")

    return result


def _parse_key_string(key_str: str) -> tuple | None:
    """Parse 'Dm', 'Ebm', 'D', 'Bb' into (key, scale) tuple."""
    key_str = key_str.strip()
    if not key_str:
        return None
    if key_str.endswith("m"):
        note = key_str[:-1]
        return (note, "minor") if note else None
    return (key_str, "major")


def format_analysis_log(analysis: dict) -> str:
    parts = []
    if analysis["camelot"]:
        parts.append(f"Key: {analysis['camelot']} ({analysis['key']})")
    if analysis["bpm"]:
        parts.append(f"BPM: {analysis['bpm']}")
    if analysis["energy"]:
        parts.append(f"Energy: {analysis['energy']}/10")
    if analysis["danceability"]:
        parts.append(f"Dance: {analysis['danceability']}/10")
    return "  │  ".join(parts) if parts else "No analysis data"
