"""bin/playlist-writeback.py: mcatalogid resolution, diff, classification.

writeback.js itself needs live Music.app and isn't unit-testable here - see
docs/mac-playlist-writeback.md for the manual verification steps that cover
it (this session's real-playlist read test, disposable-playlist write test,
idempotency test, and cleanup, all performed before this ever touched a
real user playlist).
"""
import csv
from unittest.mock import patch

from conftest import import_bin

wb = import_bin("playlist-writeback.py")


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "album", "filename",
                                            "ext", "mcatalogid"])
        w.writeheader()
        for r in rows:
            w.writerow({"artist": "", "album": "", "filename": "", "ext": "mp3", **r})


# --- load_csv_mcatalogids ---------------------------------------------------

def test_load_csv_mcatalogids_real_values_only(tmp_path):
    csv_path = tmp_path / "files.csv"
    _write_csv(csv_path, [
        {"path": "a/b.mp3", "mcatalogid": "1000"},
        {"path": "c/d.mp3", "mcatalogid": "catalog"},   # placeholder - excluded
        {"path": "e/f.mp3", "mcatalogid": ""},           # blank - excluded
    ])
    ids = wb.load_csv_mcatalogids(csv_path)
    assert ids == {"a/b.mp3": "1000"}


def test_plan_resolves_despite_nfd_vs_nfc_mismatch(tmp_path):
    # real bug, found via real playlist data: files.csv can carry a path in
    # NFD (macOS/APFS hands that back for any decomposable character, e.g.
    # accents) while the .m3u has the same logical path in NFC (or vice
    # versa - either file could be the stale/differently-sourced one). A
    # plain dict-key lookup between the two silently fails and gets
    # misreported as "unresolved" even though the id is right there.
    # Exercises plan_for_playlist's OWN normalization specifically: the
    # .m3u line here is NFD, csv_ids' key is NFC (as load_csv_mcatalogids
    # itself now always stores it) - the two are unequal as raw strings, so
    # this only resolves if plan_for_playlist normalizes the .m3u line
    # itself before the lookup.
    import unicodedata
    nfc_name = "various-artists/café-jazz/01-song.mp3"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfc_name != nfd_name  # sanity: they really are different strings

    m3u = tmp_path / "Test.m3u"
    m3u.write_text(f"#EXTM3U\n{nfd_name}\n", encoding="utf-8")
    csv_ids = {nfc_name: "1000"}
    apple_by_id = {"1000": "/apple/song.mp3"}

    with patch.object(wb, "read_playlist_locations", return_value=[]):
        plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["unresolved"] == []
    assert plan["to_add_paths"] == ["/apple/song.mp3"]


# --- read_tag (mp3/m4a round trip, same approach proven elsewhere) --------

def test_read_tag_mp3(tiny_mp3):
    from mutagen.id3 import ID3, TXXX
    id3 = ID3(tiny_mp3)
    id3.add(TXXX(encoding=3, desc="mcatalogid", text=["777"]))
    id3.save(tiny_mp3)
    assert wb.read_tag(tiny_mp3) == "777"


def test_read_tag_missing_returns_none(tiny_mp3):
    assert wb.read_tag(tiny_mp3) is None


# --- scan_apple_by_mcatalogid ----------------------------------------------

def test_scan_apple_by_mcatalogid(build_tree):
    root = build_tree({"Artist/Album/01 Title.mp3": None})
    from mutagen.id3 import ID3, TXXX
    target = root / "Artist/Album/01 Title.mp3"
    id3 = ID3(target)
    id3.add(TXXX(encoding=3, desc="mcatalogid", text=["555"]))
    id3.save(target)

    result = wb.scan_apple_by_mcatalogid(root)

    assert result == {"555": str(target)}


# --- plan_for_playlist: the diff/classification logic ----------------------

def test_plan_to_add_when_missing_from_apple(tmp_path):
    m3u = tmp_path / "Test.m3u"
    m3u.write_text("#EXTM3U\na/b.mp3\n")
    csv_ids = {"a/b.mp3": "1000"}
    apple_by_id = {"1000": "/apple/path.mp3"}

    with patch.object(wb, "read_playlist_locations", return_value=[]):
        plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["target_name"] == "Test"
    assert plan["current_found"] is True
    assert plan["to_add_paths"] == ["/apple/path.mp3"]
    assert plan["not_in_library"] == []
    assert plan["unresolved"] == []


def test_plan_already_present_is_not_to_add(tmp_path):
    m3u = tmp_path / "Test.m3u"
    m3u.write_text("#EXTM3U\na/b.mp3\n")
    csv_ids = {"a/b.mp3": "1000"}
    apple_by_id = {"1000": "/apple/path.mp3"}

    with patch.object(wb, "read_tag", return_value="1000"):
        with patch.object(wb, "read_playlist_locations", return_value=["/apple/path.mp3"]):
            plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["to_add_paths"] == []


def test_plan_not_in_library(tmp_path):
    m3u = tmp_path / "Test.m3u"
    m3u.write_text("#EXTM3U\na/b.mp3\n")
    csv_ids = {"a/b.mp3": "1000"}
    apple_by_id = {}  # not imported anywhere

    with patch.object(wb, "read_playlist_locations", return_value=[]):
        plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["to_add_paths"] == []
    assert plan["not_in_library"] == ["1000"]


def test_plan_unresolved_when_no_mcatalogid_in_csv(tmp_path):
    m3u = tmp_path / "Test.m3u"
    m3u.write_text("#EXTM3U\nuntagged/track.mp3\n")
    csv_ids = {}  # nothing in files.csv for this path
    apple_by_id = {}

    with patch.object(wb, "read_playlist_locations", return_value=[]):
        plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["unresolved"] == ["untagged/track.mp3"]
    assert plan["to_add_paths"] == []
    assert plan["not_in_library"] == []


def test_plan_new_playlist_not_found_in_apple(tmp_path):
    m3u = tmp_path / "Test.m3u"
    m3u.write_text("#EXTM3U\na/b.mp3\n")
    csv_ids = {"a/b.mp3": "1000"}
    apple_by_id = {"1000": "/apple/path.mp3"}

    with patch.object(wb, "read_playlist_locations", return_value=None):
        plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["current_found"] is False
    assert plan["to_add_paths"] == ["/apple/path.mp3"]
