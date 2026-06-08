"""
xml.py — Rekordbox XML management (single source of truth).

Handles: location encoding, XML bootstrap/load/save, track updates,
collection CRUD, playlist management (Styles, Labels, Recent, custom).
"""

import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote


# ── Constants ────────────────────────────────────────────────────────────────

RECENT_PLAYLIST_CAP = 100

KIND_MAP = {
    ".mp3":  "MP3 File",
    ".flac": "FLAC File",
    ".aiff": "AIFF File",
    ".aif":  "AIFF File",
    ".m4a":  "AAC File",
}

ENERGY_TO_RATING = {
    "1": "51", "2": "51",
    "3": "102", "4": "102",
    "5": "153", "6": "153",
    "7": "204", "8": "204",
    "9": "255", "10": "255",
}

MOOD_PRIORITY = ["aggressive", "sad", "happy", "party", "relaxed"]

MOOD_TO_COLOUR = {
    "aggressive": "0xFF0000",
    "happy":      "0xFFA500",
    "party":      "0xFFFF00",
    "relaxed":    "0x00FF00",
    "sad":        "0x8000FF",
}


# ── Location encoding ────────────────────────────────────────────────────────

def to_rb_location(path: Path) -> str:
    """Convert WSL /mnt/X/... path to file://localhost/X:/... URL for Rekordbox."""
    posix = path.as_posix()
    m = re.match(r"/mnt/([a-zA-Z])/(.*)", posix)
    if m:
        posix = f"{m.group(1).upper()}:/{m.group(2)}"
    encoded = quote(posix, safe="/:@!$&'()*+,;=-._~")
    if not encoded.startswith("/"):
        encoded = "/" + encoded
    return f"file://localhost{encoded}"


def to_rb_windows_path(path: Path) -> str:
    """Convert WSL /mnt/X/... path to X:/... (no URL encoding, for DB matching)."""
    posix = path.as_posix()
    m = re.match(r"/mnt/([a-zA-Z])/(.*)", posix)
    if m:
        return f"{m.group(1).upper()}:/{m.group(2)}"
    return posix


# ── XML sanitization ─────────────────────────────────────────────────────────

def sanitize_xml(s: str) -> str:
    """Strip control characters that are invalid in XML."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s))


# ── XML load/save ────────────────────────────────────────────────────────────

def load_or_bootstrap_xml(xml_path: Path) -> tuple:
    """Load existing Rekordbox XML or create a new DJ_PLAYLISTS structure.

    Returns (tree, root, max_track_id).
    """
    if not xml_path.exists():
        root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
        ET.SubElement(root, "PRODUCT",
                      Name="rekordbox", Version="7.0.0", Company="AlphaTheta")
        ET.SubElement(root, "COLLECTION", Entries="0")
        pl = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="0")
        return ET.ElementTree(root), root, 0

    tree = ET.parse(xml_path)
    root = tree.getroot()
    max_id = 0
    for track in root.findall(".//COLLECTION/TRACK"):
        try:
            max_id = max(max_id, int(track.get("TrackID", 0)))
        except ValueError:
            pass
    return tree, root, max_id


def save_xml(tree: ET.ElementTree, xml_path: Path):
    """Write XML with UTF-8 header and pretty indentation."""
    ET.indent(tree, space="  ")
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(xml_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)


# ── Track update ─────────────────────────────────────────────────────────────

def _pick_colour(moods: list) -> str:
    """Select Rekordbox colour based on mood priority."""
    for mood in MOOD_PRIORITY:
        if mood in moods:
            return MOOD_TO_COLOUR[mood]
    return ""


def update_xml_track(track_el: ET.Element, r: dict) -> bool:
    """Update a TRACK element with all analysis fields from result dict.

    Args:
        track_el: existing XML TRACK element
        r: analysis result dict with keys like bpm, camelot, energy,
           danceability, vibes, moods, vocal, cues

    Returns:
        True if any field was changed.
    """
    from trackstage.tags import merge_comment, build_grouping
    from trackstage.cue_detection import CUE_COLORS

    changed = False

    # BPM
    if r.get("bpm"):
        track_el.set("AverageBpm", r["bpm"])
        changed = True

    # Key (Camelot notation)
    if r.get("camelot"):
        track_el.set("Tonality", r["camelot"])
        changed = True

    # Grouping (vibes + moods)
    grouping = build_grouping(r)
    if grouping:
        track_el.set("Grouping", sanitize_xml(grouping))
        changed = True

    # Comments (merge analysis into existing)
    existing_comment = track_el.get("Comments", "")
    new_comment = merge_comment(
        existing_comment,
        r.get("energy", ""),
        r.get("danceability", ""),
        ", ".join(r.get("vibes", [])),
        r.get("vocal", ""),
    )
    if new_comment != existing_comment:
        track_el.set("Comments", sanitize_xml(new_comment))
        changed = True

    # Rating (energy mapped to Rekordbox 0-255 scale)
    if r.get("energy"):
        rating = ENERGY_TO_RATING.get(r["energy"], "")
        if rating:
            track_el.set("Rating", rating)
            changed = True

    # Colour (primary mood)
    colour = _pick_colour(r.get("moods", []))
    if colour:
        track_el.set("Colour", colour)
        changed = True

    # Cue points (replace existing POSITION_MARK elements)
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

    return changed


# ── Playlist helpers ─────────────────────────────────────────────────────────

def _find_or_create_folder(parent_node: ET.Element, name: str) -> ET.Element:
    for child in parent_node:
        if child.get("Type") == "0" and child.get("Name") == name:
            return child
    folder = ET.SubElement(parent_node, "NODE", Type="0", Name=name, Count="0")
    return folder


def _find_or_create_playlist(parent_node: ET.Element, name: str) -> ET.Element:
    for child in parent_node:
        if child.get("Type") == "1" and child.get("Name") == name:
            return child
    playlist = ET.SubElement(parent_node, "NODE",
                             Type="1", Name=name, KeyType="0", Entries="0")
    return playlist


def _add_track_to_playlist(playlist: ET.Element, track_id: str):
    for existing in playlist:
        if existing.get("Key") == track_id:
            return
    ET.SubElement(playlist, "TRACK", Key=track_id)
    current = int(playlist.get("Entries", "0"))
    playlist.set("Entries", str(current + 1))


def _trim_playlist(playlist: ET.Element, max_entries: int):
    tracks = list(playlist.findall("TRACK"))
    if len(tracks) <= max_entries:
        return
    for track in tracks[:len(tracks) - max_entries]:
        playlist.remove(track)
    playlist.set("Entries", str(max_entries))


def _update_folder_counts(root_node: ET.Element):
    count = 0
    for child in root_node:
        if child.tag == "NODE":
            count += 1
            if child.get("Type") == "0":
                _update_folder_counts(child)
    root_node.set("Count", str(count))


def update_playlists(
    root: ET.Element,
    track_entries: list,
    custom_playlist: Optional[str] = None,
    dry_run: bool = False,
):
    """Update playlist structure with newly added tracks.

    track_entries: list of {"track_id": str, "meta": dict}
    """
    if not track_entries or dry_run:
        return

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        playlists_node = ET.SubElement(root, "PLAYLISTS")

    root_node = playlists_node.find("NODE[@Name='ROOT']")
    if root_node is None:
        root_node = ET.SubElement(playlists_node, "NODE",
                                  Type="0", Name="ROOT", Count="0")

    styles_folder = _find_or_create_folder(root_node, "Styles")
    labels_folder = _find_or_create_folder(root_node, "Labels")
    recent_pl = _find_or_create_playlist(root_node, "Recent")

    custom_pl = None
    if custom_playlist:
        custom_pl = _find_or_create_playlist(root_node, custom_playlist)

    for entry in track_entries:
        tid = entry["track_id"]
        meta = entry["meta"]

        styles_str = meta.get("styles", "")
        for style in styles_str.split(", "):
            style = style.strip()
            if style:
                pl = _find_or_create_playlist(styles_folder, style)
                _add_track_to_playlist(pl, tid)

        label = meta.get("label", "").strip()
        if label:
            pl = _find_or_create_playlist(labels_folder, label)
            _add_track_to_playlist(pl, tid)

        _add_track_to_playlist(recent_pl, tid)

        if custom_pl:
            _add_track_to_playlist(custom_pl, tid)

    _trim_playlist(recent_pl, RECENT_PLAYLIST_CAP)
    _update_folder_counts(root_node)


def rebuild_playlists(xml_path: Path, as_json: bool = False):
    """Rebuild all playlists from existing XML tracks.

    Styles are extracted from Comments (e.g. "Techno, Acid | Cat# XYZ").
    Labels are read from the Label attribute.
    """
    import json

    tree, root, _ = load_or_bootstrap_xml(xml_path)
    collection = root.find("COLLECTION")
    if collection is None:
        if as_json:
            print(json.dumps({"error": "No COLLECTION in XML"}))
        else:
            print("  No COLLECTION found in XML.")
        return

    tracks = collection.findall("TRACK")
    if not tracks:
        if as_json:
            print(json.dumps({"error": "No tracks in XML"}))
        else:
            print("  No tracks in XML.")
        return

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        playlists_node = ET.SubElement(root, "PLAYLISTS")

    root_node = playlists_node.find("NODE[@Name='ROOT']")
    if root_node is None:
        root_node = ET.SubElement(playlists_node, "NODE",
                                  Type="0", Name="ROOT", Count="0")

    for name in ("Styles", "Labels"):
        for child in list(root_node):
            if child.get("Type") == "0" and child.get("Name") == name:
                root_node.remove(child)

    for child in list(root_node):
        if child.get("Type") == "1" and child.get("Name") == "Recent":
            root_node.remove(child)

    styles_folder = _find_or_create_folder(root_node, "Styles")
    labels_folder = _find_or_create_folder(root_node, "Labels")
    recent_pl = _find_or_create_playlist(root_node, "Recent")

    style_counts = {}
    label_counts = {}

    for track in tracks:
        tid = track.get("TrackID", "")
        label = track.get("Label", "").strip()
        comments = track.get("Comments", "").strip()

        styles_str = comments.split(" | ")[0] if comments else ""
        if styles_str.startswith("Cat#"):
            styles_str = ""

        for style in styles_str.split(", "):
            style = style.strip()
            if style:
                pl = _find_or_create_playlist(styles_folder, style)
                _add_track_to_playlist(pl, tid)
                style_counts[style] = style_counts.get(style, 0) + 1

        if label:
            pl = _find_or_create_playlist(labels_folder, label)
            _add_track_to_playlist(pl, tid)
            label_counts[label] = label_counts.get(label, 0) + 1

        _add_track_to_playlist(recent_pl, tid)

    _trim_playlist(recent_pl, RECENT_PLAYLIST_CAP)
    _update_folder_counts(root_node)
    save_xml(tree, xml_path)

    if as_json:
        print(json.dumps({
            "tracks": len(tracks),
            "style_playlists": len(style_counts),
            "label_playlists": len(label_counts),
            "top_styles": sorted(style_counts.items(), key=lambda x: -x[1])[:15],
            "top_labels": sorted(label_counts.items(), key=lambda x: -x[1])[:15],
        }, indent=2))
    else:
        print(f"\n{'=' * 64}")
        print(f"  Playlist Rebuild — {len(tracks)} tracks")
        print(f"{'=' * 64}")
        print(f"  Style playlists:  {len(style_counts)}")
        print(f"  Label playlists:  {len(label_counts)}")
        print(f"  Recent playlist:  {min(len(tracks), RECENT_PLAYLIST_CAP)} tracks")
        print(f"\n  Top styles:")
        for s, c in sorted(style_counts.items(), key=lambda x: -x[1])[:15]:
            print(f"    {c:4d}  {s}")
        print(f"\n  Top labels:")
        for l, c in sorted(label_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"    {c:4d}  {l}")
        print(f"\n{'=' * 64}")
        print(f"  XML saved -> {xml_path}\n")
