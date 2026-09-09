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

The menu clamps to its 8 sections; type e.g. `1` then `12` for an item, `d` to
dry-run (shows the resolved command), `t` for the theme, `0` to quit.

| # | section | what |
|---|---|---|
| 1 | Lyrics — sync & report | `lrc-sync` hand-timing, refresh the still-missing lists, browse them |
| 2 | Lyrics — fetch from LRCLIB | plain `.txt` / synced `.lrc` / word-synced `.elrc`, whole tree or one sub-path |
| 3 | Lyrics — push uncommitted | list what git hasn't committed, copy picks next to the mp3 or to Google Drive |
| 4 | Playlists & play-stats | regenerate `playlists/*.m3u` and `play_stats.csv` from Apple Music.app |
| 5 | mpdtui DB — read | summary, browse by rating / mark / tag, orphan rows |
| 6 | mpdtui DB — diff | vs the music library, vs `play_stats.csv` |
| 7 | mpdtui DB — write | backup/restore, marks, tags, import ratings, prune — dry-run + backup first |
| 8 | Settings | show / edit the config |

## Docs

- [`docs/architecture.md`](docs/architecture.md) — the two repos, the config, the data flow, every `bin/` script
- [`docs/lrc-sync.md`](docs/lrc-sync.md) · [`docs/lyrics-reports.md`](docs/lyrics-reports.md) · [`docs/playlist-sync.md`](docs/playlist-sync.md) · [`docs/play-stats.md`](docs/play-stats.md)
- [`docs/mpdtui-db.md`](docs/mpdtui-db.md) — read / diff / write the mpdtui database
- [`docs/push-to-drive.md`](docs/push-to-drive.md) — where pushed lyrics land
- [`docs/mac-playlist-writeback.md`](docs/mac-playlist-writeback.md) — future: `.m3u` edits → Apple Music.app

## Layout

This is the tooling half of a two-repo split:

| repo | holds |
|---|---|
| `music-metadata` (GitHub, travels with the music) | `lyrics/`, `playlists/`, `lyrics-reports/`, `play-stats/play_stats.csv`, `files.tree` -- **data only** |
| `music-tui` (this repo, local) | every script, all docs, the menu -- **tooling only** |

See [`docs/architecture.md`](docs/architecture.md).

## Requirements

Python 3 stdlib only. `fzf`, `sqlite3`, `rclone`, `ffprobe` for some items
(the menu checks and tells you). The Apple Music items (`playlist-sync`,
`play-stats` live fetch) need macOS + Music.app; their `--from` / merge modes
work anywhere.
