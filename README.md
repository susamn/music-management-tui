# music-tui

One indexed terminal menu over the whole `music-metadata` workflow: hand-syncing
`.lrc` lyrics, fetching lyrics from LRCLIB, regenerating the Apple Music
playlists and play-stats, pushing freshly-made lyrics out to the music
collection or Google Drive, and reading / diffing / editing the `mpdtui`
database.

```
music-tui/
  music-tui.sh     entrypoint -- loads config, launches the menu
  engine.py        the indexed-TUI engine (vendored from the tui-creator skill)
  menu.tmpl.json   the menu spec
  config.example   template for ~/.config/music-tui/config
  bin/             the scripts each menu item runs
  docs/            architecture + one page per sub-tool
```

## Run

```bash
~/workspace/projects/music-tui/music-tui.sh
```

First run writes `~/.config/music-tui/config` from `config.example` (seeding
`MUSIC_DIR` from `~/.config/mpdtui/config` if present). Edit that file, or use
the **Settings** section in the menu, to point at your paths.

Type e.g. `1` then `12` for an item, `d` to dry-run (shows the resolved
command), `t` for the theme, `0` to quit.

| # | section | what |
|---|---|---|
| 1 | Lyrics | hand-time `.lrc`, fetch from LRCLIB (`.txt`/`.lrc`/`.elrc`), refresh/browse the still-missing report, push to the music dir or Drive (uncommitted or a past commit, with a pushed-commit ledger) |
| 2 | Wiki | fetch per-track background from online sources, push into the music collection |
| 3 | Apple Music Sync | regenerate `playlists/*.m3u` and `play_stats.csv` from Apple Music.app; regenerate `files.csv` from `$GDRIVE_MUSIC_DIR`; write `.m3u` additions back into Apple Music.app, matched by `MCATALOGID` |
| 4 | MCATALOGID | status, backfill missing tags from filenames, assign new ids for tracks with none |
| 5 | mpdtui | summary, browse (by rating/mark/tag), orphans, diff vs library/play_stats, backup/restore, marks, tags, import ratings, prune |
| 6 | Settings | show / edit the config |

## Docs

- [`docs/architecture.md`](docs/architecture.md) — the two repos, the config, the data flow, every `bin/` script
- [`docs/lrc-sync.md`](docs/lrc-sync.md) · [`docs/lyrics-reports.md`](docs/lyrics-reports.md) · [`docs/playlist-sync.md`](docs/playlist-sync.md) · [`docs/play-stats.md`](docs/play-stats.md)
- [`docs/mpdtui-db.md`](docs/mpdtui-db.md) — read / diff / write the mpdtui database
- [`docs/push-to-drive.md`](docs/push-to-drive.md) — where pushed lyrics land
- [`docs/mac-playlist-writeback.md`](docs/mac-playlist-writeback.md) — `.m3u` edits → Apple Music.app, matched by `MCATALOGID`
- [`docs/mcatalogid.md`](docs/mcatalogid.md) — the `MCATALOGID` tag: backfill, new-id assignment, one-time Apple Music sync

## Layout

This is the tooling half of a two-repo split:

| repo | holds |
|---|---|
| `music-metadata` (GitHub, travels with the music) | `lyrics/`, `playlists/`, `lyrics-reports/`, `play-stats/play_stats.csv`, `files.csv` -- **data only** |
| `music-tui` (this repo, local) | every script, all docs, the menu -- **tooling only** |

See [`docs/architecture.md`](docs/architecture.md).

## Requirements

Python 3 stdlib only, except `bin/mcatalogid-backfill.py`,
`bin/apple-music-tag-sync.py`, `bin/generate-files-csv.py`, and
`bin/playlist-writeback.py`, which need `mutagen` (`pip install --user
mutagen`) to edit ID3/MP4 tags (or, for the read-only ones, just read them)
in place without touching audio data — `ffmpeg -c copy -metadata ...` was
tested and found to silently truncate a real file on an unusual mp3 stream,
so it's never used for tag writes.
`fzf`, `sqlite3`, `rclone`, `ffprobe` for some items (the menu checks and
tells you). The Apple Music items (`playlist-sync`, `play-stats` live fetch,
`apple-music-tag-sync`, `playlist-writeback`) need macOS + Music.app;
`playlist-sync`'s `--from` / merge modes work anywhere.

## Tests

`pytest` (`pip install --user pytest`) - a dev-only dependency, not needed to
run the tool itself. `tests/conftest.py` generates tiny real audio fixtures
via `ffmpeg` once per run and caches them in `tests/_fixture_audio/`.

```bash
python3 -m pytest tests/ -v
```
