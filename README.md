# DJ Library Pipeline

Automated DJ music library management: Discogs metadata lookup, audio tagging, file organization, and Rekordbox XML generation with auto-playlists.

## What It Does

1. Scans an inbox folder for new music (loose files or release folders)
2. Looks up each track/release on Discogs with fuzzy matching
3. Writes metadata tags to audio files (genre, styles, label, catalog number, year)
4. Renames and moves files to an organized library: `Library/{Year}/{Release [Label CatNo]}/Artist - Title.ext`
5. Updates a Rekordbox XML file with collection entries and auto-generated playlists

### Auto Playlists

Playlists are created automatically from Discogs metadata:
- **Styles/** — one playlist per style (Techno, House, Acid, Deep House, etc.)
- **Labels/** — one playlist per record label
- **Recent** — last 100 tracks added
- Custom playlists via `--playlist` flag

When exported to USB from Rekordbox, these playlists become browsable folders on CDJs/XDJs.

## Setup

### Requirements

```bash
pip install mutagen thefuzz python-Levenshtein requests python-dotenv
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your Discogs token and folder paths
```

Get a Discogs token at: https://www.discogs.com/settings/developers

### Claude Code Skill (optional)

Copy the skill directory to enable natural language control:

```bash
cp -r skill/dj-library ~/.claude/skills/
```

Then tell Claude things like "add that new Bicep record to my collection" or "what's in my inbox?"

## Usage

```bash
# List inbox contents
python3 pipeline.py --list

# Process a specific item
python3 pipeline.py --target "Artist - EP Name" -y

# Process everything in the inbox
python3 pipeline.py -y

# Add to a custom playlist
python3 pipeline.py --target "track.flac" -y --playlist "Summer 2026"

# Override Discogs match with a known release ID
python3 pipeline.py --target "track.flac" -y --discogs-id 12345

# Preview without making changes
python3 pipeline.py --dry-run -y

# JSON output (for automation)
python3 pipeline.py --list --json
python3 pipeline.py --target "track.flac" -y --json
```

## Supported Formats

MP3, FLAC, AIFF, M4A

## Rekordbox Import

After processing, import the updated XML in Rekordbox:

1. File → Import → Import rekordbox XML File
2. Select the XML file path from your `.env`
3. Rekordbox picks up new tracks and playlists automatically
