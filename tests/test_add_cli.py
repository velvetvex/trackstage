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


from trackstage.add import parse_query


class TestParseQuery:
    def test_by_form_splits_and_reorders(self):
        artist, title, search = parse_query("enchantress 1200 by legowelt")
        assert artist == "legowelt"
        assert title == "enchantress 1200"
        assert search == "legowelt enchantress 1200"  # artist-first, no 'by'

    def test_no_by_leaves_query_unchanged(self):
        artist, title, search = parse_query("legowelt enchantress 1200")
        assert artist == ""
        assert title == "legowelt enchantress 1200"
        assert search == "legowelt enchantress 1200"

    def test_case_insensitive_by(self):
        artist, title, search = parse_query("Windowlicker BY Aphex Twin")
        assert artist == "Aphex Twin"
        assert title == "Windowlicker"
        assert search == "Aphex Twin Windowlicker"

    def test_no_search_pollution_from_by(self):
        _, _, search = parse_query("track by artist")
        assert " by " not in f" {search} "
