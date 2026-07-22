---
name: trackstage
description: Add tracks to the DJ library — source from Soulseek, tag via Discogs, analyze with Essentia, and write straight into Rekordbox. Also lists/processes the inbox. Use when the user says "add <song>".
---

# trackstage — DJ Library

## Add a track (direct-to-DB)

When the user says **"add &lt;song&gt;"** (optionally "by &lt;artist&gt;"), run the
one-shot engine and report what landed. The engine sources from Soulseek,
identifies on Discogs, analyzes with Essentia, files into the Library, and
writes directly into Rekordbox's master.db — no XML, no manual import.

```bash
cd ~/dev/trackstage && .venv/bin/python -m trackstage add "<query>" --json
```

Add `--yes` to auto-take the top candidate. Read the JSON `status`:

| status | meaning | what you do |
|---|---|---|
| `ok` | track written to master.db | Report title, BPM, key, energy, dest. Tell the user it appears on next Rekordbox launch (waveform builds on first load). |
| `choose` | multiple distinct versions | Show the `candidates` list (n, user, file, bitrate, size). Ask which. Re-run adding `--pick N`. |
| `no_source` | no FLAC / MP3≥320 found | Tell the user; offer `--format any` to accept lower bitrate. |
| `refused` | Rekordbox is running | Ask the user to fully quit Rekordbox, then re-run. |
| `dry_run` | preview only | Report what would be downloaded. |
| `error` | DB write failed | master.db was auto-restored from backup. Report the error. |

### Rules

- **Rekordbox must be closed** before the DB write. If `refused`, do not retry
  until the user confirms it's quit.
- Prefer FLAC. Only pass `--format any` when the user accepts lossy or nothing
  ≥320 exists.
- One track per invocation. For several, run once each.
- Never use the rekordbox-database MCP `import_track` for this — it dupes rows
  and skips analysis. The engine is the only ingestion path.

## Batch: process the existing inbox

For loose files/folders already sitting in the DJ Inbox, the batch pipeline
tags → analyzes → moves → writes to the DB (same dbwriter backend, no XML):

| Action | Command |
|--------|---------|
| List inbox | `cd ~/dev/trackstage && .venv/bin/python -m trackstage --list --json` |
| Add one item | `.venv/bin/python -m trackstage --target "NAME" -y --json` |
| Add with Discogs ID | `.venv/bin/python -m trackstage --target "NAME" -y --discogs-id 12345 --json` |
| Process all | `.venv/bin/python -m trackstage -y --json` |
| Dry run | add `--dry-run` |

Match the user's request to inbox item names from `--list --json` (fuzzy). If
multiple match, show them and ask; if none, say so — do not guess. Rekordbox
must be closed for the DB write here too.

## Audio analysis (automatic per track)

Key (Camelot), BPM (half-time corrected for DnB/jungle), Energy (1–10),
Danceability (1–10), Mood/Vibes, Vocal detection, Cue Points, Loudness (EBU
R128 ReplayGain). Energy→Rating stars, mood→track Color, and Genre/Vibe/Sound/
Situation → My Tags are written straight to master.db.

## Auto playlists

- **Styles/{style}** — one per Discogs style
- **Labels/{label}** — one per record label

## Library structure

```
Library/{Year}/{Release Title [Label CatNo]}/Artist - Title.ext
```
