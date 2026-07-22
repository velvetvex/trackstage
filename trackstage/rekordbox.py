"""
rekordbox.py — Rekordbox database operations.

Handles: My Tags, Rating/Color sync, genre/vibe/sound/situation tag computation,
and direct SQLCipher database writes via pyrekordbox.
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

def to_rb_windows_path(path):
    """Convert WSL path to Rekordbox Windows path (C:/...)."""
    posix = path.as_posix()
    m = re.match(r"/mnt/([a-zA-Z])/(.*)", posix)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else posix


# ── Constants ────────────────────────────────────────────────────────────────

ENERGY_TO_RATING = {
    "1": 1, "2": 1,
    "3": 2, "4": 2,
    "5": 3, "6": 3,
    "7": 4, "8": 4,
    "9": 5, "10": 5,
}

# Rekordbox djmdColor IDs: 2=Red, 3=Orange, 4=Yellow, 5=Green, 8=Purple
MOOD_TO_COLOR = {
    "aggressive": "2",
    "happy":      "3",
    "party":      "4",
    "relaxed":    "5",
    "sad":        "8",
}

MOOD_PRIORITY = ["aggressive", "sad", "happy", "party", "relaxed"]

SITUATION_MAP = {
    "1": "Ambient", "2": "Ambient",
    "3": "Warmup",  "4": "Warmup",
    "5": "Groove",  "6": "Groove",
    "7": "Peak",    "8": "Peak",
    "9": "Rave",    "10": "Rave",
}

GENRE_TAG_MAP = {
    "house":             "House",
    "tech house":        "Tech House",
    "techno":            "Techno",
    "disco":             "Disco",
    "electro":           "Electro",
    "dubstep":           "Dubstep",
    "breakbeat":         "Breakbeat",
    "breaks":            "Breakbeat",
    "italo-disco":       "Italo-Disco",
    "drum n bass":       "DnB/Jungle",
    "jungle":            "DnB/Jungle",
    "dnb":               "DnB/Jungle",
    "ambient":           "Ambient",
    "rave":              "Rave",
    "pop":               "Pop",
    "leftfield":         "Leftfield",
    "trance":            "Trance",
    "progressive trance": "Trance",
    "uk garage":         "Garage",
    "garage house":      "Garage",
    "minimal":           "Minimal",
    "minimal techno":    "Minimal",
    "idm":               "IDM",
}

MY_TAG_SCHEMA = {
    "Genre": [
        "House", "Tech House", "Techno", "Disco", "Electro", "Dubstep",
        "Breakbeat", "Italo-Disco", "DnB/Jungle", "Rave", "Ambient", "Pop",
        "Leftfield", "Trance", "Garage", "Minimal", "IDM",
    ],
    "Vibe": ["Deep", "Dark", "Driving", "Euphoric", "Hypnotic", "Raw", "Melodic"],
    "Sound": ["Vocal", "Acid", "Dub", "Breaks"],
    "Situation": ["Ambient", "Warmup", "Groove", "Peak", "Rave"],
}


# ── Pure functions ───────────────────────────────────────────────────────────

def pick_color_id(moods: list) -> str:
    """Pick Rekordbox color ID from moods list using priority order."""
    for mood in MOOD_PRIORITY:
        if mood in moods:
            return MOOD_TO_COLOR[mood]
    return "0"


def compute_situation(energy: str) -> str:
    """Map energy level to situation tag name."""
    return SITUATION_MAP.get(str(energy), "")


def compute_vibe_tags(r: dict, energy: int) -> set:
    """Compute vibe tag names from analysis result.

    Rules:
    - deep in vibes -> "Deep"
    - dark in vibes -> "Dark"
    - driving in vibes AND energy >= 5 -> "Driving"
    - euphoric in vibes -> "Euphoric"
    - deep in vibes AND energy <= 3 -> "Hypnotic"
    - aggressive in moods AND energy >= 7 -> "Raw"
    - relaxed in moods AND aggressive NOT in moods AND energy <= 5 -> "Melodic"
    """
    vibes = set(r.get("vibes", []))
    moods = set(r.get("moods", []))
    tags = set()

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
    """Compute sound tags from analysis + comment genres.

    - vocal=voice -> "Vocal"
    - "acid" in genre -> "Acid"
    - "dub" in genre (not dubstep) -> "Dub"
    - "break" in genre -> "Breaks"
    """
    tags = set()

    if r.get("vocal") == "voice":
        tags.add("Vocal")

    genres = extract_genres_from_comment(comment)

    for genre in genres:
        if "acid" in genre:
            tags.add("Acid")
        if "dub" in genre and "dubstep" not in genre:
            tags.add("Dub")
        if "break" in genre:
            tags.add("Breaks")

    return tags


def compute_genre_tags(comment: str) -> set:
    """Extract genres from comment and map to tag names."""
    genres = extract_genres_from_comment(comment)
    tags = set()
    for genre in genres:
        tag_name = GENRE_TAG_MAP.get(genre)
        if tag_name:
            tags.add(tag_name)
    return tags


def extract_genres_from_comment(comment: str) -> list:
    """Extract genre list from first pipe-delimited section of comment.

    Returns lowercased genre strings.
    Skips if first section starts with Energy:, Dance:, or Cat#.
    """
    if not comment:
        return []
    first_section = comment.split(" | ")[0].strip()
    if not first_section:
        return []
    if first_section.startswith("Energy:") or first_section.startswith("Dance:") or first_section.startswith("Cat#"):
        return []
    return [g.strip().lower() for g in first_section.split(",") if g.strip()]


# ── RekordboxDB class ────────────────────────────────────────────────────────

class RekordboxDB:
    """Wraps pyrekordbox SQLCipher database for tag/rating/color operations."""

    def __init__(self, db_path: str):
        """Open Rekordbox SQLCipher database via pyrekordbox."""
        from pyrekordbox import Rekordbox6Database
        self.db = Rekordbox6Database(path=db_path)

    def get_content_map(self, conn) -> dict:
        """Return {FolderPath: {id, rating, color}} for all tracks."""
        rows = conn.execute(text(
            "SELECT ID, FolderPath, Rating, ColorID FROM djmdContent"
        )).fetchall()
        content_map = {}
        for cid, folder, rating, color in rows:
            content_map[folder] = {"id": cid, "rating": rating, "color": color}
        return content_map

    def get_existing_tag_assignments(self, conn) -> set:
        """Return set of (ContentID, MyTagID) for non-deleted assignments."""
        rows = conn.execute(text(
            "SELECT ContentID, MyTagID FROM djmdSongMyTag WHERE rb_local_deleted=0"
        )).fetchall()
        return {(content_id, tag_id) for content_id, tag_id in rows}

    def get_max_usn(self, conn) -> int:
        """Get max rb_local_usn from djmdSongMyTag."""
        result = conn.execute(text(
            "SELECT MAX(rb_local_usn) FROM djmdSongMyTag"
        )).fetchone()[0]
        return result or 0

    def wipe_my_tags(self, conn):
        """Delete ALL tag definitions and assignments."""
        conn.execute(text("DELETE FROM djmdSongMyTag"))
        conn.execute(text("DELETE FROM djmdMyTag"))

    def create_tag_schema(self, conn, schema: dict) -> dict:
        """Create category+tag hierarchy from schema dict.

        Returns {tag_name: tag_id} mapping.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")
        tag_map = {}
        usn = 0

        for category, tag_names in schema.items():
            # Create category (parent) row
            cat_id = str(uuid.uuid4().int >> 96)  # compact numeric ID
            usn += 1
            conn.execute(text("""
                INSERT INTO djmdMyTag
                (ID, Seq, Name, Attribute, ParentID, UUID,
                 rb_data_status, rb_local_data_status,
                 rb_local_deleted, rb_local_synced,
                 rb_local_usn, created_at, updated_at)
                VALUES (:id, :seq, :name, 1, NULL, :uuid,
                        0, 0, 0, 0, :usn, :now, :now)
            """), {
                "id": cat_id, "seq": usn, "name": category,
                "uuid": str(uuid.uuid4()), "usn": usn, "now": now,
            })

            # Create child tags under this category
            for i, tag_name in enumerate(tag_names):
                tag_id = str(uuid.uuid4().int >> 96)
                usn += 1
                conn.execute(text("""
                    INSERT INTO djmdMyTag
                    (ID, Seq, Name, Attribute, ParentID, UUID,
                     rb_data_status, rb_local_data_status,
                     rb_local_deleted, rb_local_synced,
                     rb_local_usn, created_at, updated_at)
                    VALUES (:id, :seq, :name, 0, :parent, :uuid,
                            0, 0, 0, 0, :usn, :now, :now)
                """), {
                    "id": tag_id, "seq": i + 1, "name": tag_name,
                    "parent": cat_id, "uuid": str(uuid.uuid4()),
                    "usn": usn, "now": now,
                })
                tag_map[tag_name] = tag_id

        return tag_map

    def assign_tag(self, conn, content_id, tag_id, existing: set, usn: int) -> int:
        """Add tag assignment if not exists. Returns new USN.

        If (content_id, tag_id) already in existing set, returns usn unchanged.
        """
        if (content_id, tag_id) in existing:
            return usn

        usn += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")
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
            "content": content_id,
            "uuid": str(uuid.uuid4()),
            "usn": usn,
            "now": now,
        })
        existing.add((content_id, tag_id))
        return usn
