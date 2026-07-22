"""add.py — `trackstage add "<query>"` engine.

Source (Soulseek) → identify (Discogs) → analyze (Essentia) → organize →
write directly to Rekordbox master.db. No XML, no manual import.
"""

import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


def run_add(args: argparse.Namespace) -> int:
    """Orchestrate the full add pipeline. Filled in by Task 7."""
    raise NotImplementedError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trackstage add",
        description="Add one track: Soulseek → Discogs → Essentia → Rekordbox DB",
    )
    p.add_argument("query", help="Search query, e.g. 'E Talking by Soulwax'")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview: no download, no DB commit")
    p.add_argument("--format", choices=["flac", "any"], default="flac",
                   help="flac = lossless only w/ MP3>=320 fallback; any = best available")
    p.add_argument("--pick", type=int, default=None,
                   help="Choose candidate N from a prior ranked shortlist")
    p.add_argument("--discogs-id", type=int, default=None,
                   help="Force this Discogs release ID")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Auto-accept the top candidate without prompting")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON (for the skill)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run_add(args)


if __name__ == "__main__":
    sys.exit(main())
