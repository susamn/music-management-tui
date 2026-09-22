"""bin/generate-files-csv.py: tag reading, scan() at various depths, main(),
and a cross-script integration check against playlist-sync.py's loader."""
import csv
import subprocess
import sys

from conftest import BIN, import_bin

gc = import_bin("generate-files-csv.py")
ps = import_bin("playlist-sync.py")


# --- read_mcatalogid ----------------------------------------------------

def test_read_mcatalogid_missing_is_blank(tiny_mp3, tiny_m4a, tiny_flac):
    assert gc.read_mcatalogid(tiny_mp3) == ""
    assert gc.read_mcatalogid(tiny_m4a) == ""
    assert gc.read_mcatalogid(tiny_flac) == ""


def test_read_mcatalogid_catalog_placeholder_is_blank(tiny_mp3):
    from mutagen.id3 import ID3, TXXX
    id3 = ID3(tiny_mp3)
    id3.add(TXXX(encoding=3, desc="mcatalogid", text=["catalog"]))
    id3.save(tiny_mp3)
    assert gc.read_mcatalogid(tiny_mp3) == ""


def test_read_mcatalogid_real_value_mp3(tiny_mp3):
    from mutagen.id3 import ID3, TXXX
    id3 = ID3(tiny_mp3)
    id3.add(TXXX(encoding=3, desc="mcatalogid", text=["4242"]))
    id3.save(tiny_mp3)
    assert gc.read_mcatalogid(tiny_mp3) == "4242"


def test_read_mcatalogid_real_value_m4a(tiny_m4a):
    from mutagen.mp4 import MP4, MP4FreeForm
    mp4 = MP4(tiny_m4a)
    mp4.tags["----:com.apple.iTunes:mcatalogid"] = [MP4FreeForm(b"4343")]
    mp4.save()
    assert gc.read_mcatalogid(tiny_m4a) == "4343"


def test_read_mcatalogid_real_value_flac(tiny_flac):
    from mutagen.flac import FLAC
    f = FLAC(tiny_flac)
    f["mcatalogid"] = ["4444"]
    f.save()
    assert gc.read_mcatalogid(tiny_flac) == "4444"


# --- scan() at 2/3/4-level depths (matches the real library's distribution) -

def test_scan_two_level_depth(build_tree):
    # Artist/File.ext - no album folder at all
    root = build_tree({"artist/track.mp3": None})
    rows = list(gc.scan(root))
    assert len(rows) == 1
    assert rows[0]["path"] == "artist/track.mp3"
    assert rows[0]["artist"] == "artist"
    assert rows[0]["album"] == ""
    assert rows[0]["filename"] == "track.mp3"
    assert rows[0]["ext"] == "mp3"


def test_scan_three_level_depth(build_tree):
    root = build_tree({"artist/album/track.mp3": None})
    rows = list(gc.scan(root))
    assert rows[0]["artist"] == "artist"
    assert rows[0]["album"] == "album"
    assert rows[0]["filename"] == "track.mp3"


def test_scan_four_level_depth(build_tree):
    # Artist/Album/Disc/File.ext - artist/album must still resolve to
    # parts[0]/parts[1], not to the disc folder
    root = build_tree({"artist/album/disc1/track.mp3": None})
    rows = list(gc.scan(root))
    assert rows[0]["path"] == "artist/album/disc1/track.mp3"
    assert rows[0]["artist"] == "artist"
    assert rows[0]["album"] == "album"
    assert rows[0]["filename"] == "track.mp3"


def test_scan_includes_mcatalogid(build_tree):
    root = build_tree({"artist/album/track.mp3": None})
    from mutagen.id3 import ID3, TXXX
    id3 = ID3(root / "artist/album/track.mp3")
    id3.add(TXXX(encoding=3, desc="mcatalogid", text=["999"]))
    id3.save(root / "artist/album/track.mp3")
    rows = list(gc.scan(root))
    assert rows[0]["mcatalogid"] == "999"


# --- main(): dry-run vs real write, atomic write, CSV correctness ----------

def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(BIN / "generate-files-csv.py"), *args],
        capture_output=True, text=True,
    )


def test_main_dry_run_writes_nothing(tmp_path, build_tree):
    root = build_tree({"artist/album/track.mp3": None})
    out = tmp_path / "files.csv"
    result = _run_cli(["--dry-run", "--root", str(root), "--out", str(out)])
    assert result.returncode == 0, result.stderr
    assert not out.exists()


def test_main_real_run_writes_correct_csv(tmp_path, build_tree):
    root = build_tree({
        "artist/album/track.mp3": None,
        "artist2/solo.m4a": None,
    })
    out = tmp_path / "files.csv"
    result = _run_cli(["--root", str(root), "--out", str(out)])
    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert not out.with_suffix(".csv.tmp").exists()  # atomic write leaves no .tmp

    with out.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    paths = {r["path"] for r in rows}
    assert paths == {"artist/album/track.mp3", "artist2/solo.m4a"}
    assert list(rows[0].keys()) == ["path", "artist", "album", "filename", "ext", "mcatalogid"]


def test_main_rows_sorted_by_path(tmp_path, build_tree):
    root = build_tree({
        "zebra/album/track.mp3": None,
        "aardvark/album/track.mp3": None,
    })
    out = tmp_path / "files.csv"
    _run_cli(["--root", str(root), "--out", str(out)])
    with out.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["path"] for r in rows] == sorted(r["path"] for r in rows)


# --- cross-script integration: generate-files-csv's output must be exactly
# what playlist-sync's loader/matcher expects -------------------------------

def test_generated_csv_is_consumable_by_playlist_sync(tmp_path, build_tree):
    root = build_tree({
        "r-d-burman/mehbooba/01-mere-naina-sawan-bhadon.mp3": None,
    })
    out = tmp_path / "files.csv"
    result = _run_cli(["--root", str(root), "--out", str(out)])
    assert result.returncode == 0, result.stderr

    csv_paths = list(ps.parse_csv(out))
    art_alb_trk, art_trk = ps.build_lookups(csv_paths)
    hit = ps.find_match("R.D. Burman/Mehbooba/01 Mere Naina Sawan Bhadon.mp3",
                         art_alb_trk, art_trk)
    assert hit == "r-d-burman/mehbooba/01-mere-naina-sawan-bhadon.mp3"
