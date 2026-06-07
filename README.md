# trackstage

Automated DJ music library management with audio intelligence. Handles the full pipeline from inbox to CDJ: Discogs metadata, audio analysis (key, BPM, energy, mood), automatic cue points, loudness normalization, and Rekordbox XML generation.

## Features

### Metadata Pipeline
- Discogs API lookup with fuzzy matching
- Genre, style, label, catalog number, year tagging
- Organized file structure: `Library/{Year}/{Release [Label CatNo]}/Artist - Title.ext`
- Rekordbox XML with collection entries and auto-playlists (by style, label, recent)

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

# Add to a custom playlist
trackstage --target "track.flac" -y --playlist "Summer 2026"

# Rebuild playlists from existing XML
trackstage --rebuild-playlists
```

Or run as a module:

```bash
python -m trackstage --list
```

## Architecture

```
trackstage/
├── pipeline.py          # CLI entry point, Discogs lookup, file management, XML
├── audio_analysis.py    # Key, BPM, energy, danceability (Essentia)
├── cue_detection.py     # Structural cue point detection
├── mood_detection.py    # ML mood/vibe classification (TensorFlow)
└── loudness.py          # EBU R128 measurement + ReplayGain tags
```

Analysis modules degrade gracefully — if Essentia isn't installed, the pipeline still runs with Discogs-only metadata.

## Supported Formats

MP3, FLAC, AIFF, M4A/AAC

## Rekordbox Integration

After processing, import the XML in Rekordbox:

1. **Preferences → Advanced → Database → rekordbox xml** — set the XML path
2. The XML contains track metadata, cue points (with colors), and playlists
3. Export to USB for CDJ/XDJ use — playlists become browsable folders

### Auto-Generated Playlists

- **Styles/** — One playlist per style (Techno, Acid, Deep House, DnB, etc.)
- **Labels/** — One playlist per record label
- **Recent** — Last 100 tracks added

## License

MIT
