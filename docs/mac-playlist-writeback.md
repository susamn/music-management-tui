# Mac playlist write-back (future — not implemented)

## The want

Today playlists flow **one way**: Apple Music.app → `playlists/*.m3u`
(`bin/fetch.js` dumps, `bin/playlist-sync.py` matches onto `files.tree` and
writes the `.m3u`s).

The missing direction: edit a `.m3u` in `music-metadata/playlists/` on the
Linux box (reorder, add, remove tracks), then later, on the Mac, **push that
change into Apple Music.app** so the two agree. Needed whenever the playlist is
curated somewhere other than Music.app.

## Why it's hard

`playlist-sync.py` goes `Apple path → slug`. The reverse, `slug → Apple track`,
is lossy:

- The `.m3u` lines are `files.tree` slugs (`a-r-rahman/dil-se/06-satrangi-re-[mid-1000038].mp3`).
  Music.app identifies tracks by **persistent ID**, and its file paths are
  NFD-normalised absolute paths under `~/Music/Music/Media.localized/…`.
- `bin/extract.js` already emits `persistent_id`, `path_slug` and `join_key`
  per track — so a **slug → persistent_id map** can be built from a
  `play_stats.csv` snapshot (or a fresh `extract.js` run). `join_key` gets
  ~85% of tracks; the rest need `path_slug` or a title+album fallback, same
  failure classes `playlist-sync.py` already documents.
- Music.app has **no transaction**. A half-applied reorder is a mess.

## JXA surface (for whoever builds it)

```js
const Music = Application("Music");
Music.userPlaylists.byName("Rock Hindi")            // find, or:
Music.make({new: 'playlist', withProperties: {name: 'Rock Hindi'}})
pl.tracks                                            // current contents, in order
Music.add(Path("/abs/file.mp3"), {to: pl})           // add by file
Music.duplicate(libraryTrack, {to: pl})              // add an existing library track
pl.tracks[i].delete()                                // remove
pl.move(pl.tracks[i], {to: pl.tracks[j]})            // reorder (fiddly; often
                                                    //   easier to clear + re-add in order)
```

## Suggested shape

- `bin/playlist-writeback.py` + `bin/writeback.js` (JXA helper), new items under
  menu section 4.
- **Additive-first**: default to "add tracks in the `.m3u` that Music's playlist
  lacks", never delete, print a diff and confirm. Full reconciliation
  (removals + order) behind an explicit flag.
- Take a Music library backup first (`~/Music/Music/Music Library.musiclibrary`
  copied aside) — Music.app can't roll back.
- Resolve slugs through a persistent-id map built from the newest
  `play_stats.csv`; report unresolved lines and skip them rather than guessing.
- Idempotent: a second run with nothing changed touches nothing.

## Related

- `docs/playlist-sync.md` — the forward direction and its matching rules.
- `docs/play-stats.md` — `extract.js` columns, including `persistent_id`.
