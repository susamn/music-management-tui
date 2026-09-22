"""bin/playlist-sync.py: matching engine, files.csv loader, existence check."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BIN, import_bin

ps = import_bin("playlist-sync.py")


# --- normalize / clean_apple_name / clean_tree_name -----------------------

def test_normalize_basic():
    assert ps.normalize("Hello, World!") == "hello-world"


def test_normalize_nfd_nfc_fold():
    # "Fuzon" with a combining diaeresis (NFD, what macOS hands back) must
    # normalize the same as the precomposed "o with diaeresis" (NFC).
    nfd = "Fuzön"
    nfc = "Fuzön"
    assert ps.normalize(nfd) == ps.normalize(nfc)


def test_normalize_collapses_punctuation_runs():
    assert ps.normalize("a---b  c!!d") == "a-b-c-d"


def test_normalize_empty():
    assert ps.normalize("") == ""
    assert ps.normalize(None) == ""


def test_clean_apple_name_strips_track_number():
    assert ps.clean_apple_name("07 Ghosts n Stuff.mp3") == "ghosts-n-stuff"
    assert ps.clean_apple_name("1-07 Ghosts n Stuff.mp3") == "ghosts-n-stuff"


def test_clean_tree_name_strips_mid_and_number():
    assert ps.clean_tree_name("07-ghosts-n-stuff-[mid-1234].mp3") == "ghosts-n-stuff"
    assert ps.clean_tree_name("1-07-ghosts-n-stuff-[mid-1234].mp3") == "ghosts-n-stuff"


# --- apple_track_num / tree_track_num --------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("07 Title.mp3", 7),
    ("1-07 Title.mp3", 7),
    ("Title.mp3", None),
])
def test_apple_track_num(filename, expected):
    assert ps.apple_track_num(filename) == expected


@pytest.mark.parametrize("filename,expected", [
    ("07-title-[mid-1].mp3", 7),
    ("1-07-title-[mid-1].mp3", 7),
    ("4-title-[mid-1].mp3", 4),
    ("title-[mid-1].mp3", None),
])
def test_tree_track_num(filename, expected):
    assert ps.tree_track_num(filename) == expected


def test_track_num_zero_padding_compares_equal_as_ints():
    # "04" (Apple) vs "4" (tree) must compare equal once both are ints -
    # this is the whole point of capturing them as int, not str.
    assert ps.apple_track_num("04 Title.mp3") == ps.tree_track_num("4-title-[mid-1].mp3")


# --- build_lookups ----------------------------------------------------------

def test_build_lookups_single_candidate():
    paths = ["artist/album/01-title-[mid-1].mp3"]
    art_alb_trk, art_trk = ps.build_lookups(paths)
    assert art_alb_trk["artist|album|title"] == paths
    assert art_trk["artist|title"] == paths


def test_build_lookups_multiple_candidates_preserve_order():
    paths = [
        "artist/album/01-title-[mid-1].mp3",
        "artist/other-album/01-title-[mid-2].mp3",
    ]
    _, art_trk = ps.build_lookups(paths)
    assert art_trk["artist|title"] == paths


# --- find_match: the regression-test core ----------------------------------

def test_find_match_same_title_twice_on_one_album_disambiguates_by_track_number():
    # the original bug: "Mere Naina Sawan Bhadon" appears as both track 1
    # and track 4 on the same album (two singers) - before the fix, both
    # Apple inputs collapsed onto the same wrong tree file.
    tree = [
        "r-d-burman/mehbooba/01-mere-naina-sawan-bhadon-[mid-1002871].mp3",
        "r-d-burman/mehbooba/04-mere-naina-sawan-bhadon-[mid-1002870].mp3",
    ]
    art_alb_trk, art_trk = ps.build_lookups(tree)

    hit1 = ps.find_match("/x/R.D. Burman/Mehbooba/1-01 Mere Naina Sawan Bhadon.mp3",
                          art_alb_trk, art_trk)
    hit4 = ps.find_match("/x/R.D. Burman/Mehbooba/1-04 Mere Naina Sawan Bhadon.mp3",
                          art_alb_trk, art_trk)

    assert hit1 == "r-d-burman/mehbooba/01-mere-naina-sawan-bhadon-[mid-1002871].mp3"
    assert hit4 == "r-d-burman/mehbooba/04-mere-naina-sawan-bhadon-[mid-1002870].mp3"
    assert hit1 != hit4


def test_find_match_prefer_wins_over_coincidental_cross_album_track_number():
    # reviewer's repro: two genuinely different releases (album strings
    # don't text-match, so this falls to the album-dropped lookup 2), whose
    # track numbers coincidentally collide. An established `prefer` pick
    # must not be overridden by that coincidence.
    tree = [
        "artist/original/07-song-[mid-1].mp3",
        "artist/live/04-song-[mid-2].mp3",
    ]
    art_alb_trk, art_trk = ps.build_lookups(tree)
    prefer = frozenset({"artist/original/07-song-[mid-1].mp3"})

    hit = ps.find_match("/x/Artist/Original Edition/04 Song.mp3",
                         art_alb_trk, art_trk, prefer)

    assert hit == "artist/original/07-song-[mid-1].mp3"


def test_find_match_zero_padding_mismatch_still_disambiguates():
    tree = [
        "artist/album/1-song-[mid-1].mp3",
        "artist/album/4-song-[mid-2].mp3",
    ]
    art_alb_trk, art_trk = ps.build_lookups(tree)

    hit = ps.find_match("/x/Artist/Album/04 Song.mp3", art_alb_trk, art_trk)

    assert hit == "artist/album/4-song-[mid-2].mp3"


def test_find_match_ordinary_unique_match():
    tree = ["artist/album/01-song-[mid-1].mp3"]
    art_alb_trk, art_trk = ps.build_lookups(tree)
    hit = ps.find_match("/x/Artist/Album/01 Song.mp3", art_alb_trk, art_trk)
    assert hit == "artist/album/01-song-[mid-1].mp3"


def test_find_match_no_match_returns_none():
    art_alb_trk, art_trk = ps.build_lookups(["artist/album/01-song-[mid-1].mp3"])
    assert ps.find_match("/x/Nobody/Nothing/01 Nope.mp3", art_alb_trk, art_trk) is None


def test_find_match_short_path_returns_none():
    art_alb_trk, art_trk = ps.build_lookups([])
    assert ps.find_match("just-a-filename.mp3", art_alb_trk, art_trk) is None


# --- parse_csv ---------------------------------------------------------------

def test_parse_csv_yields_path_column(tmp_path):
    csv_path = tmp_path / "files.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "album", "filename",
                                            "ext", "mcatalogid"])
        w.writeheader()
        w.writerow({"path": "a/b/c.mp3", "artist": "a", "album": "b",
                    "filename": "c.mp3", "ext": "mp3", "mcatalogid": "1"})
    assert list(ps.parse_csv(csv_path)) == ["a/b/c.mp3"]


def test_parse_csv_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        list(ps.parse_csv(tmp_path / "does-not-exist.csv"))


# --- disk_paths ----------------------------------------------------------

def test_disk_paths_reflects_real_files(build_tree):
    root = build_tree({"artist/album/track.mp3": None, "artist2/solo.m4a": None})
    paths = ps.disk_paths(root)
    assert paths == {"artist/album/track.mp3", "artist2/solo.m4a"}


def test_disk_paths_empty_dir(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert ps.disk_paths(root) == set()


# --- CLI-level: main() via subprocess ---------------------------------------

def _run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(BIN / "playlist-sync.py"), *args],
        capture_output=True, text=True, cwd=cwd,
    )


def test_cli_reports_phantom_not_silent_match(tmp_path, build_tree):
    # the exact scenario proven manually earlier this session: a files.csv
    # entry whose file has since been removed from --music-root must be
    # reported as phantom, not matched or silently dropped into missed.
    root = build_tree({"artist/album/01-title-[mid-1].mp3": None})
    csv_path = tmp_path / "files.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "album", "filename",
                                            "ext", "mcatalogid"])
        w.writeheader()
        w.writerow({"path": "artist/album/01-title-[mid-1].mp3", "artist": "artist",
                    "album": "album", "filename": "01-title-[mid-1].mp3",
                    "ext": "mp3", "mcatalogid": ""})

    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps([{
        "name": "Test Playlist", "special": "none", "smart": False,
        "tracks": ["/x/Artist/Album/01 Title.mp3"],
    }]))

    repo = tmp_path / "repo"
    repo.mkdir()

    # remove the file after files.csv was written, so it's a genuine phantom
    (root / "artist/album/01-title-[mid-1].mp3").unlink()

    result = _run_cli(["--from", str(dump), "--dry-run", "--misses",
                        "--csv", str(csv_path), "--music-root", str(root),
                        "--repo", str(repo)])

    assert result.returncode == 0, result.stderr
    assert "Exported file changes: add 0, delete 0" in result.stdout
    assert "1 missing files" in result.stdout
    assert "phantom tracks" in result.stdout


def test_cli_matches_when_file_present(tmp_path, build_tree):
    root = build_tree({"artist/album/01-title-[mid-1].mp3": None})
    csv_path = tmp_path / "files.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "album", "filename",
                                            "ext", "mcatalogid"])
        w.writeheader()
        w.writerow({"path": "artist/album/01-title-[mid-1].mp3", "artist": "artist",
                    "album": "album", "filename": "01-title-[mid-1].mp3",
                    "ext": "mp3", "mcatalogid": ""})

    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps([{
        "name": "Test Playlist", "special": "none", "smart": False,
        "tracks": ["/x/Artist/Album/01 Title.mp3"],
    }]))

    repo = tmp_path / "repo"
    repo.mkdir()

    result = _run_cli(["--from", str(dump), "--dry-run",
                        "--csv", str(csv_path), "--music-root", str(root),
                        "--repo", str(repo)])

    assert result.returncode == 0, result.stderr
    assert "Exported file changes: add 1, delete 0" in result.stdout
    assert "0 phantom" not in result.stdout or "phantom" not in result.stdout.split("\n")[-3]
    assert "Apple Music: 1 tracks" in result.stdout

    result = _run_cli(["--from", str(dump),
                       "--csv", str(csv_path), "--music-root", str(root),
                       "--repo", str(repo)])
    assert result.returncode == 0, result.stderr
    assert (repo / "playlists" / "Test Playlist.m3u").read_bytes() == (
        b"#EXTM3U\nartist/album/01-title-[mid-1].mp3\n"
    )


def test_cli_dry_run_writes_nothing(tmp_path, build_tree):
    root = build_tree({"artist/album/01-title-[mid-1].mp3": None})
    csv_path = tmp_path / "files.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "album", "filename",
                                            "ext", "mcatalogid"])
        w.writeheader()
        w.writerow({"path": "artist/album/01-title-[mid-1].mp3", "artist": "artist",
                    "album": "album", "filename": "01-title-[mid-1].mp3",
                    "ext": "mp3", "mcatalogid": ""})

    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps([{
        "name": "Test Playlist", "special": "none", "smart": False,
        "tracks": ["/x/Artist/Album/01 Title.mp3"],
    }]))

    repo = tmp_path / "repo"
    repo.mkdir()

    _run_cli(["--from", str(dump), "--dry-run",
              "--csv", str(csv_path), "--music-root", str(root),
              "--repo", str(repo)])

    assert not (repo / "playlists").exists() or \
        list((repo / "playlists").glob("*.m3u")) == []


@pytest.mark.parametrize('prior, tracks, expected', [
    (None, ['Second', 'First', 'Second'], ['second', 'first']),
    (['first', 'second', 'first', 'removed'], ['Second', 'Third', 'First', 'Third'],
     ['first', 'second', 'third']),
    (['second', 'first'], ['First', 'Second'], ['second', 'first']),
    (['first'], [], []),
])
def test_cli_reconciles_existing_playlist(tmp_path, prior, tracks, expected):
    repo = tmp_path / 'repo'
    playlists = repo / 'playlists'
    playlists.mkdir(parents=True)
    dest = playlists / 'Test.m3u'
    def render(names):
        return '#EXTM3U\n' + ''.join(f'artist/album/{n}.mp3\n' for n in names)
    if prior is not None:
        dest.write_text(render(prior).rstrip('\n'), encoding='utf-8')
    catalog = tmp_path / 'files.csv'
    catalog.write_text('path\n' + ''.join(
        f'artist/album/{n}.mp3\n' for n in ['first', 'second', 'third']), encoding='utf-8')
    dump = tmp_path / 'dump.json'
    dump.write_text(json.dumps([{'name': 'Test', 'tracks': [
        f'/Artist/Album/{n}.mp3' for n in tracks]}]), encoding='utf-8')
    args = ['--from', str(dump), '--csv', str(catalog), '--repo', str(repo),
            '--music-root', '']
    before = dest.read_bytes() if dest.exists() else None
    result = _run_cli([*args, '--dry-run'])
    assert result.returncode == 0, result.stderr
    assert (dest.read_bytes() if dest.exists() else None) == before
    assert ('New playlist: Test' if prior is None else 'Existing playlist: Test') in result.stdout
    assert f'Apple Music: {len(tracks)} tracks' in result.stdout
    assert f'Exported file: {len(prior or [])} tracks' in result.stdout
    old = set(prior or [])
    assert f'add {len(set(expected) - old)}, delete {len(prior or []) - len(old & set(expected))}' in result.stdout
    result = _run_cli(args)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == render(expected).encode('utf-8')
    timestamp = dest.stat().st_mtime_ns
    result = _run_cli(args)
    assert result.returncode == 0, result.stderr
    assert 'wrote 0 playlist file(s), unchanged 1' in result.stdout
    assert dest.stat().st_mtime_ns == timestamp
    preview = _run_cli([*args, '--dry-run'])
    assert 'Exported file changes: add 0, delete 0' in preview.stdout
    assert 'Formatting:' not in preview.stdout


def test_cli_preserves_existing_playlist_when_all_tracks_unmatched(tmp_path):
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    dest = playlists / 'Test.m3u'
    original = b'#EXTM3U\nartist/album/known.mp3\n'
    dest.write_bytes(original)
    catalog = tmp_path / 'files.csv'
    catalog.write_text('path\nartist/album/known.mp3\n', encoding='utf-8')
    dump = tmp_path / 'dump.json'
    dump.write_text(json.dumps([{'name': 'Test', 'tracks': [
        '/Artist/Album/Unknown.mp3']}]), encoding='utf-8')
    result = _run_cli(['--from', str(dump), '--csv', str(catalog),
                       '--repo', str(tmp_path), '--music-root', ''])
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == original
    assert 'kept existing' in result.stdout


@pytest.mark.parametrize('dry_run', [False, True])
def test_single_playlist_picker_scopes_export(tmp_path, monkeypatch, capsys, dry_run):
    from unittest.mock import patch
    name = '🎧 Dance Hindi'
    catalog = tmp_path / 'files.csv'
    catalog.write_text('path\n', encoding='utf-8')
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    other = playlists / 'Other.m3u'
    other.write_bytes(b'#EXTM3U\nkeep-this.mp3\n')
    before = other.stat().st_mtime_ns
    argv = ['playlist-sync.py', '--pick', '--repo', str(tmp_path),
            '--csv', str(catalog), '--music-root', '']
    if dry_run:
        argv.append('--dry-run')
    monkeypatch.setattr(sys, 'argv', argv)
    with patch.object(ps, 'fetch_playlists', return_value=[
            {'name': name, 'tracks': []}, {'name': 'Other', 'tracks': []}]), \
         patch.object(ps.subprocess, 'run', return_value=subprocess.CompletedProcess(
             ['fzf'], 0, name + '\0')) as picker:
        ps.main()
        assert name + '\0' in picker.call_args.kwargs['input']
    assert other.read_bytes() == b'#EXTM3U\nkeep-this.mp3\n'
    assert other.stat().st_mtime_ns == before
    selected = playlists / f'{name}.m3u'
    if dry_run:
        assert not selected.exists()
        assert f'New playlist: {name}' in capsys.readouterr().out
    else:
        assert selected.read_bytes() == b'#EXTM3U\n'


def test_single_playlist_cancel_and_prune_guard(tmp_path, monkeypatch):
    from unittest.mock import patch
    catalog = tmp_path / 'files.csv'
    catalog.write_text('path\n')
    argv = ['playlist-sync.py', '--pick', '--repo', str(tmp_path),
            '--csv', str(catalog), '--music-root', '']
    monkeypatch.setattr(sys, 'argv', argv)
    with patch.object(ps, 'fetch_playlists', return_value=[{'name': 'Test', 'tracks': []}]), \
         patch.object(ps.subprocess, 'run', return_value=subprocess.CompletedProcess(['fzf'], 130, '')):
        ps.main()
    assert not (tmp_path / 'playlists').exists()
    monkeypatch.setattr(sys, 'argv', [*argv, '--prune'])
    with pytest.raises(SystemExit) as error:
        ps.main()
    assert error.value.code == 2


def test_fetch_names_and_one_pass_scoped_arguments():
    from unittest.mock import patch
    with patch.object(ps.subprocess, 'run', return_value=subprocess.CompletedProcess(
            [], 0, '[]')) as run:
        ps.fetch_playlists(None, names_only=True)
        assert run.call_args.args[0][-1] == '--names'
        ps.fetch_playlists(None, one='🎧 Dance Hindi')
        assert run.call_args.args[0][-2:] == ['--one', '🎧 Dance Hindi']
