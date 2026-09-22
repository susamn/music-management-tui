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
    apple_by_id = {"1000": ["/apple/song.mp3"]}

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

    assert result == {"555": [str(target)]}


# --- plan_for_playlist: the diff/classification logic ----------------------

def test_plan_to_add_when_missing_from_apple(tmp_path):
    m3u = tmp_path / "Test.m3u"
    m3u.write_text("#EXTM3U\na/b.mp3\n")
    csv_ids = {"a/b.mp3": "1000"}
    apple_by_id = {"1000": ["/apple/path.mp3"]}

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
    apple_by_id = {"1000": ["/apple/path.mp3"]}

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
    apple_by_id = {"1000": ["/apple/path.mp3"]}

    with patch.object(wb, "read_playlist_locations", return_value=None):
        plan = wb.plan_for_playlist(m3u, csv_ids, apple_by_id)

    assert plan["current_found"] is False
    assert plan["to_add_paths"] == ["/apple/path.mp3"]


def test_plan_preserves_m3u_order_and_deduplicates(tmp_path):
    m3u = tmp_path / 'Test.m3u'
    m3u.write_text('#EXTM3U\nb.mp3\na.mp3\nb.mp3\n')
    with patch.object(wb, 'read_playlist_locations', return_value=[]):
        plan = wb.plan_for_playlist(m3u, {'a.mp3': '1', 'b.mp3': '2'},
                                    {'1': ['/apple/a.mp3'], '2': ['/apple/b.mp3']})
    assert plan['to_add_paths'] == ['/apple/b.mp3', '/apple/a.mp3']


def test_scan_excludes_unknown_files_and_preserves_duplicate_candidates(build_tree):
    import pytest
    root = build_tree({'a.mp3': None, 'b.mp3': None})
    with patch.object(wb, 'read_tag', return_value='same-id'):
        assert wb.scan_apple_by_mcatalogid(root, set()) == {}
        assert wb.scan_apple_by_mcatalogid(root, {(root / 'a.mp3').resolve()}) == {
            'same-id': [str(root / 'a.mp3')]}
        assert sorted(wb.scan_apple_by_mcatalogid(root)['same-id']) == sorted(
            [str(root / 'a.mp3'), str(root / 'b.mp3')])


def test_dry_run_reports_create_update_unchanged_blocked_without_writes(tmp_path, monkeypatch, capsys):
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    for name in ['New', 'Existing', 'Same', 'Blocked']:
        (playlists / f'{name}.m3u').write_text('#EXTM3U\na.mp3\n')
    catalog = tmp_path / 'files.csv'
    _write_csv(catalog, [{'path': 'a.mp3', 'mcatalogid': '1'}])
    monkeypatch.setattr('sys.argv', ['playlist-writeback.py', '--all', '--dry-run',
                                   '--repo', str(tmp_path), '--csv', str(catalog),
                                   '--apple-root', str(tmp_path)])
    def plan(path, *_):
        return {'target_name': path.stem, 'current_found': path.stem != 'New',
                'to_add_paths': ['/apple/a.mp3'] if path.stem in ['New', 'Existing'] else [],
                'unresolved': ['unknown.mp3'] if path.stem == 'Blocked' else [],
                'not_in_library': [], 'apple_count': 0, 'exported_count': 1}
    with patch.object(wb, 'read_library_locations', return_value=set()) as read_library, \
         patch.object(wb, 'scan_apple_by_mcatalogid', return_value={}), \
         patch.object(wb, 'plan_for_playlist', side_effect=plan), \
         patch.object(wb, 'apply_playlist') as apply:
        assert wb.main() == 1
        read_library.assert_called_once()
        apply.assert_not_called()
    output = capsys.readouterr().out
    for label in ['New playlist: New', 'Existing playlist: Existing',
                  'Existing playlist: Same', 'Existing playlist: Blocked',
                  'Blocked:', 'Apple Music changes: add 1, delete 0',
                  'Apple Music: 0 local tracks', 'Exported file: 1 tracks']:
        assert label in output


def test_apply_creates_empty_playlist(tmp_path, monkeypatch, capsys):
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    (playlists / 'Empty.m3u').write_text('#EXTM3U\n')
    catalog = tmp_path / 'files.csv'
    _write_csv(catalog, [])
    monkeypatch.setattr('sys.argv', ['playlist-writeback.py', '--all', '--repo', str(tmp_path),
                                   '--csv', str(catalog), '--apple-root', str(tmp_path)])
    with patch.object(wb, 'read_library_locations', return_value=set()), \
         patch.object(wb, 'read_playlist_locations', return_value=None), \
         patch.object(wb, 'apply_playlist', return_value={
             'added': [], 'created': True, 'already_present': [], 'not_in_library': []}) as apply:
        assert wb.main() == 0
        apply.assert_called_once_with('Empty', [])


def test_apply_reports_track_that_disappeared_from_library(tmp_path, monkeypatch, capsys):
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    (playlists / 'Test.m3u').write_text('#EXTM3U\na.mp3\n')
    catalog = tmp_path / 'files.csv'
    _write_csv(catalog, [{'path': 'a.mp3', 'mcatalogid': '1'}])
    monkeypatch.setattr('sys.argv', ['playlist-writeback.py', '--all', '--repo', str(tmp_path),
                                   '--csv', str(catalog), '--apple-root', str(tmp_path)])
    with patch.object(wb, 'read_library_locations', return_value=set()), \
         patch.object(wb, 'scan_apple_by_mcatalogid', return_value={'1': ['/apple/a.mp3']}), \
         patch.object(wb, 'read_playlist_locations', return_value=[]), \
         patch.object(wb, 'apply_playlist', return_value={
             'added': [], 'created': False, 'already_present': [],
             'not_in_library': ['/apple/a.mp3']}):
        assert wb.main() == 1
    assert 'no longer in Apple Music library: /apple/a.mp3' in capsys.readouterr().err


def test_jxa_apply_deduplicates_request_and_rejects_ambiguous_playlist():
    import shutil
    import subprocess
    import pytest
    from conftest import BIN
    if not shutil.which('node'):
        pytest.skip('Node is required for the JXA mock harness')
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const context = {ObjC: {import() {}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const playlist = {name: () => 'Test', tracks: {location: () => []}};
const playlists = [playlist];
playlists.name = () => ['Test'];
const tracks = [{}];
tracks.location = () => ['/a.mp3'];
let added = 0;
const Music = {userPlaylists: playlists, libraryPlaylists: [{fileTracks: tracks}],
               duplicate() { added++; }};
context.readFile = () => JSON.stringify({playlist: 'Test', add: ['/a.mp3', '/a.mp3']});
const result = JSON.parse(context.doApply(Music, 'request.json'));
assert.equal(added, 1);
assert.deepEqual(result.already_present, ['/a.mp3']);
playlists.name = () => ['Test', 'Test'];
assert.throws(() => context.findPlaylist(Music, 'Test'), /Ambiguous playlist/);
'''
    subprocess.run(['node', '-e', script, str(BIN / 'writeback.js')], check=True)


def test_duplicate_ids_only_block_needed_additions(tmp_path):
    m3u = tmp_path / 'Dance Hindi.m3u'
    m3u.write_text('#EXTM3U\na.mp3\n')
    index = {'1': ['/apple/a.mp3'], '2': ['/apple/b.mp3', '/apple/c.mp3']}
    with patch.object(wb, 'read_playlist_locations', return_value=[]):
        plan = wb.plan_for_playlist(m3u, {'a.mp3': '1'}, index)
        assert plan['to_add_paths'] == ['/apple/a.mp3']
        assert plan['ambiguous'] == {}
        plan = wb.plan_for_playlist(m3u, {'a.mp3': '2'}, index)
        assert plan['to_add_paths'] == []
        assert plan['ambiguous'] == {'2': ['/apple/b.mp3', '/apple/c.mp3']}
    with patch.object(wb, 'read_playlist_locations', return_value=['/apple/b.mp3']), \
         patch.object(wb, 'read_tag', return_value='2'):
        plan = wb.plan_for_playlist(m3u, {'a.mp3': '2'}, index)
        assert plan['to_add_paths'] == []
        assert plan['ambiguous'] == {}


def test_picker_preserves_emoji_name_and_cancellation(tmp_path, monkeypatch, capsys):
    import subprocess
    name = '🎧 Dance Hindi'
    playlists = tmp_path / 'playlists'
    playlists.mkdir()
    (playlists / f'{name}.m3u').write_text('#EXTM3U\n')
    catalog = tmp_path / 'files.csv'
    _write_csv(catalog, [])
    monkeypatch.setattr('sys.argv', ['playlist-writeback.py', '--pick', '--dry-run',
                                   '--repo', str(tmp_path), '--csv', str(catalog),
                                   '--apple-root', str(tmp_path)])
    with patch.object(wb.subprocess, 'run', return_value=subprocess.CompletedProcess(
            ['fzf'], 0, name + '\0')) as picker, \
         patch.object(wb, 'read_library_locations', return_value=set()), \
         patch.object(wb, 'read_playlist_locations', return_value=[]) as read, \
         patch.object(wb, 'apply_playlist') as apply:
        assert wb.main() == 0
        assert picker.call_args.kwargs['input'] == name + '\0'
        read.assert_called_once_with(name)
        apply.assert_not_called()
    assert f'Existing playlist: {name}' in capsys.readouterr().out
    with patch.object(wb.subprocess, 'run', return_value=subprocess.CompletedProcess(
            ['fzf'], 130, '')), patch.object(wb, 'read_library_locations') as library:
        assert wb.main() == 0
        library.assert_not_called()
