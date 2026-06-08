"""
tags.py — Unified tag reading/writing for all audio formats.

Single source of truth for file tag operations across TrackStage.
Handles MP3 (ID3v2.3), FLAC (Vorbis), AIFF (ID3), M4A (MP4 atoms).
"""

import logging
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TCON, TPUB, TDRC, TYER, TALB, COMM, TXXX, TIT1, TIT2, TPE1,
    TKEY, TBPM, TRCK,
)
from mutagen.flac import FLAC
from mutagen.aiff import AIFF
from mutagen.mp4 import MP4

log = logging.getLogger(__name__)

# Supported audio file extensions
EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}

# Compound vibe words that should be stripped during comment merge
VIBE_WORDS = {"dark", "euphoric", "deep", "melancholic", "driving"}


# ── Public API ─────────────────────────────────────────────────────────────────


def read_tags(fp: Path) -> dict:
    """Read artist/title from file tags, fallback to filename parsing."""
    ext = fp.suffix.lower()
    tags = {"artist": "", "title": ""}
    try:
        if ext == ".mp3":
            a = MP3(fp, ID3=ID3)
            if a.tags:
                tags["artist"] = str(a.tags.get("TPE1", "")).strip()
                tags["title"] = str(a.tags.get("TIT2", "")).strip()
        elif ext == ".flac":
            a = FLAC(fp)
            tags["artist"] = ", ".join(a.get("artist", []))
            tags["title"] = ", ".join(a.get("title", []))
        elif ext in (".aiff", ".aif"):
            a = AIFF(fp)
            if a.tags:
                tags["artist"] = str(a.tags.get("TPE1", "")).strip()
                tags["title"] = str(a.tags.get("TIT2", "")).strip()
        elif ext == ".m4a":
            a = MP4(fp)
            tags["artist"] = ", ".join(a.get("\xa9ART", []))
            tags["title"] = ", ".join(a.get("\xa9nam", []))
    except Exception:
        pass

    # Fallback to filename parsing
    if not tags["title"]:
        stem = fp.stem
        if " - " in stem:
            parts = stem.split(" - ", 1)
            if not tags["artist"]:
                tags["artist"] = parts[0].strip()
            tags["title"] = parts[1].strip()
        else:
            tags["title"] = stem.strip()

    return tags


def read_comment(fp: Path) -> str:
    """Read the comment tag from a file."""
    ext = fp.suffix.lower()
    try:
        if ext == ".flac":
            a = FLAC(fp)
            return a.get("comment", [""])[0]
        elif ext == ".mp3":
            try:
                tags = ID3(fp)
            except ID3NoHeaderError:
                return ""
            return str(tags.get("COMM::eng", ""))
        elif ext in (".aiff", ".aif"):
            a = AIFF(fp)
            if a.tags and "COMM::eng" in a.tags:
                return str(a.tags["COMM::eng"])
            return ""
        elif ext == ".m4a":
            a = MP4(fp)
            cmt = a.get("\xa9cmt")
            return cmt[0] if cmt else ""
    except Exception:
        pass
    return ""


def build_comment(meta: dict) -> str:
    """Build pipe-delimited comment from metadata dict.

    Format: Genre1, Genre2 | Cat# XYZ | Energy: 5/10 | Dance: 6/10 | deep, driving | instrumental
    """
    parts = [p for p in [
        meta.get("styles", ""),
        f"Cat# {meta['catno']}" if meta.get("catno") else "",
        f"Energy: {meta['energy']}/10" if meta.get("energy") else "",
        f"Dance: {meta['danceability']}/10" if meta.get("danceability") else "",
        meta.get("vibes", ""),
        meta.get("vocal", ""),
    ] if p]
    return " | ".join(parts)


def merge_comment(existing: str, energy: str, dance: str, vibes: str, vocal: str) -> str:
    """Merge analysis fields into existing comment without clobbering Discogs data.

    Rules:
    - Strip old analysis fields (Energy:, Dance:, vibes, vocal) from existing
    - Preserve Discogs data (genres, catno)
    - Append new analysis fields
    """
    parts = [p.strip() for p in existing.split(" | ")]
    cleaned = []
    for p in parts:
        if p.startswith("Energy:") or p.startswith("Dance:"):
            continue
        if p in ("instrumental", "voice", ""):
            continue
        # Skip if all comma-separated parts are known vibes (handles "dark, driving")
        sub_parts = [s.strip().lower() for s in p.split(",")]
        if all(s in VIBE_WORDS for s in sub_parts if s):
            continue
        cleaned.append(p)

    # Add new analysis fields
    if energy:
        cleaned.append(f"Energy: {energy}/10")
    if dance:
        cleaned.append(f"Dance: {dance}/10")
    if vibes:
        cleaned.append(vibes)
    if vocal:
        cleaned.append(vocal)

    return " | ".join(cleaned)


def build_grouping(r: dict) -> str:
    """Combine vibes + moods, no duplicates.

    E.g. "deep, driving, relaxed, party"
    """
    parts = list(r.get("vibes", []))
    for m in r.get("moods", []):
        if m not in parts:
            parts.append(m)
    return ", ".join(parts)


def write_tags(fp: Path, meta: dict, dry_run: bool = False) -> bool:
    """Write FULL metadata (Discogs + analysis) to file tags."""
    writers = {
        ".mp3": _write_mp3_full,
        ".flac": _write_flac_full,
        ".aiff": _write_aiff_full,
        ".aif": _write_aiff_full,
        ".m4a": _write_m4a_full,
    }
    fn = writers.get(fp.suffix.lower())
    if fn:
        return fn(fp, meta, dry_run)
    log.warning(f"  No tag writer for format: {fp.suffix}")
    return False


def write_analysis_tags(fp: Path, r: dict) -> bool:
    """Write analysis-only fields, preserve existing metadata.

    Writes: BPM, key, energy, danceability, grouping, comment (merged).
    """
    ext = fp.suffix.lower()
    try:
        if ext == '.flac':
            _write_flac_analysis(fp, r)
        elif ext == '.mp3':
            _write_mp3_analysis(fp, r)
        elif ext in ('.aiff', '.aif'):
            _write_aiff_analysis(fp, r)
        elif ext == '.m4a':
            _write_m4a_analysis(fp, r)
        else:
            return False
        return True
    except Exception as e:
        log.error(f"  Tag write failed ({fp.name}): {e}")
        return False


# ── Full tag writers (Discogs + analysis) ──────────────────────────────────────


def _write_mp3_full(fp, meta, dry_run):
    try:
        try:
            tags = ID3(fp)
        except ID3NoHeaderError:
            tags = ID3()
        if meta.get("genre"):
            tags["TCON"] = TCON(encoding=3, text=meta["genre"])
        if meta.get("album"):
            tags["TALB"] = TALB(encoding=3, text=meta["album"])
        if meta.get("label"):
            tags["TPUB"] = TPUB(encoding=3, text=meta["label"])
        if meta.get("year"):
            tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
            tags["TYER"] = TYER(encoding=3, text=meta["year"])
        if meta.get("catno"):
            tags["TXXX:CATALOGNUMBER"] = TXXX(
                encoding=3, desc="CATALOGNUMBER", text=meta["catno"]
            )
        if meta.get("initial_key"):
            tags["TKEY"] = TKEY(encoding=3, text=meta["initial_key"])
        if meta.get("bpm"):
            tags["TBPM"] = TBPM(encoding=3, text=meta["bpm"])
        if meta.get("energy"):
            tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=meta["energy"])
        if meta.get("danceability"):
            tags["TXXX:DANCEABILITY"] = TXXX(
                encoding=3, desc="DANCEABILITY", text=meta["danceability"]
            )
        # Grouping from vibes + moods
        grouping = build_grouping(meta) if meta.get("vibes") or meta.get("moods") else ""
        if grouping:
            tags["TIT1"] = TIT1(encoding=3, text=grouping)
        c = build_comment(meta)
        if c:
            tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=c)
        if not dry_run:
            tags.save(fp, v2_version=3)
        return True
    except Exception as e:
        log.error(f"  MP3 write failed: {e}")
        return False


def _write_flac_full(fp, meta, dry_run):
    try:
        a = FLAC(fp)
        if meta.get("genre"):
            a["genre"] = [meta["genre"]]
        if meta.get("styles"):
            a["style"] = [meta["styles"]]
        if meta.get("album"):
            a["album"] = [meta["album"]]
        if meta.get("label"):
            a["label"] = [meta["label"]]
            a["organization"] = [meta["label"]]
        if meta.get("catno"):
            a["catalognumber"] = [meta["catno"]]
        if meta.get("year"):
            a["date"] = [meta["year"]]
            a["year"] = [meta["year"]]
        if meta.get("initial_key"):
            a["initialkey"] = [meta["initial_key"]]
        if meta.get("bpm"):
            a["bpm"] = [meta["bpm"]]
        if meta.get("energy"):
            a["energy"] = [meta["energy"]]
        if meta.get("danceability"):
            a["danceability"] = [meta["danceability"]]
        # Grouping from vibes + moods
        grouping = build_grouping(meta) if meta.get("vibes") or meta.get("moods") else ""
        if grouping:
            a["grouping"] = [grouping]
        c = build_comment(meta)
        if c:
            a["comment"] = [c]
        if not dry_run:
            a.save()
        return True
    except Exception as e:
        log.error(f"  FLAC write failed: {e}")
        return False


def _write_aiff_full(fp, meta, dry_run):
    try:
        a = AIFF(fp)
        if a.tags is None:
            a.add_tags()
        if meta.get("genre"):
            a.tags["TCON"] = TCON(encoding=3, text=meta["genre"])
        if meta.get("album"):
            a.tags["TALB"] = TALB(encoding=3, text=meta["album"])
        if meta.get("label"):
            a.tags["TPUB"] = TPUB(encoding=3, text=meta["label"])
        if meta.get("year"):
            a.tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
        if meta.get("catno"):
            a.tags["TXXX:CATALOGNUMBER"] = TXXX(
                encoding=3, desc="CATALOGNUMBER", text=meta["catno"]
            )
        if meta.get("initial_key"):
            a.tags["TKEY"] = TKEY(encoding=3, text=meta["initial_key"])
        if meta.get("bpm"):
            a.tags["TBPM"] = TBPM(encoding=3, text=meta["bpm"])
        if meta.get("energy"):
            a.tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=meta["energy"])
        if meta.get("danceability"):
            a.tags["TXXX:DANCEABILITY"] = TXXX(
                encoding=3, desc="DANCEABILITY", text=meta["danceability"]
            )
        # Grouping from vibes + moods
        grouping = build_grouping(meta) if meta.get("vibes") or meta.get("moods") else ""
        if grouping:
            a.tags["TIT1"] = TIT1(encoding=3, text=grouping)
        c = build_comment(meta)
        if c:
            a.tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=c)
        if not dry_run:
            a.save()
        return True
    except Exception as e:
        log.error(f"  AIFF write failed: {e}")
        return False


def _write_m4a_full(fp, meta, dry_run):
    try:
        a = MP4(fp)
        if meta.get("genre"):
            a["\xa9gen"] = [meta["genre"]]
        if meta.get("album"):
            a["\xa9alb"] = [meta["album"]]
        if meta.get("year"):
            a["\xa9day"] = [meta["year"]]
        if meta.get("label"):
            a["----:com.apple.iTunes:LABEL"] = [meta["label"].encode("utf-8")]
        if meta.get("catno"):
            a["----:com.apple.iTunes:CATALOGNUMBER"] = [meta["catno"].encode("utf-8")]
        if meta.get("styles"):
            a["----:com.apple.iTunes:STYLE"] = [meta["styles"].encode("utf-8")]
        if meta.get("initial_key"):
            a["----:com.apple.iTunes:INITIALKEY"] = [meta["initial_key"].encode("utf-8")]
        if meta.get("bpm"):
            a["tmpo"] = [int(float(meta["bpm"]))]
        if meta.get("energy"):
            a["----:com.apple.iTunes:ENERGY"] = [meta["energy"].encode("utf-8")]
        if meta.get("danceability"):
            a["----:com.apple.iTunes:DANCEABILITY"] = [meta["danceability"].encode("utf-8")]
        # Grouping from vibes + moods
        grouping = build_grouping(meta) if meta.get("vibes") or meta.get("moods") else ""
        if grouping:
            a["\xa9grp"] = [grouping]
        c = build_comment(meta)
        if c:
            a["\xa9cmt"] = [c]
        if not dry_run:
            a.save()
        return True
    except Exception as e:
        log.error(f"  M4A write failed: {e}")
        return False


# ── Analysis-only tag writers (preserve existing Discogs metadata) ─────────────


def _write_flac_analysis(fp, r):
    a = FLAC(fp)
    if r.get("bpm"):
        a["bpm"] = [r["bpm"]]
    if r.get("camelot"):
        a["initialkey"] = [r["camelot"]]
    if r.get("energy"):
        a["energy"] = [r["energy"]]
    if r.get("danceability"):
        a["danceability"] = [r["danceability"]]
    grouping = build_grouping(r)
    if grouping:
        a["grouping"] = [grouping]
    existing = a.get("comment", [""])[0]
    new = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if new:
        a["comment"] = [new]
    a.save()


def _write_mp3_analysis(fp, r):
    try:
        tags = ID3(fp)
    except ID3NoHeaderError:
        tags = ID3()
    if r.get("camelot"):
        tags["TKEY"] = TKEY(encoding=3, text=r["camelot"])
    if r.get("bpm"):
        tags["TBPM"] = TBPM(encoding=3, text=r["bpm"])
    if r.get("energy"):
        tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=r["energy"])
    if r.get("danceability"):
        tags["TXXX:DANCEABILITY"] = TXXX(
            encoding=3, desc="DANCEABILITY", text=r["danceability"]
        )
    grouping = build_grouping(r)
    if grouping:
        tags["TIT1"] = TIT1(encoding=3, text=grouping)
    existing = ""
    if "COMM::eng" in tags:
        existing = str(tags["COMM::eng"])
    new = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if new:
        tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=new)
    tags.save(fp, v2_version=3)


def _write_aiff_analysis(fp, r):
    a = AIFF(fp)
    if a.tags is None:
        a.add_tags()
    if r.get("camelot"):
        a.tags["TKEY"] = TKEY(encoding=3, text=r["camelot"])
    if r.get("bpm"):
        a.tags["TBPM"] = TBPM(encoding=3, text=r["bpm"])
    if r.get("energy"):
        a.tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=r["energy"])
    if r.get("danceability"):
        a.tags["TXXX:DANCEABILITY"] = TXXX(
            encoding=3, desc="DANCEABILITY", text=r["danceability"]
        )
    grouping = build_grouping(r)
    if grouping:
        a.tags["TIT1"] = TIT1(encoding=3, text=grouping)
    existing = ""
    if "COMM::eng" in a.tags:
        existing = str(a.tags["COMM::eng"])
    new = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if new:
        a.tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=new)
    a.save()


def _write_m4a_analysis(fp, r):
    a = MP4(fp)
    if r.get("bpm"):
        a["tmpo"] = [int(float(r["bpm"]))]
    if r.get("camelot"):
        a["----:com.apple.iTunes:INITIALKEY"] = [r["camelot"].encode("utf-8")]
    if r.get("energy"):
        a["----:com.apple.iTunes:ENERGY"] = [r["energy"].encode("utf-8")]
    if r.get("danceability"):
        a["----:com.apple.iTunes:DANCEABILITY"] = [r["danceability"].encode("utf-8")]
    grouping = build_grouping(r)
    if grouping:
        a["\xa9grp"] = [grouping]
    existing = a.get("\xa9cmt", [""])[0] if a.get("\xa9cmt") else ""
    new = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if new:
        a["\xa9cmt"] = [new]
    a.save()
