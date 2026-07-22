"""dbwriter.py — Write a fully-analyzed track directly into Rekordbox master.db.

Replaces xml.py + sync_rekordbox.py. One pyrekordbox transaction:
Content row (+ resolved Artist/Album/Genre/Label/Key, BPM, Rating, Color,
Comment) + My Tag assignments + Styles/Labels playlist membership.
"""

import logging

from .rekordbox import (
    ENERGY_TO_RATING, pick_color_id, to_rb_windows_path,
    compute_genre_tags, compute_vibe_tags, compute_sound_tags,
    compute_situation,
)
from .tags import build_comment

log = logging.getLogger(__name__)


# ── Pure field mapping ───────────────────────────────────────────────────────

def content_fields(meta: dict, analysis: dict) -> dict:
    """Scalar DjmdContent fields derived from Discogs meta + Essentia analysis."""
    bpm = None
    if analysis.get("bpm"):
        bpm = int(round(float(analysis["bpm"]) * 100))

    rating = None
    if analysis.get("energy"):
        rating = ENERGY_TO_RATING.get(str(analysis["energy"]))

    color = pick_color_id(analysis.get("moods", []))

    year = None
    if meta.get("year"):
        try:
            year = int(str(meta["year"])[:4])
        except ValueError:
            year = None

    comment_meta = dict(meta)
    comment_meta["energy"] = analysis.get("energy", "")
    comment_meta["danceability"] = analysis.get("danceability", "")
    comment_meta["vibes"] = ", ".join(analysis.get("vibes", []))
    comment_meta["vocal"] = analysis.get("vocal", "")

    return {
        "BPM": bpm,
        "Rating": rating,
        "ColorID": color,
        "ReleaseYear": year,
        "Commnt": build_comment(comment_meta),
        "KeyName": analysis.get("camelot", ""),
    }


def computed_tag_names(meta: dict, analysis: dict) -> set[str]:
    """Union of genre / vibe / sound / situation My Tag names for a track."""
    comment = content_fields(meta, analysis)["Commnt"]
    energy = int(analysis["energy"]) if analysis.get("energy") else 5

    names: set[str] = set()
    names |= compute_genre_tags(comment)
    names |= compute_vibe_tags(analysis, energy)
    names |= compute_sound_tags(analysis, comment)
    sit = compute_situation(analysis.get("energy", ""))
    if sit:
        names.add(sit)
    return names


def resolve_tag_ids(existing_by_name: dict, names: set) -> list:
    """Map tag names → existing djmdMyTag IDs (case-insensitive). Skip unmatched."""
    lower = {k.lower(): v for k, v in existing_by_name.items()}
    ids = [lower[n.lower()] for n in names if n.lower() in lower]
    return sorted(ids)


# ── DB write shell ───────────────────────────────────────────────────────────

import uuid
from datetime import datetime, timezone

from sqlalchemy import text


def _now_rb() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")


class RekordboxWriter:
    """Wraps a pyrekordbox Rekordbox6Database handle for track writes."""

    def __init__(self, db):
        self.db = db

    # -- lookups ---------------------------------------------------------------

    def existing_tag_map(self) -> dict:
        """{tag_name: MyTagID} for child My Tags (Attribute=0)."""
        rows = self.db.session.execute(text(
            "SELECT Name, ID FROM djmdMyTag WHERE Attribute=0 "
            "AND rb_local_deleted=0"))
        return {name: tid for name, tid in rows}

    def _existing_content_id(self, win_path: str):
        rows = list(self.db.session.execute(text(
            "SELECT ID FROM djmdContent WHERE FolderPath=:p"),
            {"p": win_path}))
        return rows[0][0] if rows else None

    def resolve_key_id(self, camelot: str):
        if not camelot:
            return None
        rows = self.db.session.execute(text("SELECT ID, Name FROM djmdKey"))
        for kid, name in rows:
            if str(name).strip().lower() == camelot.strip().lower():
                return kid
        return None

    # -- resolve-or-create related rows ---------------------------------------

    def resolve_or_create(self, kind: str, name: str):
        if not name:
            return None
        getter = {"artist": self.db.get_artist, "genre": self.db.get_genre,
                  "label": self.db.get_label}[kind]
        adder = {"artist": self.db.add_artist, "genre": self.db.add_genre,
                 "label": self.db.add_label}[kind]
        existing = getter(Name=name).first()
        return existing if existing is not None else adder(name)

    def resolve_album(self, name: str, artist_row):
        if not name:
            return None
        existing = self.db.get_album(Name=name).first()
        if existing is not None:
            return existing
        return self.db.add_album(name, artist=artist_row)

    # -- playlists -------------------------------------------------------------

    def ensure_playlist(self, folder_name: str, child_name: str):
        folder = self.db.get_playlist(Name=folder_name).first()
        if folder is None:
            folder = self.db.create_playlist_folder(folder_name)
        child = self.db.get_playlist(Name=child_name).first()
        if child is None:
            child = self.db.create_playlist(child_name, parent=folder)
        return child

    def _already_in_playlist(self, playlist, content_id) -> bool:
        for song in self.db.get_playlist_songs(playlist):
            if str(getattr(song, "ContentID", "")) == str(content_id):
                return True
        return False

    # -- My Tags ---------------------------------------------------------------

    def assign_my_tags(self, content_id, tag_ids) -> int:
        existing = {(c, t) for c, t in self.db.session.execute(text(
            "SELECT ContentID, MyTagID FROM djmdSongMyTag "
            "WHERE rb_local_deleted=0"))}
        max_usn = list(self.db.session.execute(text(
            "SELECT MAX(rb_local_usn) FROM djmdSongMyTag")))
        usn = (max_usn[0][0] if max_usn and max_usn[0][0] else 0)
        added = 0
        for tid in tag_ids:
            if (content_id, tid) in existing:
                continue
            usn += 1
            self.db.session.execute(text("""
                INSERT INTO djmdSongMyTag
                (ID, MyTagID, ContentID, UUID,
                 rb_data_status, rb_local_data_status,
                 rb_local_deleted, rb_local_synced,
                 rb_local_usn, created_at, updated_at)
                VALUES (:id, :tag, :content, :uuid,
                        0, 0, 0, 0, :usn, :now, :now)
            """), {"id": str(uuid.uuid4()), "tag": tid, "content": content_id,
                   "uuid": str(uuid.uuid4()), "usn": usn, "now": _now_rb()})
            added += 1
        return added

    # -- orchestration ---------------------------------------------------------

    def add_track(self, wsl_path, win_path, filename, title, artist,
                  meta, analysis) -> str:
        fields = content_fields(meta, analysis)

        artist_row = self.resolve_or_create("artist", artist)
        album_row = self.resolve_album(meta.get("album", ""), artist_row)
        genre_row = self.resolve_or_create("genre", meta.get("genre", ""))
        label_row = self.resolve_or_create("label", meta.get("label", ""))
        key_id = self.resolve_key_id(fields["KeyName"])

        content = None
        try:
            content = self.db.add_content(wsl_path, Title=title)
            content.FolderPath = win_path
            content.FileNameL = filename
            if artist_row is not None:
                content.ArtistID = artist_row.ID
            if album_row is not None:
                content.AlbumID = album_row.ID
            if genre_row is not None:
                content.GenreID = genre_row.ID
            if label_row is not None:
                content.LabelID = label_row.ID
            if key_id is not None:
                content.KeyID = key_id
            if fields["BPM"] is not None:
                content.BPM = fields["BPM"]
            if fields["Rating"] is not None:
                content.Rating = fields["Rating"]
            if fields["ColorID"] != "0":
                content.ColorID = fields["ColorID"]
            if fields["ReleaseYear"] is not None:
                content.ReleaseYear = fields["ReleaseYear"]
            if fields["Commnt"]:
                content.Commnt = fields["Commnt"]
            content_id = content.ID
        except ValueError:
            content = None
            existing = self._existing_content_id(win_path)
            if existing is None:
                raise
            content_id = existing
            log.info("  — track already in DB; ensuring tags + playlists only")

        # My Tags
        tag_ids = resolve_tag_ids(self.existing_tag_map(),
                                  computed_tag_names(meta, analysis))
        self.assign_my_tags(content_id, tag_ids)

        # Playlists: Styles/<style> and Labels/<label>.
        # Only join when we hold a fresh content row (dup path leaves the
        # already-imported track's playlist membership untouched — v1 idempotency
        # guarantees "no crash, no dupes", not re-joining existing tracks).
        styles = [s.strip() for s in meta.get("styles", "").split(",") if s.strip()]
        for style in styles:
            pl = self.ensure_playlist("Styles", style)
            if content is not None and not self._already_in_playlist(pl, content_id):
                self.db.add_to_playlist(pl, content)
        if meta.get("label"):
            pl = self.ensure_playlist("Labels", meta["label"].strip())
            if content is not None and not self._already_in_playlist(pl, content_id):
                self.db.add_to_playlist(pl, content)

        return content_id


# ── Guards ───────────────────────────────────────────────────────────────────

import shutil
import subprocess
from pathlib import Path


def rekordbox_running() -> bool:
    """True if rekordbox.exe is running (checked via Windows tasklist from WSL)."""
    try:
        r = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq rekordbox.exe"],
            capture_output=True, text=True, timeout=15)
        return "rekordbox.exe" in r.stdout.lower()
    except Exception:
        return False


def backup_db(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_suffix(db_path.suffix + f".bak-{ts}")
    shutil.copy2(db_path, backup)
    return backup


def restore_db(backup_path: Path, db_path: Path) -> None:
    shutil.copy2(backup_path, db_path)
