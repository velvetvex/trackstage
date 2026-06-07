---
name: trackstage
description: Use when the user wants to add music to their DJ collection, process their DJ inbox, list inbox contents, manage Rekordbox playlists, or organize their music library
---

# trackstage — DJ Library Management

Manages DJ music pipeline: Discogs lookup → audio analysis (key, BPM, energy, mood, cues, loudness) → tag → rename → move to Library → update Rekordbox XML with auto-playlists.

## Quick Reference

| Action | Command |
|--------|---------|
| List inbox | `trackstage --list --json` |
| Add item | `trackstage --target "NAME" -y --json` |
| Add + playlist | `trackstage --target "NAME" -y --playlist "NAME" --json` |
| Add with Discogs ID | `trackstage --target "NAME" -y --discogs-id 12345 --json` |
| Process all | `trackstage -y --json` |
| Dry run | Add `--dry-run` to any command |
| Rebuild playlists | `trackstage --rebuild-playlists` |

## Workflow

1. Run `--list --json` to see inbox contents
2. Match user's request to inbox item(s) using fuzzy name matching on the JSON output
3. Run pipeline with `--target` for each match — always use `-y` and `--json`
4. Parse JSON output for results
5. Report to user: what was tagged, where it moved, which playlists it landed in
6. After successful processing, remind: "Import updated XML in Rekordbox: File → Import → Import rekordbox XML File"

## Matching User Intent

- "add [name]" → search inbox item names from `--list --json` output
- Folders usually named "Artist - Release" or "Release [Label CatNo]"
- Files usually named "NN-track.ext" or "Artist - Title.ext"
- If multiple matches, show them and ask user to pick
- If zero matches, say so — do not guess

## Audio Analysis (automatic per track)

Each track is analyzed for:
- **Key** — Camelot notation (e.g. 8A, 5B) for harmonic mixing
- **BPM** — with smart half-time correction for DnB/jungle
- **Energy** (1–10) — calibrated acoustic intensity
- **Danceability** (1–10) — blended rhythmic regularity + onset density
- **Mood/Vibes** — dark, euphoric, deep, melancholic, driving
- **Vocal** — instrumental vs. voice detection
- **Cue Points** — Mix In/Out, Drops, Breakdowns, Sections (written to Rekordbox XML)
- **Loudness** — EBU R128 ReplayGain tags for consistent playback

## Auto Playlists

Pipeline auto-creates Rekordbox playlists from Discogs metadata:
- **Styles/{style}** — one per Discogs style (Techno, House, Acid, etc.)
- **Labels/{label}** — one per record label
- **Recent** — last 100 tracks added
- Custom playlists via `--playlist` flag

## Library Structure

```
Library/{Year}/{Release Title [Label CatNo]}/Artist - Title.ext
```

## Tags Written

Genre, Styles, Label, Catalog Number, Year, Album, Key (Camelot), BPM, Energy, Danceability, ReplayGain, Comment (styles + catno + vibes)
