"""add.py — `trackstage add "<query>"` engine.

Source (Soulseek) → identify (Discogs) → analyze (Essentia) → organize →
write directly to Rekordbox master.db. No XML, no manual import.
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from .sourcer import SlskdClient, rank_candidates
from .identifier import identify
from .analyzer import analyze_track
from .cache import AnalysisCache
from .pipeline import DiscogsClient, build_dest
from .tags import read_tags, write_tags
from .rekordbox import to_rb_windows_path
from .dbwriter import (
    RekordboxWriter, rekordbox_running, backup_db, restore_db,
)

try:
    from pyrekordbox import Rekordbox6Database
except ImportError:
    Rekordbox6Database = None

DEFAULT_DB = "/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db"


def parse_query(query: str):
    """Split a user query into (artist, title, search_str).

    "<title> by <artist>" → artist/title split on the last ' by ' (case-
    insensitive). The Soulseek search string drops the ' by ' filler and leads
    with the artist ("<artist> <title>"), which is what peers name folders by —
    the raw 'by' form returns almost nothing. No ' by ' → title is the whole
    query, artist unknown, search string unchanged.
    """
    parts = re.split(r"\s+by\s+", query.strip(), flags=re.IGNORECASE)
    if len(parts) >= 2:
        title = " by ".join(parts[:-1]).strip()
        artist = parts[-1].strip()
        search_str = f"{artist} {title}".strip()
    else:
        title, artist, search_str = query.strip(), "", query.strip()
    return artist, title, search_str


def _emit(payload: dict, as_json: bool):
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        for k, v in payload.items():
            print(f"  {k}: {v}")


def run_add(args) -> int:
    # 1. Source
    q_artist, q_title, search_str = parse_query(args.query)
    client = SlskdClient()
    files = client.search(search_str)
    candidates = rank_candidates(files, fmt=args.format)
    if not candidates:
        _emit({"status": "no_source",
               "message": f"No {args.format} source >=320kbps for: {args.query}"},
              args.json)
        return 1

    # Disambiguation: multiple distinct top candidates and not --yes/--pick
    if args.pick is not None:
        chosen = candidates[args.pick - 1]
    elif args.yes or len(candidates) == 1:
        chosen = candidates[0]
    else:
        shortlist = [{"n": i + 1, "user": c.username, "file": c.filename,
                      "ext": c.extension, "bitrate": c.bitrate,
                      "size_mb": round(c.size / 1_048_576, 1)}
                     for i, c in enumerate(candidates[:5])]
        _emit({"status": "choose", "candidates": shortlist,
               "message": "Re-run with --pick N"}, args.json)
        return 2

    if args.dry_run:
        _emit({"status": "dry_run", "would_download": chosen.filename,
               "from": chosen.username}, args.json)
        return 0

    # 2. Download
    src = client.download(chosen)

    # 3. Identify
    tags = read_tags(src)
    artist = tags["artist"] or q_artist or q_title
    title = tags["title"] or q_title
    dclient = DiscogsClient(os.environ.get("DISCOGS_TOKEN", ""))
    meta, conf = identify(dclient, artist, title, discogs_id=args.discogs_id)
    if meta is None:
        meta = {"release_title": title, "album": title, "genre": "",
                "styles": "", "label": "", "catno": "", "year": "",
                "discogs_id": ""}

    # 4. Analyze (cached)
    cache = AnalysisCache()
    analysis = cache.get(src) or analyze_track(src, existing_key=meta.get("initial_key", ""))
    cache.put(src, analysis)
    cache.close()

    # Merge analysis into meta for tag/file writes
    meta["bpm"] = analysis.get("bpm", "")
    meta["initial_key"] = analysis.get("camelot", "")
    meta["energy"] = analysis.get("energy", "")
    meta["danceability"] = analysis.get("danceability", "")
    meta["vibes"] = ", ".join(analysis.get("vibes", []))
    meta["vocal"] = analysis.get("vocal", "")

    # 5. Organize (move to Library, write file tags)
    library = Path(os.environ["LIBRARY_PATH"])
    dest_dir, dest_path = build_dest(artist, title, meta, library, src.suffix)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest_path))
    write_tags(dest_path, meta, dry_run=False)

    # 6. Write to DB
    if rekordbox_running():
        _emit({"status": "refused",
               "message": "Rekordbox is running. Close it and re-run."},
              args.json)
        return 3

    db_path = Path(os.environ.get("REKORDBOX_DB", DEFAULT_DB))
    backup = backup_db(db_path)
    win_path = to_rb_windows_path(dest_path)
    db = Rekordbox6Database(path=str(db_path))
    try:
        writer = RekordboxWriter(db)
        content_id = writer.add_track(
            wsl_path=str(dest_path), win_path=win_path,
            filename=dest_path.name, title=title, artist=artist,
            meta=meta, analysis=analysis)
        db.commit()
    except Exception as e:
        restore_db(backup, db_path)
        _emit({"status": "error", "message": f"DB write failed: {e}. "
               f"master.db restored from backup."}, args.json)
        return 1

    _emit({"status": "ok", "content_id": content_id,
           "title": title, "artist": artist,
           "bpm": analysis.get("bpm"), "key": analysis.get("camelot"),
           "energy": analysis.get("energy"),
           "dest": str(dest_path)}, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trackstage add",
        description="Add one track: Soulseek → Discogs → Essentia → Rekordbox DB",
    )
    p.add_argument("query", help="Search query, e.g. 'E Talking by Soulwax'")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview: no download, no DB commit")
    p.add_argument("--format", choices=["flac", "any"], default="flac",
                   help="flac = lossless only w/ MP3>=320 fallback; any = best available")
    p.add_argument("--pick", type=int, default=None,
                   help="Choose candidate N from a prior ranked shortlist")
    p.add_argument("--discogs-id", type=int, default=None,
                   help="Force this Discogs release ID")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Auto-accept the top candidate without prompting")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON (for the skill)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run_add(args)


if __name__ == "__main__":
    sys.exit(main())
