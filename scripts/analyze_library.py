#!/home/kaitlyn/dev/trackstage/.venv/bin/python3
"""
Analyze full library — run audio analysis, write tags, update Rekordbox XML.

Writes BPM, key, energy, danceability, vibes to file tags and updates
cue points in the Rekordbox XML. Preserves existing Discogs metadata.

Usage:
    ./scripts/analyze_library.py              # analyze all untagged tracks
    ./scripts/analyze_library.py --force      # re-analyze everything
    ./scripts/analyze_library.py --dry-run    # analyze without writing anything
"""

import re
import sys
import os
import time
import argparse
import xml.etree.ElementTree as ET

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.aiff import AIFF
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, TKEY, TBPM, COMM

from trackstage.analyzer import analyze_track
from trackstage.loudness import write_replaygain_tags
from trackstage.pipeline import (
    to_rb_location, sanitize_xml,
    load_or_bootstrap_xml, _save_xml,
)
from trackstage.cue_detection import CUE_COLORS

LIBRARY = Path("/mnt/c/Users/Kaitlyn/Music/Library")
EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}


def has_replaygain(fp: Path) -> bool:
    try:
        ext = fp.suffix.lower()
        if ext == '.flac':
            f = FLAC(fp)
            return 'replaygain_track_gain' in f
        elif ext == '.mp3':
            f = MP3(fp, ID3=ID3)
            if f.tags:
                return any('REPLAYGAIN' in k for k in f.tags.keys())
        return False
    except Exception:
        return False


def _merge_comment(existing: str, energy: str, dance: str, vibes: str, vocal: str) -> str:
    """Merge analysis fields into existing comment without clobbering Discogs data."""
    # Strip old analysis fields from existing comment
    parts = [p.strip() for p in existing.split(" | ")]
    cleaned = []
    for p in parts:
        if p.startswith("Energy:") or p.startswith("Dance:"):
            continue
        if p in ("instrumental", "voice", ""):
            continue
        # Skip if all comma-separated parts are known vibes (handles "dark, driving")
        vibe_words = {"dark", "euphoric", "deep", "melancholic", "driving"}
        sub_parts = [s.strip().lower() for s in p.split(",")]
        if all(s in vibe_words for s in sub_parts if s):
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


def write_analysis_tags(fp: Path, r: dict) -> bool:
    """Write only analysis fields to file tags, preserving existing metadata."""
    ext = fp.suffix.lower()
    try:
        if ext == '.flac':
            a = FLAC(fp)
            if r.get("bpm"):       a["bpm"] = [r["bpm"]]
            if r.get("camelot"):   a["initialkey"] = [r["camelot"]]
            if r.get("energy"):    a["energy"] = [r["energy"]]
            if r.get("danceability"): a["danceability"] = [r["danceability"]]
            existing_comment = a.get("comment", [""])[0]
            new_comment = _merge_comment(
                existing_comment, r.get("energy", ""), r.get("danceability", ""),
                ", ".join(r.get("vibes", [])), r.get("vocal", ""),
            )
            if new_comment:
                a["comment"] = [new_comment]
            a.save()

        elif ext == '.mp3':
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
                tags["TXXX:DANCEABILITY"] = TXXX(encoding=3, desc="DANCEABILITY", text=r["danceability"])
            existing_comment = ""
            if "COMM::eng" in tags:
                existing_comment = str(tags["COMM::eng"])
            new_comment = _merge_comment(
                existing_comment, r.get("energy", ""), r.get("danceability", ""),
                ", ".join(r.get("vibes", [])), r.get("vocal", ""),
            )
            if new_comment:
                tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=new_comment)
            tags.save(fp, v2_version=3)

        elif ext in ('.aiff', '.aif'):
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
                a.tags["TXXX:DANCEABILITY"] = TXXX(encoding=3, desc="DANCEABILITY", text=r["danceability"])
            existing_comment = ""
            if "COMM::eng" in a.tags:
                existing_comment = str(a.tags["COMM::eng"])
            new_comment = _merge_comment(
                existing_comment, r.get("energy", ""), r.get("danceability", ""),
                ", ".join(r.get("vibes", [])), r.get("vocal", ""),
            )
            if new_comment:
                a.tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=new_comment)
            a.save()

        elif ext == '.m4a':
            a = MP4(fp)
            if r.get("bpm"):
                a["tmpo"] = [int(float(r["bpm"]))]
            if r.get("camelot"):
                a["----:com.apple.iTunes:INITIALKEY"] = [r["camelot"].encode("utf-8")]
            if r.get("energy"):
                a["----:com.apple.iTunes:ENERGY"] = [r["energy"].encode("utf-8")]
            if r.get("danceability"):
                a["----:com.apple.iTunes:DANCEABILITY"] = [r["danceability"].encode("utf-8")]
            existing_comment = a.get("\xa9cmt", [""])[0] if a.get("\xa9cmt") else ""
            new_comment = _merge_comment(
                existing_comment, r.get("energy", ""), r.get("danceability", ""),
                ", ".join(r.get("vibes", [])), r.get("vocal", ""),
            )
            if new_comment:
                a["\xa9cmt"] = [new_comment]
            a.save()

        return True
    except Exception as e:
        print(f"  TAG ERROR: {fp.name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Analyze full DJ library")
    parser.add_argument("--force", action="store_true", help="Re-analyze already tagged tracks")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without writing tags")
    args = parser.parse_args()

    xml_path = Path(os.environ.get("XML_PATH", ""))
    if not xml_path.name:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        xml_path = Path(os.environ.get("XML_PATH", ""))

    tracks = sorted([
        f for f in LIBRARY.rglob("*")
        if f.suffix.lower() in EXTENSIONS and f.is_file()
    ])
    print(f"Found {len(tracks)} tracks in library.")

    if not args.force:
        before = len(tracks)
        tracks = [t for t in tracks if not has_replaygain(t)]
        skipped = before - len(tracks)
        if skipped:
            print(f"Skipping {skipped} already-analyzed tracks. Use --force to redo.")

    if not tracks:
        print("Nothing to analyze.")
        return

    print(f"Analyzing {len(tracks)} tracks...")
    print("=" * 70)

    # Load XML once
    tree, root, max_id = load_or_bootstrap_xml(xml_path)
    collection = root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(root, "COLLECTION", Entries="0")

    existing_loc_map = {}
    for track_el in collection.findall("TRACK"):
        existing_loc_map[track_el.get("Location", "")] = track_el

    start = time.time()
    success = 0
    errors = 0
    xml_updated = 0

    for i, fp in enumerate(tracks):
        t0 = time.time()
        try:
            r = analyze_track(fp)

            if not args.dry_run:
                # Write analysis tags to file (preserves existing Discogs metadata)
                write_analysis_tags(fp, r)

                # Write ReplayGain tags
                if r.get("loudness") and r["loudness"].get("gain_db") is not None:
                    write_replaygain_tags(fp, r["loudness"])

                # Update Rekordbox XML entry
                loc = to_rb_location(fp)
                track_el = existing_loc_map.get(loc)
                if track_el is not None:
                    if r.get("bpm"):
                        track_el.set("AverageBpm", r["bpm"])
                    if r.get("camelot"):
                        track_el.set("Tonality", r["camelot"])
                    if r.get("vibes"):
                        track_el.set("Grouping", sanitize_xml(", ".join(r["vibes"])))

                    # Replace cue points
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
                    xml_updated += 1

            elapsed = time.time() - t0
            success += 1

            if i % 10 == 0 or i < 3:
                avg = (time.time() - start) / (i + 1)
                eta = avg * (len(tracks) - i - 1) / 60
                vibes = ",".join(r.get("vibes", [])) or "—"
                print(
                    f"  [{i+1:>4}/{len(tracks)}] {elapsed:.1f}s "
                    f"| BPM={r.get('bpm') or '—':>5} Key={r.get('camelot') or '—':>3} "
                    f"E={r.get('energy') or '—'} D={r.get('danceability') or '—'} "
                    f"Cues={len(r.get('cues', []))} Vibes={vibes} "
                    f"| {fp.parent.name}/{fp.name}"
                )
                if i > 0:
                    print(f"         ETA: {eta:.0f} min remaining")

        except Exception as e:
            errors += 1
            elapsed = time.time() - t0
            print(f"  [{i+1:>4}/{len(tracks)}] ERROR ({elapsed:.1f}s): {fp.name}: {e}")

    # Save XML once at the end
    if not args.dry_run and xml_updated > 0:
        _save_xml(tree, xml_path)
        print(f"\nXML updated: {xml_updated} tracks → {xml_path}")

    total_time = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"Done in {total_time/60:.1f} min ({total_time/len(tracks):.1f}s/track avg)")
    print(f"  Success: {success}")
    print(f"  Errors:  {errors}")
    print(f"  Tags written: {success if not args.dry_run else 0}")
    print(f"  XML cues updated: {xml_updated}")


if __name__ == "__main__":
    main()
