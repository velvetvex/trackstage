---
name: dj-library
description: Use when the user wants to add music to their DJ collection, process their DJ inbox, list inbox contents, manage Rekordbox playlists, or organize their music library
---

# DJ Library Management

Manages DJ music pipeline: Discogs lookup → tag audio files → rename → move to Library → update Rekordbox XML with auto-playlists (Styles, Labels, Recent).

## Quick Reference

| Action | Command |
|--------|---------|
| List inbox | `python3 ~/dj-library/pipeline.py --list --json` |
| Add item | `python3 ~/dj-library/pipeline.py --target "NAME" -y --json` |
| Add + playlist | `python3 ~/dj-library/pipeline.py --target "NAME" -y --playlist "NAME" --json` |
| Add with Discogs ID | `python3 ~/dj-library/pipeline.py --target "NAME" -y --discogs-id 12345 --json` |
| Process all | `python3 ~/dj-library/pipeline.py -y --json` |
| Dry run | Add `--dry-run` to any command |

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

Genre, Styles, Label, Catalog Number, Year, Album (release title), Comment (styles + catno)
