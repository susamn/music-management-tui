# play-stats

Snapshot **rating** and **play count** for every track in Apple Music.app
(`Music.app`) into a CSV, and keep that CSV up to date by re-running one
command. Read-only against Music.app — it never changes your library.

Menu **3·4 / 3·5** (Apple Music Sync).

```
bin/extract.js          JXA - dumps a fresh snapshot of Music.app as CSV to stdout
bin/play-stats-sync.py  runs extract.js and MERGES it into $PLAY_STATS_CSV
```

`$PLAY_STATS_CSV` defaults to `music-metadata/play-stats/play_stats.csv` (the
tracked file, safe to hand-annotate). Needs macOS + Music.app.

## Where the data comes from

Apple Music keeps rating / play count in
`~/Music/Music/Music Library.musiclibrary/Library.musicdb`, an undocumented
binary blob. The only supported way to read it is to **script Music.app**;
`extract.js` does that via JXA (`osascript -l JavaScript`). No `pip install`,
no Music.app UI steps, no "Share Library XML" toggle.

Fields pulled per track: `persistent ID`, `location`, `artist/album/title`,
`track/disc number`, `rating` (+ `rating kind`), `played count`,
`skipped count`, `played date`, `cloud status`.

## Run

```bash
bin/play-stats-sync.py            # refresh play_stats.csv in place
bin/play-stats-sync.py --dry-run  # show what would change, write nothing
bin/play-stats-sync.py --out /tmp/x.csv
```

Takes ~7 s for an ~8k-track library. Requires macOS with Music.app; the first
run may raise a macOS "allow Terminal to control Music" prompt — accept it.

### What merging does

Rows are keyed on `persistent_id` (Music.app's stable per-track ID — survives
renames, moves and re-tagging, never reused).

| situation | result |
|---|---|
| track in Music, new to the CSV | row added, `stale=0` |
| track in both | the columns `extract.js` owns are overwritten with fresh values; **any columns you added by hand are left untouched** |
| row in the CSV, no longer in Music | kept, `stale=1` (so hand notes never silently disappear) |

So you can open `play_stats.csv`, add your own columns (`my_note`,
`playlist_wanted`, whatever), commit it, and every later `sync.py` run keeps
them while refreshing the rating/play numbers.

`extract.js` is the source of truth for these columns — editing `rating` or
`play_count` in the CSV by hand will be reverted on the next sync. Rate the
track in Music.app instead.

## Columns

| column | meaning |
|---|---|
| `persistent_id` | merge key. Music.app persistent ID. |
| `path_abs` | absolute file path. Empty for cloud / Apple Music streaming entries. |
| `path_lib` | `path_abs` minus the media-root prefix — the same relative path MPD's `music_directory` uses on this Mac. |
| `path_slug` | lowercase/hyphen slug of `path_lib` (extension kept). Grep hint. |
| `join_key` | `path_slug` minus extension and the leading track number: `a-r-rahman/dil-se/satrangi-re`. Best column for joining to `lyrics/` and `playlists/`. |
| `artist` `album` `title` | track tags (note: `artist` is the *track* artist, so it carries featured artists — the repo folder uses the album artist). |
| `track_number` `disc_number` | |
| `rating` | 0–100, 20 per star. |
| `rating_kind` | `user` = actually rated. `computed` = inherited from the album rating; **treat as unrated**. |
| `play_count` `skip_count` | |
| `played_date` | ISO-8601, or empty. |
| `cloud_status` | `uploaded` / `matched` / `subscription` / `purchased` / `unknown` … |
| `stale` | `1` if the row is no longer reported by Music.app. |

Current library: ~8,240 tracks, ~144 user-rated, ~1,870 played at least once.

## Joining play stats back to this repo

`lyrics/<artist>/<album>/<NN-title>-[mid-####].{txt,lrc}` and the `.m3u`
playlists use slugified paths with a leading track number and a `-[mid-####]`
suffix that Music.app has no equivalent for. To match a repo path to a CSV
row, normalise the repo side and compare on `join_key`:

```python
import csv, re
def repo_join_key(rel):                     # rel = path under lyrics/ or a playlist line
    rel = re.sub(r'\.(txt|lrc|mp3|m4a|flac)$', '', rel)
    d, _, base = rel.rpartition('/')
    base = re.sub(r'-\[mid-[^\]]*\]$', '', base)   # drop -[mid-####]
    base = re.sub(r'^\d+(-\d+)?-', '', base)       # drop leading NN- / D-NN-
    return f'{d}/{base}'

stats = {r['join_key']: r for r in csv.DictReader(open('$PLAY_STATS_CSV'))
         if r['join_key']}
```

About 85% of tracks that have a repo lyrics file join this way. The rest miss
on featured-artist folders, `?`/`!` in album names, or `(feat. …)` parts —
fall back to `path_slug` grep or match on `title` + `album`.

## Notes / limits

- `.m4p` files under `Apple Music/` are DRM downloads — they get a `path_lib`
  but no repo counterpart.
- `rating` reads album-inherited ratings as `computed`; there is no way to see
  a per-track rating that was never set.
- Re-run is cheap and idempotent — running it twice with nothing changed in
  Music produces a byte-identical CSV.
