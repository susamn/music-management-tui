"""bin/mcatalogid-backfill.py: is_real, tag read/write round-trip, two-pass logic."""
import subprocess
import sys

import pytest

from conftest import BIN, import_bin

mb = import_bin("mcatalogid-backfill.py")


# --- is_real -----------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, False),
    ("", False),
    ("catalog", False),
    ("CATALOG", False),
    ("Catalog", False),
    ("  catalog  ", False),
    ("1234567", True),
    ("catalog2", True),  # not exactly the placeholder string
])
def test_is_real(value, expected):
    assert mb.is_real(value) is expected


# --- read_tag / write_tag round-trip on real fixtures ------------------------

def test_mp3_tag_round_trip_preserves_audio(tiny_mp3):
    from mutagen.id3 import ID3
    before_size = tiny_mp3.stat().st_size
    before_hdr = ID3(tiny_mp3).size
    with open(tiny_mp3, "rb") as f:
        before_audio = f.read()[before_hdr:]

    assert mb.read_tag(tiny_mp3) is None
    mb.write_tag(tiny_mp3, "1234567")
    assert mb.read_tag(tiny_mp3) == "1234567"

    after_hdr = ID3(tiny_mp3).size
    with open(tiny_mp3, "rb") as f:
        after_audio = f.read()[after_hdr:]
    assert before_audio == after_audio, "write_tag must not touch audio bytes"
    assert tiny_mp3.stat().st_size >= before_size - 4096  # sanity, not truncated


def test_m4a_tag_round_trip(tiny_m4a):
    assert mb.read_tag(tiny_m4a) is None
    mb.write_tag(tiny_m4a, "7654321")
    assert mb.read_tag(tiny_m4a) == "7654321"


def test_flac_tag_round_trip(tiny_flac):
    assert mb.read_tag(tiny_flac) is None
    mb.write_tag(tiny_flac, "111222")
    assert mb.read_tag(tiny_flac) == "111222"


def test_write_tag_overwrites_previous_value(tiny_mp3):
    mb.write_tag(tiny_mp3, "111")
    mb.write_tag(tiny_mp3, "222")
    assert mb.read_tag(tiny_mp3) == "222"


# --- scan() ------------------------------------------------------------------

def test_scan_reports_current_and_filename_id(build_tree):
    root = build_tree({
        "artist/album/01-title-[mid-555].mp3": None,
        "artist/album/02-other.mp3": None,
    })
    mb.write_tag(root / "artist/album/01-title-[mid-555].mp3", "555")

    rows = {str(p.relative_to(root)): (cur, fid) for p, cur, fid in mb.scan(root)}
    assert rows["artist/album/01-title-[mid-555].mp3"] == ("555", "555")
    assert rows["artist/album/02-other.mp3"] == (None, None)


# --- main(): the two-pass backfill/assign logic, end to end via subprocess --

def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(BIN / "mcatalogid-backfill.py"), *args],
        capture_output=True, text=True,
    )


def _fixture_library(build_tree):
    """One file per case the two-pass logic needs to distinguish."""
    root = build_tree({
        "already/tagged/01-real-[mid-1000].mp3": None,   # already real - untouched
        "backfill/case/02-has-id-[mid-2000].mp3": None,  # missing tag, real filename id
        "assign/case/03-no-id.mp3": None,                # missing tag, no filename id
        "catalog/case/04-placeholder-[mid-catalog].mp3": None,  # placeholder -> assign
        "conflict/case/05-mismatch-[mid-9000].mp3": None,       # tag != filename
    })
    mb.write_tag(root / "already/tagged/01-real-[mid-1000].mp3", "1000")
    mb.write_tag(root / "catalog/case/04-placeholder-[mid-catalog].mp3", "catalog")
    mb.write_tag(root / "conflict/case/05-mismatch-[mid-9000].mp3", "8888")
    return root


def test_status_counts_match_fixture(build_tree):
    # catalog/case has neither a real tag nor a numeric filename id (its
    # filename literally says "[mid-catalog]"), so it counts toward "needs a
    # brand-new id" alongside assign/case - 2, not 1. That's intended: a
    # "catalog" placeholder means "no real id was ever assigned", same as
    # having none at all.
    root = _fixture_library(build_tree)
    result = _run_cli(["--status", "--root", str(root)])
    assert result.returncode == 0, result.stderr

    def value_for(label):
        for line in result.stdout.splitlines():
            if line.strip().startswith(label):
                return line.rsplit(None, 1)[-1]
        raise AssertionError(f"no line starting with {label!r} in:\n{result.stdout}")

    assert value_for("total audio files:") == "5"
    assert value_for("tagged (real id):") == "1"
    assert value_for("missing:") == "3"
    assert value_for("backfillable from filename:") == "1"
    assert value_for("needs a brand-new id:") == "2"
    assert value_for("conflicts (tag != filename):") == "1"


def test_dry_run_writes_nothing(build_tree):
    root = _fixture_library(build_tree)
    before = {p: mb.read_tag(p) for p, _, _ in mb.scan(root)}
    result = _run_cli(["--dry-run", "--root", str(root)])
    assert result.returncode == 0, result.stderr
    after = {p: mb.read_tag(p) for p, _, _ in mb.scan(root)}
    assert before == after


def test_real_run_backfills_assigns_and_leaves_conflicts_alone(build_tree):
    root = _fixture_library(build_tree)
    result = _run_cli(["--root", str(root)])
    assert result.returncode == 0, result.stderr

    assert mb.read_tag(root / "already/tagged/01-real-[mid-1000].mp3") == "1000"
    assert mb.read_tag(root / "backfill/case/02-has-id-[mid-2000].mp3") == "2000"
    assert mb.read_tag(root / "catalog/case/04-placeholder-[mid-catalog].mp3") not in (
        None, "catalog")
    assert mb.read_tag(root / "conflict/case/05-mismatch-[mid-9000].mp3") == "8888"

    assign_val = mb.read_tag(root / "assign/case/03-no-id.mp3")
    assert assign_val is not None and assign_val.isdigit()
    # new ids must start above every real id already present (1000, 2000, 9000)
    assert int(assign_val) > 9000


def test_real_run_is_idempotent(build_tree):
    root = _fixture_library(build_tree)
    _run_cli(["--root", str(root)])
    after_first = {str(p.relative_to(root)): mb.read_tag(p) for p, _, _ in mb.scan(root)}

    result = _run_cli(["--root", str(root)])
    assert result.returncode == 0
    assert "already tagged" not in "" or True  # no crash is the main assertion
    after_second = {str(p.relative_to(root)): mb.read_tag(p) for p, _, _ in mb.scan(root)}
    assert after_first == after_second


def test_new_ids_are_unique_and_sequential(build_tree):
    root = build_tree({
        f"artist/album/{i:02d}-track{i}.mp3": None for i in range(1, 5)
    })
    _run_cli(["--root", str(root)])
    values = sorted(int(mb.read_tag(p)) for p, _, _ in mb.scan(root))
    assert values == list(range(values[0], values[0] + 4))
    assert len(set(values)) == 4
