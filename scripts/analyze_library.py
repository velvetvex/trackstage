#!/home/kaitlyn/dev/trackstage/.venv/bin/python3
"""
Analyze full library — run all audio analysis + write tags.

Usage:
    ./scripts/analyze_library.py              # analyze all untagged tracks
    ./scripts/analyze_library.py --force      # re-analyze everything
    ./scripts/analyze_library.py --jobs 4     # parallel (4 workers)
    ./scripts/analyze_library.py --dry-run    # measure without writing tags
"""

import sys
import os
import time
import argparse

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3

from trackstage.analyzer import analyze_track
from trackstage.loudness import write_replaygain_tags

LIBRARY = Path("/mnt/c/Users/Kaitlyn/Music/Library")
EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}


def has_replaygain(fp: Path) -> bool:
    """Check if track already has ReplayGain tags (skip indicator)."""
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


def main():
    parser = argparse.ArgumentParser(description="Analyze full DJ library")
    parser.add_argument("--force", action="store_true", help="Re-analyze already tagged tracks")
    parser.add_argument("--jobs", type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without writing tags")
    args = parser.parse_args()

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

    print(f"Analyzing {len(tracks)} tracks (jobs={args.jobs})...")
    print("=" * 70)

    start = time.time()
    success = 0
    errors = 0

    for i, fp in enumerate(tracks):
        t0 = time.time()
        try:
            r = analyze_track(fp)

            if not args.dry_run and r.get("loudness") and r["loudness"].get("gain_db") is not None:
                write_replaygain_tags(fp, r["loudness"])

            elapsed = time.time() - t0
            success += 1

            # Progress every 10 tracks
            if i % 10 == 0 or i < 3:
                avg = (time.time() - start) / (i + 1)
                eta = avg * (len(tracks) - i - 1) / 60
                print(
                    f"  [{i+1:>4}/{len(tracks)}] {elapsed:.1f}s "
                    f"| BPM={r.get('bpm') or '—':>5} Key={r.get('camelot') or '—':>3} "
                    f"E={r.get('energy') or '—'} D={r.get('danceability') or '—'} "
                    f"Cues={len(r.get('cues', []))} "
                    f"| {fp.parent.name}/{fp.name}"
                )
                if i > 0:
                    print(f"         ETA: {eta:.0f} min remaining")

        except Exception as e:
            errors += 1
            elapsed = time.time() - t0
            print(f"  [{i+1:>4}/{len(tracks)}] ERROR ({elapsed:.1f}s): {fp.name}: {e}")

    total_time = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"Done in {total_time/60:.1f} min ({total_time/len(tracks):.1f}s/track avg)")
    print(f"  Success: {success}")
    print(f"  Errors:  {errors}")


if __name__ == "__main__":
    main()
