#!/usr/bin/env python3
"""
pipeline.py — trackstage core pipeline

Scan inbox → Discogs lookup → audio analysis → tag → rename → move to Library →
write directly to Rekordbox master.db with auto-playlists (Styles/Labels).

Usage:
    trackstage --list                              # show inbox contents
    trackstage --target "Artist - EP" -y           # process one item
    trackstage -y                                  # process everything
    trackstage --target "track.flac" -y --discogs-id 12345
    trackstage --dry-run -y                        # preview without changes
    trackstage add "E Talking by Soulwax" -y       # source + add one track

Config (.env in same folder as this script):
    DISCOGS_TOKEN=yourtoken
    INBOX_PATH=/path/to/inbox
    LIBRARY_PATH=/path/to/library
    REKORDBOX_DB=/mnt/c/.../rekordbox/master.db    # optional override

Requirements:
    pip install mutagen thefuzz python-Levenshtein requests python-dotenv \
                pyrekordbox SQLAlchemy
"""

import os
import json
import re
import sys
import time
import shutil
import logging
import argparse
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from .tags import read_tags, write_tags, build_comment, EXTENSIONS as AUDIO_EXTS_TAG
from .dbwriter import (
    RekordboxWriter, rekordbox_running, backup_db, restore_db,
)
from .rekordbox import to_rb_windows_path

try:
    from pyrekordbox import Rekordbox6Database
except ImportError:
    Rekordbox6Database = None

DEFAULT_DB = "/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db"

try:
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.aiff import AIFF
    from mutagen.mp4 import MP4
except ImportError:
    print("ERROR: mutagen not installed.\nRun: pip install mutagen")
    sys.exit(1)

try:
    from thefuzz import fuzz
    _FUZZY = True
except ImportError:
    _FUZZY = False

try:
    from .audio_analysis import analyze as analyze_audio, format_analysis_log
    _ANALYSIS = True
except ImportError:
    _ANALYSIS = False

try:
    from .cue_detection import detect_cues, format_cues_log, CUE_COLORS
    _CUE_DETECTION = True
except ImportError:
    _CUE_DETECTION = False

try:
    from .mood_detection import detect_mood, format_mood_log
    _MOOD_DETECTION = True
except ImportError:
    _MOOD_DETECTION = False

try:
    from .loudness import measure_loudness, write_replaygain_tags, format_loudness_log
    _LOUDNESS = True
except ImportError:
    _LOUDNESS = False

try:
    from .analyzer import analyze_track
    _FAST_ANALYSIS = True
except ImportError:
    _FAST_ANALYSIS = False


# ── Constants ─────────────────────────────────────────────────────────────────

DISCOGS_API_BASE  = "https://api.discogs.com"
REQUESTS_PER_MIN  = 55
DEFAULT_THRESHOLD = 85
REVIEW_THRESHOLD  = 60
MAX_NAME_LENGTH   = 180
AUDIO_EXTS   = {".mp3", ".flac", ".aiff", ".aif", ".m4a"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}

NON_MUSIC_EXTS = {
    ".py", ".csv", ".xml", ".txt", ".ini", ".log", ".json", ".yaml", ".toml",
    ".asd", ".als", ".alp", ".nml", ".nfo", ".ptx", ".ptf", ".rpp",
    ".m3u", ".m3u8", ".cue", ".pls",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".pdf", ".docx",
}


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self.last = 0.0

    def wait(self):
        gap = self.interval - (time.time() - self.last)
        if gap > 0:
            time.sleep(gap)
        self.last = time.time()


# ── Discogs client ────────────────────────────────────────────────────────────

class DiscogsClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Discogs token={token}",
            "User-Agent": "DJLibraryPipeline/3.0",
        })
        self.limiter = RateLimiter(REQUESTS_PER_MIN)
        self._cache: dict = {}

    def verify(self) -> bool:
        try:
            r = self.session.get(f"{DISCOGS_API_BASE}/oauth/identity", timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def search(self, query: str, limit: int = 5) -> list:
        self.limiter.wait()
        try:
            r = self.session.get(
                f"{DISCOGS_API_BASE}/database/search",
                params={"q": query, "type": "release", "per_page": limit, "page": 1},
                timeout=15,
            )
            if r.status_code == 429:
                log.warning("  ⚠  Rate limited — waiting 60s…")
                time.sleep(61)
                return self.search(query, limit)
            r.raise_for_status()
            return r.json().get("results", [])
        except requests.RequestException as e:
            log.warning(f"  ⚠  Search failed: {e}")
            return []

    def get_release(self, release_id: int) -> Optional[dict]:
        if release_id in self._cache:
            return self._cache[release_id]
        self.limiter.wait()
        try:
            r = self.session.get(
                f"{DISCOGS_API_BASE}/releases/{release_id}", timeout=15
            )
            if r.status_code == 429:
                log.warning("  ⚠  Rate limited — waiting 60s…")
                time.sleep(61)
                return self.get_release(release_id)
            r.raise_for_status()
            data = r.json()
            self._cache[release_id] = data
            return data
        except requests.RequestException as e:
            log.warning(f"  ⚠  Release fetch failed ({release_id}): {e}")
            return None


# ── Utilities ─────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    name = _ILLEGAL.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"-{2,}", "-", name)
    return name[:MAX_NAME_LENGTH].rstrip(" -")


def unique_dest(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_stem(f"{path.stem} ({n})")
        if not candidate.exists():
            return candidate
        n += 1




# ── Confidence scoring ────────────────────────────────────────────────────────

def score_track(result: dict, artist: str, title: str) -> int:
    if not _FUZZY:
        return 70
    combined = result.get("title", "")
    ra, rt = combined.split(" - ", 1) if " - " in combined else ("", combined)
    a_score = fuzz.token_sort_ratio(artist.lower(), ra.strip().lower()) if artist else 50
    t_score = fuzz.token_sort_ratio(title.lower(),  rt.strip().lower()) if title  else 50
    return int(a_score * 0.35 + t_score * 0.65)


def score_album(result: dict, artist: str, album: str) -> int:
    if not _FUZZY:
        return 70
    combined = result.get("title", "")
    ra, rt = combined.split(" - ", 1) if " - " in combined else ("", combined)
    a_score = fuzz.token_sort_ratio(artist.lower(), ra.strip().lower()) if artist else 50
    t_score = fuzz.token_sort_ratio(album.lower(),  rt.strip().lower()) if album  else 50
    return int(a_score * 0.40 + t_score * 0.60)


def score_by_tracklist(release: dict, title: str) -> int:
    if not _FUZZY:
        return 70
    tracklist = release.get("tracklist", [])
    if not tracklist:
        return 0
    t_lower = title.strip().lower()
    return max(
        fuzz.token_sort_ratio(t_lower, entry.get("title", "").strip().lower())
        for entry in tracklist
    )


# ── Metadata extraction ──────────────────────────────────────────────────────

def extract_meta(release: dict) -> dict:
    labels = release.get("labels", [])
    label  = labels[0].get("name", "").strip() if labels else ""
    catno  = labels[0].get("catno", "").strip() if labels else ""
    if catno.lower() in ("none", ""):
        catno = ""
    return {
        "release_title": release.get("title", "").strip(),
        "genre":         ", ".join(release.get("genres", [])),
        "styles":        ", ".join(release.get("styles", [])),
        "label":         label,
        "catno":         catno,
        "year":          str(release.get("year", "")).strip(),
        "album":         release.get("title", "").strip(),
        "discogs_id":    str(release.get("id", "")),
    }


# ── Bandcamp support ─────────────────────────────────────────────────────────

def _parse_bandcamp_json(html: str) -> Optional[dict]:
    match = re.search(r'var\s+TralbumData\s*=\s*({.*?});', html, re.S)
    if not match:
        return None
    text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"(['\n\r])", '"', text)
        text = re.sub(r'([a-zA-Z0-9_]+)\s*:', r'"\1":', text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None


def parse_bandcamp_url(url: str, default_artist: str, default_title: str) -> Optional[tuple]:
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith("http"):
        url = f"https://{url}"
    if "bandcamp.com" not in url.lower():
        return None

    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"  ⚠  Bandcamp fetch failed: {e}")
        return None

    html = resp.text
    artist = default_artist or ""
    title = default_title or ""
    release_title = ""
    label = "Bandcamp"
    year = ""
    genre = ""
    styles = ""

    json_data = _parse_bandcamp_json(html)
    if json_data:
        artist = json_data.get("artist", artist) or artist
        if isinstance(json_data.get("trackinfo"), list) and json_data["trackinfo"]:
            title = json_data["trackinfo"][0].get("title", title) or title
        release_title = json_data.get("album_title") or json_data.get("title") or release_title
        label = json_data.get("label_name", label) or label
    else:
        og_title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if og_title:
            og_value = og_title.group(1).strip()
            if " - " in og_value:
                a_s, t_s = og_value.split(" - ", 1)
                artist = artist or a_s.strip()
                title = title or t_s.strip()
            else:
                title = title or og_value
        by_artist = re.search(r'<meta itemprop="byArtist" content="([^"]+)"', html)
        if by_artist:
            artist = artist or by_artist.group(1).strip()
        name_tag = re.search(r'<meta itemprop="name" content="([^"]+)"', html)
        if name_tag:
            release_title = release_title or name_tag.group(1).strip()

    date_meta = re.search(r'<meta itemprop="datePublished" content="([^"]+)"', html)
    if date_meta:
        year = date_meta.group(1).strip()[:4]

    if not release_title:
        release_title = title or artist or "Bandcamp Release"
    if not artist:
        artist = "Unknown Artist"
    if not title:
        title = default_title or "Unknown Title"

    return ({
        "release_title": release_title,
        "album":         release_title,
        "genre":         genre,
        "styles":        styles,
        "label":         label,
        "catno":         "",
        "year":          year,
        "discogs_id":    "",
    }, artist, title)




# ── Audio info ────────────────────────────────────────────────────────────────

def read_audio_info(fp: Path) -> dict:
    info = {"total_time": "0", "bitrate": "0", "samplerate": "0"}
    try:
        ext = fp.suffix.lower()
        a = (
            MP3(fp)  if ext == ".mp3"  else
            FLAC(fp) if ext == ".flac" else
            AIFF(fp) if ext in (".aiff", ".aif") else
            MP4(fp)  if ext == ".m4a"  else None
        )
        if a and hasattr(a, "info"):
            info["total_time"] = str(round(getattr(a.info, "length",      0)))
            info["bitrate"]    = str(getattr(a.info, "bitrate",     0))
            info["samplerate"] = str(getattr(a.info, "sample_rate", 0))
    except Exception:
        pass
    return info






# ── Collection + Rekordbox DB write ──────────────────────────────────────────

def write_results_to_db(results: list, dry_run: bool = False) -> dict:
    """Write each processed track directly to Rekordbox master.db via dbwriter."""
    if dry_run or not results:
        return {"written": 0, "skipped": 0}

    if rekordbox_running():
        log.error("  ✗  Rekordbox is running — close it and re-run. Nothing written.")
        return {"written": 0, "skipped": len(results)}

    db_path = Path(os.environ.get("REKORDBOX_DB", DEFAULT_DB))
    backup = backup_db(db_path)
    db = Rekordbox6Database(path=str(db_path))
    written = 0
    try:
        writer = RekordboxWriter(db)
        for r in results:
            fp = r["file_path"]
            meta = r["meta"]
            analysis = {
                "bpm": meta.get("bpm", ""), "camelot": meta.get("initial_key", ""),
                "energy": meta.get("energy", ""),
                "danceability": meta.get("danceability", ""),
                "vibes": [v.strip() for v in meta.get("vibes", "").split(",") if v.strip()],
                "vocal": meta.get("vocal", ""), "moods": meta.get("_moods", []),
            }
            writer.add_track(
                wsl_path=str(fp), win_path=to_rb_windows_path(fp),
                filename=fp.name, title=r["title"], artist=r["artist"],
                meta=meta, analysis=analysis)
            written += 1
        db.commit()
    except Exception as e:
        restore_db(backup, db_path)
        log.error(f"  ✗  DB write failed: {e}. master.db restored from backup.")
        return {"written": 0, "skipped": len(results)}
    return {"written": written, "skipped": 0}


# ── Inbox discovery ───────────────────────────────────────────────────────────

def _item_type_label(item: Path) -> str:
    if item.is_file():
        ext = item.suffix.lower()
        if ext in AUDIO_EXTS:
            return f"audio file ({ext})"
        if ext in ARCHIVE_EXTS:
            return f"archive ({ext}) — extract manually before processing"
        return f"file ({ext or 'no extension'})"
    if item.is_dir():
        audio_inside = [f for f in item.rglob("*") if f.suffix.lower() in AUDIO_EXTS]
        n = len(audio_inside)
        return f"folder — {n} audio file(s) inside" if n else "folder — no audio files found inside"
    return "unknown"


def discover_items(inbox: Path) -> tuple:
    presentable = []
    skipped     = []

    for item in sorted(inbox.iterdir()):
        if item.name.startswith("."):
            skipped.append((item, "hidden"))
            continue

        if item.is_file():
            ext = item.suffix.lower()
            if item.name == Path(__file__).name:
                skipped.append((item, "this script"))
                continue
            if ext in NON_MUSIC_EXTS:
                skipped.append((item, f"non-music file ({ext})"))
                continue

        presentable.append(item)

    return presentable, skipped


def list_inbox(inbox: Path, as_json: bool = False):
    """List inbox contents, optionally as JSON."""
    presentable, skipped = discover_items(inbox)

    if as_json:
        items = []
        for item in presentable:
            entry = {"name": item.name, "type": "folder" if item.is_dir() else "file"}
            if item.is_file():
                entry["format"] = item.suffix.lower().lstrip(".")
                entry["size_mb"] = round(item.stat().st_size / (1024 * 1024), 1)
                tags = read_tags(item)
                if tags["artist"]:
                    entry["artist"] = tags["artist"]
                if tags["title"]:
                    entry["title"] = tags["title"]
            elif item.is_dir():
                audio = [f for f in item.rglob("*") if f.suffix.lower() in AUDIO_EXTS]
                entry["tracks"] = len(audio)
                if " - " in item.name:
                    parts = item.name.split(" - ", 1)
                    entry["artist"] = parts[0].strip()
                    entry["release"] = parts[1].strip()
            items.append(entry)
        print(json.dumps({"inbox": str(inbox), "count": len(items),
                           "items": items}, indent=2))
        return

    print(f"\n{'═' * 64}")
    print(f"  DJ Inbox — {inbox}")
    print(f"{'═' * 64}")
    print(f"  {len(presentable)} item(s)  │  {len(skipped)} silently skipped\n")

    for i, item in enumerate(presentable, 1):
        label = _item_type_label(item)
        print(f"  {i:3d}. {item.name}")
        print(f"       {label}")

    if not presentable:
        print("  Inbox is empty.")
    print()


# ── Interactive prompts ───────────────────────────────────────────────────────

def ask_item(item: Path, idx: int, total: int) -> bool:
    label = _item_type_label(item)
    print(f"\n  ┌─ [{idx}/{total}] {'─' * 50}")
    print(f"  │  {item.name}")
    print(f"  │  {label}")
    print(f"  ├{'─' * 61}")
    print(f"  │  [y]  Yes — include in this run")
    print(f"  │  [s]  Skip — leave in inbox")
    print(f"  │  [f]  Finish — stop asking, process approved so far")
    print(f"  │  [a]  Abort — stop everything")
    print(f"  └{'─' * 61}")

    while True:
        choice = input("  Choice [y/s/f/a]: ").strip().lower()
        if choice == "y":
            return True
        if choice in ("s", ""):
            return False
        if choice == "f":
            return None
        if choice == "a":
            sys.exit(0)


def fallback_prompt(fp, artist, title, results, scores, client, library,
                    threshold, dry_run):
    print(f"\n  {'─' * 62}")
    print(f"  Could not auto-match: {fp.name}")

    if results:
        print(f"\n  Top candidates (none reached {threshold}%):")
        for i, (r, s) in enumerate(zip(results[:3], scores[:3]), 1):
            labels = r.get("label", [])
            lbl    = f"  [{labels[0]}]" if labels else ""
            print(f"    [{i}] ({s:3d}%)  {r.get('title', '?')}{lbl}")
    else:
        print(f"\n  No Discogs results found.")

    print(f"\n  Options:")
    n = min(3, len(results))
    if n:
        print(f"    [1-{n}]  Choose a candidate")
    print(f"    [d]  Enter Discogs release ID")
    print(f"    [b]  Enter Bandcamp URL")
    print(f"    [m]  Enter metadata manually")
    print(f"    [s]  Skip — leave in inbox")
    print(f"  {'─' * 62}")

    while True:
        choice = input(f"  Choice: ").strip().lower()

        if choice == "s":
            return None

        if choice.isdigit() and 1 <= int(choice) <= n:
            result = results[int(choice) - 1]
            release = client.get_release(result["id"])
            if not release:
                print(f"  Could not fetch release — try another option.")
                continue
            meta = extract_meta(release)
            _print_meta(meta)
            _run_analysis(fp, meta, dry_run)
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist, title, meta, library, dry_run)

        if choice == "d":
            raw = input("  Discogs release ID: ").strip()
            if not raw.isdigit():
                continue
            release = client.get_release(int(raw))
            if not release:
                continue
            meta = extract_meta(release)
            _print_meta(meta)
            _run_analysis(fp, meta, dry_run)
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist, title, meta, library, dry_run)

        if choice == "b":
            raw = input("  Bandcamp URL: ").strip()
            parsed = parse_bandcamp_url(raw, artist, title)
            if not parsed:
                continue
            meta, artist_out, title_out = parsed
            _print_meta(meta)
            _run_analysis(fp, meta, dry_run)
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist_out, title_out, meta, library, dry_run)

        if choice == "m":
            meta, artist_out, title_out = _collect_manual_metadata(artist, title)
            _run_analysis(fp, meta, dry_run)
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist_out, title_out, meta, library, dry_run)


def _collect_manual_metadata(artist: str, title: str) -> tuple:
    print(f"\n  Enter metadata (press Enter to keep shown value):\n")

    def prompt(field, default):
        val = input(f"    {field:<12} [{default}]: ").strip()
        return val if val else default

    a = prompt("Artist",  artist or "")
    t = prompt("Title",   title or "")
    y = prompt("Year",    "")
    g = prompt("Genre",   "")
    s = prompt("Styles",  "")
    l = prompt("Label",   "Not On Label")
    c = prompt("Cat#",    "")
    r = prompt("Release", t)

    return {
        "release_title": r, "album": r, "genre": g, "styles": s,
        "label": l, "catno": c, "year": y, "discogs_id": "",
    }, a, t


# ── Destination builder ──────────────────────────────────────────────────────

def build_dest(artist, title, meta, library, src_ext):
    year_dir  = sanitize(meta["year"]) if meta.get("year") else "Unknown Year"
    lbl_str   = " ".join(filter(None, [meta.get("label", ""), meta.get("catno", "")]))
    album_dir = sanitize(meta.get("release_title") or artist or "Unknown Release")
    if lbl_str:
        album_dir = f"{album_dir} [{sanitize(lbl_str)}]"

    filename  = (f"{sanitize(artist or 'Unknown Artist')} - "
                 f"{sanitize(title or 'Unknown Title')}{src_ext.lower()}")
    dest_dir  = library / year_dir / album_dir
    dest_path = unique_dest(dest_dir / filename)
    return dest_dir, dest_path


def _move_file(fp, artist, title, meta, library, dry_run):
    dest_dir, dest_path = build_dest(artist, title, meta, library, fp.suffix)
    log.info(f"  {fp.name}  →  {dest_path.name}")

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(fp), str(dest_path))
        except Exception as e:
            log.error(f"  ✗  Move failed: {e}")
            return None

    return {
        "file_path": dest_path,
        "artist":    artist,
        "title":     title,
        "meta":      meta,
    }


def _run_analysis(fp: Path, meta: dict, dry_run: bool) -> dict:
    if dry_run:
        return meta

    existing_key = meta.get("initial_key", "")

    # Fast path: single-load coordinator (eliminates redundant beat tracking)
    if _FAST_ANALYSIS:
        log.info(f"  Analyzing audio…")
        r = analyze_track(fp, existing_key=existing_key)
        if r.get("bpm"):
            meta["bpm"] = r["bpm"]
        if r.get("camelot"):
            meta["initial_key"] = r["camelot"]
        if r.get("energy"):
            meta["energy"] = r["energy"]
        if r.get("danceability"):
            meta["danceability"] = r["danceability"]
        if r.get("cues"):
            meta["_cues"] = r["cues"]
        if r.get("vibes"):
            meta["vibes"] = ", ".join(r["vibes"])
        if r.get("vocal"):
            meta["vocal"] = r["vocal"]
        if r.get("loudness") and r["loudness"].get("gain_db") is not None:
            write_replaygain_tags(fp, r["loudness"])
            meta["_loudness"] = r["loudness"]

        # Log summary
        from .audio_analysis import format_analysis_log
        from .cue_detection import format_cues_log
        from .mood_detection import format_mood_log
        from .loudness import format_loudness_log
        log.info(f"  {format_analysis_log(r)}")
        if r.get("cues"):
            log.info(f"  {format_cues_log(r['cues'])}")
        if r.get("vibes") or r.get("vocal"):
            log.info(f"  {format_mood_log({'moods': r.get('moods', []), 'vibes': r.get('vibes', []), 'vocal': r.get('vocal', '')})}")
        if r.get("loudness"):
            log.info(f"  {format_loudness_log(r['loudness'])}")
        return meta

    # Fallback: individual modules (if analyzer not available)
    if _ANALYSIS:
        log.info(f"  Analyzing audio…")
        analysis = analyze_audio(fp, existing_key=existing_key)
        log.info(f"  {format_analysis_log(analysis)}")
        if analysis["bpm"]:
            meta["bpm"] = analysis["bpm"]
        if analysis["camelot"]:
            meta["initial_key"] = analysis["camelot"]
        if analysis["energy"]:
            meta["energy"] = analysis["energy"]
        if analysis["danceability"]:
            meta["danceability"] = analysis["danceability"]
    if _CUE_DETECTION:
        log.info(f"  Detecting cue points…")
        cues = detect_cues(fp)
        if cues:
            log.info(f"  {format_cues_log(cues)}")
            meta["_cues"] = cues
    if _MOOD_DETECTION:
        log.info(f"  Classifying mood…")
        energy_int = int(meta.get("energy") or "5")
        mood = detect_mood(fp, energy=energy_int)
        log.info(f"  {format_mood_log(mood)}")
        if mood["vibes"]:
            meta["vibes"] = ", ".join(mood["vibes"])
        if mood["vocal"]:
            meta["vocal"] = mood["vocal"]
    if _LOUDNESS:
        log.info(f"  Measuring loudness…")
        loudness = measure_loudness(fp)
        log.info(f"  {format_loudness_log(loudness)}")
        if loudness["gain_db"] is not None:
            write_replaygain_tags(fp, loudness)
            meta["_loudness"] = loudness
    return meta


def _print_meta(meta: dict):
    log.info(f"  Release: {meta['release_title']}")
    log.info(f"  Genre: {meta['genre'] or '—'}  │  Styles: {meta['styles'] or '—'}")
    log.info(f"  Label: {meta['label'] or '—'}  │  Cat#: {meta['catno'] or '—'}  │  Year: {meta['year'] or '—'}")


# ── Single-file processor ────────────────────────────────────────────────────

def process_file(fp, client, library, threshold, dry_run,
                 discogs_id=None):
    file_tags = read_tags(fp)
    artist    = file_tags["artist"]
    title     = file_tags["title"]

    if not title:
        log.info("  ⚠  No title found — leaving in inbox.")
        return None

    log.info(f"  Artist: {artist or '(unknown)'}  │  Title: {title}")

    # Direct Discogs ID override
    if discogs_id:
        release = client.get_release(discogs_id)
        if release:
            meta = extract_meta(release)
            _print_meta(meta)
            _run_analysis(fp, meta, dry_run)
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist, title, meta, library, dry_run)
        log.warning(f"  ⚠  Could not fetch Discogs release {discogs_id}")

    # Pass 1: search + first-pass scoring
    results = client.search(f"{artist} {title}")
    if not results:
        log.info("  ✗  No Discogs results.")
        return fallback_prompt(fp, artist, title, [], [], client, library,
                               threshold, dry_run)

    scores = [score_track(r, artist, title) for r in results]
    top    = scores[0]
    log.info(f"  Pass 1 — best: {results[0].get('title', '?')}  ({top}%)")

    if top >= threshold:
        log.info(f"  ✓  Auto-approved on pass 1.")
        release = client.get_release(results[0]["id"])
        if not release:
            return fallback_prompt(fp, artist, title, results, scores, client,
                                   library, threshold, dry_run)
        meta = extract_meta(release)
        _print_meta(meta)
        _run_analysis(fp, meta, dry_run)
        write_tags(fp, meta, dry_run)
        return _move_file(fp, artist, title, meta, library, dry_run)

    if top < REVIEW_THRESHOLD:
        log.info(f"  ✗  Score too low for second pass ({top}%).")
        return fallback_prompt(fp, artist, title, results, scores, client,
                               library, threshold, dry_run)

    # Pass 2: tracklist check on top 3
    log.info(f"  Borderline ({top}%) — pass 2 tracklist check…")

    best_release = None
    best_score   = 0
    best_result  = None

    for i, result in enumerate(results[:3]):
        release = client.get_release(result["id"])
        if not release:
            continue
        tl_score = score_by_tracklist(release, title)
        log.info(f"    Candidate {i+1}: {result.get('title', '?')} → {tl_score}%")
        if tl_score > best_score:
            best_score   = tl_score
            best_release = release
            best_result  = result

    if best_score >= threshold:
        log.info(f"  ✓  Auto-approved on pass 2 ({best_score}%).")
        meta = extract_meta(best_release)
        _print_meta(meta)
        _run_analysis(fp, meta, dry_run)
        write_tags(fp, meta, dry_run)
        return _move_file(fp, artist, title, meta, library, dry_run)

    log.info(f"  ✗  Pass 2 best {best_score}% — below threshold.")
    final_results = ([best_result] + [r for r in results[:3] if r != best_result]
                     if best_result else results)
    final_scores  = [best_score] + scores[1:3]
    return fallback_prompt(fp, artist, title, final_results, final_scores,
                           client, library, threshold, dry_run)


# ── Release-folder processor ─────────────────────────────────────────────────

def process_release_folder(folder, client, library, threshold, dry_run,
                           discogs_id=None):
    stem = folder.name
    if " - " in stem:
        parts  = stem.split(" - ", 1)
        artist = parts[0].strip()
        album  = parts[1].strip()
    else:
        artist = ""
        album  = stem.strip()

    log.info(f"  Folder: {stem}")
    log.info(f"  Artist: {artist or '(unknown)'}  │  Album: {album}")

    release = None
    if discogs_id:
        release = client.get_release(discogs_id)

    if not release:
        results = client.search(f"{artist} {album}")
        if not results:
            log.info("  ✗  No Discogs results — leaving in inbox.")
            return []

        scores = [score_album(r, artist, album) for r in results]
        top    = scores[0]
        log.info(f"  Best match: {results[0].get('title', '?')}  ({top}%)")

        if top < threshold:
            log.info(f"  —  Below threshold ({top}%) — leaving in inbox.")
            return []

        release = client.get_release(results[0]["id"])
        if not release:
            return []

    meta = extract_meta(release)
    _print_meta(meta)

    results_out = []
    audio_files = sorted(
        [f for f in folder.rglob("*") if f.suffix.lower() in AUDIO_EXTS],
        key=lambda f: f.name,
    )

    for fp in audio_files:
        file_tags = read_tags(fp)
        f_artist  = file_tags["artist"] or artist
        f_title   = file_tags["title"]  or fp.stem
        file_meta = dict(meta, album=meta["release_title"])

        _run_analysis(fp, file_meta, dry_run)
        write_tags(fp, file_meta, dry_run)

        dest_dir, dest_path = build_dest(f_artist, f_title, file_meta,
                                         library, fp.suffix)
        log.info(f"    {fp.name}  →  {dest_path.name}")

        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(fp), str(dest_path))
            except Exception as e:
                log.error(f"    ✗  Move failed: {e}")
                continue

        results_out.append({
            "file_path": dest_path,
            "artist":    f_artist,
            "title":     f_title,
            "meta":      file_meta,
        })

    if not dry_run:
        remaining = [f for f in folder.rglob("*")
                     if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
        if not remaining:
            try:
                shutil.rmtree(folder)
                log.info(f"  ✓  Cleaned up: {folder.name}/")
            except Exception:
                pass

    return results_out




# ── Main pipeline ────────────────────────────────────────────────────────────

def run_pipeline(
    inbox, library, token, threshold, dry_run, target,
    auto_approve, discogs_id, as_json,
):
    client = DiscogsClient(token)

    if not as_json:
        print(f"\n{'═' * 64}")
        print(f"  trackstage")
        print(f"{'═' * 64}")
        print(f"  Inbox:     {inbox}")
        print(f"  Library:   {library}")
        print(f"  Threshold: ≥{threshold}%")
        if auto_approve:
            print(f"  Mode:      AUTO-APPROVE")
        if dry_run:
            print(f"  *** DRY RUN ***")
        print(f"  Verifying Discogs token...", end=" ", flush=True)

    if not client.verify():
        if as_json:
            print(json.dumps({"error": "Invalid Discogs token"}))
        else:
            print("FAILED")
        sys.exit(1)

    if not as_json:
        print("OK ✓\n")

    # Item discovery
    if target is not None:
        target_path = inbox / target if not target.is_absolute() else target
        if not target_path.exists():
            if as_json:
                print(json.dumps({"error": f"Target not found: {target_path}"}))
            else:
                log.error(f"  ERROR: Target not found: {target_path}")
            sys.exit(1)
        presentable = [target_path]
        skipped = []
    else:
        presentable, skipped = discover_items(inbox)

    if not as_json:
        print(f"  {len(presentable)} item(s) found  │  {len(skipped)} skipped\n")

    if not presentable:
        if as_json:
            print(json.dumps({"processed": 0, "results": []}))
        else:
            print("  Nothing to process.")
        return

    # Per-item approval
    approved_files   = []
    approved_folders = []

    for idx, item in enumerate(presentable, 1):
        if auto_approve or target is not None:
            include = True
        else:
            include = ask_item(item, idx, len(presentable))

        if include is None:
            break
        if not include:
            continue

        if item.is_file():
            if item.suffix.lower() in AUDIO_EXTS:
                approved_files.append(item)
            elif not as_json:
                print(f"  ⚠  {item.name} is not a supported audio format.")
        elif item.is_dir():
            approved_folders.append(item)

    total = len(approved_files) + len(approved_folders)
    if not as_json:
        print(f"\n  ── Approved: {len(approved_files)} file(s) + "
              f"{len(approved_folders)} folder(s) {'─' * 18}")

    if total == 0:
        if as_json:
            print(json.dumps({"processed": 0, "results": []}))
        else:
            print("\n  Nothing approved.")
        return

    # Process
    all_results = []

    if approved_files and not as_json:
        print(f"\n  ── Audio files {'─' * 48}")

    for idx, fp in enumerate(approved_files, 1):
        if not as_json:
            print(f"\n[{idx:4d}/{total}] {fp.name}")
        result = process_file(fp, client, library, threshold, dry_run,
                              discogs_id)
        if result:
            all_results.append(result)

    offset = len(approved_files)
    if approved_folders and not as_json:
        print(f"\n  ── Release folders {'─' * 44}")

    for idx, folder in enumerate(approved_folders, 1):
        if not as_json:
            print(f"\n[{offset + idx:4d}/{total}]  {folder.name}/")
        results = process_release_folder(folder, client, library, threshold,
                                         dry_run, discogs_id)
        all_results.extend(results)

    # Write to Rekordbox DB
    if not as_json:
        print(f"\n  ── Rekordbox database {'─' * 41}")

    if all_results:
        db_result = write_results_to_db(all_results, dry_run)
    else:
        db_result = {"written": 0, "skipped": 0}
        if not as_json:
            print("  — No tracks processed — database unchanged.")

    processed = len(all_results)

    if as_json:
        json_results = []
        for r in all_results:
            json_results.append({
                "artist":  r.get("artist", ""),
                "title":   r.get("title", ""),
                "dest":    str(r.get("file_path", "")),
                "genre":   r["meta"].get("genre", ""),
                "styles":  r["meta"].get("styles", ""),
                "label":   r["meta"].get("label", ""),
                "year":    r["meta"].get("year", ""),
            })

        print(json.dumps({
            "processed":  processed,
            "dry_run":    dry_run,
            "results":    json_results,
            "db_written": db_result["written"],
        }, indent=2))
    else:
        print(f"\n{'═' * 64}")
        print(f"  ✓  Processed & moved : {processed}")
        print(f"  ✓  Written to DB     : {db_result['written']}")
        print(f"  —  Left in inbox     : {total - processed}")
        print(f"  —  Silently skipped  : {len(skipped)}")
        print(f"{'═' * 64}\n")

        if dry_run:
            print("  Re-run without --dry-run to apply changes.\n")
        elif processed:
            print("  Tracks written directly to Rekordbox — launch Rekordbox to see them.\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Subcommand dispatch: `trackstage add "<query>" ...`
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        from .add import main as add_main
        sys.exit(add_main(sys.argv[2:]))

    inbox_default   = os.environ.get("INBOX_PATH", "")
    library_default = os.environ.get("LIBRARY_PATH", "")

    p = argparse.ArgumentParser(
        description="trackstage: Discogs → Tag → Analyze → Move → Rekordbox DB + Playlists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--list", action="store_true",
                   help="List inbox contents and exit")
    p.add_argument("--target", type=Path, default=None,
                   help="Process only this file or folder (relative to inbox)")
    p.add_argument("--discogs-id", type=int, default=None,
                   help="Use this Discogs release ID instead of searching")
    p.add_argument("--inbox", type=Path,
                   default=Path(inbox_default) if inbox_default else None,
                   help="DJ Inbox folder")
    p.add_argument("--library", type=Path,
                   default=Path(library_default) if library_default else None,
                   help="Library root folder")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help=f"Discogs match confidence to auto-approve (default: {DEFAULT_THRESHOLD})")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview without modifying or moving files")
    p.add_argument("--auto-approve", "-y", action="store_true",
                   help="Skip per-item prompts")
    p.add_argument("--json", action="store_true",
                   help="Output results as JSON (for automation)")
    args = p.parse_args()

    # Validate paths
    for attr, label in [("inbox", "Inbox"), ("library", "Library")]:
        val = getattr(args, attr)
        if val is None:
            print(f"ERROR: {label} path not configured. "
                  f"Set {attr.upper()}_PATH in .env or pass --{attr}.")
            sys.exit(1)
        if not val.exists():
            print(f"ERROR: {label} folder not found: {val}")
            sys.exit(1)

    if args.list:
        list_inbox(args.inbox, args.json)
        return

    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if not token:
        print("ERROR: DISCOGS_TOKEN not set. Add it to .env or set as env var.")
        sys.exit(1)

    run_pipeline(
        inbox=args.inbox,
        library=args.library,
        token=token,
        threshold=args.threshold,
        dry_run=args.dry_run,
        target=args.target,
        auto_approve=args.auto_approve,
        discogs_id=args.discogs_id,
        as_json=args.json,
    )


if __name__ == "__main__":
    main()
