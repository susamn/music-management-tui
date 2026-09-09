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
