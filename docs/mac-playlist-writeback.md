# Mac playlist write-back

Menu **3·6–3·9**.

The single-playlist menu actions open an `fzf` picker of existing `.m3u`
names, including emoji. Select with Enter; Escape cancels without contacting
Music.app. From the command line, use `--pick --dry-run` or `--pick`.

```
bin/writeback.js          JXA - the only Music.app *write* in this repo
bin/playlist-writeback.py decides what to add, matched by MCATALOGID
```

## The want

`playlist-sync.py` only ever goes **Apple Music.app → `playlists/*.m3u`**.
After a `git pull` brings in a playlist edited elsewhere (or the
`m3u-union` merge driver - see `docs/playlist-sync.md` - combines two
machines' additions), those new tracks sit in the `.m3u` but never reach
Apple Music.app itself. This is the reverse direction: for each track a
`.m3u` wants that the corresponding Apple Music.app playlist doesn't have
yet, add it there.

## Matching: MCATALOGID, not persistent ID or fuzzy slug matching

An earlier draft of this doc proposed resolving `.m3u` slugs through a
`persistent_id` map built from `play_stats.csv` - `extract.js`'s own
comment notes persistent ID is "stable across renames/moves" but says
nothing about surviving a *reimport* into a different library, which it
doesn't. `MCATALOGID` (`docs/mcatalogid.md`) already solves the identity
problem more directly: a tag embedded in the file itself, present on both
the Google Drive copy and Apple Music's own managed copy (via
`apple-music-tag-sync.py`), already a column in `files.csv`. No separate
mapping file, no fuzzy artist/album/track collision risk - exact match, or
a clear "not yet imported to Apple Music" when there isn't one.

- Each `.m3u` line's id comes straight from `files.csv`.
- Each Apple Music track's id is read from the tag on the actual file at
  its `location()` - the same `read_tag()` approach already proven in
  `apple-music-tag-sync.py` / `mcatalogid-backfill.py`, duplicated here per
  this repo's standalone-script convention.

## `bin/writeback.js`

Two modes, both scoped to one named playlist (never the full 215-playlist
fetch `fetch.js`/`extract.js` do - unnecessary here, and slow):

```js
osascript -l JavaScript writeback.js --read "Playlist Name"
// {"found": true, "locations": [...]} or {"found": false}

osascript -l JavaScript writeback.js request.json
// request.json: {"playlist": "Name", "create_if_missing": true, "add": [...]}
// {"created": bool, "added": [...], "already_present": [...], "not_in_library": [...]}
```

Finds a playlist by iterating `Music.userPlaylists` (matching `fetch.js`'s
own pattern) rather than `Music.userPlaylists.byName(...)`, and - after a
transient mismatch was actually observed once in testing (a `pls[i]`
briefly returning a different playlist's data than that same index's
`name()` had just reported, root cause unconfirmed, not reproduced again
across a stress test of 6 concurrent reads) - **re-confirms** the resolved
playlist's own `.name()` matches before using it for anything, and throws
rather than silently continuing if it doesn't. Cheap, and it's the one
check standing between "found the right playlist" and writing into the
wrong one.

Locating tracks reuses `extract.js`'s own trick: bulk `.location()` throws
across the whole library because cloud-only tracks don't have one, but
works over `lib.fileTracks` - build a `location → track` index from there
once, and `Music.duplicate(track, {to: playlist})` (per the JXA surface
below) to add an existing library track to a playlist without duplicating
the file. `not_in_library` means the path isn't any track's location
anywhere in the whole library - never imported, nothing to add.

### JXA surface used

```js
Music.userPlaylists                                  // iterate + match by name()
Music.make({new: 'playlist', withProperties: {name: 'Rock Hindi'}})
pl.tracks.location()                                 // bulk, falls back per-track (fetch.js's pattern)
lib.fileTracks, ft.location()                         // location -> track index, extract.js's pattern
Music.duplicate(libraryTrack, {to: pl})               // add an existing library track
```

## `bin/playlist-writeback.py`

```bash
bin/playlist-writeback.py --one "Rock Hindi" --dry-run   # one playlist, report only
bin/playlist-writeback.py --one "Rock Hindi"              # one playlist, apply
bin/playlist-writeback.py --all --dry-run                 # every playlists/*.m3u, report only
bin/playlist-writeback.py --all                            # every playlists/*.m3u, apply
```

Target Apple Music playlist name = the `.m3u` filename minus extension -
the same sanitization `playlist-sync.py`'s `safe_filename()` already
applies (and already warns about on collision); no new lossiness here.

Per playlist: resolve every `.m3u` line's `mcatalogid` from `files.csv`
(anything blank is reported as `unresolved` - `files.csv`'s current 100%
coverage means this should be rare to never); read the Apple playlist's
current locations via `writeback.js --read`, read `mcatalogid` off each of
those files, diff against the `.m3u`'s wanted set; for every missing id,
resolve to an absolute `$APPLE_MUSIC_DIR` path by scanning for a file whose
tag matches (same approach as `generate-files-csv.py`, ~8s for the whole
library - no persistent cache needed here). Anything with no match
anywhere under `$APPLE_MUSIC_DIR` is `not_in_library`, never attempted.

**Additive only** - never removes a track from an Apple Music playlist,
same default this doc always intended and the same philosophy as the
`m3u-union` git merge driver. Auto-creates the Apple Music playlist if it
doesn't exist yet.

`--dry-run` calls the read-only `--library` and `--read` modes. It reports
`CREATE`, `UPDATE`, `UNCHANGED`, or `BLOCKED` with full playlist names,
the paths to add, and a summary of how many playlists would change.
The library query ensures a file on disk is actually registered with Music.
Duplicate catalog IDs are retained as candidates. They block only a playlist
that needs to add that ID; unrelated duplicates and IDs already present in the
playlist do not prevent planning. All ambiguous candidate paths are reported.
Duplicate target playlist names are rejected rather than choosing arbitrarily.

Unresolved entries or missing library tracks block that playlist and produce
a nonzero exit status. Other valid playlists can still proceed. Additions
preserve their order in the source M3U, with repeated IDs added only once.
Missing empty playlists are created as empty playlists. A library rejection
during apply is reported and produces a nonzero exit status.

No JXA write happens on a dry run. Idempotent: a second run with nothing new to add writes nothing
(verified directly: applying the same request twice reports `0 to add` /
`already_present` the second time, track count unchanged).

## Before a real `--all` run

Music.app has no transaction or rollback. A single-playlist run is a
handful of `duplicate` calls at most - trivially undone by hand if
something looks wrong. Before the first real `--all` run specifically,
consider quitting Music.app and copying `~/Music/Music/Music
Library.musiclibrary` aside first, since there's no automated backup here.

## Verified

Before this ever touched a real playlist: `--read` against a real,
well-understood playlist (matched a previously-recorded `.m3u`), the
diff/classification logic against three constructed cases (a genuinely
missing track, a track never imported anywhere, a playlist that doesn't
exist yet), and the apply path against a disposable test-only playlist -
created, one known track added, read back to confirm, applied a second
time to confirm idempotency (no duplicate), then deleted.

## Related

- `docs/playlist-sync.md` — the forward direction, and the `m3u-union`
  merge driver for syncing `.m3u` files across machines without conflicts.
- `docs/mcatalogid.md` — the tag this whole direction is matched on.
