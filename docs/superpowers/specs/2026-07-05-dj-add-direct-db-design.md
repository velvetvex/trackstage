# DJ "add song" — direct-to-Rekordbox-DB pipeline

**Date:** 2026-07-05
**Status:** Design approved, pending spec review
**Project:** trackstage

## Problem

The current flow is three disconnected steps with a manual gap in the middle:

1. `trackstage` (pipeline.py) → Discogs tag + organize + write `rekordbox_library.xml`
2. **User manually** opens Rekordbox → File → Import → Import rekordbox XML File (creates `djmdContent` rows)
3. `sync_rekordbox.py` → separate pass, writes energy→Rating, mood→Color, My Tags directly to `master.db`

`sync_rekordbox.py` matches analysis-cache paths against `djmdContent.FolderPath` rows, which only exist **after** step 2. So analysis tags cannot land until the user clicks Import in the GUI. Verified: analyzing a track then running sync = 0 changes, because the track has no `djmdContent` row until imported. The manual XML-import step is the friction.

## Goal

One conversational command — **"add &lt;song&gt;"** — sources the track (Soulseek/slskd), analyzes it (Essentia), tags it (Discogs), organizes it into the Library, and writes it **directly to `master.db`** via pyrekordbox. No XML, no manual import, no separate sync. Next time the user opens Rekordbox, the track is present with full metadata, key, BPM, rating, color, My Tags, and playlist membership.

## Decisions (locked with user)

- **Rekordbox usually closed when adding** → write to `master.db` immediately; no queue/flush machinery. If Rekordbox is running, refuse the write and tell the user to close it.
- **Confirm on ambiguity** → auto-pick when one clearly-best FLAC exists; show a ranked shortlist and let the user choose when distinct versions exist (original vs remix vs edit).
- **"Fully in library" = metadata + tags** → waveform/beatgrid (ANLZ) are NOT pre-generated; Rekordbox builds them when the track is first loaded. Essentia BPM + key ARE pre-written so the track is usable immediately.
- **Reuse brains, replace backend** → keep trackstage's analyzer / Discogs matching / tag computation / cue / loudness modules. Delete `xml.py`, XML generation, the manual-import step, and the standalone `sync_rekordbox.py` pass. Build one new direct-to-DB writer + a conversational skill.

## Architecture

Five units with clear interfaces, orchestrated by a `trackstage add` engine and driven conversationally by the rewritten `dj-library` skill.

| Unit | Input → Output | Status |
|---|---|---|
| **sourcer** | query → local file path + ranked candidate list | NEW — wraps slskd MCP (search / responses / enqueue / downloads) |
| **identifier** | file / name → Discogs release metadata | REUSE — extract Discogs matching from `pipeline.py` |
| **analyzer** | file → `{bpm, key, camelot, energy, danceability, moods, vibes, vocal, cues}` | REUSE — `analyzer.py`, unchanged |
| **organizer** | file + metadata → final Library path + written file tags | REUSE — file-org logic + `tags.py` |
| **dbwriter** | Library path + metadata + analysis → Content ID + playlist rows | **NEW — the heart** |

### dbwriter (new — replaces `xml.py` + `sync_rekordbox.py`)

One pyrekordbox transaction against `master.db`:

1. **Content row** via `add_content(file_path, **kwargs)`:
   - `ArtistID`, `AlbumID`, `GenreID`, `LabelID` — resolve-or-create related rows (pattern already in rekordbox-mcp `_resolve_or_create`)
   - `KeyID` — resolve-or-create `djmdKey` row from Essentia key (Rekordbox stores key as `djmdKey.KeyName`, referenced by `Content.KeyID`)
   - `BPM` — Essentia BPM × 100 (Rekordbox stores int×100)
   - `Commnt` — styles + catno comment (trackstage convention)
   - `Rating` — energy (1–10) → stars (1–5) via `ENERGY_TO_RATING`
   - `ColorID` — mood → color via `MOOD_TO_COLOR` / `pick_color_id`
2. **My Tags** — insert `djmdSongMyTag` rows for computed Genre / Vibe / Sound / Situation tags (reuse `trackstage/rekordbox.py` tag computation + assignment logic, incl. USN sequencing and existing-assignment dedupe)
3. **Playlists** — ensure `Styles/{style}` and `Labels/{label}` playlists exist (create `djmdPlaylist` folders + children as needed), add `djmdSongPlaylist` membership
4. **Commit**

**Guards:**
- Refuse write if Rekordbox process is running (pyrekordbox `commit()` blocks anyway; fail fast with a clear message before doing work)
- Backup `master.db` (timestamped) before the transaction; **restore the backup if commit raises**
- Idempotent: detect existing track by `FolderPath` (and artist+title fallback); skip Content insert, only ensure playlist membership + missing tags

**Deleted:** `xml.py`, XML generation path, `sync_rekordbox.py` as a standalone script, all manual File→Import guidance.

## Data flow

```
user: "add E Talking by Soulwax"
  → sourcer: slskd search, filter FLAC, rank (free slot / queue / quality)
       ambiguous (distinct versions)? → show ranked shortlist, user picks
       one clear best? → auto
  → enqueue → wait for completion in Inbox
       dead peer / stall? → retry next ranked source (≤3)
  → identifier: Discogs match
       low confidence? → confirm / fall back to file tags + filename
  → analyzer: Essentia → bpm, key, energy, mood, cues (cached)
  → organizer: move → Library/{Year}/{Release [Label CatNo]}/Artist - Title.ext
       write file tags (mutagen) for portability
  → dbwriter: [Rekordbox closed? backup master.db] → one txn:
       Content (+ Artist/Album/Genre/Label/Key/BPM/Rating/Color/Commnt)
       + My Tags (Genre/Vibe/Sound/Situation)
       + ensure & join Styles/{style} + Labels/{label}
     commit  (restore backup on failure)
  → report: "✓ E Talking in library — 6A, 128.8 BPM, energy 5,
             Styles/Electro + Labels/[PIAS] Recordings"
next Rekordbox launch: track present; waveform builds on first load
```

## Error handling

| Failure | Behavior |
|---|---|
| No FLAC on Soulseek | Offer best MP3 / AIFF / M4A instead, or abort — ask the user |
| Download stalls / dead peer | Auto-retry next ranked source (≤3), then report failure |
| Discogs no / low match | Fall back to file tags + filename; report what was used; offer manual `--discogs-id` |
| Rekordbox running | Refuse DB write, tell user to close it. Nothing half-written |
| Commit throws | Restore the pre-write `master.db` backup |
| Duplicate track | Detect by `FolderPath` (+ artist/title); skip Content insert, ensure playlist membership only. Idempotent |
| Partial pipeline crash | File remains in Inbox; nothing written to DB; re-runnable from clean state |

## Testing

- **dbwriter** (critical): unit tests on mocked pyrekordbox (mirror trackstage's existing ~90-test mock setup) — Content fields, energy→Rating, mood→ColorID, Key resolve-or-create, My Tag inserts + USN sequencing, playlist ensure+join, **idempotency** (add twice → one Content row), Rekordbox-running guard, backup-restore-on-commit-failure.
- **sourcer**: mock slskd API responses — FLAC filter, ranking, disambiguation trigger.
- **Reuse** existing analyzer / Discogs / tag tests unchanged.
- **`--dry-run`**: full pipeline, no download / no DB commit — prints what would happen.

## Interface

- **Engine:** `trackstage add "<query>"` — one-shot deterministic pipeline. Flags: `--dry-run`, `--discogs-id <id>`, `--format flac|any`, `--yes`.
- **Skill:** rewrite `~/.claude/skills/dj-library` — teaches Claude to drive the engine conversationally, handle the disambiguation shortlist (Claude shows candidates from sourcer, user picks), and report results. Removes all XML-import instructions.

## Out of scope (YAGNI)

- Queue/flush-while-Rekordbox-open (user adds with RB closed)
- Waveform/beatgrid pre-generation (Rekordbox builds on load)
- Batch "add N songs" in one command (v1 is one track; batching is a later iteration)
- Loudness/ReplayGain pass (optional; not required for v1 "shows up in library")

## Follow-up

Update `project_rekordbox-write-ownership` memory: **dbwriter** becomes the canonical writer for Content rows + Styles/Labels playlists (via DB, not XML). The rekordbox-database MCP retains reads + manual/ad-hoc playlists. One-writer-at-a-time + Rekordbox-closed + backup guardrails still hold.
