"""
cue_detection.py — Auto-detect mix points and structure for Rekordbox cue markers.

Detects: first downbeat, drop(s), breakdown(s), outro.
Returns cue points as list of dicts ready for Rekordbox XML POSITION_MARK elements.
"""

import logging
from pathlib import Path

import numpy as np
import essentia
from essentia.standard import (
    MonoLoader,
    BeatTrackerMultiFeature,
    Energy,
)

essentia.log.infoActive = False
essentia.log.warningActive = False

log = logging.getLogger(__name__)

CUE_COLORS = {
    "mix_in":    {"Red": "0",   "Green": "226", "Blue": "255"},   # cyan
    "buildup":   {"Red": "255", "Green": "134", "Blue": "0"},     # orange
    "drop":      {"Red": "232", "Green": "28",  "Blue": "2"},     # red
    "breakdown": {"Red": "146", "Green": "52",  "Blue": "232"},   # purple
    "mix_out":   {"Red": "0",   "Green": "192", "Blue": "0"},     # green
}


def _beat_energy_contour(audio, beats, sr=44100):
    energy_algo = Energy()
    beat_energies = []
    for i in range(len(beats) - 1):
        start = int(beats[i] * sr)
        end = int(beats[i + 1] * sr)
        if end > len(audio):
            break
        segment = audio[start:end]
        if len(segment) > 0:
            beat_energies.append(energy_algo(segment))
        else:
            beat_energies.append(0.0)
    return np.array(beat_energies)


def _smooth(arr, window=16):
    if len(arr) < window:
        return arr
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same")


def _find_transitions(smoothed, beats, threshold_ratio=0.4):
    """Find significant energy rises and drops."""
    max_e = np.max(smoothed)
    min_e = np.min(smoothed)
    energy_range = max_e - min_e
    if energy_range < 1e-6:
        return [], []

    threshold = energy_range * threshold_ratio

    rises = []
    drops = []
    n = len(smoothed)

    window = 32
    for i in range(window, n - window):
        before = np.mean(smoothed[i - window:i])
        after = np.mean(smoothed[i:i + window])
        diff = after - before

        if diff > threshold:
            rises.append((i, diff))
        elif diff < -threshold:
            drops.append((i, abs(diff)))

    # Deduplicate — keep strongest within 16-beat windows
    rises = _dedupe(rises, min_gap=32)
    drops = _dedupe(drops, min_gap=32)

    return rises, drops


def _dedupe(transitions, min_gap=32):
    if not transitions:
        return []
    transitions.sort(key=lambda x: -x[1])
    kept = []
    for idx, strength in transitions:
        if all(abs(idx - k[0]) > min_gap for k in kept):
            kept.append((idx, strength))
    kept.sort(key=lambda x: x[0])
    return kept


def detect_cues(file_path: Path) -> list:
    """Detect cue points for a track. Returns list of cue dicts."""
    cues = []

    try:
        audio = MonoLoader(filename=str(file_path), sampleRate=44100)()
    except Exception as e:
        log.warning(f"  ⚠  Could not load audio for cues: {e}")
        return cues

    try:
        beats, _ = BeatTrackerMultiFeature()(audio)
    except Exception as e:
        log.warning(f"  ⚠  Beat tracking failed: {e}")
        return cues

    if len(beats) < 32:
        return cues

    beat_energy = _beat_energy_contour(audio, beats)
    smoothed = _smooth(beat_energy, window=16)

    max_e = np.max(smoothed)
    min_e = np.min(smoothed)
    energy_range = max_e - min_e

    if energy_range < 1e-6:
        return cues

    # Mix-in: first beat where energy exceeds 20% of range
    # If energy is high from the start (no intro), use beat 0 as "Start" marker
    mix_in_threshold = min_e + energy_range * 0.2
    mix_in_beat = 0
    for i, e in enumerate(smoothed):
        if e > mix_in_threshold:
            mix_in_beat = max(0, i - 4)
            break

    mix_in_time = float(beats[mix_in_beat])
    # Minimum 4 seconds — if below, track starts hot. Use first detected beat as cue.
    if mix_in_time >= 4.0:
        cues.append({
            "name": "Mix In",
            "time": round(mix_in_time, 3),
            "type": "mix_in",
        })
    else:
        # Snap to first beat (first downbeat for DJ cueing)
        cues.append({
            "name": "Drop In",
            "time": round(float(beats[0]), 3),
            "type": "mix_in",
        })

    # Find structural transitions
    rises, drops = _find_transitions(smoothed, beats)

    # Drops (energy rises = musical drops)
    for i, (beat_idx, _) in enumerate(rises[:2]):
        if beat_idx < len(beats):
            label = "Drop" if i == 0 else f"Drop {i + 1}"
            cues.append({
                "name": label,
                "time": round(float(beats[beat_idx]), 3),
                "type": "drop",
            })

    # Breakdowns (energy drops)
    for i, (beat_idx, _) in enumerate(drops[:2]):
        if beat_idx < len(beats):
            label = "Breakdown" if i == 0 else f"Breakdown {i + 1}"
            cues.append({
                "name": label,
                "time": round(float(beats[beat_idx]), 3),
                "type": "breakdown",
            })

    # Mix-out: last point where energy drops below 30% of range from the end
    mix_out_threshold = min_e + energy_range * 0.3
    mix_out_beat = len(smoothed) - 1
    for i in range(len(smoothed) - 1, -1, -1):
        if smoothed[i] > mix_out_threshold:
            mix_out_beat = min(len(beats) - 1, i + 4)
            break

    # Only add mix-out if it's meaningfully before the end
    track_duration = float(beats[-1])
    mix_out_time = float(beats[mix_out_beat])
    if mix_out_time < track_duration - 5.0:
        cues.append({
            "name": "Mix Out",
            "time": round(mix_out_time, 3),
            "type": "mix_out",
        })

    # If fewer than 2 structural transitions (flat/monotone energy), add section markers
    structural_cues = [c for c in cues if c["type"] in ("drop", "breakdown")]
    if len(structural_cues) < 2 and len(beats) > 128:
        existing_times = [c["time"] for c in cues]
        section_interval = 64
        section_num = 0
        for i in range(section_interval, len(beats) - section_interval, section_interval):
            t = round(float(beats[i]), 3)
            # Skip if too close to existing cue (within 8 beats)
            beat_duration = float(beats[1] - beats[0]) if len(beats) > 1 else 0.5
            min_gap = beat_duration * 8
            if any(abs(t - et) < min_gap for et in existing_times):
                continue
            section_num += 1
            cues.append({
                "name": f"Section {section_num}",
                "time": t,
                "type": "buildup",
            })
            existing_times.append(t)
            if len(cues) >= 6:
                break

    cues.sort(key=lambda c: c["time"])
    return cues


def format_cues_log(cues: list) -> str:
    if not cues:
        return "No cues detected"
    parts = [f"{c['name']} @ {c['time']:.1f}s" for c in cues]
    return "  │  ".join(parts)
