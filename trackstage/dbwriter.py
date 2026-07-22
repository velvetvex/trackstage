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
