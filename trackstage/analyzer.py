"""
analyzer.py — Single-load audio analysis coordinator.

Loads audio once from disk and runs all analysis modules on the shared buffer.
~3x faster than calling each module independently (eliminates redundant decodes).
"""

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


def analyze_track(file_path: Path, existing_key: str = "") -> dict:
    """Run full analysis on a single track with single audio load.

    Returns dict with keys: bpm, key, camelot, key_confidence, energy,
    danceability, cues, moods, vibes, vocal, loudness.
    """
    import essentia
    essentia.log.infoActive = False
    essentia.log.warningActive = False

    from essentia.standard import (
        MonoLoader, AudioLoader, KeyExtractor, RhythmExtractor2013,
        Energy, Danceability, OnsetRate,
    )

    result = {
        "bpm": "", "key": "", "camelot": "", "key_confidence": "",
        "energy": "", "danceability": "",
        "cues": [], "moods": [], "vibes": [], "vocal": "",
        "loudness": None,
    }

    # ── Single audio load ────────────────────────────────────────────────────
    try:
        audio_mono = MonoLoader(filename=str(file_path), sampleRate=44100)()
    except Exception as e:
        log.warning(f"  ⚠  Could not load audio: {e}")
        return result

    # ── Key ──────────────────────────────────────────────────────────────────
    from .audio_analysis import to_camelot, _parse_key_string, CAMELOT
    try:
        key, scale, key_strength = KeyExtractor()(audio_mono)
        result["key_confidence"] = str(round(key_strength, 2))
        if key_strength >= 0.5 or not existing_key:
            result["key"] = f"{key} {scale}"
            result["camelot"] = to_camelot(key, scale)
        else:
            result["key"] = existing_key
            ek = _parse_key_string(existing_key)
            if ek:
                result["camelot"] = to_camelot(*ek)
    except Exception as e:
        log.warning(f"  ⚠  Key detection failed: {e}")
        if existing_key:
            result["key"] = existing_key
            ek = _parse_key_string(existing_key)
            if ek:
                result["camelot"] = to_camelot(*ek)

    # ── Energy ───────────────────────────────────────────────────────────────
    from .audio_analysis import ENERGY_P5, ENERGY_P95
    try:
        frame_size = 2048
        hop_size = 1024
        energy_algo = Energy()
        total_energy = 0.0
        n_frames = 0
        for i in range(0, len(audio_mono) - frame_size, hop_size):
            total_energy += energy_algo(audio_mono[i:i + frame_size])
            n_frames += 1
        if n_frames > 0:
            avg_energy = total_energy / n_frames
            normalized = (avg_energy - ENERGY_P5) / (ENERGY_P95 - ENERGY_P5)
            scaled = min(10, max(1, round(1 + normalized * 9)))
            result["energy"] = str(scaled)
    except Exception as e:
        log.warning(f"  ⚠  Energy analysis failed: {e}")

    # ── OnsetRate (computed once, used for BPM gate + danceability) ───────────
    onset_rate = None
    try:
        _, onset_rate = OnsetRate()(audio_mono)
    except Exception:
        pass

    # ── BPM + beat ticks (reused for cue detection — eliminates duplicate beat tracking) ─
    from .audio_analysis import BPM_FLOOR, BPM_CEILING
    beats = np.array([])
    try:
        bpm, ticks, rhythm_conf, _, _ = RhythmExtractor2013(method="multifeature")(audio_mono)
        beats = ticks
        if rhythm_conf < 0.3:
            result["bpm"] = ""
        else:
            if bpm < BPM_FLOOR and bpm * 2 <= BPM_CEILING:
                energy_val = int(result.get("energy") or "0")
                if energy_val >= 7:
                    bpm = bpm * 2
                elif onset_rate and onset_rate > 4.5 and rhythm_conf < 2.5:
                    bpm = bpm * 2
            result["bpm"] = str(round(bpm, 1))
    except Exception as e:
        log.warning(f"  ⚠  BPM detection failed: {e}")

    # ── Danceability ─────────────────────────────────────────────────────────
    from .audio_analysis import DANCE_P5, DANCE_P95, ONSET_RATE_P5, ONSET_RATE_P95
    try:
        dance_val, _ = Danceability()(audio_mono)
        dfa_norm = max(0.0, min(1.0, (dance_val - DANCE_P5) / (DANCE_P95 - DANCE_P5)))

        if onset_rate is not None:
            onset_norm = max(0.0, min(1.0,
                (onset_rate - ONSET_RATE_P5) / (ONSET_RATE_P95 - ONSET_RATE_P5)))
        else:
            onset_norm = dfa_norm

        blended = 0.5 * dfa_norm + 0.5 * onset_norm
        scaled = min(10, max(1, round(1 + blended * 9)))
        result["danceability"] = str(scaled)
    except Exception as e:
        log.warning(f"  ⚠  Danceability analysis failed: {e}")

    # ── Cue detection (reuses beats from RhythmExtractor — no second beat tracker) ─
    from .cue_detection import (
        _beat_energy_contour, _smooth, _find_transitions, _dedupe, CUE_COLORS,
    )
    try:
        if len(beats) >= 32:
            beat_energy = _beat_energy_contour(audio_mono, beats)
            smoothed = _smooth(beat_energy, window=16)
            max_e = np.max(smoothed)
            min_e = np.min(smoothed)
            energy_range = max_e - min_e

            cues = []
            if energy_range > 1e-6:
                # Mix-in
                mix_in_threshold = min_e + energy_range * 0.2
                mix_in_beat = 0
                for i, e in enumerate(smoothed):
                    if e > mix_in_threshold:
                        mix_in_beat = max(0, i - 4)
                        break
                mix_in_time = float(beats[mix_in_beat])
                if mix_in_time >= 4.0:
                    cues.append({"name": "Mix In", "time": round(mix_in_time, 3), "type": "mix_in"})
                else:
                    cues.append({"name": "Drop In", "time": round(float(beats[0]), 3), "type": "mix_in"})

                # Structural transitions
                rises, drops = _find_transitions(smoothed, beats)
                for i, (beat_idx, _) in enumerate(rises[:2]):
                    if beat_idx < len(beats):
                        label = "Drop" if i == 0 else f"Drop {i + 1}"
                        cues.append({"name": label, "time": round(float(beats[beat_idx]), 3), "type": "drop"})
                for i, (beat_idx, _) in enumerate(drops[:2]):
                    if beat_idx < len(beats):
                        label = "Breakdown" if i == 0 else f"Breakdown {i + 1}"
                        cues.append({"name": label, "time": round(float(beats[beat_idx]), 3), "type": "breakdown"})

                # Mix-out
                mix_out_threshold = min_e + energy_range * 0.3
                mix_out_beat = len(smoothed) - 1
                for i in range(len(smoothed) - 1, -1, -1):
                    if smoothed[i] > mix_out_threshold:
                        mix_out_beat = min(len(beats) - 1, i + 4)
                        break
                track_duration = float(beats[-1])
                mix_out_time = float(beats[mix_out_beat])
                if mix_out_time < track_duration - 5.0:
                    cues.append({"name": "Mix Out", "time": round(mix_out_time, 3), "type": "mix_out"})

                # Section markers for flat-energy tracks
                structural_cues = [c for c in cues if c["type"] in ("drop", "breakdown")]
                if len(structural_cues) < 2 and len(beats) > 128:
                    existing_times = [c["time"] for c in cues]
                    section_interval = 64
                    section_num = 0
                    beat_duration = float(beats[1] - beats[0]) if len(beats) > 1 else 0.5
                    min_gap = beat_duration * 8
                    for i in range(section_interval, len(beats) - section_interval, section_interval):
                        t = round(float(beats[i]), 3)
                        if any(abs(t - et) < min_gap for et in existing_times):
                            continue
                        section_num += 1
                        cues.append({"name": f"Section {section_num}", "time": t, "type": "buildup"})
                        existing_times.append(t)
                        if len(cues) >= 6:
                            break

            cues.sort(key=lambda c: c["time"])
            result["cues"] = cues
    except Exception as e:
        log.warning(f"  ⚠  Cue detection failed: {e}")

    # ── Mood detection (needs 16kHz reload — unavoidable for TF model) ───────
    try:
        from .mood_detection import detect_mood, ENERGY_GATED_VIBES
        energy_int = int(result["energy"]) if result["energy"] else 5
        mood = detect_mood(file_path, energy=energy_int)
        result["moods"] = mood["moods"]
        result["vibes"] = mood["vibes"]
        result["vocal"] = mood["vocal"]
    except Exception as e:
        log.warning(f"  ⚠  Mood detection failed: {e}")

    # ── Loudness (needs stereo reload — unavoidable for EBU R128) ────────────
    try:
        from .loudness import measure_loudness
        loudness = measure_loudness(file_path)
        result["loudness"] = loudness
    except Exception as e:
        log.warning(f"  ⚠  Loudness measurement failed: {e}")

    return result


def analyze_batch(
    file_paths: list[Path],
    existing_keys: Optional[dict[str, str]] = None,
    jobs: int = 1,
    callback=None,
) -> list[dict]:
    """Analyze multiple tracks, optionally in parallel.

    Args:
        file_paths: List of audio file paths.
        existing_keys: Optional map of filepath→existing key string.
        jobs: Number of parallel workers (1 = sequential).
        callback: Optional callable(path, result) invoked per completed track.
    """
    existing_keys = existing_keys or {}

    if jobs <= 1:
        results = []
        for fp in file_paths:
            r = analyze_track(fp, existing_key=existing_keys.get(str(fp), ""))
            if callback:
                callback(fp, r)
            results.append(r)
        return results

    # Parallel execution
    results = [None] * len(file_paths)
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        future_to_idx = {}
        for i, fp in enumerate(file_paths):
            ek = existing_keys.get(str(fp), "")
            future = executor.submit(analyze_track, fp, ek)
            future_to_idx[future] = (i, fp)

        for future in as_completed(future_to_idx):
            idx, fp = future_to_idx[future]
            try:
                r = future.result()
                results[idx] = r
                if callback:
                    callback(fp, r)
            except Exception as e:
                log.warning(f"  ⚠  Failed: {fp.name}: {e}")
                results[idx] = {}

    return results
