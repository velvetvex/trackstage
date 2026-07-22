# trackstage

Automated DJ music library management with audio intelligence. Handles the full pipeline from inbox to CDJ: Discogs metadata, audio analysis (key, BPM, energy, mood), automatic cue points, loudness normalization, and **direct writes into the Rekordbox `master.db`** — no XML export, no manual import.

## Quick add

Source a track from Soulseek and land it fully tagged in Rekordbox in one command:

```bash
trackstage add "E Talking by Soulwax" -y
```

The engine sources from Soulseek (slskd), identifies on Discogs, analyzes with
Essentia, files it into the Library, and writes the Content row + tags + Styles/
Labels playlists straight into `master.db`. **Rekordbox must be closed** for the
write; a timestamped `master.db` backup is taken first and restored on failure.
The track appears on next Rekordbox launch (waveform builds on first load).

Prefer FLAC (or MP3 ≥320); pass `--format any` to accept lower bitrate. Use
`--json` for machine-readable output, `--dry-run` to preview, `--pick N` to
choose from a shortlist.

## Features

### Metadata Pipeline
- Discogs API lookup with fuzzy matching
- Genre, style, label, catalog number, year tagging
- Organized file structure: `Library/{Year}/{Release [Label CatNo]}/Artist - Title.ext`
- Direct Rekordbox `master.db` writes: Content + Artist/Album/Genre/Label/Key,
  BPM, energy→Rating, mood→Color, comment, My Tags, and auto-playlists (Styles, Labels)

### Audio Analysis
- **Key detection** — Camelot wheel notation for harmonic mixing
- **BPM** — Smart half-time correction (handles DnB, jungle, breakbeat)
- **Energy** (1–10) — Calibrated from library percentiles
- **Danceability** (1–10) — Blended DFA + onset density
- **Mood/Vibes** — ML classification (dark, euphoric, deep, melancholic, driving)
- **Vocal detection** — Instrumental vs. voice

### Cue Points
- Auto-detected structural markers for Rekordbox
- Mix In / Mix Out points for seamless transitions
- Drop and Breakdown detection via energy contour analysis
- Section markers for flat-energy tracks (loops, acid, etc.)

### Loudness Normalization
- EBU R128 integrated loudness measurement
- ReplayGain tags for consistent playback across eras
- Non-destructive — audio files are never modified, only metadata

## Setup

### Requirements

Python 3.10+

```bash
# Core (metadata pipeline)
pip install -e .

# Full (with audio analysis)
pip install -e ".[analysis]"
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` with your [Discogs token](https://www.discogs.com/settings/developers) and folder paths.

### ML Models

Download Essentia TensorFlow models for mood/vibe classification:

```bash
./scripts/download_models.sh
```

## Usage

```bash
# List inbox contents
trackstage --list

# Process a specific release
trackstage --target "Artist - EP Name" -y

# Process everything
trackstage -y

# Preview without changes
trackstage --dry-run -y

# Override Discogs match
trackstage --target "track.flac" -y --discogs-id 12345

# Source + add a single track end-to-end
trackstage add "E Talking by Soulwax" -y
```

Batch processing writes to `master.db` too — Rekordbox must be closed and a
backup is taken automatically.

Or run as a module:

```bash
python -m trackstage --list
```

## Architecture

```
trackstage/
├── pipeline.py          # Batch CLI entry point, Discogs lookup, file management
├── add.py               # `trackstage add` engine: source→identify→analyze→organize→DB
├── sourcer.py           # Soulseek sourcing via slskd REST + candidate ranking
├── identifier.py        # Reusable Discogs release identification
├── dbwriter.py          # Direct master.db writes (Content/tags/playlists), guards, backup
├── rekordbox.py         # Tag computation + DB helpers
├── audio_analysis.py    # Key, BPM, energy, danceability (Essentia)
├── cue_detection.py     # Structural cue point detection
├── mood_detection.py    # ML mood/vibe classification (TensorFlow)
└── loudness.py          # EBU R128 measurement + ReplayGain tags
```

Analysis modules degrade gracefully — if Essentia isn't installed, the pipeline still runs with Discogs-only metadata.

## Supported Formats

MP3, FLAC, AIFF, M4A/AAC

## Rekordbox Integration

trackstage writes **directly** into Rekordbox's encrypted `master.db` via
pyrekordbox — no XML export or manual import. Guardrails:

1. **Rekordbox must be closed** for any write (refused otherwise).
2. A timestamped `master.db` backup is taken before every write and restored on failure.
3. Set `REKORDBOX_DB` in `.env` to override the default DB path.

Tracks appear on next Rekordbox launch; Rekordbox builds the waveform/beatgrid
(ANLZ) and regenerates the key on first load.

### Auto-Generated Playlists

- **Styles/** — One playlist per style (Techno, Acid, Deep House, DnB, etc.)
- **Labels/** — One playlist per record label

## License

MIT
