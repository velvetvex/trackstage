# Pipeline Cleanup & Rekordbox Tag System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicated code across pipeline.py/tags.py/xml.py/analyze_library.py/backfill_xml.py, consolidate into a clean module structure, and replace the current Rekordbox My Tag system with an analysis-driven design.

**Architecture:** pipeline.py stays as the Discogs+ingestion orchestrator but sheds all tag-writing, XML, and analysis code into focused modules. Three scripts (analyze_library.py, backfill_xml.py, sync_rekordbox.py) merge into one `sync.py` that reads cache and writes to file tags + XML + Rekordbox DB in a single pass. The Rekordbox My Tag schema is rebuilt from scratch using analysis data distributions.

**Tech Stack:** Python 3.12, mutagen, essentia, tensorflow, pyrekordbox, sqlcipher3, SQLite

---

## New My Tag Design

Based on analysis of 565-track cache and 1208-track collection:

### Vibe (replaces Quality — maps to ML mood/vibe detection)
| Tag | Source | Tracks |
|-----|--------|--------|
| Deep | vibe=deep | 257 |
| Dark | vibe=dark | 101 |
| Driving | vibe=driving | 111 |
| Euphoric | vibe=euphoric | 43 |
| Hypnotic | energy≤3 + deep | ~50 |
| Raw | energy≥7 + aggressive | ~40 |
| Melodic | danceability≤4 + not aggressive | ~60 |

### Situation (maps to energy tiers — drives set planning)
| Tag | Source |
|-----|--------|
| Ambient | energy 1-2 (143 tracks) |
| Warmup | energy 3-4 (194 tracks) |
| Groove | energy 5-6 (123 tracks) |
| Peak | energy 7-8 (72 tracks) |
| Rave | energy 9-10 (33 tracks) |

### Sound (replaces Components — maps to vocal + genre cues)
| Tag | Source |
|-----|--------|
| Vocal | vocal=voice (103 tracks) |
| Acid | genre contains "acid" |
| Dub | genre contains "dub" (not dubstep) |
| Breaks | genre contains "break" |

### Genre (kept — maps to Discogs genre in comments)
Same tags: House, Tech House, Techno, Disco, Electro, Dubstep, Breakbeat, Italo-Disco, DnB/Jungle, Rave, Ambient, Pop, Leftfield

Plus new: Trance, Garage, Minimal, IDM

---

## File Structure (Final State)

### Keep unchanged
- `trackstage/__init__.py` — package metadata
- `trackstage/__main__.py` — delegates to pipeline.main()
- `trackstage/analyzer.py` — single-load audio analysis coordinator
- `trackstage/audio_analysis.py` — essentia spectral analysis
- `trackstage/cue_detection.py` — structural cue detection
- `trackstage/loudness.py` — EBU R128 + ReplayGain
- `trackstage/mood_detection.py` — TF mood/vocal classification
- `trackstage/cache.py` — SQLite analysis cache

### Rewrite
- `trackstage/tags.py` — sole owner of all file tag read/write (absorbs from pipeline.py + analyze_library.py)
- `trackstage/xml.py` — sole owner of all Rekordbox XML operations (absorbs from pipeline.py)
- `trackstage/rekordbox.py` — NEW: Rekordbox DB operations (My Tags, Rating, Color)

### Slim down
- `trackstage/pipeline.py` — remove: tag writers, XML helpers, comment builders. Keep: DiscogsClient, scoring, file movement, CLI orchestration. Import from tags.py + xml.py.

### Scripts
- `scripts/analyze_library.py` — rewrite: analysis + cache only, no tag/XML writing
- `scripts/sync.py` — NEW: replaces backfill_xml.py + sync_rekordbox.py. Single pass: cache → file tags + XML + Rekordbox DB
- DELETE: `scripts/backfill_xml.py`, `scripts/sync_rekordbox.py`

---

## Task 1: Consolidate tags.py as sole tag writer

**Files:**
- Rewrite: `trackstage/tags.py`
- Modify: `trackstage/pipeline.py` (remove tag writers, import from tags.py)
- Test: `tests/test_tags.py`

- [ ] **Step 1: Write failing tests for the unified tag API**

```python
# tests/test_tags.py
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from trackstage.tags import (
    read_tags, build_comment, merge_comment,
    write_tags, write_analysis_tags,
)


def test_build_comment_full():
    meta = {
        "styles": ["House", "Deep House"],
        "catno": "CAT001",
        "energy": "7",
        "danceability": "6",
        "vibes": ["deep", "driving"],
        "vocal": "voice",
    }
    result = build_comment(meta)
    assert result == "House, Deep House | Cat# CAT001 | Energy: 7/10 | Dance: 6/10 | deep, driving | voice"


def test_build_comment_minimal():
    meta = {"styles": ["Techno"]}
    result = build_comment(meta)
    assert result == "Techno"


def test_build_comment_no_styles_with_catno():
    meta = {"catno": "XYZ01"}
    result = build_comment(meta)
    assert result == "Cat# XYZ01"


def test_merge_comment_preserves_discogs():
    existing = "House, Deep House | Cat# CAT001"
    result = merge_comment(existing, "5", "6", "deep", "instrumental")
    assert "House, Deep House" in result
    assert "Cat# CAT001" in result
    assert "Energy: 5/10" in result
    assert "Dance: 6/10" in result
    assert "deep" in result
    assert "instrumental" in result


def test_merge_comment_strips_old_analysis():
    existing = "Techno | Cat# X | Energy: 3/10 | Dance: 4/10 | dark | voice"
    result = merge_comment(existing, "7", "8", "driving", "instrumental")
    assert "Energy: 7/10" in result
    assert "Energy: 3/10" not in result
    assert "Dance: 8/10" in result
    assert "voice" not in result
    assert "Techno" in result
    assert "Cat# X" in result


def test_merge_comment_empty_existing():
    result = merge_comment("", "5", "6", "deep", "voice")
    assert result == "Energy: 5/10 | Dance: 6/10 | deep | voice"


def test_build_grouping():
    from trackstage.tags import build_grouping
    r = {"vibes": ["deep", "driving"], "moods": ["relaxed", "party"]}
    result = build_grouping(r)
    assert result == "deep, driving, relaxed, party"


def test_build_grouping_no_duplicates():
    from trackstage.tags import build_grouping
    r = {"vibes": ["deep"], "moods": ["deep"]}
    result = build_grouping(r)
    assert result == "deep"


def test_read_tags_returns_dict():
    result = read_tags(Path("/nonexistent/file.flac"))
    assert isinstance(result, dict)
    assert "artist" in result
    assert "title" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kaitlyn/dev/trackstage && .venv/bin/pytest tests/test_tags.py -v`
Expected: Failures (current tags.py has different signatures or missing functions)

- [ ] **Step 3: Rewrite tags.py**

Rewrite `trackstage/tags.py` to be the single source of truth for all tag operations. It must export:

```python
# trackstage/tags.py
"""Unified file tag read/write for all audio formats."""

from pathlib import Path
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.aiff import AIFF
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, TKEY, TBPM, COMM, TIT1, TPE1, TALB, TCON, TDRC, TPUB, TRCK

EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}


def read_tags(fp: Path) -> dict:
    """Extract artist and title from file tags, falling back to filename."""
    artist, title = "", ""
    try:
        ext = fp.suffix.lower()
        if ext == ".flac":
            f = FLAC(fp)
            artist = f.get("artist", [""])[0]
            title = f.get("title", [""])[0]
        elif ext == ".mp3":
            f = MP3(fp)
            if f.tags:
                artist = str(f.tags.get("TPE1", ""))
                title = str(f.tags.get("TIT2", ""))
        elif ext in (".aiff", ".aif"):
            f = AIFF(fp)
            if f.tags:
                artist = str(f.tags.get("TPE1", ""))
                title = str(f.tags.get("TIT2", ""))
        elif ext == ".m4a":
            f = MP4(fp)
            artist = f.get("\xa9ART", [""])[0]
            title = f.get("\xa9nam", [""])[0]
    except Exception:
        pass

    if not artist or not title:
        stem = fp.stem
        if " - " in stem:
            parts = stem.split(" - ", 1)
            artist = artist or parts[0].strip()
            title = title or parts[1].strip()
        else:
            title = title or stem

    return {"artist": artist, "title": title}


def build_comment(meta: dict) -> str:
    """Build pipe-delimited comment from metadata dict."""
    parts = []
    if meta.get("styles"):
        parts.append(", ".join(meta["styles"]))
    if meta.get("catno"):
        parts.append(f"Cat# {meta['catno']}")
    if meta.get("energy"):
        parts.append(f"Energy: {meta['energy']}/10")
    if meta.get("danceability"):
        parts.append(f"Dance: {meta['danceability']}/10")
    if meta.get("vibes"):
        vstr = meta["vibes"] if isinstance(meta["vibes"], str) else ", ".join(meta["vibes"])
        parts.append(vstr)
    if meta.get("vocal"):
        parts.append(meta["vocal"])
    return " | ".join(parts)


def merge_comment(existing: str, energy: str, dance: str, vibes: str, vocal: str) -> str:
    """Merge analysis fields into existing comment, preserving Discogs data."""
    parts = [p.strip() for p in existing.split(" | ")] if existing else []
    cleaned = []
    vibe_words = {"dark", "euphoric", "deep", "melancholic", "driving"}
    for p in parts:
        if p.startswith("Energy:") or p.startswith("Dance:"):
            continue
        if p in ("instrumental", "voice", ""):
            continue
        sub = [s.strip().lower() for s in p.split(",")]
        if all(s in vibe_words for s in sub if s):
            continue
        cleaned.append(p)

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
    """Combine vibes + moods into grouping string, no duplicates."""
    parts = list(r.get("vibes", []))
    for m in r.get("moods", []):
        if m not in parts:
            parts.append(m)
    return ", ".join(parts)


def write_tags(fp: Path, meta: dict, dry_run: bool = False) -> bool:
    """Write full Discogs + analysis metadata to file tags."""
    if dry_run:
        return True
    ext = fp.suffix.lower()
    try:
        if ext == ".flac":
            return _write_flac_full(fp, meta)
        elif ext == ".mp3":
            return _write_mp3_full(fp, meta)
        elif ext in (".aiff", ".aif"):
            return _write_aiff_full(fp, meta)
        elif ext == ".m4a":
            return _write_m4a_full(fp, meta)
        return False
    except Exception as e:
        print(f"  TAG ERROR: {fp.name}: {e}")
        return False


def write_analysis_tags(fp: Path, r: dict) -> bool:
    """Write analysis-only fields to file tags, preserving existing metadata."""
    ext = fp.suffix.lower()
    grouping = build_grouping(r)
    try:
        if ext == ".flac":
            return _write_flac_analysis(fp, r, grouping)
        elif ext == ".mp3":
            return _write_mp3_analysis(fp, r, grouping)
        elif ext in (".aiff", ".aif"):
            return _write_aiff_analysis(fp, r, grouping)
        elif ext == ".m4a":
            return _write_m4a_analysis(fp, r, grouping)
        return False
    except Exception as e:
        print(f"  TAG ERROR: {fp.name}: {e}")
        return False


# ── FLAC ──────────────────────────────────────────────────────────────

def _write_flac_full(fp: Path, meta: dict) -> bool:
    a = FLAC(fp)
    if meta.get("artist"):      a["artist"] = [meta["artist"]]
    if meta.get("title"):       a["title"] = [meta["title"]]
    if meta.get("album"):       a["album"] = [meta["album"]]
    if meta.get("year"):        a["date"] = [meta["year"]]
    if meta.get("label"):       a["organization"] = [meta["label"]]
    if meta.get("catno"):       a["catalognumber"] = [meta["catno"]]
    if meta.get("genre"):       a["genre"] = [meta["genre"]]
    if meta.get("tracknumber"): a["tracknumber"] = [meta["tracknumber"]]
    if meta.get("bpm"):         a["bpm"] = [meta["bpm"]]
    if meta.get("key"):         a["initialkey"] = [meta["key"]]
    if meta.get("energy"):      a["energy"] = [meta["energy"]]
    if meta.get("danceability"):a["danceability"] = [meta["danceability"]]
    comment = build_comment(meta)
    if comment:
        a["comment"] = [comment]
    grouping = build_grouping(meta)
    if grouping:
        a["grouping"] = [grouping]
    a.save()
    return True


def _write_flac_analysis(fp: Path, r: dict, grouping: str) -> bool:
    a = FLAC(fp)
    if r.get("bpm"):         a["bpm"] = [r["bpm"]]
    if r.get("camelot"):     a["initialkey"] = [r["camelot"]]
    if r.get("energy"):      a["energy"] = [r["energy"]]
    if r.get("danceability"):a["danceability"] = [r["danceability"]]
    if grouping:             a["grouping"] = [grouping]
    existing = a.get("comment", [""])[0]
    merged = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if merged:
        a["comment"] = [merged]
    a.save()
    return True


# ── MP3 ───────────────────────────────────────────────────────────────

def _write_mp3_full(fp: Path, meta: dict) -> bool:
    try:
        tags = ID3(fp)
    except ID3NoHeaderError:
        tags = ID3()
    if meta.get("artist"):      tags["TPE1"] = TPE1(encoding=3, text=meta["artist"])
    if meta.get("album"):       tags["TALB"] = TALB(encoding=3, text=meta["album"])
    if meta.get("genre"):       tags["TCON"] = TCON(encoding=3, text=meta["genre"])
    if meta.get("year"):        tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
    if meta.get("label"):       tags["TPUB"] = TPUB(encoding=3, text=meta["label"])
    if meta.get("tracknumber"): tags["TRCK"] = TRCK(encoding=3, text=meta["tracknumber"])
    if meta.get("key"):         tags["TKEY"] = TKEY(encoding=3, text=meta["key"])
    if meta.get("bpm"):         tags["TBPM"] = TBPM(encoding=3, text=meta["bpm"])
    if meta.get("energy"):      tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=meta["energy"])
    if meta.get("danceability"):tags["TXXX:DANCEABILITY"] = TXXX(encoding=3, desc="DANCEABILITY", text=meta["danceability"])
    if meta.get("catno"):       tags["TXXX:CATALOGNUMBER"] = TXXX(encoding=3, desc="CATALOGNUMBER", text=meta["catno"])
    comment = build_comment(meta)
    if comment:
        tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=comment)
    grouping = build_grouping(meta)
    if grouping:
        tags["TIT1"] = TIT1(encoding=3, text=grouping)
    tags.save(fp, v2_version=3)
    return True


def _write_mp3_analysis(fp: Path, r: dict, grouping: str) -> bool:
    try:
        tags = ID3(fp)
    except ID3NoHeaderError:
        tags = ID3()
    if r.get("camelot"):     tags["TKEY"] = TKEY(encoding=3, text=r["camelot"])
    if r.get("bpm"):         tags["TBPM"] = TBPM(encoding=3, text=r["bpm"])
    if r.get("energy"):      tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=r["energy"])
    if r.get("danceability"):tags["TXXX:DANCEABILITY"] = TXXX(encoding=3, desc="DANCEABILITY", text=r["danceability"])
    if grouping:             tags["TIT1"] = TIT1(encoding=3, text=grouping)
    existing = str(tags.get("COMM::eng", ""))
    merged = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if merged:
        tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=merged)
    tags.save(fp, v2_version=3)
    return True


# ── AIFF ──────────────────────────────────────────────────────────────

def _write_aiff_full(fp: Path, meta: dict) -> bool:
    a = AIFF(fp)
    if a.tags is None:
        a.add_tags()
    if meta.get("artist"):      a.tags["TPE1"] = TPE1(encoding=3, text=meta["artist"])
    if meta.get("album"):       a.tags["TALB"] = TALB(encoding=3, text=meta["album"])
    if meta.get("genre"):       a.tags["TCON"] = TCON(encoding=3, text=meta["genre"])
    if meta.get("year"):        a.tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
    if meta.get("label"):       a.tags["TPUB"] = TPUB(encoding=3, text=meta["label"])
    if meta.get("tracknumber"): a.tags["TRCK"] = TRCK(encoding=3, text=meta["tracknumber"])
    if meta.get("key"):         a.tags["TKEY"] = TKEY(encoding=3, text=meta["key"])
    if meta.get("bpm"):         a.tags["TBPM"] = TBPM(encoding=3, text=meta["bpm"])
    if meta.get("energy"):      a.tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=meta["energy"])
    if meta.get("danceability"):a.tags["TXXX:DANCEABILITY"] = TXXX(encoding=3, desc="DANCEABILITY", text=meta["danceability"])
    if meta.get("catno"):       a.tags["TXXX:CATALOGNUMBER"] = TXXX(encoding=3, desc="CATALOGNUMBER", text=meta["catno"])
    comment = build_comment(meta)
    if comment:
        a.tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=comment)
    grouping = build_grouping(meta)
    if grouping:
        a.tags["TIT1"] = TIT1(encoding=3, text=grouping)
    a.save()
    return True


def _write_aiff_analysis(fp: Path, r: dict, grouping: str) -> bool:
    a = AIFF(fp)
    if a.tags is None:
        a.add_tags()
    if r.get("camelot"):     a.tags["TKEY"] = TKEY(encoding=3, text=r["camelot"])
    if r.get("bpm"):         a.tags["TBPM"] = TBPM(encoding=3, text=r["bpm"])
    if r.get("energy"):      a.tags["TXXX:ENERGY"] = TXXX(encoding=3, desc="ENERGY", text=r["energy"])
    if r.get("danceability"):a.tags["TXXX:DANCEABILITY"] = TXXX(encoding=3, desc="DANCEABILITY", text=r["danceability"])
    if grouping:             a.tags["TIT1"] = TIT1(encoding=3, text=grouping)
    existing = str(a.tags.get("COMM::eng", ""))
    merged = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if merged:
        a.tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=merged)
    a.save()
    return True


# ── M4A ───────────────────────────────────────────────────────────────

def _write_m4a_full(fp: Path, meta: dict) -> bool:
    a = MP4(fp)
    if meta.get("artist"):      a["\xa9ART"] = [meta["artist"]]
    if meta.get("album"):       a["\xa9alb"] = [meta["album"]]
    if meta.get("genre"):       a["\xa9gen"] = [meta["genre"]]
    if meta.get("year"):        a["\xa9day"] = [meta["year"]]
    if meta.get("tracknumber"): a["trkn"] = [(int(meta["tracknumber"]), 0)]
    if meta.get("bpm"):         a["tmpo"] = [int(float(meta["bpm"]))]
    if meta.get("key"):         a["----:com.apple.iTunes:INITIALKEY"] = [meta["key"].encode("utf-8")]
    if meta.get("energy"):      a["----:com.apple.iTunes:ENERGY"] = [meta["energy"].encode("utf-8")]
    if meta.get("danceability"):a["----:com.apple.iTunes:DANCEABILITY"] = [meta["danceability"].encode("utf-8")]
    if meta.get("label"):       a["----:com.apple.iTunes:LABEL"] = [meta["label"].encode("utf-8")]
    if meta.get("catno"):       a["----:com.apple.iTunes:CATALOGNUMBER"] = [meta["catno"].encode("utf-8")]
    comment = build_comment(meta)
    if comment:
        a["\xa9cmt"] = [comment]
    grouping = build_grouping(meta)
    if grouping:
        a["\xa9grp"] = [grouping]
    a.save()
    return True


def _write_m4a_analysis(fp: Path, r: dict, grouping: str) -> bool:
    a = MP4(fp)
    if r.get("bpm"):         a["tmpo"] = [int(float(r["bpm"]))]
    if r.get("camelot"):     a["----:com.apple.iTunes:INITIALKEY"] = [r["camelot"].encode("utf-8")]
    if r.get("energy"):      a["----:com.apple.iTunes:ENERGY"] = [r["energy"].encode("utf-8")]
    if r.get("danceability"):a["----:com.apple.iTunes:DANCEABILITY"] = [r["danceability"].encode("utf-8")]
    if grouping:             a["\xa9grp"] = [grouping]
    existing = a.get("\xa9cmt", [""])[0] if a.get("\xa9cmt") else ""
    merged = merge_comment(
        existing, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if merged:
        a["\xa9cmt"] = [merged]
    a.save()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tags.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add trackstage/tags.py tests/test_tags.py
git commit -m "Rewrite tags.py as unified tag read/write module"
```

---

## Task 2: Consolidate xml.py as sole XML owner

**Files:**
- Rewrite: `trackstage/xml.py`
- Test: `tests/test_xml.py`

The current `xml.py` and `pipeline.py` have identical XML functions. Rewrite `xml.py` to be authoritative, adding the `append_tracks_to_xml` and cue-writing logic from pipeline.py.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_xml.py
import pytest
import xml.etree.ElementTree as ET
from pathlib import Path
from trackstage.xml import (
    to_rb_location, sanitize_xml, load_or_bootstrap_xml,
    save_xml, update_xml_track,
)


def test_to_rb_location_wsl_path():
    p = Path("/mnt/c/Users/Test/Music/track.flac")
    result = to_rb_location(p)
    assert result.startswith("file://localhost/C:/Users/Test/Music/")
    assert "track.flac" in result


def test_to_rb_location_spaces():
    p = Path("/mnt/c/Users/Test/My Music/my track.flac")
    result = to_rb_location(p)
    assert "My%20Music" in result
    assert "my%20track.flac" in result


def test_sanitize_xml_control_chars():
    assert sanitize_xml("hello\x00world\x0b") == "helloworld"
    assert sanitize_xml("normal text") == "normal text"


def test_load_or_bootstrap_creates_structure(tmp_path):
    xml_path = tmp_path / "test.xml"
    tree, root, max_id = load_or_bootstrap_xml(xml_path)
    assert root.tag == "DJ_PLAYLISTS"
    assert root.find("COLLECTION") is not None
    assert root.find("PLAYLISTS") is not None
    assert max_id == 0


def test_load_or_bootstrap_reads_existing(tmp_path):
    xml_path = tmp_path / "test.xml"
    root = ET.Element("DJ_PLAYLISTS")
    col = ET.SubElement(root, "COLLECTION")
    ET.SubElement(col, "TRACK", TrackID="42", Location="file://test")
    tree = ET.ElementTree(root)
    tree.write(str(xml_path))

    tree2, root2, max_id = load_or_bootstrap_xml(xml_path)
    assert max_id == 42


def test_update_xml_track_sets_all_fields(tmp_path):
    xml_path = tmp_path / "test.xml"
    tree, root, _ = load_or_bootstrap_xml(xml_path)
    col = root.find("COLLECTION")
    track_el = ET.SubElement(col, "TRACK", TrackID="1", Location="file://test",
                              AverageBpm="0.00", Tonality="", Grouping="", Comments="Techno")

    r = {
        "bpm": "128.0", "camelot": "8A", "energy": "7", "danceability": "6",
        "vibes": ["dark", "driving"], "moods": ["aggressive"],
        "vocal": "instrumental",
        "cues": [{"name": "Drop", "time": 64.0, "type": "drop"}],
    }
    update_xml_track(track_el, r)

    assert track_el.get("AverageBpm") == "128.0"
    assert track_el.get("Tonality") == "8A"
    assert "dark" in track_el.get("Grouping")
    assert "Energy: 7/10" in track_el.get("Comments")
    assert track_el.findall("POSITION_MARK")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_xml.py -v`
Expected: ImportError on `update_xml_track`

- [ ] **Step 3: Rewrite xml.py**

```python
# trackstage/xml.py
"""Rekordbox XML management — the sole owner of all XML operations."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from trackstage.tags import merge_comment, build_grouping
from trackstage.cue_detection import CUE_COLORS

RECENT_PLAYLIST_CAP = 100

KIND_MAP = {
    ".mp3": "MP3 File", ".flac": "FLAC File",
    ".aiff": "AIFF File", ".aif": "AIFF File", ".m4a": "AAC File",
}

ENERGY_TO_RATING = {
    "1": "51", "2": "51", "3": "102", "4": "102",
    "5": "153", "6": "153", "7": "204", "8": "204",
    "9": "255", "10": "255",
}

MOOD_PRIORITY = ["aggressive", "sad", "happy", "party", "relaxed"]
MOOD_TO_COLOUR = {
    "aggressive": "0xFF0000", "happy": "0xFFA500",
    "party": "0xFFFF00", "relaxed": "0x00FF00", "sad": "0x8000FF",
}


def to_rb_location(path: Path) -> str:
    posix = path.as_posix()
    m = re.match(r"/mnt/([a-zA-Z])/(.*)", posix)
    if m:
        posix = f"{m.group(1).upper()}:/{m.group(2)}"
    encoded = quote(posix, safe="/:@!$&'()*+,;=-._~")
    if not encoded.startswith("/"):
        encoded = "/" + encoded
    return f"file://localhost{encoded}"


def to_rb_windows_path(path: Path) -> str:
    """Convert WSL path to Windows path (no URL encoding)."""
    posix = path.as_posix()
    m = re.match(r"/mnt/([a-zA-Z])/(.*)", posix)
    if m:
        return f"{m.group(1).upper()}:/{m.group(2)}"
    return posix


def sanitize_xml(s: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s))


def load_or_bootstrap_xml(xml_path: Path) -> tuple:
    if not xml_path.exists():
        root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
        ET.SubElement(root, "PRODUCT",
                      Name="rekordbox", Version="7.0.0", Company="AlphaTheta")
        ET.SubElement(root, "COLLECTION", Entries="0")
        pl = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="0")
        return ET.ElementTree(root), root, 0

    tree = ET.parse(xml_path)
    root = tree.getroot()
    max_id = 0
    for track in root.findall(".//COLLECTION/TRACK"):
        try:
            max_id = max(max_id, int(track.get("TrackID", 0)))
        except ValueError:
            pass
    return tree, root, max_id


def save_xml(tree: ET.ElementTree, xml_path: Path):
    ET.indent(tree, space="  ")
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(xml_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)


def update_xml_track(track_el: ET.Element, r: dict):
    """Update a single TRACK element with all analysis fields."""
    if r.get("bpm"):
        track_el.set("AverageBpm", r["bpm"])
    if r.get("camelot"):
        track_el.set("Tonality", r["camelot"])

    grouping = build_grouping(r)
    if grouping:
        track_el.set("Grouping", sanitize_xml(grouping))

    existing_comment = track_el.get("Comments", "")
    new_comment = merge_comment(
        existing_comment, r.get("energy", ""), r.get("danceability", ""),
        ", ".join(r.get("vibes", [])), r.get("vocal", ""),
    )
    if new_comment:
        track_el.set("Comments", sanitize_xml(new_comment))

    if r.get("energy"):
        rating = ENERGY_TO_RATING.get(r["energy"])
        if rating:
            track_el.set("Rating", rating)

    for mood in MOOD_PRIORITY:
        if mood in r.get("moods", []):
            track_el.set("Colour", MOOD_TO_COLOUR[mood])
            break

    if r.get("cues"):
        for old in track_el.findall("POSITION_MARK"):
            track_el.remove(old)
        for cue in r["cues"]:
            attrs = {
                "Name": cue["name"], "Type": "0",
                "Start": str(cue["time"]), "Num": "-1",
            }
            attrs.update(CUE_COLORS.get(cue["type"], {}))
            ET.SubElement(track_el, "POSITION_MARK", **attrs)


# ── Playlist helpers (unchanged logic, single location) ──────────────

def _find_or_create_folder(parent: ET.Element, name: str) -> ET.Element:
    for child in parent:
        if child.get("Type") == "0" and child.get("Name") == name:
            return child
    return ET.SubElement(parent, "NODE", Type="0", Name=name, Count="0")


def _find_or_create_playlist(parent: ET.Element, name: str) -> ET.Element:
    for child in parent:
        if child.get("Type") == "1" and child.get("Name") == name:
            return child
    return ET.SubElement(parent, "NODE", Type="1", Name=name, KeyType="0", Entries="0")


def _add_track_to_playlist(playlist: ET.Element, track_id: str):
    for existing in playlist:
        if existing.get("Key") == track_id:
            return
    ET.SubElement(playlist, "TRACK", Key=track_id)
    playlist.set("Entries", str(int(playlist.get("Entries", "0")) + 1))


def _trim_playlist(playlist: ET.Element, max_entries: int):
    tracks = list(playlist.findall("TRACK"))
    if len(tracks) <= max_entries:
        return
    for track in tracks[:len(tracks) - max_entries]:
        playlist.remove(track)
    playlist.set("Entries", str(max_entries))


def _update_folder_counts(root_node: ET.Element):
    count = sum(1 for child in root_node if child.tag == "NODE")
    root_node.set("Count", str(count))
    for child in root_node:
        if child.get("Type") == "0":
            _update_folder_counts(child)


def update_playlists(root: ET.Element, track_entries: list,
                     custom_playlist: str = None, dry_run: bool = False):
    if not track_entries or dry_run:
        return

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        playlists_node = ET.SubElement(root, "PLAYLISTS")
    root_node = playlists_node.find("NODE[@Name='ROOT']")
    if root_node is None:
        root_node = ET.SubElement(playlists_node, "NODE", Type="0", Name="ROOT", Count="0")

    styles_folder = _find_or_create_folder(root_node, "Styles")
    labels_folder = _find_or_create_folder(root_node, "Labels")
    recent_pl = _find_or_create_playlist(root_node, "Recent")
    custom_pl = _find_or_create_playlist(root_node, custom_playlist) if custom_playlist else None

    for entry in track_entries:
        tid = entry["track_id"]
        meta = entry["meta"]

        for style in meta.get("styles", "").split(", "):
            style = style.strip()
            if style:
                _add_track_to_playlist(_find_or_create_playlist(styles_folder, style), tid)

        label = meta.get("label", "").strip()
        if label:
            _add_track_to_playlist(_find_or_create_playlist(labels_folder, label), tid)

        _add_track_to_playlist(recent_pl, tid)
        if custom_pl:
            _add_track_to_playlist(custom_pl, tid)

    _trim_playlist(recent_pl, RECENT_PLAYLIST_CAP)
    _update_folder_counts(root_node)


def rebuild_playlists(xml_path: Path, as_json: bool = False):
    """Wipe and rebuild all auto-playlists from XML track data."""
    import json
    tree, root, _ = load_or_bootstrap_xml(xml_path)
    collection = root.find("COLLECTION")
    if collection is None:
        return
    tracks = collection.findall("TRACK")
    if not tracks:
        return

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        playlists_node = ET.SubElement(root, "PLAYLISTS")
    root_node = playlists_node.find("NODE[@Name='ROOT']")
    if root_node is None:
        root_node = ET.SubElement(playlists_node, "NODE", Type="0", Name="ROOT", Count="0")

    for name in ("Styles", "Labels"):
        for child in list(root_node):
            if child.get("Type") == "0" and child.get("Name") == name:
                root_node.remove(child)
    for child in list(root_node):
        if child.get("Type") == "1" and child.get("Name") == "Recent":
            root_node.remove(child)

    styles_folder = _find_or_create_folder(root_node, "Styles")
    labels_folder = _find_or_create_folder(root_node, "Labels")
    recent_pl = _find_or_create_playlist(root_node, "Recent")

    style_counts, label_counts = {}, {}
    for track in tracks:
        tid = track.get("TrackID", "")
        label = track.get("Label", "").strip()
        comments = track.get("Comments", "").strip()

        styles_str = comments.split(" | ")[0] if comments else ""
        if styles_str.startswith("Cat#") or styles_str.startswith("Energy"):
            styles_str = ""

        for style in styles_str.split(", "):
            style = style.strip()
            if style:
                _add_track_to_playlist(_find_or_create_playlist(styles_folder, style), tid)
                style_counts[style] = style_counts.get(style, 0) + 1

        if label:
            _add_track_to_playlist(_find_or_create_playlist(labels_folder, label), tid)
            label_counts[label] = label_counts.get(label, 0) + 1

        _add_track_to_playlist(recent_pl, tid)

    _trim_playlist(recent_pl, RECENT_PLAYLIST_CAP)
    _update_folder_counts(root_node)
    save_xml(tree, xml_path)

    if as_json:
        print(json.dumps({
            "tracks": len(tracks),
            "style_playlists": len(style_counts),
            "label_playlists": len(label_counts),
        }, indent=2))
    else:
        print(f"Rebuilt: {len(style_counts)} style, {len(label_counts)} label playlists from {len(tracks)} tracks")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_xml.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add trackstage/xml.py tests/test_xml.py
git commit -m "Rewrite xml.py as sole owner of all Rekordbox XML operations"
```

---

## Task 3: Create rekordbox.py — Rekordbox DB operations

**Files:**
- Create: `trackstage/rekordbox.py`
- Test: `tests/test_rekordbox.py`

This module owns all direct Rekordbox database interaction: My Tags, Rating, Color.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_rekordbox.py
import pytest
from trackstage.rekordbox import (
    ENERGY_TO_RATING, MOOD_TO_COLOR, GENRE_TAG_MAP,
    pick_color_id, compute_situation, compute_vibe_tags,
    extract_genres_from_comment,
)


def test_energy_to_rating():
    assert ENERGY_TO_RATING["1"] == 1
    assert ENERGY_TO_RATING["5"] == 3
    assert ENERGY_TO_RATING["10"] == 5


def test_pick_color_aggressive():
    assert pick_color_id(["aggressive", "party"]) == "2"  # Red wins


def test_pick_color_relaxed():
    assert pick_color_id(["relaxed"]) == "5"  # Green


def test_pick_color_empty():
    assert pick_color_id([]) == "0"


def test_compute_situation_ambient():
    assert compute_situation("1") == "Ambient"
    assert compute_situation("2") == "Ambient"


def test_compute_situation_peak():
    assert compute_situation("8") == "Peak"
    assert compute_situation("9") == "Rave"


def test_compute_vibe_tags_deep():
    tags = compute_vibe_tags({"vibes": ["deep"], "moods": ["relaxed"]}, energy=2)
    assert "Deep" in tags
    assert "Hypnotic" in tags


def test_compute_vibe_tags_raw():
    tags = compute_vibe_tags({"vibes": ["dark"], "moods": ["aggressive"]}, energy=8)
    assert "Dark" in tags
    assert "Raw" in tags


def test_compute_vibe_tags_driving_needs_energy():
    tags_low = compute_vibe_tags({"vibes": ["driving"], "moods": ["party"]}, energy=3)
    tags_high = compute_vibe_tags({"vibes": ["driving"], "moods": ["party"]}, energy=6)
    assert "Driving" not in tags_low
    assert "Driving" in tags_high


def test_extract_genres():
    genres = extract_genres_from_comment("House, Deep House | Cat# X | Energy: 5/10")
    assert "house" in genres
    assert "deep house" in genres


def test_extract_genres_empty():
    assert extract_genres_from_comment("") == []
    assert extract_genres_from_comment("Energy: 5/10") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rekordbox.py -v`
Expected: ImportError

- [ ] **Step 3: Write rekordbox.py**

```python
# trackstage/rekordbox.py
"""Rekordbox 6/7 database operations — My Tags, Rating, Color.

Requires pyrekordbox + sqlcipher3. Rekordbox must be CLOSED.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trackstage.xml import to_rb_windows_path

ENERGY_TO_RATING = {
    "1": 1, "2": 1, "3": 2, "4": 2,
    "5": 3, "6": 3, "7": 4, "8": 4,
    "9": 5, "10": 5,
}

MOOD_TO_COLOR = {
    "aggressive": "2",  # Red
    "happy":      "3",  # Orange
    "party":      "4",  # Yellow
    "relaxed":    "5",  # Green
    "sad":        "8",  # Purple
}

MOOD_PRIORITY = ["aggressive", "sad", "happy", "party", "relaxed"]

SITUATION_MAP = {
    "1": "Ambient", "2": "Ambient",
    "3": "Warmup", "4": "Warmup",
    "5": "Groove", "6": "Groove",
    "7": "Peak", "8": "Peak",
    "9": "Rave", "10": "Rave",
}

GENRE_TAG_MAP = {
    "house": "House", "tech house": "Tech House", "techno": "Techno",
    "disco": "Disco", "electro": "Electro", "dubstep": "Dubstep",
    "breakbeat": "Breakbeat", "breaks": "Breakbeat",
    "italo-disco": "Italo-Disco",
    "drum n bass": "DnB/Jungle", "jungle": "DnB/Jungle", "dnb": "DnB/Jungle",
    "ambient": "Ambient", "rave": "Rave", "pop": "Pop",
    "leftfield": "Leftfield",
    "trance": "Trance", "progressive trance": "Trance",
    "uk garage": "Garage", "garage house": "Garage",
    "minimal": "Minimal", "minimal techno": "Minimal",
    "idm": "IDM",
}

MY_TAG_SCHEMA = {
    "Genre": [
        "House", "Tech House", "Techno", "Disco", "Electro", "Dubstep",
        "Breakbeat", "Italo-Disco", "DnB/Jungle", "Rave", "Ambient",
        "Pop", "Leftfield", "Trance", "Garage", "Minimal", "IDM",
    ],
    "Vibe": [
        "Deep", "Dark", "Driving", "Euphoric", "Hypnotic", "Raw", "Melodic",
    ],
    "Sound": ["Vocal", "Acid", "Dub", "Breaks"],
    "Situation": ["Ambient", "Warmup", "Groove", "Peak", "Rave"],
}


def pick_color_id(moods: list) -> str:
    for mood in MOOD_PRIORITY:
        if mood in moods:
            return MOOD_TO_COLOR[mood]
    return "0"


def compute_situation(energy: str) -> str:
    return SITUATION_MAP.get(energy, "Groove")


def compute_vibe_tags(r: dict, energy: int) -> set:
    tags = set()
    vibes = r.get("vibes", [])
    moods = r.get("moods", [])

    if "deep" in vibes:
        tags.add("Deep")
    if "dark" in vibes:
        tags.add("Dark")
    if "driving" in vibes and energy >= 5:
        tags.add("Driving")
    if "euphoric" in vibes:
        tags.add("Euphoric")

    if "deep" in vibes and energy <= 3:
        tags.add("Hypnotic")
    if "aggressive" in moods and energy >= 7:
        tags.add("Raw")
    if "relaxed" in moods and "aggressive" not in moods and energy <= 5:
        tags.add("Melodic")

    return tags


def compute_sound_tags(r: dict, comment: str = "") -> set:
    tags = set()
    if r.get("vocal") == "voice":
        tags.add("Vocal")
    genres = extract_genres_from_comment(comment)
    for g in genres:
        if "acid" in g:
            tags.add("Acid")
        if "dub" in g and "dubstep" not in g:
            tags.add("Dub")
        if "break" in g:
            tags.add("Breaks")
    return tags


def compute_genre_tags(comment: str) -> set:
    tags = set()
    for genre in extract_genres_from_comment(comment):
        mapped = GENRE_TAG_MAP.get(genre)
        if mapped:
            tags.add(mapped)
    return tags


def extract_genres_from_comment(comment: str) -> list:
    if not comment:
        return []
    first = comment.split(" | ")[0]
    if first.startswith("Energy") or first.startswith("Dance") or first.startswith("Cat#"):
        return []
    return [g.strip().lower() for g in first.split(",") if g.strip()]


class RekordboxDB:
    """Wrapper for Rekordbox 6/7 SQLCipher database."""

    def __init__(self, db_path: str):
        from pyrekordbox import Rekordbox6Database
        self.db = Rekordbox6Database(path=db_path)
        self._tag_ids = {}  # name → ID
        self._category_ids = {}  # name → ID

    def load_tag_index(self, conn):
        from sqlalchemy import text
        rows = conn.execute(text(
            "SELECT ID, Name, Attribute, ParentID FROM djmdMyTag"
        )).fetchall()
        for id_, name, attr, parent in rows:
            if attr == 1:
                self._category_ids[name] = id_
            else:
                self._tag_ids[name] = id_

    def get_content_map(self, conn) -> dict:
        from sqlalchemy import text
        rows = conn.execute(text(
            "SELECT ID, FolderPath, Rating, ColorID FROM djmdContent"
        )).fetchall()
        return {
            folder: {"id": cid, "rating": rating, "color": color}
            for cid, folder, rating, color in rows
        }

    def get_existing_tag_assignments(self, conn) -> set:
        from sqlalchemy import text
        rows = conn.execute(text(
            "SELECT ContentID, MyTagID FROM djmdSongMyTag WHERE rb_local_deleted=0"
        )).fetchall()
        return {(cid, tid) for cid, tid in rows}

    def get_max_usn(self, conn) -> int:
        from sqlalchemy import text
        row = conn.execute(text(
            "SELECT MAX(rb_local_usn) FROM djmdSongMyTag"
        )).fetchone()
        return row[0] or 0

    def wipe_my_tags(self, conn):
        """Delete all tag definitions and assignments."""
        from sqlalchemy import text
        conn.execute(text("DELETE FROM djmdSongMyTag"))
        conn.execute(text("DELETE FROM djmdMyTag"))

    def create_tag_schema(self, conn, schema: dict) -> dict:
        """Create category/tag hierarchy. Returns name→ID map."""
        from sqlalchemy import text
        import random

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")
        usn = 1
        tag_ids = {}

        for seq, (category, tags) in enumerate(schema.items(), 1):
            cat_id = str(seq)
            conn.execute(text("""
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, UUID,
                    rb_data_status, rb_local_data_status, rb_local_deleted,
                    rb_local_synced, rb_local_usn, created_at, updated_at)
                VALUES (:id, :seq, :name, 1, 'root', :uuid,
                    0, 0, 0, 0, :usn, :now, :now)
            """), {"id": cat_id, "seq": seq, "name": category,
                   "uuid": str(uuid.uuid4()), "usn": usn, "now": now})
            usn += 1

            for tseq, tag_name in enumerate(tags, 1):
                tag_id = str(random.randint(100000000, 4294967295))
                conn.execute(text("""
                    INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, UUID,
                        rb_data_status, rb_local_data_status, rb_local_deleted,
                        rb_local_synced, rb_local_usn, created_at, updated_at)
                    VALUES (:id, :seq, :name, 0, :parent, :uuid,
                        0, 0, 0, 0, :usn, :now, :now)
                """), {"id": tag_id, "seq": tseq, "name": tag_name,
                       "parent": cat_id, "uuid": str(uuid.uuid4()),
                       "usn": usn, "now": now})
                tag_ids[tag_name] = tag_id
                usn += 1

        return tag_ids

    def assign_tag(self, conn, content_id: str, tag_id: str,
                   existing: set, usn: int) -> int:
        """Add tag assignment if not exists. Returns new USN."""
        if (content_id, tag_id) in existing:
            return usn
        from sqlalchemy import text
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")
        usn += 1
        conn.execute(text("""
            INSERT INTO djmdSongMyTag
            (ID, MyTagID, ContentID, UUID,
             rb_data_status, rb_local_data_status,
             rb_local_deleted, rb_local_synced,
             rb_local_usn, created_at, updated_at)
            VALUES (:id, :tag, :content, :uuid,
                    0, 0, 0, 0, :usn, :now, :now)
        """), {
            "id": str(uuid.uuid4()), "tag": tag_id,
            "content": content_id, "uuid": str(uuid.uuid4()),
            "usn": usn, "now": now,
        })
        existing.add((content_id, tag_id))
        return usn
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_rekordbox.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add trackstage/rekordbox.py tests/test_rekordbox.py
git commit -m "Add rekordbox.py for Rekordbox DB operations and My Tag system"
```

---

## Task 4: Slim down pipeline.py

**Files:**
- Modify: `trackstage/pipeline.py`
- Test: existing tests in `tests/test_edge_cases.py`, `tests/test_xml_update.py`

Remove all tag-writing functions, XML helpers, and comment builders from pipeline.py. Replace with imports from tags.py and xml.py.

- [ ] **Step 1: Replace imports and delete duplicated functions**

In `pipeline.py`:
1. Add imports: `from trackstage.tags import read_tags as _read_tags, write_tags, build_comment, merge_comment, build_grouping`
2. Add imports: `from trackstage.xml import to_rb_location, sanitize_xml, load_or_bootstrap_xml, save_xml as _save_xml, update_xml_track, update_playlists, rebuild_playlists`
3. Delete these functions from pipeline.py:
   - `write_mp3`, `write_flac`, `write_aiff`, `write_m4a`, `write_tags` (lines ~421-560)
   - `to_rb_location`, `load_or_bootstrap_xml`, `_save_xml` (lines ~585-630)
   - `update_playlists` (lines ~678-735)
   - `rebuild_playlists` (lines ~1333-1438)
   - `_build_comment` (find and delete)
4. Keep `read_tags` as a thin wrapper or rename to avoid collision
5. Update `append_tracks_to_xml` to use `update_xml_track` and imported helpers
6. Update `_run_analysis` to use `tags.write_analysis_tags`

- [ ] **Step 2: Fix imports in test files**

Update `tests/test_edge_cases.py` and `tests/test_xml_update.py` to import from `trackstage.xml` and `trackstage.tags` instead of `trackstage.pipeline`.

- [ ] **Step 3: Run all tests**

Run: `.venv/bin/pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add trackstage/pipeline.py tests/
git commit -m "Remove duplicated tag/XML code from pipeline.py, import from modules"
```

---

## Task 5: Rewrite analyze_library.py (analysis only)

**Files:**
- Rewrite: `scripts/analyze_library.py`

Strip all tag-writing, XML updating, comment merging from this script. It should ONLY: scan library → analyze uncached tracks → store in cache. Tag/XML/DB sync is done by `scripts/sync.py`.

- [ ] **Step 1: Rewrite analyze_library.py**

```python
#!/usr/bin/env python3
"""Analyze library tracks and store results in cache.

Does NOT write file tags, XML, or Rekordbox DB — use sync.py for that.

Usage:
    ./scripts/analyze_library.py              # analyze uncached tracks
    ./scripts/analyze_library.py --force      # re-analyze everything
    ./scripts/analyze_library.py --dry-run    # analyze without caching
"""

import os
import sys
import time
import argparse

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from dotenv import load_dotenv
from trackstage.analyzer import analyze_track
from trackstage.cache import AnalysisCache
from trackstage.tags import EXTENSIONS

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main():
    parser = argparse.ArgumentParser(description="Analyze DJ library tracks")
    parser.add_argument("--force", action="store_true", help="Re-analyze all tracks")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without caching")
    parser.add_argument("--library", type=Path, default=None)
    args = parser.parse_args()

    library = args.library or Path(os.environ.get("LIBRARY_PATH", ""))
    if not library.is_dir():
        print(f"ERROR: Library not found: {library}")
        sys.exit(1)

    tracks = sorted([
        f for f in library.rglob("*")
        if f.suffix.lower() in EXTENSIONS and f.is_file()
    ])
    print(f"Found {len(tracks)} tracks.")

    cache = AnalysisCache()
    print(f"Cache: {cache.count()} entries.")

    if not args.force:
        before = len(tracks)
        tracks = [t for t in tracks if cache.get(t) is None]
        skipped = before - len(tracks)
        if skipped:
            print(f"Skipping {skipped} cached tracks. Use --force to redo.")

    if not tracks:
        print("Nothing to analyze.")
        return

    print(f"Analyzing {len(tracks)} tracks...")
    print("=" * 70)

    start = time.time()
    success, errors = 0, 0

    for i, fp in enumerate(tracks):
        t0 = time.time()
        try:
            r = analyze_track(fp)
            if not args.dry_run:
                cache.put(fp, r)
            elapsed = time.time() - t0
            success += 1

            if i % 10 == 0 or i < 3:
                avg = (time.time() - start) / (i + 1)
                eta = avg * (len(tracks) - i - 1) / 60
                vibes = ",".join(r.get("vibes", [])) or "-"
                print(
                    f"  [{i+1:>4}/{len(tracks)}] {elapsed:.1f}s "
                    f"| BPM={r.get('bpm') or '-':>5} Key={r.get('camelot') or '-':>3} "
                    f"E={r.get('energy') or '-'} D={r.get('danceability') or '-'} "
                    f"Cues={len(r.get('cues', []))} Vibes={vibes} "
                    f"| {fp.parent.name}/{fp.name}"
                )
                if i > 0:
                    print(f"         ETA: {eta:.0f} min remaining")
        except Exception as e:
            errors += 1
            print(f"  [{i+1:>4}/{len(tracks)}] ERROR ({time.time()-t0:.1f}s): {fp.name}: {e}")

    cache.close()
    total = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"Done in {total/60:.1f} min ({total/len(tracks):.1f}s/track avg)")
    print(f"  Success: {success}  Errors: {errors}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

Run: `.venv/bin/python scripts/analyze_library.py --dry-run 2>&1 | head -5`
Expected: Shows track count and "Skipping N cached tracks"

- [ ] **Step 3: Commit**

```bash
git add scripts/analyze_library.py
git commit -m "Simplify analyze_library.py to analysis+cache only"
```

---

## Task 6: Create unified sync.py

**Files:**
- Create: `scripts/sync.py`
- Delete: `scripts/backfill_xml.py`, `scripts/sync_rekordbox.py`

Single script that reads cache and writes to: file tags + Rekordbox XML + Rekordbox DB (My Tags, Rating, Color). Replaces both deleted scripts.

- [ ] **Step 1: Write sync.py**

```python
#!/usr/bin/env python3
"""Sync analysis cache to file tags, Rekordbox XML, and Rekordbox DB.

Reads from ~/.trackstage/analysis.db cache. Writes to:
  1. File tags (BPM, key, energy, danceability, grouping, comment)
  2. Rekordbox XML (all fields + cue points + playlists)
  3. Rekordbox DB (My Tags, Rating, Color) — requires Rekordbox closed

Usage:
    ./scripts/sync.py                    # sync tags + XML + DB
    ./scripts/sync.py --tags-only        # file tags only
    ./scripts/sync.py --xml-only         # XML only
    ./scripts/sync.py --db-only          # Rekordbox DB only
    ./scripts/sync.py --dry-run          # preview
"""

import json
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from dotenv import load_dotenv

from trackstage.cache import AnalysisCache
from trackstage.tags import write_analysis_tags, EXTENSIONS
from trackstage.xml import (
    to_rb_location, to_rb_windows_path, load_or_bootstrap_xml,
    save_xml, update_xml_track,
)
from trackstage.loudness import write_replaygain_tags
from trackstage.rekordbox import (
    RekordboxDB, MY_TAG_SCHEMA,
    ENERGY_TO_RATING, pick_color_id,
    compute_situation, compute_vibe_tags, compute_sound_tags, compute_genre_tags,
    extract_genres_from_comment,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_DB = "/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db"


def load_cache() -> dict:
    cache = AnalysisCache()
    result = {}
    for row in cache.conn.execute("SELECT path, result FROM analysis"):
        result[row[0]] = json.loads(row[1])
    cache.close()
    return result


def sync_tags(library: Path, cache_by_path: dict, dry_run: bool) -> int:
    """Write analysis data to file tags."""
    updated = 0
    tracks = sorted([
        f for f in library.rglob("*")
        if f.suffix.lower() in EXTENSIONS and f.is_file()
    ])
    for fp in tracks:
        r = cache_by_path.get(str(fp))
        if r is None:
            continue
        if not dry_run:
            write_analysis_tags(fp, r)
            if r.get("loudness") and r["loudness"].get("gain_db") is not None:
                write_replaygain_tags(fp, r["loudness"])
        updated += 1
    return updated


def sync_xml(library: Path, xml_path: Path, cache_by_path: dict, dry_run: bool) -> int:
    """Write analysis data to Rekordbox XML."""
    tree, root, _ = load_or_bootstrap_xml(xml_path)
    collection = root.find("COLLECTION")
    if collection is None:
        return 0

    loc_map = {}
    for el in collection.findall("TRACK"):
        loc_map[el.get("Location", "")] = el

    updated = 0
    tracks = sorted([
        f for f in library.rglob("*")
        if f.suffix.lower() in EXTENSIONS and f.is_file()
    ])
    for fp in tracks:
        r = cache_by_path.get(str(fp))
        if r is None:
            continue
        loc = to_rb_location(fp)
        el = loc_map.get(loc)
        if el is None:
            continue
        if not dry_run:
            update_xml_track(el, r)
        updated += 1

    if not dry_run and updated > 0:
        save_xml(tree, xml_path)
    return updated


def sync_db(library: Path, cache_by_path: dict, db_path: str, dry_run: bool) -> dict:
    """Write Rating, Color, My Tags to Rekordbox DB."""
    from sqlalchemy import text

    rdb = RekordboxDB(db_path)
    stats = {"ratings": 0, "colors": 0, "tags": 0, "matched": 0}

    with rdb.db.engine.connect() as conn:
        # Wipe and recreate tag schema
        if not dry_run:
            rdb.wipe_my_tags(conn)
        tag_ids = rdb.create_tag_schema(conn, MY_TAG_SCHEMA) if not dry_run else {}

        content_map = rdb.get_content_map(conn)
        existing_tags = set() if not dry_run else set()
        usn = rdb.get_max_usn(conn) if not dry_run else 0
        now_str = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")

        tracks = sorted([
            f for f in library.rglob("*")
            if f.suffix.lower() in EXTENSIONS and f.is_file()
        ])

        for fp in tracks:
            r = cache_by_path.get(str(fp))
            if r is None:
                continue
            rb_path = to_rb_windows_path(fp)
            content = content_map.get(rb_path)
            if content is None:
                continue

            stats["matched"] += 1
            cid = content["id"]
            energy = int(r["energy"]) if r.get("energy") else 0

            # Rating
            if r.get("energy"):
                rating = ENERGY_TO_RATING.get(r["energy"], 0)
                if rating and content["rating"] != rating:
                    if not dry_run:
                        conn.execute(text(
                            "UPDATE djmdContent SET Rating=:r, updated_at=:now WHERE ID=:id"
                        ), {"r": rating, "now": now_str, "id": cid})
                    stats["ratings"] += 1

            # Color
            color_id = pick_color_id(r.get("moods", []))
            if color_id != "0" and content["color"] != color_id:
                if not dry_run:
                    conn.execute(text(
                        "UPDATE djmdContent SET ColorID=:c, updated_at=:now WHERE ID=:id"
                    ), {"c": color_id, "now": now_str, "id": cid})
                stats["colors"] += 1

            if dry_run:
                continue

            # Vibe tags
            for tag_name in compute_vibe_tags(r, energy):
                if tag_name in tag_ids:
                    usn = rdb.assign_tag(conn, cid, tag_ids[tag_name], existing_tags, usn)
                    stats["tags"] += 1

            # Sound tags
            comment = r.get("_comment", "")
            for tag_name in compute_sound_tags(r, comment):
                if tag_name in tag_ids:
                    usn = rdb.assign_tag(conn, cid, tag_ids[tag_name], existing_tags, usn)
                    stats["tags"] += 1

            # Genre tags
            for tag_name in compute_genre_tags(comment):
                if tag_name in tag_ids:
                    usn = rdb.assign_tag(conn, cid, tag_ids[tag_name], existing_tags, usn)
                    stats["tags"] += 1

            # Situation tag
            if r.get("energy"):
                sit = compute_situation(r["energy"])
                if sit in tag_ids:
                    usn = rdb.assign_tag(conn, cid, tag_ids[sit], existing_tags, usn)
                    stats["tags"] += 1

            # Vocal tag
            if r.get("vocal") == "voice" and "Vocal" in tag_ids:
                usn = rdb.assign_tag(conn, cid, tag_ids["Vocal"], existing_tags, usn)
                stats["tags"] += 1

        if not dry_run:
            conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync analysis to tags, XML, and Rekordbox DB")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tags-only", action="store_true")
    parser.add_argument("--xml-only", action="store_true")
    parser.add_argument("--db-only", action="store_true")
    parser.add_argument("--library", type=Path, default=None)
    args = parser.parse_args()

    library = args.library or Path(os.environ.get("LIBRARY_PATH", ""))
    xml_path = Path(os.environ.get("XML_PATH", ""))
    db_path = os.environ.get("REKORDBOX_DB", DEFAULT_DB)

    if not library.is_dir():
        print(f"ERROR: Library not found: {library}")
        sys.exit(1)

    do_all = not (args.tags_only or args.xml_only or args.db_only)
    prefix = "[DRY RUN] " if args.dry_run else ""

    print("Loading cache...")
    cache = load_cache()
    print(f"Cache: {len(cache)} entries")

    if do_all or args.tags_only:
        print("\nSyncing file tags...")
        n = sync_tags(library, cache, args.dry_run)
        print(f"{prefix}Tags: {n} tracks updated")

    if do_all or args.xml_only:
        if not xml_path.name:
            print("WARNING: XML_PATH not set, skipping XML sync")
        else:
            print("\nSyncing Rekordbox XML...")
            n = sync_xml(library, xml_path, cache, args.dry_run)
            print(f"{prefix}XML: {n} tracks updated")

    if do_all or args.db_only:
        if not Path(db_path).exists():
            print("WARNING: Rekordbox DB not found, skipping DB sync")
        else:
            print("\nSyncing Rekordbox DB...")
            import shutil
            backup = db_path.replace("master.db", "master.pre_sync.db")
            if not args.dry_run:
                shutil.copy2(db_path, backup)
                print(f"  Backup: {backup}")
            stats = sync_db(library, cache, db_path, args.dry_run)
            print(f"{prefix}DB: {stats['matched']} matched, "
                  f"{stats['ratings']} ratings, {stats['colors']} colors, "
                  f"{stats['tags']} tags")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Delete old scripts**

```bash
rm scripts/backfill_xml.py scripts/sync_rekordbox.py
```

- [ ] **Step 3: Test sync.py dry run**

Run: `.venv/bin/python scripts/sync.py --dry-run --tags-only 2>&1 | head -10`
Expected: Shows cache count and tags updated count

- [ ] **Step 4: Commit**

```bash
git add scripts/sync.py
git rm scripts/backfill_xml.py scripts/sync_rekordbox.py
git commit -m "Add unified sync.py, delete backfill_xml.py and sync_rekordbox.py"
```

---

## Task 7: Wire the comment field into sync_db for genre extraction

**Files:**
- Modify: `scripts/sync.py`

The cache doesn't store the raw comment (genres come from file tags). Before genre tag assignment, read the comment from the file tag so `compute_genre_tags` has data.

- [ ] **Step 1: Add comment reading to sync_db**

In `sync_db`, after matching a track to cache, read the file's comment tag:

```python
from trackstage.tags import read_comment

# Inside the track loop, after getting `r`:
comment = read_comment(fp)
```

- [ ] **Step 2: Add `read_comment` to tags.py**

```python
def read_comment(fp: Path) -> str:
    """Read comment tag from file."""
    ext = fp.suffix.lower()
    try:
        if ext == ".flac":
            return FLAC(fp).get("comment", [""])[0]
        elif ext == ".mp3":
            tags = ID3(fp)
            return str(tags.get("COMM::eng", ""))
        elif ext in (".aiff", ".aif"):
            a = AIFF(fp)
            return str(a.tags.get("COMM::eng", "")) if a.tags else ""
        elif ext == ".m4a":
            a = MP4(fp)
            return a.get("\xa9cmt", [""])[0] if a.get("\xa9cmt") else ""
    except Exception:
        pass
    return ""
```

- [ ] **Step 3: Run all tests**

Run: `.venv/bin/pytest tests/ -v`

- [ ] **Step 4: Commit**

```bash
git add trackstage/tags.py scripts/sync.py
git commit -m "Wire comment reading into sync for genre tag extraction"
```

---

## Task 8: Run full sync and verify

**Files:** None (execution + verification)

- [ ] **Step 1: Run analysis check**

```bash
.venv/bin/python scripts/analyze_library.py --dry-run 2>&1 | head -5
```

- [ ] **Step 2: Close Rekordbox and run full sync**

```bash
.venv/bin/python scripts/sync.py
```

Expected output shows tags, XML, and DB sync counts.

- [ ] **Step 3: Verify file tags**

```bash
.venv/bin/python -c "
from mutagen.flac import FLAC
a = FLAC('/mnt/c/Users/Kaitlyn/Music/Library/2018/Be Real [Not On Label]/Kolter - Be Real.flac')
for f in ['bpm', 'initialkey', 'energy', 'danceability', 'grouping', 'comment']:
    print(f'{f}: {a.get(f, [\"(missing)\"])[0]}')
"
```

- [ ] **Step 4: Verify Rekordbox DB tags**

```bash
.venv/bin/python -c "
from pyrekordbox import Rekordbox6Database
from sqlalchemy import text

db = Rekordbox6Database(path='/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db')
with db.engine.connect() as conn:
    tags = conn.execute(text(\"SELECT Name FROM djmdMyTag WHERE Attribute=1 ORDER BY Seq\")).fetchall()
    print('Categories:', [t[0] for t in tags])
    count = conn.execute(text('SELECT COUNT(*) FROM djmdSongMyTag WHERE rb_local_deleted=0')).fetchone()
    print(f'Tag assignments: {count[0]}')
"
```

- [ ] **Step 5: Open Rekordbox and verify My Tags panel shows new categories**

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Verify full pipeline: analyze → sync → Rekordbox"
```

---

## Task 9: Run existing tests and fix any breakage

**Files:**
- Modify: any test files that broke from import changes

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest tests/ -v
```

- [ ] **Step 2: Fix any import errors in tests**

Update test files that import from `trackstage.pipeline` for functions that moved to `trackstage.xml` or `trackstage.tags`.

Common fixes:
- `from trackstage.pipeline import to_rb_location` → `from trackstage.xml import to_rb_location`
- `from trackstage.pipeline import sanitize_xml` → `from trackstage.xml import sanitize_xml`
- `from trackstage.pipeline import _build_comment` → `from trackstage.tags import build_comment`

- [ ] **Step 3: Run tests again**

```bash
.venv/bin/pytest tests/ -v
```
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "Fix test imports after module consolidation"
```

---

## Task 10: Final cleanup

**Files:**
- Modify: `trackstage/pipeline.py` (remove dead code)
- Modify: `pyproject.toml` (add pyrekordbox dependency)

- [ ] **Step 1: Add pyrekordbox to optional deps**

In `pyproject.toml`, add to `[project.optional-dependencies]`:
```toml
rekordbox = [
    "pyrekordbox>=0.4",
    "sqlcipher3-wheels>=0.5",
]
```

- [ ] **Step 2: Verify pipeline.py has no dead imports or functions**

Grep for any remaining duplicated function definitions:
```bash
grep -n "^def write_mp3\|^def write_flac\|^def write_aiff\|^def write_m4a\|^def to_rb_location\|^def _save_xml\|^def _build_comment\|^def rebuild_playlists" trackstage/pipeline.py
```
Expected: No output (all moved to modules)

- [ ] **Step 3: Run full test suite one final time**

```bash
.venv/bin/pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml trackstage/pipeline.py
git commit -m "Add pyrekordbox dep, final cleanup"
```
