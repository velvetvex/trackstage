#!/usr/bin/env python3
"""Backfill Rekordbox XML from analysis cache — no re-analysis needed."""

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from dotenv import load_dotenv

from trackstage.cache import AnalysisCache
from trackstage.pipeline import (
    to_rb_location, sanitize_xml, load_or_bootstrap_xml, _save_xml,
)
from trackstage.cue_detection import CUE_COLORS

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}


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

    # Build path→result map ignoring mtime (tags write changed mtime after caching)
    cache_by_path = {}
    for row in cache.conn.execute("SELECT path, result FROM analysis"):
        cache_by_path[row[0]] = __import__("json").loads(row[1])
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

        if r.get("bpm") and track_el.get("AverageBpm") in ("0.00", "", None):
            track_el.set("AverageBpm", r["bpm"])
            changed = True

        if r.get("camelot") and not track_el.get("Tonality"):
            track_el.set("Tonality", r["camelot"])
            changed = True

        if r.get("vibes") and not track_el.get("Grouping"):
            track_el.set("Grouping", sanitize_xml(", ".join(r["vibes"])))
            changed = True

        if r.get("cues") and not track_el.findall("POSITION_MARK"):
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
