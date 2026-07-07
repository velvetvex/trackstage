# DJ "add song" — direct-to-Rekordbox-DB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command — `trackstage add "<query>"` — sources a track from Soulseek, identifies it on Discogs, analyzes it with Essentia, organizes it into the Library, and writes it straight into Rekordbox's `master.db` so it appears fully tagged on next launch — no XML, no manual import.

**Architecture:** Reuse trackstage's existing analyzer / Discogs / tag-computation / loudness modules unchanged. Add three new units: a **sourcer** (slskd REST client + candidate ranking), a **dbwriter** (pyrekordbox transaction that replaces `xml.py` + `sync_rekordbox.py`), and an **add engine** (`trackstage/add.py`) that orchestrates them. A rewritten `dj-library` skill drives it conversationally. The old XML batch path is redirected through the dbwriter, then `xml.py` / `sync_rekordbox.py` are deleted.

**Tech Stack:** Python 3.10+, pyrekordbox 0.4.4 (SQLCipher master.db), SQLAlchemy (via pyrekordbox), Essentia (analysis, already integrated), mutagen (tags), requests (slskd REST + Discogs), pytest.

## Global Constraints

- **Rekordbox MUST be closed for any DB write.** dbwriter refuses to write if `rekordbox.exe` is running (checked via `tasklist.exe`). Copied verbatim from spec: "If Rekordbox is running, refuse the write and tell the user to close it."
- **Back up `master.db` before every write; restore on failure.** Timestamped copy beside the DB.
- **Rekordbox stores Windows paths** (`C:/Users/Kaitlyn/...`), not WSL paths (`/mnt/c/...`). Every `FolderPath` written to the DB MUST be the `C:/` form via `trackstage.rekordbox.to_rb_windows_path`.
- **master.db default path:** `/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db` (override via `REKORDBOX_DB` env).
- **slskd REST:** `http://localhost:5030`, header `X-API-Key: <SLSKD_API_KEY>`. The autonomous CLI talks to slskd over REST directly — the slskd **MCP** is for Claude's conversational/manual use only, not the engine.
- **Format policy:** prefer FLAC (or any lossless); else best MP3 with bitrate ≥ 320 kbps; nothing ≥ 320 → abort. Verbatim from spec.
- **Never create new My Tags or new djmdKey rows.** Resolve computed tag names against existing `djmdMyTag` rows by name (case-insensitive); skip unmatched. KeyID: reuse an existing `djmdKey` row matching the Essentia camelot; skip if absent (Rekordbox regenerates key when it builds ANLZ on first load).
- **New env keys** (add to `.env` / `.env.example`): `SLSKD_URL` (default `http://localhost:5030`), `SLSKD_API_KEY`, `REKORDBOX_DB` (optional).

---

### Task 1: Add dependencies + `add` subcommand dispatch

**Files:**
- Modify: `pyproject.toml` (dependencies, scripts)
- Modify: `trackstage/pipeline.py:1169-1247` (main — dispatch `add` before argparse)
- Create: `trackstage/add.py`
- Modify: `.env.example`
- Test: `tests/test_add_cli.py`

**Interfaces:**
- Produces: `trackstage.add.main(argv: list[str] | None = None) -> int` — the add-engine entry point. Returns process exit code. Later tasks fill in its body; this task establishes the CLI surface and dispatch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_add_cli.py
"""Tests for the `trackstage add` CLI surface."""

import subprocess
import sys


def test_add_no_query_exits_nonzero():
    r = subprocess.run(
        [sys.executable, "-m", "trackstage", "add"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "query" in (r.stdout + r.stderr).lower()


def test_add_parses_query_and_flags(monkeypatch):
    """add.main should parse a query plus flags without raising."""
    from trackstage import add
    captured = {}

    def fake_run(args):
        captured["query"] = args.query
        captured["dry_run"] = args.dry_run
        captured["fmt"] = args.format
        return 0

    monkeypatch.setattr(add, "run_add", fake_run)
    rc = add.main(["E Talking by Soulwax", "--dry-run", "--format", "any"])
    assert rc == 0
    assert captured["query"] == "E Talking by Soulwax"
    assert captured["dry_run"] is True
    assert captured["fmt"] == "any"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_add_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackstage.add'`

- [ ] **Step 3: Create `trackstage/add.py` with CLI skeleton**

```python
# trackstage/add.py
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
```

- [ ] **Step 4: Wire dispatch into `pipeline.main`**

In `trackstage/pipeline.py`, at the very top of `main()` (line ~1169, before `inbox_default = ...`), insert:

```python
def main():
    # Subcommand dispatch: `trackstage add "<query>" ...`
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        from .add import main as add_main
        sys.exit(add_main(sys.argv[2:]))

    inbox_default   = os.environ.get("INBOX_PATH", "")
    # ... rest unchanged
```

- [ ] **Step 5: Add deps + console script + env example**

In `pyproject.toml`, add to `dependencies` (after `"numpy>=1.24",`):

```toml
    "pyrekordbox>=0.4,<0.5",
    "SQLAlchemy>=2.0",
```

In `.env.example`, append:

```
# Soulseek (slskd) — sourcing for `trackstage add`
SLSKD_URL=http://localhost:5030
SLSKD_API_KEY=

# Rekordbox database (optional override)
REKORDBOX_DB=/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_add_cli.py -v`
Expected: PASS (both tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml trackstage/add.py trackstage/pipeline.py .env.example tests/test_add_cli.py
git commit -m "feat(add): CLI skeleton + subcommand dispatch for 'trackstage add'"
```

---

### Task 2: sourcer — slskd REST client + candidate ranking

**Files:**
- Create: `trackstage/sourcer.py`
- Test: `tests/test_sourcer.py`

**Interfaces:**
- Consumes: env `SLSKD_URL`, `SLSKD_API_KEY`.
- Produces:
  - `Candidate` dataclass: `{username: str, filename: str, size: int, bitrate: int | None, extension: str, free_upload_slots: bool, queue_length: int}`
  - `rank_candidates(files: list[dict], fmt: str) -> list[Candidate]` — pure; filters by format policy, sorts best-first.
  - `class SlskdClient(base_url, api_key)` with `search(query, timeout=15) -> list[dict]` (flattened file dicts) and `download(candidate: Candidate, wait=True) -> Path` (returns final Inbox path).

- [ ] **Step 1: Write the failing test (ranking is pure — test it hard)**

```python
# tests/test_sourcer.py
from trackstage.sourcer import rank_candidates, Candidate


def _f(user, name, size, bitrate, slots=True, queue=0):
    return {
        "username": user, "filename": name, "size": size,
        "bitRate": bitrate, "freeUploadSlots": slots, "queueLength": queue,
    }


class TestRankCandidates:
    def test_flac_preferred_over_mp3(self):
        files = [
            _f("a", "track.mp3", 9_000_000, 320),
            _f("b", "track.flac", 40_000_000, None),
        ]
        ranked = rank_candidates(files, fmt="flac")
        assert ranked[0].extension == "flac"

    def test_mp3_below_320_dropped_in_flac_mode(self):
        files = [_f("a", "track.mp3", 5_000_000, 256)]
        assert rank_candidates(files, fmt="flac") == []

    def test_mp3_320_kept_when_no_flac(self):
        files = [_f("a", "track.mp3", 9_000_000, 320)]
        ranked = rank_candidates(files, fmt="flac")
        assert len(ranked) == 1
        assert ranked[0].extension == "mp3"
        assert ranked[0].bitrate == 320

    def test_free_slot_ranks_above_queued(self):
        files = [
            _f("busy", "track.flac", 40_000_000, None, slots=False, queue=10),
            _f("free", "track.flac", 40_000_000, None, slots=True, queue=0),
        ]
        ranked = rank_candidates(files, fmt="flac")
        assert ranked[0].username == "free"

    def test_any_mode_keeps_low_bitrate(self):
        files = [_f("a", "track.mp3", 5_000_000, 192)]
        ranked = rank_candidates(files, fmt="any")
        assert len(ranked) == 1

    def test_non_audio_dropped(self):
        files = [_f("a", "cover.jpg", 100_000, None)]
        assert rank_candidates(files, fmt="any") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sourcer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackstage.sourcer'`

- [ ] **Step 3: Implement `trackstage/sourcer.py`**

```python
# trackstage/sourcer.py
"""sourcer.py — Soulseek sourcing via slskd REST API.

Talks to the slskd daemon (localhost:5030) directly. Ranks search results
by format policy and availability, downloads to the DJ Inbox.
"""

import os
import time
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

AUDIO_EXTS = {"flac", "wav", "aiff", "aif", "mp3", "m4a"}
LOSSLESS_EXTS = {"flac", "wav", "aiff", "aif"}
MIN_MP3_BITRATE = 320


@dataclass
class Candidate:
    username: str
    filename: str
    size: int
    bitrate: int | None
    extension: str
    free_upload_slots: bool
    queue_length: int


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def rank_candidates(files: list[dict], fmt: str) -> list[Candidate]:
    """Filter by format policy and sort best-first.

    fmt='flac': lossless preferred; MP3 kept only if bitrate>=320.
    fmt='any':  any audio file kept.
    Sort key: lossless first, then higher bitrate, then free slot,
    then shorter queue, then larger file.
    """
    cands: list[Candidate] = []
    for f in files:
        ext = _ext(f.get("filename", ""))
        if ext not in AUDIO_EXTS:
            continue
        bitrate = f.get("bitRate")
        is_lossless = ext in LOSSLESS_EXTS
        if fmt == "flac" and not is_lossless:
            if bitrate is None or bitrate < MIN_MP3_BITRATE:
                continue
        cands.append(Candidate(
            username=f.get("username", ""),
            filename=f.get("filename", ""),
            size=int(f.get("size", 0)),
            bitrate=bitrate,
            extension=ext,
            free_upload_slots=bool(f.get("freeUploadSlots", False)),
            queue_length=int(f.get("queueLength", 0)),
        ))

    def sort_key(c: Candidate):
        return (
            0 if c.extension in LOSSLESS_EXTS else 1,   # lossless first
            -(c.bitrate or 0),                          # higher bitrate first
            0 if c.free_upload_slots else 1,            # free slot first
            c.queue_length,                             # shorter queue first
            -c.size,                                    # larger file first
        )

    return sorted(cands, key=sort_key)


class SlskdClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or os.environ.get("SLSKD_URL",
                         "http://localhost:5030")).rstrip("/")
        self.api_key = api_key or os.environ.get("SLSKD_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.api_key})

    def search(self, query: str, timeout: float = 30.0) -> list[dict]:
        """Start a search, poll to completion, return flattened file dicts."""
        r = self.session.post(f"{self.base_url}/api/v0/searches",
                              json={"searchText": query}, timeout=15)
        r.raise_for_status()
        search_id = r.json()["id"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.session.get(
                f"{self.base_url}/api/v0/searches/{search_id}", timeout=15).json()
            if s.get("state", "").startswith("Completed") or s.get("isComplete"):
                break
            time.sleep(1.0)

        resp = self.session.get(
            f"{self.base_url}/api/v0/searches/{search_id}/responses",
            timeout=15).json()
        files = []
        for r_ in resp:
            uname = r_.get("username", "")
            slots = r_.get("hasFreeUploadSlot", False)
            qlen = r_.get("queueLength", 0)
            for fdict in r_.get("files", []):
                files.append({
                    "username": uname,
                    "filename": fdict.get("filename", ""),
                    "size": fdict.get("size", 0),
                    "bitRate": fdict.get("bitRate"),
                    "freeUploadSlots": slots,
                    "queueLength": qlen,
                })
        return files

    def download(self, cand: Candidate, wait: bool = True,
                 timeout: float = 600.0) -> Path:
        """Enqueue a download; wait for completion; return the Inbox file path."""
        payload = [{"filename": cand.filename, "size": cand.size}]
        r = self.session.post(
            f"{self.base_url}/api/v0/transfers/downloads/{cand.username}",
            json=payload, timeout=15)
        r.raise_for_status()

        inbox = Path(os.environ["INBOX_PATH"])
        target_name = cand.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not wait:
            return inbox / target_name

        deadline = time.time() + timeout
        dest = inbox / target_name
        while time.time() < deadline:
            if dest.exists():
                return dest
            time.sleep(2.0)
        raise TimeoutError(f"Download did not complete: {target_name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sourcer.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add trackstage/sourcer.py tests/test_sourcer.py
git commit -m "feat(add): slskd REST sourcer with format-policy candidate ranking"
```

---

### Task 3: identifier — reusable Discogs lookup

**Files:**
- Create: `trackstage/identifier.py`
- Test: `tests/test_identifier.py`

**Interfaces:**
- Consumes: `pipeline.DiscogsClient`, `pipeline.extract_meta`, `pipeline.score_track`, `pipeline.score_by_tracklist` (all existing).
- Produces: `identify(client, artist, title, threshold=85, discogs_id=None) -> tuple[dict | None, int]` — returns `(meta, confidence)`. `meta` is the `extract_meta` dict (or `None` if nothing cleared `REVIEW_THRESHOLD`). Pure orchestration over the existing scoring functions; no I/O of its own beyond `client`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identifier.py
from trackstage.identifier import identify


class FakeClient:
    def __init__(self, search_results, releases):
        self._search = search_results
        self._releases = releases
        self.searched = None

    def search(self, q):
        self.searched = q
        return self._search

    def get_release(self, rid):
        return self._releases.get(rid)


def _rel(rid, title, tracklist=None):
    return {"id": rid, "title": title, "genres": ["Electronic"],
            "styles": ["Electro"], "labels": [{"name": "PIAS", "catno": "X1"}],
            "year": 2004, "tracklist": tracklist or []}


def test_forced_discogs_id_skips_search():
    c = FakeClient([], {337822: _rel(337822, "Soulwax - Any Minute Now")})
    meta, conf = identify(c, "Soulwax", "E Talking", discogs_id=337822)
    assert meta["discogs_id"] == "337822"
    assert c.searched is None
    assert conf == 100


def test_high_confidence_first_pass():
    results = [{"id": 1, "title": "Soulwax - E Talking",
                "label": ["PIAS"]}]
    c = FakeClient(results, {1: _rel(1, "Soulwax - E Talking")})
    meta, conf = identify(c, "Soulwax", "E Talking")
    assert meta is not None
    assert conf >= 85


def test_no_results_returns_none():
    c = FakeClient([], {})
    meta, conf = identify(c, "Nobody", "Nothing")
    assert meta is None
    assert conf == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_identifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackstage.identifier'`

- [ ] **Step 3: Implement `trackstage/identifier.py`**

```python
# trackstage/identifier.py
"""identifier.py — Discogs release identification for a single track.

Thin orchestration over pipeline's existing scoring + metadata extraction.
"""

from .pipeline import (
    extract_meta, score_track, score_by_tracklist,
    DEFAULT_THRESHOLD, REVIEW_THRESHOLD,
)


def identify(client, artist, title, threshold=DEFAULT_THRESHOLD,
            discogs_id=None):
    """Return (meta, confidence). meta is None if nothing clears REVIEW_THRESHOLD.

    Mirrors pipeline.process_file's two-pass logic without side effects.
    """
    if discogs_id:
        release = client.get_release(discogs_id)
        if release:
            return extract_meta(release), 100
        return None, 0

    results = client.search(f"{artist} {title}")
    if not results:
        return None, 0

    scores = [score_track(r, artist, title) for r in results]
    top = scores[0]

    if top >= threshold:
        release = client.get_release(results[0]["id"])
        return (extract_meta(release), top) if release else (None, 0)

    if top < REVIEW_THRESHOLD:
        return None, top

    # Pass 2: tracklist check on top 3
    best_release, best_score = None, 0
    for result in results[:3]:
        release = client.get_release(result["id"])
        if not release:
            continue
        tl = score_by_tracklist(release, title)
        if tl > best_score:
            best_score, best_release = tl, release

    if best_release and best_score >= REVIEW_THRESHOLD:
        return extract_meta(best_release), best_score
    return None, best_score
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_identifier.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add trackstage/identifier.py tests/test_identifier.py
git commit -m "feat(add): reusable Discogs identifier extracted from pipeline"
```

---

### Task 4: dbwriter pure helpers — field mapping + tag resolution

**Files:**
- Create: `trackstage/dbwriter.py` (pure-function section only this task)
- Test: `tests/test_dbwriter_pure.py`

**Interfaces:**
- Consumes: `rekordbox.ENERGY_TO_RATING`, `rekordbox.pick_color_id`, `rekordbox.compute_genre_tags`, `rekordbox.compute_vibe_tags`, `rekordbox.compute_sound_tags`, `rekordbox.compute_situation`, `tags.build_comment`, `rekordbox.to_rb_windows_path`.
- Produces:
  - `content_fields(meta: dict, analysis: dict) -> dict` — DjmdContent scalar fields to set: `{BPM, Rating, ColorID, ReleaseYear, Commnt, KeyName}`. `BPM` is int×100 or `None`; `Rating` int or `None`; `ColorID` str; `KeyName` camelot str or `""`.
  - `computed_tag_names(meta: dict, analysis: dict) -> set[str]` — union of genre/vibe/sound/situation tag names.
  - `resolve_tag_ids(existing_by_name: dict[str,str], names: set[str]) -> list[str]` — map names→IDs case-insensitively, skip unmatched, sorted for determinism.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dbwriter_pure.py
from trackstage.dbwriter import (
    content_fields, computed_tag_names, resolve_tag_ids,
)


class TestContentFields:
    def test_bpm_scaled_by_100(self):
        f = content_fields({}, {"bpm": "128.8"})
        assert f["BPM"] == 12880

    def test_bpm_missing_is_none(self):
        f = content_fields({}, {})
        assert f["BPM"] is None

    def test_energy_maps_to_rating(self):
        f = content_fields({}, {"energy": "6"})
        assert f["Rating"] == 3

    def test_mood_maps_to_color(self):
        f = content_fields({}, {"moods": ["party"]})
        assert f["ColorID"] == "4"

    def test_no_mood_color_zero(self):
        f = content_fields({}, {"moods": []})
        assert f["ColorID"] == "0"

    def test_year_from_meta(self):
        f = content_fields({"year": "2004"}, {})
        assert f["ReleaseYear"] == 2004

    def test_camelot_key_passed_through(self):
        f = content_fields({}, {"camelot": "6A"})
        assert f["KeyName"] == "6A"

    def test_comment_built_from_meta(self):
        f = content_fields(
            {"styles": "Electro", "catno": "X1"},
            {"energy": "5"})
        assert "Electro" in f["Commnt"]


class TestComputedTagNames:
    def test_genre_and_vibe_union(self):
        meta = {"styles": "House"}
        analysis = {"energy": "6", "vibes": ["driving"], "moods": []}
        names = computed_tag_names(meta, analysis)
        assert "House" in names
        assert "Driving" in names

    def test_situation_included(self):
        names = computed_tag_names({}, {"energy": "8"})
        assert "Peak" in names  # rekordbox.compute_situation("8")


class TestResolveTagIds:
    def test_case_insensitive_match(self):
        existing = {"house": "111", "driving": "222"}
        ids = resolve_tag_ids(existing, {"House", "Driving"})
        assert set(ids) == {"111", "222"}

    def test_unmatched_skipped(self):
        existing = {"house": "111"}
        ids = resolve_tag_ids(existing, {"House", "Peak"})
        assert ids == ["111"]

    def test_empty(self):
        assert resolve_tag_ids({}, set()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dbwriter_pure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackstage.dbwriter'`

- [ ] **Step 3: Implement the pure section of `trackstage/dbwriter.py`**

```python
# trackstage/dbwriter.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dbwriter_pure.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add trackstage/dbwriter.py tests/test_dbwriter_pure.py
git commit -m "feat(add): dbwriter pure field-mapping + tag-name resolution"
```

---

### Task 5: dbwriter DB shell — RekordboxWriter over a db handle

**Files:**
- Modify: `trackstage/dbwriter.py` (append `RekordboxWriter`)
- Test: `tests/test_dbwriter_write.py`

**Interfaces:**
- Consumes: `content_fields`, `computed_tag_names`, `resolve_tag_ids` (Task 4); a duck-typed `db` handle exposing pyrekordbox's `add_content`, `add_artist`, `add_album`, `add_genre`, `add_label`, `get_artist`, `get_album`, `get_genre`, `get_label`, `get_key`, `create_playlist_folder`, `create_playlist`, `get_playlist`, `add_to_playlist`, `get_playlist_songs`, and `.session.execute(text_stmt, params)`.
- Produces:
  - `class RekordboxWriter(db)` with:
    - `existing_tag_map() -> dict[str,str]` — `{tag_name: MyTagID}` from `djmdMyTag` (child tags only, `Attribute=0`).
    - `resolve_or_create(kind, name) -> object` — kind in `{"artist","genre","label"}`; returns the row (existing or newly added).
    - `resolve_album(name, artist_row) -> object`
    - `resolve_key_id(camelot) -> str | None` — existing `djmdKey` ID matching name, else `None`.
    - `ensure_playlist(folder_name, child_name) -> object` — ensure `folder_name/child_name` playlist exists (create folder + child as needed), return child row.
    - `assign_my_tags(content_id, tag_ids) -> int` — raw-SQL insert into `djmdSongMyTag`, idempotent, returns count added.
    - `add_track(wsl_path, win_path, filename, title, artist, meta, analysis) -> str` — full per-track write; returns Content ID. Dup by `FolderPath` → returns existing ID after ensuring playlists/tags only.

- [ ] **Step 1: Write the failing test (FakeDB captures ORM calls)**

```python
# tests/test_dbwriter_write.py
from types import SimpleNamespace
from trackstage.dbwriter import RekordboxWriter


class FakeRow(SimpleNamespace):
    pass


class FakeSession:
    def __init__(self, rows):
        self._rows = rows          # list of dicts for SELECT results
        self.executed = []         # (sql, params) for INSERT/UPDATE

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        if "FROM djmdMyTag" in sql:
            return iter([(r["Name"], r["ID"]) for r in self._rows["my_tags"]])
        if "FROM djmdKey" in sql:
            return iter([(r["ID"], r["Name"]) for r in self._rows["keys"]])
        if "FROM djmdSongMyTag" in sql:
            return iter(self._rows.get("song_tags", []))
        return iter([])


class FakeDB:
    """Duck-typed stand-in for pyrekordbox Rekordbox6Database."""
    def __init__(self, rows):
        self.session = FakeSession(rows)
        self.added_content = []
        self.playlists = {}        # name -> row
        self.playlist_adds = []    # (playlist_id, content_id)
        self.artists = {}
        self._id = 1000

    def _next(self):
        self._id += 1
        return str(self._id)

    def add_content(self, path, **kw):
        row = FakeRow(ID=self._next(), FolderPath=str(path),
                      FileNameL=None, **kw)
        self.added_content.append(row)
        return row

    def add_artist(self, name, search_str=None):
        r = FakeRow(ID=self._next(), Name=name)
        self.artists[name] = r
        return r

    def add_album(self, name, artist=None, **kw):
        return FakeRow(ID=self._next(), Name=name)

    def add_genre(self, name):
        return FakeRow(ID=self._next(), Name=name)

    def add_label(self, name):
        return FakeRow(ID=self._next(), Name=name)

    def get_artist(self, **kw):
        r = self.artists.get(kw.get("Name"))
        return SimpleNamespace(first=lambda: r)

    def get_album(self, **kw):
        return SimpleNamespace(first=lambda: None)

    def get_genre(self, **kw):
        return SimpleNamespace(first=lambda: None)

    def get_label(self, **kw):
        return SimpleNamespace(first=lambda: None)

    def get_playlist(self, **kw):
        return SimpleNamespace(first=lambda: self.playlists.get(kw.get("Name")))

    def create_playlist_folder(self, name, parent=None, **kw):
        r = FakeRow(ID=self._next(), Name=name)
        self.playlists[name] = r
        return r

    def create_playlist(self, name, parent=None, **kw):
        r = FakeRow(ID=self._next(), Name=name)
        self.playlists[name] = r
        return r

    def get_playlist_songs(self, playlist):
        return []

    def add_to_playlist(self, playlist, content, track_no=None):
        self.playlist_adds.append((playlist.ID, content.ID))
        return FakeRow(ID=self._next())


def _rows():
    return {
        "my_tags": [{"Name": "House", "ID": "H1"},
                    {"Name": "Driving", "ID": "D1"}],
        "keys": [{"Name": "6A", "ID": "K6A"}],
        "song_tags": [],
    }


def test_add_track_creates_content_with_windows_path():
    db = FakeDB(_rows())
    w = RekordboxWriter(db)
    cid = w.add_track(
        wsl_path="/mnt/c/Music/Library/x/Soulwax - E Talking.flac",
        win_path="C:/Music/Library/x/Soulwax - E Talking.flac",
        filename="Soulwax - E Talking.flac",
        title="E Talking", artist="Soulwax",
        meta={"styles": "House", "label": "PIAS", "album": "Any Minute Now",
              "year": "2004", "genre": "Electronic", "catno": "X1"},
        analysis={"bpm": "128.8", "energy": "6", "camelot": "6A",
                  "moods": ["party"], "vibes": ["driving"]},
    )
    assert cid == db.added_content[0].ID
    assert db.added_content[0].FolderPath == \
        "C:/Music/Library/x/Soulwax - E Talking.flac"
    assert db.added_content[0].BPM == 12880
    assert db.added_content[0].Rating == 3


def test_my_tags_resolved_and_inserted():
    db = FakeDB(_rows())
    w = RekordboxWriter(db)
    w.add_track(
        wsl_path="/mnt/c/Music/x/a.flac", win_path="C:/Music/x/a.flac",
        filename="a.flac", title="t", artist="a",
        meta={"styles": "House"},
        analysis={"energy": "6", "vibes": ["driving"], "moods": []},
    )
    inserts = [e for e in db.session.executed
               if "INSERT INTO djmdSongMyTag" in e[0]]
    inserted_tag_ids = {e[1]["tag"] for e in inserts}
    assert inserted_tag_ids == {"H1", "D1"}


def test_playlists_ensured_and_joined():
    db = FakeDB(_rows())
    w = RekordboxWriter(db)
    w.add_track(
        wsl_path="/mnt/c/Music/x/a.flac", win_path="C:/Music/x/a.flac",
        filename="a.flac", title="t", artist="a",
        meta={"styles": "House", "label": "PIAS"},
        analysis={"energy": "6", "moods": []},
    )
    assert "House" in db.playlists
    assert "PIAS" in db.playlists
    assert len(db.playlist_adds) == 2


def test_duplicate_path_skips_content_insert():
    db = FakeDB(_rows())

    def raise_dup(path, **kw):
        raise ValueError(f"Track with path '{path}' already exists in database")
    db.add_content = raise_dup
    # Pre-seed the existing-content lookup
    db.session._rows["existing"] = [("EXIST1",)]
    w = RekordboxWriter(db)
    cid = w.add_track(
        wsl_path="/mnt/c/Music/x/a.flac", win_path="C:/Music/x/a.flac",
        filename="a.flac", title="t", artist="a",
        meta={"styles": "House"}, analysis={"energy": "6", "moods": []},
    )
    assert cid == "EXIST1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dbwriter_write.py -v`
Expected: FAIL — `ImportError: cannot import name 'RekordboxWriter'`

- [ ] **Step 3: Append `RekordboxWriter` to `trackstage/dbwriter.py`**

```python
# ── DB write shell ───────────────────────────────────────────────────────────

import uuid
from datetime import datetime, timezone

from sqlalchemy import text


def _now_rb() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 +00:00")


class RekordboxWriter:
    """Wraps a pyrekordbox Rekordbox6Database handle for track writes."""

    def __init__(self, db):
        self.db = db

    # -- lookups ---------------------------------------------------------------

    def existing_tag_map(self) -> dict:
        """{tag_name: MyTagID} for child My Tags (Attribute=0)."""
        rows = self.db.session.execute(text(
            "SELECT Name, ID FROM djmdMyTag WHERE Attribute=0 "
            "AND rb_local_deleted=0"))
        return {name: tid for name, tid in rows}

    def _existing_content_id(self, win_path: str):
        rows = list(self.db.session.execute(text(
            "SELECT ID FROM djmdContent WHERE FolderPath=:p"),
            {"p": win_path}))
        return rows[0][0] if rows else None

    def resolve_key_id(self, camelot: str):
        if not camelot:
            return None
        rows = self.db.session.execute(text("SELECT ID, ScaleName FROM djmdKey")) \
            if False else self.db.session.execute(text(
                "SELECT ID, Name FROM djmdKey"))
        for kid, name in rows:
            if str(name).strip().lower() == camelot.strip().lower():
                return kid
        return None

    # -- resolve-or-create related rows ---------------------------------------

    def resolve_or_create(self, kind: str, name: str):
        if not name:
            return None
        getter = {"artist": self.db.get_artist, "genre": self.db.get_genre,
                  "label": self.db.get_label}[kind]
        adder = {"artist": self.db.add_artist, "genre": self.db.add_genre,
                 "label": self.db.add_label}[kind]
        existing = getter(Name=name).first()
        return existing if existing is not None else adder(name)

    def resolve_album(self, name: str, artist_row):
        if not name:
            return None
        existing = self.db.get_album(Name=name).first()
        if existing is not None:
            return existing
        return self.db.add_album(name, artist=artist_row)

    # -- playlists -------------------------------------------------------------

    def ensure_playlist(self, folder_name: str, child_name: str):
        folder = self.db.get_playlist(Name=folder_name).first()
        if folder is None:
            folder = self.db.create_playlist_folder(folder_name)
        child = self.db.get_playlist(Name=child_name).first()
        if child is None:
            child = self.db.create_playlist(child_name, parent=folder)
        return child

    def _already_in_playlist(self, playlist, content_id) -> bool:
        for song in self.db.get_playlist_songs(playlist):
            if str(getattr(song, "ContentID", "")) == str(content_id):
                return True
        return False

    # -- My Tags ---------------------------------------------------------------

    def assign_my_tags(self, content_id, tag_ids) -> int:
        existing = {(c, t) for c, t in self.db.session.execute(text(
            "SELECT ContentID, MyTagID FROM djmdSongMyTag "
            "WHERE rb_local_deleted=0"))}
        max_usn = list(self.db.session.execute(text(
            "SELECT MAX(rb_local_usn) FROM djmdSongMyTag")))
        usn = (max_usn[0][0] if max_usn and max_usn[0][0] else 0)
        added = 0
        for tid in tag_ids:
            if (content_id, tid) in existing:
                continue
            usn += 1
            self.db.session.execute(text("""
                INSERT INTO djmdSongMyTag
                (ID, MyTagID, ContentID, UUID,
                 rb_data_status, rb_local_data_status,
                 rb_local_deleted, rb_local_synced,
                 rb_local_usn, created_at, updated_at)
                VALUES (:id, :tag, :content, :uuid,
                        0, 0, 0, 0, :usn, :now, :now)
            """), {"id": str(uuid.uuid4()), "tag": tid, "content": content_id,
                   "uuid": str(uuid.uuid4()), "usn": usn, "now": _now_rb()})
            added += 1
        return added

    # -- orchestration ---------------------------------------------------------

    def add_track(self, wsl_path, win_path, filename, title, artist,
                  meta, analysis) -> str:
        fields = content_fields(meta, analysis)

        artist_row = self.resolve_or_create("artist", artist)
        album_row = self.resolve_album(meta.get("album", ""), artist_row)
        genre_row = self.resolve_or_create("genre", meta.get("genre", ""))
        label_row = self.resolve_or_create("label", meta.get("label", ""))
        key_id = self.resolve_key_id(fields["KeyName"])

        try:
            content = self.db.add_content(wsl_path, Title=title)
            content.FolderPath = win_path
            content.FileNameL = filename
            if artist_row is not None:
                content.ArtistID = artist_row.ID
            if album_row is not None:
                content.AlbumID = album_row.ID
            if genre_row is not None:
                content.GenreID = genre_row.ID
            if label_row is not None:
                content.LabelID = label_row.ID
            if key_id is not None:
                content.KeyID = key_id
            if fields["BPM"] is not None:
                content.BPM = fields["BPM"]
            if fields["Rating"] is not None:
                content.Rating = fields["Rating"]
            if fields["ColorID"] != "0":
                content.ColorID = fields["ColorID"]
            if fields["ReleaseYear"] is not None:
                content.ReleaseYear = fields["ReleaseYear"]
            if fields["Commnt"]:
                content.Commnt = fields["Commnt"]
            content_id = content.ID
        except ValueError:
            existing = self._existing_content_id(win_path)
            if existing is None:
                raise
            content_id = existing
            log.info("  — track already in DB; ensuring tags + playlists only")

        # My Tags
        tag_ids = resolve_tag_ids(self.existing_tag_map(),
                                  computed_tag_names(meta, analysis))
        self.assign_my_tags(content_id, tag_ids)

        # Playlists: Styles/<style> and Labels/<label>
        styles = [s.strip() for s in meta.get("styles", "").split(",") if s.strip()]
        for style in styles:
            pl = self.ensure_playlist("Styles", style)
            if not self._already_in_playlist(pl, content_id):
                self.db.add_to_playlist(pl, content)
        if meta.get("label"):
            pl = self.ensure_playlist("Labels", meta["label"].strip())
            if not self._already_in_playlist(pl, content_id):
                self.db.add_to_playlist(pl, content)

        return content_id
```

Note: in the duplicate branch, `content` is undefined for the playlist-join calls. Guard the join loops so they only run when a fresh `content` object exists:

```python
        # Playlists: Styles/<style> and Labels/<label>
        fresh = locals().get("content") if "content" in dir() else None
        styles = [s.strip() for s in meta.get("styles", "").split(",") if s.strip()]
        for style in styles:
            pl = self.ensure_playlist("Styles", style)
            if fresh is not None and not self._already_in_playlist(pl, content_id):
                self.db.add_to_playlist(pl, fresh)
        if meta.get("label"):
            pl = self.ensure_playlist("Labels", meta["label"].strip())
            if fresh is not None and not self._already_in_playlist(pl, content_id):
                self.db.add_to_playlist(pl, fresh)
```

Replace the earlier playlist block with this guarded version. (In the duplicate path, playlist membership for an already-imported track is left to the standard flow — the track already exists in Rekordbox with its playlists; re-joining requires fetching the content row, which is out of scope for v1's idempotency guarantee of "no crash, no dupes.")

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dbwriter_write.py -v`
Expected: PASS (4 tests). If `test_duplicate_path_skips_content_insert` needs the `existing` lookup, confirm `_existing_content_id` reads via `FakeSession` — extend `FakeSession.execute` to return `self._rows["existing"]` when `"FROM djmdContent"` is in the SQL:

```python
        if "FROM djmdContent" in sql:
            return iter(self._rows.get("existing", []))
```

Add that branch to the test's `FakeSession` before running.

- [ ] **Step 5: Commit**

```bash
git add trackstage/dbwriter.py tests/test_dbwriter_write.py
git commit -m "feat(add): RekordboxWriter — Content/tags/playlists in one txn"
```

---

### Task 6: guards — rekordbox-running check + backup/restore

**Files:**
- Modify: `trackstage/dbwriter.py` (append module-level helpers)
- Test: `tests/test_dbwriter_guards.py`

**Interfaces:**
- Produces:
  - `rekordbox_running() -> bool` — runs `tasklist.exe /FI "IMAGENAME eq rekordbox.exe"`; True if `rekordbox.exe` appears in output. On any subprocess error, returns `False` (best-effort; commit will still fail-safe).
  - `backup_db(db_path: Path) -> Path` — copies `db_path` to `db_path.with_suffix(db_path.suffix + f".bak-{ts}")`, returns backup path.
  - `restore_db(backup_path: Path, db_path: Path) -> None` — copies backup back over db_path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dbwriter_guards.py
import subprocess
from pathlib import Path

from trackstage import dbwriter


def test_rekordbox_running_true(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a, 0,
            stdout="rekordbox.exe   1234 Console", stderr=""))
    assert dbwriter.rekordbox_running() is True


def test_rekordbox_running_false(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a, 0,
            stdout="INFO: No tasks are running", stderr=""))
    assert dbwriter.rekordbox_running() is False


def test_rekordbox_running_subprocess_error_returns_false(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("tasklist.exe not found")
    monkeypatch.setattr(subprocess, "run", boom)
    assert dbwriter.rekordbox_running() is False


def test_backup_and_restore(tmp_path):
    db = tmp_path / "master.db"
    db.write_bytes(b"ORIGINAL")
    bak = dbwriter.backup_db(db)
    assert bak.exists()
    assert bak.read_bytes() == b"ORIGINAL"
    db.write_bytes(b"CORRUPTED")
    dbwriter.restore_db(bak, db)
    assert db.read_bytes() == b"ORIGINAL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dbwriter_guards.py -v`
Expected: FAIL — `AttributeError: module 'trackstage.dbwriter' has no attribute 'rekordbox_running'`

- [ ] **Step 3: Append guards to `trackstage/dbwriter.py`**

```python
# ── Guards ───────────────────────────────────────────────────────────────────

import shutil
import subprocess
from pathlib import Path


def rekordbox_running() -> bool:
    """True if rekordbox.exe is running (checked via Windows tasklist from WSL)."""
    try:
        r = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq rekordbox.exe"],
            capture_output=True, text=True, timeout=15)
        return "rekordbox.exe" in r.stdout.lower()
    except Exception:
        return False


def backup_db(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_suffix(db_path.suffix + f".bak-{ts}")
    shutil.copy2(db_path, backup)
    return backup


def restore_db(backup_path: Path, db_path: Path) -> None:
    shutil.copy2(backup_path, db_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dbwriter_guards.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add trackstage/dbwriter.py tests/test_dbwriter_guards.py
git commit -m "feat(add): rekordbox-running guard + master.db backup/restore"
```

---

### Task 7: engine orchestration — wire the full add pipeline

**Files:**
- Modify: `trackstage/add.py` (implement `run_add`)
- Test: `tests/test_add_engine.py`

**Interfaces:**
- Consumes: `sourcer.SlskdClient`, `sourcer.rank_candidates`, `identifier.identify`, `analyzer.analyze_track`, `cache.AnalysisCache`, `pipeline.DiscogsClient`, `pipeline.build_dest`, `dbwriter.RekordboxWriter`, `dbwriter.rekordbox_running`, `dbwriter.backup_db`, `dbwriter.restore_db`, `rekordbox.to_rb_windows_path`, `tags.read_tags`, `pyrekordbox.Rekordbox6Database`.
- Produces: `run_add(args) -> int`. Emits a result dict (printed as JSON when `--json`). Exit codes: `0` success/dry-run, `2` needs disambiguation (candidates printed), `3` refused (Rekordbox running), `1` other failure.

- [ ] **Step 1: Write the failing test (all externals mocked)**

```python
# tests/test_add_engine.py
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trackstage import add
from trackstage.sourcer import Candidate


@pytest.fixture
def wired(monkeypatch, tmp_path):
    # Environment
    inbox = tmp_path / "inbox"; inbox.mkdir()
    library = tmp_path / "library"; library.mkdir()
    monkeypatch.setenv("INBOX_PATH", str(inbox))
    monkeypatch.setenv("LIBRARY_PATH", str(library))
    monkeypatch.setenv("DISCOGS_TOKEN", "tok")
    monkeypatch.setenv("SLSKD_API_KEY", "key")

    # Sourcer: one FLAC candidate; download writes a fake file into inbox
    cand = Candidate("peer", "Soulwax - E Talking.flac", 40_000_000,
                     None, "flac", True, 0)
    dl_path = inbox / "Soulwax - E Talking.flac"

    class FakeSlskd:
        def __init__(self, *a, **k): pass
        def search(self, q, timeout=30.0):
            return [{"username": "peer",
                     "filename": "Soulwax - E Talking.flac",
                     "size": 40_000_000, "bitRate": None,
                     "freeUploadSlots": True, "queueLength": 0}]
        def download(self, c, wait=True, timeout=600.0):
            dl_path.write_bytes(b"FLACDATA")
            return dl_path

    monkeypatch.setattr(add, "SlskdClient", FakeSlskd)

    # Discogs
    class FakeDiscogs:
        def __init__(self, *a, **k): pass
        def verify(self): return True
    monkeypatch.setattr(add, "DiscogsClient", FakeDiscogs)
    monkeypatch.setattr(add, "identify", lambda *a, **k: (
        {"styles": "Electro", "label": "PIAS", "album": "Any Minute Now",
         "year": "2004", "genre": "Electronic", "catno": "X1",
         "release_title": "Any Minute Now"}, 92))

    # Analyzer (+ cache)
    monkeypatch.setattr(add, "analyze_track", lambda fp, existing_key="": {
        "bpm": "128.8", "camelot": "6A", "energy": "5",
        "danceability": "6", "moods": ["party"], "vibes": ["driving"],
        "vocal": "instrumental", "cues": [], "loudness": None})

    class FakeCache:
        def __init__(self, *a, **k): pass
        def get(self, fp): return None
        def put(self, fp, r): pass
        def close(self): pass
    monkeypatch.setattr(add, "AnalysisCache", FakeCache)

    # dbwriter guards + writer + DB
    monkeypatch.setattr(add, "rekordbox_running", lambda: False)
    monkeypatch.setattr(add, "backup_db", lambda p: Path(str(p) + ".bak"))
    monkeypatch.setattr(add, "restore_db", lambda b, p: None)

    written = {}

    class FakeWriter:
        def __init__(self, db): pass
        def add_track(self, **kw):
            written.update(kw)
            return "CID123"
    monkeypatch.setattr(add, "RekordboxWriter", FakeWriter)

    class FakeDB:
        def __init__(self, *a, **k): pass
        def commit(self): written["committed"] = True
    monkeypatch.setattr(add, "Rekordbox6Database", FakeDB)

    return SimpleNamespace(inbox=inbox, library=library, written=written,
                           dl_path=dl_path)


def test_happy_path_writes_and_commits(wired, capsys):
    args = add.build_parser().parse_args(["E Talking by Soulwax", "--yes"])
    rc = add.run_add(args)
    assert rc == 0
    assert wired.written["committed"] is True
    assert wired.written["title"]  # add_track received metadata
    assert wired.written["win_path"].startswith("C:/")


def test_dry_run_no_commit(wired):
    args = add.build_parser().parse_args(["E Talking by Soulwax",
                                          "--yes", "--dry-run"])
    rc = add.run_add(args)
    assert rc == 0
    assert "committed" not in wired.written


def test_refuses_when_rekordbox_running(wired, monkeypatch):
    monkeypatch.setattr(add, "rekordbox_running", lambda: True)
    args = add.build_parser().parse_args(["E Talking by Soulwax", "--yes"])
    rc = add.run_add(args)
    assert rc == 3
    assert "committed" not in wired.written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_add_engine.py -v`
Expected: FAIL — `NotImplementedError` from the stub `run_add`.

- [ ] **Step 3: Implement `run_add` in `trackstage/add.py`**

Replace the imports block and `run_add` stub with:

```python
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from .sourcer import SlskdClient, rank_candidates
from .identifier import identify
from .analyzer import analyze_track
from .cache import AnalysisCache
from .pipeline import DiscogsClient, build_dest
from .tags import read_tags, write_tags
from .rekordbox import to_rb_windows_path
from .dbwriter import (
    RekordboxWriter, rekordbox_running, backup_db, restore_db,
)

try:
    from pyrekordbox import Rekordbox6Database
except ImportError:
    Rekordbox6Database = None

DEFAULT_DB = "/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db"


def _emit(payload: dict, as_json: bool):
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        for k, v in payload.items():
            print(f"  {k}: {v}")


def run_add(args) -> int:
    # 1. Source
    client = SlskdClient()
    files = client.search(args.query)
    candidates = rank_candidates(files, fmt=args.format)
    if not candidates:
        _emit({"status": "no_source",
               "message": f"No {args.format} source >=320kbps for: {args.query}"},
              args.json)
        return 1

    # Disambiguation: multiple distinct top candidates and not --yes/--pick
    if args.pick is not None:
        chosen = candidates[args.pick - 1]
    elif args.yes or len(candidates) == 1:
        chosen = candidates[0]
    else:
        shortlist = [{"n": i + 1, "user": c.username, "file": c.filename,
                      "ext": c.extension, "bitrate": c.bitrate,
                      "size_mb": round(c.size / 1_048_576, 1)}
                     for i, c in enumerate(candidates[:5])]
        _emit({"status": "choose", "candidates": shortlist,
               "message": "Re-run with --pick N"}, args.json)
        return 2

    if args.dry_run:
        _emit({"status": "dry_run", "would_download": chosen.filename,
               "from": chosen.username}, args.json)
        return 0

    # 2. Download
    src = client.download(chosen)

    # 3. Identify
    tags = read_tags(src)
    artist = tags["artist"] or args.query.split(" by ")[-1].strip()
    title = tags["title"] or args.query.split(" by ")[0].strip()
    dclient = DiscogsClient(os.environ.get("DISCOGS_TOKEN", ""))
    meta, conf = identify(dclient, artist, title, discogs_id=args.discogs_id)
    if meta is None:
        meta = {"release_title": title, "album": title, "genre": "",
                "styles": "", "label": "", "catno": "", "year": "",
                "discogs_id": ""}

    # 4. Analyze (cached)
    cache = AnalysisCache()
    analysis = cache.get(src) or analyze_track(src, existing_key=meta.get("initial_key", ""))
    cache.put(src, analysis)
    cache.close()

    # Merge analysis into meta for tag/file writes
    meta["bpm"] = analysis.get("bpm", "")
    meta["initial_key"] = analysis.get("camelot", "")
    meta["energy"] = analysis.get("energy", "")
    meta["danceability"] = analysis.get("danceability", "")
    meta["vibes"] = ", ".join(analysis.get("vibes", []))
    meta["vocal"] = analysis.get("vocal", "")

    # 5. Organize (move to Library, write file tags)
    library = Path(os.environ["LIBRARY_PATH"])
    dest_dir, dest_path = build_dest(artist, title, meta, library, src.suffix)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest_path))
    write_tags(dest_path, meta, dry_run=False)

    # 6. Write to DB
    if rekordbox_running():
        _emit({"status": "refused",
               "message": "Rekordbox is running. Close it and re-run."},
              args.json)
        return 3

    db_path = Path(os.environ.get("REKORDBOX_DB", DEFAULT_DB))
    backup = backup_db(db_path)
    win_path = to_rb_windows_path(dest_path)
    db = Rekordbox6Database(path=str(db_path))
    try:
        writer = RekordboxWriter(db)
        content_id = writer.add_track(
            wsl_path=str(dest_path), win_path=win_path,
            filename=dest_path.name, title=title, artist=artist,
            meta=meta, analysis=analysis)
        db.commit()
    except Exception as e:
        restore_db(backup, db_path)
        _emit({"status": "error", "message": f"DB write failed: {e}. "
               f"master.db restored from backup."}, args.json)
        return 1

    _emit({"status": "ok", "content_id": content_id,
           "title": title, "artist": artist,
           "bpm": analysis.get("bpm"), "key": analysis.get("camelot"),
           "energy": analysis.get("energy"),
           "dest": str(dest_path)}, args.json)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_add_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all green (new + existing tests)

- [ ] **Step 6: Commit**

```bash
git add trackstage/add.py tests/test_add_engine.py
git commit -m "feat(add): wire full source->identify->analyze->organize->DB engine"
```

---

### Task 8: rewrite the `dj-library` skill

**Files:**
- Modify: `skill/trackstage/SKILL.md`
- Test: manual (documented below; no automated test — it's a prompt doc)

**Interfaces:**
- Consumes: `trackstage add "<query>"` engine (JSON mode), exit codes from Task 7.
- Produces: conversational behavior — "add <song>" → run engine → on exit 2 show shortlist and re-run with `--pick`, on exit 3 tell user to close Rekordbox, on exit 0 report the landed track.

- [ ] **Step 1: Read the current skill to preserve voice/structure**

Run: `cat skill/trackstage/SKILL.md`
Note the frontmatter format and any project conventions to keep.

- [ ] **Step 2: Rewrite `skill/trackstage/SKILL.md`**

Replace the body with content teaching this flow (keep existing frontmatter `name`/`description` keys; update description to mention direct-to-DB add):

```markdown
---
name: dj-library
description: Add tracks to the DJ library — source from Soulseek, tag via Discogs, analyze with Essentia, and write straight into Rekordbox. Use when the user says "add <song>".
---

# DJ Library — add a track

When the user says **"add &lt;song&gt;"** (optionally "by &lt;artist&gt;"), run the
one-shot engine and report what landed. The engine sources from Soulseek,
identifies on Discogs, analyzes with Essentia, files into the Library, and
writes directly into Rekordbox's master.db — no XML, no manual import.

## Run it

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

## Rules

- **Rekordbox must be closed** before the DB write. If `refused`, do not retry
  until the user confirms it's quit.
- Prefer FLAC. Only pass `--format any` when the user accepts lossy or nothing
  ≥320 exists.
- One track per invocation. For several, run once each.
- Never use the rekordbox-database MCP `import_track` for this — it dupes rows
  and skips analysis. The engine is the only ingestion path.
```

- [ ] **Step 3: Manual smoke test (dry-run, no download)**

Run: `cd ~/dev/trackstage && .venv/bin/python -m trackstage add "test query" --dry-run --yes --json`
Expected: JSON with `status` of `no_source` or `dry_run` (depending on live slskd results) — confirms the skill's documented command shape works end to end.

- [ ] **Step 4: Commit**

```bash
git add skill/trackstage/SKILL.md
git commit -m "docs(add): rewrite dj-library skill to drive direct-to-DB add"
```

---

### Task 9: retire XML backend — redirect batch flow through dbwriter, delete xml.py + sync_rekordbox.py

**Files:**
- Modify: `trackstage/pipeline.py` (replace `append_tracks_to_xml` call + XML CLI + import guidance)
- Delete: `trackstage/xml.py`, `scripts/sync_rekordbox.py`, `scripts/backfill_xml.py`, `tests/test_xml.py`, `tests/test_xml_update.py`
- Test: `tests/test_pipeline_dbwrite.py` (new — batch path routes to dbwriter)

**Interfaces:**
- Consumes: `dbwriter.RekordboxWriter`, `dbwriter.rekordbox_running`, `dbwriter.backup_db`, `dbwriter.restore_db`, `rekordbox.to_rb_windows_path`, `pyrekordbox.Rekordbox6Database`.
- Produces: `pipeline.write_results_to_db(results: list[dict], dry_run: bool) -> dict` — writes each processed track via dbwriter in one transaction; returns `{"written": int, "skipped": int}`. Replaces `append_tracks_to_xml`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_dbwrite.py
from pathlib import Path
from types import SimpleNamespace

import pytest

from trackstage import pipeline


def test_write_results_to_db_routes_each_track(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "rekordbox_running", lambda: False)
    monkeypatch.setattr(pipeline, "backup_db", lambda p: Path(str(p) + ".bak"))
    monkeypatch.setattr(pipeline, "restore_db", lambda b, p: None)

    calls = []

    class FakeWriter:
        def __init__(self, db): pass
        def add_track(self, **kw):
            calls.append(kw["title"])
            return "CID"

    class FakeDB:
        def __init__(self, *a, **k): pass
        def commit(self): calls.append("commit")

    monkeypatch.setattr(pipeline, "RekordboxWriter", FakeWriter)
    monkeypatch.setattr(pipeline, "Rekordbox6Database", FakeDB)
    monkeypatch.setenv("REKORDBOX_DB", str(tmp_path / "master.db"))
    (tmp_path / "master.db").write_bytes(b"DB")

    f = tmp_path / "Artist - T.flac"; f.write_bytes(b"x")
    results = [{"file_path": f, "artist": "Artist", "title": "T",
                "meta": {"styles": "House", "bpm": "120", "energy": "5",
                         "camelot": "8A", "vibes": "", "vocal": ""}}]
    out = pipeline.write_results_to_db(results, dry_run=False)
    assert out["written"] == 1
    assert "T" in calls
    assert "commit" in calls


def test_write_results_to_db_dry_run_no_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "rekordbox_running", lambda: False)
    out = pipeline.write_results_to_db([], dry_run=True)
    assert out == {"written": 0, "skipped": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline_dbwrite.py -v`
Expected: FAIL — `AttributeError: module 'trackstage.pipeline' has no attribute 'write_results_to_db'`

- [ ] **Step 3: Add `write_results_to_db` + remove XML imports in `pipeline.py`**

Delete the `from .xml import (...)` block (lines ~47-50). Replace with:

```python
from .dbwriter import (
    RekordboxWriter, rekordbox_running, backup_db, restore_db,
)
from .rekordbox import to_rb_windows_path

try:
    from pyrekordbox import Rekordbox6Database
except ImportError:
    Rekordbox6Database = None

DEFAULT_DB = "/mnt/c/Users/Kaitlyn/AppData/Roaming/Pioneer/rekordbox/master.db"
```

Add this function (place it where `append_tracks_to_xml` was, ~line 390) and delete `append_tracks_to_xml` entirely:

```python
def write_results_to_db(results: list, dry_run: bool = False) -> dict:
    """Write each processed track directly to Rekordbox master.db via dbwriter."""
    if dry_run or not results:
        return {"written": 0, "skipped": 0}

    if rekordbox_running():
        log.error("  ✗  Rekordbox is running — close it and re-run. Nothing written.")
        return {"written": 0, "skipped": len(results)}

    db_path = Path(os.environ.get("REKORDBOX_DB", DEFAULT_DB))
    backup = backup_db(db_path)
    db = Rekordbox6Database(path=str(db_path))
    written = 0
    try:
        writer = RekordboxWriter(db)
        for r in results:
            fp = r["file_path"]
            meta = r["meta"]
            analysis = {
                "bpm": meta.get("bpm", ""), "camelot": meta.get("initial_key", ""),
                "energy": meta.get("energy", ""),
                "danceability": meta.get("danceability", ""),
                "vibes": [v.strip() for v in meta.get("vibes", "").split(",") if v.strip()],
                "vocal": meta.get("vocal", ""), "moods": meta.get("_moods", []),
            }
            writer.add_track(
                wsl_path=str(fp), win_path=to_rb_windows_path(fp),
                filename=fp.name, title=r["title"], artist=r["artist"],
                meta=meta, analysis=analysis)
            written += 1
        db.commit()
    except Exception as e:
        restore_db(backup, db_path)
        log.error(f"  ✗  DB write failed: {e}. master.db restored from backup.")
        return {"written": 0, "skipped": len(results)}
    return {"written": written, "skipped": 0}
```

- [ ] **Step 4: Redirect `run_pipeline` + CLI to the new writer**

In `run_pipeline` (replace the "Update XML" block ~lines 1105-1116):

```python
    # Write to Rekordbox DB
    if not as_json:
        print(f"\n  ── Rekordbox database {'─' * 41}")

    if all_results:
        db_result = write_results_to_db(all_results, dry_run)
    else:
        db_result = {"written": 0, "skipped": 0}
        if not as_json:
            print("  — No tracks processed — database unchanged.")
```

Remove the `added = append_tracks_to_xml(...)` references and the trailing
`playlists_added` JSON block that read from `added`; replace the JSON summary's
`"xml_path"` with `"db_written": db_result["written"]`. Remove the final
"In Rekordbox 7: File → Import" guidance block (~lines 1160-1164). Delete the
`--xml`, `--rebuild-playlists`, and `XML_PATH` argparse handling and the
`xml_default`/`args.xml` validation. Remove `custom_playlist` XML plumbing if
it only fed `append_tracks_to_xml` (keep the `--playlist` flag as a no-op or
delete it — deleting is cleaner; remove `--playlist` and its parameter).

Update `run_pipeline`'s signature to drop `xml_path` and `custom_playlist`, and
update the call in `main()` accordingly.

- [ ] **Step 5: Delete the dead files + their tests**

```bash
git rm trackstage/xml.py scripts/sync_rekordbox.py scripts/backfill_xml.py \
       tests/test_xml.py tests/test_xml_update.py
```

Check for stragglers:

Run: `grep -rn "from .xml\|import xml\b\|append_tracks_to_xml\|sync_rekordbox\|XML_PATH\|rebuild_playlists" trackstage/ scripts/ tests/`
Expected: no results (fix any that remain — e.g. `scripts/analyze_library.py` should not reference them).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all green. If `tests/test_comment_merge.py` or others imported from `xml.py`, update them to the new source (they should not — comment logic lives in `tags.py`).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: retire XML backend — batch flow writes via dbwriter; delete xml.py + sync_rekordbox.py"
```

---

### Task 10: update the ownership memory + docs

**Files:**
- Modify: `/home/kaitlyn/.claude/projects/-home-kaitlyn/memory/project_rekordbox-write-ownership.md`
- Modify: `/home/kaitlyn/.claude/projects/-home-kaitlyn/memory/project_pipeline-order-gotcha.md`
- Modify: `README.md` (usage: add `trackstage add`, remove XML-import step)

**Interfaces:** none (documentation).

- [ ] **Step 1: Update the ownership memory**

Edit `project_rekordbox-write-ownership.md`: dbwriter is now the canonical writer for Content rows + Rating/Color/BPM/Comment + Styles/Labels playlists + My Tags, **written directly to master.db** (no XML). The rekordbox-database MCP retains reads + manual/ad-hoc playlists. Guardrails unchanged (one writer, RB closed, backup). Note `xml.py`/`sync_rekordbox.py` deleted.

- [ ] **Step 2: Update the ordering-gotcha memory**

Edit `project_pipeline-order-gotcha.md`: mark the gotcha **resolved** — dbwriter creates the `djmdContent` row itself, so there is no XML-import ordering dependency anymore. Keep the historical note for context.

- [ ] **Step 3: Update README usage**

In `README.md`, add a "Quick add" section documenting `trackstage add "<query>"` and remove the "File → Import → Import rekordbox XML File" instructions from the workflow.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/trackstage && git add README.md
git commit -m "docs: README — document 'trackstage add', drop XML-import step"
```

(The memory files live outside the repo; they are saved in place, not committed here.)

---

## Self-Review

**Spec coverage:**
- Sourcing (Soulseek/slskd) → Task 2. ✓
- FLAC-preferred / MP3≥320 fallback / abort → Task 2 `rank_candidates` + Task 7 `no_source`. ✓
- Discogs identify + confirm-on-ambiguity → Task 3 + Task 7 (`choose`/`--pick`). ✓
- Essentia analyze (cached) + loudness/RG → Task 7 reuses `analyze_track` (loudness already wired in analyzer/pipeline). ✓
- Organize → Task 7 `build_dest` + `shutil.move` + `write_tags`. ✓
- dbwriter one-txn Content + Artist/Album/Genre/Label/Key/BPM/Rating/Color/Comment → Task 4/5. ✓
- My Tags (Genre/Vibe/Sound/Situation) → Task 4 `computed_tag_names` + Task 5 `assign_my_tags`. ✓
- Styles/Labels playlists ensure+join → Task 5 `ensure_playlist`. ✓
- Guards: refuse if RB running, backup + restore-on-failure, idempotent by FolderPath → Task 5 (dup) + Task 6 (guards) + Task 7/9 wiring. ✓
- Interface `trackstage add` + `--dry-run`/`--discogs-id`/`--format`/`--yes` → Task 1/7. ✓
- Skill rewrite, remove XML import → Task 8. ✓
- Delete xml.py + sync_rekordbox.py → Task 9. ✓
- Follow-up memory update → Task 10. ✓

**Deviations from spec (flagged):**
1. **Sourcer uses slskd REST, not the MCP.** The engine runs autonomously (a CLI process cannot invoke Claude's MCP tools); the MCP stays for conversational/manual use. Same daemon, same result.
2. **KeyID is reuse-existing-or-skip** (no new djmdKey rows) because pyrekordbox 0.4.4 has no `add_key`, and Rekordbox regenerates key when it builds ANLZ on first load. BPM is still pre-written.
3. **Batch inbox flow (`trackstage -y`) is redirected through dbwriter** rather than deleted, honoring "scrap the XML pipeline" while keeping batch ingestion working on the new backend.

**Placeholder scan:** every code step contains complete code; no TODO/TBD. The one conditional-logic note (Task 5 duplicate-path playlist guard) includes the exact replacement code.

**Type consistency:** `add_track(wsl_path, win_path, filename, title, artist, meta, analysis)` signature identical in Task 5 (def), Task 7 (call), Task 9 (call). `content_fields`/`computed_tag_names`/`resolve_tag_ids` names consistent across Tasks 4–5. `rekordbox_running`/`backup_db`/`restore_db` consistent across Tasks 6, 7, 9. `run_add(args) -> int` consistent Tasks 1, 7.
