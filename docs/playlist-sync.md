# playlist-sync

Regenerate `$MUSIC_METADATA_DIR/playlists/*.m3u` from Apple Music.app.
Menu **4·1 / 4·2 / 4·3**.

```
bin/fetch.js           JXA - dumps every Music.app user playlist (name + track paths) as JSON
bin/playlist-sync.py   matches those onto $FILES_TREE and writes playlists/*.m3u
```

Writes into `music-metadata/playlists/`; reads `music-metadata/files.tree`.
Live fetch needs macOS + Music.app; `--from <dump>` works anywhere.

## What it does

```
Music.app ──fetch.js──► [{name, tracks:[/abs/path.mp3, …]}, …]
                                   │
                        sync.py: fuzzy-match each path
                                   │  against files.tree (slug namespace)
                                   ▼
                   ../playlists/<name>.m3u   (#EXTM3U + repo-relative slug paths)
```

- Pulls **all** user playlists live from Music.app each run (smart playlists
  included; folder playlists and cloud-only tracks skipped).
- Writes one clean `.m3u` per playlist - single `#EXTM3U` header, tracks in
  Music.app's playlist order, each repo path once (deduped).
- Overwrites `playlists/*.m3u` in place. `../files.tree` is the match target
  and is **not** modified; `../lyrics/` is never touched.

## Run

```bash
bin/playlist-sync.py            # regenerate playlists/ in place
bin/playlist-sync.py --dry-run  # report only, write nothing
bin/playlist-sync.py --misses   # also list every unmatched track
bin/playlist-sync.py --prune    # delete playlists/*.m3u that have no
                                         # matching Music.app playlist
bin/playlist-sync.py --from d.json   # use a saved fetch.js dump
bin/playlist-sync.py --from -        # read a dump from stdin
```

Fetching from Music.app takes ~2-3 min (~215 playlists, ~23k track lookups over
Apple Events). macOS + Music.app required unless `--from` is given. First run
may raise a "let Terminal control Music" prompt - accept it.

### Progress

`fetch.js` prints one line to **stderr** as it reads each playlist, so the pass
is not a silent wait:

```
[ 47/214] Rock Hindi  (52 tracks)
[ 48/214] 🎧 English  (306 tracks)
```

`sync.py` then prints a line per playlist as it matches. For an actual progress
bar, stream NDJSON through `pv`:

```bash
bin/fetch.js --ndjson | pv -l | \
    bin/playlist-sync.py --from -
```

To capture a dump for offline / repeated runs:

```bash
bin/fetch.js > /tmp/pl.json   # progress on stderr
bin/playlist-sync.py --from /tmp/pl.json --dry-run
```

### No duplicate tracks

Each track lands in a `.m3u` **once**, even when Music.app:

- lists the same track twice in a playlist,
- has two different tracks that resolve to the same repo file, or
- has two playlists with the same name (their tracks are merged into one file).

The summary line reports how many duplicates were dropped.

## Matching

Ported from the Swift tool, plus an NFC fix. For each Apple path
`…/Artist/Album/NN Title.ext`:

| step | rule |
|---|---|
| `normalize` | NFC-fold, lowercase, every char outside `[a-z0-9@]` → `-`, collapse `-` |
| apple track name | drop extension, strip leading `D-NN ` / `NN ` |
| tree filename | drop extension, strip `-[mid-…]` suffix and leading `D-NN-` / `NN-` |
| lookup 1 | `artist \| album \| track` |
| lookup 2 | `artist \| track` (album dropped - handles truncated album dirs) |
| tie-break 1 | if several tree files match and exactly one has the same track number as the Apple file, use that (same title appears twice on the album - a reprise, two singers) |
| tie-break 2 | otherwise, keep the one already in that `.m3u` (stable diffs), else first in `files.tree` order |
| dedupe | each resolved repo path written once per playlist (see "No duplicate tracks" above) |

Idempotent: a second run with an unchanged library rewrites the files
byte-for-byte identically.

### Guards

- A playlist that matches **0 tracks** does **not** overwrite an existing
  non-empty `.m3u` (folder playlists in Music.app report as empty). Pass
  `--allow-empty` to override.
- Playlists that lose >50 % of their tracks are printed as `⚠` warnings for you
  to eyeball before committing.
- `.m3u` files with no corresponding Music.app playlist are listed and left
  alone unless `--prune`.

### Known misses (won't match, by design)

- Tracks absent from `files.tree` - spa/meditation music, spoken word,
  newer soundtracks not yet in the MPD library. `files.tree` is a frozen
  snapshot; regenerate it on the Linux box to pick these up.
- Apple's `_` stand-in for `:` `/` `'` in a few names (`A Hard Day_s Night`).
- Heavy classical/raga naming (`Indian Classical`, `Thumri`) - check these by
  hand; they're flagged as warnings.

Run `--misses` to see the full unmatched list (~5k lines, mostly the first
category).

## Relation to the old app and to play-stats/

- `tools/playlist-sync-manager/` (the Swift app + `library.map`) is superseded
  by this. Its metadata-CSV mode (play count / rating per track) is covered
  better by `../play-stats/`.
- `tools/playlist-sync-manager/apple-music-playlists/*.m3u8` are raw Apple
  exports; this script goes straight to Music.app instead.
