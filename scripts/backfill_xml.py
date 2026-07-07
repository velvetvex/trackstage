#!/usr/bin/env python3
"""Backfill Rekordbox XML from analysis cache — no re-analysis needed."""

import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from dotenv import load_dotenv
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.aiff import AIFF
from mutagen.id3 import ID3, ID3NoHeaderError, TIT1

from trackstage.cache import AnalysisCache
from trackstage.pipeline import (
    to_rb_location, sanitize_xml, load_or_bootstrap_xml, _save_xml,
)
from trackstage.cue_detection import CUE_COLORS

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}

# Rekordbox Rating: 0/51/102/153/204/255 → 0-5 stars
ENERGY_TO_RATING = {
    "1": 51, "2": 51,
    "3": 102, "4": 102,
    "5": 153, "6": 153,
    "7": 204, "8": 204,
    "9": 255, "10": 255,
}

# Rekordbox track colors (hex values used in XML Colour attribute)
MOOD_TO_COLOUR = {
    "aggressive": "0xFF0000",  # Red
    "happy":      "0xFFA500",  # Orange
    "party":      "0xFFFF00",  # Yellow
    "relaxed":    "0x00FF00",  # Green
    "sad":        "0x8000FF",  # Purple
}


def build_grouping(r: dict) -> str:
    parts = list(r.get("vibes", []))
    for m in r.get("moods", []):
        if m not in parts:
            parts.append(m)
    return ", ".join(parts)


def pick_colour(moods: list) -> str:
    priority = ["aggressive", "sad", "happy", "party", "relaxed"]
    for mood in priority:
        if mood in moods:
            return MOOD_TO_COLOUR[mood]
    return ""


def merge_xml_comment(existing: str, r: dict) -> str:
    """Append analysis fields to existing XML comment without clobbering Discogs data."""
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

    if r.get("energy"):
        cleaned.append(f"Energy: {r['energy']}/10")
    if r.get("danceability"):
        cleaned.append(f"Dance: {r['danceability']}/10")
    if r.get("vibes"):
        cleaned.append(", ".join(r["vibes"]))
    if r.get("vocal"):
        cleaned.append(r["vocal"])
    return " | ".join(cleaned)


def write_grouping_tag(fp: Path, grouping: str) -> bool:
    ext = fp.suffix.lower()
    try:
        if ext == '.flac':
            a = FLAC(fp)
            a["grouping"] = [grouping]
            a.save()
        elif ext == '.mp3':
            try:
                tags = ID3(fp)
            except ID3NoHeaderError:
                tags = ID3()
            tags["TIT1"] = TIT1(encoding=3, text=grouping)
            tags.save(fp, v2_version=3)
        elif ext in ('.aiff', '.aif'):
            a = AIFF(fp)
            if a.tags is None:
                a.add_tags()
            a.tags["TIT1"] = TIT1(encoding=3, text=grouping)
            a.save()
        elif ext == '.m4a':
            a = MP4(fp)
            a["\xa9grp"] = [grouping]
            a.save()
        return True
    except Exception as e:
        print(f"  TAG ERROR: {fp.name}: {e}")
        return False


def main():
    library = Path(os.environ.get("LIBRARY_PATH", ""))
    xml_path = Path(os.environ.get("XML_PATH", ""))

    if not library.is_dir():
        print(f"ERROR: LIBRARY_PATH not found: {library}")
        sys.exit(1)
    if not xml_path.name:
        print("ERROR: XML_PATH not set in .env.")
        sys.exit(1)

    cache = AnalysisCache()
    print(f"Cache: {cache.count()} entries")

    tree, root, max_id = load_or_bootstrap_xml(xml_path)
    collection = root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(root, "COLLECTION", Entries="0")

    existing_loc_map = {}
    for track_el in collection.findall("TRACK"):
        existing_loc_map[track_el.get("Location", "")] = track_el

    print(f"XML: {len(existing_loc_map)} tracks in collection")

    tracks = sorted([
        f for f in library.rglob("*")
        if f.suffix.lower() in EXTENSIONS and f.is_file()
    ])

    cache_by_path = {}
    for row in cache.conn.execute("SELECT path, result FROM analysis"):
        cache_by_path[row[0]] = json.loads(row[1])
    print(f"Cache paths loaded: {len(cache_by_path)}")

    updated = 0
    skipped = 0
    no_cache = 0

    for fp in tracks:
        r = cache_by_path.get(str(fp))
        if r is None:
            no_cache += 1
            continue

        loc = to_rb_location(fp)
        track_el = existing_loc_map.get(loc)
        if track_el is None:
            skipped += 1
            continue

        changed = False

        if r.get("bpm"):
            track_el.set("AverageBpm", r["bpm"])
            changed = True

        if r.get("camelot"):
            track_el.set("Tonality", r["camelot"])
            changed = True

        grouping = build_grouping(r)
        if grouping:
            track_el.set("Grouping", sanitize_xml(grouping))
            write_grouping_tag(fp, grouping)
            changed = True

        new_comment = merge_xml_comment(track_el.get("Comments", ""), r)
        if new_comment != track_el.get("Comments", ""):
            track_el.set("Comments", sanitize_xml(new_comment))
            changed = True

        if r.get("energy"):
            rating = ENERGY_TO_RATING.get(r["energy"], 0)
            if rating:
                track_el.set("Rating", str(rating))
                changed = True

        colour = pick_colour(r.get("moods", []))
        if colour:
            track_el.set("Colour", colour)
            changed = True

        if r.get("cues"):
            for old_cue in track_el.findall("POSITION_MARK"):
                track_el.remove(old_cue)
            for cue in r["cues"]:
                cue_colors = CUE_COLORS.get(cue["type"], {})
                cue_attrs = {
                    "Name": cue["name"],
                    "Type": "0",
                    "Start": str(cue["time"]),
                    "Num": "-1",
                }
                cue_attrs.update(cue_colors)
                ET.SubElement(track_el, "POSITION_MARK", **cue_attrs)
            changed = True

        if changed:
            updated += 1

    cache.close()

    if updated > 0:
        _save_xml(tree, xml_path)

    print(f"\nDone: {updated} tracks updated, {skipped} not in XML, {no_cache} not in cache")
    print(f"XML saved → {xml_path}")


if __name__ == "__main__":
    main()
