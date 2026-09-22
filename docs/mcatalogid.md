# MCATALOGID tagging

A custom tag (`TXXX:mcatalogid` on mp3, `----:com.apple.iTunes:mcatalogid`
freeform atom on m4a, a Vorbis comment on flac) that identifies a track
independent of its path, so playlists can be regrouped after a move to a new
machine even if filenames or folders change. Menu **4·1–4·3**.

```
bin/mcatalogid-backfill.py   fills in a missing tag on $GDRIVE_MUSIC_DIR - ongoing
bin/apple-music-tag-sync.py  copies the tag into Apple Music's own copy - one-time
```

## Why not ffmpeg

Every other tagging tool in this repo (`music-tagger` skill) writes tags with
`ffmpeg -c copy -metadata k=v`. That remuxes the whole file, and on a file
with an unusual mp3 frame structure it was found to **silently truncate the
audio to a few seconds while reporting success** (exit 0, no error) - caught
by testing on a copy before it ever touched a real file.

Both scripts here use [mutagen](https://mutagen.readthedocs.io/) instead
(`pip install --user mutagen`), which patches the tag block in place and
never touches audio data. Verified against a real file: identical size,
duration, and raw-audio-bytes-after-the-ID3-header before and after.

## `bin/mcatalogid-backfill.py`

Two passes, in order, over every mp3/m4a/flac under the given root:

1. **backfill** - no real tag, but the filename already carries one
   (`...-[mid-1012747].m4a`) - use it as-is.
2. **assign** - still no real tag (this also covers a tag whose value is the
   literal placeholder `"catalog"`, meaning "no real id was ever assigned" -
   same as having none). Take the highest `MCATALOGID` found anywhere in the
   tree - in a tag *or* a filename, so a value that only exists in a
   filename still can't collide - and assign `max+1, max+2, ...`, one per
   file, in path-sorted order. **The filename is never renamed.**

A file whose tag and filename both carry a real but *different* id is left
alone and reported as a conflict, never silently overwritten.

```bash
bin/mcatalogid-backfill.py --dry-run   # report only, write nothing
bin/mcatalogid-backfill.py             # write for real
bin/mcatalogid-backfill.py --root DIR  # override $GDRIVE_MUSIC_DIR
```

Idempotent - already-tagged files are always skipped, so it's safe to run
again whenever new tracks show up. Each write is read back and verified
before being counted as done.

### New tracks with no id at all

By design, this never invents an id for a track with **neither** a real tag
**nor** a filename id - `assign` only fires once the file is confirmed to
have nothing usable at all. If you're adding new tracks with a filename id
already in hand, pass 1 picks it up automatically; nothing separate to run.

## `bin/apple-music-tag-sync.py`

With "keep library organized" + "copy music files" on in Music.app, importing
a track makes Apple Music's **own byte copy** under `$APPLE_MUSIC_DIR` -
tagging the Google Drive copy doesn't touch it. This script copies
`MCATALOGID` from the Google Drive copy into the matching Apple Music copy,
one time, to catch up the existing library. Going forward, tag before
importing and the tag travels with the copy automatically - no sync needed.

Reuses the matching engine from `bin/playlist-sync.py` (`normalize`,
`clean_apple_name`, `clean_tree_name`, `build_lookups`, `find_match`,
`apple_track_num`, `tree_track_num`) to pair each Apple Music track with its
`$GDRIVE_MUSIC_DIR` counterpart, the same way `playlist-sync.py` matches
Apple paths onto `files.csv`.

```bash
bin/apple-music-tag-sync.py --dry-run   # report only, write nothing
bin/apple-music-tag-sync.py             # write for real
bin/apple-music-tag-sync.py --from d.json  # use a saved fetch.js dump
```

A track whose Apple copy already has a *different* real id is reported as a
conflict and left alone. Not wired into the menu - it's meant to run once;
`docs/mcatalogid.md` is where to find it again if you ever need to.

## Google Drive sync

`GDRIVE_MUSIC_DIR` points at Google Drive's local File Provider mount
(`~/Library/CloudStorage/GoogleDrive-…`, or a stable symlink to it like
`~/Google Drive`), not the `rclone` remote. Editing a file there is picked up
and uploaded automatically - confirmed in testing via the file's updated
mtime and its `com.google.drivefs.item-id` extended attribute, and it's the
core job of DriveFS's File Provider extension (same mechanism as iCloud
Drive) to watch that folder for exactly this kind of change.

## Config

| Key | |
|---|---|
| `GDRIVE_MUSIC_DIR` | local Drive-mounted music folder. No default - the path embeds your account email. |
| `APPLE_MUSIC_DIR` | Apple Music's managed copy, default `~/Music/Music/Media.localized/Music` |
