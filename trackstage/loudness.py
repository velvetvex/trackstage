"""
loudness.py — EBU R128 loudness measurement and ReplayGain tag writing.

Measures integrated loudness per track and writes REPLAYGAIN_TRACK_GAIN/PEAK tags
so Rekordbox (and other software) can auto-level playback without modifying audio.
"""

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

TARGET_LUFS = -9.0


def measure_loudness(file_path: Path) -> dict:
    """Measure EBU R128 integrated loudness and true peak."""
    from essentia.standard import AudioLoader, LoudnessEBUR128

    result = {"lufs": None, "peak": None, "gain_db": None, "range_lu": None}

    try:
        audio, sr, _, _, _, _ = AudioLoader(filename=str(file_path))()
    except Exception as e:
        log.warning(f"  ⚠  Could not load audio for loudness: {e}")
        return result

    try:
        _, _, integrated, loudness_range = LoudnessEBUR128(sampleRate=sr)(audio)
    except Exception as e:
        log.warning(f"  ⚠  Loudness measurement failed: {e}")
        return result

    peak = float(np.max(np.abs(audio)))
    gain = TARGET_LUFS - integrated

    result["lufs"] = round(integrated, 2)
    result["peak"] = round(peak, 6)
    result["gain_db"] = round(gain, 2)
    result["range_lu"] = round(loudness_range, 2)

    return result


def write_replaygain_tags(file_path: Path, loudness: dict) -> bool:
    """Write ReplayGain tags to audio file. Returns True on success."""
    if loudness["gain_db"] is None:
        return False

    gain_str = f"{loudness['gain_db']:+.2f} dB"
    peak_str = f"{loudness['peak']:.6f}"

    ext = file_path.suffix.lower()

    try:
        if ext == ".mp3":
            _write_mp3(file_path, gain_str, peak_str)
        elif ext == ".flac":
            _write_flac(file_path, gain_str, peak_str)
        elif ext == ".aiff" or ext == ".aif":
            _write_aiff(file_path, gain_str, peak_str)
        elif ext == ".m4a":
            _write_m4a(file_path, gain_str, peak_str)
        else:
            log.warning(f"  ⚠  Unsupported format for ReplayGain: {ext}")
            return False
        return True
    except Exception as e:
        log.warning(f"  ⚠  Failed to write ReplayGain tags: {e}")
        return False


def _write_mp3(file_path: Path, gain_str: str, peak_str: str):
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TXXX

    f = MP3(file_path, ID3=ID3)
    if f.tags is None:
        f.add_tags()
    f.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=[gain_str]))
    f.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text=[peak_str]))
    f.save()


def _write_flac(file_path: Path, gain_str: str, peak_str: str):
    from mutagen.flac import FLAC

    f = FLAC(file_path)
    f["REPLAYGAIN_TRACK_GAIN"] = gain_str
    f["REPLAYGAIN_TRACK_PEAK"] = peak_str
    f.save()


def _write_aiff(file_path: Path, gain_str: str, peak_str: str):
    from mutagen.aiff import AIFF
    from mutagen.id3 import TXXX

    f = AIFF(file_path)
    if f.tags is None:
        f.add_tags()
    f.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=[gain_str]))
    f.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text=[peak_str]))
    f.save()


def _write_m4a(file_path: Path, gain_str: str, peak_str: str):
    from mutagen.mp4 import MP4

    f = MP4(file_path)
    if f.tags is None:
        f.add_tags()
    # M4A uses freeform atoms for custom tags
    f.tags["----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN"] = [
        gain_str.encode("utf-8")
    ]
    f.tags["----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK"] = [
        peak_str.encode("utf-8")
    ]
    f.save()


def format_loudness_log(loudness: dict) -> str:
    if loudness["lufs"] is None:
        return "No loudness data"
    peak_db = 20 * np.log10(loudness["peak"]) if loudness["peak"] > 1e-10 else -100.0
    clip_warn = " ⚠ CLIP" if loudness["gain_db"] + peak_db > 0 else ""
    return (
        f"LUFS: {loudness['lufs']:.1f}  │  "
        f"Gain: {loudness['gain_db']:+.1f} dB  │  "
        f"Peak: {peak_db:.1f} dBFS  │  "
        f"Range: {loudness['range_lu']:.1f} LU{clip_warn}"
    )
