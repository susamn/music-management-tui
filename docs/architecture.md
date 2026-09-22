# Architecture

## Two repos

| repo | is | holds |
|---|---|---|
| **`music-metadata`** | data, GitHub-backed, travels with the music collection | `lyrics/`, `playlists/`, `lyrics-reports/` (reports + `meta_genre.json`), `play-stats/play_stats.csv`, `files.csv` |
| **`music-tui`** (this one) | tooling, local git | every script, all docs, the menu |

They were one repo. The tooling was a grab-bag of folders each run by hand
(`lrc-sync/`, `playlist-sync/`, `play-stats/`, `lyrics-reports/refresh.py`),
plus three lyrics-fetch scripts off in the dotfiles with hard-coded paths.
Splitting it means `music-metadata` is purely the thing that gets synced next
to the music, and the tooling is one project with one entry point and one
config.

`music-tui` never writes inside its own tree at runtime — outputs land in
`music-metadata` (lyrics, playlists, reports) or the music collection or the
mpdtui DB, all located via config.

## Config — `~/.config/music-tui/config`

`music-tui.sh` creates it from `config.example` on first run (seeding
`MUSIC_DIR` from `~/.config/mpdtui/config`), sources it, exports every key, and
generates `~/.config/music-tui/menu.json` from `menu.tmpl.json` (only `@THEME@`
is substituted; the `$VARS` in each `cmd` are expanded by the engine's
`shell=True` at run time).

| key | default | read by |
|---|---|---|
| `MUSIC_METADATA_DIR` | `~/Music/music-metadata` | everything that reads/writes lyrics, playlists, reports |
| `MUSIC_DIR` | mpdtui's `music_dir`, else `~/Music/susamn-music-collection` | `fetch-lyrics`, `lyrics-push to-music`, `mpdtui.py` diff/orphans/prune |
| `MPDTUI_DB` | `~/.config/mpdtui/mpdtui.db` | `mpdtui.py`, `mpdtui-db-backup.sh` |
| `PLAY_STATS_CSV` | `$MUSIC_METADATA_DIR/play-stats/play_stats.csv` | `play-stats-sync`, `mpdtui.py diff-stats` / `import-ratings` |
| `FILES_CSV` | `$MUSIC_METADATA_DIR/files.csv` | `playlist-sync`, `generate-files-csv` |
| `GDRIVE_MUSIC_DIR` | none - required | `generate-files-csv`, `playlist-sync` (existence check), `mcatalogid-backfill`, `apple-music-tag-sync` |
| `APPLE_MUSIC_DIR` | `~/Music/Music/Media.localized/Music` | `apple-music-tag-sync` |
| `RCLONE_MUSIC_REMOTE_PATH` | `gdrive:Media/Music` | `lyrics-push to-drive` |
| `THEME` | `obsidian` | the menu |

## Data flow

```
                 Apple Music.app  (macOS only)
                    │        │
          fetch.js  │        │  extract.js
                    ▼        ▼
       playlist-sync.py   play-stats-sync.py
                    │        │
                    ▼        ▼
   music-metadata/playlists/   music-metadata/play-stats/play_stats.csv
                                          │
                                          │  mpdtui.py diff-stats / import-ratings
                                          ▼
   LRCLIB ──fetch-lyrics.py──►  music-metadata/lyrics/<rel>.{txt,lrc,elrc}
                                          ▲        │
                       lrc-sync.py  ──────┘        │  lyrics-push.py  (uncommitted, or by commit)
                    (hand-time vs MPD)             ├──► $MUSIC_DIR/<rel>   (sidecar, for testing)
                                                   └──► $RCLONE_MUSIC_REMOTE_PATH/<rel>  (Drive)

   $MUSIC_DIR (mp3 + sidecars)  ◄──►  ~/.config/mpdtui/mpdtui.db
                                   mpdtui.py: summary / list / diff-library / mark / tag / prune
                                   refresh-reports.py: lyrics coverage vs meta_genre.json
```

## `bin/`

| file | was | input | output | paths from |
|---|---|---|---|---|
| `lrc-sync.py` | `lrc-sync/lrc-sync.py` | MPD playback + a `.txt` | a synced `.lrc` | `--lyrics` / `$MUSIC_METADATA_DIR` |
| `fetch-lyrics.py` | dotfiles `fetch_{lyrics,lrc,elrc}.py` | a music tree + LRCLIB | `.txt`/`.lrc`/`.elrc` at mirrored path | `--music`/`--out`, `$MUSIC_DIR`/`$MUSIC_METADATA_DIR` |
| `refresh-reports.py` | `lyrics-reports/refresh.py` | `meta_genre.json` + lyrics on disk | `still_missing_*` + `README.md` in the reports dir | `$MUSIC_METADATA_DIR` |
| `generate-files-csv.py` | new | a music dir (default `$GDRIVE_MUSIC_DIR`) | `files.csv` | `--root`/`--out`, `$GDRIVE_MUSIC_DIR`/`$FILES_CSV` |
| `playlist-sync.py` + `fetch.js` | `playlist-sync/` | Music.app (or a dump) + `files.csv` | `playlists/*.m3u` | `$MUSIC_METADATA_DIR` / `$FILES_CSV` / `$GDRIVE_MUSIC_DIR` |
| `play-stats-sync.py` + `extract.js` | `play-stats/` | Music.app | merged `play_stats.csv` | `$PLAY_STATS_CSV` |
| `mpdtui.py` | new | `mpdtui.db` (+ library, + `play_stats.csv`) | reports / diffs / DB edits | `$MPDTUI_DB` / `$MUSIC_DIR` / `$PLAY_STATS_CSV` |
| `mpdtui-db-backup.sh` | dotfiles, verbatim | `mpdtui.db` | rclone remote snapshot | `$MPDTUI_DB` |
| `lyrics-push.py` | new | `git status` / recent commits of `music-metadata/lyrics` | copies to `$MUSIC_DIR` / Drive; appends `lyrics-reports/drive-push.log` | `$MUSIC_METADATA_DIR` / `$MUSIC_DIR` / `$RCLONE_MUSIC_REMOTE_PATH` |
| `show-config.sh` | new | the config | resolved paths + existence checks | — |

## The engine

`engine.py` is `assets/tui_template.py` from the `tui-creator` skill, **verbatim**
except for one branch in `run_task()`: an item with `"interactive": true` runs
with the real terminal attached (no output capture, no spinner, no pager) so
`lrc-sync.py`'s curses UI, `$EDITOR`, `read -rp` prompts and `fzf` pickers piped
into a consumer all work. Plain items keep the capture-and-page behaviour;
`"use_fzf": true` items keep the browse-in-fzf behaviour.

It is **vendored**, not referenced from `$TOOLS_PATH`, because this project is
self-contained and lives in `~/workspace/projects/` with the other `*tui`
tools, not under `$TOOLS_PATH`.

## Menu → script

| section | items | scripts |
|---|---|---|
| 1 Lyrics | lrc-sync, refresh/browse missing, fetch (plain/lrc/elrc/one sub-path), push (uncommitted or past commits → music dir / Drive, ledger) | `lrc-sync.py`, `refresh-reports.py`, `fetch-lyrics.py`, `lyrics-push.py` |
| 2 Wiki | fetch/retry batches, push to music dir / Drive | `wiki-fetch.py`, `wiki-push.py` |
| 3 Apple Music Sync | playlist-sync live/dump/dry, play-stats live/dry, regenerate files.csv | `playlist-sync.py`, `play-stats-sync.py`, `generate-files-csv.py` |
| 4 MCATALOGID | status, backfill/assign dry-run and real | `mcatalogid-backfill.py` |
| 5 mpdtui | summary, browse, 5★, orphans, diff vs library/play_stats, backup/restore, mark, tag, import ratings, prune | `mpdtui.py`, `mpdtui-db-backup.sh` |
| 6 Settings | show config, edit config | `show-config.sh` |

## Future

[`mac-playlist-writeback.md`](mac-playlist-writeback.md) — the wanted but
not-yet-built reverse direction: reconcile `.m3u` edits made on Linux back into
Apple Music.app.
