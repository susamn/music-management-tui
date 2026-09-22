"""bin/merge-m3u.py: the git union merge driver for playlists/*.m3u."""
import subprocess
import sys

from conftest import BIN


def _run_driver(tmp_path, base_lines, ours_lines, theirs_lines):
    base = tmp_path / "base.m3u"
    ours = tmp_path / "ours.m3u"
    theirs = tmp_path / "theirs.m3u"
    base.write_text("\n".join(base_lines) + "\n")
    ours.write_text("\n".join(ours_lines) + "\n")
    theirs.write_text("\n".join(theirs_lines) + "\n")

    result = subprocess.run(
        [sys.executable, str(BIN / "merge-m3u.py"), str(base), str(ours), str(theirs)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return ours.read_text().splitlines()


def test_union_of_two_divergent_additions(tmp_path):
    base = ["#EXTM3U", "artist1/track1.mp3", "artist2/track2.mp3"]
    ours = base + ["artist3/track3.mp3"]
    theirs = base + ["artist4/track4.mp3"]

    merged = _run_driver(tmp_path, base, ours, theirs)

    assert merged[0] == "#EXTM3U"
    assert "artist1/track1.mp3" in merged
    assert "artist2/track2.mp3" in merged
    assert "artist3/track3.mp3" in merged
    assert "artist4/track4.mp3" in merged


def test_no_duplicates_when_both_sides_add_the_same_track(tmp_path):
    base = ["#EXTM3U", "artist1/track1.mp3"]
    ours = ["#EXTM3U", "artist1/track1.mp3", "artist3/track3.mp3", "shared/new.mp3"]
    theirs = ["#EXTM3U", "artist1/track1.mp3", "shared/new.mp3", "artist4/track4.mp3"]

    merged = _run_driver(tmp_path, base, ours, theirs)

    assert merged.count("shared/new.mp3") == 1
    assert len(merged) == len(set(merged))  # every line unique, including the header


def test_single_header_regardless_of_input(tmp_path):
    base = ["#EXTM3U"]
    ours = ["#EXTM3U", "a/b.mp3"]
    theirs = ["#EXTM3U", "c/d.mp3"]

    merged = _run_driver(tmp_path, base, ours, theirs)

    assert merged[0] == "#EXTM3U"
    assert merged.count("#EXTM3U") == 1


def test_ours_order_preserved_theirs_new_appended(tmp_path):
    base = ["#EXTM3U"]
    ours = ["#EXTM3U", "z/last.mp3", "a/first.mp3"]
    theirs = ["#EXTM3U", "a/first.mp3", "m/middle.mp3"]

    merged = _run_driver(tmp_path, base, ours, theirs)

    # ours' own order is untouched; theirs' new-only entry is appended after
    assert merged == ["#EXTM3U", "z/last.mp3", "a/first.mp3", "m/middle.mp3"]


def test_empty_theirs_keeps_ours_unchanged(tmp_path):
    base = ["#EXTM3U", "a/b.mp3"]
    ours = ["#EXTM3U", "a/b.mp3", "c/d.mp3"]
    theirs = ["#EXTM3U"]

    merged = _run_driver(tmp_path, base, ours, theirs)

    assert merged == ["#EXTM3U", "a/b.mp3", "c/d.mp3"]


def test_exits_zero_always(tmp_path):
    # a union driver must never signal an unresolved conflict - that's the
    # entire point of choosing this merge strategy.
    merged = _run_driver(tmp_path, [], [], [])
    assert merged == ["#EXTM3U"]
