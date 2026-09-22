"""bin/apple-music-tag-sync.py: its own matching-engine copy, and main()
end to end across a gdrive_root/apple_root pair."""
import subprocess
import sys

from conftest import BIN, import_bin

am = import_bin("apple-music-tag-sync.py")


# --- independent coverage of this file's own find_match/build_lookups copy -
# (the real risk of this repo's standalone-script convention is drift
# between the duplicated copies - playlist-sync.py's own copy is covered in
# tests/test_playlist_sync.py)

def test_find_match_same_title_twice_disambiguates_by_track_number():
    tree = [
        "r-d-burman/mehbooba/01-mere-naina-sawan-bhadon-[mid-1002871].mp3",
        "r-d-burman/mehbooba/04-mere-naina-sawan-bhadon-[mid-1002870].mp3",
    ]
    art_alb_trk, art_trk = am.build_lookups(tree)
    hit1 = am.find_match("R.D. Burman/Mehbooba/1-01 Mere Naina Sawan Bhadon.mp3",
                          art_alb_trk, art_trk)
    hit4 = am.find_match("R.D. Burman/Mehbooba/1-04 Mere Naina Sawan Bhadon.mp3",
                          art_alb_trk, art_trk)
    assert hit1 != hit4
    assert hit1.endswith("01-mere-naina-sawan-bhadon-[mid-1002871].mp3")
    assert hit4.endswith("04-mere-naina-sawan-bhadon-[mid-1002870].mp3")


def test_find_match_no_match_returns_none():
    art_alb_trk, art_trk = am.build_lookups(["artist/album/01-song-[mid-1].mp3"])
    assert am.find_match("Nobody/Nothing/01 Nope.mp3", art_alb_trk, art_trk) is None


# --- read_tag / write_tag (mp3 + m4a only - this script never touches flac) -

def test_mp3_round_trip(tiny_mp3):
    assert am.read_tag(tiny_mp3) is None
    am.write_tag(tiny_mp3, "42")
    assert am.read_tag(tiny_mp3) == "42"


def test_m4a_round_trip(tiny_m4a):
    assert am.read_tag(tiny_m4a) is None
    am.write_tag(tiny_m4a, "43")
    assert am.read_tag(tiny_m4a) == "43"


# --- main(): end to end across two small trees ------------------------------

def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(BIN / "apple-music-tag-sync.py"), *args],
        capture_output=True, text=True,
    )


def test_main_writes_conflicts_and_skips(tmp_path, build_tree):
    gdrive = build_tree({
        "artist/album/01-write-me-[mid-100].mp3": None,
        "artist/album/02-already-ok-[mid-200].mp3": None,
        "artist/album/03-conflict-[mid-300].mp3": None,
        "artist/album/04-no-source-id.mp3": None,
        "artist/album/05-unmatched-only-on-gdrive.mp3": None,
    }, root_name="gdrive")
    am.write_tag(gdrive / "artist/album/01-write-me-[mid-100].mp3", "100")
    am.write_tag(gdrive / "artist/album/02-already-ok-[mid-200].mp3", "200")
    am.write_tag(gdrive / "artist/album/03-conflict-[mid-300].mp3", "300")
    # 04 and 05 deliberately left untagged on the gdrive side

    apple = build_tree({
        "Artist/Album/01 Write Me.mp3": None,
        "Artist/Album/02 Already Ok.mp3": None,
        "Artist/Album/03 Conflict.mp3": None,
        "Artist/Album/04 No Source Id.mp3": None,
        "Artist/Album/06 Unmatched Only On Apple.mp3": None,
    }, root_name="apple")
    am.write_tag(apple / "Artist/Album/02 Already Ok.mp3", "200")     # matches -> skip
    am.write_tag(apple / "Artist/Album/03 Conflict.mp3", "999")      # differs -> conflict

    result = _run_cli(["--gdrive", str(gdrive), "--apple", str(apple)])
    assert result.returncode == 0, result.stderr

    assert am.read_tag(apple / "Artist/Album/01 Write Me.mp3") == "100"
    assert am.read_tag(apple / "Artist/Album/02 Already Ok.mp3") == "200"
    assert am.read_tag(apple / "Artist/Album/03 Conflict.mp3") == "999"  # untouched
    assert am.read_tag(apple / "Artist/Album/04 No Source Id.mp3") is None
    assert am.read_tag(apple / "Artist/Album/06 Unmatched Only On Apple.mp3") is None

    assert "wrote 1, failed 0" in result.stderr
    assert "conflicts (left alone):   1" in result.stderr


def test_main_dry_run_writes_nothing(tmp_path, build_tree):
    gdrive = build_tree({"artist/album/01-title-[mid-1].mp3": None}, root_name="gdrive")
    am.write_tag(gdrive / "artist/album/01-title-[mid-1].mp3", "1")
    apple = build_tree({"Artist/Album/01 Title.mp3": None}, root_name="apple")

    result = _run_cli(["--dry-run", "--gdrive", str(gdrive), "--apple", str(apple)])
    assert result.returncode == 0, result.stderr
    assert am.read_tag(apple / "Artist/Album/01 Title.mp3") is None
    assert "--dry-run: 1 file(s) would be tagged, nothing written" in result.stderr
