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


from pathlib import Path
from trackstage.sourcer import _find_downloaded


class TestFindDownloaded:
    def test_finds_nested_file(self, tmp_path):
        # slskd nests under remote folder structure
        nested = tmp_path / "Like A Song From Your Dream (LIES-206) (2024)"
        nested.mkdir()
        f = nested / "01 - Enchantress 1200.flac"
        f.write_bytes(b"x")
        assert _find_downloaded(tmp_path, "01 - Enchantress 1200.flac") == f

    def test_finds_flat_file(self, tmp_path):
        f = tmp_path / "track.flac"; f.write_bytes(b"x")
        assert _find_downloaded(tmp_path, "track.flac") == f

    def test_missing_returns_none(self, tmp_path):
        assert _find_downloaded(tmp_path, "nope.flac") is None

    def test_glob_metachars_in_name(self, tmp_path):
        # brackets/parens must be matched literally, not as glob patterns
        f = tmp_path / "sub" / "A1 [Remix] (Dub).flac"
        f.parent.mkdir()
        f.write_bytes(b"x")
        assert _find_downloaded(tmp_path, "A1 [Remix] (Dub).flac") == f
