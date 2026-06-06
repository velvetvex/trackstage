#!/usr/bin/env python3
"""
pipeline.py — DJ Library Pipeline

Scan inbox → Discogs lookup → tag → rename → move to Library → update Rekordbox
XML with collection entries and auto-playlists (by Style, Label, and Recent).

Usage:
    python3 pipeline.py --list                          # show inbox contents
    python3 pipeline.py --list --json                   # JSON inventory
    python3 pipeline.py --target "Artist - EP" -y       # process one item
    python3 pipeline.py -y                              # process everything
    python3 pipeline.py --target "track.flac" -y --discogs-id 12345
    python3 pipeline.py --target "track.flac" -y --playlist "Summer 2026"
    python3 pipeline.py --dry-run -y                    # preview without changes

Config (.env in same folder as this script):
    DISCOGS_TOKEN=yourtoken
    INBOX_PATH=/path/to/inbox
    LIBRARY_PATH=/path/to/library
    XML_PATH=/path/to/rekordbox.xml

Requirements:
    pip install mutagen thefuzz python-Levenshtein requests python-dotenv
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
from urllib.parse import quote
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import (
        ID3, ID3NoHeaderError,
        TCON, TPUB, TDRC, TYER, TALB, COMM, TXXX, TIT2, TPE1,
    )
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


# ── Constants ─────────────────────────────────────────────────────────────────

DISCOGS_API_BASE  = "https://api.discogs.com"
REQUESTS_PER_MIN  = 55
DEFAULT_THRESHOLD = 85
REVIEW_THRESHOLD  = 60
MAX_NAME_LENGTH   = 180
RECENT_PLAYLIST_CAP = 100

AUDIO_EXTS   = {".mp3", ".flac", ".aiff", ".aif", ".m4a"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}

NON_MUSIC_EXTS = {
    ".py", ".csv", ".xml", ".txt", ".ini", ".log", ".json", ".yaml", ".toml",
    ".asd", ".als", ".alp", ".nml", ".nfo", ".ptx", ".ptf", ".rpp",
    ".m3u", ".m3u8", ".cue", ".pls",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".pdf", ".docx",
}

KIND_MAP = {
    ".mp3":  "MP3 File",
    ".flac": "FLAC File",
    ".aiff": "AIFF File",
    ".aif":  "AIFF File",
    ".m4a":  "AAC File",
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


# ── Tag reading ───────────────────────────────────────────────────────────────

def read_tags(fp: Path) -> dict:
    ext  = fp.suffix.lower()
    tags = {"artist": "", "title": ""}
    try:
        if ext == ".mp3":
            a = MP3(fp, ID3=ID3)
            if a.tags:
                tags["artist"] = str(a.tags.get("TPE1", "")).strip()
                tags["title"]  = str(a.tags.get("TIT2", "")).strip()
        elif ext == ".flac":
            a = FLAC(fp)
            tags["artist"] = ", ".join(a.get("artist", []))
            tags["title"]  = ", ".join(a.get("title",  []))
        elif ext in (".aiff", ".aif"):
            a = AIFF(fp)
            if a.tags:
                tags["artist"] = str(a.tags.get("TPE1", "")).strip()
                tags["title"]  = str(a.tags.get("TIT2", "")).strip()
        elif ext == ".m4a":
            a = MP4(fp)
            tags["artist"] = ", ".join(a.get("\xa9ART", []))
            tags["title"]  = ", ".join(a.get("\xa9nam", []))
    except Exception:
        pass

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


# ── Tag writing ───────────────────────────────────────────────────────────────

def _build_comment(meta: dict) -> str:
    parts = [p for p in [
        meta.get("styles", ""),
        f"Cat# {meta['catno']}" if meta.get("catno") else "",
    ] if p]
    return " | ".join(parts)


def write_mp3(fp: Path, meta: dict, dry_run: bool) -> bool:
    try:
        try:
            tags = ID3(fp)
        except ID3NoHeaderError:
            tags = ID3()
        if meta.get("genre"):  tags["TCON"] = TCON(encoding=3, text=meta["genre"])
        if meta.get("album"):  tags["TALB"] = TALB(encoding=3, text=meta["album"])
        if meta.get("label"):  tags["TPUB"] = TPUB(encoding=3, text=meta["label"])
        if meta.get("year"):
            tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
            tags["TYER"] = TYER(encoding=3, text=meta["year"])
        if meta.get("catno"):
            tags["TXXX:CATALOGNUMBER"] = TXXX(
                encoding=3, desc="CATALOGNUMBER", text=meta["catno"]
            )
        c = _build_comment(meta)
        if c:
            tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=c)
        if not dry_run:
            tags.save(fp, v2_version=3)
        return True
    except Exception as e:
        log.error(f"  ✗  MP3 write failed: {e}")
        return False


def write_flac(fp: Path, meta: dict, dry_run: bool) -> bool:
    try:
        a = FLAC(fp)
        if meta.get("genre"):  a["genre"]         = [meta["genre"]]
        if meta.get("styles"): a["style"]          = [meta["styles"]]
        if meta.get("album"):  a["album"]          = [meta["album"]]
        if meta.get("label"):
            a["label"]        = [meta["label"]]
            a["organization"] = [meta["label"]]
        if meta.get("catno"):  a["catalognumber"]  = [meta["catno"]]
        if meta.get("year"):
            a["date"] = [meta["year"]]
            a["year"] = [meta["year"]]
        c = _build_comment(meta)
        if c:
            a["comment"] = [c]
        if not dry_run:
            a.save()
        return True
    except Exception as e:
        log.error(f"  ✗  FLAC write failed: {e}")
        return False


def write_aiff(fp: Path, meta: dict, dry_run: bool) -> bool:
    try:
        a = AIFF(fp)
        if a.tags is None:
            a.add_tags()
        if meta.get("genre"):  a.tags["TCON"] = TCON(encoding=3, text=meta["genre"])
        if meta.get("album"):  a.tags["TALB"] = TALB(encoding=3, text=meta["album"])
        if meta.get("label"):  a.tags["TPUB"] = TPUB(encoding=3, text=meta["label"])
        if meta.get("year"):   a.tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
        if meta.get("catno"):
            a.tags["TXXX:CATALOGNUMBER"] = TXXX(
                encoding=3, desc="CATALOGNUMBER", text=meta["catno"]
            )
        c = _build_comment(meta)
        if c:
            a.tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=c)
        if not dry_run:
            a.save()
        return True
    except Exception as e:
        log.error(f"  ✗  AIFF write failed: {e}")
        return False


def write_m4a(fp: Path, meta: dict, dry_run: bool) -> bool:
    try:
        a = MP4(fp)
        if meta.get("genre"):  a["\xa9gen"] = [meta["genre"]]
        if meta.get("album"):  a["\xa9alb"] = [meta["album"]]
        if meta.get("year"):   a["\xa9day"] = [meta["year"]]
        if meta.get("label"):
            a["----:com.apple.iTunes:LABEL"] = [meta["label"].encode("utf-8")]
        if meta.get("catno"):
            a["----:com.apple.iTunes:CATALOGNUMBER"] = [meta["catno"].encode("utf-8")]
        if meta.get("styles"):
            a["----:com.apple.iTunes:STYLE"] = [meta["styles"].encode("utf-8")]
        c = _build_comment(meta)
        if c:
            a["\xa9cmt"] = [c]
        if not dry_run:
            a.save()
        return True
    except Exception as e:
        log.error(f"  ✗  M4A write failed: {e}")
        return False


def write_tags(fp: Path, meta: dict, dry_run: bool) -> bool:
    writers = {
        ".mp3":  write_mp3,
        ".flac": write_flac,
        ".aiff": write_aiff,
        ".aif":  write_aiff,
        ".m4a":  write_m4a,
    }
    fn = writers.get(fp.suffix.lower())
    if fn:
        return fn(fp, meta, dry_run)
    log.warning(f"  ⚠  No tag writer for format: {fp.suffix}")
    return False


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


# ── Rekordbox XML helpers ─────────────────────────────────────────────────────

def to_rb_location(path: Path) -> str:
    posix   = path.as_posix()
    encoded = quote(posix, safe="/:@!$&'()*+,;=-._~")
    if not encoded.startswith("/"):
        encoded = "/" + encoded
    return f"file://localhost{encoded}"


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


def _save_xml(tree: ET.ElementTree, xml_path: Path):
    ET.indent(tree, space="  ")
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(xml_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)


# ── Playlist management ──────────────────────────────────────────────────────

def _find_or_create_folder(parent_node: ET.Element, name: str) -> ET.Element:
    for child in parent_node:
        if child.get("Type") == "0" and child.get("Name") == name:
            return child
    folder = ET.SubElement(parent_node, "NODE", Type="0", Name=name, Count="0")
    return folder


def _find_or_create_playlist(parent_node: ET.Element, name: str) -> ET.Element:
    for child in parent_node:
        if child.get("Type") == "1" and child.get("Name") == name:
            return child
    playlist = ET.SubElement(parent_node, "NODE",
                             Type="1", Name=name, KeyType="0", Entries="0")
    return playlist


def _add_track_to_playlist(playlist: ET.Element, track_id: str):
    for existing in playlist:
        if existing.get("Key") == track_id:
            return
    ET.SubElement(playlist, "TRACK", Key=track_id)
    current = int(playlist.get("Entries", "0"))
    playlist.set("Entries", str(current + 1))


def _trim_playlist(playlist: ET.Element, max_entries: int):
    tracks = list(playlist.findall("TRACK"))
    if len(tracks) <= max_entries:
        return
    for track in tracks[:len(tracks) - max_entries]:
        playlist.remove(track)
    playlist.set("Entries", str(max_entries))


def _update_folder_counts(root_node: ET.Element):
    count = 0
    for child in root_node:
        if child.tag == "NODE":
            count += 1
            if child.get("Type") == "0":
                _update_folder_counts(child)
    root_node.set("Count", str(count))


def update_playlists(
    root: ET.Element,
    track_entries: list,
    custom_playlist: Optional[str] = None,
    dry_run: bool = False,
):
    """
    Update playlist structure with newly added tracks.

    track_entries: list of {"track_id": str, "meta": dict}
    """
    if not track_entries or dry_run:
        return

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        playlists_node = ET.SubElement(root, "PLAYLISTS")

    root_node = playlists_node.find("NODE[@Name='ROOT']")
    if root_node is None:
        root_node = ET.SubElement(playlists_node, "NODE",
                                  Type="0", Name="ROOT", Count="0")

    styles_folder = _find_or_create_folder(root_node, "Styles")
    labels_folder = _find_or_create_folder(root_node, "Labels")
    recent_pl = _find_or_create_playlist(root_node, "Recent")

    custom_pl = None
    if custom_playlist:
        custom_pl = _find_or_create_playlist(root_node, custom_playlist)

    for entry in track_entries:
        tid = entry["track_id"]
        meta = entry["meta"]

        styles_str = meta.get("styles", "")
        for style in styles_str.split(", "):
            style = style.strip()
            if style:
                pl = _find_or_create_playlist(styles_folder, style)
                _add_track_to_playlist(pl, tid)

        label = meta.get("label", "").strip()
        if label:
            pl = _find_or_create_playlist(labels_folder, label)
            _add_track_to_playlist(pl, tid)

        _add_track_to_playlist(recent_pl, tid)

        if custom_pl:
            _add_track_to_playlist(custom_pl, tid)

    _trim_playlist(recent_pl, RECENT_PLAYLIST_CAP)
    _update_folder_counts(root_node)


# ── Collection + XML update ──────────────────────────────────────────────────

def append_tracks_to_xml(
    xml_path: Path,
    new_tracks: list,
    custom_playlist: Optional[str] = None,
    dry_run: bool = False,
) -> list:
    """
    Add tracks to COLLECTION and update playlists.
    Returns list of result dicts with track_id for reporting.
    """
    if not new_tracks:
        return []

    tree, root, max_id = load_or_bootstrap_xml(xml_path)
    collection = root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(root, "COLLECTION", Entries="0")

    existing_locs = {t.get("Location", "") for t in collection.findall("TRACK")}

    added = []
    for entry in new_tracks:
        fp     = entry["file_path"]
        meta   = entry["meta"]
        artist = sanitize_xml(entry.get("artist", ""))
        title  = sanitize_xml(entry.get("title", ""))

        loc = to_rb_location(fp)
        if loc in existing_locs:
            log.info(f"  — XML: already indexed, skipping: {fp.name}")
            continue

        max_id += 1
        ainfo   = read_audio_info(fp) if fp.exists() else {
            "total_time": "0", "bitrate": "0", "samplerate": "0"
        }
        comment = sanitize_xml(_build_comment(meta))

        attrs = {
            "TrackID":     str(max_id),
            "Name":        title,
            "Artist":      artist,
            "Composer":    "",
            "Album":       sanitize_xml(meta.get("album", "")),
            "Grouping":    "",
            "Genre":       sanitize_xml(meta.get("genre", "")),
            "Kind":        KIND_MAP.get(fp.suffix.lower(), "Unknown"),
            "Size":        str(fp.stat().st_size) if fp.exists() else "0",
            "TotalTime":   ainfo["total_time"],
            "DiscNumber":  "0",
            "TrackNumber": "0",
            "Year":        sanitize_xml(meta.get("year", "")),
            "AverageBpm":  "0.00",
            "DateAdded":   str(date.today()),
            "BitRate":     ainfo["bitrate"],
            "SampleRate":  ainfo["samplerate"],
            "Comments":    comment,
            "PlayCount":   "0",
            "Rating":      "0",
            "Location":    loc,
            "Remixer":     "",
            "Tonality":    "",
            "Label":       sanitize_xml(meta.get("label", "")),
            "Mix":         "",
        }

        if not dry_run:
            ET.SubElement(collection, "TRACK", **attrs)
        added.append({"track_id": str(max_id), "meta": meta,
                       "artist": artist, "title": title})
        existing_locs.add(loc)

    if not dry_run and added:
        current = int(collection.get("Entries", "0"))
        collection.set("Entries", str(current + len(added)))
        update_playlists(root, added, custom_playlist, dry_run)
        _save_xml(tree, xml_path)

    verb = "[DRY RUN] Would add" if dry_run else "✓  Added"
    log.info(f"  {verb} {len(added)} track(s) to XML → {xml_path}")

    return added


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
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist, title, meta, library, dry_run)

        if choice == "b":
            raw = input("  Bandcamp URL: ").strip()
            parsed = parse_bandcamp_url(raw, artist, title)
            if not parsed:
                continue
            meta, artist_out, title_out = parsed
            _print_meta(meta)
            write_tags(fp, meta, dry_run)
            return _move_file(fp, artist_out, title_out, meta, library, dry_run)

        if choice == "m":
            meta, artist_out, title_out = _collect_manual_metadata(artist, title)
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
    inbox, library, xml_path, token, threshold, dry_run, target,
    auto_approve, discogs_id, custom_playlist, as_json,
):
    client = DiscogsClient(token)

    if not as_json:
        print(f"\n{'═' * 64}")
        print(f"  DJ Library Pipeline")
        print(f"{'═' * 64}")
        print(f"  Inbox:     {inbox}")
        print(f"  Library:   {library}")
        print(f"  XML:       {xml_path}")
        print(f"  Threshold: ≥{threshold}%")
        if auto_approve:
            print(f"  Mode:      AUTO-APPROVE")
        if dry_run:
            print(f"  *** DRY RUN ***")
        if custom_playlist:
            print(f"  Playlist:  {custom_playlist}")
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

    # Update XML
    if not as_json:
        print(f"\n  ── Rekordbox XML {'─' * 46}")

    if all_results:
        added = append_tracks_to_xml(xml_path, all_results, custom_playlist,
                                     dry_run)
    else:
        added = []
        if not as_json:
            print("  — No tracks processed — XML unchanged.")

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

        playlists_added = set()
        for a in added:
            meta = a["meta"]
            for s in meta.get("styles", "").split(", "):
                if s.strip():
                    playlists_added.add(f"Styles/{s.strip()}")
            if meta.get("label", "").strip():
                playlists_added.add(f"Labels/{meta['label'].strip()}")
            playlists_added.add("Recent")
            if custom_playlist:
                playlists_added.add(custom_playlist)

        print(json.dumps({
            "processed": processed,
            "dry_run":   dry_run,
            "results":   json_results,
            "playlists": sorted(playlists_added),
            "xml_path":  str(xml_path),
        }, indent=2))
    else:
        print(f"\n{'═' * 64}")
        print(f"  ✓  Processed & moved : {processed}")
        print(f"  —  Left in inbox     : {total - processed}")
        print(f"  —  Silently skipped  : {len(skipped)}")
        print(f"{'═' * 64}\n")

        if dry_run:
            print("  Re-run without --dry-run to apply changes.\n")
        elif processed:
            print(
                "  In Rekordbox 7: File → Import → Import rekordbox XML File\n"
                f"  Select: {xml_path}\n"
            )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    inbox_default   = os.environ.get("INBOX_PATH", "")
    library_default = os.environ.get("LIBRARY_PATH", "")
    xml_default     = os.environ.get("XML_PATH", "")

    p = argparse.ArgumentParser(
        description="DJ Library Pipeline: Discogs → Tag → Move → Rekordbox XML + Playlists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--list", action="store_true",
                   help="List inbox contents and exit")
    p.add_argument("--target", type=Path, default=None,
                   help="Process only this file or folder (relative to inbox)")
    p.add_argument("--discogs-id", type=int, default=None,
                   help="Use this Discogs release ID instead of searching")
    p.add_argument("--playlist", type=str, default=None,
                   help="Also add tracks to this custom Rekordbox playlist")
    p.add_argument("--inbox", type=Path,
                   default=Path(inbox_default) if inbox_default else None,
                   help="DJ Inbox folder")
    p.add_argument("--library", type=Path,
                   default=Path(library_default) if library_default else None,
                   help="Library root folder")
    p.add_argument("--xml", type=Path,
                   default=Path(xml_default) if xml_default else None,
                   help="Rekordbox XML path")
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

    if args.xml is None:
        print("ERROR: XML path not configured. Set XML_PATH in .env or pass --xml.")
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
        xml_path=args.xml,
        token=token,
        threshold=args.threshold,
        dry_run=args.dry_run,
        target=args.target,
        auto_approve=args.auto_approve,
        discogs_id=args.discogs_id,
        custom_playlist=args.playlist,
        as_json=args.json,
    )


if __name__ == "__main__":
    main()
