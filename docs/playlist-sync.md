# playlist-sync

Regenerate `$MUSIC_METADATA_DIR/playlists/*.m3u` from Apple Music.app.
Menu **5·1 / 5·2 / 5·3** (files.csv generation: **5·6 / 5·7**).

```
bin/fetch.js              JXA - dumps every Music.app user playlist (name + track paths) as JSON
bin/generate-files-csv.py scans a music dir into files.csv - the match namespace
bin/playlist-sync.py      matches Apple paths onto $FILES_CSV and writes playlists/*.m3u
```

Writes into `music-metadata/playlists/`; reads `music-metadata/files.csv`.
Live fetch needs macOS + Music.app; `--from <dump>` works anywhere.

## What it does

```
Music.app ──fetch.js──► [{name, tracks:[/abs/path.mp3, …]}, …]
                                   │
                        sync.py: fuzzy-match each path
                                   │  against files.csv (slug namespace)
                                   │  + verify the match still exists on disk
                                   ▼
                   ../playlists/<name>.m3u   (#EXTM3U + repo-relative slug paths)
```

- Pulls **all** user playlists live from Music.app each run (smart playlists
  included; folder playlists and cloud-only tracks skipped).
- Writes one clean `.m3u` per playlist - single `#EXTM3U` header, tracks in
  Music.app's playlist order, each repo path once (deduped).
- Overwrites `playlists/*.m3u` in place. `../files.csv` is the match target
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
bin/playlist-sync.py --music-root DIR  # existence-check against DIR instead
                                        # of $GDRIVE_MUSIC_DIR; '' to skip it
```

### Generating files.csv

`files.csv` used to be `files.tree`, a text snapshot regenerated only on a
separate machine (this repo had no script that produced it) - which is
exactly why it went stale: 224 real Drive tracks weren't in it at all, and 98
of its entries no longer existed. `bin/generate-files-csv.py` replaces that:
any machine can produce it from whatever local music directory it has.

```bash
bin/generate-files-csv.py            # regenerate $FILES_CSV from $GDRIVE_MUSIC_DIR
bin/generate-files-csv.py --dry-run  # report the count only, write nothing
bin/generate-files-csv.py --root DIR # scan a different directory
```

Columns: `path,artist,album,filename,ext,mcatalogid` - `path` is the same
slug format the matching engine has always used; `mcatalogid` is read via
`mutagen` during the same scan (blank if missing or still the `"catalog"`
placeholder - see `docs/mcatalogid.md`), so ID-based matching is possible
later without a second pass over the library. Full regeneration every run,
not a merge - this is a snapshot, not history like `play_stats.csv`.

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
| tie-break 1 | if several tree files match, keep the one already in that `.m3u` (stable diffs) - an established pick always wins, even over a plausible-looking track number |
| tie-break 2 | otherwise, if exactly one of them has the same track number as the Apple file (compared as integers, ignoring zero-padding), use that - same title appears more than once for this artist (a reprise, two singers, the same song on two pressings), and the number is usually the only thing that tells them apart, even across lookup 2 where the album string itself doesn't text-match |
| tie-break 3 | otherwise, first in `files.csv` order |
| existence check | a match not actually present under `$GDRIVE_MUSIC_DIR` (or `--music-root`) is a `phantom`, not a silent match - `files.csv` is a snapshot and can go stale between generation and use |
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

- Tracks absent from `files.csv` - spa/meditation music, spoken word, or
  anything added to the library since the last regenerate. Run
  **5·6/5·7** (`bin/generate-files-csv.py`) to pick these up; unlike the old
  `files.tree`, this doesn't require a different machine.
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
