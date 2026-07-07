#!/usr/bin/env python3
"""Sync analysis data into Rekordbox database — My Tags, Rating, Color.

Requires Rekordbox to be CLOSED. Modifies the encrypted master.db directly
via pyrekordbox/sqlcipher.

Usage:
    ./scripts/sync_rekordbox.py              # sync all cached tracks
    ./scripts/sync_rekordbox.py --dry-run    # preview without writing
"""

import json
import os
import sys
import uuid
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from dotenv import load_dotenv
from pyrekordbox import Rekordbox6Database
from sqlalchemy import text

from trackstage.cache import AnalysisCache

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EXTENSIONS = {'.flac', '.mp3', '.aiff', '.aif', '.m4a'}

# Energy (1-10) → Rekordbox Rating (0-5 stars)
ENERGY_TO_RATING = {
    "1": 1, "2": 1,
    "3": 2, "4": 2,
    "5": 3, "6": 3,
    "7": 4, "8": 4,
    "9": 5, "10": 5,
}

# Mood → Rekordbox Color ID (from djmdColor table)
MOOD_TO_COLOR = {
    "aggressive": "2",  # Red
    "happy":      "3",  # Orange
    "party":      "4",  # Yellow
    "relaxed":    "5",  # Green
    "sad":        "8",  # Purple
}

MOOD_PRIORITY = ["aggressive", "sad", "happy", "party", "relaxed"]

# Map analysis vibes/moods → user's existing My Tag IDs
QUALITY_TAG_MAP = {
    "deep":       "431374945",
    "driving":    None,  # no matching tag yet
    "dark":       None,
    "euphoric":   None,
    "melancholic": None,
}

COMPONENT_TAG_MAP = {
    "voice":        "3901231902",  # Vocal
    "instrumental": None,
}

# Map genres from comments to user's Genre tags
GENRE_TAG_MAP = {
    "house":       "3810321585",
    "tech house":  "141506072",
    "techno":      "3344393550",
    "disco":       "2373167528",
    "electro":     "3463648609",
    "dubstep":     "2167917313",
    "breakbeat":   "545299999",
    "italo-disco": "4170439872",
    "drum n bass": "193884088",
    "jungle":      "193884088",
    "dnb":         "193884088",
    "ambient":     "3587654460",
    "rave":        "128821137",
    "pop":         "1826393795",
}

# Situation mapping based on energy level
ENERGY_TO_SITUATION = {
    "1": "1967791472",   # Lounge
    "2": "1967791472",   # Lounge
    "3": "1985900357",   # Warmup
    "4": "1985900357",   # Warmup
    "5": "1985900357",   # Warmup
    "6": "3997244479",   # Peaktime
    "7": "3997244479",   # Peaktime
    "8": "3997244479",   # Peaktime
    "9": "3997244479",   # Peaktime
    "10": "3997244479",  # Peaktime
}


def path_to_rb(fp: Path) -> str:
    """Convert WSL path to Rekordbox Windows path."""
    posix = fp.as_posix()
    import re
    m = re.match(r"/mnt/([a-zA-Z])/(.*)", posix)
    if m:
        return f"{m.group(1).upper()}:/{m.group(2)}"
    return posix


def pick_color_id(moods: list) -> str:
    for mood in MOOD_PRIORITY:
        if mood in moods:
            return MOOD_TO_COLOR[mood]
    return "0"


def extract_genres(comments: str) -> list:
    """Extract genre names from the first pipe-delimited section of comments."""
    if not comments:
        return []
    first_section = comments.split(" | ")[0]
    if first_section.startswith("Energy:") or first_section.startswith("Cat#"):
        return []
    return [g.strip().lower() for g in first_section.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Sync analysis to Rekordbox DB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    library = Path(os.environ.get("LIBRARY_PATH", ""))
    if not library.is_dir():
        print(f"ERROR: LIBRARY_PATH not found: {library}")
        sys.exit(1)

    default_db = "/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db"
    db_path = Path(os.environ.get("REKORDBOX_DB", default_db))
    if not db_path.exists():
        print(f"ERROR: Rekordbox master.db not found at {db_path}")
        sys.exit(1)

    print("Connecting to Rekordbox database...")
    db = Rekordbox6Database(path=str(db_path))

    cache = AnalysisCache()
    cache_by_path = {}
    for row in cache.conn.execute("SELECT path, result FROM analysis"):
        cache_by_path[row[0]] = json.loads(row[1])
    print(f"Cache: {len(cache_by_path)} entries")

    with db.engine.connect() as conn:
        # Build content path → ID map
        rows = conn.execute(text(
            "SELECT ID, FolderPath, Rating, ColorID FROM djmdContent"
        )).fetchall()
        content_map = {}
        for cid, folder, rating, color in rows:
            content_map[folder] = {"id": cid, "rating": rating, "color": color}
        print(f"Rekordbox: {len(content_map)} tracks in database")

        # Get existing tag assignments
        existing_tags = set()
        tag_rows = conn.execute(text(
            "SELECT ContentID, MyTagID FROM djmdSongMyTag WHERE rb_local_deleted=0"
        )).fetchall()
        for content_id, tag_id in tag_rows:
            existing_tags.add((content_id, tag_id))
        print(f"Existing tag assignments: {len(existing_tags)}")

        # Get max USN for sequencing
        max_usn = conn.execute(text(
            "SELECT MAX(rb_local_usn) FROM djmdSongMyTag"
        )).fetchone()[0] or 0

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")
        updated_ratings = 0
        updated_colors = 0
        new_tags = 0
        matched = 0

        tracks = sorted([
            f for f in library.rglob("*")
            if f.suffix.lower() in EXTENSIONS and f.is_file()
        ])

        for fp in tracks:
            r = cache_by_path.get(str(fp))
            if r is None:
                continue

            rb_path = path_to_rb(fp)
            content = content_map.get(rb_path)
            if content is None:
                continue

            matched += 1
            cid = content["id"]

            # Rating from energy
            if r.get("energy"):
                rating = ENERGY_TO_RATING.get(r["energy"], 0)
                if rating and content["rating"] != rating:
                    if not args.dry_run:
                        conn.execute(text(
                            "UPDATE djmdContent SET Rating=:rating, updated_at=:now WHERE ID=:id"
                        ), {"rating": rating, "now": now, "id": cid})
                    updated_ratings += 1

            # Color from mood
            color_id = pick_color_id(r.get("moods", []))
            if color_id != "0" and content["color"] != color_id:
                if not args.dry_run:
                    conn.execute(text(
                        "UPDATE djmdContent SET ColorID=:color, updated_at=:now WHERE ID=:id"
                    ), {"color": color_id, "now": now, "id": cid})
                updated_colors += 1

            # My Tags
            tags_to_add = set()

            # Quality tags from vibes
            for vibe in r.get("vibes", []):
                tag_id = QUALITY_TAG_MAP.get(vibe)
                if tag_id:
                    tags_to_add.add(tag_id)

            # Component: Vocal tag
            if r.get("vocal") == "voice":
                tags_to_add.add(COMPONENT_TAG_MAP["voice"])

            # Genre tags from comments
            comments = r.get("comments", "")
            if not comments:
                # Try reading from file tag via cache — comments might be in a different field
                pass
            genres = extract_genres(comments)
            for genre in genres:
                tag_id = GENRE_TAG_MAP.get(genre)
                if tag_id:
                    tags_to_add.add(tag_id)

            # Situation from energy
            if r.get("energy"):
                sit_id = ENERGY_TO_SITUATION.get(r["energy"])
                if sit_id:
                    tags_to_add.add(sit_id)

            tags_to_add.discard(None)

            for tag_id in tags_to_add:
                if (cid, tag_id) not in existing_tags:
                    max_usn += 1
                    if not args.dry_run:
                        conn.execute(text("""
                            INSERT INTO djmdSongMyTag
                            (ID, MyTagID, ContentID, UUID,
                             rb_data_status, rb_local_data_status,
                             rb_local_deleted, rb_local_synced,
                             rb_local_usn, created_at, updated_at)
                            VALUES (:id, :tag, :content, :uuid,
                                    0, 0, 0, 0, :usn, :now, :now)
                        """), {
                            "id": str(uuid.uuid4()),
                            "tag": tag_id,
                            "content": cid,
                            "uuid": str(uuid.uuid4()),
                            "usn": max_usn,
                            "now": now,
                        })
                    existing_tags.add((cid, tag_id))
                    new_tags += 1

        if not args.dry_run:
            conn.commit()

    cache.close()

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{prefix}Matched: {matched} tracks")
    print(f"{prefix}Ratings updated: {updated_ratings}")
    print(f"{prefix}Colors updated: {updated_colors}")
    print(f"{prefix}My Tags added: {new_tags}")


if __name__ == "__main__":
    main()
