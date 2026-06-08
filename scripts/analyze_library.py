#!/usr/bin/env python3
"""
Analyze full library — run audio analysis and cache results.

Does NOT write file tags or update XML (use sync.py for that).

Usage:
    ./scripts/analyze_library.py              # analyze all unanalyzed tracks
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
from trackstage.analyzer import analyze_track
from trackstage.cache import AnalysisCache
from trackstage.tags import EXTENSIONS


def main():
    parser = argparse.ArgumentParser(description="Analyze full DJ library")
    parser.add_argument("--force", action="store_true", help="Re-analyze already cached tracks")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without caching results")
    parser.add_argument("--library", type=Path, default=None,
                        help="Library path (or set LIBRARY_PATH in .env)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    library = args.library or Path(os.environ.get("LIBRARY_PATH", ""))
    if not library or not library.exists():
        print(f"ERROR: Library not found. Pass --library or set LIBRARY_PATH in .env.")
        sys.exit(1)

    tracks = sorted([
        f for f in library.rglob("*")
        if f.suffix.lower() in EXTENSIONS and f.is_file()
    ])
    print(f"Found {len(tracks)} tracks in library.")

    cache = AnalysisCache()
    print(f"Cache: {cache.count()} previous results stored.")

    if not args.force:
        before = len(tracks)
        tracks = [t for t in tracks if cache.get(t) is None]
        skipped = before - len(tracks)
        if skipped:
            print(f"Skipping {skipped} already-analyzed tracks. Use --force to redo.")

    if not tracks:
        print("Nothing to analyze.")
        cache.close()
        return

    print(f"Analyzing {len(tracks)} tracks...")
    print("=" * 70)

    start = time.time()
    success = 0
    errors = 0

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

    cache.close()

    total_time = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"Done in {total_time/60:.1f} min ({total_time/len(tracks):.1f}s/track avg)")
    print(f"  Success: {success}")
    print(f"  Errors:  {errors}")
    print(f"  Cached:  {success if not args.dry_run else 0}")


if __name__ == "__main__":
    main()
